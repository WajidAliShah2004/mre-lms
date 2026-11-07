"""Read Messages' chat.db on the Mac, from a copy (R2, D-081).

Every iMessage, SMS and RCS text this Mac's Messages holds is in
~/Library/Messages/chat.db. Like the WhatsApp reader, this module copies the
file (and its -wal and -shm) and reads the COPY (core/adapters/chat.py);
nothing calls Messages and nothing is ever written to it. Names come from a
copy of the Mac's Contacts databases.

The table and column names are a real chat.db's. They are not trusted:
check_schema() names any that are missing, and the pass fails loudly rather
than reading nothing. `ops/imessage.py --probe` prints the real ones.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import chat as _chat
from .chat import Chat, Media, Message

CONTAINER = Path("~/Library/Messages").expanduser()
DB_NAME = "chat.db"
CONTACTS_ROOT = Path("~/Library/Application Support/AddressBook").expanduser()
CONTACTS_DB = "AddressBook-v22.abcddb"
GROUP_STYLE = 43                # chat.style of a group; 45 is one-to-one
COPY_ATTEMPTS = 3
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
SECONDS_LIMIT = 10**11          # a date under this is seconds (old rows), not ns
ME = "Matthew"
UNREADABLE = "[message not readable]"

REQUIRED: dict[str, set[str]] = {
    "message": {"ROWID", "guid", "text", "attributedBody", "date", "is_from_me",
                "handle_id", "service", "associated_message_type", "item_type",
                "is_audio_message"},
    "chat": {"ROWID", "guid", "chat_identifier", "style", "display_name"},
    "handle": {"ROWID", "id"},
    "chat_message_join": {"chat_id", "message_id"},
    "chat_handle_join": {"chat_id", "handle_id"},
    "message_attachment_join": {"message_id", "attachment_id"},
    "attachment": {"ROWID", "filename", "mime_type", "transfer_name",
                   "total_bytes"},
}


class IMessageError(_chat.ChatDBError):
    pass


def snapshot(src_dir: Path, tmp_dir: Path, attempts: int = COPY_ATTEMPTS) -> Path:
    return _chat.snapshot(src_dir, tmp_dir, DB_NAME, attempts, error=IMessageError)


def check_schema(conn: sqlite3.Connection) -> list[str]:
    """Required tables and columns that are not there, sorted."""
    missing = []
    for table, cols in sorted(REQUIRED.items()):
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not have:
            missing.append(table)
            continue
        missing += [f"{table}.{c}" for c in sorted(cols - have)]
    return missing


def apple_date(value: int) -> datetime:
    """message.date: nanoseconds since 2001-01-01 UTC; seconds on old rows."""
    if abs(value) < SECONDS_LIMIT:
        return APPLE_EPOCH + timedelta(seconds=value)
    return APPLE_EPOCH + timedelta(microseconds=value // 1000)


def decode_attributed_body(blob: bytes | None) -> str | None:
    """The text inside an attributedBody "typedstream", or None.

    Newer macOS keeps some messages' text only here. After the NSString
    class marker comes "+", then the length: one byte under 0x80, or 0x81
    and 2 bytes little-endian, or 0x82 and 4 bytes; then that many UTF-8
    bytes. Anything else is None: this never raises."""
    try:
        if not blob:
            return None
        blob = bytes(blob)
        i = blob.find(b"NSString")
        if i < 0:
            return None
        plus = blob.find(b"+", i + 8, i + 20)
        if plus < 0:
            return None
        k = plus + 1
        lead = blob[k]
        if lead == 0x81:
            n, k = int.from_bytes(blob[k + 1:k + 3], "little"), k + 3
        elif lead == 0x82:
            n, k = int.from_bytes(blob[k + 1:k + 5], "little"), k + 5
        elif lead < 0x80:
            n, k = lead, k + 1
        else:
            return None
        raw = blob[k:k + n]
        if len(raw) != n:
            return None
        return raw.decode("utf-8")
    except Exception:                                         # noqa: BLE001
        return None
