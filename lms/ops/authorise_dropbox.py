#!/usr/bin/env python3
"""Authorise Dropbox for one account, once, read-only (R5, D-083).

The Dropbox app is created once in the Dropbox App Console (ops/DRIVES_APPLY.md
step 5): scoped access, full Dropbox, permissions files.metadata.read,
files.content.read and account_info.read and nothing else. Development
status is enough. Only its app key is needed: the grant uses PKCE, so there
is no app secret to keep.

Matthew opens the address this prints in a private browser window, signs in,
clicks Allow and pastes the code Dropbox shows. The offline refresh token goes
in the Keychain under lms/dropbox-oauth/<address>.

    ./ops/authorise_dropbox.py matthew@mrecai.com --app-key KEY
    ./ops/authorise_dropbox.py matthew@mrecai.com --check

Before saving, it asks Dropbox whose account the token reads, and refuses if
that is not the address given.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import secrets
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _reexec import ensure_venv                             # noqa: E402
from authorise_gmail import keychain_read, keychain_write   # noqa: E402
from core.adapters import dropbox                           # noqa: E402
from core.adapters.drive import wrong_account               # noqa: E402

AUTHORIZE = "https://www.dropbox.com/oauth2/authorize"


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode().rstrip("=")


def authorize_url(app_key: str, challenge: str) -> str:
    return AUTHORIZE + "?" + urllib.parse.urlencode({
        "client_id": app_key, "response_type": "code", "code_challenge": challenge,
        "code_challenge_method": "S256", "token_access_type": "offline",
        "scope": " ".join(dropbox.SCOPES)})


def scope_problem(granted: str | None) -> str | None:
    got = set((granted or "").split())
    if not got:
        return None
    extra = sorted(got - set(dropbox.SCOPES))
    if extra:
        return (f"Dropbox granted more than asked ({', '.join(extra)}). Nothing was "
                "stored: the read-only guarantee is the point of this.")
    missing = sorted(set(dropbox.SCOPES) - got)
    if missing:
        return (f"Dropbox did not grant {', '.join(missing)}. Check the app's "
                "Permissions tab, then run this again. Nothing was stored.")
    return None


def exchange(app_key: str, code: str, verifier: str, post=dropbox.urllib_post) -> dict:
    body = urllib.parse.urlencode({"code": code, "grant_type": "authorization_code",
                                   "code_verifier": verifier, "client_id": app_key}).encode()
    return json.loads(post(dropbox.TOKEN_URL, body,
                           {"Content-Type": "application/x-www-form-urlencoded"}))


def main() -> int:
    ensure_venv("LMS_AUTHDROPBOX_REEXEC", script=__file__)
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("address")
    p.add_argument("--app-key", help="the Dropbox app's key (not its secret)")
    p.add_argument("--check", action="store_true",
                   help="report what is stored, and prove it still works")
    args = p.parse_args()
    service = dropbox.keychain_service(args.address)

    if args.check:
        stored = keychain_read(service)
        if not stored:
            print(f"{service}: nothing stored")
            return 1
        try:
            api = dropbox.DropboxAPI(stored["app_key"], stored["refresh_token"])
            email = api.rpc("users/get_current_account", None)["email"]
        except Exception as exc:                              # noqa: BLE001
            print(f"{service}: stored, but it does not work: {exc}")
            return 1
        problem = wrong_account(args.address, email, "Dropbox")
        if problem:
            print(f"{service}: {problem}")
            return 1
        print(f"{service}: works, reads {email}'s Dropbox.")
        return 0

    if not args.app_key:
        p.error("--app-key is needed to authorise")
    verifier, challenge = pkce_pair()
    print(f"Authorising Dropbox for {args.address}")
    print(f"  scopes: {' '.join(dropbox.SCOPES)}\n")
    print("Open this in a private browser window, sign in as that account, click "
          "Allow, and copy the code Dropbox shows:\n")
    print(authorize_url(args.app_key, challenge) + "\n")
    code = input("Code: ").strip()
    if not code:
        print("No code given. Nothing was stored.", file=sys.stderr)
        return 1
    try:
        token = exchange(args.app_key, code, verifier)
    except dropbox.HTTPError as exc:
        print(f"Dropbox refused the code: {exc}. Nothing was stored.", file=sys.stderr)
        return 1
    if not token.get("refresh_token"):
        print("Dropbox returned no refresh token. Nothing was stored.", file=sys.stderr)
        return 1
    problem = scope_problem(token.get("scope"))
    if problem:
        print(problem, file=sys.stderr)
        return 1
    api = dropbox.DropboxAPI(args.app_key, token["refresh_token"])
    email = api.rpc("users/get_current_account", None)["email"]
    problem = wrong_account(args.address, email, "Dropbox")
    if problem:
        print(f"{problem} Nothing was stored.", file=sys.stderr)
        return 1

    payload = {"refresh_token": token["refresh_token"], "app_key": args.app_key,
               "address": args.address, "scopes": list(dropbox.SCOPES)}
    keychain_write(service, args.address, payload)
    back = keychain_read(service)
    if not back or back.get("refresh_token") != token["refresh_token"]:
        print("VERIFICATION FAILED: the Keychain does not hold what was just granted.",
              file=sys.stderr)
        return 1
    api.rpc("files/list_folder", {"path": "", "limit": 1})
    print(f"Stored {service} and read it back unchanged. It reads {email}'s Dropbox.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
