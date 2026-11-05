"""Read the WhatsApp Mac app's chat database, from a copy (R40, D-079).

WhatsApp has no API for a personal account's history. The Mac app keeps every
chat it has synced in ChatStorage.sqlite in its group container. This module
copies that file (and its -wal and -shm) and reads the COPY; SQLite never
opens the original, so a WhatsApp running beside us cannot be disturbed and
nothing of his is ever written.

The table and column names are the iOS schema the Mac app is reported to
share. They are not trusted: check_schema() names any that are missing, and
the pass fails loudly rather than reading nothing (a WhatsApp update that
renames a column must not look like a quiet hour). `ops/whatsapp.py --probe`
prints the real ones on the Mac.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

CONTAINER = Path("~/Library/Group Containers/"
                 "group.net.whatsapp.WhatsApp.shared").expanduser()
DB_NAME = "ChatStorage.sqlite"
MEDIA_DIR = "Message"
STATUS_JID = "status@broadcast"
GROUP_SESSION = 1               # ZSESSIONTYPE of a group; confirm with --probe
COPY_ATTEMPTS = 3
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
ME = "Matthew"

REQUIRED: dict[str, set[str]] = {
    "ZWACHATSESSION": {"Z_PK", "ZCONTACTJID", "ZPARTNERNAME", "ZSESSIONTYPE"},
    "ZWAMESSAGE": {"Z_PK", "ZCHATSESSION", "ZSTANZAID", "ZMESSAGEDATE",
                   "ZISFROMME", "ZTEXT", "ZMESSAGETYPE", "ZGROUPMEMBER",
                   "ZMEDIAITEM"},
    "ZWAMEDIAITEM": {"Z_PK", "ZMEDIALOCALPATH", "ZTITLE", "ZFILESIZE",
                     "ZMOVIEDURATION"},
    "ZWAGROUPMEMBER": {"Z_PK", "ZCONTACTNAME", "ZMEMBERJID"},
}

# ZMESSAGETYPE -> kind. Expected values; --probe lists what the Mac has.
KINDS: dict[int, str] = {0: "text", 1: "image", 2: "video", 3: "voice",
                         8: "document", 15: "sticker"}


class WhatsAppError(RuntimeError):
    pass


@dataclass(frozen=True)
class Media:
    local_path: Path | None         # None: the Mac app never downloaded it
    title: str | None
    size: int | None
    duration_s: float | None


@dataclass(frozen=True)
class Message:
    stanza_id: str                  # WhatsApp's own id; survives a re-link
    sent_at: datetime
    from_me: bool
    sender_name: str
    text: str | None
    kind: str
    media: Media | None


@dataclass
class Chat:
    jid: str
    name: str
    is_group: bool
    messages: list[Message] = field(default_factory=list)


def apple_time(seconds: float) -> datetime:
    return APPLE_EPOCH + timedelta(seconds=seconds)


def number(jid: str) -> str:
    local = (jid or "").split("@", 1)[0]
    return f"+{local}" if local.isdigit() else local


def snapshot(src_dir: Path, tmp_dir: Path, attempts: int = COPY_ATTEMPTS) -> Path:
    """Copy the database and its WAL into tmp_dir; return the checked copy.

    WhatsApp may be writing while the three files are copied, so a copy can be
    torn. quick_check catches that; the copy is retried, then the pass fails."""
    src = Path(src_dir) / DB_NAME
    if not src.is_file():
        raise WhatsAppError(f"no {DB_NAME} in {src_dir}")
    tmp_dir = Path(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(tmp_dir, 0o700)
    last = ""
    for n in range(attempts):
        dest = tmp_dir / f"copy{n}"
        dest.mkdir(mode=0o700)
        for suffix in ("", "-wal", "-shm"):
            f = Path(src_dir) / (DB_NAME + suffix)
            if f.exists():
                shutil.copyfile(f, dest / f.name)
        copy = dest / DB_NAME
        try:
            conn = sqlite3.connect(copy)
            try:
                last = conn.execute("PRAGMA quick_check").fetchone()[0]
            finally:
                conn.close()
        except sqlite3.DatabaseError as exc:
            last = str(exc)
        if last == "ok":
            return copy
    raise WhatsAppError(f"copy failed quick_check {attempts} times: {last}")


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


_SQL = """
SELECT m.ZSTANZAID, m.ZMESSAGEDATE, m.ZISFROMME, m.ZTEXT, m.ZMESSAGETYPE,
       s.ZCONTACTJID, s.ZPARTNERNAME, s.ZSESSIONTYPE,
       g.ZCONTACTNAME, g.ZMEMBERJID,
       i.ZMEDIALOCALPATH, i.ZTITLE, i.ZFILESIZE, i.ZMOVIEDURATION, i.Z_PK
  FROM ZWAMESSAGE m
  JOIN ZWACHATSESSION s ON s.Z_PK = m.ZCHATSESSION
  LEFT JOIN ZWAGROUPMEMBER g ON g.Z_PK = m.ZGROUPMEMBER
  LEFT JOIN ZWAMEDIAITEM i ON i.Z_PK = m.ZMEDIAITEM
 WHERE m.ZMESSAGEDATE >= ? AND s.ZCONTACTJID != ?
   AND m.ZSTANZAID IS NOT NULL
 ORDER BY s.ZCONTACTJID, m.ZMESSAGEDATE, m.Z_PK
"""


def read(conn: sqlite3.Connection, since: datetime, media_root: Path) -> list[Chat]:
    chats: dict[str, Chat] = {}
    since_s = (since - APPLE_EPOCH).total_seconds()
    for (stanza, when, from_me, text, mtype, jid, partner, stype, member,
         member_jid, rel, title, size, duration, media_pk) in conn.execute(
            _SQL, (since_s, STATUS_JID)):
        is_group = stype == GROUP_SESSION
        chat = chats.get(jid)
        if chat is None:
            chat = chats[jid] = Chat(jid, partner or number(jid), is_group)
        if from_me:
            sender = ME
        elif is_group:
            sender = member or number(member_jid or "")
        else:
            sender = chat.name
        media = None
        if media_pk is not None:
            path = Path(media_root) / rel if rel else None
            media = Media(path if path and path.is_file() else None, title,
                          size, duration)
        chat.messages.append(Message(stanza, apple_time(when), bool(from_me),
                                     sender, text, KINDS.get(mtype, "other"),
                                     media))
    return list(chats.values())


def probe(conn: sqlite3.Connection) -> dict:
    """The database's shape, never its content: for --probe on the Mac."""
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in REQUIRED if t in tables}
    types = {}
    if "ZWAMESSAGE" in tables:
        types = {t: n for t, n in conn.execute(
            "SELECT ZMESSAGETYPE, COUNT(*) FROM ZWAMESSAGE GROUP BY 1 ORDER BY 1")}
    return {"missing": check_schema(conn), "tables": tables, "counts": counts,
            "message_types": types}
