"""Closing a task — the human's side of the ledger (ledger.py is the rest).

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

LEDGER V2 (D-066)
-----------------
OPEN / DONE / DISMISSED became fifteen states. In those terms:

    done      -> CLOSED,   final_outcome DONE      (his word is the evidence)
    dismiss   -> CANCELED, final_outcome DISMISSED
    reopen    -> TRIAGED, from any of the three ending states

A human may close or cancel a task from any open state; nothing else may skip
a step (ledger.TRANSITIONS). DONE and DISMISSED are now outcomes, not states,
and the distinction above is kept in `final_outcome`.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..db import database as db
from . import ledger

OUTCOMES = ("DONE", "DISMISSED")
_TO = {"DONE": "CLOSED", "DISMISSED": "CANCELED"}

# Everything the ledger refuses is a TaskError to done.py.
TaskError = ledger.LedgerError


@dataclass(frozen=True)
class CloseResult:
    task_id: int
    title: str
    status: str
    outcome: str | None = None
    was_already: str | None = None


def get_task(conn: sqlite3.Connection, task_id: int) -> sqlite3.Row:
    return ledger.get(conn, task_id)


def close_task(conn: sqlite3.Connection, task_id: int, *,
               outcome: str = "DONE", note: str | None = None,
               by: str = "human") -> CloseResult:
    """Mark a task DONE or DISMISSED. Never deletes it.

    The row stays, with `completed_at`, because "this was raised and handled"
    is the record — and because a task that vanishes cannot be shown to have
    been raised at all if someone later asks why nothing happened.

    Closing an already-closed task is reported, not raised: two people acting
    on the same brief is normal, and the second one should get "already done"
    rather than an error.
    """
    if outcome not in OUTCOMES:
        raise TaskError(f"outcome must be one of {OUTCOMES}, not {outcome!r}")

    row = get_task(conn, task_id)
    if row["status"] in ledger.TERMINAL:
        was = row["final_outcome"] or row["status"]
        return CloseResult(task_id=task_id, title=row["title"],
                           status=row["status"], outcome=row["final_outcome"],
                           was_already=was)

    evidence = None
    if outcome == "DONE":
        evidence = f"attested by {by}" + (f": {note}" if note else "")
    moved = ledger.transition(conn, task_id, _TO[outcome], by=by,
                              expect=row["status"], outcome=outcome,
                              evidence=evidence, detail=note or outcome.lower())

    detail = f"{row['title']}"
    if note:
        detail += f" — {note}"
    db.log_action(conn, f"TASK_{outcome}", artifact_id=row["source_artifact"],
                  detail=f"[{by}] {detail}"[:400])
    conn.commit()

    return CloseResult(task_id=task_id, title=row["title"],
                       status=moved.to_status, outcome=outcome)


def reopen_task(conn: sqlite3.Connection, task_id: int, *,
                by: str = "human") -> CloseResult:
    """Undo a close.

    Closing is one keystroke and the brief is read one-handed on a phone. The
    cost of a mistake has to be one keystroke back, or people hesitate over
    every close and the list stops getting used for the opposite reason.
    """
    row = get_task(conn, task_id)
    if row["status"] not in ledger.TERMINAL:
        return CloseResult(task_id=task_id, title=row["title"],
                           status=row["status"], was_already=row["status"])

    moved = ledger.transition(conn, task_id, ledger.REOPEN_TO, by=by,
                              expect=row["status"], detail="reopened")
    db.log_action(conn, "TASK_REOPENED", artifact_id=row["source_artifact"],
                  detail=f"[{by}] {row['title']}"[:400])
    conn.commit()
    return CloseResult(task_id=task_id, title=row["title"], status=moved.to_status)


def mark_duplicate(conn: sqlite3.Connection, task_id: int, of: int, *,
                   by: str = "human") -> CloseResult:
    """The same obligation recorded twice. Fold it into the other; keep its evidence."""
    row = get_task(conn, task_id)
    if row["status"] == "DUPLICATE":
        return CloseResult(task_id=task_id, title=row["title"], status="DUPLICATE",
                           outcome="DUPLICATE", was_already="DUPLICATE")
    ledger.mark_duplicate(conn, task_id, of, by=by)
    conn.commit()
    return CloseResult(task_id=task_id, title=row["title"], status="DUPLICATE",
                       outcome="DUPLICATE")


def dismissals(conn: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    """Tasks a human said should never have existed.

    The only direct evidence of the classifier inventing work. A run of these
    against one counterparty or one category is a taxonomy problem, and a
    steady trickle across everything is an urgency-threshold problem — but
    neither is visible unless DISMISSED is a distinct outcome from DONE.
    """
    return conn.execute(
        "SELECT * FROM tasks WHERE final_outcome = 'DISMISSED' "
        "ORDER BY completed_at DESC LIMIT ?", (limit,)).fetchall()
