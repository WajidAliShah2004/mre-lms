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
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

CONTAINER = Path("~/Library/Group Containers/"
                 "group.net.whatsapp.WhatsApp.shared").expanduser()
DB_NAME = "ChatStorage.sqlite"
MEDIA_DIR = "Message"
STATUS_JID = "status@broadcast"
OWNER_SERVICE = "lms/whatsapp-owner"    # Keychain item D-080 stores, digits
GROUP_SESSION = 1               # ZSESSIONTYPE of a group (Mac probe Nov 6: all 14 @g.us chats)
# Only 0 (direct) and 1 (group) are read: 2 is a broadcast list, 3 the status
# feed (Mac probe Nov 6); _SQL filters on these two values.
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

# ZMESSAGETYPE -> kind. --probe lists what the Mac has. 7 is a message with a
# link preview: on the Mac (Nov 6) all 618 had text, so it reads as text.
KINDS: dict[int, str] = {0: "text", 1: "image", 2: "video", 3: "voice",
                         7: "text", 8: "document", 15: "sticker"}


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
    local, _, domain = (jid or "").partition("@")
    if domain == "lid":             # WhatsApp's linked ID: not a phone number
        return "number hidden"
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
   AND s.ZSESSIONTYPE IN (0, 1)
   AND m.ZSTANZAID IS NOT NULL
 ORDER BY s.ZCONTACTJID, m.ZMESSAGEDATE, m.Z_PK
"""


def _keychain(service: str) -> str | None:
    try:
        r = subprocess.run(["/usr/bin/security", "find-generic-password",
                            "-a", "lms", "-s", service, "-w"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None                      # not a Mac (tests), or no Keychain
    value = r.stdout.strip()
    return value if r.returncode == 0 and value else None


def own_jids(lookup=None) -> frozenset[str]:
    """Matthew's own chat ("Message yourself"), from the number D-080 stored.

    That chat carries his /lms commands and the LMS's replies (D-080). The
    reader skips it, so the LMS never reads its own output back in as new
    messages (echo alerts, duplicate tasks). Empty if the number is not set:
    verify_setup check [12] fails then."""
    digits = "".join(c for c in ((lookup or _keychain)(OWNER_SERVICE) or "")
                     if c.isdigit())
    return frozenset({f"{digits}@s.whatsapp.net"}) if digits else frozenset()


def own_chat_count(conn: sqlite3.Connection, jids: frozenset[str]) -> int:
    """How many chat sessions are his own chat: for --probe, a count only."""
    return sum(conn.execute("SELECT COUNT(*) FROM ZWACHATSESSION "
                            "WHERE ZCONTACTJID = ?", (j,)).fetchone()[0]
               for j in jids)


# His own chat by what is in it. On the Mac (Nov 6) "Message yourself" is not
# stored under his number but under a hidden-number @lid id, which nothing
# here can map to the number. It is the direct chat where he sends /lms
# commands (D-080 answers /lms only there) and nearly every message is his:
# on the Mac 76 of 77, the other one a WhatsApp notice. So: at most 2
# messages not marked his, and under 5% of the chat. A conversation with
# anyone else fails that (the one other chat with /lms text: 60% his).
_OWN_BY_CONTENT = """
SELECT s.ZCONTACTJID FROM ZWACHATSESSION s
  JOIN ZWAMESSAGE m ON m.ZCHATSESSION = s.Z_PK
 WHERE s.ZSESSIONTYPE = 0 AND s.ZCONTACTJID IS NOT NULL
 GROUP BY s.Z_PK
HAVING SUM(COALESCE(m.ZISFROMME, 0) = 1
           AND (m.ZTEXT = '/lms' OR m.ZTEXT LIKE '/lms %')) > 0
   AND SUM(COALESCE(m.ZISFROMME, 0) = 0) <= 2
   AND SUM(COALESCE(m.ZISFROMME, 0) = 0) * 20 < COUNT(*)
"""


_LMS_CHATS = """
SELECT s.ZSESSIONTYPE, s.ZCONTACTJID, COUNT(*),
       SUM(COALESCE(m.ZISFROMME, 0) = 1),
       SUM(m.ZTEXT = '/lms' OR m.ZTEXT LIKE '/lms %'),
       SUM(COALESCE(m.ZISFROMME, 0) = 1
           AND (m.ZTEXT = '/lms' OR m.ZTEXT LIKE '/lms %'))
  FROM ZWACHATSESSION s JOIN ZWAMESSAGE m ON m.ZCHATSESSION = s.Z_PK
 WHERE s.Z_PK IN (SELECT ZCHATSESSION FROM ZWAMESSAGE
                   WHERE ZTEXT LIKE '%/lms%')
 GROUP BY s.Z_PK ORDER BY 3 DESC
"""


def lms_chat_shapes(conn: sqlite3.Connection) -> list[dict]:
    """Chats with any '/lms' text, as counts only: for --probe, to see how
    the Mac stores his own chat. No id, number or text leaves this."""
    return [{"type": t, "domain": (jid or "").rpartition("@")[2] or "(none)",
             "messages": n, "from_him": mine or 0, "lms": lms or 0,
             "lms_from_him": lms_mine or 0}
            for t, jid, n, mine, lms, lms_mine in conn.execute(_LMS_CHATS)]


def own_chats_by_content(conn: sqlite3.Connection) -> frozenset[str]:
    return frozenset(r[0] for r in conn.execute(_OWN_BY_CONTENT))


def own_chats(conn: sqlite3.Connection,
              numbered: frozenset[str] | None = None) -> frozenset[str]:
    """Every JID that is his own chat: under his number, or found by content."""
    numbered = own_jids() if numbered is None else numbered
    return frozenset(numbered) | own_chats_by_content(conn)


def read(conn: sqlite3.Connection, since: datetime, media_root: Path,
         skip: frozenset[str] = frozenset()) -> list[Chat]:
    chats: dict[str, Chat] = {}
    since_s = (since - APPLE_EPOCH).total_seconds()
    for (stanza, when, from_me, text, mtype, jid, partner, stype, member,
         member_jid, rel, title, size, duration, media_pk) in conn.execute(
            _SQL, (since_s, STATUS_JID)):
        if jid in skip:
            continue
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


def newest(conn: sqlite3.Connection) -> datetime | None:
    """The latest message time in the copy: when the Mac app last synced."""
    s = conn.execute("SELECT MAX(ZMESSAGEDATE) FROM ZWAMESSAGE").fetchone()[0]
    return None if s is None else apple_time(s)


def probe(conn: sqlite3.Connection) -> dict:
    """The database's shape, never its content: for --probe on the Mac."""
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in REQUIRED if t in tables}
    missing = check_schema(conn)
    types, sessions, shape, domains, exts = {}, {}, {}, {}, {}
    if not missing:
        types = {t: n for t, n in conn.execute(
            "SELECT ZMESSAGETYPE, COUNT(*) FROM ZWAMESSAGE GROUP BY 1 ORDER BY 1")}
        # session type -> (chats, of which group JIDs): confirms GROUP_SESSION.
        sessions = {t: (n, g) for t, n, g in conn.execute(
            "SELECT ZSESSIONTYPE, COUNT(*), "
            "SUM(ZCONTACTJID LIKE '%@g.us') FROM ZWACHATSESSION "
            "GROUP BY 1 ORDER BY 1")}
        # message type -> (with text, with media): which unknown types matter.
        shape = {t: (x, m) for t, x, m in conn.execute(
            "SELECT ZMESSAGETYPE, SUM(COALESCE(ZTEXT, '') != ''), "
            "SUM(ZMEDIAITEM IS NOT NULL) FROM ZWAMESSAGE GROUP BY 1 ORDER BY 1")}
        # session type -> {JID domain: chats}: the part after "@", no number.
        for t, jid in conn.execute("SELECT ZSESSIONTYPE, ZCONTACTJID "
                                   "FROM ZWACHATSESSION ORDER BY 1"):
            dom = (jid or "").rpartition("@")[2] or "(none)"
            d = domains.setdefault(t, {})
            d[dom] = d.get(dom, 0) + 1
        # message type -> {file extension: count}: says what unknown types hold.
        for t, rel in conn.execute(
                "SELECT m.ZMESSAGETYPE, i.ZMEDIALOCALPATH FROM ZWAMESSAGE m "
                "JOIN ZWAMEDIAITEM i ON i.Z_PK = m.ZMEDIAITEM ORDER BY 1"):
            ext = (Path(rel).suffix.lower() or "(none)") if rel else "(no file)"
            e = exts.setdefault(t, {})
            e[ext] = e.get(ext, 0) + 1
    # media column -> how many rows fill it: where a path might live instead.
    cols = [r[1] for r in conn.execute("PRAGMA table_info(ZWAMEDIAITEM)")]
    media_cols = {c: conn.execute(
        f'SELECT COUNT(*) FROM ZWAMEDIAITEM WHERE "{c}" IS NOT NULL '
        f"AND \"{c}\" != ''").fetchone()[0] for c in cols}
    return {"missing": missing, "tables": tables, "counts": counts,
            "message_types": types, "session_types": sessions,
            "type_shape": shape, "session_domains": domains,
            "media_ext": exts, "media_columns": media_cols}


def local_paths_found(conn: sqlite3.Connection, media_root: Path) -> tuple[int, int]:
    """(found, filled): of the media items with ZMEDIALOCALPATH, how many
    resolve to a file the way read() resolves them."""
    rows = [r[0] for r in conn.execute(
        "SELECT ZMEDIALOCALPATH FROM ZWAMEDIAITEM "
        "WHERE ZMEDIALOCALPATH IS NOT NULL AND ZMEDIALOCALPATH != ''")]
    found = sum(1 for rel in rows if (Path(media_root) / rel).is_file())
    return found, len(rows)


def media_files(root: Path) -> dict[str, int]:
    """File extension -> count under the media folder; no names."""
    out: dict[str, int] = {}
    for dirpath, _, names in os.walk(root):
        for n in names:
            ext = Path(n).suffix.lower() or "(none)"
            out[ext] = out.get(ext, 0) + 1
    return out
