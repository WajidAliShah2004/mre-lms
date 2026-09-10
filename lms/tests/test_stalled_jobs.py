"""The brief notices when part of the system has stopped.

Four launchd jobs run unattended on a machine in Matthew's office. If one dies
— a bad plist, a revoked token, a full disk, a Mac sitting at a locked login
screen after a power cut — the only symptom is that something stops arriving.

Absence is the hardest signal for a person to notice, and this whole build has
been a catalogue of things that failed silently while reporting success. So the
brief, which is the one thing he reads, says when the system itself is unwell.
"""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.db import database as db
from core.reports import brief as reports

OPS = Path(__file__).resolve().parents[1] / "ops"


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "lms.db")
    yield c
    c.close()


def logged_at(conn, action: str, when: datetime, ts: str | None = None) -> None:
    """Insert directly, with a chosen timestamp.

    Not log_action-then-UPDATE: the actions_log is append-only and the trigger
    refuses the UPDATE, which is that hard stop working exactly as intended.
    """
    conn.execute("INSERT INTO actions_log (ts, action, detail) VALUES (?, ?, ?)",
                 (ts if ts is not None else when.isoformat(timespec="seconds"),
                  action, "x"))
    conn.commit()


NOW = datetime(2026, 9, 12, 6, 30)


# ---------------------------------------------------------------------------
# The alarm has to be armed by something that really happens
# ---------------------------------------------------------------------------

def test_every_watched_action_is_written_somewhere():
    """The D-027 trap, which this feature walked straight into.

    `BACKUP_COMPLETED` was in the watch list before anything wrote it — the
    backup job left no trace in the database at all. The watch would have been
    permanently unarmed and the brief would have reported healthy silence
    forever, which is worse than no alarm because it looks like one.
    """
    sources = "\n".join(
        p.read_text(encoding="utf-8")
        for p in list(OPS.glob("*.py")) + list(
            (Path(__file__).resolve().parents[1] / "core").rglob("*.py")))

    for action in reports.WATCHED_JOBS:
        assert f'"{action}"' in sources, (
            f"the brief watches for {action} and nothing ever writes it — the "
            f"alarm can never fire, and its silence reads as health")


# ---------------------------------------------------------------------------
# When it fires
# ---------------------------------------------------------------------------

def test_a_job_that_has_gone_quiet_is_reported(conn):
    logged_at(conn, "MAIL_POLL_STOP", NOW - timedelta(days=3))
    stalled = reports.stalled_jobs(conn, NOW)
    assert len(stalled) == 1
    assert stalled[0].startswith("mail: last ran 3d ago")


def test_a_job_running_normally_is_silent(conn):
    logged_at(conn, "MAIL_POLL_STOP", NOW - timedelta(hours=11))
    assert reports.stalled_jobs(conn, NOW) == []


def test_one_missed_run_is_not_an_alarm(conn):
    """The mail poll runs twice a day. One skipped run is a Mac that was
    asleep. Crying wolf trains people to ignore the section, and then the real
    one goes past too."""
    logged_at(conn, "MAIL_POLL_STOP", NOW - timedelta(hours=25))
    assert reports.stalled_jobs(conn, NOW) == []


def test_a_job_that_has_never_run_is_not_reported(conn):
    """On a fresh machine that is every job, and an alarm that fires on day one
    is an alarm ignored by day two. The first successful run arms it."""
    assert reports.stalled_jobs(conn, NOW) == []


def test_only_the_most_recent_run_counts(conn):
    """An old row must not keep the alarm quiet, and must not raise one either."""
    logged_at(conn, "BACKUP_COMPLETED", NOW - timedelta(days=30))
    logged_at(conn, "BACKUP_COMPLETED", NOW - timedelta(hours=4))
    assert reports.stalled_jobs(conn, NOW) == []


def test_each_job_is_judged_on_its_own_schedule(conn):
    """Backup runs nightly, mail twice daily. One threshold for both would
    either nag about the backup or miss the mail."""
    logged_at(conn, "MAIL_POLL_STOP", NOW - timedelta(hours=40))
    logged_at(conn, "BACKUP_COMPLETED", NOW - timedelta(hours=40))

    stalled = reports.stalled_jobs(conn, NOW)
    assert len(stalled) == 1 and stalled[0].startswith("mail:")


def test_an_unparseable_timestamp_does_not_crash_the_brief(conn):
    logged_at(conn, "BRIEF_DELIVERED", NOW, ts="not a date")
    assert reports.stalled_jobs(conn, NOW) == []


# ---------------------------------------------------------------------------
# Where it appears
# ---------------------------------------------------------------------------

def test_it_is_printed_above_the_work(conn):
    """"Nothing needs you" means something very different when the mail poll
    died on Tuesday. He has to know that before he reads the rest."""
    logged_at(conn, "MAIL_POLL_STOP", NOW - timedelta(days=4))
    b = reports.build_brief(conn, kind="morning", now=NOW)
    text = reports.render_text(b)

    assert "THE SYSTEM NEEDS ATTENTION" in text
    assert text.index("THE SYSTEM NEEDS ATTENTION") < text.index("WHAT NEEDS YOU")
    assert "may be incomplete" in text


def test_a_healthy_system_says_nothing_about_itself(conn):
    logged_at(conn, "MAIL_POLL_STOP", NOW - timedelta(hours=11))
    text = reports.render_text(reports.build_brief(conn, kind="morning", now=NOW))
    assert "THE SYSTEM NEEDS ATTENTION" not in text


def test_a_stalled_job_alone_is_not_an_empty_brief(conn):
    """Otherwise the one message that matters is replaced by "nothing needs
    you" — which is exactly the claim that is not true."""
    logged_at(conn, "MAIL_POLL_STOP", NOW - timedelta(days=4))
    b = reports.build_brief(conn, kind="morning", now=NOW)

    assert not b.is_empty
    text = reports.render_text(b)
    assert "Nothing needs you" not in text
    assert "THE SYSTEM NEEDS ATTENTION" in text
