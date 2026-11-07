#!/usr/bin/env python3
"""Matthew's fixed commands from WhatsApp and Telegram (D-080).

    ./ops/remote.py --channel whatsapp --sender <id> -- tasks overdue

Called only by the lms-control OpenClaw plugin, once per /lms message. Prints
the reply; the plugin sends it back. Prints nothing for anyone who is not
Matthew, so a stranger gets no answer at all.

Code carries out every command; the model never chooses one (D-056 still
holds: lms-orchestrator has no tools). The list is fixed: help status brief
tasks coverage mail whatsapp imessage backup done approve deny halt. There is no
resume: a stolen phone can stop the Mac and never restart it (D-059).
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import subprocess
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent_context                                         # noqa: E402
from core.db import database as db                         # noqa: E402

MAX_REPLY = 3500
CUT = "\n… more on the Dashboard."
NUM = re.compile(r"^[0-9]{1,6}$")
OWNER_SERVICES = {"telegram": "lms/telegram-owner-id",
                  "whatsapp": "lms/whatsapp-owner"}
LOG = Path.home() / "LMS" / "logs" / "remote.log"

HELP = """LMS commands (send in this chat):
/lms status – halted or not, and each job
/lms brief – today's brief
/lms tasks – open tasks · /lms tasks overdue
/lms coverage – sources that are behind
/lms mail – read all mailboxes now
/lms whatsapp – read WhatsApp now
/lms imessage – read iMessage and texts now
/lms backup – back up now
/lms done 12 – close task 12 (shows it; add yes to close)
/lms approve 7 · /lms deny 7 – answer approval AP-7 (add yes)
/lms halt – stop everything now
Plain questions get an answer from today's snapshot.
There is no resume from a phone: resume is at the Mac."""


@dataclass
class Env:
    connect: Callable[[], sqlite3.Connection]
    owners: dict[str, str]
    flag: Path
    run: Callable[[list[str]], subprocess.CompletedProcess]
    spawn_halt: Callable[[str], None]
    status: Callable[[], dict]
    refresh: Callable[[sqlite3.Connection], None]
    now: Callable[[], datetime]
    uid: int
    log: Callable[[str], None]


def sender_id(raw: str | None) -> str:
    """'+1 555-123-4567', '15551234567@s.whatsapp.net', '1555...:12@...',
    'whatsapp:+1555...' -> digits. A leading 'channel:' is OpenClaw's own form."""
    base = re.sub(r"^[a-z]+:", "", (raw or "").strip())
    base = base.split("@", 1)[0].split(":", 1)[0]
    return re.sub(r"\D", "", base)


def is_owner(env: Env, channel: str, sender: str) -> bool:
    want = sender_id(env.owners.get(channel, ""))
    return bool(want) and want == sender_id(sender)


def cap(text: str) -> str:
    if len(text) <= MAX_REPLY:
        return text
    return text[: MAX_REPLY - len(CUT)].rsplit("\n", 1)[0] + CUT


def audit(env: Env, action: str, detail: str, approved_by: str | None = None) -> None:
    """Best effort, like halt_bot.record: never blocks a halt."""
    env.log(f"{action} {detail}")
    try:
        conn = env.connect()
        try:
            db.log_action(conn, action, detail=detail, approved_by=approved_by)
        finally:
            conn.close()
    except (Exception, SystemExit) as exc:                  # noqa: BLE001
        env.log(f"AUDIT_FAILED {action}: {type(exc).__name__}: {exc}")


# --- the commands ------------------------------------------------------------

def cmd_help(env, conn, args, channel):
    return HELP


def _last_good_checks(env) -> list[str]:
    """Best effort: status must answer even when the archive is not mounted."""
    try:
        conn = env.connect()
        try:
            rows = conn.execute(
                "SELECT CASE WHEN source_id LIKE 'email:%' THEN 'mail' "
                "WHEN source_id = 'chat:whatsapp' THEN 'whatsapp' "
                "ELSE 'imessage' END AS k, MAX(finished_at) FROM source_checks "
                "WHERE result = 'OK' AND (source_id LIKE 'email:%' "
                "OR source_id IN ('chat:whatsapp', 'messages:imessage')) "
                "GROUP BY k ORDER BY k").fetchall()
        finally:
            conn.close()
    except (Exception, SystemExit):                          # noqa: BLE001
        return ["(last checks unavailable: database not reachable)"]
    return [f"last good {k}: {ts[:16]}" for k, ts in rows]


def cmd_status(env, conn, args, channel):
    s = env.status()
    h = s["halted"]
    head = (f"HALTED since {h.get('ts')} (by {h.get('by')})." if h
            else "Running (not halted).")
    rows = [f"{label.split('.', 2)[-1]}: {state}" for label, state in s["jobs"]]
    return "\n".join([head, *(rows or ["no jobs installed"]),
                      *_last_good_checks(env)])


def cmd_brief(env, conn, args, channel):
    from core.pipeline.registry import load_registry
    from core.reports import brief as reports
    now = env.now()
    kind = "morning" if now.hour < 14 else "evening"
    return reports.render_text(reports.build_brief(conn, kind=kind, now=now),
                               load_registry())


def cmd_tasks(env, conn, args, channel):
    from core.reports import brief as reports
    scored = reports.todo_list(conn, today=env.now().date())
    if args == ["overdue"]:
        scored = [t for t in scored if t.overdue]
    if not scored:
        return "Nothing open." if args != ["overdue"] else "Nothing overdue."
    lines = []
    for t in scored:
        mark = "!! " if t.overdue else ""
        due = t.row["due_date"] or "no due date"
        lines.append(f"#{t.task_id} {mark}{t.shown_title} · due {due}")
    lines.append(f"\n{len(scored)} shown. Close one: /lms done <number>")
    return "\n".join(lines)


def cmd_coverage(env, conn, args, channel):
    from core.pipeline import coverage
    gaps = coverage.disclosures(conn, now=env.now())
    return ("Every registered source is on schedule." if not gaps
            else "\n".join(gaps))


JOBS = {"mail": "com.lms.mail", "whatsapp": "com.lms.whatsapp",
        "imessage": "com.lms.imessage", "backup": "com.lms.backup"}


def _halted_reply(flag: dict) -> str:
    return (f"Halted since {flag.get('ts')}. Nothing runs until it is resumed "
            f"at the Mac: ./ops/halt.py --resume")


def cmd_run_now(name: str):
    def run_now(env, conn, args, channel):
        flag = agent_context.read_halt_flag(env.flag)
        if flag:
            return _halted_reply(flag)
        label = JOBS[name]
        r = env.run(["launchctl", "kickstart", f"gui/{env.uid}/{label}"])
        if r.returncode != 0:
            return f"{name} is not installed on this Mac ({label})."
        return f"Started {name} now. /lms status shows it."
    return run_now


def cmd_halt(env, conn, args, channel):
    flag = agent_context.read_halt_flag(env.flag)
    env.spawn_halt(channel)
    if flag:
        return (f"Was already halted since {flag.get('ts')} (by {flag.get('by')}); "
                "halting again in 2 seconds. Everything stops, including this chat. "
                "@MREOC18bot /status shows the result. Resume is at the Mac only.")
    return ("Halting in 2 seconds. Everything stops, including this chat. "
            "@MREOC18bot /status shows the result. Resume is at the Mac only.")


def _refresh_best_effort(env, conn) -> None:
    """The change is already committed; a failed snapshot refresh must not
    change the reply or the audit result. The hourly job refreshes it anyway."""
    try:
        env.refresh(conn)
    except Exception as exc:   # noqa: BLE001
        env.log(f"snapshot refresh failed after commit: {type(exc).__name__}: {exc}")


def _shown_task_title(conn, task_id: int, title: str) -> str:
    from core.reports import brief as reports
    return (reports.private_line(task_id)
            if task_id in reports.private_task_ids(conn) else title)


def cmd_done(env, conn, args, channel):
    from core.pipeline import ledger, tasks as task_ops
    tid = int(args[0])
    try:
        row = ledger.get(conn, tid)
    except ledger.LedgerError:
        return f"No task #{tid}."
    title = _shown_task_title(conn, tid, row["title"])
    if args[1:] != ["yes"]:
        return (f"#{tid} {title} ({row['status']}, due {row['due_date'] or 'none'}).\n"
                f"Send /lms done {tid} yes to close it.")
    r = task_ops.close_task(conn, tid, note=f"via {channel}", by="matthew")
    _refresh_best_effort(env, conn)
    if r.was_already:
        return f"#{tid} was already {r.was_already.lower()}: {title}"
    return f"Closed #{tid}: {title}"


def cmd_answer(answer: str):
    def respond(env, conn, args, channel):
        from core.pipeline import approvals
        from core.reports import brief as reports
        n = args[0]
        ref = f"AP-{n}"
        try:
            a = approvals.get(conn, ref)
        except approvals.ApprovalError:
            return f"No approval {ref}."
        if args[1:] != ["yes"]:
            private = int(a["task_id"]) in reports.private_task_ids(conn)
            who = "(private)" if private else (a["recipient"] or "")
            return (f"{ref} {a['kind']} {who} {agent_context.money(a['amount_cents'])}"
                    f" · {a['status']} · expires {a['expires_at'][:16]}\n"
                    f"Send /lms {answer} {n} yes to {answer} it.").replace("  ", " ")
        try:
            res = approvals.decide(conn, ref, answer, by="matthew", note=f"via {channel}")
        except approvals.ApprovalError as exc:
            return str(exc)
        _refresh_best_effort(env, conn)
        word = {"approve": "approved", "deny": "denied"}[answer]
        extra = ("" if answer == "deny" or res.authorizes
                 else " (recorded as agreement only: a hard stop still applies)")
        return f"{ref} {word}.{extra}"
    return respond


COMMANDS: dict[str, Callable] = {
    "help": cmd_help,
    "status": cmd_status,
    "brief": cmd_brief,
    "tasks": cmd_tasks,
    "coverage": cmd_coverage,
    "mail": cmd_run_now("mail"),
    "whatsapp": cmd_run_now("whatsapp"),
    "imessage": cmd_run_now("imessage"),
    "backup": cmd_run_now("backup"),
    "halt": cmd_halt,
    "done": cmd_done,
    "approve": cmd_answer("approve"),
    "deny": cmd_answer("deny"),
}
NO_ARGS = {"help", "status", "brief", "coverage", "mail", "whatsapp", "imessage", "backup", "halt"}
TWO_STEP = {"done", "approve", "deny"}
CHANGES = {"done", "approve", "deny", "halt"}
NEEDS_DB = {"brief", "tasks", "coverage", "done", "approve", "deny"}


def well_formed(sub: str, args: list[str]) -> bool:
    if sub not in COMMANDS:
        return False
    if sub in NO_ARGS:
        return args == []
    if sub == "tasks":
        return args in ([], ["overdue"])
    if sub in TWO_STEP:
        return (len(args) in (1, 2) and bool(NUM.match(args[0]))
                and (len(args) == 1 or args[1] == "yes"))
    return False


def handle(words: list[str], channel: str, sender: str, env: Env) -> str:
    if channel not in OWNER_SERVICES or not is_owner(env, channel, sender):
        tail = sender_id(sender)[-4:] or "none"
        audit(env, "remote:refused", f"{channel} sender …{tail}")
        return ""

    words = [w.lower() for w in words] or ["help"]
    sub, args = words[0], words[1:]
    if not well_formed(sub, args):
        sub, args = "help", []

    by = "matthew" if sub in CHANGES and (sub == "halt" or args[-1:] == ["yes"]) else None
    try:
        if sub in NEEDS_DB:
            conn = env.connect()
            try:
                conn.execute("PRAGMA busy_timeout = 10000")
                reply = COMMANDS[sub](env, conn, args, channel)
            finally:
                conn.close()
        else:
            reply = COMMANDS[sub](env, None, args, channel)
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower():
            reply = "Busy, try again in a minute."
        else:
            env.log(traceback.format_exc())
            reply = "Failed. See ~/LMS/logs/remote.log."
    except SystemExit as exc:                    # done.connect: archive not mounted
        reply = str(exc) or "Failed. See ~/LMS/logs/remote.log."
    except Exception:                            # noqa: BLE001
        env.log(traceback.format_exc())
        reply = "Failed. See ~/LMS/logs/remote.log."

    audit(env, f"remote:{sub}",
          f"{channel} {' '.join(args)} -> {reply.splitlines()[0][:120] if reply else ''}",
          approved_by=by)
    return cap(reply)


# --- the Mac -----------------------------------------------------------------

def mac_env() -> Env:
    import _halt                                  # Mac only (os.killpg)
    from halt_bot import keychain
    from done import connect as done_connect

    def log(msg: str) -> None:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        with LOG.open("a", encoding="utf-8") as f:
            f.write(f"{stamp} {msg}\n")

    def run(argv: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(argv, capture_output=True, text=True, timeout=20)

    def spawn_halt(channel: str) -> None:
        """Detached and 2 s late, so this reply is sent before the gateway stops."""
        out = LOG.open("a", encoding="utf-8")
        subprocess.Popen(
            ["/bin/sh", "-c", 'sleep 2; exec "$0" "$1" --by "$2"',
             sys.executable, str(Path(__file__).with_name("halt.py")), channel],
            stdin=subprocess.DEVNULL, stdout=out, stderr=out,
            start_new_session=True, close_fds=True)

    def refresh(conn: sqlite3.Connection) -> None:
        agent_context.refresh(conn)

    return Env(connect=done_connect,
               owners={ch: keychain(svc) or "" for ch, svc in OWNER_SERVICES.items()},
               flag=_halt.FLAG, run=run, spawn_halt=spawn_halt,
               status=_halt.status, refresh=refresh,
               now=lambda: datetime.now(db.TZ), uid=os.getuid(), log=log)


def main(argv: list[str] | None = None) -> int:
    from _reexec import ensure_venv
    ensure_venv("LMS_REMOTE_REEXEC", script=__file__)
    from _env import load_lms_env
    load_lms_env()

    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--channel", required=True)
    p.add_argument("--sender", required=True)
    p.add_argument("words", nargs="*")
    a = p.parse_args(argv)
    reply = handle(a.words, a.channel, a.sender, mac_env())
    if reply:
        print(reply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
