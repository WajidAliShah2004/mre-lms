"""The checked copy and the types every chat reader shares (D-079, D-081)."""

import sqlite3

import pytest

from core.adapters import chat
from core.adapters import whatsapp as wa


def make_db(folder, name):
    folder.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(folder / name)
    c.execute("CREATE TABLE t (x)")
    c.execute("INSERT INTO t VALUES (1)")
    c.commit()
    c.close()


def test_snapshot_copies_any_named_database_and_leaves_the_original(tmp_path):
    make_db(tmp_path / "src", "chat.db")
    before = (tmp_path / "src" / "chat.db").read_bytes()
    copy = chat.snapshot(tmp_path / "src", tmp_path / "tmp", "chat.db")
    assert copy.name == "chat.db" and copy.parent.parent == tmp_path / "tmp"
    c = sqlite3.connect(copy)
    assert c.execute("SELECT x FROM t").fetchone() == (1,)
    c.close()
    assert (tmp_path / "src" / "chat.db").read_bytes() == before


def test_a_missing_database_raises_the_callers_error(tmp_path):
    class Mine(chat.ChatDBError):
        pass
    with pytest.raises(Mine, match="no chat.db in"):
        chat.snapshot(tmp_path, tmp_path / "tmp", "chat.db", error=Mine)


def test_a_torn_copy_is_retried_then_fails(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "chat.db").write_bytes(b"not a database" * 100)
    with pytest.raises(chat.ChatDBError, match="quick_check 3 times"):
        chat.snapshot(tmp_path / "src", tmp_path / "tmp", "chat.db")


def test_whatsapp_keeps_its_names():
    assert wa.Media is chat.Media and wa.Message is chat.Message
    assert wa.Chat is chat.Chat
    assert issubclass(wa.WhatsAppError, chat.ChatDBError)
