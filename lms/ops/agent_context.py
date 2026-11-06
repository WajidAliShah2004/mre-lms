#!/usr/bin/env python3
"""What lms-orchestrator knows when Matthew asks it something in plain words (D-080).

    ./ops/agent_context.py            # write the snapshot now
    ./ops/agent_context.py --print    # show it; write nothing

The agent has no tools (D-056), so it cannot look anything up. This writes a
short, read-only snapshot (open tasks, approvals waiting, sources behind) into
a workspace file OpenClaw puts in the agent's prompt, between two marker
lines. Everything else in that file is left as it is.

Hourly from com.lms.agentcontext, and after each /lms change (ops/remote.py).
Nothing is written while the system is halted: a halt stops writing, and the
gateway that would read it is stopped too.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

WORKSPACE = Path.home() / ".openclaw" / "workspace-lms-orchestrator"
TARGET = "USER.md"            # injected into the prompt (Task 1, Mac facts)
FLAG = Path.home() / "LMS" / "HALTED.json"      # same file as _halt.FLAG
BEGIN = "<!-- LMS_NOW:BEGIN (written by ops/agent_context.py; do not edit) -->"
END = "<!-- LMS_NOW:END -->"
MAX_CHARS = 15_000
TASK_LIMIT = 40


def read_halt_flag(flag: Path = FLAG) -> dict | None:
    """_halt.read_flag, for code that must import on Windows too.

    _halt.py does not import there (os.killpg). Same rule: a corrupt flag
    still means halted; damage is never read as running.
    """
    try:
        data = json.loads(flag.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"ts": "?", "by": "?", "corrupt": True}
    if isinstance(data, dict) and data:
        return data
    # Valid JSON that is empty or not an object ({}, [], null, 0) is damage too.
    return {"ts": "?", "by": "?", "corrupt": True}


def money(cents: int | None) -> str:
    return "" if cents is None else f"${cents / 100:,.2f}"


def pending_approvals(conn: sqlite3.Connection, now: datetime) -> list[sqlite3.Row]:
    from core.pipeline import approvals
    return [a for a in approvals.pending(conn)
            if datetime.fromisoformat(a["expires_at"]) > now]


def _task_lines(tasks: list, what: str, command: str) -> list[str]:
    out = [f"- #{t.task_id} {t.shown_title} · due {t.row['due_date'] or 'no due date'}"
           f" · {t.row['entity_id'] or '?'}" for t in tasks[:TASK_LIMIT]]
    if len(tasks) > TASK_LIMIT:
        out.append(f"- … {len(tasks) - TASK_LIMIT} more {what} ({command})")
    return out


def snapshot(conn: sqlite3.Connection, now: datetime, *,
             gaps: list[str] | None = None) -> str:
    from core.pipeline import coverage
    from core.reports import brief as reports

    lines = [
        f"LMS snapshot, generated {now:%a %b %d %H:%M} (New York time).",
        "Answer Matthew's questions about his tasks, approvals and sources only "
        "from this snapshot, and say when it was generated. You cannot do "
        "anything: for an action, give him the /lms command (/lms help lists them).",
        "For a list of tasks, copy the lines of the one section that answers "
        "the question, adding none and dropping none, as plain lines with no "
        "tables; a task in Not overdue is never overdue. The exact lists are "
        "/lms tasks overdue and /lms tasks: tell him so.",
        "",
    ]

    scored = reports.todo_list(conn, today=now.date())
    late = [t for t in scored if t.overdue]
    rest = [t for t in scored if not t.overdue]
    lines.append(f"Open tasks: {len(scored)} ({len(late)} overdue).")
    lines.append("")
    if not late:
        lines.append("Overdue: none.")
    else:
        shown = ("the complete list" if len(late) <= TASK_LIMIT
                 else f"the first {TASK_LIMIT}")
        lines.append(f"Overdue ({len(late)}), {shown}:")
        lines.extend(_task_lines(late, "overdue", "/lms tasks overdue"))
    lines.append("")
    if rest:
        lines.append(f"Not overdue ({len(rest)}):")
        lines.extend(_task_lines(rest, "not overdue", "/lms tasks"))
        lines.append("")

    private = reports.private_task_ids(conn)
    waiting = pending_approvals(conn, now)
    lines.append(f"Approvals waiting: {len(waiting)}.")
    for a in waiting:
        who = "(private)" if int(a["task_id"]) in private else (a["recipient"] or "")
        lines.append(f"- {a['ref']} {a['kind']} {who} {money(a['amount_cents'])} · "
                     f"expires {a['expires_at'][:16]}".replace("  ", " "))
    lines.append("")

    if gaps is None:
        gaps = coverage.disclosures(conn, now=now, active_only=True)
    lines.append("Sources behind: " + ("none." if not gaps else f"{len(gaps)}."))
    lines.extend(f"- {g}" for g in gaps)

    text = "\n".join(lines)
    if len(text) > MAX_CHARS:
        tail = "\n… (cut; /lms tasks has the full list)"
        text = text[: MAX_CHARS - len(tail)].rsplit("\n", 1)[0] + tail
    return text


def write_block(path: Path, block: str) -> None:
    """Replace what is between the markers; keep the rest; atomic."""
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    new_block = f"{BEGIN}\n{block}\n{END}"
    if BEGIN in old and END in old:
        head, rest = old.split(BEGIN, 1)
        _, tail = rest.split(END, 1)
        text = head + new_block + tail
    else:
        text = (old.rstrip("\n") + "\n\n" if old.strip() else "") + new_block + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def refresh(conn: sqlite3.Connection, now: datetime | None = None, *,
            workspace: Path = WORKSPACE, flag: Path = FLAG) -> bool:
    if read_halt_flag(flag):
        return False
    from core.db import database as db
    now = now or datetime.now(db.TZ)
    write_block(workspace / TARGET, snapshot(conn, now))
    return True


def main() -> int:
    from _reexec import ensure_venv
    ensure_venv("LMS_AGENTCONTEXT_REEXEC", script=__file__)
    from _env import load_lms_env
    load_lms_env()

    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--print", action="store_true", help="show it; write nothing")
    args = p.parse_args()

    flag = read_halt_flag()
    if flag and not args.print:
        print(f"HALTED since {flag.get('ts')}: snapshot not written.")
        return 0

    from core.db import database as db
    from done import connect
    conn = connect()
    try:
        now = datetime.now(db.TZ)
        if args.print:
            print(snapshot(conn, now))
            return 0
        refresh(conn, now)
        print(f"wrote {WORKSPACE / TARGET}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
