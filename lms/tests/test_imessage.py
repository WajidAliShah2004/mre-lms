"""iMessage chat days: the pass, the SMS line, the brief (R2, D-081)."""

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from core.adapters import asr
from core.adapters import imessage as im
from core.adapters.lmstudio import Completion
from core.db import database as db
from core.pipeline import filing
from core.pipeline import imessage as i
from core.pipeline.classify import Classifier
from core.pipeline.registry import load_registry
from core.reports import brief
from tests.im_fixture import IM, contacts

ET = ZoneInfo("America/New_York")
CONFIG = Path(__file__).resolve().parents[1] / "config"
NOW = datetime(2026, 11, 7, 12, tzinfo=ET)


class FakeModel:
    def complete(self, **kw):
        return Completion(text=json.dumps({
            "domain": "BUSINESS", "entity_id": "B_MRE", "category": "VENDORS",
            "subcategory": "invoices-received", "urgency": "NORMAL",
            "confidence": 0.93, "requires_reply": False, "due_date": None,
            "counterparty": "acme-supply", "descriptor": "invoice chat",
            "amount_cents": None, "rationale": "vendor"}),
            model="fake", elapsed_s=0.01, finish_reason="stop")


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "lms.db")
    yield c
    c.close()


@pytest.fixture
def pipe(tmp_path):
    roots = filing.StorageRoots(archive=tmp_path / "archive",
                                originals=tmp_path / "_originals",
                                quarantine=tmp_path / "quarantine")
    roots.ensure()
    registry = load_registry(CONFIG)
    return roots, registry, Classifier(registry, client=FakeModel())


@pytest.fixture(autouse=True)
def no_contacts(tmp_path, monkeypatch):
    monkeypatch.setattr(im, "CONTACTS_ROOT", tmp_path / "no-contacts")


def no_speech(path, tmp):
    raise asr.ASRUnavailable("not in tests")


class Seeing:
    def __init__(self, real):
        self.real, self.seen = real, []

    def classify(self, art):
        self.seen.append(art)
        return self.real.classify(art)


def build(tmp_path, when, service="iMessage"):
    box = IM(tmp_path / "Messages")
    h = box.handle("+15550100100", service=service)
    c = box.chat("+15550100100", service=service)
    box.message(c, when, "Can you resend the invoice?", from_me=True, service=service)
    box.message(c, when + timedelta(minutes=5), "Sent it", handle=h, service=service)
    box.close()
    return box.root


def checks(conn):
    return dict(conn.execute("SELECT source_id, result FROM source_checks").fetchall())


def test_a_whole_pass_files_the_day_under_imessage(conn, pipe, tmp_path, monkeypatch):
    roots, registry, real = pipe
    monkeypatch.setattr(im, "CONTACTS_ROOT", contacts(
        tmp_path / "ab", {"first": "Aunt", "last": "Rhonda", "phones": ["555-010-0100"]}))
    now = datetime.now(ET)
    src = build(tmp_path, (now - timedelta(days=1)).replace(hour=9, minute=0))
    clf = Seeing(real)
    r = i.run_pass(conn, roots, registry, clf, src_dir=src, tmp_root=tmp_path / "tmp",
                   now=now.replace(hour=1, minute=0), transcriber=no_speech)
    assert r.error is None and r.filed == 1 and r.result == "OK"
    filed = clf.seen[-1]
    assert filed.body.splitlines()[0] == "iMessage chat: Aunt Rhonda (direct, +15550100100)"
    assert filed.recipient == "Matthew (iMessage)"
    assert "Aunt Rhonda: Sent it" in filed.body
    [ref] = [r[0] for r in conn.execute(
        "SELECT source_ref FROM artifacts WHERE source='imessage'")]
    assert ref.startswith("imessage:+15550100100:") and ref.endswith(":1")
    assert checks(conn) == {"messages:imessage": "OK", "messages:sms": "PARTIAL"}
    assert conn.execute("SELECT COUNT(*) FROM imessage_messages").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM whatsapp_messages").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM actions_log "
                        "WHERE action='IMESSAGE_CHECKED'").fetchone()[0] == 1
    assert list((tmp_path / "tmp").iterdir()) == [], "the copy was left behind"


def test_messages_signed_out_is_partial_with_the_sign_in_hint(conn, pipe, tmp_path):
    roots, registry, clf = pipe
    src = build(tmp_path, datetime(2026, 10, 5, 9, tzinfo=ET))
    r = i.run_pass(conn, roots, registry, clf, src_dir=src, tmp_root=tmp_path / "tmp",
                   now=NOW, transcriber=no_speech)
    assert r.result == "PARTIAL"
    assert ("Messages on the Mac has not synced since 2026-10-05 09:05 "
            "(is it signed in?)") in r.summary()
    assert checks(conn) == {"messages:imessage": "PARTIAL", "messages:sms": "OK"}


def test_a_changed_schema_fails_both_lines_loudly(conn, pipe, tmp_path):
    roots, registry, clf = pipe
    src = build(tmp_path, datetime(2026, 11, 4, 9, tzinfo=ET))
    raw = sqlite3.connect(src / "chat.db")
    raw.execute("ALTER TABLE message RENAME COLUMN attributedBody TO body")
    raw.commit()
    raw.close()
    r = i.run_pass(conn, roots, registry, clf, src_dir=src, tmp_root=tmp_path / "tmp",
                   now=datetime(2026, 11, 5, 1, tzinfo=ET), transcriber=no_speech)
    assert r.result == "FAILED" and "message.attributedBody" in r.error
    assert conn.execute("SELECT detail FROM actions_log WHERE action='IMESSAGE_FAILED'"
                        ).fetchone()[0].startswith("IMessageError")
    assert checks(conn) == {"messages:imessage": "FAILED", "messages:sms": "FAILED"}


def test_no_chat_db_fails_the_pass(conn, pipe, tmp_path):
    roots, registry, clf = pipe
    r = i.run_pass(conn, roots, registry, clf, src_dir=tmp_path / "empty",
                   tmp_root=tmp_path / "tmp", now=NOW, transcriber=no_speech)
    assert r.result == "FAILED" and "no chat.db" in r.error


# --- the SMS line ----------------------------------------------------------------

def result(**by):
    r = i.PassResult()
    r.by_service = by
    return r


def test_the_sms_line_is_ok_while_texts_arrive():
    assert i.sms_line(result(iMessage=NOW, SMS=NOW - timedelta(days=6)), NOW)[0] == "OK"
    assert i.sms_line(result(iMessage=NOW, RCS=NOW), NOW)[0] == "OK"


def test_the_sms_line_is_partial_when_only_imessage_arrives():
    state, detail = i.sms_line(
        result(iMessage=NOW - timedelta(hours=2), SMS=datetime(2026, 10, 5, 9, tzinfo=ET)),
        NOW)
    assert state == "PARTIAL"
    assert detail == ("no SMS or RCS since 2026-10-05 while iMessage arrives: "
                      "is Text Message Forwarding on for this Mac?")
    state, detail = i.sms_line(result(iMessage=NOW), NOW)
    assert state == "PARTIAL" and detail.startswith("no SMS or RCS in this Mac's Messages")


def test_the_sms_line_is_ok_when_the_whole_database_is_stale():
    r = result(iMessage=NOW - timedelta(days=30))
    r.stale_since = NOW - timedelta(days=30)
    assert i.sms_line(r, NOW)[0] == "OK"


def test_the_sms_line_fails_with_the_pass():
    assert i.sms_line(i.PassResult(error="IMessageError: x"), NOW) == \
        ("FAILED", "IMessageError: x")


# --- the brief -------------------------------------------------------------------

def test_every_action_the_brief_reads_is_one_this_job_writes():
    """The D-027 trap, as in test_whatsapp.py."""
    assert brief.JOB_FAILURES[i.FAILED] == i.CHECKED
    assert brief.WATCHED_JOBS[i.CHECKED] == ("imessage", 3)
    assert (i.CHAT_SOURCE.checked, i.CHAT_SOURCE.failed) == (i.CHECKED, i.FAILED)
