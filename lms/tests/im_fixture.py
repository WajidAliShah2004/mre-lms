"""A fake Messages chat.db and Contacts database, with the tables the
iMessage reader needs (D-081).

The table and column names are the ones in a real chat.db and
AddressBook-v22.abcddb. `ops/imessage.py --probe` confirms them on the Mac;
if it shows different ones, this file and core/adapters/imessage.py change
together. Numbers are 555-01xx test numbers only.
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)

SCHEMA = """
CREATE TABLE handle (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL,
    service TEXT);
CREATE TABLE chat (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, guid TEXT UNIQUE,
    style INTEGER, chat_identifier TEXT, service_name TEXT, display_name TEXT);
CREATE TABLE message (ROWID INTEGER PRIMARY KEY AUTOINCREMENT,
    guid TEXT UNIQUE NOT NULL, text TEXT, attributedBody BLOB,
    handle_id INTEGER DEFAULT 0, service TEXT, date INTEGER,
    is_from_me INTEGER DEFAULT 0, associated_message_type INTEGER DEFAULT 0,
    item_type INTEGER DEFAULT 0, is_audio_message INTEGER DEFAULT 0);
CREATE TABLE attachment (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, guid TEXT,
    filename TEXT, mime_type TEXT, transfer_name TEXT, total_bytes INTEGER);
CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER,
    message_date INTEGER DEFAULT 0, PRIMARY KEY (chat_id, message_id));
CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER,
    UNIQUE (chat_id, handle_id));
CREATE TABLE message_attachment_join (message_id INTEGER, attachment_id INTEGER,
    UNIQUE (message_id, attachment_id));
"""

CONTACTS_SCHEMA = """
CREATE TABLE ZABCDRECORD (Z_PK INTEGER PRIMARY KEY, ZFIRSTNAME TEXT,
    ZLASTNAME TEXT, ZORGANIZATION TEXT, ZNICKNAME TEXT);
CREATE TABLE ZABCDPHONENUMBER (Z_PK INTEGER PRIMARY KEY, ZOWNER INTEGER,
    ZFULLNUMBER TEXT);
CREATE TABLE ZABCDEMAILADDRESS (Z_PK INTEGER PRIMARY KEY, ZOWNER INTEGER,
    ZADDRESS TEXT);
"""

# The typedstream bytes before the text in a real attributedBody, and some
# of what follows it.
PREFIX = (b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84"
          b"\x12NSAttributedString\x00\x84\x84\x08NSObject\x00\x85\x92"
          b"\x84\x84\x84\x08NSString\x01\x94\x84\x01+")
SUFFIX = (b"\x86\x84\x02iI\x01\x05\x92\x84\x84\x84\x0cNSDictionary\x00"
          b"\x94\x84\x01i\x01\x92\x86\x86")


def attributed(text: str) -> bytes:
    raw = text.encode("utf-8")
    n = len(raw)
    if n < 0x80:
        length = bytes([n])
    elif n < 0x10000:
        length = b"\x81" + n.to_bytes(2, "little")
    else:
        length = b"\x82" + n.to_bytes(4, "little")
    return PREFIX + length + raw + SUFFIX


def apple_ns(when: datetime) -> int:
    return (when - APPLE_EPOCH) // timedelta(microseconds=1) * 1000


def apple_s(when: datetime) -> int:
    return (when - APPLE_EPOCH) // timedelta(seconds=1)


class IM:
    def __init__(self, root: Path):
        self.root = Path(root)
        (self.root / "Attachments").mkdir(parents=True, exist_ok=True)
        self.path = self.root / "chat.db"
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript(SCHEMA)
        self._n = 0

    def _next(self) -> int:
        self._n += 1
        return self._n

    def handle(self, ident: str, service: str = "iMessage") -> int:
        cur = self.conn.execute("INSERT INTO handle (id, service) VALUES (?, ?)",
                                (ident, service))
        self.conn.commit()
        return cur.lastrowid

    def chat(self, identifier: str, *, group: bool = False, name: str | None = None,
             members=(), service: str = "iMessage") -> int:
        cur = self.conn.execute(
            "INSERT INTO chat (guid, style, chat_identifier, service_name, "
            "display_name) VALUES (?, ?, ?, ?, ?)",
            (f"{service};{'+' if group else '-'};{identifier}", 43 if group else 45,
             identifier, service, name or ""))
        for h in members:
            self.conn.execute("INSERT INTO chat_handle_join VALUES (?, ?)",
                              (cur.lastrowid, h))
        self.conn.commit()
        return cur.lastrowid

    def attachment(self, name: str, *, mime: str | None = None,
                   content: bytes = b"x", on_disk: bool = True) -> int:
        f = self.root / "Attachments" / f"a{self._next()}" / name
        if on_disk:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(content)
        cur = self.conn.execute(
            "INSERT INTO attachment (guid, filename, mime_type, transfer_name, "
            "total_bytes) VALUES (?, ?, ?, ?, ?)",
            (f"at-{self._n}", str(f), mime, name, len(content)))
        self.conn.commit()
        return cur.lastrowid

    def message(self, chat_id: int, when: datetime, text: str | None = None, *,
                from_me: bool = False, handle: int | None = None,
                service: str = "iMessage", guid: str | None = None,
                body: bytes | None = None, reaction: int = 0, item_type: int = 0,
                audio: bool = False, seconds: bool = False,
                attachments=()) -> str:
        guid = guid or f"G-{self._next()}"
        date = apple_s(when) if seconds else apple_ns(when)
        cur = self.conn.execute(
            "INSERT INTO message (guid, text, attributedBody, handle_id, service, "
            "date, is_from_me, associated_message_type, item_type, "
            "is_audio_message) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (guid, text, body, handle or 0, service, date, int(from_me),
             reaction, item_type, int(audio)))
        self.conn.execute("INSERT INTO chat_message_join VALUES (?, ?, ?)",
                          (chat_id, cur.lastrowid, date))
        for a in attachments:
            self.conn.execute("INSERT INTO message_attachment_join VALUES (?, ?)",
                              (cur.lastrowid, a))
        self.conn.commit()
        return guid

    def close(self) -> None:
        self.conn.close()


def contacts(root: Path, *people: dict) -> Path:
    """A Contacts folder holding one source database. Each person is a dict
    with any of first, last, org, phones, emails. Returns root."""
    folder = Path(root) / "Sources" / "A1B2"
    folder.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(folder / "AddressBook-v22.abcddb")
    c.executescript(CONTACTS_SCHEMA)
    for p in people:
        pk = c.execute("INSERT INTO ZABCDRECORD (ZFIRSTNAME, ZLASTNAME, "
                       "ZORGANIZATION) VALUES (?, ?, ?)",
                       (p.get("first"), p.get("last"), p.get("org"))).lastrowid
        for ph in p.get("phones", ()):
            c.execute("INSERT INTO ZABCDPHONENUMBER (ZOWNER, ZFULLNUMBER) "
                      "VALUES (?, ?)", (pk, ph))
        for e in p.get("emails", ()):
            c.execute("INSERT INTO ZABCDEMAILADDRESS (ZOWNER, ZADDRESS) "
                      "VALUES (?, ?)", (pk, e))
    c.commit()
    c.close()
    return Path(root)
