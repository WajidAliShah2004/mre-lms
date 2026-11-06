"""Telegram alerts (R10, D-077): what interrupts, and how often.

Telegram is faked: `send` is a list. Everything else is real, including
ingest, the database and the brief's stalled-jobs list.
"""

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from core.adapters.lmstudio import Completion
from core.db import database as db
from core.pipeline import filing, notify
from core.pipeline.classify import Artifact, Classifier
from core.pipeline.ingest import ingest_file
from core.pipeline.registry import load_registry

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))
import notify as notify_job                                  # noqa: E402

CONFIG = Path(__file__).resolve().parents[1] / "config"
ET = ZoneInfo("America/New_York")
TODAY = date(2026, 11, 4)


def at(hour, minute=0, day=4):
    return datetime(2026, 11, day, hour, minute, tzinfo=ET)


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "lms.db")
    yield c
    c.close()


def artifact(conn, n):
    return db.insert_artifact(conn, sha256=f"{n:064d}", source="email")


def item(i=1, critical=False, reason="LEGAL"):
    return notify.Pending(i, reason, critical, "x", "")


# ---------------------------------------------------------------------------
# What interrupts
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("urgency,category,want", [
    ("CRITICAL", "VENDORS", "CRITICAL"),
    ("HIGH", "LEGAL", "LEGAL"),
    ("HIGH", "TAX", "LEGAL"),
    ("NORMAL", "LEGAL", None),       # formation papers, an agreement: the brief
    ("HIGH", "VENDORS", None),       # an invoice due soon: the brief
    ("NONE", "LEGAL", None),
])
def test_what_interrupts(urgency, category, want):
    assert notify.reason_for(urgency, category) == want


def test_a_backfill_of_old_mail_is_not_news():
    assert notify.is_fresh("2026-11-03", TODAY)
    assert notify.is_fresh("2026-11-02", TODAY)
    assert not notify.is_fresh("2026-10-20", TODAY), "backfilled mail alerted"
    assert notify.is_fresh(None, TODAY), "a phone photo has no received date"


def test_one_alert_per_email_however_many_parts(conn):
    email, notice, note = (artifact(conn, n) for n in (7, 8, 9))
    kw = dict(parent_id=email, received_date="2026-11-04", urgency="CRITICAL",
              category="VENDORS", counterparty="biberk", descriptor="lapse notice",
              due_date="2026-11-10", amount_cents=None, today=TODAY)
    assert notify.for_document(conn, artifact_id=notice, **kw)
    assert not notify.for_document(conn, artifact_id=note, **kw)
    assert len(notify.pending(conn)) == 1


def test_old_mail_is_not_queued(conn):
    assert not notify.for_document(
        conn, artifact_id=1, parent_id=None, received_date="2026-10-01",
        urgency="CRITICAL", category="VENDORS", counterparty="", descriptor="",
        due_date=None, amount_cents=None, today=TODAY)
    assert notify.pending(conn) == []


def test_a_health_document_is_never_named(conn):
    notify.for_document(
        conn, artifact_id=artifact(conn, 1), parent_id=None, received_date=None,
        urgency="CRITICAL", category="HEALTH", counterparty="dr-patel",
        descriptor="biopsy results", due_date=None, amount_cents=None, today=TODAY)
    text = notify.pending(conn)[0].text
    assert "patel" not in text.lower() and "biopsy" not in text.lower()


def test_lines_are_redacted_and_links_defanged(conn):
    notify.enqueue(conn, key="k", reason="PHISHING",
                   text="sender evil-pay.com asks to confirm SSN 123-45-6789 "
                        "at https://evil-pay.com/x")
    text = notify.pending(conn)[0].text
    assert "123-45-6789" not in text
    assert "evil-pay.com" not in text and "https://" not in text
    assert "evil-pay[.]com" in text


# ---------------------------------------------------------------------------
# How often (§8.8)
# ---------------------------------------------------------------------------

def test_sends_in_the_day_when_nothing_has_gone_out():
    assert notify.why_not([item()], [], at(10)) is None


def test_quiet_hours_hold_everything_but_critical():
    assert "quiet" in notify.why_not([item()], [], at(23))
    assert "quiet" in notify.why_not([item()], [], at(6, 59))
    assert notify.why_not([item(), item(2, critical=True)], [], at(3)) is None


def test_at_most_one_an_hour():
    assert "hour" in notify.why_not([item()], [at(10)], at(10, 59))
    assert notify.why_not([item()], [at(10)], at(11)) is None


def test_at_most_three_a_day_and_the_count_resets_at_midnight():
    sent = [at(8), at(10), at(12)]
    assert "3 pushes" in notify.why_not([item()], sent, at(15))
    assert notify.why_not([item(critical=True)], sent, at(15)) is None
    assert notify.why_not([item()], sent, at(8, day=5)) is None


# ---------------------------------------------------------------------------
# The job
# ---------------------------------------------------------------------------

def test_the_job_sends_once_and_marks_it_sent(conn):
    notify.enqueue(conn, key="a", reason="CRITICAL", text="Lapse notice")
    notify.enqueue(conn, key="b", reason="LEGAL", text="Summons")
    sent = []
    assert notify_job.run(conn, sent.append, at(10)) == 0
    assert len(sent) == 1, "two alerts are one push"
    assert "URGENT: Lapse notice" in sent[0] and "Legal/tax: Summons" in sent[0]
    assert notify.pending(conn) == []
    assert notify_job.run(conn, sent.append, at(10, 20)) == 0
    assert len(sent) == 1


def test_a_failed_send_is_logged_and_kept_for_the_next_run(conn):
    notify.enqueue(conn, key="a", reason="LEGAL", text="Summons")

    def down(text):
        raise RuntimeError("sendMessage: HTTP 502 Bad Gateway")
    assert notify_job.run(conn, down, at(10)) == 1
    assert len(notify.pending(conn)) == 1
    from core.reports import brief
    assert any(s.startswith("alerts: FAILED")
               for s in brief.stalled_jobs(conn, at(10, 1)))
    sent = []
    assert notify_job.run(conn, sent.append, at(10, 15)) == 0
    assert len(sent) == 1, "a failed send must not use up the hour"


def test_a_stalled_job_is_pushed_once_a_day(conn):
    conn.execute("INSERT INTO actions_log (ts, action) VALUES (?, ?)",
                 (at(7, day=1).isoformat(), "MAIL_POLL_STOP"))
    sent = []
    notify_job.run(conn, sent.append, at(10))
    notify_job.run(conn, sent.append, at(12))
    assert len(sent) == 1 and "System: mail: last ran" in sent[0]


def test_dry_run_sends_and_marks_nothing(conn):
    notify.enqueue(conn, key="a", reason="LEGAL", text="Summons")
    notify_job.run(conn, None, at(10), dry_run=True)
    assert len(notify.pending(conn)) == 1


# ---------------------------------------------------------------------------
# Ingest queues it
# ---------------------------------------------------------------------------

class FakeModel:
    def __init__(self, response):
        self._response = response

    def complete(self, **kw):
        return Completion(text=json.dumps(self._response), model="fake",
                          elapsed_s=0.01, finish_reason="stop")


LAPSE = {
    "domain": "BUSINESS", "entity_id": "B_MRE", "category": "VENDORS",
    "subcategory": "invoices-received", "urgency": "CRITICAL", "confidence": 0.93,
    "requires_reply": False, "due_date": "2026-11-10",
    "counterparty": "biberk", "descriptor": "policy lapse notice",
    "amount_cents": None, "rationale": "lapse",
}


@pytest.fixture
def pipeline(tmp_path):
    roots = filing.StorageRoots(archive=tmp_path / "archive",
                                originals=tmp_path / "_originals",
                                quarantine=tmp_path / "quarantine")
    roots.ensure()
    registry = load_registry(CONFIG)
    return roots, registry, Classifier(registry, client=FakeModel(LAPSE))


def test_ingest_queues_a_fresh_critical_document(conn, pipeline, tmp_path):
    roots, registry, clf = pipeline
    src = tmp_path / "lapse.pdf"
    src.write_bytes(b"BiBerk policy will lapse")
    today = datetime.now(ET).date().isoformat()
    r = ingest_file(conn, roots, registry, clf, src, source="email",
                    artifact=Artifact(recipient="matthew@mrecai.com",
                                      body="policy will lapse",
                                      received_date=today), run_ocr=False)
    assert r.status == "FILED"
    [p] = notify.pending(conn)
    assert p.critical and p.text.startswith("URGENT: Policy lapse notice, Biberk")


def test_ingest_does_not_queue_backfilled_mail(conn, pipeline, tmp_path):
    roots, registry, clf = pipeline
    src = tmp_path / "lapse.pdf"
    src.write_bytes(b"BiBerk policy will lapse")
    ingest_file(conn, roots, registry, clf, src, source="email",
                artifact=Artifact(recipient="matthew@mrecai.com",
                                  body="policy will lapse",
                                  received_date="2026-01-05"), run_ocr=False)
    assert notify.pending(conn) == []


def test_ingest_queues_blocked_phishing(conn, pipeline, tmp_path):
    roots, registry, clf = pipeline
    src = tmp_path / "urgent.pdf"
    src.write_bytes(b"x")
    ingest_file(conn, roots, registry, clf, src, source="email",
                artifact=Artifact(
                    sender="accounts@acme-supply.com",
                    recipient="matthew@mrecai.com",
                    subject="URGENT: updated bank details",
                    body="Please note our updated bank details. Use these new "
                         "wiring instructions for the outstanding invoice."),
                run_ocr=False)
    [p] = notify.pending(conn)
    assert p.reason == "PHISHING" and not p.critical
    assert "do not act on it" in p.text


def test_every_action_the_brief_reads_is_one_this_job_writes():
    """The D-027 trap: an alarm armed by a string nothing writes never fires."""
    from core.reports import brief
    assert brief.JOB_FAILURES[notify_job.FAILED] == notify_job.CHECKED
    assert notify_job.CHECKED in brief.WATCHED_JOBS
    assert (timedelta(hours=brief.WATCHED_JOBS[notify_job.CHECKED][1])
            > 2 * notify_job.HEARTBEAT), "one late heartbeat would cry wolf"


def test_a_given_key_makes_two_documents_one_alert(conn):
    a, b = artifact(conn, 31), artifact(conn, 32)
    kw = dict(parent_id=None, received_date="2026-11-04", urgency="CRITICAL",
              category="VENDORS", counterparty="acme", descriptor="final notice",
              due_date=None, amount_cents=None, today=TODAY,
              key="whatsapp:15550100@s.whatsapp.net:2026-11-04")
    assert notify.for_document(conn, artifact_id=a, **kw)
    assert not notify.for_document(conn, artifact_id=b, **kw)


def test_ingest_passes_its_alert_key_through(conn, pipeline, tmp_path):
    roots, registry, clf = pipeline
    today = datetime.now(ET).date().isoformat()
    notify.enqueue(conn, key="whatsapp:x:today", reason="CRITICAL", text="from the hourly check")
    src = tmp_path / "lapse.pdf"
    src.write_bytes(b"BiBerk policy will lapse again")
    ingest_file(conn, roots, registry, clf, src, source="whatsapp",
                artifact=Artifact(recipient="matthew@mrecai.com",
                                  body="policy will lapse", received_date=today),
                run_ocr=False, alert_key="whatsapp:x:today")
    assert len(notify.pending(conn)) == 1


def test_the_whatsapp_table_exists(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(whatsapp_messages)")}
    assert cols == {"stanza_id", "chat_jid", "day", "scanned_at",
                    "artifact_id", "voice_text"}
