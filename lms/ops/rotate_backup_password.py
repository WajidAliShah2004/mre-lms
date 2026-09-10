#!/usr/bin/env python3
"""Change the restic repository passphrase without losing the repository.

    ./ops/rotate_backup_password.py --check     # what keys exist, no changes
    ./ops/rotate_backup_password.py             # rotate

WHY NOW
-------
The current passphrase was generated on Sept 10 and has since appeared in two
terminal histories on this Mac and in a contractor chat transcript. Nothing has
gone wrong; the point is that neither of us can any longer say how many copies
exist, which is the same reasoning as D-017 for the mailbox credentials.

WHAT RESTIC ACTUALLY DOES
-------------------------
There is no "change the password" operation. A repository holds a set of KEYS,
any one of which unlocks it, and rotation is: add a new key, prove it works,
remove the old one. The data is never re-encrypted, so this is fast and safe —
provided the order is right.

THE ORDER IS THE WHOLE THING
----------------------------
    1. generate a new passphrase
    2. `restic key add`                 (needs the OLD one; repo now has both)
    3. store the new one in the Keychain, keeping the old in memory
    4. LIST SNAPSHOTS USING ONLY THE NEW PASSPHRASE
    5. only then `restic key remove <old>`
    6. list snapshots again

Step 4 gates step 5, and that is not a formality. D-037: the backup was deleted
on my instruction because a verification had passed against the wrong
repository — a destructive step ran on the strength of a fact nobody had
actually established. Removing the old key before proving the new one works
would make every snapshot permanently unreadable, and restic has no recovery
path. The backup would still be there, and nothing on earth could open it.

If ANY step fails, the old key is left in place. Both keys working is a
harmless state; neither working is unrecoverable.

THE NEW PASSPHRASE IS PRINTED ONCE
----------------------------------
Deliberately, and it is the only time. It has to reach the password manager,
and a secret that exists only in a Keychain dies with the Mac — which is the
one failure this whole backup exists to survive. Copy it before pressing
return, then clear the scrollback.
"""

from __future__ import annotations

import argparse
import re
import secrets
import string
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backup as backup_ops                               # noqa: E402
from _env import load_lms_env                             # noqa: E402
from _reexec import ensure_venv                           # noqa: E402

ALPHABET = string.ascii_letters + string.digits
NEW_LENGTH = 40


def generate() -> str:
    """Generated here, never typed.

    Four attempts at pasting a passphrase into a no-echo prompt on this machine
    produced an empty password twice and a five-character one once — bracketed
    paste turns the pasted text into terminal escape codes. Removing the human
    from the transcription removed the whole class of failure.
    """
    return "".join(secrets.choice(ALPHABET) for _ in range(NEW_LENGTH))


def run(args: list[str], password: str, repo: str) -> subprocess.CompletedProcess:
    """restic, with the passphrase on stdin.

    Not `--password-command`, not RESTIC_PASSWORD in the environment: an env
    var is visible in `ps` output to any process on the machine for the life of
    the call.
    """
    return subprocess.run(
        ["restic", "-r", repo, "--password-file", "/dev/stdin", *args],
        input=password, capture_output=True, text=True)


def add_key(old: str, new: str, repo: str) -> subprocess.CompletedProcess:
    """`restic key add`, with BOTH passwords passed as file descriptors.

    restic wants the current password and the new one from two different
    sources, and they cannot both be /dev/stdin. The three usual answers are
    all worse than this one:

      * `RESTIC_PASSWORD` in the environment — visible in `ps` output to every
        process on the machine for the life of the call
      * a temporary file — a plaintext passphrase on disk, however briefly, on
        the machine whose theft this backup exists to survive
      * a `--password-command` shell string — the secret ends up in an argv

    An anonymous pipe has none of those properties: it exists only between
    these two processes and has no name outside them.
    """
    import os

    r_old, w_old = os.pipe()
    r_new, w_new = os.pipe()
    os.write(w_old, old.encode())
    os.write(w_new, new.encode())
    os.close(w_old)
    os.close(w_new)

    try:
        return subprocess.run(
            ["restic", "-r", repo, "key", "add",
             "--password-file", f"/dev/fd/{r_old}",
             "--new-password-file", f"/dev/fd/{r_new}"],
            capture_output=True, text=True, pass_fds=(r_old, r_new))
    finally:
        os.close(r_old)
        os.close(r_new)


def keys(password: str, repo: str) -> list[tuple[str, bool]]:
    """(key id, is the one we are currently using)."""
    r = run(["key", "list"], password, repo)
    if r.returncode != 0:
        raise SystemExit(f"restic key list failed:\n{r.stderr.strip()}")
    out = []
    for line in r.stdout.splitlines():
        m = re.match(r"^(\*?)\s*([0-9a-f]{8})\s", line.strip())
        if m:
            out.append((m.group(2), bool(m.group(1))))
    return out


def snapshot_count(password: str, repo: str) -> int:
    """A real read. Proves the passphrase opens the repository AND that the
    repository has something in it."""
    r = run(["snapshots", "--json"], password, repo)
    if r.returncode != 0:
        raise SystemExit(f"could not read snapshots:\n{r.stderr.strip()}")
    import json
    return len(json.loads(r.stdout or "[]"))


def keychain_store(password: str) -> None:
    """Via `security -i`, so the secret never reaches argv, ps, or history."""
    script = (f"add-generic-password -a {Path.home().name} "
              f"-s {backup_ops.KEYCHAIN_SERVICE} -U -w {password}\n")
    r = subprocess.run(["security", "-i"], input=script,
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"security failed (exit {r.returncode}): "
                         f"{r.stderr.strip() or '(no message)'}")


def main() -> int:
    ensure_venv("LMS_ROTATEBACKUP_REEXEC", script=__file__)
    load_lms_env()

    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--check", action="store_true",
                   help="show the keys and verify the current passphrase; "
                        "change nothing")
    args = p.parse_args()

    try:
        repo = str(backup_ops.repo_path())
        old = backup_ops.restic_password()
    except backup_ops.BackupError as exc:
        print(exc, file=sys.stderr)
        return 2

    existing = keys(old, repo)
    current = [k for k, is_current in existing if is_current]
    n = snapshot_count(old, repo)

    print(f"repository: {repo}")
    print(f"keys:       {len(existing)} ({', '.join(k for k, _ in existing)})")
    print(f"in use:     {current[0] if current else '(unknown)'}")
    print(f"snapshots:  {n}")

    if args.check:
        return 0

    if n == 0:
        print("\nThe repository has no snapshots. Rotating the key of an empty "
              "repository is pointless — run ./ops/backup.py first, so that "
              "what the new key protects is something worth protecting.",
              file=sys.stderr)
        return 1

    new = generate()

    # --- 1. add the new key. The repository now opens with EITHER. -----------
    r = add_key(old, new, repo)
    if r.returncode != 0:
        print(f"\nCould not add a new key:\n{r.stderr.strip()}", file=sys.stderr)
        print("Nothing changed. The existing passphrase still works.",
              file=sys.stderr)
        return 1

    after = keys(old, repo)
    added = sorted({k for k, _ in after} - {k for k, _ in existing})
    if not added:
        print("\nrestic reported success but no new key appeared. Nothing has "
              "been removed and the existing passphrase still works.",
              file=sys.stderr)
        return 1

    # --- 2. prove the NEW passphrase alone opens the repository --------------
    #
    # This is the gate. D-037: a destructive step ran once on this project
    # because a verification had passed against something other than the thing
    # about to be destroyed.
    try:
        n_new = snapshot_count(new, repo)
    except SystemExit as exc:
        print(f"\nThe new key does not work: {exc}", file=sys.stderr)
        print("The OLD key has NOT been removed. The repository is fine — "
              "keep using the existing passphrase.", file=sys.stderr)
        return 1

    if n_new != n:
        print(f"\nThe new key reads {n_new} snapshots, the old one {n}. "
              f"Refusing to go further.", file=sys.stderr)
        return 1

    keychain_store(new)
    back = backup_ops.restic_password()
    if back != new:
        print("\nThe Keychain does not hold what was just generated. Both keys "
              "still work; nothing was removed.", file=sys.stderr)
        return 1

    # --- 3. and only now, remove the old ------------------------------------
    old_ids = [k for k, is_current in existing]
    removed = []
    for key_id in old_ids:
        r = run(["key", "remove", key_id], new, repo)
        if r.returncode == 0:
            removed.append(key_id)

    final = keys(new, repo)
    n_final = snapshot_count(new, repo)

    print("\n" + "=" * 66)
    print("  THE NEW BACKUP PASSPHRASE — copy it into the password manager NOW")
    print("=" * 66)
    print(f"\n    {new}\n")
    print("=" * 66)
    print("This is the only time it is printed. restic has no recovery path:")
    print("lose it and every snapshot is permanently unreadable — the backup")
    print("will still be there and nothing will open it.")
    print("=" * 66)
    print(f"\nold keys removed: {', '.join(removed) or 'none'}")
    print(f"keys now:         {', '.join(k for k, _ in final)}")
    print(f"snapshots:        {n_final}  (was {n})")
    print("\nThen clear the scrollback and the shell history:")
    print("    history -p && rm -f ~/.zsh_history && exec zsh")
    print("\nAnd prove the nightly job still works:")
    print("    ./ops/backup.py && ./ops/restore_test.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
