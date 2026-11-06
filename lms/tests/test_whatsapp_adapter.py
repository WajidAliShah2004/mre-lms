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
    # Mac probe Nov 6: 22 direct chats use WhatsApp's linked IDs, not numbers.
    assert wa.number("185432112345678@lid") == "number hidden"


def test_own_jids_comes_from_the_stored_number():
    # D-080 stores his number, digits only, in Keychain lms/whatsapp-owner.
    seen = []

    def lookup(service):
        seen.append(service)
        return "+1 (555) 0100"
    assert wa.own_jids(lookup) == frozenset({"15550100@s.whatsapp.net"})
    assert seen == ["lms/whatsapp-owner"]
    assert wa.own_jids(lambda s: None) == frozenset()


def test_read_skips_his_own_chat(box):
    # His "Message yourself" chat holds /lms commands and the LMS's replies
    # (D-080); reading it would feed the LMS its own output (user, Nov 6).
    t = datetime(2026, 11, 4, 14, tzinfo=UTC)
    box.message(box.chat("15550100@s.whatsapp.net", "Acme"), t, "kept")
    box.message(box.chat("15559999@s.whatsapp.net", "Matthew"), t, "/lms tasks")
    chats = wa.read(box.conn, datetime(2026, 11, 1, tzinfo=UTC),
                    box.container / "Message",
                    skip=frozenset({"15559999@s.whatsapp.net"}))
    assert [c.jid for c in chats] == ["15550100@s.whatsapp.net"]
    assert wa.own_chat_count(box.conn, frozenset({"15559999@s.whatsapp.net"})) == 1
    assert wa.own_chat_count(box.conn, frozenset({"1@s.whatsapp.net"})) == 0


def test_probe_says_whether_his_own_chat_is_found(box, capsys, monkeypatch):
    import whatsapp as job
    box.message(box.chat("15559999@s.whatsapp.net", "Matthew"),
                datetime(2026, 11, 4, tzinfo=UTC), "/lms help")
    monkeypatch.setattr(wa, "own_jids",
                        lambda: frozenset({"15559999@s.whatsapp.net"}))
    job.do_probe(box.container)
    out = capsys.readouterr().out
    assert "own chat (Message yourself): found under his number" in out
    assert "15559999" not in out
    monkeypatch.setattr(wa, "own_jids", lambda: frozenset())
    job.do_probe(box.container)
    assert "not found (and no number in Keychain" in capsys.readouterr().out


def test_own_chat_is_found_by_content_under_a_hidden_number(box):
    # Mac probe, Nov 6: "Message yourself" is not under his number but an
    # @lid id. It is the one direct chat where every message is his and he
    # sends /lms commands.
    t = datetime(2026, 11, 4, 14, tzinfo=UTC)
    me = box.chat("185432112345678@lid", "Matthew")
    box.message(me, t, "/lms tasks", from_me=True)
    box.message(me, t, "#7 Eo compliance notice", from_me=True)
    friend = box.chat("15550100@s.whatsapp.net", "Jo")
    box.message(friend, t, "/lms status", from_me=True)     # typed by mistake
    box.message(friend, t, "what?")                          # Jo replied
    group = box.chat("120363000000000001@g.us", "Board", group=True)
    box.message(group, t, "/lms help", from_me=True)
    notes = box.chat("185400000000001@lid", "Pat")           # only his, no /lms
    box.message(notes, t, "call me back", from_me=True)
    assert wa.own_chats_by_content(box.conn) == frozenset({"185432112345678@lid"})
    assert wa.own_chats(box.conn, frozenset()) == frozenset({"185432112345678@lid"})
    assert wa.own_chats(box.conn, frozenset({"15559999@s.whatsapp.net"})) == \
        frozenset({"185432112345678@lid", "15559999@s.whatsapp.net"})
    chats = wa.read(box.conn, datetime(2026, 11, 1, tzinfo=UTC),
                    box.container / "Message",
                    skip=wa.own_chats(box.conn, frozenset()))
    assert "185432112345678@lid" not in {c.jid for c in chats}
    assert "15550100@s.whatsapp.net" in {c.jid for c in chats}


def test_probe_reports_the_own_chat_found_by_content(box, capsys, monkeypatch):
    import whatsapp as job
    t = datetime(2026, 11, 4, tzinfo=UTC)
    box.message(box.chat("185432112345678@lid", "Matthew"), t, "/lms help",
                from_me=True)
    monkeypatch.setattr(wa, "own_jids",
                        lambda: frozenset({"15559999@s.whatsapp.net"}))
    job.do_probe(box.container)
    out = capsys.readouterr().out
    assert ("own chat (Message yourself): found by its /lms commands (lid); "
            "the reader skips it") in out
    assert "185432112345678" not in out and "15559999" not in out
    box.message(box.chat("185400000000002@lid", "Matthew 2"), t, "/lms tasks",
                from_me=True)
    job.do_probe(box.container)
    assert "2 chats found" in capsys.readouterr().out


def test_read_skips_broadcast_and_status_sessions(box):
    # Mac probe Nov 6: session type 2 is a broadcast list, 3 the status feed.
    t = datetime(2026, 11, 4, 14, tzinfo=UTC)
    box.message(box.chat("15550100@s.whatsapp.net", "Acme"), t, "kept")
    box.message(box.chat("1699999999@broadcast", "My list", stype=2), t, "list")
    box.message(box.chat("0@status", None, stype=3), t, "feed")
    chats = wa.read(box.conn, datetime(2026, 11, 1, tzinfo=UTC),
                    box.container / "Message")
    assert [c.jid for c in chats] == ["15550100@s.whatsapp.net"]


def test_probe_shows_where_media_could_be(box):
    # Mac probe Nov 6: no media item had ZMEDIALOCALPATH. Which columns are
    # filled, and what files the media folder holds, say where media is.
    c = box.chat("15550100@s.whatsapp.net", "Acme")
    when = datetime(2026, 11, 4, tzinfo=UTC)
    box.message(c, when, None, kind=8, media=box.media(None, title="Lease.pdf"))
    box.message(c, when, None, kind=1, media=box.media("a/b/secret.jpg"))
    p = wa.probe(box.conn)
    assert p["media_columns"]["ZMEDIALOCALPATH"] == 1
    assert p["media_columns"]["ZTITLE"] == 1
    assert p["media_columns"]["Z_PK"] == 2
    assert wa.media_files(box.container / "Message") == {".jpg": 1}
    assert "Lease" not in repr(p) and "secret" not in repr(p)


def test_local_paths_found_counts_what_the_reader_can_open(box):
    # Mac, Nov 6: auto-download filled ZMEDIALOCALPATH for a new PDF. This
    # says whether read() would find it under the media folder.
    c = box.chat("15550100@s.whatsapp.net", "Acme")
    when = datetime(2026, 11, 4, tzinfo=UTC)
    box.message(c, when, None, kind=8, media=box.media("a/inv.pdf"))
    gone = box.media(None)
    box.conn.execute("UPDATE ZWAMEDIAITEM SET ZMEDIALOCALPATH='b/missing.pdf' "
                     "WHERE Z_PK=?", (gone,))
    box.conn.commit()
    box.message(c, when, None, kind=8, media=gone)
    assert wa.local_paths_found(box.conn, box.container / "Message") == (1, 2)


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
    assert "media item columns filled: \n" in out   # no media rows at all
    assert "media folder files: 0" in out
    assert "local paths the reader can open: 0 of 0" in out
    box.conn.execute("DROP TABLE ZWAGROUPMEMBER")
    assert job.do_probe(box.container) == 1
    assert "MISSING: ZWAGROUPMEMBER" in capsys.readouterr().out


def test_newest_is_the_latest_message_in_the_copy(box):
    assert wa.newest(box.conn) is None
    c = box.chat("15550100@s.whatsapp.net", "Acme")
    box.message(c, datetime(2026, 11, 4, 9, tzinfo=UTC), "older")
    box.message(c, datetime(2026, 11, 4, 14, tzinfo=UTC), "newest")
    assert wa.newest(box.conn) == datetime(2026, 11, 4, 14, tzinfo=UTC)


def test_dry_run_reports_an_unreadable_container_without_a_traceback(monkeypatch, capsys):
    import whatsapp as job
    from core.pipeline import whatsapp as w

    def denied(*a, **k):
        raise PermissionError("Operation not permitted")
    monkeypatch.setattr(w, "read_chats", denied)
    assert job.do_dry_run(None, datetime(2026, 11, 5, tzinfo=UTC)) == 1
    assert "FAIL  Operation not permitted" in capsys.readouterr().err
