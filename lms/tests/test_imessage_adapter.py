"""Read Messages' chat.db, from a copy (R2, D-081)."""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from core.adapters import imessage as im
from tests.im_fixture import IM, PREFIX, attributed, contacts

UTC = timezone.utc
SINCE = datetime(2026, 11, 1, tzinfo=UTC)
T = datetime(2026, 11, 4, 14, tzinfo=UTC)


@pytest.fixture
def box(tmp_path):
    b = IM(tmp_path / "Messages")
    yield b
    b.close()


# --- the attributedBody decoder ---------------------------------------------

@pytest.mark.parametrize("text", ["ok", "x" * 300, "y" * 70000,
                                  "Café ☕ 你好 — due Friday"])
def test_attributed_body_decodes_every_length_form(text):
    assert im.decode_attributed_body(attributed(text)) == text


@pytest.mark.parametrize("blob", [
    None, b"", b"junk", b"NSString", PREFIX, PREFIX + b"\x81\x05",
    PREFIX + b"\x05ab", PREFIX + b"\x02\xff\xfe", PREFIX + b"\x90abc",
    PREFIX + b"\x82\xff\xff\xff\xff",
])
def test_attributed_body_garbage_is_none_never_an_error(blob):
    assert im.decode_attributed_body(blob) is None


# --- dates -------------------------------------------------------------------

def test_dates_are_nanoseconds_since_2001_or_seconds_on_old_rows():
    assert im.apple_date(0) == im.APPLE_EPOCH
    as_seconds = im.apple_date(500_000_000)
    assert as_seconds == im.APPLE_EPOCH + timedelta(seconds=500_000_000)
    assert im.apple_date(500_000_000 * 10**9) == as_seconds


# --- the schema -------------------------------------------------------------

def test_a_complete_database_misses_nothing(box):
    assert im.check_schema(box.conn) == []


def test_a_renamed_column_and_a_missing_table_are_named(box):
    box.conn.execute("ALTER TABLE message RENAME COLUMN text TO body")
    box.conn.execute("DROP TABLE chat_handle_join")
    assert im.check_schema(box.conn) == ["chat_handle_join", "message.text"]


def test_snapshot_fails_with_the_imessage_error(tmp_path):
    with pytest.raises(im.IMessageError, match="no chat.db"):
        im.snapshot(tmp_path, tmp_path / "tmp")


# --- Contacts ------------------------------------------------------------------

def test_contacts_name_by_last_10_digits_and_by_case_folded_email(tmp_path):
    root = contacts(tmp_path / "ab",
                    {"first": "Rhonda", "last": "Eps",
                     "phones": ["(555) 010-0100"], "emails": ["Rhonda@Example.com"]},
                    {"org": "Acme Supply", "phones": ["+1 555 010 0199"]})
    names = im.contact_names(root, tmp_path / "tmp")
    assert im.name_for(names, "+15550100100") == "Rhonda Eps"
    assert im.name_for(names, "rhonda@EXAMPLE.com") == "Rhonda Eps"
    assert im.name_for(names, "+15550100199") == "Acme Supply"
    assert im.name_for(names, "+15550100777") == "+15550100777"
    assert im.name_for(names, "12345") == "12345"


def test_unreadable_or_missing_contacts_fall_back_to_numbers(tmp_path):
    bad = tmp_path / "ab" / "Sources" / "X"
    bad.mkdir(parents=True)
    (bad / "AddressBook-v22.abcddb").write_bytes(b"not a database" * 50)
    assert im.contact_names(tmp_path / "ab", tmp_path / "tmp") == {}
    assert im.contact_names(tmp_path / "nowhere", tmp_path / "tmp") == {}


# --- chats and messages --------------------------------------------------------

def read(box, names=None, since=SINCE):
    return im.read(box.conn, since, names or {})


def test_a_direct_chat_reads_text_and_names_both_sides(box):
    h = box.handle("+15550100100")
    c = box.chat("+15550100100")
    box.message(c, T, "Can you call me?", handle=h, guid="g1")
    box.message(c, T + timedelta(minutes=1), "Yes at 5", from_me=True, guid="g2")
    [chat] = read(box, {"5550100100": "Rhonda Eps"})
    assert (chat.jid, chat.name, chat.is_group) == ("+15550100100", "Rhonda Eps", False)
    assert [(m.stanza_id, m.sender_name, m.text, m.kind, m.from_me)
            for m in chat.messages] == [
        ("g1", "Rhonda Eps", "Can you call me?", "text", False),
        ("g2", "Matthew", "Yes at 5", "text", True)]
    assert chat.messages[0].sent_at == T


def test_text_kept_only_in_attributed_body_is_read(box):
    h = box.handle("+15550100100")
    c = box.chat("+15550100100")
    box.message(c, T, None, body=attributed("Invoice attached, due Friday"),
                handle=h, guid="a")
    box.message(c, T, None, body=b"\x04\x0bstreamtyped garbage", handle=h, guid="b")
    assert [m.text for m in read(box)[0].messages] == \
        ["Invoice attached, due Friday", "[message not readable]"]


def test_reactions_system_rows_and_empty_rows_are_not_conversation(box):
    h = box.handle("+15550100100")
    c = box.chat("+15550100100")
    box.message(c, T, "hello", handle=h, guid="keep")
    box.message(c, T, "Loved “hello”", handle=h, reaction=2000, guid="tapback")
    box.message(c, T, None, item_type=2, guid="renamed")
    box.message(c, T, None, handle=h, guid="empty")
    assert [m.stanza_id for m in read(box)[0].messages] == ["keep"]


def test_one_persons_imessage_and_sms_threads_are_one_chat(box):
    h1 = box.handle("+15550100100")
    h2 = box.handle("+15550100100", service="SMS")
    c1 = box.chat("+15550100100")
    c2 = box.chat("+15550100100", service="SMS")
    box.message(c1, T, "over iMessage", handle=h1)
    box.message(c2, T + timedelta(minutes=1), "over SMS", handle=h2, service="SMS")
    [chat] = read(box)
    assert [m.text for m in chat.messages] == ["over iMessage", "over SMS"]


def test_groups_are_named_and_their_senders_too(box):
    a = box.handle("+15550100100")
    b = box.handle("dee@example.com")
    d = box.handle("+15550100300")
    e = box.handle("+15550100400")
    named = box.chat("chat111", group=True, name="Board", members=(a, b))
    unnamed = box.chat("chat222", group=True, members=(a, b, d, e))
    box.message(named, T, "Agenda?", handle=b)
    box.message(unnamed, T, "hi all", handle=a)
    names = {"5550100100": "Rhonda Eps", "dee@example.com": "Dee"}
    chats = {c.jid: c for c in read(box, names)}
    assert chats["chat111"].name == "Board" and chats["chat111"].is_group
    assert chats["chat111"].messages[0].sender_name == "Dee"
    assert chats["chat222"].name == "Group with Rhonda Eps, Dee, +15550100300"


def test_attachments_each_become_a_line_of_their_kind(box):
    h = box.handle("+15550100100")
    c = box.chat("+15550100100")
    img = box.attachment("IMG_1.HEIC", mime="image/heic")
    pdf = box.attachment("INV-2291.pdf", mime="application/pdf", content=b"%PDF")
    box.message(c, T, "￼￼Here you go", handle=h, guid="m",
                attachments=(img, pdf))
    vid = box.attachment("clip.mov", mime="video/quicktime")
    voice = box.attachment("Audio Message.caf", mime="audio/x-caf")
    gone = box.attachment("scan.pdf", mime="application/pdf", on_disk=False)
    odd = box.attachment("card.vcf", mime=None)
    box.message(c, T, None, handle=h, guid="v", attachments=(vid,))
    box.message(c, T, None, handle=h, guid="a", audio=True, attachments=(voice,))
    box.message(c, T, None, handle=h, guid="g", attachments=(gone,))
    box.message(c, T, None, handle=h, guid="o", attachments=(odd,))
    msgs = read(box)[0].messages
    assert [(m.stanza_id, m.kind, m.text) for m in msgs] == [
        ("m", "image", "Here you go"), ("m#1", "document", None),
        ("v", "video", None), ("a", "voice", None), ("g", "document", None),
        ("o", "other", "[file: card.vcf]")]
    assert msgs[1].media.local_path.read_bytes() == b"%PDF"
    assert msgs[1].media.title == "INV-2291.pdf"
    assert msgs[4].media.local_path is None and msgs[4].media.title == "scan.pdf"
    assert all(m.media.duration_s is None for m in msgs)


@pytest.mark.parametrize("mime,audio,kind", [
    ("image/jpeg", False, "image"), ("video/mp4", False, "video"),
    ("application/pdf", False, "document"), ("text/plain", False, "document"),
    ("audio/x-caf", True, "voice"), ("audio/mpeg", False, "other"),
    (None, False, "other")])
def test_kinds_come_from_the_mime_type(mime, audio, kind):
    assert im.kind_of(mime, audio) == kind


def test_old_rows_in_seconds_and_new_rows_in_nanoseconds_are_both_read(box):
    h = box.handle("+15550100100")
    c = box.chat("+15550100100")
    box.message(c, datetime(2015, 3, 1, 12, tzinfo=UTC), "from 2015", handle=h,
                seconds=True, guid="old")
    box.message(c, SINCE - timedelta(days=1), "before the window", handle=h,
                guid="before")
    box.message(c, T, "from 2026", handle=h, guid="new")
    assert [m.stanza_id for m in read(box)[0].messages] == ["new"]
    everything = read(box, since=datetime(2015, 1, 1, tzinfo=UTC))[0].messages
    assert [m.stanza_id for m in everything] == ["old", "before", "new"]
    assert everything[0].sent_at == datetime(2015, 3, 1, 12, tzinfo=UTC)


def test_newest_overall_and_per_service(box):
    h = box.handle("+15550100100")
    c1 = box.chat("+15550100100")
    c2 = box.chat("+15550100100", service="SMS")
    box.message(c1, T, "imsg", handle=h)
    box.message(c2, T + timedelta(hours=1), "sms", handle=h, service="SMS")
    assert im.newest(box.conn) == T + timedelta(hours=1)
    assert im.newest_by_service(box.conn) == {"iMessage": T,
                                              "SMS": T + timedelta(hours=1)}


def test_the_probe_counts_and_never_shows_content(box):
    h = box.handle("+15550100100")
    c = box.chat("+15550100100")
    g = box.chat("chat333", group=True, name="Secret Board", members=(h,))
    box.message(c, T, "SECRET-TEXT", handle=h)
    box.message(c, T, None, body=attributed("SECRET-BODY"), handle=h)
    box.message(c, T, None, body=b"NSString junk", handle=h)
    box.message(c, T, "Loved it", handle=h, reaction=2000)
    box.message(g, T, "hi", handle=h, service="SMS",
                attachments=(box.attachment("x.pdf", mime="application/pdf"),))
    p = im.probe(box.conn)
    assert p["missing"] == []
    assert p["services"] == {"iMessage": 4, "SMS": 1}
    assert p["styles"] == {43: 1, 45: 1}
    assert p["attachment_types"] == {"application": 1}
    assert (p["reactions"], p["system"]) == (1, 0)
    assert p["attributed"] == (2, 1)
    assert p["files"] == (1, 1)
    assert p["shared_identifiers"] == 0
    shown = repr(p)
    for secret in ("SECRET", "+15550100100", "Secret Board", "x.pdf", "chat333"):
        assert secret not in shown
