"""Closing a task.

The schema has had `status IN ('OPEN','DONE','DISMISSED')` and a
`completed_at` column since day one, and **nothing has ever written either**.
`insert_task` and `open_tasks` are the only functions that touch the table.

Which means: the first real task the system created — the jury duty summons,
the client's own worked example — would have sat at the top of every morning
brief for the rest of the year, growing more overdue each day, after he had
already served. A to-do list that cannot be crossed off stops being a to-do
list within about a week; it becomes a thing you scroll past, and then the
genuinely urgent item scrolls past with it.

DONE AND DISMISSED ARE NOT THE SAME FACT
----------------------------------------
Both close the task and both stop it appearing. They differ in what they say
about the system:

    DONE       he did the thing. The task was right to exist.
    DISMISSED  the task should never have been created.

Only the second is a defect report, and separating them is the only way to
find out whether the classifier invents work. If every close is DONE, the
question cannot be asked. `dismissals()` is the query that asks it.

REFERENCED BY ID, NEVER BY POSITION
-----------------------------------
The brief numbers tasks 1..7 by SCORE, and the score moves — a task that is
second this morning is first tomorrow because something else was completed or
because its own due date got closer. Closing "number 2" would close a
different task depending on when it was read. So closing takes the task id,
and `ops/done.py` prints ids next to titles.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..db import database as db

CLOSED = ("DONE", "DISMISSED")


class TaskError(RuntimeError):
    pass


@dataclass(frozen=True)
class CloseResult:
    task_id: int
    title: str
    status: str
    was_already: str | None = None


def get_task(conn: sqlite3.Connection, task_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise TaskError(f"no task {task_id}. `ops/done.py --list` shows the ids.")
    return row


def close_task(conn: sqlite3.Connection, task_id: int, *,
               status: str = "DONE", note: str | None = None,
               by: str = "human") -> CloseResult:
    """Mark a task DONE or DISMISSED. Never deletes it.

    The row stays, with `completed_at`, because "this was raised and handled"
    is the record — and because a task that vanishes cannot be shown to have
    been raised at all if someone later asks why nothing happened.

    Closing an already-closed task is reported, not raised: two people acting
    on the same brief is normal, and the second one should get "already done"
    rather than an error.
    """
    if status not in CLOSED:
        raise TaskError(f"status must be one of {CLOSED}, not {status!r}")

    row = get_task(conn, task_id)
    if row["status"] in CLOSED:
        return CloseResult(task_id=task_id, title=row["title"],
                           status=row["status"], was_already=row["status"])

    conn.execute(
        "UPDATE tasks SET status = ?, completed_at = ? WHERE id = ?",
        (status, db.now_iso(), task_id))

    detail = f"{row['title']}"
    if note:
        detail += f" — {note}"
    db.log_action(conn, f"TASK_{status}", artifact_id=row["source_artifact"],
                  detail=f"[{by}] {detail}"[:400])
    conn.commit()

    return CloseResult(task_id=task_id, title=row["title"], status=status)


def reopen_task(conn: sqlite3.Connection, task_id: int, *,
                by: str = "human") -> CloseResult:
    """Undo a close.

    Closing is one keystroke and the brief is read one-handed on a phone. The
    cost of a mistake has to be one keystroke back, or people hesitate over
    every close and the list stops getting used for the opposite reason.
    """
    row = get_task(conn, task_id)
    if row["status"] == "OPEN":
        return CloseResult(task_id=task_id, title=row["title"], status="OPEN",
                           was_already="OPEN")

    conn.execute(
        "UPDATE tasks SET status = 'OPEN', completed_at = NULL WHERE id = ?",
        (task_id,))
    db.log_action(conn, "TASK_REOPENED", artifact_id=row["source_artifact"],
                  detail=f"[{by}] {row['title']}"[:400])
    conn.commit()
    return CloseResult(task_id=task_id, title=row["title"], status="OPEN")


def dismissals(conn: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    """Tasks a human said should never have existed.

    The only direct evidence of the classifier inventing work. A run of these
    against one counterparty or one category is a taxonomy problem, and a
    steady trickle across everything is an urgency-threshold problem — but
    neither is visible unless DISMISSED is a distinct outcome from DONE.
    """
    return conn.execute(
        "SELECT * FROM tasks WHERE status = 'DISMISSED' "
        "ORDER BY completed_at DESC LIMIT ?", (limit,)).fetchall()
