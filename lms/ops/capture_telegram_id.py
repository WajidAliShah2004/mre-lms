#!/usr/bin/env python3
"""Capture Matthew's numeric Telegram user id from his first message. Closes C2.

Why this exists
---------------
`commands.ownerAllowFrom` needs a NUMERIC id — "telegram:487392015" — not an
@handle. The Bot API only ever reports the numeric id on incoming updates,
because handles can be changed or unset and are therefore not identity. Put a
handle in that field and the owner allowlist matches nothing, which means
/halt — the sole remote kill switch under D-005 — has no authorised sender.

The obvious alternative is to ask Matthew to look his id up with a third-party
bot like @userinfobot. This is better: it needs nothing from him except the
message he has to send anyway to prove pairing works, and it involves no third
party touching his account.

The token is read from the Keychain, never passed as an argument. A token on a
command line lands in the process table and in shell history — the exact class
of exposure D-017 is about.

Usage
-----
    1. Ask Matthew to send any message to the bot ("hello" is fine).
    2. ./ops/capture_telegram_id.py

Then set the value it prints:

    openclaw config set commands.ownerAllowFrom '["telegram:<ID>"]'
    openclaw gateway restart
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.request

KEYCHAIN_ACCOUNT = "lms"
KEYCHAIN_SERVICE = "lms/telegram-bot-token"
API = "https://api.telegram.org/bot{token}/getUpdates"


def read_token() -> str:
    """Read the bot token from the login Keychain.

    Deliberately not an argument, an env var, or a file.
    """
    try:
        out = subprocess.run(
            ["/usr/bin/security", "find-generic-password",
             "-a", KEYCHAIN_ACCOUNT, "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError:
        sys.exit(
            f"No Keychain item for {KEYCHAIN_SERVICE!r}.\n"
            "Store it first (the -w with no value prompts, so the token never\n"
            "reaches the command line or your shell history):\n\n"
            f"    security add-generic-password -a {KEYCHAIN_ACCOUNT} "
            f"-s {KEYCHAIN_SERVICE} -w\n"
        )
    return out.stdout.strip()


def get_updates(token: str) -> list[dict]:
    url = API.format(token=token)
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            payload = json.load(r)
    except Exception as exc:
        sys.exit(f"Could not reach the Telegram API: {exc}\n"
                 "If egress filtering is already on, api.telegram.org must be "
                 "on the allowlist.")
    if not payload.get("ok"):
        sys.exit(f"Telegram API returned an error: {payload}")
    return payload.get("result", [])


def main() -> int:
    token = read_token()
    updates = get_updates(token)

    if not updates:
        print("No messages waiting.\n")
        print("Ask Matthew to send any message to the bot, then run this again.")
        print("\nTwo things that produce an empty result even when he HAS sent one:")
        print("  * The gateway is running and already consumed the updates —")
        print("    getUpdates is destructive per offset. Stop the gateway first:")
        print("        openclaw gateway stop")
        print("  * A webhook is set, which disables getUpdates entirely.")
        return 1

    senders: dict[int, dict] = {}
    for u in updates:
        msg = u.get("message") or u.get("edited_message") or {}
        frm = msg.get("from") or {}
        if frm.get("id"):
            senders[frm["id"]] = frm

    if not senders:
        print("Updates arrived but none carried a sender id.")
        return 1

    print(f"{len(senders)} distinct sender(s):\n")
    for uid, frm in senders.items():
        name = " ".join(filter(None, [frm.get("first_name"), frm.get("last_name")]))
        handle = f"@{frm['username']}" if frm.get("username") else "(no handle)"
        bot = " [BOT]" if frm.get("is_bot") else ""
        print(f"  telegram:{uid}    {name}  {handle}{bot}")

    print()
    if len(senders) > 1:
        print("MORE THAN ONE SENDER. Do not guess — confirm which is Matthew")
        print("before setting the allowlist. This field decides who can stop")
        print("the system.")
        return 1

    uid = next(iter(senders))
    print("Set it with:\n")
    print(f"    openclaw config set commands.ownerAllowFrom '[\"telegram:{uid}\"]'")
    print("    openclaw gateway restart\n")
    print("Then confirm both of these clear:")
    print("    openclaw doctor --allow-exec        -> no 'command owner' warning")
    print("    openclaw security audit --deep      -> operator.read warning gone")
    return 0


if __name__ == "__main__":
    sys.exit(main())
