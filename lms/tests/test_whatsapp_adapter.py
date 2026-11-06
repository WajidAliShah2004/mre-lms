"""The WhatsApp reader: a copy, checked, read (R40, D-079)."""

import hashlib
import sqlite3
from datetime import datetime, timezone

import pytest

from core.adapters import whatsapp as wa
from tests.wa_fixture import WA

UTC = timezone.utc


def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns


@pytest.fixture
def box(tmp_path):
    w = WA(tmp_path / "container")
    yield w
    w.close()


def test_apple_time_and_numbers():
    assert wa.apple_time(0) == datetime(2001, 1, 1, tzinfo=UTC)
    assert wa.number("15550100@s.whatsapp.net") == "+15550100"
    assert wa.number("1203630@g.us") == "+1203630"
    assert wa.number("status@broadcast") == "status"


def test_snapshot_reads_a_copy_and_never_changes_the_original(box, tmp_path):
    c = box.chat("15550100@s.whatsapp.net", "Acme Supply")
    box.message(c, datetime(2026, 11, 4, 14, tzinfo=UTC), "hello")
    before = digest(box.path)
    copy = wa.snapshot(box.container, tmp_path / "tmp")
    assert copy != box.path and copy.is_file()
    rconn = sqlite3.connect(copy)
    [chat] = wa.read(rconn, datetime(2026, 11, 1, tzinfo=UTC), box.container / "Message")
    rconn.close()
    assert chat.messages[0].text == "hello"
    assert digest(box.path) == before, "the original was touched"


def test_messages_still_in_the_wal_are_seen(tmp_path):
    w = WA(tmp_path / "container", wal=True)
    c = w.chat("15550100@s.whatsapp.net", "Acme")
    w.message(c, datetime(2026, 11, 4, 14, tzinfo=UTC), "in the wal")
    assert (w.container / "ChatStorage.sqlite-wal").stat().st_size > 0
    copy = wa.snapshot(w.container, tmp_path / "tmp")
    rconn = sqlite3.connect(copy)
    [chat] = wa.read(rconn, datetime(2026, 11, 1, tzinfo=UTC), w.container / "Message")
    rconn.close()
    w.close()
    assert chat.messages[0].text == "in the wal"


def test_a_damaged_copy_is_retried_then_fails(tmp_path, monkeypatch):
    src = tmp_path / "container"
    src.mkdir()
    (src / "ChatStorage.sqlite").write_bytes(b"not a database at all" * 100)
    calls = []
    real = wa.shutil.copyfile
    monkeypatch.setattr(wa.shutil, "copyfile",
                        lambda a, b: (calls.append(a), real(a, b))[1])
    with pytest.raises(wa.WhatsAppError, match="quick_check 3 times"):
        wa.snapshot(src, tmp_path / "tmp")
    assert len(calls) == 3


def test_no_database_is_an_error(tmp_path):
    with pytest.raises(wa.WhatsAppError, match="no ChatStorage.sqlite"):
        wa.snapshot(tmp_path, tmp_path / "tmp")


def test_a_missing_column_is_reported(box):
    assert wa.check_schema(box.conn) == []
    box.conn.execute("ALTER TABLE ZWAMESSAGE RENAME COLUMN ZTEXT TO ZBODY")
    assert wa.check_schema(box.conn) == ["ZWAMESSAGE.ZTEXT"]
    box.conn.execute("DROP TABLE ZWAGROUPMEMBER")
    assert "ZWAGROUPMEMBER" in wa.check_schema(box.conn)


def test_read_names_senders_and_skips_status(box):
    direct = box.chat("15550100@s.whatsapp.net", "Acme Supply")
    group = box.chat("1203630@g.us", "Board", group=True)
    status = box.chat("status@broadcast", None)
    jo = box.member("Jo Lee", "15550111@s.whatsapp.net")
    anon = box.member(None, "15550122@s.whatsapp.net")
    t = datetime(2026, 11, 4, 14, tzinfo=UTC)
    box.message(direct, t, "invoice?", from_me=True)
    box.message(direct, t, "sent")
    box.message(group, t, "hi", member=jo)
    box.message(group, t, "hey", member=anon)
    box.message(status, t, "my status")
    chats = {c.jid: c for c in wa.read(box.conn, datetime(2026, 11, 1, tzinfo=UTC),
                                       box.container / "Message")}
    assert set(chats) == {"15550100@s.whatsapp.net", "1203630@g.us"}
    assert [m.sender_name for m in chats["15550100@s.whatsapp.net"].messages] == \
        ["Matthew", "Acme Supply"]
    assert [m.sender_name for m in chats["1203630@g.us"].messages] == \
        ["Jo Lee", "+15550122"]
    assert chats["1203630@g.us"].is_group


def test_read_media_kinds_and_downloads(box):
    c = box.chat("15550100@s.whatsapp.net", "Acme")
    t = datetime(2026, 11, 4, 14, tzinfo=UTC)
    doc = box.media("Media/x/INV.pdf", title="INV-2291.pdf", size=1000, content=b"%PDF")
    gone = box.media("Media/x/never.pdf", title="scan.pdf")
    (box.container / "Message" / "Media/x/never.pdf").unlink()
    voice = box.media("Media/x/a.opus", duration=42.0)
    box.message(c, t, kind=8, media=doc)
    box.message(c, t, kind=8, media=gone)
    box.message(c, t, kind=3, media=voice)
    box.message(c, t, kind=15)
    box.message(c, t, kind=99, text="poll")
    [chat] = wa.read(box.conn, datetime(2026, 11, 1, tzinfo=UTC), box.container / "Message")
    kinds = [m.kind for m in chat.messages]
    assert kinds == ["document", "document", "voice", "sticker", "other"]
    assert chat.messages[0].media.local_path.read_bytes() == b"%PDF"
    assert chat.messages[1].media.local_path is None
    assert chat.messages[1].media.title == "scan.pdf"
    assert chat.messages[2].media.duration_s == 42.0


def test_read_only_returns_messages_since(box):
    c = box.chat("15550100@s.whatsapp.net", "Acme")
    box.message(c, datetime(2026, 10, 1, tzinfo=UTC), "old")
    box.message(c, datetime(2026, 11, 4, tzinfo=UTC), "new")
    [chat] = wa.read(box.conn, datetime(2026, 11, 1, tzinfo=UTC), box.container / "Message")
    assert [m.text for m in chat.messages] == ["new"]


def test_probe_prints_shape_not_content(box):
    c = box.chat("15550100@s.whatsapp.net", "Acme")
    box.message(c, datetime(2026, 11, 4, tzinfo=UTC), "a secret", kind=0)
    p = wa.probe(box.conn)
    assert p["missing"] == []
    assert p["counts"]["ZWAMESSAGE"] == 1
    assert p["message_types"] == {0: 1}
    assert "a secret" not in repr(p)


def test_probe_shows_which_session_type_is_a_group(box):
    # GROUP_SESSION was a guess; the Mac's group chats have JIDs ending @g.us.
    box.chat("15550100@s.whatsapp.net", "Acme")
    box.chat("120363000000000001@g.us", "Board", group=True)
    box.chat("120363000000000002@g.us", "Co-op", group=True)
    p = wa.probe(box.conn)
    assert p["session_types"] == {0: (1, 0), 1: (2, 2)}
    assert p["session_domains"] == {0: {"s.whatsapp.net": 1}, 1: {"g.us": 2}}
    assert "Board" not in repr(p) and "120363" not in repr(p)
    assert "15550100" not in repr(p)


def test_probe_lists_file_extensions_per_type(box):
    # Unknown types with media and no text (14, 59, 66 on the Mac) may be
    # photos or documents the reader would not file; the extension says.
    c = box.chat("15550100@s.whatsapp.net", "Acme")
    when = datetime(2026, 11, 4, tzinfo=UTC)
    box.message(c, when, None, kind=14, media=box.media("a/secret-name.webp"))
    box.message(c, when, None, kind=14, media=box.media("a/other.WEBP"))
    box.message(c, when, None, kind=14, media=box.media(None, title="Lease.pdf"))
    p = wa.probe(box.conn)
    assert p["media_ext"] == {14: {".webp": 2, "(no file)": 1}}
    assert "secret" not in repr(p) and "Lease" not in repr(p)


def test_link_messages_are_text():
    # Mac probe, Nov 6: all 618 messages of type 7 have text (a link preview).
    assert wa.KINDS[7] == "text"


def test_probe_shows_which_message_types_carry_text_or_media(box):
    c = box.chat("15550100@s.whatsapp.net", "Acme")
    when = datetime(2026, 11, 4, tzinfo=UTC)
    box.message(c, when, "see https://example.com", kind=7)
    box.message(c, when, None, kind=6)
    box.message(c, when, "", kind=6)
    box.message(c, when, "the invoice", kind=8, media=box.media(title="inv.pdf"))
    p = wa.probe(box.conn)
    assert p["type_shape"] == {6: (0, 0), 7: (1, 0), 8: (1, 1)}
    assert "invoice" not in repr(p) and "example" not in repr(p)
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))


def test_probe_command_reports_shape_and_exit_status(box, capsys):
    import whatsapp as job
    c = box.chat("15550100@s.whatsapp.net", "Acme")
    box.message(c, datetime(2026, 11, 4, tzinfo=UTC), "private words")
    assert job.do_probe(box.container) == 0
    out = capsys.readouterr().out
    assert "ZWAMESSAGE: 1" in out and "private words" not in out
    assert "session types: 0=1 (0 groups; s.whatsapp.net 1)" in out
    assert "0=1 (text), with text 1, with media 0" in out
    box.conn.execute("DROP TABLE ZWAGROUPMEMBER")
    assert job.do_probe(box.container) == 1
    assert "MISSING: ZWAGROUPMEMBER" in capsys.readouterr().out
