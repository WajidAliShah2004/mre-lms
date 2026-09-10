"""Closing a task.

The schema has had `status IN ('OPEN','DONE','DISMISSED')` and a
`completed_at` column since day one and nothing ever wrote either. The jury
duty summons — the client's own worked example — would have sat at the top of
every morning brief for the rest of the year, growing more overdue each day,
after he had already served.

Same shape as D-027 (an overrides block nothing read) and D-036 (hard stops
held only by absence): the schema described a behaviour that did not exist.
"""

from datetime import date, timedelta

import pytest

from core.db import database as db
from core.pipeline import tasks as task_ops
from core.reports import brief as reports


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "lms.db")
    yield c
    c.close()


def a_task(conn, title="Jury duty summons — Nassau County Superior Court",
           due=None, urgency="HIGH"):
    return db.insert_task(conn, title=title, urgency=urgency,
                          due_date=due or date.today().isoformat(),
                          entity_id="UNASSIGNED")


# ---------------------------------------------------------------------------
# The point of the whole thing
# ---------------------------------------------------------------------------

def test_a_done_task_leaves_the_brief(conn):
    """Without this the list cannot be crossed off, and a list that cannot be
    crossed off becomes a thing you scroll past — taking the genuinely urgent
    item with it."""
    tid = a_task(conn)
    assert [t.task_id for t in reports.todo_list(conn)] == [tid]

    task_ops.close_task(conn, tid)

    assert reports.todo_list(conn) == []
    b = reports.build_brief(conn, kind="morning")
    assert b.tasks == []


def test_the_row_is_kept_not_deleted(conn):
    """"This was raised and handled" is the record. A task that vanishes
    cannot be shown to have been raised at all."""
    tid = a_task(conn)
    task_ops.close_task(conn, tid)

    row = task_ops.get_task(conn, tid)
    assert row["status"] == "DONE"
    assert row["completed_at"], "closed without recording when"


def test_closing_is_in_the_audit_log(conn):
    tid = a_task(conn)
    task_ops.close_task(conn, tid, note="served 10 Sep")

    row = conn.execute(
        "SELECT action, detail FROM actions_log WHERE action LIKE 'TASK_%'"
    ).fetchone()
    assert row["action"] == "TASK_DONE"
    assert "served 10 Sep" in row["detail"]


# ---------------------------------------------------------------------------
# DONE and DISMISSED are different facts
# ---------------------------------------------------------------------------

def test_dismissed_is_recorded_separately_from_done(conn):
    """Both close it. Only one says the task should never have existed, and
    that is the only direct evidence of the classifier inventing work."""
    did = a_task(conn, title="Reply to newsletter")
    done = a_task(conn, title="Pay invoice 7010")

    task_ops.close_task(conn, did, status="DISMISSED")
    task_ops.close_task(conn, done, status="DONE")

    dismissed = task_ops.dismissals(conn)
    assert [r["title"] for r in dismissed] == ["Reply to newsletter"]


def test_both_outcomes_remove_it_from_the_list(conn):
    a = a_task(conn, title="one")
    b = a_task(conn, title="two")
    task_ops.close_task(conn, a, status="DONE")
    task_ops.close_task(conn, b, status="DISMISSED")
    assert reports.todo_list(conn) == []


def test_an_invented_status_is_refused(conn):
    tid = a_task(conn)
    with pytest.raises(task_ops.TaskError, match="status must be"):
        task_ops.close_task(conn, tid, status="MAYBE")
    assert task_ops.get_task(conn, tid)["status"] == "OPEN"


# ---------------------------------------------------------------------------
# Closing by id, never by position
# ---------------------------------------------------------------------------

def test_the_id_is_stable_while_the_ranking_is_not(conn):
    """The brief numbers 1..7 by score, and the score moves. "Done number 2"
    means a different task depending on when it is said."""
    soon = a_task(conn, title="due in ten days",
                  due=(date.today() + timedelta(days=10)).isoformat())
    later = a_task(conn, title="due in twenty days",
                   due=(date.today() + timedelta(days=20)).isoformat())

    ranked = reports.todo_list(conn)
    assert ranked[0].task_id == soon

    task_ops.close_task(conn, soon)

    ranked = reports.todo_list(conn)
    assert ranked[0].task_id == later, "the task now at position 1 changed"
    # ...and `later`'s id did not.
    assert task_ops.get_task(conn, later)["status"] == "OPEN"


def test_an_unknown_id_says_how_to_find_the_right_one(conn):
    with pytest.raises(task_ops.TaskError, match="--list"):
        task_ops.close_task(conn, 9999)


# ---------------------------------------------------------------------------
# Doing it twice, and undoing it
# ---------------------------------------------------------------------------

def test_closing_twice_reports_rather_than_raises(conn):
    """Two people acting on the same brief is normal. The second should get
    "already done", not a stack trace."""
    tid = a_task(conn)
    task_ops.close_task(conn, tid)
    again = task_ops.close_task(conn, tid)
    assert again.was_already == "DONE"


def test_closing_twice_does_not_move_the_completion_time(conn):
    tid = a_task(conn)
    first = task_ops.close_task(conn, tid)
    when = task_ops.get_task(conn, tid)["completed_at"]
    task_ops.close_task(conn, tid, status="DISMISSED")
    assert task_ops.get_task(conn, tid)["completed_at"] == when
    assert task_ops.get_task(conn, tid)["status"] == "DONE", (
        "a second close rewrote the first one's outcome")
    assert first.status == "DONE"


def test_reopening_puts_it_back(conn):
    """Closing is one keystroke on a phone. The cost of a mistake has to be
    one keystroke back, or people hesitate over every close and the list stops
    being used for the opposite reason."""
    tid = a_task(conn)
    task_ops.close_task(conn, tid)
    task_ops.reopen_task(conn, tid)

    row = task_ops.get_task(conn, tid)
    assert row["status"] == "OPEN"
    assert row["completed_at"] is None
    assert [t.task_id for t in reports.todo_list(conn)] == [tid]


def test_reopening_an_open_task_is_harmless(conn):
    tid = a_task(conn)
    r = task_ops.reopen_task(conn, tid)
    assert r.was_already == "OPEN"


def test_reopening_is_in_the_audit_log(conn):
    """The log is append-only, so a close followed by a reopen leaves both —
    which is the record of someone changing their mind, not a contradiction."""
    tid = a_task(conn)
    task_ops.close_task(conn, tid)
    task_ops.reopen_task(conn, tid)

    actions = [r["action"] for r in conn.execute(
        "SELECT action FROM actions_log WHERE action LIKE 'TASK_%' ORDER BY id")]
    assert actions == ["TASK_DONE", "TASK_REOPENED"]
