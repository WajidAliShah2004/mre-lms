"""The chat-day engine with two sources side by side (D-081)."""

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from core.adapters import asr
from core.adapters.chat import Chat, Media, Message
from core.adapters.lmstudio import Completion
from core.db import database as db
from core.pipeline import chatdays as cd
from core.pipeline import filing, notify
from core.pipeline import whatsapp as w
from core.pipeline.classify import Classifier
from core.pipeline.registry import load_registry

ET = ZoneInfo("America/New_York")
CONFIG = Path(__file__).resolve().parents[1] / "config"


def at(day, hour, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=ET)


def texts_source(**kw):
    """A second source, shaped like iMessage, built only from the engine."""
    base = dict(name="imessage", label="iMessage", source_id="messages:imessage",
                table="imessage_messages", action="IMESSAGE",
                recipient="Matthew (iMessage)",
                read=lambda src_dir, tmp, since: cd.ReadResult([], None),
                ident=str,
                not_synced="Messages on the Mac has not synced since {since} "
                           "(is it signed in?)")
    base.update(kw)
    return cd.ChatSource(**base)


def msg(stanza, when, text=None, *, kind="text", media=None, sender="Rhonda"):
    return Message(stanza, when, False, sender, text, kind, media)


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "lms.db")
    yield c
    c.close()


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
def pipe(tmp_path):
    roots = filing.StorageRoots(archive=tmp_path / "archive",
                                originals=tmp_path / "_originals",
                                quarantine=tmp_path / "quarantine")
    roots.ensure()
    registry = load_registry(CONFIG)
    return roots, registry, Classifier(registry, client=FakeModel())


class Critical:
    def classify(self, art):
        return SimpleNamespace(urgency="CRITICAL", category="VENDORS",
                               counterparty="acme-supply", descriptor="final notice",
                               due_date=None, amount_cents=None)


def no_speech(path, tmp):
    raise asr.ASRUnavailable("not in tests")


def test_a_table_that_is_not_a_chat_table_is_refused():
    with pytest.raises(ValueError, match="artifacts"):
        texts_source(table="artifacts")


def test_the_action_names_are_the_ones_the_brief_reads():
    src = texts_source()
    assert (src.checked, src.failed, src.day_failed) == \
        ("IMESSAGE_CHECKED", "IMESSAGE_FAILED", "IMESSAGE_DAY_FAILED")
    assert w.CHAT_SOURCE.checked == w.CHECKED == "WHATSAPP_CHECKED"
    assert w.CHAT_SOURCE.failed == w.FAILED == "WHATSAPP_FAILED"


def test_the_header_uses_the_sources_label_and_ident():
    src = texts_source()
    chat = Chat("+15550100100", "Aunt Rhonda", False, [])
    assert cd.header(src, chat, "2026-11-05", 1).splitlines()[0] == \
        "iMessage chat: Aunt Rhonda (direct, +15550100100)"
    assert cd.alert_key(src, chat, "2026-11-05") == "imessage:+15550100100:2026-11-05"


def test_a_voice_note_with_no_known_length_says_voice_only():
    v = Media(None, None, None, None)
    m = msg("V", at(5, 9), kind="voice", media=v)
    assert cd.line(m, None) == "09:00 Rhonda: [voice, not transcribed]"
    assert cd.line(m, "call me back") == "09:00 Rhonda: [voice] call me back"
    zero = msg("Z", at(5, 9), kind="voice", media=Media(None, None, None, 0.0))
    assert cd.line(zero, None) == "09:00 Rhonda: [voice 0:00, not transcribed]"


def test_the_summary_uses_the_sources_words():
    r = cd.PassResult(texts_source(), failed=1, stale_since=at(1, 9))
    s = r.summary()
    assert "see IMESSAGE_DAY_FAILED" in s
    assert "Messages on the Mac has not synced since 2026-11-01 09:00 " \
           "(is it signed in?)" in s


def test_a_problem_makes_the_pass_partial_and_ends_the_summary():
    r = cd.PassResult(texts_source(), problems=["x"])
    assert r.result == "PARTIAL"
    assert r.summary().endswith(", x")
    assert cd.PassResult(texts_source()).result == "OK"


def test_two_sources_with_the_same_ids_do_not_collide(conn, pipe, tmp_path):
    roots, registry, clf = pipe
    im = texts_source()
    wa_chat = Chat("+15550100100", "Rhonda", False, [msg("A", at(4, 9), "whatsapp says")])
    im_chat = Chat("+15550100100", "Rhonda", False, [msg("A", at(4, 9), "imessage says")])
    w.record(conn, [wa_chat])
    cd.record(im, conn, [im_chat])
    cd.scan(im, conn, [im_chat], Critical(), at(4, 10), tmp_path, no_speech,
            cd.PassResult(im))
    w.scan(conn, [wa_chat], Critical(), at(4, 10), tmp_path, no_speech, w.PassResult())
    assert w.close(conn, roots, registry, clf, [wa_chat], at(5, 1), tmp_path,
                   no_speech, w.PassResult()) == 1
    assert cd.close(im, conn, roots, registry, clf, [im_chat], at(5, 1), tmp_path,
                    no_speech, cd.PassResult(im)) == 1
    refs = sorted(r[0] for r in conn.execute(
        "SELECT source || ' ' || source_ref FROM artifacts WHERE parent_id IS NULL"))
    assert refs == ["imessage imessage:+15550100100:2026-11-04:1",
                    "whatsapp whatsapp:+15550100100:2026-11-04:1"]
    keys = sorted(r[0] for r in conn.execute("SELECT key FROM notifications"))
    assert keys == ["imessage:+15550100100:2026-11-04",
                    "whatsapp:+15550100100:2026-11-04"]
    for table in ("whatsapp_messages", "imessage_messages"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table} "
                            "WHERE artifact_id IS NOT NULL").fetchone()[0] == 1


def test_a_pass_records_its_own_line_and_hands_the_result_on(conn, pipe, tmp_path):
    roots, registry, clf = pipe
    seen = []
    newest = {"iMessage": at(5, 8)}
    src = texts_source(
        read=lambda src_dir, tmp, since: cd.ReadResult([], at(5, 8), newest),
        after_pass=lambda c, r, now, started: seen.append((r.result, r.by_service)))
    r = cd.run_pass(src, conn, roots, registry, clf, src_dir=tmp_path,
                    tmp_root=tmp_path / "tmp", now=at(5, 9), transcriber=no_speech)
    assert r.result == "OK" and seen == [("OK", newest)]
    assert conn.execute("SELECT source_id FROM source_checks").fetchall()[0][0] == \
        "messages:imessage"
    assert conn.execute("SELECT COUNT(*) FROM actions_log "
                        "WHERE action='IMESSAGE_CHECKED'").fetchone()[0] == 1
