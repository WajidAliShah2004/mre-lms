#!/usr/bin/env python3
"""The remote kill switch: /halt from Matthew's phone (D-005, D-059).

    ./ops/halt_bot.py            # run forever (com.lms.haltbot does this)
    ./ops/halt_bot.py --check    # token + owner present, bot reachable; exit

Bot: @MREOC18bot. Commands, from the owner only, in a private chat only:

    /halt     stop every LMS and OpenClaw job, persistently (ops/_halt.py)
    /status   halted or not, and the state of each job
    /help     this list

There is no /resume. A stolen or hijacked phone can stop the system and can
never restart it; resuming is ./ops/halt.py --resume at the Mac.

WHY A SEPARATE BOT AND NOT AN OPENCLAW COMMAND
----------------------------------------------
OpenClaw has no /halt. Its commands are /stop (abort the current reply) and
/restart; nothing stops the gateway from chat. And a kill switch that lives
inside the gateway cannot act when the gateway is the thing that is wedged,
which is when a kill switch is needed. This process depends on nothing but the
Python standard library, the Keychain and launchctl.

Telegram allows one getUpdates reader per bot. This bot is ONLY the kill
switch; the gateway's chat channel (Phase 7b) gets a bot of its own.

WHO IS OBEYED
-------------
`message.from.id` equal to the owner id in the Keychain, in a chat whose id is
also that id (a private chat). Everyone else gets NO reply at all: answering a
stranger confirms the bot is live and listening. They are logged, once each.
Group invitations are disabled in BotFather as well, so this is the second
line, not the only one.

A /halt that was sent while this process was down (the Mac rebooting) is
still honoured when it arrives, with the delay stated. Halting late is the
safe direction; dropping it is not.

SECRETS
-------
Token: Keychain `-a lms -s lms/telegram-halt-token`. Owner id: Keychain
`-a lms -s lms/telegram-owner-id`. Neither is an argument, an env var, or a
file, and neither is ever printed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _halt                                                # noqa: E402

KEYCHAIN_ACCOUNT = "lms"
TOKEN_SERVICE = "lms/telegram-halt-token"
OWNER_SERVICE = "lms/telegram-owner-id"
API = "https://api.telegram.org/bot{token}/{method}"
POLL_S = 50

HELP = ("Kill switch for the MRE LMS.\n"
        "/halt: stop everything now. It stays stopped through a restart.\n"
        "/status: what is running.\n"
        "Resuming is done at the Mac, never from here.")


def log(msg: str) -> None:
    print(f"{datetime.now().astimezone().isoformat(timespec='seconds')} {msg}",
          flush=True)


def keychain(service: str) -> str | None:
    r = subprocess.run(["/usr/bin/security", "find-generic-password",
                        "-a", KEYCHAIN_ACCOUNT, "-s", service, "-w"],
                       capture_output=True, text=True)
    value = r.stdout.strip()
    return value if r.returncode == 0 and value else None


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

class Telegram:
    def __init__(self, token: str):
        self._token = token

    def call(self, method: str, http_timeout: float = 15, **params) -> dict:
        """`params` go to Telegram as-is. The HTTP timeout is named
        http_timeout because getUpdates has its own `timeout` (the long-poll
        length); sharing the name crashed the daemon on its first poll on the
        Mac, Oct 6."""
        data = urllib.parse.urlencode(
            {k: json.dumps(v) if isinstance(v, (list, dict)) else v
             for k, v in params.items()}).encode()
        url = API.format(token=self._token, method=method)
        try:
            return self._call(url, data, method, http_timeout)
        except RuntimeError as exc:
            # The URL carries the token, and some errors quote the URL.
            raise RuntimeError(str(exc).replace(self._token, "<token>")) from None

    @staticmethod
    def _call(url: str, data: bytes, method: str, timeout: float) -> dict:
        try:
            with urllib.request.urlopen(url, data=data, timeout=timeout) as r:
                payload = json.load(r)
        except urllib.error.HTTPError as exc:
            # The URL contains the token: never let it reach a log.
            body = exc.read().decode("utf-8", "replace")[:300]
            raise RuntimeError(f"{method}: HTTP {exc.code} {body}") from None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise RuntimeError(f"{method}: {type(exc).__name__}: "
                               f"{getattr(exc, 'reason', exc)}") from None
        if not payload.get("ok"):
            raise RuntimeError(f"{method}: {payload.get('description')}")
        return payload

    def send(self, chat_id: int, text: str) -> None:
        try:
            self.call("sendMessage", chat_id=chat_id, text=text)
        except RuntimeError as exc:
            log(f"SEND_FAILED {exc}")


# ---------------------------------------------------------------------------
# Deciding what a message is
# ---------------------------------------------------------------------------

def command_of(text: str | None) -> str | None:
    """'/halt', '/HALT', '/halt@MREOC18bot', '/halt now' -> 'halt'."""
    if not text or not text.startswith("/"):
        return None
    word = text[1:].split(None, 1)[0] if len(text) > 1 else ""
    return word.split("@", 1)[0].lower() or None


def authorised(message: dict, owner_id: int) -> bool:
    frm = (message.get("from") or {}).get("id")
    chat = message.get("chat") or {}
    return (frm == owner_id and chat.get("id") == owner_id
            and chat.get("type") == "private")


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def record(action: str, detail: str) -> None:
    """Best effort into the audit log. Never blocks or undoes a halt.

    The database lives on the array; the kill switch must work on the day the
    array is not mounted, so a failure here is logged and ignored.
    """
    try:
        from _env import archive_volume_problem, load_lms_env
        load_lms_env()
        problem = archive_volume_problem()
        if problem:
            log(f"AUDIT_SKIPPED {action}: {problem}")
            return
        from core.db import database as db
        archive = os.environ["LMS_ARCHIVE_ROOT"]
        conn = db.connect(os.environ.get("LMS_DB",
                                         str(Path(archive).parent / "lms.db")))
        db.log_action(conn, action, detail=detail, approved_by="telegram-owner")
        conn.close()
    except Exception as exc:                                  # noqa: BLE001
        log(f"AUDIT_FAILED {action}: {type(exc).__name__}: {exc}")


def do_halt(sent_at: int | None) -> tuple[str, "_halt.HaltResult", float]:
    lag = time.time() - sent_at if sent_at else 0.0
    result = _halt.halt(by="telegram")
    reply = _halt.describe(result)
    if lag > 30:
        reply = (f"(Your /halt was sent {lag/60:.0f} min ago and only arrived "
                 f"now; applied on arrival.)\n") + reply
    log(f"SYSTEM_HALTED ok={result.ok} elapsed={result.elapsed:.2f}s "
        f"telegram_lag={lag:.1f}s targets={result.targets} "
        f"still={result.still_running} killed={result.killed}")
    return reply, result, lag


def do_status() -> str:
    s = _halt.status()
    h = s["halted"]
    head = (f"HALTED since {h.get('ts')} (by {h.get('by')})." if h
            else "Running (not halted).")
    rows = [f"{_halt.short(l)}: {st}" for l, st in s["jobs"]] or ["no jobs installed"]
    return "\n".join([head, *rows])


# ---------------------------------------------------------------------------

def handle(message: dict, owner_id: int, tg: Telegram, strangers: set) -> None:
    cmd = command_of(message.get("text"))
    if not authorised(message, owner_id):
        sender = (message.get("from") or {}).get("id")
        if sender not in strangers:
            strangers.add(sender)
            log(f"IGNORED sender={sender} chat_type="
                f"{(message.get('chat') or {}).get('type')} (not the owner)")
        return

    if cmd == "halt":
        tg.send(owner_id, "Halting…")
        reply, result, lag = do_halt(message.get("date"))
        tg.send(owner_id, reply)
        record("SYSTEM_HALTED",
               f"via telegram in {result.elapsed:.2f}s (lag {lag:.1f}s); "
               f"targets={result.targets}; still_running={result.still_running}")
    elif cmd == "status":
        tg.send(owner_id, do_status())
    else:
        tg.send(owner_id, HELP)


def serve(tg: Telegram, owner_id: int, *, max_polls: int | None = None) -> int:
    """Poll forever. `max_polls` exists so the loop itself can be tested:
    the Oct 6 crash was in this function, and every helper had passed."""
    offset = None
    strangers: set = set()
    backoff = 1
    log(f"haltbot up; owner configured; pid {os.getpid()}")
    polls = 0
    while max_polls is None or polls < max_polls:
        polls += 1
        try:
            params = {"timeout": POLL_S, "allowed_updates": ["message"]}
            if offset is not None:
                params["offset"] = offset
            updates = tg.call("getUpdates", http_timeout=POLL_S + 15, **params)["result"]
            backoff = 1
        except RuntimeError as exc:
            log(f"POLL_FAILED {exc}; retry in {backoff}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)
            continue
        for u in updates:
            offset = u["update_id"] + 1
            if u.get("message"):
                try:
                    handle(u["message"], owner_id, tg, strangers)
                except Exception as exc:                      # noqa: BLE001
                    log(f"HANDLE_FAILED {type(exc).__name__}: {exc}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--check", action="store_true")
    args = p.parse_args()

    token = keychain(TOKEN_SERVICE)
    owner = keychain(OWNER_SERVICE)
    problems = []
    if not token:
        problems.append(f"no bot token in Keychain ({TOKEN_SERVICE})")
    if not owner or not owner.lstrip("-").isdigit():
        problems.append(f"no numeric owner id in Keychain ({OWNER_SERVICE}); "
                        f"run ./ops/capture_telegram_id.py")
    if problems:
        log("NOT ARMED: " + "; ".join(problems) +
            ". /halt has no authorised sender. See ops/PHASE7_APPLY.md.")
        if not args.check:
            time.sleep(300)     # KeepAlive would otherwise restart us every 10 s
        return 1

    tg = Telegram(token)
    if args.check:
        try:
            me = tg.call("getMe")["result"]
        except RuntimeError as exc:
            print(f"FAIL  bot unreachable: {exc}")
            return 1
        print(f"OK    @{me.get('username')} reachable; owner id set "
              f"({len(owner)} digits)")
        return 0
    return serve(tg, int(owner))


if __name__ == "__main__":
    raise SystemExit(main())
