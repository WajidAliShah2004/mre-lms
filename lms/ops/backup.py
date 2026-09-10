#!/usr/bin/env python3
"""Nightly backup — spec §Day-6.4.

Until this existed there was no backup of any kind. Everything Matthew has —
the archive, the classifications, the audit log — sat on one volume, and a
disk failure would have taken all of it. That was the largest remaining risk
in the build and the only one whose downside is unrecoverable.

WHAT IS BACKED UP, AND WHY THAT LIST
------------------------------------
  * the database    — the catalogue, the audit log, the corrections. Small,
                      irreplaceable, and the only thing that knows WHY each
                      document is where it is.
  * the sidecars    — one .meta.json per filed document. Together with the
                      documents they make the archive self-describing; without
                      them a restored tree is a heap of well-named files.
  * config/         — entities.yaml is the classifier. Losing it does not lose
                      documents, it loses the ability to file new ones
                      correctly, which is worse than it sounds.
  * ops/lms.env     — paths only, no secrets (D-016).

  * the documents  — the filed archive itself. INCLUDED BY DEFAULT since
                      Sept 10; `--catalogue-only` opts out.

WHY THE DEFAULT CHANGED
-----------------------
The spec says "DB, configs, sidecars", and documents were optional to match
it. That default rested on an assumption the spec states plainly — "it is only
sufficient while the documents survive elsewhere" — and on this machine
"elsewhere" is one RAID volume that D-035 already established is not a backup.
Single-parity RAID survives a disk; it does not survive the enclosure, the
controller, the filesystem, a deletion, or theft.

So the nightly job was producing a flawless index of files that would not
exist. The restore test even said so, in a WARN nobody was going to act on
twice.

The cost argument is gone too: the archive is a few hundred megabytes, restic
deduplicates and compresses, and the repository is on the internal SSD with
room to spare. There is nothing left on the other side of the trade.

`--catalogue-only` remains, because a fast catalogue snapshot before a risky
migration is a real use. It is a deliberate choice now rather than what
happens if nobody passes a flag.

THE PART THAT MAKES IT A BACKUP RATHER THAN A FILE COPY
-------------------------------------------------------
SQLite is copied through the online backup API, never with `cp`.

The database runs in WAL mode. Copying `lms.db` while anything is writing
gives you a file that is missing the WAL's recent commits, or is torn
mid-transaction — and neither failure announces itself. It restores, it
opens, and it is quietly wrong. `Connection.backup()` takes a consistent
snapshot of a live database, which is the entire difference between a backup
and a file that resembles one.

ENCRYPTION AND THE PASSWORD
---------------------------
restic encrypts client-side, so the repository is ciphertext at rest. The
password lives in the macOS Keychain and is read at run time — never in this
file, never in lms.env, never in a launchd plist (D-016, spec §12.4).

    security add-generic-password -a "$USER" -s lms/restic-repo -w

with NO value after -w, so it prompts and the secret stays out of the shell
history and the process table.

    ./ops/backup.py                     # database, config, sidecars, documents
    ./ops/backup.py --catalogue-only    # everything EXCEPT the documents
    ./ops/backup.py --dry-run           # say what would happen
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env import load_lms_env
from _reexec import ensure_venv                            # noqa: E402

REPO_DIR = Path(__file__).resolve().parents[1]
KEYCHAIN_SERVICE = "lms/restic-repo"


class BackupError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# The consistent database snapshot
# ---------------------------------------------------------------------------

def snapshot_database(db_path: Path, dest: Path) -> Path:
    """A consistent copy of a live SQLite database.

    Uses the online backup API rather than copying bytes. See the module
    docstring: with WAL enabled, a byte copy of a database being written to is
    silently incomplete, and silence is the failure mode you discover during a
    restore you are already having a bad day about.
    """
    db_path, dest = Path(db_path), Path(dest)
    if not db_path.exists():
        raise BackupError(f"no database at {db_path}")

    dest.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        out = sqlite3.connect(dest)
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()

    # Verify what we just wrote, here, while there is still a good original to
    # compare against. A backup nobody checked is a hope.
    check = sqlite3.connect(dest)
    try:
        result = check.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        check.close()
    if result != "ok":
        raise BackupError(f"the snapshot failed its own integrity check: {result}")
    return dest


def row_counts(db_path: Path) -> dict[str, int]:
    """Per-table counts, used to prove a restore actually restored something."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'").fetchall()]
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in sorted(tables)}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# restic
# ---------------------------------------------------------------------------

# `security` exit codes we can say something useful about. Anything else gets
# reported verbatim rather than guessed at.
_SEC_ITEM_NOT_FOUND = 44

# The repository password is the only thing protecting client tax and NPI data
# in an off-machine copy. `security add-generic-password -w` accepts an empty
# value in silence — it happened on the first real run here, and restic would
# then have initialised the repository with an empty passphrase and reported
# success. An encrypted-at-rest backup whose key is "" is a plaintext backup
# with extra steps.
MIN_PASSWORD_CHARS = 12

_CREATE_HINT = f"""    # 1. put a long random passphrase on the clipboard
    LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 40 | pbcopy

    # 2. paste it at BOTH prompts (nothing is echoed — paste, do not type)
    security add-generic-password -a "$USER" -s {KEYCHAIN_SERVICE} -w

    # 3. prove it took. Anything under {MIN_PASSWORD_CHARS} means it did not.
    security find-generic-password -s {KEYCHAIN_SERVICE} -w | tr -d '\\n' | wc -c

  No value after -w, so the secret never reaches your shell history or the
  process table. The clipboard holds it briefly instead, which is the lesser
  exposure of the two.

  THEN PUT IT IN THE PASSWORD MANAGER, BEFORE THE FIRST BACKUP RUNS.
  restic has no recovery path. If this passphrase is lost, every snapshot in
  the repository is permanently unreadable — the backup will still be there,
  still be encrypted, and be of no use to anyone."""


def repo_path(override: str | None = None) -> Path:
    """The restic repository. Computed HERE and nowhere else.

    Extracted from main() when a second script needed it. Two places deriving
    one path from the same environment is precisely how D-035 happened — the
    nightly job and the restore test each computed their own, disagreed, and
    both reported success against different repositories for days.

    resolve(), so the path printed is the path used: `archive.parent` renders
    as ".../archive/.." otherwise, which hides that the default sits on the
    same volume as the data.
    """
    archive = os.environ.get("LMS_ARCHIVE_ROOT")
    if not archive:
        raise BackupError("LMS_ARCHIVE_ROOT is not set — ops/lms.env not found")
    return Path(override or os.environ.get(
        "LMS_BACKUP_REPO", Path(archive).resolve().parent / "LMS_backup")).resolve()


def restic_password() -> str:
    """From the Keychain. Never from a file, an env var in lms.env, or a plist.

    Reports what `security` actually said. The first version of this collapsed
    every non-zero exit into "no Keychain item", and then said so to someone
    who had just created the item successfully — a message that sent them to
    check the one thing that was fine. An error that names a cause it has not
    established is worse than one that admits it does not know.
    """
    cmd = ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"]
    r = subprocess.run(cmd, capture_output=True, text=True)

    if r.returncode == 0 and r.stdout.strip():
        password = r.stdout.rstrip("\n")
        if len(password) < MIN_PASSWORD_CHARS:
            raise BackupError(
                f"the {KEYCHAIN_SERVICE!r} password is {len(password)} "
                f"characters. This key is the only thing protecting client tax "
                f"and NPI data in an off-machine copy — a short one makes the "
                f"encryption decorative.\n"
                f"Replace it:\n"
                f"    security delete-generic-password -s {KEYCHAIN_SERVICE}\n"
                f"{_CREATE_HINT}")
        return password

    stderr = r.stderr.strip()
    create = _CREATE_HINT

    if r.returncode == _SEC_ITEM_NOT_FOUND:
        raise BackupError(
            f"the Keychain has no item with service {KEYCHAIN_SERVICE!r}.\n"
            f"Create it with:\n{create}\n\n"
            f"  If you believe you already did, check which keychain it landed "
            f"in:\n    security dump-keychain | grep -A1 {KEYCHAIN_SERVICE}")

    if r.returncode == 0:
        raise BackupError(
            f"`security` reported success for {KEYCHAIN_SERVICE!r} but returned "
            f"an empty password. The item exists with no value in it — delete "
            f"and recreate it:\n"
            f"    security delete-generic-password -s {KEYCHAIN_SERVICE}\n{create}")

    raise BackupError(
        f"could not read {KEYCHAIN_SERVICE!r} from the Keychain.\n"
        f"  command : {' '.join(cmd)}\n"
        f"  exit    : {r.returncode}\n"
        f"  stderr  : {stderr or '(nothing)'}\n\n"
        f"  Exit 51 means access was denied — macOS shows a dialog the first "
        f"time a new process reads an item, and it must be allowed. Exit 36 "
        f"means the keychain is locked: `security unlock-keychain`.")


def run_restic(args: list[str], repo: Path, password: str,
               dry_run: bool = False) -> subprocess.CompletedProcess:
    if shutil.which("restic") is None:
        raise BackupError("restic is not installed. `brew install restic`")
    cmd = ["restic", "-r", str(repo), *args]
    if dry_run:
        print("   would run:", " ".join(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")
    env = {**os.environ, "RESTIC_PASSWORD": password}
    return subprocess.run(cmd, env=env, capture_output=True, text=True)


def same_volume(a: Path, b: Path) -> bool:
    """Are these two paths on the same physical device?

    st_dev, not a string comparison of the paths. /Volumes/MacStudioHD/LMS and
    /Volumes/MacStudioHD_backup look different and can be the same disk; a
    bind mount or a symlink can make one path look like two volumes.
    """
    def dev(p: Path) -> int | None:
        for candidate in (p, *p.parents):
            try:
                return candidate.stat().st_dev
            except OSError:
                continue
        return None

    da, db = dev(a), dev(b)
    return da is not None and da == db


def warn_if_same_volume(archive: Path, repo: Path) -> bool:
    """A backup beside the thing it is backing up is not a backup.

    It genuinely helps with the common cases — a bad delete, a corrupted
    database, a classification run that went wrong — and those are worth
    having. It does nothing whatsoever about the case the word "backup" is
    usually reaching for: the disk failing. Both copies go at once.

    Not fatal. A same-volume repository is better than none, and refusing to
    run would leave a machine with no backup at all while D-011 is open. But
    it must never be mistaken for disaster protection, so it says so every
    time rather than once in a README.
    """
    if not same_volume(archive, repo):
        return False
    print()
    print("  " + "!" * 68)
    print("  WARNING: the backup repository is on the SAME VOLUME as the archive.")
    print()
    print(f"    archive  {archive}")
    print(f"    repo     {repo}")
    print()
    print("  This protects against a bad delete, a corrupted database, or a")
    print("  classification run that went wrong — all worth having.")
    print()
    print("  It does NOTHING about the disk failing, which is the case the word")
    print("  'backup' is usually reaching for. Both copies die together.")
    print()
    print("  Point LMS_BACKUP_REPO at a DIFFERENT DEVICE — not another volume")
    print("  or partition on the same one. See D-011: the 12 TB array is four")
    print("  disks presented as a single device, so anything carved out of it")
    print("  shares the same failure. `diskutil list` shows what is separate.")
    print("  " + "!" * 68)
    return True


def ensure_repo(repo: Path, password: str, dry_run: bool = False) -> None:
    if (repo / "config").exists():
        return
    print(f"==> initialising an encrypted restic repo at {repo}")
    if dry_run:
        return
    repo.mkdir(parents=True, exist_ok=True)
    r = run_restic(["init"], repo, password)
    if r.returncode != 0:
        raise BackupError(f"restic init failed: {r.stderr.strip()}")


# ---------------------------------------------------------------------------

def main() -> int:
    ensure_venv("LMS_BACKUP_REEXEC", script=__file__)
    load_lms_env()

    p = argparse.ArgumentParser()
    p.add_argument("--catalogue-only", action="store_true",
                   help="database, config and sidecars, but NOT the filed "
                        "documents. A restore then gives a perfect index of "
                        "files it cannot produce — only useful as a fast "
                        "snapshot before a risky migration.")
    p.add_argument("--documents", action="store_true",
                   help=argparse.SUPPRESS)   # documents are the default now
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--repo", default=None,
                   help="restic repository (default: <mirror>/LMS_backup)")
    args = p.parse_args()

    # Documents unless someone explicitly asks for the catalogue alone. The
    # old `--documents` flag is accepted and hidden: it is now the default, so
    # a script or a runbook line still carrying it keeps working and means
    # exactly what it says, rather than failing on an unrecognised argument at
    # 02:30.
    include_documents = not args.catalogue_only

    archive = os.environ.get("LMS_ARCHIVE_ROOT")
    if not archive:
        print("LMS_ARCHIVE_ROOT is not set — run: source ops/lms.env", file=sys.stderr)
        return 2
    # resolve(), so the paths printed are the paths used. `archive.parent`
    # renders as ".../archive/.." otherwise, which hides that the repo default
    # sits on the same volume as the data.
    archive = Path(archive).resolve()
    db_path = Path(os.environ.get("LMS_DB", archive.parent / "lms.db")).resolve()
    repo = repo_path(args.repo)

    print(f"==> database  {db_path}")
    print(f"==> archive   {archive}")
    print(f"==> repo      {repo}")
    colocated = warn_if_same_volume(archive, repo)

    try:
        password = restic_password()
    except BackupError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 2

    staging = Path(tempfile.mkdtemp(prefix="lms-backup-"))
    try:
        print("==> consistent database snapshot (online backup API, not cp)")
        snap = snapshot_database(db_path, staging / "lms.db")
        counts = row_counts(snap)
        print("    " + ", ".join(f"{t}={n}" for t, n in counts.items() if n))
        print("    integrity_check: ok")

        for src, name in ((REPO_DIR / "config", "config"),
                          (REPO_DIR / "ops" / "lms.env", "lms.env")):
            if src.exists():
                if src.is_dir():
                    shutil.copytree(src, staging / name)
                else:
                    shutil.copy2(src, staging / name)

        sidecars = sorted(archive.rglob("*.meta.json"))

        # Only stage a separate copy when the archive itself is NOT going in.
        # With --documents the sidecars are already inside the archive tree,
        # and copying them again put two of everything in the snapshot — which
        # made the restore check read "4 sidecar(s) restored (archive has 2)"
        # and pass on arithmetic rather than on evidence.
        if include_documents:
            print(f"==> including the filed documents ({len(sidecars)} sidecars "
                  f"among them)")
            targets = [str(staging), str(archive)]
        else:
            print(f"==> {len(sidecars)} sidecar(s)")
            for s in sidecars:
                target = staging / "sidecars" / s.relative_to(archive)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(s, target)
            targets = [str(staging)]

        # What this snapshot CLAIMS to contain, written into the snapshot.
        #
        # Without it, a restore can only be checked against the live archive —
        # a moving target. A document arriving between the backup and the
        # check makes a perfectly good backup look short, and the only way to
        # tolerate that is a >= comparison loose enough to miss real loss.
        # A backup that states its own contents can be verified exactly.
        (staging / "manifest.json").write_text(json.dumps({
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "documents_included": include_documents,
            "sidecars": len(sidecars),
            "row_counts": counts,
        }, indent=2, sort_keys=True), encoding="utf-8")

        try:
            ensure_repo(repo, password, args.dry_run)
        except BackupError as exc:
            print(f"\n{exc}", file=sys.stderr)
            return 2

        stamp = datetime.now().strftime("%Y-%m-%dT%H:%M")
        r = run_restic(["backup", "--tag", "lms", "--tag", stamp, *targets],
                       repo, password, args.dry_run)
        if not args.dry_run and r.returncode != 0:
            print(f"restic backup failed: {r.stderr.strip()}", file=sys.stderr)
            return 1
        if not args.dry_run:
            print(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "done")
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    if not include_documents:
        print("\nNOTE: the documents themselves are NOT in this snapshot, only")
        print("the catalogue that describes them. A restore would give a perfect")
        print("index of files that no longer exist. That matches the spec, and")
        print("it is only sufficient while the documents survive elsewhere.")

    if colocated:
        print("\nThis snapshot is on the same volume as the archive. It survives")
        print("a mistake. It does not survive the disk. See the warning above.")

    print("\nOFF-MACHINE COPY — still outstanding, and it is Matthew's decision.")
    print("A local repo does not survive theft, fire, or the volume failing.")
    print("But an off-machine target means client tax and NPI data leaving the")
    print("premises. restic encrypts before upload, so the provider stores")
    print("ciphertext — that is the mitigation, not an argument that the")
    print("question does not arise. Get it in writing before enabling it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
