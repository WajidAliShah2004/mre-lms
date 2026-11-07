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
