#!/usr/bin/env python3
"""Send pending alerts to Matthew on Telegram (R10, D-077).

    ./ops/notify.py               # what com.lms.notify runs every 15 minutes
    ./ops/notify.py --dry-run     # print what would go out now; send nothing
    ./ops/notify.py --test        # one test message; proves token, owner, route

What interrupts and how often is core/pipeline/notify.py. This file adds the
system-failure alerts (the brief's stalled-jobs list), asks the budget, and
sends whatever is pending as ONE message.

WHICH BOT
---------
@MREOCbot, the gateway's chat bot (Keychain `lms/telegram-bot-token`), to the
owner id the kill switch already uses. Not @MREOC18bot: that one is the kill
switch and nothing else (D-059). Telegram allows one reader of a bot's
updates, and the gateway is it; sending is not reading, so this does not
compete with it. A reply to an alert goes to the gateway's tool-less
orchestrator, which can do nothing with it.

Halted (D-059), it sends nothing: com.lms.notify is disabled with every other
job, and run by hand it stops at the flag.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _env import archive_volume_problem, load_lms_env      # noqa: E402
from _reexec import ensure_venv                            # noqa: E402

TOKEN_SERVICE = "lms/telegram-bot-token"                   # @MREOCbot (7b)
CHECKED = "NOTIFY_CHECKED"
FAILED = "NOTIFY_FAILED"
HEARTBEAT = timedelta(hours=1)


def last_row(conn) -> tuple[str, str] | None:
    r = conn.execute("SELECT action, ts FROM actions_log WHERE action IN (?, ?) "
                     "ORDER BY id DESC LIMIT 1", (CHECKED, FAILED)).fetchone()
    return (r["action"], r["ts"]) if r else None


def heartbeat(conn, now: datetime, detail: str) -> None:
    """NOTIFY_CHECKED at most hourly, so the brief can tell a stopped alert
    job from a quiet day (a push is not a heartbeat: most days have none)."""
    from core.db import database as db
    last = last_row(conn)
    if (last is None or last[0] != CHECKED
            or now - datetime.fromisoformat(last[1]) >= HEARTBEAT):
        db.log_action(conn, CHECKED, detail=detail[:400])
        conn.commit()


def run(conn, send, now: datetime, *, dry_run: bool = False) -> int:
    """One pass. `send(text)` raises RuntimeError on failure. Returns the
    exit status."""
    from core.db import database as db
    from core.pipeline import notify
    from core.reports import brief

    # The alert job's own row is the one stalled job it cannot report: if it
    # is not running, nothing here runs either. The brief covers that.
    stalled = [s for s in brief.stalled_jobs(conn, now)
               if not s.startswith("alerts:")]
    notify.for_system(conn, stalled, now.astimezone(notify.TZ).date())

    items = notify.pending(conn)
    wait = notify.why_not(items, notify.sent_times(conn), now)
    if wait:
        print(f"{len(items)} pending; not sent: {wait}")
        if not dry_run:
            heartbeat(conn, now, f"{len(items)} pending: {wait}")
        return 0

    text = notify.message(items)
    if dry_run:
        print(text)
        return 0
    try:
        send(text)
    except RuntimeError as exc:
        db.log_action(conn, FAILED, detail=str(exc)[:400])
        conn.commit()
        print(f"FAIL  {exc}", file=sys.stderr)
        return 1
    notify.mark_sent(conn, items)
    heartbeat(conn, now, f"sent {len(items)}")
    print(f"sent {len(items)}")
    return 0


def telegram():
    """(send, problem). The token is read here and never printed."""
    import halt_bot
    token = halt_bot.keychain(TOKEN_SERVICE)
    owner = halt_bot.keychain(halt_bot.OWNER_SERVICE)
    if not token:
        return None, f"no chat bot token in Keychain ({TOKEN_SERVICE})"
    if not owner or not owner.isdigit():
        return None, f"no numeric owner id in Keychain ({halt_bot.OWNER_SERVICE})"
    tg = halt_bot.Telegram(token)

    def send(text: str) -> None:
        tg.call("sendMessage", chat_id=int(owner), text=text,
                disable_web_page_preview="true")
    return send, None


def main() -> int:
    ensure_venv("LMS_NOTIFY_REEXEC", script=__file__)
    load_lms_env()
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--test", action="store_true")
    args = p.parse_args()

    import _halt
    flag = _halt.read_flag()
    if flag is not None:
        print(f"HALTED since {flag.get('ts')} (by {flag.get('by')}): nothing sent.")
        return 0

    send, problem = (None, None) if args.dry_run else telegram()
    if problem:
        print(f"FAIL  {problem}", file=sys.stderr)
        return 1
    if args.test:
        try:
            send("LMS alerts are connected. Urgent mail, phishing, legal or "
                 "tax notices and stopped jobs will be sent here: at most 3 a "
                 "day, 1 an hour, none 22:00-07:00 unless urgent.")
        except RuntimeError as exc:
            print(f"FAIL  {exc}", file=sys.stderr)
            return 1
        print("OK    test message sent")
        return 0

    problem = archive_volume_problem()
    if problem:
        print(f"archive unavailable: {problem}", file=sys.stderr)
        return 1
    from core.db import database as db
    archive = os.environ["LMS_ARCHIVE_ROOT"]
    conn = db.connect(os.environ.get("LMS_DB", str(Path(archive).parent / "lms.db")))
    try:
        return run(conn, send, datetime.now().astimezone(), dry_run=args.dry_run)
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
