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
    1. Ask Matthew to send any message to @MREOC18bot ("hello" is fine).
    2. ./ops/capture_telegram_id.py            # shows the sender(s)
    3. ./ops/capture_telegram_id.py --store    # saves the id for the kill switch

Run it BEFORE com.lms.haltbot is installed: getUpdates is destructive per
offset, and a running bot consumes the message first.

`--bot chat` reads the gateway's own bot instead (Phase 7b), whose id goes into
`commands.ownerAllowFrom` and `channels.telegram.allowFrom`.

The id is stored in the Keychain beside the token (lms/telegram-owner-id). It
is not a secret, but it is identity, and like the EINs (D-057) it stays off
git. --store refuses if more than one person has messaged the bot.
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.request

KEYCHAIN_ACCOUNT = "lms"
TOKEN_SERVICES = {
    "halt": "lms/telegram-halt-token",     # @MREOC18bot, the kill switch (D-059)
    "chat": "lms/telegram-bot-token",      # the gateway's bot (Phase 7b)
}
OWNER_SERVICE = "lms/telegram-owner-id"
KEYCHAIN_SERVICE = TOKEN_SERVICES["halt"]
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


def store_owner(uid: int) -> None:
    """-U updates an existing item, so re-pairing is the same command."""
    subprocess.run(
        ["/usr/bin/security", "add-generic-password", "-U",
         "-a", KEYCHAIN_ACCOUNT, "-s", OWNER_SERVICE, "-w", str(uid)],
        check=True, capture_output=True)


def main() -> int:
    global KEYCHAIN_SERVICE
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--bot", choices=sorted(TOKEN_SERVICES), default="halt")
    p.add_argument("--store", action="store_true",
                   help="save the single sender's id as the owner")
    args = p.parse_args()
    KEYCHAIN_SERVICE = TOKEN_SERVICES[args.bot]
    token = read_token()
    updates = get_updates(token)

    if not updates:
        print("No messages waiting.\n")
        print("Ask Matthew to send any message to the bot, then run this again.")
        print("\nTwo things that produce an empty result even when he HAS sent one:")
        print("  * Something already consumed the updates (getUpdates is")
        print("    destructive per offset): com.lms.haltbot for the halt bot,")
        print("    the gateway for the chat bot. Stop it, ask for a new message.")
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
    if args.store:
        store_owner(uid)
        print(f"Stored telegram:{uid} as the owner ({OWNER_SERVICE}).")
        print("The kill switch obeys this id and nobody else.\n")
    print("For the gateway (Phase 7b), set it with:\n")
    print(f"    openclaw config set commands.ownerAllowFrom '[\"telegram:{uid}\"]'")
    print("    openclaw gateway restart\n")
    print("Then confirm both of these clear:")
    print("    openclaw doctor --allow-exec        -> no 'command owner' warning")
    print("    openclaw security audit --deep      -> operator.read warning gone")
    return 0


if __name__ == "__main__":
    sys.exit(main())
