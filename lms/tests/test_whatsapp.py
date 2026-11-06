"""WhatsApp chat days: formatting, the hourly check, closing, the pass (D-079)."""

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from core.adapters import asr
from core.adapters.whatsapp import Chat, Media, Message
from core.db import database as db
from core.pipeline import notify
from core.pipeline import whatsapp as w

ET = ZoneInfo("America/New_York")
UTC = timezone.utc


def at(day, hour, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=ET)


def msg(stanza, when, text=None, *, kind="text", sender="Acme Supply",
        from_me=False, media=None):
    return Message(stanza, when, from_me, "Matthew" if from_me else sender,
                   text, kind, media)


def direct(*messages):
    return Chat("15550100@s.whatsapp.net", "Acme Supply", False, list(messages))


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "lms.db")
    yield c
    c.close()


class StubClassifier:
    def __init__(self, urgency="NORMAL", category="VENDORS"):
        self.urgency, self.category, self.seen = urgency, category, []

    def classify(self, art):
        self.seen.append(art)
        return SimpleNamespace(urgency=self.urgency, category=self.category,
                               counterparty="acme-supply", descriptor="final notice",
                               due_date=None, amount_cents=None)


def no_speech(path, tmp):
    raise asr.ASRUnavailable("not in tests")


# --- formatting ------------------------------------------------------------

def test_days_are_eastern_including_across_the_dst_change():
    assert w.day_of(datetime(2026, 11, 2, 4, 30, tzinfo=UTC)) == "2026-11-01"
    assert w.day_of(datetime(2026, 10, 31, 3, 30, tzinfo=UTC)) == "2026-10-30"


def test_a_day_closes_at_half_past_midnight():
    assert not w.closed("2026-11-04", at(5, 0, 29))
    assert w.closed("2026-11-04", at(5, 0, 30))
    assert not w.closed("2026-11-05", at(5, 23, 59))


def test_each_kind_has_its_line(tmp_path):
    f = tmp_path / "x.pdf"
    f.write_bytes(b"%PDF")
    t = at(5, 9, 14)
    doc = Media(f, "INV-2291.pdf", 4, None)
    lost = Media(None, "scan.pdf", None, None)
    v = Media(f, None, None, 42.0)
    long = Media(f, None, None, 730.0)
    assert w.line(msg("1", t, "Resend?", from_me=True), None) == "09:14 Matthew: Resend?"
    assert w.line(msg("2", t, kind="document", media=doc), None) == \
        "09:14 Acme Supply: [document: INV-2291.pdf]"
    assert w.line(msg("3", t, kind="document", media=lost), None) == \
        "09:14 Acme Supply: [document: scan.pdf, not downloaded on the Mac]"
    assert w.line(msg("4", t, kind="voice", media=v), "Hi Matthew") == \
        "09:14 Acme Supply: [voice 0:42] Hi Matthew"
    assert w.line(msg("5", t, kind="voice", media=v), None) == \
        "09:14 Acme Supply: [voice 0:42, not transcribed]"
    assert w.line(msg("6", t, kind="voice", media=long), None) == \
        "09:14 Acme Supply: [voice 12:10, not transcribed: over 10 minutes]"
    assert w.line(msg("7", t, kind="image", media=doc, text="the bill"), None) == \
        "09:14 Acme Supply: [photo] the bill"
    assert w.line(msg("8", t, kind="video", media=doc), None) == \
        "09:14 Acme Supply: [video, not filed]"
    assert w.line(msg("9", t, kind="sticker"), None) == "09:14 Acme Supply: [sticker]"


def test_the_header_says_whose_chat_and_which_part():
    assert w.header(direct(), "2026-11-05", 2).splitlines() == [
        "WhatsApp chat: Acme Supply (direct, +15550100)",
        "Date: 2026-11-05 (America/New_York)",
        "Part: 2", ""]
    group = Chat("1203630@g.us", "Board", True, [])
    assert w.header(group, "2026-11-05", 1).splitlines()[0] == \
        "WhatsApp chat: Board (group)"


# --- the hourly check --------------------------------------------------------

def test_an_urgent_chat_day_alerts_once(conn, tmp_path):
    chat = direct(msg("A", at(5, 9), "Final notice: pay today"))
    w.record(conn, [chat])
    clf, result = StubClassifier("CRITICAL"), w.PassResult()
    w.scan(conn, [chat], clf, at(5, 10), tmp_path, no_speech, result)
    chat.messages.append(msg("B", at(5, 10, 30), "Still waiting"))
    w.record(conn, [chat])
    w.scan(conn, [chat], clf, at(5, 11), tmp_path, no_speech, result)
    [p] = notify.pending(conn)
    assert p.critical and "Final notice" in p.text
    assert result.alerts == 1
    assert [a.body for a in clf.seen] == ["09:00 Acme Supply: Final notice: pay today",
                                          "10:30 Acme Supply: Still waiting"]


def test_a_normal_chat_day_does_not_alert(conn, tmp_path):
    chat = direct(msg("A", at(5, 9), "Thanks!"))
    w.record(conn, [chat])
    w.scan(conn, [chat], StubClassifier("NORMAL"), at(5, 10), tmp_path, no_speech,
           w.PassResult())
    assert notify.pending(conn) == []


def test_old_days_are_not_scanned(conn, tmp_path):
    chat = direct(msg("A", at(2, 9), "pay now"))
    w.record(conn, [chat])
    clf = StubClassifier("CRITICAL")
    w.scan(conn, [chat], clf, at(5, 10), tmp_path, no_speech, w.PassResult())
    assert clf.seen == []


def test_a_voice_note_is_transcribed_once_and_cached(conn, tmp_path):
    f = tmp_path / "a.opus"
    f.write_bytes(b"opus")
    chat = direct(msg("V", at(5, 9), kind="voice", media=Media(f, None, None, 42.0)))
    w.record(conn, [chat])
    calls = []

    def speech(path, tmp):
        calls.append(path)
        return "the invoice is due Friday"
    result = w.PassResult()
    assert w.voice_text(conn, chat.messages[0], tmp_path, speech, result) == \
        "the invoice is due Friday"
    assert w.voice_text(conn, chat.messages[0], tmp_path, speech, result) == \
        "the invoice is due Friday"
    assert len(calls) == 1 and result.voice_missing == 0


def test_no_speech_model_counts_as_missing_not_failure(conn, tmp_path):
    f = tmp_path / "a.opus"
    f.write_bytes(b"opus")
    chat = direct(msg("V", at(5, 9), kind="voice", media=Media(f, None, None, 42.0)))
    w.record(conn, [chat])
    result = w.PassResult()
    assert w.voice_text(conn, chat.messages[0], tmp_path, no_speech, result) is None
    assert result.voice_missing == 1 and result.result == "PARTIAL"
