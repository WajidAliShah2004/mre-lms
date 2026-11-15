#!/usr/bin/env python3
"""Authorise Google Drive for one account, once, read-only (R5, D-083).

Matthew signs in and clicks Allow. The refresh token goes in the Keychain
under lms/gdrive-oauth/<address>, apart from the account's Gmail grant, so
each grant stays as narrow as its job.

WHAT IS ASKED FOR: drive.readonly and nothing else. The token can list and
download files. It cannot create, change, move, share or delete one: Google
refuses.

WHICH APP: the same internal Workspace app as the account's Gmail grant
(D-061), once the Drive API is enabled in its Cloud project and
drive.readonly is on its consent screen (ops/DRIVES_APPLY.md step 4). By
default the client id and secret come from the Gmail grant stored for the
same address.

    ./ops/authorise_gdrive.py matthew@mrecai.com
    ./ops/authorise_gdrive.py matthew@mrecai.com --check
    ./ops/authorise_gdrive.py matthew@mleca.com --client-json ~/Downloads/client_secret_*.json

The browser may already be signed in to another account: sign out of it, or
copy the address the script prints into a private window. Before saving,
the script asks Drive whose files the token reads, and refuses if that is
not the address given.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _reexec import ensure_venv                                         # noqa: E402
from authorise_gmail import keychain_read, keychain_write, load_client  # noqa: E402
from core.adapters import gdrive, gmail                                 # noqa: E402
from core.adapters.drive import wrong_account                           # noqa: E402

TOKEN_URI = "https://oauth2.googleapis.com/token"


def scope_problem(granted, wanted=gdrive.SCOPES) -> str | None:
    """Why the granted scopes are not exactly the one asked for, or None.
    An empty answer is not a problem: the token is checked by using it."""
    got = set(granted or ())
    if not got or got == set(wanted):
        return None
    missing = sorted(set(wanted) - got)
    if missing:
        return (f"Google did not grant {', '.join(missing)}. Is "
                "drive.readonly on the app's consent screen? Nothing was stored.")
    extra = sorted(got - set(wanted))
    return (f"Google granted more than asked ({', '.join(extra)}). Nothing was "
            "stored: the read-only guarantee is the point of this.")


def credentials(stored: dict):
    from google.oauth2.credentials import Credentials
    return Credentials(token=None, refresh_token=stored["refresh_token"],
                       client_id=stored["client_id"], client_secret=stored["client_secret"],
                       token_uri=TOKEN_URI, scopes=list(gdrive.SCOPES))


def main() -> int:
    ensure_venv("LMS_AUTHGDRIVE_REEXEC", script=__file__)
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("address")
    p.add_argument("--client-json", help="a downloaded Desktop-app client JSON")
    p.add_argument("--client-from", metavar="ADDRESS",
                   help="take the app from this address's Gmail grant (default: the same address)")
    p.add_argument("--check", action="store_true",
                   help="report what is stored, and prove it still works")
    args = p.parse_args()
    service = gdrive.keychain_service(args.address)

    if args.check:
        stored = keychain_read(service)
        if not stored:
            print(f"{service}: nothing stored")
            return 1
        try:
            email = gdrive.GoogleDriveAPI(credentials(stored)).user_email()
        except Exception as exc:                              # noqa: BLE001
            print(f"{service}: stored, but it does not work: {exc}")
            return 1
        problem = wrong_account(args.address, email, "Google Drive")
        if problem:
            print(f"{service}: {problem}")
            return 1
        print(f"{service}: works, reads {email}'s Drive.")
        return 0

    if args.client_json:
        client_id, client_secret, json_path = load_client(args.client_json)
    else:
        donor_address = args.client_from or args.address
        donor = keychain_read(gmail.keychain_service(donor_address))
        if not donor or not donor.get("client_id") or not donor.get("client_secret"):
            raise SystemExit(f"no Gmail grant stored for {donor_address} to take the app "
                             "from; use --client-json instead")
        client_id, client_secret, json_path = donor["client_id"], donor["client_secret"], None

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("Missing libraries: .venv/bin/pip install google-api-python-client "
              "google-auth-oauthlib", file=sys.stderr)
        return 2

    print(f"Authorising Google Drive for {args.address}")
    print(f"  scope: {gdrive.SCOPES[0]}")
    print("  a browser will open: sign in as this account and click Allow\n")
    flow = InstalledAppFlow.from_client_config(
        {"installed": {"client_id": client_id, "client_secret": client_secret,
                       "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                       "token_uri": TOKEN_URI, "redirect_uris": ["http://localhost"]}},
        scopes=list(gdrive.SCOPES))
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline",
                                  login_hint=args.address)
    if not creds.refresh_token:
        print("\nGoogle returned no refresh token. Revoke the old grant at "
              "myaccount.google.com/permissions and run this again.", file=sys.stderr)
        return 1
    problem = scope_problem(getattr(creds, "scopes", None))
    if problem:
        print(f"\n{problem}", file=sys.stderr)
        return 1
    email = gdrive.GoogleDriveAPI(creds).user_email()
    problem = wrong_account(args.address, email, "Google Drive")
    if problem:
        print(f"\n{problem} Nothing was stored. Sign out of that account in the "
              "browser (or use a private window) and run this again.", file=sys.stderr)
        return 1

    payload = {"refresh_token": creds.refresh_token, "client_id": client_id,
               "client_secret": client_secret, "address": args.address,
               "scopes": list(gdrive.SCOPES)}
    keychain_write(service, args.address, payload)
    back = keychain_read(service)
    if not back or back.get("refresh_token") != creds.refresh_token:
        print("\nVERIFICATION FAILED: the Keychain does not hold what was just granted.",
              file=sys.stderr)
        return 1
    print(f"\nStored {service} and read it back unchanged. It reads {email}'s Drive.")
    if json_path:
        print(f"\nYou can delete {json_path} now: the client id and secret are in the Keychain.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
