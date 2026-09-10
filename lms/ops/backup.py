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

Documents themselves are OPTIONAL (--documents) and off by default, matching
the spec's "DB, configs, sidecars". Read the warning printed at the end before
deciding that is enough: a restore without documents gives a perfect
catalogue of files you no longer have.

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

    ./ops/backup.py                     # snapshot to the mirror volume
    ./ops/backup.py --documents         # include the filed documents too
    ./ops/backup.py --dry-run           # say what would happen
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
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

def restic_password() -> str:
    """From the Keychain. Never from a file, an env var in lms.env, or a plist."""
    r = subprocess.run(
        ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
        capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        raise BackupError(
            f"no Keychain item {KEYCHAIN_SERVICE!r}. Create it with:\n"
            f'    security add-generic-password -a "$USER" -s {KEYCHAIN_SERVICE} -w\n'
            f"  (no value after -w — it prompts, keeping the secret out of your "
            f"shell history and the process table)")
    return r.stdout.rstrip("\n")


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

    p = argparse.ArgumentParser()
    p.add_argument("--documents", action="store_true",
                   help="include the filed documents, not just the catalogue")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--repo", default=None,
                   help="restic repository (default: <mirror>/LMS_backup)")
    args = p.parse_args()

    archive = os.environ.get("LMS_ARCHIVE_ROOT")
    if not archive:
        print("LMS_ARCHIVE_ROOT is not set — run: source ops/lms.env", file=sys.stderr)
        return 2
    archive = Path(archive)
    db_path = Path(os.environ.get("LMS_DB", archive.parent / "lms.db"))
    repo = Path(args.repo or os.environ.get(
        "LMS_BACKUP_REPO", archive.parent / "LMS_backup"))

    print(f"==> database  {db_path}")
    print(f"==> archive   {archive}")
    print(f"==> repo      {repo}")

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
        print(f"==> {len(sidecars)} sidecar(s)")
        for s in sidecars:
            rel = s.relative_to(archive)
            target = staging / "sidecars" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, target)

        targets = [str(staging)]
        if args.documents:
            print("==> including the filed documents")
            targets.append(str(archive))

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

    if not args.documents:
        print("\nNOTE: the documents themselves are NOT in this snapshot, only")
        print("the catalogue that describes them. A restore would give a perfect")
        print("index of files that no longer exist. That matches the spec, and")
        print("it is only sufficient while the documents survive elsewhere.")

    print("\nOFF-MACHINE COPY — still outstanding, and it is Matthew's decision.")
    print("A local repo does not survive theft, fire, or the volume failing.")
    print("But an off-machine target means client tax and NPI data leaving the")
    print("premises. restic encrypts before upload, so the provider stores")
    print("ciphertext — that is the mitigation, not an argument that the")
    print("question does not arise. Get it in writing before enabling it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
