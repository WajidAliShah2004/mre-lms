#!/usr/bin/env python3
"""Off-site copy of the nightly backup to Backblaze B2 — R29, spec §11.4.

    "Nightly restic snapshots ... to an external volume and an encrypted
     off-site target; 90-day retention with monthly long-terms; ... restores
     tested quarterly (an untested backup is not a backup)."   — spec §11.4

WHY THIS EXISTS
---------------
The local repository is on the internal SSD (D-011): it survives the array
failing, a bad delete, a classification run gone wrong. It does not survive
the machine. Theft, fire, or the Mac dying takes the archive AND its backup,
because both are inside one box. D-012 has said so since August, and
`backup.py` printed it every night.

D-063 settled the shape: a private B2 bucket, an application key restricted to
that bucket, a second restic repository there, and a nightly `restic copy`.

INERT UNTIL CONFIGURED
----------------------
The bucket and the key do not exist yet; Matthew has to create them
(ops/OFFSITE_APPLY.md). Until then this does nothing, says so in one line of
the nightly log, and writes OFFSITE_NOT_CONFIGURED to the audit log ONCE per
unconfigured stretch — not every night. 365 identical rows a year is how an
audit log stops being read.

It does not fail, and it does not reach the brief. An alarm that fires every
morning about a thing nobody can fix until a Backblaze account is set up is an
alarm that trains people to skip the section — and the section also carries
the mail poll dying. The "not configured" state is shown where it is
actionable: `verify_setup.py` [8] WARNs it, and the weekly audit runs
verify_setup.

"Configured" means ONE thing: the Keychain holds `lms/offsite-repo`. The setup
script (`set_offsite_backup.py`) stores that item LAST, after the key has been
checked and the repository initialised, so its presence means setup finished.
Location present but a credential missing is a broken configuration, not an
absent one, and fails loudly.

WHY A SEPARATE SCRIPT, CALLED FROM backup.py
--------------------------------------------
Called from `backup.py` after a successful local snapshot, so:

  * ordering is structural: there is no off-site copy of a backup that failed,
    and no second launchd job whose 02:45 start time has to be kept clear of
    the first's finish (a slow night would race it);
  * the halt (D-059) covers it with no new plist to discover;
  * the local backup is recorded (BACKUP_COMPLETED) BEFORE the copy starts, so
    a B2 outage never makes a good local snapshot look like a failed one.

But a module of its own, runnable by hand, because the first copy after setup
is run interactively (it arms the brief's watch), and so is the catch-up after
an outage. The copy is idempotent: `restic copy` skips snapshots already
present, so a missed night is caught up by the next.

Exit codes from `backup.py`: 0 local done (and off-site done or inert),
3 local done but the off-site copy FAILED, anything else the local backup
failed and nothing was attempted off-site.

THE PASSWORDS
-------------
`restic copy` opens two repositories and needs both passwords.

The off-site repository has its OWN password (`lms/restic-offsite`), not the
local one, because:

  * the local passphrase has been in two terminal histories and a chat
    transcript (D-049) and may not be rotated yet. Reusing it would put a
    known-exposed key on the one copy that leaves the building;
  * each can then be rotated without touching the other;
  * one leaking opens one copy, not both.

The cost is a second passphrase in the password manager. It is not optional:
restic has no recovery path, and this is the copy that exists for the day the
Mac — and its Keychain — are gone. A passphrase that lives only in the Keychain
of the machine that burned is the off-site backup lost with it.

Both reach restic as anonymous pipes (`--password-file /dev/fd/N`,
`--from-password-file /dev/fd/M`), the method D-049 settled on for
`rotate_backup_password.py`: not in argv (visible in `ps`), not a temp file
(plaintext on the disk whose theft this exists to survive), not
RESTIC_PASSWORD.

The B2 key id and application key go in the ENVIRONMENT of the restic child
(B2_ACCOUNT_ID / B2_ACCOUNT_KEY), because that is the only way restic accepts
them — it has no file or fd option for B2 credentials. That exposure is to the
same user's processes for the life of the call, and it is why the key must be
restricted to one bucket: a leaked key reaches ciphertext and nothing else.
Never this process's own os.environ, never argv, never a log line: every
string that leaves this module for the audit log or stdout passes `scrub()`.

RETENTION
---------
    forget --prune --group-by host --keep-daily 90 --keep-monthly 24
           --keep-tag hold

  * `--keep-daily 90`: the newest snapshot of each of the last 90 days that HAVE
    one. Counted in snapshot-days, not calendar days, so a fortnight with the
    Mac switched off stretches the window rather than eating into it.
  * `--keep-monthly 24`: the spec's "monthly long-terms", with the number left
    to us. Two years: long enough to recover a document deleted and not missed
    for a season. Not longer, because each month kept is another copy of
    client NPI held by a third party, and the archive itself — not this
    backup — is the system of record for retention (§6.5). One constant below.
  * `--keep-tag hold`: the litigation-hold override §11.4 refers to. A snapshot
    tagged `hold` in the off-site repository is never forgotten.
  * `--group-by host`, NOT restic's default `host,paths`, and this one matters.
    `backup.py` snapshots a fresh `mkdtemp` staging directory every night, so
    no two snapshots share a path set — and the default grouping would put
    every snapshot in a group of one, of which forget keeps the newest. It
    would keep everything, forever, and report success. Grouping by tags fails
    the same way: each snapshot carries its own timestamp tag.

Forget runs only after a copy that succeeded on this run. Retention cannot
remove the newest snapshot, so this is not the D-037 gate; it is simply that a
repository we could not write to tonight is not one to start deleting from.

    ./ops/offsite_backup.py              # copy + retention now (first copy, catch-up)
    ./ops/offsite_backup.py --dry-run    # say what would run, touch nothing
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env import load_lms_env                              # noqa: E402
from _reexec import ensure_venv                            # noqa: E402

REPO_DIR = Path(__file__).resolve().parents[1]

# Keychain services. The location is not secret, but it lives beside the
# credentials so that "configured" is one fact in one place (D-035: two places
# describing one repository is how the restore test came to verify the wrong
# one).
REPO_SERVICE = "lms/offsite-repo"           # b2:<bucket>:<path>   stored LAST
PASSWORD_SERVICE = "lms/restic-offsite"     # the off-site repository's own key
KEY_ID_SERVICE = "lms/b2-key-id"            # B2 application keyID
APP_KEY_SERVICE = "lms/b2-app-key"          # B2 applicationKey

KEEP_DAILY = 90
KEEP_MONTHLY = 24
RETENTION = ["--group-by", "host",
             "--keep-daily", str(KEEP_DAILY),
             "--keep-monthly", str(KEEP_MONTHLY),
             "--keep-tag", "hold"]

# A first copy of a few hundred MB over an office line is minutes; six hours
# means it is stuck. Without a limit a hung TCP connection holds the 02:30 job
# — and the repository lock — until someone notices, which nobody would.
TIMEOUT_SECONDS = 6 * 3600

# B2 bucket names: 6-50 characters, letters, digits and hyphens. The path is
# required: a repository at the bucket root leaves no room for anything else.
_REPO_RE = re.compile(r"^b2:[A-Za-z0-9-]{6,50}:[A-Za-z0-9._/-]+$")

_SEC_ITEM_NOT_FOUND = 44

ACTION_COMPLETED = "OFFSITE_COPY_COMPLETED"
ACTION_FAILED = "OFFSITE_COPY_FAILED"
ACTION_PRUNE_FAILED = "OFFSITE_PRUNE_FAILED"
ACTION_NOT_CONFIGURED = "OFFSITE_NOT_CONFIGURED"


class OffsiteError(RuntimeError):
    pass


@dataclass(frozen=True)
class OffsiteConfig:
    repo: str
    password: str = field(repr=False)
    key_id: str = field(repr=False)
    app_key: str = field(repr=False)

    def secrets(self) -> list[str]:
        return [self.password, self.key_id, self.app_key]


# ---------------------------------------------------------------------------
# Configuration, from the Keychain
# ---------------------------------------------------------------------------

def keychain_read(service: str) -> str | None:
    """The item's value, or None if there is no such item.

    Anything else — access denied (51), a locked keychain (36) — is an error
    and says what `security` actually said. D-035: an error that names a cause
    it has not established sends the reader to check the one thing that is
    fine.

    No `security` binary (not a Mac) is "not configured": there is no Keychain
    for anything to be configured in.
    """
    cmd = ["security", "find-generic-password", "-s", service, "-w"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        return None
    if r.returncode == _SEC_ITEM_NOT_FOUND:
        return None
    if r.returncode != 0:
        raise OffsiteError(
            f"could not read {service!r} from the Keychain "
            f"(exit {r.returncode}): {r.stderr.strip() or '(nothing)'}. "
            f"51 = access denied (allow the dialog), 36 = keychain locked.")
    return r.stdout.rstrip("\n")


def validate_repo(repo: str) -> str:
    if not _REPO_RE.match(repo):
        raise OffsiteError(
            f"{REPO_SERVICE} holds {repo!r}, which is not b2:<bucket>:<path>. "
            f"Re-run ./ops/set_offsite_backup.py.")
    return repo


def load_config() -> OffsiteConfig | None:
    """None if off-site is not configured. OffsiteError if it is half-configured.

    The location item is checked first and alone: when it is absent this reads
    nothing else, so an unconfigured machine costs one `security` call a night.
    """
    from backup import MIN_PASSWORD_CHARS

    repo = keychain_read(REPO_SERVICE)
    if repo is None:
        return None
    validate_repo(repo.strip())

    values = {}
    for service in (PASSWORD_SERVICE, KEY_ID_SERVICE, APP_KEY_SERVICE):
        v = keychain_read(service)
        if not v:
            raise OffsiteError(
                f"{REPO_SERVICE} is set but {service!r} is missing or empty — "
                f"the off-site backup is half-configured. Re-run "
                f"./ops/set_offsite_backup.py.")
        values[service] = v

    password = values[PASSWORD_SERVICE]
    if len(password) < MIN_PASSWORD_CHARS:
        raise OffsiteError(
            f"the {PASSWORD_SERVICE!r} password is {len(password)} characters. "
            f"It is the only thing between a third party's disks and client "
            f"tax and NPI data; a short one makes the encryption decorative.")

    return OffsiteConfig(repo=repo.strip(), password=password,
                         key_id=values[KEY_ID_SERVICE].strip(),
                         app_key=values[APP_KEY_SERVICE].strip())


# ---------------------------------------------------------------------------
# restic, with every secret off the command line
# ---------------------------------------------------------------------------

def scrub(text: str, secrets: list[str]) -> str:
    """Remove every secret from a string bound for a log or a terminal.

    restic does not echo passwords, and B2's error bodies should not echo keys.
    "Should not" is not a control; this is.
    """
    for s in secrets:
        if s:
            text = text.replace(s, "[redacted]")
    return text


def restic_env(cfg: OffsiteConfig) -> dict[str, str]:
    """The restic child's environment: ours, minus anything that could
    override what we pass explicitly, plus the B2 credentials.

    An inherited RESTIC_PASSWORD or RESTIC_REPOSITORY from an operator's shell
    must not quietly win over the Keychain — or be handed to a repository it
    was never meant for.
    """
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("RESTIC_PASSWORD", "RESTIC_FROM_",
                                "RESTIC_REPOSITORY", "B2_"))}
    env["B2_ACCOUNT_ID"] = cfg.key_id
    env["B2_ACCOUNT_KEY"] = cfg.app_key
    return env


@contextmanager
def password_fds(*passwords: str):
    """Each password on its own anonymous pipe; yields the read ends.

    Written and closed before restic starts, so restic reads to EOF and the
    pipe never blocks. Passwords are tens of bytes; a pipe holds 16 KB+.
    """
    readers = []
    try:
        for pw in passwords:
            r, w = os.pipe()
            os.write(w, pw.encode())
            os.close(w)
            readers.append(r)
        yield readers
    finally:
        for r in readers:
            try:
                os.close(r)
            except OSError:
                pass


def run_restic(cfg: OffsiteConfig, args: list[str], *,
               from_repo: Path | None = None,
               from_password: str | None = None,
               timeout: int | None = TIMEOUT_SECONDS
               ) -> subprocess.CompletedProcess:
    """restic against the off-site repository.

    With `from_repo`, also opens the local repository as the source (copy,
    init --copy-chunker-params). Both passwords by fd; see the module
    docstring for why not the other three ways.
    """
    if shutil.which("restic") is None:
        raise OffsiteError("restic is not installed. `brew install restic`")

    pws = [cfg.password] + ([from_password] if from_repo is not None else [])
    with password_fds(*pws) as fds:
        cmd = ["restic", "-r", cfg.repo, "--password-file", f"/dev/fd/{fds[0]}"]
        cmd += args
        if from_repo is not None:
            cmd += ["--from-repo", str(from_repo),
                    "--from-password-file", f"/dev/fd/{fds[1]}"]
        return subprocess.run(cmd, env=restic_env(cfg), capture_output=True,
                              text=True, pass_fds=tuple(fds), timeout=timeout)


def copy(cfg: OffsiteConfig, local_repo: Path,
         local_password: str) -> subprocess.CompletedProcess:
    """Every local snapshot not already off-site. Idempotent."""
    return run_restic(cfg, ["copy"], from_repo=local_repo,
                      from_password=local_password)


def forget(cfg: OffsiteConfig) -> subprocess.CompletedProcess:
    return run_restic(cfg, ["forget", "--prune", *RETENTION])


def init(cfg: OffsiteConfig, local_repo: Path,
         local_password: str) -> subprocess.CompletedProcess:
    """Create the off-site repository with the LOCAL repository's chunker
    parameters, which restic's docs require for deduplication to work across
    the two. Without it a copy still succeeds — and stores more than it needs
    to, forever."""
    return run_restic(cfg, ["init", "--copy-chunker-params"],
                      from_repo=local_repo, from_password=local_password)


def restore_latest(cfg: OffsiteConfig, target: Path) -> subprocess.CompletedProcess:
    """For restore_test.py --offsite. Downloads the whole latest snapshot."""
    return run_restic(cfg, ["restore", "latest", "--target", str(target)])


def snapshot_count(cfg: OffsiteConfig) -> int:
    import json
    r = run_restic(cfg, ["snapshots", "--json"], timeout=600)
    if r.returncode != 0:
        raise OffsiteError(scrub(r.stderr.strip(), cfg.secrets()))
    return len(json.loads(r.stdout or "[]"))


# ---------------------------------------------------------------------------
# The audit log
# ---------------------------------------------------------------------------

def _connect(db_path: Path):
    sys.path.insert(0, str(REPO_DIR))
    from core.db import database as db
    return db, db.connect(db_path)


def last_offsite_action(db_path: Path) -> str | None:
    try:
        _, conn = _connect(db_path)
        try:
            row = conn.execute(
                "SELECT action FROM actions_log WHERE action LIKE 'OFFSITE_%' "
                "ORDER BY id DESC LIMIT 1").fetchone()
        finally:
            conn.close()
    except Exception:
        return None
    return row[0] if row else None


def record(db_path: Path, action: str, detail: str, secrets: list[str]) -> None:
    """Append to actions_log. Never raises: a logging failure must not turn a
    good copy into a failed job, and must not hide a failed one either — the
    exit code still carries it."""
    try:
        db, conn = _connect(db_path)
        try:
            db.log_action(conn, action, detail=scrub(detail, secrets)[:400])
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        print(f"    (could not record {action} in the audit log: {exc})",
              file=sys.stderr)


def _tail(text: str, lines: int = 3) -> str:
    return " | ".join(text.strip().splitlines()[-lines:])


_LOCK_HINT = ("\n  A previous run left a lock in the off-site repository. If no "
              "other restic is running:\n    ./ops/offsite_backup.py --unlock")


# ---------------------------------------------------------------------------

def run_after_local_backup(db_path: Path, local_repo: Path, local_password: str,
                           *, dry_run: bool = False) -> int:
    """Copy tonight's local snapshot off-site, then apply retention there.

    0 = copied (or not configured — inert by design); 1 = failed, logged.
    Called by backup.py only after BACKUP_COMPLETED has been written.
    """
    secrets = [local_password]
    try:
        cfg = load_config()
    except OffsiteError as exc:
        msg = str(exc)
        print(f"\n==> off-site: FAILED — {msg}", file=sys.stderr)
        if not dry_run:
            record(db_path, ACTION_FAILED, f"step=config {msg}", secrets)
        return 1

    if cfg is None:
        print("\n==> off-site: not configured (R29). The local repository is "
              "the only copy, and it does not survive theft or fire. "
              "See ops/OFFSITE_APPLY.md.")
        # Once per unconfigured stretch, not every night.
        if not dry_run and last_offsite_action(db_path) != ACTION_NOT_CONFIGURED:
            record(db_path, ACTION_NOT_CONFIGURED,
                   f"no Keychain item {REPO_SERVICE}; off-site copy skipped",
                   secrets)
        return 0

    secrets += cfg.secrets()
    print(f"\n==> off-site copy  {local_repo}  ->  {cfg.repo}")
    if dry_run:
        print(f"   would run: restic -r {cfg.repo} copy --from-repo {local_repo}")
        print(f"   would run: restic -r {cfg.repo} forget --prune "
              f"{' '.join(RETENTION)}")
        return 0

    started = time.monotonic()
    try:
        r = copy(cfg, local_repo, local_password)
    except subprocess.TimeoutExpired:
        msg = f"restic copy did not finish in {TIMEOUT_SECONDS // 3600}h"
        print(f"    FAILED: {msg}", file=sys.stderr)
        record(db_path, ACTION_FAILED, f"step=copy repo={cfg.repo} {msg}", secrets)
        return 1
    except OffsiteError as exc:
        print(f"    FAILED: {exc}", file=sys.stderr)
        record(db_path, ACTION_FAILED, f"step=copy {exc}", secrets)
        return 1

    if r.returncode != 0:
        err = scrub(r.stderr.strip(), secrets)
        print(f"    restic copy FAILED (exit {r.returncode}):\n    {err}"
              + (_LOCK_HINT if "lock" in err.lower() else ""), file=sys.stderr)
        record(db_path, ACTION_FAILED,
               f"step=copy repo={cfg.repo} exit={r.returncode} {_tail(err)}",
               secrets)
        return 1

    seconds = int(time.monotonic() - started)
    print(f"    copied ({seconds}s)")
    # Written once restic has said yes, so it records what happened. This row
    # is what arms the brief's watch on the off-site copy.
    record(db_path, ACTION_COMPLETED,
           f"repo={cfg.repo} from={local_repo} seconds={seconds}", secrets)

    print(f"==> off-site retention: forget --prune {' '.join(RETENTION)}")
    try:
        f = forget(cfg)
    except (subprocess.TimeoutExpired, OffsiteError) as exc:
        msg = scrub(str(exc), secrets)
        print(f"    retention FAILED: {msg}", file=sys.stderr)
        record(db_path, ACTION_PRUNE_FAILED, f"repo={cfg.repo} {msg}", secrets)
        return 1
    if f.returncode != 0:
        err = scrub(f.stderr.strip(), secrets)
        # The copy is safe; only the pruning failed. Still non-zero: a
        # repository that never prunes grows without limit, and a lock left
        # behind will fail tomorrow's copy.
        print(f"    retention FAILED (exit {f.returncode}):\n    {err}"
              + (_LOCK_HINT if "lock" in err.lower() else ""), file=sys.stderr)
        record(db_path, ACTION_PRUNE_FAILED,
               f"repo={cfg.repo} exit={f.returncode} {_tail(err)}", secrets)
        return 1
    print("    done")
    return 0


def main() -> int:
    ensure_venv("LMS_OFFSITE_REEXEC", script=__file__)
    load_lms_env()

    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--unlock", action="store_true",
                   help="remove stale locks from the off-site repository "
                        "(only when no other restic is running)")
    args = p.parse_args()

    import backup
    try:
        local_repo = backup.repo_path()
        local_password = backup.restic_password()
        db_path = backup.live_db_path()
    except backup.BackupError as exc:
        print(exc, file=sys.stderr)
        return 2

    if args.unlock:
        try:
            cfg = load_config()
        except OffsiteError as exc:
            print(exc, file=sys.stderr)
            return 2
        if cfg is None:
            print("off-site backup is not configured — nothing to unlock")
            return 0
        r = run_restic(cfg, ["unlock"], timeout=600)
        print(scrub((r.stdout + r.stderr).strip(), cfg.secrets()) or "unlocked")
        return 0 if r.returncode == 0 else 1

    if not (local_repo / "config").exists():
        print(f"no local restic repository at {local_repo} — run "
              f"./ops/backup.py first", file=sys.stderr)
        return 2
    return run_after_local_backup(db_path, local_repo, local_password,
                                  dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
