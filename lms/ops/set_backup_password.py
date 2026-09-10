#!/usr/bin/env python3
"""Generate the restic repository passphrase and store it in the Keychain.

WHY THIS EXISTS RATHER THAN A DOCUMENTED `security` COMMAND
-----------------------------------------------------------
Three attempts to create this item by hand produced, in order: an item with an
empty password stored silently, a "passwords don't match" mismatch, and
another empty one. The cause turned out to be the terminal, not the operator —
`5~5~5~5~...` appearing on the command line is the tail of the bracketed-paste
escape `ESC[200~` arriving as literal text. Paste was being mangled, so what
reached `security`'s no-echo prompt was neither what was on the clipboard nor
anything the typist could see.

`security add-generic-password -w` accepts an empty password without
complaint. Combined with a terminal that mangles paste, the failure is
completely silent, and the consequence is a restic repository encrypted with
"" — a plaintext backup with extra steps, which is worse than no backup
because it looks like one.

So the passphrase is never typed, never pasted, and never round-trips through
a terminal at all.

HOW THE SECRET STAYS OUT OF THE PROCESS TABLE
---------------------------------------------
`security -i` reads commands from stdin, so the password is written to a pipe
rather than passed as an argument. It therefore never appears in `ps`, never
reaches shell history, and is never a temp file. That is the one thing this
script does that a documented one-liner cannot.

It IS printed once, deliberately. restic has no recovery path — an
unrecorded passphrase is an unreadable backup — so a human has to capture it
exactly once, now, into the password manager.

    ./ops/set_backup_password.py            # generate, store, verify, show
    ./ops/set_backup_password.py --show     # print the existing one instead
"""

from __future__ import annotations

import argparse
import secrets
import string
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _reexec import ensure_venv                            # noqa: E402

SERVICE = "lms/restic-repo"
LENGTH = 40

# Letters and digits only. Punctuation survives a pipe perfectly well, but
# this value gets copied into a password manager and occasionally read aloud
# or retyped during a restore that is already going badly. Ambiguity is a
# worse trade than four extra characters of entropy.
ALPHABET = string.ascii_letters + string.digits


def generate() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))


def store(password: str, account: str) -> None:
    """Write via `security -i`, so the value is piped and never in argv."""
    script = (f"add-generic-password -a {account} -s {SERVICE} "
              f"-U -w {password}\n")
    r = subprocess.run(["security", "-i"], input=script,
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"security failed (exit {r.returncode}): "
                         f"{r.stderr.strip() or '(no message)'}")


def read_back() -> str:
    r = subprocess.run(
        ["security", "find-generic-password", "-s", SERVICE, "-w"],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"stored, but could not read it back "
                         f"(exit {r.returncode}): {r.stderr.strip()}")
    return r.stdout.rstrip("\n")


def show(password: str) -> None:
    bar = "=" * 68
    print()
    print(bar)
    print("  RESTIC REPOSITORY PASSPHRASE — record this NOW")
    print(bar)
    print()
    print(f"    {password}")
    print()
    print("  Put it in the password manager before the first backup runs.")
    print()
    print("  restic has no recovery path. If this is lost, every snapshot in")
    print("  the repository is permanently unreadable — still there, still")
    print("  encrypted, and of no use to anyone including Matthew. That makes")
    print("  it a credential-custody item alongside C7, not a detail of this")
    print("  script.")
    print(bar)


def main() -> int:
    ensure_venv("LMS_SETPW_REEXEC", script=__file__)

    p = argparse.ArgumentParser()
    p.add_argument("--show", action="store_true",
                   help="print the existing passphrase instead of replacing it")
    p.add_argument("--account", default=None)
    args = p.parse_args()

    if sys.platform != "darwin":
        print("The Keychain is macOS-only.", file=sys.stderr)
        return 2

    if args.show:
        existing = read_back()
        if not existing:
            print(f"{SERVICE} exists but holds an empty password.", file=sys.stderr)
            return 1
        show(existing)
        return 0

    import getpass
    account = args.account or getpass.getuser()

    password = generate()
    # -U updates in place if the item exists, so no delete step and no window
    # in which the machine has no passphrase at all.
    store(password, account)

    got = read_back()
    if got != password:
        print(f"\nVERIFICATION FAILED: stored {LENGTH} characters, read back "
              f"{len(got)}. Do NOT run a backup — the Keychain does not hold "
              f"what this script generated.", file=sys.stderr)
        return 1

    print(f"stored {SERVICE} ({len(got)} characters) and read it back "
          f"unchanged")
    show(password)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
