"""The snapshot lms-orchestrator answers from (D-080, ops/agent_context.py)."""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))

import agent_context as ac                                   # noqa: E402
from core.db import database as db                         # noqa: E402

NOW = datetime(2026, 11, 5, 9, 0, tzinfo=db.TZ)


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "lms.db")
    yield c
    c.close()


def task(conn, title, *, due=None, entity="B_MRE", sensitivity="normal"):
    return db.insert_task(conn, title=title, due_date=due, urgency="NORMAL",
                          entity_id=entity, status="TRIAGED",
                          sensitivity=sensitivity)


def section(text, heading):
    """The lines under a heading, up to the next blank line."""
    _, rest = text.split(heading, 1)
    return rest.split("\n\n", 1)[0]


def test_snapshot_lists_open_tasks_with_numbers_and_overdue(conn):
    late = task(conn, "Pay Con Edison", due="2026-11-01")
    soon = task(conn, "File NY biennial", due="2026-11-20")
    text = ac.snapshot(conn, NOW, gaps=[])
    assert f"#{late} Pay Con Edison" in text
    assert f"#{soon} File NY biennial" in text
    assert "Open tasks: 2 (1 overdue)." in text
    assert "generated Thu Nov 05 09:00" in text


def test_snapshot_keeps_overdue_and_not_overdue_apart(conn):
    # Nov 6: asked "what's overdue?", the agent listed about 37 tasks, most
    # not overdue, from one mixed list. Two sections leave nothing to sort.
    late = task(conn, "Pay Con Edison", due="2026-11-01")
    soon = task(conn, "File NY biennial", due="2026-11-20")
    undated = task(conn, "Read the Hiscox policy")
    text = ac.snapshot(conn, NOW, gaps=[])
    over = section(text, "Overdue (1), the complete list:")
    rest = section(text, "Not overdue (2):")
    assert f"#{late} " in over
    assert f"#{soon} " not in over and f"#{undated} " not in over
    assert f"#{soon} " in rest and f"#{undated} " in rest
    assert f"#{late} " not in rest


def test_snapshot_says_when_nothing_is_overdue(conn):
    task(conn, "File NY biennial", due="2026-11-20")
    text = ac.snapshot(conn, NOW, gaps=[])
    assert "Overdue: none." in text
    assert "Open tasks: 1 (0 overdue)." in text


def test_snapshot_says_when_the_overdue_list_is_cut(conn):
    for i in range(ac.TASK_LIMIT + 3):
        task(conn, f"Late bill {i}", due="2026-10-01")
    text = ac.snapshot(conn, NOW, gaps=[])
    n = ac.TASK_LIMIT + 3
    assert f"Overdue ({n}), the first {ac.TASK_LIMIT}:" in text
    assert "the complete list" not in text
    assert "- … 3 more overdue (/lms tasks overdue)" in text


def test_snapshot_sends_lists_to_the_exact_command(conn):
    text = ac.snapshot(conn, NOW, gaps=[])
    assert "/lms tasks overdue" in text
    assert "no tables" in text


def test_snapshot_never_names_a_private_task(conn):
    tid = task(conn, "Dr. Patel follow-up", sensitivity="private")
    text = ac.snapshot(conn, NOW, gaps=[])
    assert "Patel" not in text
    assert f"#{tid}" in text


def test_snapshot_says_what_the_agent_may_do(conn):
    text = ac.snapshot(conn, NOW, gaps=[])
    assert "You cannot do anything" in text
    assert "/lms help" in text


def test_snapshot_lists_source_gaps(conn):
    text = ac.snapshot(conn, NOW, gaps=["Gmail (mleca): last good check 3 days ago"])
    assert "Gmail (mleca): last good check 3 days ago" in text


def test_snapshot_is_capped(conn):
    for i in range(400):
        task(conn, f"Task number {i} " + "x" * 80, due="2026-12-01")
    text = ac.snapshot(conn, NOW, gaps=[])
    assert len(text) <= ac.MAX_CHARS


def test_write_block_creates_the_file(tmp_path):
    f = tmp_path / "USER.md"
    ac.write_block(f, "hello")
    assert f.read_text(encoding="utf-8") == f"{ac.BEGIN}\nhello\n{ac.END}\n"


def test_write_block_keeps_what_is_outside_the_markers(tmp_path):
    f = tmp_path / "USER.md"
    f.write_text("# About Matthew\nHe prefers short answers.\n", encoding="utf-8")
    ac.write_block(f, "first")
    ac.write_block(f, "second")
    text = f.read_text(encoding="utf-8")
    assert text.startswith("# About Matthew\nHe prefers short answers.\n")
    assert "second" in text and "first" not in text
    assert text.count(ac.BEGIN) == 1 and text.count(ac.END) == 1
    assert not (tmp_path / "USER.md.tmp").exists()


def test_refresh_writes_nothing_while_halted(conn, tmp_path):
    flag = tmp_path / "HALTED.json"
    flag.write_text(json.dumps({"ts": "2026-11-05T08:00", "by": "telegram"}))
    assert ac.refresh(conn, NOW, workspace=tmp_path, flag=flag) is False
    assert not (tmp_path / ac.TARGET).exists()


def test_refresh_writes_when_running(conn, tmp_path):
    task(conn, "Pay Con Edison", due="2026-11-01")
    assert ac.refresh(conn, NOW, workspace=tmp_path,
                      flag=tmp_path / "HALTED.json") is True
    assert "Pay Con Edison" in (tmp_path / ac.TARGET).read_text(encoding="utf-8")


def test_a_corrupt_halt_flag_counts_as_halted(tmp_path):
    flag = tmp_path / "HALTED.json"
    flag.write_text("{not json")
    assert ac.read_halt_flag(flag) is not None
    assert ac.read_halt_flag(tmp_path / "absent.json") is None


def test_pending_approvals_leaves_out_expired(conn):
    tid = task(conn, "Con Edison bill")
    for ref, hours in (("AP-1", 5), ("AP-2", -5)):
        conn.execute(
            "INSERT INTO approvals (ref, task_id, basis, kind, action_json, fingerprint,"
            " policy_verdict, requested_by, requested_at, expires_at, status)"
            " VALUES (?, ?, 'HUMAN', 'pay_bill', '{}', ?, 'NEEDS_APPROVAL', 'finance', ?, ?,"
            " 'PENDING')",
            (ref, tid, ref, NOW.isoformat(), (NOW + timedelta(hours=hours)).isoformat()))
    assert [a["ref"] for a in ac.pending_approvals(conn, NOW)] == ["AP-1"]


def test_snapshot_cut_reaches_the_cap(conn):
    gaps = [f"Source {i}: " + "y" * 100 for i in range(300)]
    text = ac.snapshot(conn, NOW, gaps=gaps)
    assert len(text) <= ac.MAX_CHARS
    assert text.endswith("… (cut; /lms tasks has the full list)")


@pytest.mark.parametrize("content", ["{}", "null", "[]", "0"])
def test_a_falsy_halt_flag_still_counts_as_halted(tmp_path, content):
    flag = tmp_path / "HALTED.json"
    flag.write_text(content)
    assert ac.read_halt_flag(flag)
