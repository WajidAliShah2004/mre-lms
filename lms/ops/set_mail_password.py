#!/usr/bin/env python3
"""Store a Gmail app password in the Keychain, without a terminal prompt.

WHY NOT `security add-generic-password -w`
------------------------------------------
Because it does not work on this machine. Pasting into that prompt has now
failed four times across two credentials: the restic passphrase stored empty
twice and mismatched once, and a Gmail app password stored as 5 characters
instead of 16. The cause is bracketed paste arriving as literal text — the
`5~5~5~...` seen on the command line — so what reaches the no-echo prompt is
neither the clipboard nor anything the typist can see.

A no-echo prompt plus a terminal that mangles paste plus a `security` command
that accepts a short value in silence is three failures that only announce
themselves later, at the point of use.

So: Google's app-password dialog has a copy button. The value goes clipboard →
here → Keychain, and is never typed, never pasted into a prompt, and never
displayed.

HOW THE SECRET STAYS OUT OF THE PROCESS TABLE
---------------------------------------------
`security -i` reads its commands from stdin, so the password travels down a
pipe rather than in argv. It never appears in `ps`, never reaches shell
history, and is never written to a file.

    ./ops/set_mail_password.py matthew@mrecai.com     # from the clipboard
    ./ops/set_mail_password.py matthew@mrecai.com --check
    ./ops/set_mail_password.py --list
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _reexec import ensure_venv                            # noqa: E402

SERVICE_PREFIX = "lms/gmail/"

# Google issues 16 characters, displayed as four groups of four. The spaces
# are presentation only and must be stripped — a 19-character "password"
# including them authenticates against nothing.
APP_PASSWORD_CHARS = 16

_ADDRESS = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def clipboard() -> str:
    r = subprocess.run(["pbpaste"], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"could not read the clipboard: {r.stderr.strip()}")
    return r.stdout


def normalise(raw: str) -> str:
    """Strip every kind of whitespace Google's dialog might carry."""
    return "".join(raw.split())


def store(service: str, account: str, password: str) -> None:
    script = f"add-generic-password -a {account} -s {service} -U -w {password}\n"
    r = subprocess.run(["security", "-i"], input=script,
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"security failed (exit {r.returncode}): "
                         f"{r.stderr.strip() or '(no message)'}")


def read_back(service: str) -> str:
    r = subprocess.run(
        ["security", "find-generic-password", "-s", service, "-w"],
        capture_output=True, text=True)
    if r.returncode != 0:
        return ""
    return r.stdout.rstrip("\n")


def list_stored() -> int:
    r = subprocess.run(["security", "dump-keychain"],
                       capture_output=True, text=True)
    found = sorted(set(re.findall(rf'"({re.escape(SERVICE_PREFIX)}[^"]+)"',
                                  r.stdout)))
    if not found:
        print("No mailbox passwords stored yet.")
        return 1
    for service in found:
        n = len(read_back(service))
        state = "ok" if n == APP_PASSWORD_CHARS else f"WRONG LENGTH ({n})"
        print(f"  {service:<45} {state}")
    return 0


def main() -> int:
    ensure_venv("LMS_SETMAIL_REEXEC", script=__file__)

    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("address", nargs="?", help="the mailbox, e.g. matthew@mrecai.com")
    p.add_argument("--check", action="store_true",
                   help="report what is stored without changing it")
    p.add_argument("--list", action="store_true",
                   help="every mailbox password stored, and whether it looks right")
    args = p.parse_args()

    if sys.platform != "darwin":
        print("The Keychain is macOS-only.", file=sys.stderr)
        return 2
    if args.list:
        return list_stored()
    if not args.address:
        p.error("give a mailbox address, or use --list")
    if not _ADDRESS.match(args.address):
        p.error(f"{args.address!r} does not look like an email address")

    service = SERVICE_PREFIX + args.address

    if args.check:
        n = len(read_back(service))
        if n == 0:
            print(f"{service}: nothing stored")
            return 1
        if n != APP_PASSWORD_CHARS:
            print(f"{service}: {n} characters — an app password is "
                  f"{APP_PASSWORD_CHARS}. This will not authenticate.")
            return 1
        print(f"{service}: {n} characters, looks right")
        return 0

    password = normalise(clipboard())

    if not password:
        print("The clipboard is empty. Copy the app password from Google's "
              "dialog first — it has a copy button.", file=sys.stderr)
        return 1
    if len(password) != APP_PASSWORD_CHARS:
        # Say what was found without printing it. A wrong length is almost
        # always the clipboard holding something else entirely.
        print(f"The clipboard holds {len(password)} characters; an app "
              f"password is {APP_PASSWORD_CHARS}.\n"
              f"Nothing was stored. Copy the app password again — the four "
              f"groups of four from Google's dialog.", file=sys.stderr)
        return 1
    if not password.isalnum():
        print("The clipboard contains punctuation. Google's app passwords are "
              "letters only — this is probably the account password, not an "
              "app password. Nothing was stored.", file=sys.stderr)
        return 1

    store(service, args.address, password)

    got = read_back(service)
    if got != password:
        print(f"\nVERIFICATION FAILED: stored {len(password)} characters, read "
              f"back {len(got)}. Do NOT rely on this — the Keychain does not "
              f"hold what the clipboard did.", file=sys.stderr)
        return 1

    print(f"stored {service} ({len(got)} characters) and read it back unchanged")

    # Leave the clipboard clean. The value is in the Keychain now, and a
    # 16-character secret sitting in the paste buffer is one Cmd-V from
    # somewhere it should not be.
    subprocess.run(["pbcopy"], input="", text=True)
    print("clipboard cleared")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
