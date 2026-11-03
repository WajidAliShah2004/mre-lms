"""Delivering the brief.

The one thing that must not go wrong here is marking a brief sent that did not
arrive. `mark_brief_sent` moves the window for the NEXT brief's "filed since
last brief" section, so a false mark does not lose one brief — it removes those
documents from every future brief, permanently, because nothing looks back.
"""

import sys
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parents[1] / "ops"
sys.path.insert(0, str(OPS))

import deliver_brief                                          # noqa: E402
from core.db import database as db                            # noqa: E402
from core.reports import brief as reports                     # noqa: E402


# ---------------------------------------------------------------------------
# The write is verified before anything is marked
# ---------------------------------------------------------------------------

def test_a_written_brief_reads_back_identical(tmp_path):
    target = tmp_path / "briefs" / "2026-09-10 morning.txt"
    deliver_brief.write_verified(target, "WHAT NEEDS YOU (2)\n  1. Jury duty\n")
    assert target.read_text(encoding="utf-8").startswith("WHAT NEEDS YOU")


def test_it_creates_the_directory(tmp_path):
    """First run on a fresh machine, before bringup has made the folder."""
    target = tmp_path / "a" / "b" / "c" / "brief.txt"
    deliver_brief.write_verified(target, "x" * 100)
    assert target.exists()


def test_a_short_read_back_is_a_delivery_failure(tmp_path, monkeypatch):
    """iCloud will accept a write into a directory it has not materialised and
    hand back a file that reads short."""
    target = tmp_path / "brief.txt"

    real_read = Path.read_text

    def truncated(self, *a, **k):
        text = real_read(self, *a, **k)
        return text[:5] if self == target else text

    monkeypatch.setattr(Path, "read_text", truncated)

    with pytest.raises(deliver_brief.DeliveryError, match="did not complete"):
        deliver_brief.write_verified(target, "a much longer brief than five")


def test_an_unreadable_file_is_a_delivery_failure(tmp_path, monkeypatch):
    target = tmp_path / "brief.txt"

    def boom(self, *a, **k):
        raise OSError("volume not mounted")

    monkeypatch.setattr(Path, "read_text", boom)

    with pytest.raises(deliver_brief.DeliveryError, match="could not read it back"):
        deliver_brief.write_verified(target, "text")


# ---------------------------------------------------------------------------
# What a failed delivery must NOT do
# ---------------------------------------------------------------------------

def test_a_failed_delivery_does_not_move_the_window(tmp_path):
    """The whole point.

    If delivery fails and the brief is marked sent anyway, everything it
    covered drops out of every future brief — the window moves past those
    documents and nothing ever looks back at them.
    """
    conn = db.connect(tmp_path / "lms.db")
    assert reports.last_brief_at(conn, "morning") is None

    # Delivery failed, so mark_brief_sent was never reached.
    db.log_action(conn, "BRIEF_DELIVERY_FAILED", detail="volume not mounted")

    assert reports.last_brief_at(conn, "morning") is None, (
        "a failed delivery moved the window; those documents are now invisible "
        "to every future brief")
    conn.close()


def test_a_successful_delivery_does_move_the_window(tmp_path):
    """And the other half — otherwise every brief repeats yesterday's."""
    conn = db.connect(tmp_path / "lms.db")
    reports.mark_brief_sent(conn, "morning")
    assert reports.last_brief_at(conn, "morning") is not None
    conn.close()


def test_the_two_kinds_have_separate_windows(tmp_path):
    conn = db.connect(tmp_path / "lms.db")
    reports.mark_brief_sent(conn, "morning")
    assert reports.last_brief_at(conn, "evening") is None, (
        "the evening brief would start from the morning's boundary and skip "
        "everything filed during the day")
    conn.close()


# ---------------------------------------------------------------------------
# Where it goes
# ---------------------------------------------------------------------------

def test_the_default_is_icloud(monkeypatch):
    """It has to land somewhere his phone can see. A brief on the office Mac's
    local disk is a brief he never reads."""
    monkeypatch.delenv("LMS_BRIEF_DIR", raising=False)
    d = str(deliver_brief.brief_dir())
    assert "Mobile Documents" in d and "CloudDocs" in d


def test_lms_env_wins_over_the_default(monkeypatch, tmp_path):
    monkeypatch.setenv("LMS_BRIEF_DIR", str(tmp_path / "elsewhere"))
    assert deliver_brief.brief_dir() == tmp_path / "elsewhere"


def test_it_is_plain_text_not_markdown():
    """The iOS Files app previews .txt inline and offers .md as a download.
    One tap versus a download is the difference between read and unread."""
    assert deliver_brief.SUFFIX == ".txt"
    assert deliver_brief.LATEST.endswith(".txt")


# ---------------------------------------------------------------------------
# Escalation and redaction, through main() (R15)
# ---------------------------------------------------------------------------

@pytest.fixture
def mac(tmp_path, monkeypatch):
    """main() as the 06:30 job runs it, minus the venv re-exec and the volume
    check, against a database in tmp_path. One overdue task with an EIN in its
    title, never escalated."""
    monkeypatch.setattr(deliver_brief, "ensure_venv", lambda *a, **k: None)
    monkeypatch.setattr(deliver_brief, "load_lms_env", lambda *a, **k: {})
    monkeypatch.setattr(deliver_brief, "archive_volume_problem", lambda *a: None)
    monkeypatch.setenv("LMS_ARCHIVE_ROOT", str(tmp_path / "archive"))
    monkeypatch.setenv("LMS_DB", str(tmp_path / "lms.db"))
    monkeypatch.setenv("LMS_BRIEF_DIR", str(tmp_path / "briefs"))

    conn = db.connect(tmp_path / "lms.db")
    tid = db.insert_task(conn, title="EIN 93-4868827 penalty notice",
                         due_date="2020-01-01", urgency="HIGH",
                         entity_id="B_MRE", status="TRIAGED")
    conn.close()
    return tmp_path, tid


def escalations(tmp_path, tid):
    conn = db.connect(tmp_path / "lms.db")
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM task_events WHERE task_id = ? AND event = ?",
            (tid, reports.ESCALATED)).fetchone()[0]
    finally:
        conn.close()


def run(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["deliver_brief.py", *args])
    return deliver_brief.main()


def test_a_delivered_brief_escalates_and_is_redacted(mac, monkeypatch):
    tmp_path, tid = mac
    assert run(monkeypatch) == 0

    text = (tmp_path / "briefs" / deliver_brief.LATEST).read_text(encoding="utf-8")
    assert text.index("ACT NOW") < text.index("penalty notice")
    assert "4868827" not in text and "••-•••8827" in text
    assert escalations(tmp_path, tid) == 1

    # The next delivery does not escalate it again.
    assert run(monkeypatch) == 0
    assert escalations(tmp_path, tid) == 1
    text = (tmp_path / "briefs" / deliver_brief.LATEST).read_text(encoding="utf-8")
    assert "ACT NOW" not in text and "penalty notice" in text


def test_a_failed_delivery_does_not_mark_an_escalation(mac, monkeypatch):
    """Marked but never seen is worse than not escalated: it is never
    escalated again, and the record says it was."""
    tmp_path, tid = mac

    def fail(path, text):
        raise deliver_brief.DeliveryError("iCloud read back short")

    monkeypatch.setattr(deliver_brief, "write_verified", fail)
    assert run(monkeypatch) == 1
    assert escalations(tmp_path, tid) == 0


def test_a_dry_run_does_not_mark_an_escalation(mac, monkeypatch, capsys):
    tmp_path, tid = mac
    assert run(monkeypatch, "--dry-run") == 0
    assert "ACT NOW" in capsys.readouterr().out
    assert escalations(tmp_path, tid) == 0
