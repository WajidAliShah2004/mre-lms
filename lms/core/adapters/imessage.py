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


# ---------------------------------------------------------------------------
# Contacts: names for numbers and addresses
# ---------------------------------------------------------------------------

def phone_key(number: str) -> str | None:
    """The last 10 digits: "+1 (555) 010-0100" and "5550100100" match. Short
    codes (under 7 digits) have no key and show as themselves."""
    digits = "".join(c for c in number or "" if c.isdigit())
    return digits[-10:] if len(digits) >= 7 else None


def name_for(names: dict[str, str], ident: str) -> str:
    if "@" in (ident or ""):
        return names.get(ident.strip().casefold(), ident)
    key = phone_key(ident)
    return names.get(key, ident) if key else ident


def _person(first: str | None, last: str | None, org: str | None) -> str | None:
    return " ".join(p for p in (first, last) if p) or org or None


def contact_names(root: Path, tmp: Path) -> dict[str, str]:
    """Phone key and case-folded email -> name, from a copy of every Contacts
    database under root. A database that cannot be read is skipped: names
    then fall back to numbers, which is not a failure of the pass."""
    names: dict[str, str] = {}
    try:
        found = sorted(Path(root).glob(f"**/{CONTACTS_DB}"))
    except OSError:
        return names
    for n, path in enumerate(found):
        try:
            copy = _chat.snapshot(path.parent, Path(tmp) / f"contacts{n}",
                                  CONTACTS_DB, error=IMessageError)
            c = sqlite3.connect(copy)
            try:
                people = {pk: _person(first, last, org)
                          for pk, first, last, org in c.execute(
                              "SELECT Z_PK, ZFIRSTNAME, ZLASTNAME, ZORGANIZATION "
                              "FROM ZABCDRECORD")}
                for owner, number in c.execute(
                        "SELECT ZOWNER, ZFULLNUMBER FROM ZABCDPHONENUMBER"):
                    key = phone_key(number or "")
                    if key and people.get(owner):
                        names.setdefault(key, people[owner])
                for owner, address in c.execute(
                        "SELECT ZOWNER, ZADDRESS FROM ZABCDEMAILADDRESS"):
                    if address and people.get(owner):
                        names.setdefault(address.strip().casefold(), people[owner])
            finally:
                c.close()
        except (IMessageError, sqlite3.Error, OSError):
            continue
    return names


# ---------------------------------------------------------------------------
# Chats and messages
# ---------------------------------------------------------------------------

_SQL = """
SELECT m.guid, m.text, m.attributedBody, m.date, m.is_from_me,
       m.is_audio_message, h.id, c.chat_identifier, c.style, c.display_name,
       a.ROWID, a.filename, a.mime_type, a.transfer_name, a.total_bytes
  FROM message m
  JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
  JOIN chat c ON c.ROWID = cmj.chat_id
  LEFT JOIN handle h ON h.ROWID = m.handle_id
  LEFT JOIN message_attachment_join maj ON maj.message_id = m.ROWID
  LEFT JOIN attachment a ON a.ROWID = maj.attachment_id
 WHERE (m.date >= ? OR (m.date < ? AND m.date >= ?))
   AND COALESCE(m.associated_message_type, 0) = 0
   AND COALESCE(m.item_type, 0) = 0
   AND m.guid IS NOT NULL AND c.chat_identifier IS NOT NULL
 ORDER BY c.chat_identifier, m.date, m.ROWID, a.ROWID
"""

_MEMBERS = """
SELECT c.chat_identifier, h.id FROM chat c
  JOIN chat_handle_join chj ON chj.chat_id = c.ROWID
  JOIN handle h ON h.ROWID = chj.handle_id
 WHERE c.style = ? ORDER BY c.chat_identifier, h.ROWID
"""


def kind_of(mime: str | None, is_audio: bool) -> str:
    if is_audio:
        return "voice"
    m = (mime or "").lower()
    if m.startswith("image/"):
        return "image"
    if m.startswith("video/"):
        return "video"
    if m.startswith(("application/", "text/")):
        return "document"
    return "other"


def _local(filename: str | None) -> Path | None:
    if not filename:
        return None
    p = Path(os.path.expanduser(filename))
    return p if p.is_file() else None


def _clean(text: str | None) -> str | None:
    """Without the U+FFFC placeholders Messages puts where attachments go."""
    return (text or "").replace("￼", "").strip() or None


def _text(text: str | None, blob: bytes | None) -> tuple[str | None, bool]:
    """(the message's text, whether a blob was there but could not be read)."""
    if _clean(text):
        return _clean(text), False
    if blob:
        decoded = decode_attributed_body(blob)
        if decoded is None:
            return None, True
        return _clean(decoded), False
    return None, False


def _new_chat(ident: str, style: int | None, display: str | None,
              handles: list[str], names: dict[str, str]) -> Chat:
    if style == GROUP_STYLE:
        if display:
            return Chat(ident, display, True)
        who = [name_for(names, h) for h in handles[:3]]
        return Chat(ident, "Group with " + ", ".join(who) if who else "Group chat",
                    True)
    return Chat(ident, name_for(names, ident), False)


def _messages(guid: str, m: dict, chat: Chat,
              names: dict[str, str]) -> list[Message]:
    when = apple_date(m["date"])
    if m["from_me"]:
        sender = ME
    elif chat.is_group:
        sender = name_for(names, m["handle"]) if m["handle"] else "someone"
    else:
        sender = chat.name
    text, unreadable = _text(m["text"], m["blob"])
    if not m["atts"]:
        if text is None and not unreadable:
            return []           # an app or retracted message: nothing to file
        return [Message(guid, when, m["from_me"], sender,
                        text if text is not None else UNREADABLE, "text", None)]
    out = []
    for n, (filename, mime, transfer, size) in enumerate(m["atts"].values()):
        kind = kind_of(mime, m["audio"])
        body = (text if text is not None else (UNREADABLE if unreadable else None)
                ) if n == 0 else None
        if kind == "other":
            body = f"[file: {transfer or 'untitled'}]" + (f" {body}" if body else "")
        out.append(Message(guid if n == 0 else f"{guid}#{n}", when, m["from_me"],
                           sender, body, kind,
                           Media(_local(filename), transfer, size, None)))
    return out


def read(conn: sqlite3.Connection, since: datetime,
         names: dict[str, str] | None = None) -> list[Chat]:
    """Every chat with a message since `since`, keyed on chat_identifier:
    one person's iMessage and SMS threads are one chat."""
    names = names or {}
    ns = (since - APPLE_EPOCH) // timedelta(microseconds=1) * 1000
    secs = (since - APPLE_EPOCH) // timedelta(seconds=1)
    rows: dict[tuple[str, str], dict] = {}
    for (guid, text, blob, date, from_me, audio, handle, ident, style, display,
         att_id, filename, mime, transfer, size) in conn.execute(
            _SQL, (ns, SECONDS_LIMIT, secs)):
        m = rows.get((ident, guid))
        if m is None:
            m = rows[(ident, guid)] = {
                "text": text, "blob": blob, "date": date,
                "from_me": bool(from_me), "audio": bool(audio), "handle": handle,
                "style": style, "display": display, "atts": {}}
        if att_id is not None:
            m["atts"].setdefault(att_id, (filename, mime, transfer, size))
    members: dict[str, list[str]] = {}
    for ident, handle in conn.execute(_MEMBERS, (GROUP_STYLE,)):
        members.setdefault(ident, []).append(handle)
    chats: dict[str, Chat] = {}
    for (ident, guid), m in rows.items():
        chat = chats.get(ident)
        if chat is None:
            chat = chats[ident] = _new_chat(ident, m["style"], m["display"],
                                            members.get(ident, []), names)
        chat.messages.extend(_messages(guid, m, chat, names))
    return [c for c in chats.values() if c.messages]


def newest(conn: sqlite3.Connection) -> datetime | None:
    """The latest message time in the copy: when Messages on the Mac last synced."""
    v = conn.execute("SELECT MAX(date) FROM message").fetchone()[0]
    return None if v is None else apple_date(v)


def newest_by_service(conn: sqlite3.Connection) -> dict[str, datetime]:
    """The latest iMessage, SMS and RCS times: for the SMS line."""
    return {s: apple_date(v) for s, v in conn.execute(
        "SELECT service, MAX(date) FROM message WHERE service IS NOT NULL "
        "GROUP BY service") if v is not None}


def probe(conn: sqlite3.Connection) -> dict:
    """The database's shape, never its content: for --probe on the Mac.
    Counts, table names and dates only; no text, number, address or name."""
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    out: dict = {"missing": check_schema(conn), "tables": tables,
                 "counts": {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                            for t in REQUIRED if t in tables}}
    if out["missing"]:
        return out
    out["services"] = {s or "(none)": n for s, n in conn.execute(
        "SELECT service, COUNT(*) FROM message GROUP BY 1 ORDER BY 2 DESC")}
    out["styles"] = {s: n for s, n in conn.execute(
        "SELECT style, COUNT(*) FROM chat GROUP BY 1 ORDER BY 1")}
    types: dict[str, int] = {}
    for (mime,) in conn.execute("SELECT mime_type FROM attachment"):
        major = (mime or "(none)").split("/")[0]
        types[major] = types.get(major, 0) + 1
    out["attachment_types"] = types
    out["reactions"] = conn.execute(
        "SELECT COUNT(*) FROM message WHERE COALESCE(associated_message_type, 0) != 0"
    ).fetchone()[0]
    out["system"] = conn.execute(
        "SELECT COUNT(*) FROM message WHERE COALESCE(item_type, 0) != 0").fetchone()[0]
    blobs = [b for (b,) in conn.execute(
        "SELECT attributedBody FROM message WHERE COALESCE(text, '') = '' "
        "AND attributedBody IS NOT NULL")]
    out["attributed"] = (len(blobs),
                         sum(1 for b in blobs if decode_attributed_body(b) is not None))
    out["shared_identifiers"] = conn.execute(
        "SELECT COUNT(*) FROM (SELECT chat_identifier FROM chat "
        "GROUP BY chat_identifier HAVING COUNT(*) > 1)").fetchone()[0]
    files = [f for (f,) in conn.execute(
        "SELECT filename FROM attachment WHERE COALESCE(filename, '') != ''")]
    out["files"] = (sum(1 for f in files if _local(f) is not None), len(files))
    out["newest"] = {s: d.isoformat(timespec="minutes")
                     for s, d in newest_by_service(conn).items()}
    return out
