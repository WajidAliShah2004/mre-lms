#!/usr/bin/env python3
"""Authorise one mailbox, once. No 2-Step Verification, no app password.

Matthew signs in in a browser and clicks Allow. Google returns a refresh
token; that goes in the Keychain and the LMS uses it from then on. He never
does this again for that mailbox unless he revokes it.

WHY THIS ROUTE
--------------
Google stopped accepting legacy passwords for IMAP on 14 March 2025, so the
original plan — share the password, turn 2FA off — cannot work at all. The
alternatives were an app password (which REQUIRES 2-Step Verification) or
OAuth. This is OAuth through an **internal** Workspace app: no verification,
no CASA assessment, no service-account key with domain-wide reach, and no 2SV.

WHAT IS ASKED FOR
-----------------
`gmail.readonly`, and nothing else. The resulting token cannot delete a
message, cannot mark one read, and cannot write a label — three of the eight
hard stops in rules.yaml, enforced by Google refusing rather than by the LMS
declining. See core/adapters/gmail.py.

The consent screen will say "Read your email messages and settings". That is
the whole grant.

WHAT ENDS UP IN THE KEYCHAIN
----------------------------
The refresh token, the client id and the client secret, under
`lms/gmail-oauth/<address>`. All three, so the adapter needs nothing but the
Keychain and the downloaded JSON can be deleted afterwards.

Written via `security -i`, which reads from stdin, so no secret reaches argv,
`ps`, or shell history. That is the same mechanism as the restic passphrase,
for the same reason: pasting into a no-echo prompt on this machine has failed
four times.

    ./ops/authorise_gmail.py matthew@mrecai.com --client-json ~/Downloads/client_secret_*.json
    ./ops/authorise_gmail.py matthew@mrecai.com --check
"""

from __future__ import annotations

import argparse
import glob
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _reexec import ensure_venv                            # noqa: E402
from core.adapters import gmail                            # noqa: E402


def keychain_write(service: str, account: str, payload: dict) -> None:
    """Store the whole grant as one JSON blob, via stdin.

    One item rather than three: a half-updated credential — new refresh token
    beside an old client id — fails at the point of use with an error that
    describes neither.
    """
    blob = json.dumps(payload, separators=(",", ":"))
    script = f"add-generic-password -a {account} -s {service} -U -w {blob}\n"
    r = subprocess.run(["security", "-i"], input=script,
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"security failed (exit {r.returncode}): "
                         f"{r.stderr.strip() or '(no message)'}")


def keychain_read(service: str) -> dict | None:
    r = subprocess.run(
        ["security", "find-generic-password", "-s", service, "-w"],
        capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        return json.loads(r.stdout.rstrip("\n"))
    except ValueError:
        return None


def load_client(path_glob: str) -> tuple[str, str, Path]:
    matches = sorted(glob.glob(str(Path(path_glob).expanduser())))
    if not matches:
        raise SystemExit(f"no client JSON matching {path_glob}")
    path = Path(matches[0])
    data = json.loads(path.read_text(encoding="utf-8"))

    if "installed" not in data:
        kind = ", ".join(data) or "(empty)"
        raise SystemExit(
            f"{path.name} is a {kind!r} client. This flow needs a **Desktop "
            f"app** client — in the Cloud console: Clients → Create client → "
            f"Application type: Desktop app.")

    c = data["installed"]
    return c["client_id"], c["client_secret"], path


def main() -> int:
    ensure_venv("LMS_AUTHGMAIL_REEXEC", script=__file__)

    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("address")
    p.add_argument("--client-json", default="~/Downloads/client_secret_*.json")
    p.add_argument("--check", action="store_true",
                   help="report what is stored, and prove it still works")
    args = p.parse_args()

    service = gmail.keychain_service(args.address)

    # ---- check ---------------------------------------------------------
    if args.check:
        stored = keychain_read(service)
        if not stored:
            print(f"{service}: nothing stored")
            return 1
        missing = [k for k in ("refresh_token", "client_id", "client_secret")
                   if not stored.get(k)]
        if missing:
            print(f"{service}: incomplete — missing {missing}")
            return 1
        try:
            n = _smoke_test(stored, args.address)
        except Exception as exc:
            print(f"{service}: stored, but it does not work — {exc}")
            return 1
        print(f"{service}: works. {n} message(s) visible in the last day.")
        return 0

    # ---- authorise -----------------------------------------------------
    client_id, client_secret, json_path = load_client(args.client_json)

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("Missing libraries. Install them with:\n"
              "    .venv/bin/pip install google-api-python-client "
              "google-auth-oauthlib", file=sys.stderr)
        return 2

    print(f"Authorising {args.address}")
    print(f"  scope: {gmail.SCOPES[0]}")
    print("  a browser will open — sign in as this mailbox and click Allow\n")

    flow = InstalledAppFlow.from_client_secrets_file(
        str(json_path), scopes=list(gmail.SCOPES))
    creds = flow.run_local_server(port=0, prompt="consent",
                                  access_type="offline")

    if not creds.refresh_token:
        print("\nGoogle returned no refresh token. That happens when this "
              "mailbox was authorised before — revoke the old grant at "
              "myaccount.google.com/permissions and run this again.",
              file=sys.stderr)
        return 1

    # Google may narrow or widen what it grants. Check rather than assume.
    granted = set(getattr(creds, "scopes", []) or [])
    if granted and granted != set(gmail.SCOPES):
        print(f"\nGoogle granted {sorted(granted)}, not {list(gmail.SCOPES)}.",
              file=sys.stderr)
        if granted - set(gmail.SCOPES):
            print("That is WIDER than asked for. Nothing was stored — the "
                  "read-only guarantee is the point of this.", file=sys.stderr)
            return 1

    payload = {"refresh_token": creds.refresh_token,
               "client_id": client_id, "client_secret": client_secret,
               "address": args.address, "scopes": list(gmail.SCOPES)}
    keychain_write(service, args.address, payload)

    back = keychain_read(service)
    if not back or back.get("refresh_token") != creds.refresh_token:
        print("\nVERIFICATION FAILED: the Keychain does not hold what was "
              "just granted.", file=sys.stderr)
        return 1

    n = _smoke_test(payload, args.address)
    print(f"\nStored {service} and read it back unchanged.")
    print(f"Proved it works: {n} message(s) in the last day.")
    print(f"\nYou can delete {json_path} now — the client id and secret are "
          f"in the Keychain.")
    return 0


def _smoke_test(stored: dict, address: str) -> int:
    """One real read. Storing a credential that has never been used is how you
    find out at 06:30 that it does not work."""
    from google.oauth2.credentials import Credentials

    creds = Credentials(
        token=None, refresh_token=stored["refresh_token"],
        client_id=stored["client_id"], client_secret=stored["client_secret"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=list(gmail.SCOPES))
    client = gmail.GmailClient(gmail.GoogleTransport(creds))
    return len(client.recent(newer_than_days=1, limit=5))


if __name__ == "__main__":
    raise SystemExit(main())
