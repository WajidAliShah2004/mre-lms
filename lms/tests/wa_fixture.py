"""A fake WhatsApp Mac-app database with the tables the reader needs (D-079).

The table and column names are the expected ones (the iOS schema the Mac app
shares). `ops/whatsapp.py --probe` confirms them on the Mac; if it shows
different names, this file and core/adapters/whatsapp.py change together.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)

SCHEMA = """
CREATE TABLE ZWACHATSESSION (Z_PK INTEGER PRIMARY KEY, ZCONTACTJID TEXT,
    ZPARTNERNAME TEXT, ZSESSIONTYPE INTEGER);
CREATE TABLE ZWAGROUPMEMBER (Z_PK INTEGER PRIMARY KEY, ZCONTACTNAME TEXT,
    ZMEMBERJID TEXT);
CREATE TABLE ZWAMEDIAITEM (Z_PK INTEGER PRIMARY KEY, ZMEDIALOCALPATH TEXT,
    ZTITLE TEXT, ZFILESIZE INTEGER, ZMOVIEDURATION REAL);
CREATE TABLE ZWAMESSAGE (Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER,
    ZSTANZAID TEXT, ZMESSAGEDATE REAL, ZISFROMME INTEGER, ZTEXT TEXT,
    ZMESSAGETYPE INTEGER, ZGROUPMEMBER INTEGER, ZMEDIAITEM INTEGER);
"""


class WA:
    def __init__(self, container: Path, *, wal: bool = False):
        self.container = Path(container)
        (self.container / "Message").mkdir(parents=True, exist_ok=True)
        self.path = self.container / "ChatStorage.sqlite"
        self.conn = sqlite3.connect(self.path)
        if wal:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA wal_autocheckpoint=0")
        self.conn.executescript(SCHEMA)
        self._n = 0

    def chat(self, jid: str, name: str, group: bool = False) -> int:
        cur = self.conn.execute(
            "INSERT INTO ZWACHATSESSION (ZCONTACTJID, ZPARTNERNAME, ZSESSIONTYPE) "
            "VALUES (?, ?, ?)", (jid, name, 1 if group else 0))
        self.conn.commit()
        return cur.lastrowid

    def member(self, name: str | None, jid: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO ZWAGROUPMEMBER (ZCONTACTNAME, ZMEMBERJID) VALUES (?, ?)",
            (name, jid))
        self.conn.commit()
        return cur.lastrowid

    def media(self, rel: str | None = None, title: str | None = None,
              size: int | None = None, duration: float | None = None,
              content: bytes = b"x") -> int:
        if rel:
            f = self.container / "Message" / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(content)
        cur = self.conn.execute(
            "INSERT INTO ZWAMEDIAITEM (ZMEDIALOCALPATH, ZTITLE, ZFILESIZE, "
            "ZMOVIEDURATION) VALUES (?, ?, ?, ?)", (rel, title, size, duration))
        self.conn.commit()
        return cur.lastrowid

    def message(self, chat: int, when: datetime, text: str | None = None, *,
                kind: int = 0, from_me: bool = False, member: int | None = None,
                media: int | None = None, stanza: str | None = None) -> str:
        self._n += 1
        stanza = stanza or f"S{self._n:04d}"
        self.conn.execute(
            "INSERT INTO ZWAMESSAGE (ZCHATSESSION, ZSTANZAID, ZMESSAGEDATE, "
            "ZISFROMME, ZTEXT, ZMESSAGETYPE, ZGROUPMEMBER, ZMEDIAITEM) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (chat, stanza, (when - APPLE_EPOCH).total_seconds(), int(from_me),
             text, kind, member, media))
        self.conn.commit()
        return stanza

    def close(self) -> None:
        self.conn.close()
