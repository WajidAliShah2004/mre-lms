"""WhatsApp chat days: formatting, the hourly check, closing, the pass (D-079)."""

import json
from datetime import datetime, timedelta, timezone
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


from core.adapters.lmstudio import Completion
from core.pipeline import filing
from core.pipeline.classify import Classifier
from core.pipeline.registry import load_registry

CONFIG = Path(__file__).resolve().parents[1] / "config"


class FakeModel:
    def __init__(self, response):
        self._response = response

    def complete(self, **kw):
        return Completion(text=json.dumps(self._response), model="fake",
                          elapsed_s=0.01, finish_reason="stop")


def answer(urgency="NORMAL"):
    return {"domain": "BUSINESS", "entity_id": "B_MRE", "category": "VENDORS",
            "subcategory": "invoices-received", "urgency": urgency,
            "confidence": 0.93, "requires_reply": False, "due_date": None,
            "counterparty": "acme-supply", "descriptor": "invoice chat",
            "amount_cents": None, "rationale": "vendor"}


@pytest.fixture
def pipe(tmp_path):
    roots = filing.StorageRoots(archive=tmp_path / "archive",
                                originals=tmp_path / "_originals",
                                quarantine=tmp_path / "quarantine")
    roots.ensure()
    registry = load_registry(CONFIG)
    return roots, registry, Classifier(registry, client=FakeModel(answer()))


def filed_ids(conn, *stanzas):
    return [conn.execute("SELECT artifact_id FROM whatsapp_messages WHERE stanza_id=?",
                         (s,)).fetchone()[0] for s in stanzas]


def test_a_closed_day_is_filed_as_one_transcript(conn, pipe, tmp_path):
    roots, registry, clf = pipe
    chat = direct(msg("A", at(4, 9), "Can you resend the invoice?", from_me=True),
                  msg("B", at(4, 9, 20), "Sent it"))
    w.record(conn, [chat])
    result = w.PassResult()
    assert w.close(conn, roots, registry, clf, [chat], at(5, 0, 29), tmp_path,
                   no_speech, result) == 0
    assert w.close(conn, roots, registry, clf, [chat], at(5, 0, 30), tmp_path,
                   no_speech, result) == 1
    a, b = filed_ids(conn, "A", "B")
    assert a == b and a is not None
    row = conn.execute("SELECT source, source_ref FROM artifacts WHERE id=?", (a,)).fetchone()
    assert row["source"] == "whatsapp"
    assert row["source_ref"] == "whatsapp:15550100@s.whatsapp.net:2026-11-04:1"


def test_a_late_message_is_filed_as_part_2(conn, pipe, tmp_path):
    roots, registry, clf = pipe
    chat = direct(msg("A", at(4, 9), "first"))
    w.record(conn, [chat])
    w.close(conn, roots, registry, clf, [chat], at(5, 1), tmp_path, no_speech, w.PassResult())
    chat.messages.append(msg("L", at(4, 22), "synced late"))
    w.record(conn, [chat])
    assert w.close(conn, roots, registry, clf, [chat], at(5, 2), tmp_path,
                   no_speech, w.PassResult()) == 1
    first, late = filed_ids(conn, "A", "L")
    assert first != late
    ref = conn.execute("SELECT source_ref FROM artifacts WHERE id=?", (late,)).fetchone()[0]
    assert ref.endswith(":2026-11-04:2")


def test_documents_and_photos_are_filed_under_the_transcript(conn, pipe, tmp_path):
    roots, registry, clf = pipe
    media = tmp_path / "container" / "INV.pdf"
    media.parent.mkdir()
    media.write_bytes(b"%PDF-1.4 invoice 2291")
    chat = direct(msg("D", at(4, 9), kind="document",
                      media=Media(media, "INV-2291.pdf", 20, None)),
                  msg("N", at(4, 9, 5), kind="document",
                      media=Media(None, "scan.pdf", None, None)),
                  msg("V", at(4, 9, 6), kind="video", media=Media(media, None, None, 5)))
    w.record(conn, [chat])
    result = w.PassResult()
    w.close(conn, roots, registry, clf, [chat], at(5, 1), tmp_path, no_speech, result,
            ocr=False)
    assert media.read_bytes() == b"%PDF-1.4 invoice 2291", "WhatsApp's file was moved"
    parent = filed_ids(conn, "D")[0]
    kids = conn.execute("SELECT source, source_ref FROM artifacts WHERE parent_id=?",
                        (parent,)).fetchall()
    assert [(k["source"], k["source_ref"]) for k in kids] == [("attachment", "whatsapp:D")]
    assert result.media_missing == 1 and result.result == "PARTIAL"


def test_backfill_limits_by_day_and_count(conn, pipe, tmp_path):
    roots, registry, clf = pipe
    chat = direct(msg("A", at(1, 9), "one"), msg("B", at(2, 9), "two"),
                  msg("C", at(3, 9), "three"), msg("D", at(4, 9), "four"))
    w.record(conn, [chat])
    n = w.close(conn, roots, registry, clf, [chat], at(5, 1), tmp_path, no_speech,
                w.PassResult(), before="2026-11-04", limit=2)
    assert n == 2
    assert [x is not None for x in filed_ids(conn, "A", "B", "C", "D")] == \
        [True, True, False, False]


def test_filing_does_not_repeat_the_hourly_alert(conn, tmp_path):
    roots = filing.StorageRoots(archive=tmp_path / "archive",
                                originals=tmp_path / "_originals",
                                quarantine=tmp_path / "quarantine")
    roots.ensure()
    registry = load_registry(CONFIG)
    clf = Classifier(registry, client=FakeModel(answer("CRITICAL")))
    now = datetime.now(ET)
    yesterday = now - timedelta(days=1)
    chat = direct(msg("A", yesterday.replace(hour=9), "Final notice, pay today"))
    w.record(conn, [chat])
    w.scan(conn, [chat], StubClassifier("CRITICAL"), yesterday.replace(hour=10),
           tmp_path, no_speech, w.PassResult())
    w.close(conn, roots, registry, clf, [chat], now.replace(hour=1), tmp_path,
            no_speech, w.PassResult())
    assert len(notify.pending(conn)) == 1


def test_one_bad_chat_day_does_not_stop_the_others(conn, pipe, tmp_path):
    roots, registry, clf = pipe
    good = direct(msg("G", at(4, 9), "fine"))
    bad = Chat("15550199@s.whatsapp.net", "Broken", False, [msg("X", at(4, 9), "boom")])
    w.record(conn, [good, bad])

    from core.pipeline.ingest import ingest_file

    def flaky(conn_, roots_, registry_, clf_, path, **kw):
        if "Broken" in path.name:
            raise RuntimeError("disk full")
        return ingest_file(conn_, roots_, registry_, clf_, path, **kw)
    result = w.PassResult()
    assert w.close(conn, roots, registry, clf, [bad, good], at(5, 1), tmp_path,
                   no_speech, result, ingest=flaky) == 1
    assert result.failed == 1
    assert filed_ids(conn, "X") == [None]
    assert conn.execute("SELECT COUNT(*) FROM actions_log WHERE action='WHATSAPP_DAY_FAILED'"
                        ).fetchone()[0] == 1


def test_hostile_media_titles_stay_inside_the_temp_dir(conn, pipe, tmp_path):
    roots, registry, clf = pipe
    media = tmp_path / "container" / "INV.pdf"
    media.parent.mkdir()
    media.write_bytes(b"%PDF-1.4 invoice 777")
    tmp = tmp_path / "pass"
    chat = direct(msg("E", at(4, 9), kind="document", media=Media(media, "../../evil.pdf", 20, None)),
                  msg("F", at(4, 9, 1), kind="document", media=Media(media, "../..", 20, None)))
    w.record(conn, [chat])
    result = w.PassResult()
    assert w.close(conn, roots, registry, clf, [chat], at(5, 1), tmp, no_speech, result,
                   ocr=False) == 1
    assert result.failed == 0
    assert not (tmp_path / "evil.pdf").exists() and not (tmp.parent / "evil.pdf").exists()
    assert w._media_name("../../evil.pdf", media) == "evil.pdf"
    assert w._media_name(r"..\..\x.pdf", media) == "x.pdf"
    assert w._media_name("../..", media) == "INV.pdf"
    assert w._media_name("///", media) == "INV.pdf"
    # A caption as title (no extension) keeps the file's own type.
    assert w._media_name("Lease signed today", media) == "Lease signed today.pdf"
    assert filed_ids(conn, "E", "F")[0] is not None


def test_a_failed_attachment_leaves_the_day_for_retry(conn, pipe, tmp_path):
    from core.pipeline.ingest import ingest_file
    roots, registry, clf = pipe
    media = tmp_path / "container" / "a.pdf"
    media.parent.mkdir()
    media.write_bytes(b"%PDF-1.4 one")
    media2 = tmp_path / "container" / "b.pdf"
    media2.write_bytes(b"%PDF-1.4 two")
    chat = direct(msg("P", at(4, 9), kind="document", media=Media(media, "first.pdf", 12, None)),
                  msg("Q", at(4, 9, 1), kind="document", media=Media(media2, "second.pdf", 12, None)))
    w.record(conn, [chat])
    attempted = []

    def flaky(conn_, roots_, registry_, clf_, path, **kw):
        if kw.get("source") == "attachment":
            attempted.append(path.name)
            if path.name == "first.pdf":
                raise RuntimeError("boom")
        return ingest_file(conn_, roots_, registry_, clf_, path, **kw)
    result = w.PassResult()
    assert w.close(conn, roots, registry, clf, [chat], at(5, 1), tmp_path, no_speech,
                   result, ocr=False, ingest=flaky) == 0
    assert result.failed == 1
    assert attempted == ["first.pdf", "second.pdf"]
    assert filed_ids(conn, "P", "Q") == [None, None]
    assert w.close(conn, roots, registry, clf, [chat], at(5, 2), tmp_path, no_speech,
                   w.PassResult(), ocr=False) == 1
    assert None not in filed_ids(conn, "P", "Q")


from core.reports import brief
from tests.wa_fixture import WA


def build_container(tmp_path, when):
    box = WA(tmp_path / "container")
    c = box.chat("15550100@s.whatsapp.net", "Acme Supply")
    box.message(c, when, "Can you resend the invoice?", from_me=True)
    box.message(c, when + timedelta(minutes=5), "Sent it")
    box.close()
    return box.container


def test_a_whole_pass_files_records_and_cleans_up(conn, pipe, tmp_path):
    roots, registry, clf = pipe
    now = datetime.now(ET)
    src = build_container(tmp_path, (now - timedelta(days=1)).replace(hour=9))
    r = w.run_pass(conn, roots, registry, clf, src_dir=src,
                   tmp_root=tmp_path / "tmp", now=now.replace(hour=1, minute=0),
                   transcriber=no_speech)
    assert r.error is None and r.filed == 1 and r.result == "OK"
    [check] = conn.execute("SELECT * FROM source_checks WHERE source_id='chat:whatsapp'")
    assert check["result"] == "OK"
    assert conn.execute("SELECT COUNT(*) FROM actions_log WHERE action='WHATSAPP_CHECKED'"
                        ).fetchone()[0] == 1
    assert list((tmp_path / "tmp").iterdir()) == [], "the copy was left behind"


def test_a_changed_schema_fails_the_pass_loudly(conn, pipe, tmp_path):
    roots, registry, clf = pipe
    src = build_container(tmp_path, at(4, 9))
    import sqlite3
    raw = sqlite3.connect(src / "ChatStorage.sqlite")
    raw.execute("ALTER TABLE ZWAMESSAGE RENAME COLUMN ZTEXT TO ZBODY")
    raw.commit()
    raw.close()
    r = w.run_pass(conn, roots, registry, clf, src_dir=src, tmp_root=tmp_path / "tmp",
                   now=at(5, 1), transcriber=no_speech)
    assert r.result == "FAILED" and "ZWAMESSAGE.ZTEXT" in r.error
    assert conn.execute("SELECT detail FROM actions_log WHERE action='WHATSAPP_FAILED'"
                        ).fetchone()[0].startswith("WhatsAppError")
    assert conn.execute("SELECT result FROM source_checks").fetchone()[0] == "FAILED"


def test_read_chats_skips_his_own_chat(tmp_path, monkeypatch):
    # User, Nov 6: the reader skips the "Message yourself" chat (D-080's
    # /lms commands and replies), so the LMS never reads its own output.
    from core.adapters import whatsapp as wa
    box = WA(tmp_path / "container")
    t = datetime(2026, 11, 4, 14, tzinfo=timezone.utc)
    box.message(box.chat("15550100@s.whatsapp.net", "Acme"), t, "invoice?")
    box.message(box.chat("15559999@s.whatsapp.net", "Matthew"), t, "/lms tasks")
    box.close()
    monkeypatch.setattr(wa, "own_jids",
                        lambda: frozenset({"15559999@s.whatsapp.net"}))
    chats = w.read_chats(box.container, tmp_path / "tmp",
                         datetime(2026, 11, 1, tzinfo=timezone.utc))
    assert [c.jid for c in chats] == ["15550100@s.whatsapp.net"]


def test_plan_days_reads_without_writing(conn, tmp_path):
    chat = direct(msg("A", at(4, 9), "x"), msg("B", at(5, 9), "y"))
    assert w.plan_days(conn, [chat], at(5, 10)) == [
        ("Acme Supply (+15550100)", "2026-11-04", 1, True),
        ("Acme Supply (+15550100)", "2026-11-05", 1, False)]
    assert conn.execute("SELECT COUNT(*) FROM whatsapp_messages").fetchone()[0] == 0


def test_every_action_the_brief_reads_is_one_this_job_writes():
    """The D-027 trap, as in test_notify.py."""
    assert brief.JOB_FAILURES[w.FAILED] == w.CHECKED
    assert brief.WATCHED_JOBS[w.CHECKED] == ("whatsapp", 3)


def test_the_job_stops_when_halted(monkeypatch, capsys):
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))
    import whatsapp as job
    monkeypatch.setattr(job._halt, "read_flag", lambda: {"ts": "now", "by": "telegram"})
    monkeypatch.setattr(job, "ensure_venv", lambda *a, **k: None)
    monkeypatch.setattr(job, "load_lms_env", lambda: {})
    monkeypatch.setattr(_sys, "argv", ["whatsapp.py"])
    assert job.main() == 0
    assert "HALTED" in capsys.readouterr().out


def test_backfill_files_old_days_in_batches_and_leaves_yesterday(conn, pipe, tmp_path):
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))
    import backfill_whatsapp as bf
    roots, registry, clf = pipe
    now = datetime.now(ET)
    box = WA(tmp_path / "container")
    c = box.chat("15550100@s.whatsapp.net", "Acme Supply")
    for back in (5, 4, 3, 1):
        box.message(c, (now - timedelta(days=back)).replace(hour=9), f"day -{back}")
    box.close()
    filed = bf.backfill(conn, roots, registry, clf, src_dir=box.container,
                        tmp_root=tmp_path / "tmp", now=now, days=10, batch=2,
                        transcriber=no_speech)
    assert filed == 3
    days = {r[0]: r[1] for r in conn.execute(
        "SELECT day, artifact_id FROM whatsapp_messages")}
    assert days[w.day_of(now - timedelta(days=1))] is None, "yesterday is the hourly job's"
    assert notify.pending(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM whatsapp_messages WHERE scanned_at IS NULL "
                        "AND artifact_id IS NOT NULL").fetchone()[0] == 0


def test_an_attachment_is_classified_from_its_own_text_not_its_caption(conn, pipe, tmp_path):
    """As mail does (mail.py): the caption stays in the transcript; the file
    is classified from what it says, or a "here you go" routes an invoice."""
    roots, registry, real = pipe
    media = tmp_path / "container" / "notes.txt"
    media.parent.mkdir()
    media.write_text("Invoice 2291 from Acme Supply, due Friday", encoding="utf-8")
    chat = direct(msg("D", at(4, 9), "here you go", kind="document",
                      media=Media(media, "Invoice.txt", 41, None)))
    w.record(conn, [chat])

    class Seeing:
        def __init__(self):
            self.seen = []

        def classify(self, art):
            self.seen.append(art)
            return real.classify(art)
    clf = Seeing()
    w.close(conn, roots, registry, clf, [chat], at(5, 1), tmp_path, no_speech,
            w.PassResult(), ocr=True)
    transcript, attachment = clf.seen
    assert "here you go" in transcript.body
    assert attachment.body == "Invoice 2291 from Acme Supply, due Friday"
    assert attachment.subject == "Invoice.txt"
    assert (attachment.sender, attachment.recipient, attachment.received_date) == \
        ("Acme Supply (+15550100)", w.RECIPIENT, "2026-11-04")


# --- a bad chat day, the model down, a voice note still downloading ----------

def scanned(conn, *stanzas):
    return [conn.execute("SELECT scanned_at FROM whatsapp_messages WHERE stanza_id=?",
                         (s,)).fetchone()[0] is not None for s in stanzas]


def test_a_chat_day_that_fails_its_check_does_not_stop_the_pass(conn, pipe, tmp_path):
    roots, registry, real = pipe
    box = WA(tmp_path / "container")
    good = box.chat("15550100@s.whatsapp.net", "Acme Supply")
    bad = box.chat("15550199@s.whatsapp.net", "Broken")
    box.message(bad, at(4, 9), "boom", stanza="X")
    box.message(good, at(4, 9), "fine", stanza="G")
    box.close()

    class Picky:
        def classify(self, art):
            if art.subject == "Broken":
                raise ValueError("bad chat")
            return real.classify(art)
    r = w.run_pass(conn, roots, registry, Picky(), src_dir=box.container,
                   tmp_root=tmp_path / "tmp", now=at(5, 1), transcriber=no_speech)
    assert r.error is None and r.result == "PARTIAL"
    assert scanned(conn, "G", "X") == [True, False]
    assert filed_ids(conn, "G")[0] is not None and filed_ids(conn, "X") == [None]
    assert r.filed == 1 and r.failed == 2
    assert conn.execute("SELECT COUNT(*) FROM actions_log WHERE action='WHATSAPP_DAY_FAILED' "
                        "AND detail LIKE '15550199%'").fetchone()[0] == 2


class DownModel:
    def complete(self, **kw):
        from core.adapters.lmstudio import ModelError
        raise ModelError("LM Studio is down")


def test_the_model_down_leaves_the_lines_for_the_next_hour(conn, tmp_path):
    clf = Classifier(load_registry(CONFIG), client=DownModel())
    chat = direct(msg("A", at(5, 9), "Final notice: pay today"))
    w.record(conn, [chat])
    result = w.PassResult()
    w.scan(conn, [chat], clf, at(5, 10), tmp_path, no_speech, result)
    assert scanned(conn, "A") == [False]
    assert result.failed == 1 and result.result == "PARTIAL"
    assert conn.execute("SELECT detail FROM actions_log WHERE action='WHATSAPP_DAY_FAILED'"
                        ).fetchone()[0].startswith("15550100@s.whatsapp.net 2026-11-05")


def test_a_voice_note_still_downloading_is_checked_again_next_hour(conn, tmp_path):
    chat = direct(msg("T", at(5, 9), "listen to this"),
                  msg("V", at(5, 9, 1), kind="voice", media=Media(None, None, None, 30.0)))
    w.record(conn, [chat])
    clf = StubClassifier("NORMAL")
    w.scan(conn, [chat], clf, at(5, 10), tmp_path, no_speech, w.PassResult())
    assert scanned(conn, "T", "V") == [True, False]
    w.scan(conn, [chat], clf, at(5, 11), tmp_path, no_speech, w.PassResult())
    assert clf.seen[1].body == "09:01 Acme Supply: [voice 0:30, not transcribed]"


def test_an_unreadable_voice_file_counts_as_not_transcribed(conn, tmp_path):
    gone = tmp_path / "gone.opus"
    chat = direct(msg("V", at(5, 9), kind="voice", media=Media(gone, None, None, 42.0)))
    w.record(conn, [chat])
    result = w.PassResult()
    assert w.voice_text(conn, chat.messages[0], tmp_path, lambda p, t: "x", result) is None
    assert result.voice_missing == 1 and result.failed == 0


def test_hostile_message_ids_stay_inside_the_temp_dir(conn, pipe, tmp_path):
    """A modified client chooses its own message ids: never a path segment."""
    from core.pipeline.ingest import ingest_file
    roots, registry, clf = pipe
    src = tmp_path / "container"
    src.mkdir()
    (src / "INV.pdf").write_bytes(b"%PDF-1.4 invoice 31")
    (src / "v.opus").write_bytes(b"opus")
    tmp = tmp_path / "a" / "b" / "c" / "pass"
    chat = direct(msg("../../../../x", at(4, 9), kind="document",
                      media=Media(src / "INV.pdf", "INV.pdf", 20, None)),
                  msg("../../../../v", at(4, 9, 1), kind="voice",
                      media=Media(src / "v.opus", None, None, 5.0)))
    w.record(conn, [chat])
    handed = []

    def speech(path, tmp_):
        handed.append(path)
        return "hello"

    def watching(conn_, roots_, registry_, clf_, path, **kw):
        handed.append(path)
        return ingest_file(conn_, roots_, registry_, clf_, path, **kw)
    result = w.PassResult()
    assert w.close(conn, roots, registry, clf, [chat], at(5, 1), tmp, speech, result,
                   ocr=False, ingest=watching) == 1
    assert result.failed == 0
    assert all(p.resolve().is_relative_to(tmp.resolve()) for p in handed), handed
    assert not (tmp_path / "a" / "x").exists() and not (tmp.parent / "x").exists()
    assert len(handed) == 3
    for hostile in ("/abs/../../x", "../../x", "..", ""):
        slot = w._slot(hostile)
        assert len(slot) == 16 and slot.isalnum()


# --- a stale sync, the DST change, the backfill halted -----------------------

def test_a_sync_older_than_two_days_is_partial_not_a_quiet_hour(conn, pipe, tmp_path):
    roots, registry, clf = pipe
    box = WA(tmp_path / "container")
    c = box.chat("15550100@s.whatsapp.net", "Acme Supply")
    box.message(c, at(1, 9), "the last thing synced")
    box.close()
    r = w.run_pass(conn, roots, registry, clf, src_dir=box.container,
                   tmp_root=tmp_path / "tmp", now=at(5, 1), transcriber=no_speech)
    assert r.error is None and r.messages == 0
    assert r.result == "PARTIAL"
    assert "WhatsApp on the Mac has not synced since 2026-11-01 09:00" in r.summary()
    assert conn.execute("SELECT result FROM source_checks").fetchone()[0] == "PARTIAL"


def test_a_sync_within_two_days_is_ok(conn, pipe, tmp_path):
    roots, registry, clf = pipe
    box = WA(tmp_path / "container")
    c = box.chat("15550100@s.whatsapp.net", "Acme Supply")
    box.message(c, at(3, 2), "47 hours ago")
    box.close()
    r = w.run_pass(conn, roots, registry, clf, src_dir=box.container,
                   tmp_root=tmp_path / "tmp", now=at(5, 1), transcriber=no_speech)
    assert r.result == "OK" and "not synced" not in r.summary()


def test_yesterday_is_the_eastern_calendar_day_across_the_dst_change(conn, tmp_path):
    # ops passes a fixed-offset now; 24 hours before 00:15 EDT on Mar 9 is
    # 23:15 EST on Mar 7, but yesterday is Mar 8.
    now = datetime(2026, 3, 9, 0, 15, tzinfo=timezone(timedelta(hours=-4)))
    assert w.yesterday(now) == "2026-03-08"
    chat = direct(msg("A", datetime(2026, 3, 8, 20, tzinfo=ET), "pay today"))
    w.record(conn, [chat])
    clf = StubClassifier()
    w.scan(conn, [chat], clf, now, tmp_path, no_speech, w.PassResult())
    assert len(clf.seen) == 1


def test_the_backfill_stops_between_batches_when_halted(conn, pipe, tmp_path, monkeypatch):
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))
    import backfill_whatsapp as bf
    roots, registry, clf = pipe
    box = WA(tmp_path / "container")
    c = box.chat("15550100@s.whatsapp.net", "Acme Supply")
    for d in (1, 2, 3):
        box.message(c, at(d, 9), f"day {d}")
    box.close()
    monkeypatch.setattr(bf._halt, "read_flag", lambda: {"ts": "now", "by": "telegram"})
    assert bf.backfill(conn, roots, registry, clf, src_dir=box.container,
                       tmp_root=tmp_path / "tmp", now=at(5, 1), days=10, batch=1,
                       transcriber=no_speech) == 1
    assert list((tmp_path / "tmp").iterdir()) == []
