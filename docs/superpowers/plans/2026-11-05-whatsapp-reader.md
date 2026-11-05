# WhatsApp Reader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Read every WhatsApp chat from the Mac app's local database, file one transcript per chat per day, check new messages hourly for anything urgent, and transcribe voice notes locally. WhatsApp is never written to and nothing is ever sent.

**Architecture:**
- `core/adapters/whatsapp.py` copies `ChatStorage.sqlite`, checks the copy, and turns rows into dataclasses.
- `core/adapters/asr.py` turns a voice note into text with `ffmpeg` and `mlx-whisper`.
- `core/pipeline/whatsapp.py` keeps per-message state in a new `whatsapp_messages` table and runs the hourly urgency check and the day closing (through the existing `ingest_file`). Its `run_pass` is the whole pass.
- `ops/whatsapp.py` wires a pass for launchd and adds `--probe` and `--dry-run`; `ops/backfill_whatsapp.py` files old days.

**Tech Stack:**
- Python 3 and `sqlite3`, both standard library.
- `ffmpeg` (Homebrew) and `mlx-whisper`: Mac only, imported lazily.
- pytest.

**Spec:** [docs/superpowers/specs/2026-11-05-whatsapp-reader-design.md](../specs/2026-11-05-whatsapp-reader-design.md)

## Global Constraints

- Read-only by construction. SQLite never opens a file inside `~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/`, and no file there is moved, renamed or written. Every file handed to `ingest_file` is a copy in the pass's temp directory, because `filing.file_artifact` moves what it is given (`core/pipeline/filing.py:326`).
- Nothing sends anything (`never_send_without_approval`). No network at run time: speech runs with `HF_HUB_OFFLINE=1`.
- Times: `TZ = ZoneInfo("America/New_York")`. A day closes at the first pass at or after **00:30** Eastern on the next day.
- Voice notes over **600 seconds** are not transcribed.
- `snapshot` retries a copy that fails `PRAGMA quick_check` up to **3** times.
- Action strings are written as literals in double quotes: `"WHATSAPP_CHECKED"` and `"WHATSAPP_FAILED"`. `tests/test_stalled_jobs.py` greps for them.
- Brief watch: `("whatsapp", 3)` hours.
- Coverage source id: `chat:whatsapp`.
- New `ops/*.py` scripts start with `#!/usr/bin/env python3`, call `ensure_venv(...)` then `load_lms_env()`, and are added to git with the executable bit: `git add --chmod=+x`.
- Plists: no `--` inside XML comments, no `LMS_*` variables, an absolute interpreter, and a label equal to the filename (`tests/test_plists.py`).
- zsh on the Mac: never put a `# comment` after a command in docs.
- **Commits:** the user commits only on request. Each task ends with the commit to make; run it only when the user has asked for commits on this work.
- Test command on the developer machine, run from `lms/`. `$PY` is `C:/Users/murta/AppData/Local/Temp/claude/C--Users-murta-Documents-source-codes-openclaw-mathew/8a3c1626-c8ef-4ae5-8761-7c8430ab64c0/scratchpad/venv/Scripts/python.exe`:
  `$PY -m pytest -o addopts="" -p no:cacheprovider --ignore=tests/test_halt.py -q`

## File map

| File | Status | Responsibility |
|---|---|---|
| `lms/tests/wa_fixture.py` | new | Builds a fake `ChatStorage.sqlite` with the expected tables |
| `lms/core/adapters/whatsapp.py` | new | Snapshot, schema check, read, probe |
| `lms/ops/whatsapp.py` | new | The hourly job, `--probe`, `--dry-run` |
| `lms/core/adapters/asr.py` | new | `transcribe(path, tmp_dir) -> str` |
| `lms/core/db/schema.sql` | modify | `whatsapp_messages` table |
| `lms/core/pipeline/notify.py` | modify | `for_document(key=...)` |
| `lms/core/pipeline/ingest.py` | modify | `ingest_file(alert_key=...)` |
| `lms/core/pipeline/whatsapp.py` | new | Formatting, record, scan, close, `run_pass`, `plan_days` |
| `lms/core/reports/brief.py` | modify | `WATCHED_JOBS`, `JOB_FAILURES` |
| `lms/ops/backfill_whatsapp.py` | new | Files days before yesterday in batches |
| `lms/ops/com.lms.whatsapp.plist` | new | launchd job, hourly |
| `lms/ops/WHATSAPP_APPLY.md` | new | Mac setup |
| `lms/config/sources.yaml` | modify | `chat:whatsapp` entry |
| `DECISIONS.md`, `REQUIREMENTS_REMAINING.md` | modify | D-079; R40 |
| tests | new | `test_whatsapp_adapter.py`, `test_asr.py`, `test_whatsapp.py` |

---

### Task 1: Fixture database and the reader adapter

**Files:**
- Create: `lms/tests/wa_fixture.py`
- Create: `lms/core/adapters/whatsapp.py`
- Test: `lms/tests/test_whatsapp_adapter.py`

**Interfaces:**
- Produces:
  - `whatsapp.CONTAINER: Path`, `DB_NAME = "ChatStorage.sqlite"`, `MEDIA_DIR = "Message"`, `STATUS_JID = "status@broadcast"`, `GROUP_SESSION = 1`, `COPY_ATTEMPTS = 3`
  - `REQUIRED: dict[str, set[str]]`, `KINDS: dict[int, str]`
  - `class WhatsAppError(RuntimeError)`
  - `@dataclass(frozen=True) Media(local_path: Path | None, title: str | None, size: int | None, duration_s: float | None)`
  - `@dataclass(frozen=True) Message(stanza_id: str, sent_at: datetime, from_me: bool, sender_name: str, text: str | None, kind: str, media: Media | None)`
  - `@dataclass Chat(jid: str, name: str, is_group: bool, messages: list[Message])`
  - `apple_time(seconds: float) -> datetime`
  - `number(jid: str) -> str`
  - `snapshot(src_dir: Path, tmp_dir: Path, attempts: int = COPY_ATTEMPTS) -> Path`
  - `check_schema(conn) -> list[str]`
  - `read(conn, since: datetime, media_root: Path) -> list[Chat]`
  - `probe(conn) -> dict`
  - From the fixture: `wa_fixture.WA(container: Path)` with `.chat(jid, name, group=False) -> int`, `.member(name, jid) -> int`, `.media(rel=None, title=None, size=None, duration=None, content=b"x") -> int`, `.message(chat, when, text=None, *, kind=0, from_me=False, member=None, media=None, stanza=None) -> str`, `.close()`

- [ ] **Step 1: Write the fixture builder**

`lms/tests/wa_fixture.py`:

```python
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
```

- [ ] **Step 2: Write the failing adapter tests**

`lms/tests/test_whatsapp_adapter.py`:

```python
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
```

- [ ] **Step 3: Run them and confirm they fail**

Run: `$PY -m pytest -o addopts="" -p no:cacheprovider tests/test_whatsapp_adapter.py -q`
Expected: FAIL. `ModuleNotFoundError: No module named 'core.adapters.whatsapp'`.

- [ ] **Step 4: Write the adapter**

`lms/core/adapters/whatsapp.py`:

```python
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
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `$PY -m pytest -o addopts="" -p no:cacheprovider tests/test_whatsapp_adapter.py -q`
Expected: PASS (10 tests).

- [ ] **Step 6: Commit**

```bash
git add lms/tests/wa_fixture.py lms/core/adapters/whatsapp.py lms/tests/test_whatsapp_adapter.py
git commit -m "R40: read the WhatsApp Mac app's database from a checked copy"
```

---

### Task 2: `ops/whatsapp.py --probe`, then the Mac gate

**Files:**
- Create: `lms/ops/whatsapp.py` (probe only in this task; Task 7 adds the pass)
- Test: `lms/tests/test_whatsapp_adapter.py` (append)

**Interfaces:**
- Consumes: `whatsapp.snapshot`, `whatsapp.probe`, `whatsapp.CONTAINER`, `whatsapp.MEDIA_DIR` (Task 1)
- Produces: `ops/whatsapp.py` with `do_probe(src_dir: Path) -> int`, which prints and returns the exit status

- [ ] **Step 1: Write the failing test**

Append to `lms/tests/test_whatsapp_adapter.py`:

```python
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
    box.conn.execute("DROP TABLE ZWAGROUPMEMBER")
    assert job.do_probe(box.container) == 1
    assert "MISSING: ZWAGROUPMEMBER" in capsys.readouterr().out
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `$PY -m pytest -o addopts="" -p no:cacheprovider tests/test_whatsapp_adapter.py -q -k probe_command`
Expected: FAIL. `ModuleNotFoundError: No module named 'whatsapp'`.

- [ ] **Step 3: Write the probe command**

`lms/ops/whatsapp.py`:

```python
#!/usr/bin/env python3
"""Read WhatsApp, hourly (R40, D-079).

    ./ops/whatsapp.py --probe      # the database's shape, no content; writes nothing

Reads the WhatsApp Mac app's database from a copy; never writes to WhatsApp,
never sends. See core/adapters/whatsapp.py and ops/WHATSAPP_APPLY.md.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _env import load_lms_env                              # noqa: E402
from _reexec import ensure_venv                            # noqa: E402
from core.adapters import whatsapp as wa                   # noqa: E402


def do_probe(src_dir: Path) -> int:
    tmp = Path(tempfile.mkdtemp(prefix="wa-probe-"))
    try:
        copy = wa.snapshot(src_dir, tmp)
        conn = sqlite3.connect(copy)
        try:
            p = wa.probe(conn)
        finally:
            conn.close()
        media = Path(src_dir) / wa.MEDIA_DIR
        print(f"database: {src_dir / wa.DB_NAME} (copied, checked)")
        print("tables: " + ", ".join(p["tables"]))
        for table, n in p["counts"].items():
            print(f"{table}: {n}")
        print("message types: " + ", ".join(
            f"{t}={n} ({wa.KINDS.get(t, 'other')})"
            for t, n in p["message_types"].items()))
        print(f"media folder readable: {media.is_dir()}")
        for m in p["missing"]:
            print(f"MISSING: {m}")
        return 1 if p["missing"] else 0
    except (wa.WhatsAppError, OSError) as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    ensure_venv("LMS_WHATSAPP_REEXEC", script=__file__)
    load_lms_env()
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--probe", action="store_true")
    args = p.parse_args()
    if args.probe:
        return do_probe(wa.CONTAINER)
    p.error("only --probe exists so far")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `$PY -m pytest -o addopts="" -p no:cacheprovider tests/test_whatsapp_adapter.py tests/test_ops_scripts.py -q`
Expected: PASS. `test_ops_scripts` passes only once the file is in the index as executable, so do Step 5 first if it fails on the mode.

- [ ] **Step 5: Commit with the executable bit**

```bash
git add --chmod=+x lms/ops/whatsapp.py
git add lms/tests/test_whatsapp_adapter.py
git commit -m "R40: ops/whatsapp.py --probe prints the database's shape"
```

- [ ] **Step 6: MAC GATE. Run the probe on the Mac before any further task.**

This step is for the user, over RustDesk. The prerequisites are `WHATSAPP_APPLY.md` steps 1–3 (written in Task 8; give them inline now):
1. Install WhatsApp from the Mac App Store.
2. Matthew links it with the QR code.
3. Grant Full Disk Access to `/Users/mleca/lms-repo/lms/.venv/bin/python`.

```bash
cd ~/lms-repo/lms && git pull
```
```bash
./ops/whatsapp.py --probe
```

Expected: `ZWAMESSAGE: <n>` with n > 0, no `MISSING:` lines, and `media folder readable: True`.

- **`FAIL` with a permission error:** Full Disk Access is missing or not applied. Stop and report.
- **`MISSING:` lines:** paste the output. Change `REQUIRED`, `_SQL` and `tests/wa_fixture.py` to the real names before Task 4.
- **Message types other than 0/1/2/3/8/15** with large counts: paste them. Extend `KINDS`, and the matching test, to the real values.
- **`file is not a database` after 3 tries:** the database is encrypted on this version. **Stop the plan.** Record it in D-079 and fall back to chat export (spec, "Decisions").

---

### Task 3: Speech adapter

**Files:**
- Create: `lms/core/adapters/asr.py`
- Test: `lms/tests/test_asr.py`

**Interfaces:**
- Produces:
  - `asr.MAX_SECONDS = 600`
  - `class ASRUnavailable(RuntimeError)`
  - `model_dir() -> Path`
  - `transcribe(src: Path, tmp_dir: Path) -> str`

- [ ] **Step 1: Write the failing tests**

`lms/tests/test_asr.py`:

```python
"""Local speech-to-text for voice notes (D-079). ffmpeg and mlx-whisper are
faked here; the real pair is exercised once on the Mac (WHATSAPP_APPLY.md)."""

import sys
import types

import pytest

from core.adapters import asr


def test_no_ffmpeg_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(asr.shutil, "which", lambda name: None)
    model = tmp_path / "model"
    model.mkdir()
    monkeypatch.setenv("LMS_ASR_MODEL", str(model))
    with pytest.raises(asr.ASRUnavailable, match="ffmpeg"):
        asr.transcribe(tmp_path / "a.opus", tmp_path)


def test_no_model_directory_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("LMS_ASR_MODEL", str(tmp_path / "absent"))
    with pytest.raises(asr.ASRUnavailable, match="model"):
        asr.transcribe(tmp_path / "a.opus", tmp_path)


def test_transcribes_offline_from_the_local_model(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")      # restored after the test
    model = tmp_path / "model"
    model.mkdir()
    monkeypatch.setenv("LMS_ASR_MODEL", str(model))
    monkeypatch.setattr(asr.shutil, "which", lambda name: "/opt/homebrew/bin/ffmpeg")
    ran = []
    monkeypatch.setattr(asr.subprocess, "run",
                        lambda argv, **kw: ran.append(argv))
    seen = {}

    def fake_transcribe(path, path_or_hf_repo):
        import os
        seen.update(path=path, repo=path_or_hf_repo,
                    offline=os.environ.get("HF_HUB_OFFLINE"))
        return {"text": "  Hi Matthew,\n the invoice is due Friday. "}
    monkeypatch.setitem(sys.modules, "mlx_whisper",
                        types.SimpleNamespace(transcribe=fake_transcribe))
    text = asr.transcribe(tmp_path / "a.opus", tmp_path)
    assert text == "Hi Matthew, the invoice is due Friday."
    assert ran[0][:2] == ["/opt/homebrew/bin/ffmpeg", "-nostdin"]
    assert "16000" in ran[0]
    assert seen["repo"] == str(model) and seen["offline"] == "1"
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `$PY -m pytest -o addopts="" -p no:cacheprovider tests/test_asr.py -q`
Expected: FAIL. `ModuleNotFoundError: No module named 'core.adapters.asr'`.

- [ ] **Step 3: Write the adapter**

`lms/core/adapters/asr.py`:

```python
"""Voice note to text, on this machine (D-079; TIER-A).

LM Studio serves chat and embeddings, not speech, so speech runs in-process:
ffmpeg decodes WhatsApp's .opus to 16 kHz mono WAV, and mlx-whisper runs
Whisper large-v3-turbo from a local directory with HF_HUB_OFFLINE=1, so
nothing is fetched at run time and no audio leaves the Mac (D-004).

Missing ffmpeg, mlx-whisper or the model raise ASRUnavailable: the caller
marks the note "not transcribed" and the pass is PARTIAL, not FAILED.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

MAX_SECONDS = 600


class ASRUnavailable(RuntimeError):
    pass


def model_dir() -> Path:
    return Path(os.environ.get(
        "LMS_ASR_MODEL", "~/LMS/models/whisper-large-v3-turbo")).expanduser()


def transcribe(src: Path, tmp_dir: Path) -> str:
    model = model_dir()
    if not model.is_dir():
        raise ASRUnavailable(f"speech model not found at {model}")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ASRUnavailable("ffmpeg not installed (brew install ffmpeg)")
    try:
        import mlx_whisper
    except ImportError as exc:
        raise ASRUnavailable("mlx-whisper not installed in the venv") from exc
    wav = Path(tmp_dir) / (Path(src).stem + ".wav")
    subprocess.run([ffmpeg, "-nostdin", "-loglevel", "error", "-y", "-i",
                    str(src), "-ar", "16000", "-ac", "1", str(wav)],
                   check=True, timeout=300)
    os.environ["HF_HUB_OFFLINE"] = "1"
    out = mlx_whisper.transcribe(str(wav), path_or_hf_repo=str(model))
    return " ".join(str(out.get("text", "")).split())
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `$PY -m pytest -o addopts="" -p no:cacheprovider tests/test_asr.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add lms/core/adapters/asr.py lms/tests/test_asr.py
git commit -m "D-079: local speech-to-text for voice notes (ffmpeg + mlx-whisper, offline)"
```

---

### Task 4: State table, and one alert per chat day

**Files:**
- Modify: `lms/core/db/schema.sql` (append at the end)
- Modify: `lms/core/pipeline/notify.py` (`for_document`, lines 136–151)
- Modify: `lms/core/pipeline/ingest.py` (`ingest_file` signature at line 206; `_alert_document` calls at lines 325 and 363; `_alert_document` at line 403)
- Test: `lms/tests/test_notify.py` (append)

**Interfaces:**
- Produces:
  - Table `whatsapp_messages(stanza_id PK, chat_jid, day, scanned_at, artifact_id, voice_text)`
  - `notify.for_document(..., key: str | None = None)`
  - `ingest_file(..., alert_key: str | None = None)`

- [ ] **Step 1: Write the failing tests**

Append to `lms/tests/test_notify.py`:

```python
def test_a_given_key_makes_two_documents_one_alert(conn):
    a, b = artifact(conn, 31), artifact(conn, 32)
    kw = dict(parent_id=None, received_date="2026-11-04", urgency="CRITICAL",
              category="VENDORS", counterparty="acme", descriptor="final notice",
              due_date=None, amount_cents=None, today=TODAY,
              key="whatsapp:15550100@s.whatsapp.net:2026-11-04")
    assert notify.for_document(conn, artifact_id=a, **kw)
    assert not notify.for_document(conn, artifact_id=b, **kw)


def test_ingest_passes_its_alert_key_through(conn, pipeline, tmp_path):
    roots, registry, clf = pipeline
    today = datetime.now(ET).date().isoformat()
    notify.enqueue(conn, key="whatsapp:x:today", reason="CRITICAL", text="from the hourly check")
    src = tmp_path / "lapse.pdf"
    src.write_bytes(b"BiBerk policy will lapse again")
    ingest_file(conn, roots, registry, clf, src, source="whatsapp",
                artifact=Artifact(recipient="matthew@mrecai.com",
                                  body="policy will lapse", received_date=today),
                run_ocr=False, alert_key="whatsapp:x:today")
    assert len(notify.pending(conn)) == 1


def test_the_whatsapp_table_exists(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(whatsapp_messages)")}
    assert cols == {"stanza_id", "chat_jid", "day", "scanned_at",
                    "artifact_id", "voice_text"}
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `$PY -m pytest -o addopts="" -p no:cacheprovider tests/test_notify.py -q`
Expected: 3 FAIL:
- `TypeError: for_document() got an unexpected keyword argument 'key'`
- `TypeError: ingest_file() got an unexpected keyword argument 'alert_key'`
- an empty column set

- [ ] **Step 3: Add the table**

Append to `lms/core/db/schema.sql`:

```sql

-- ---------------------------------------------------------------------------
-- whatsapp_messages — what the WhatsApp reader has seen (R40, D-079).
-- Keyed on WhatsApp's own message id (ZSTANZAID), which survives a re-link of
-- the Mac app; its row numbers do not. No message text is kept here: the text
-- lives in the filed transcript. voice_text caches a transcription so a voice
-- note is transcribed once, by the hourly check, and reused at filing.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS whatsapp_messages (
    stanza_id       TEXT    PRIMARY KEY,
    chat_jid        TEXT    NOT NULL,
    day             TEXT    NOT NULL,          -- YYYY-MM-DD, America/New_York
    scanned_at      TEXT,                      -- hourly urgency check done
    artifact_id     INTEGER REFERENCES artifacts(id),   -- transcript it is filed in
    voice_text      TEXT
);

CREATE INDEX IF NOT EXISTS idx_whatsapp_unfiled
    ON whatsapp_messages(chat_jid, day) WHERE artifact_id IS NULL;
```

- [ ] **Step 4: Add `key` to `notify.for_document`**

In `lms/core/pipeline/notify.py`, replace the signature and the `enqueue` call of `for_document`:

```python
def for_document(conn: sqlite3.Connection, *, artifact_id: int,
                 parent_id: int | None, received_date: str | None,
                 urgency: str | None, category: str, counterparty: str | None,
                 descriptor: str | None, due_date: str | None,
                 amount_cents: int | None, today: date | None = None,
                 key: str | None = None) -> bool:
    """Queue a classified document if it interrupts and is news.

    Keyed on the EMAIL, so a notice and its covering note are one alert. A
    caller with a better key passes it: WhatsApp keys on the chat day, so the
    hourly check's alert and the day's filing are one alert (D-079)."""
    reason = reason_for(urgency, category)
    if reason is None or not is_fresh(received_date, today or datetime.now(TZ).date()):
        return False
    return enqueue(conn, key=key or f"artifact:{parent_id or artifact_id}",
                   reason=reason, artifact_id=artifact_id,
                   text=describe(category=category, counterparty=counterparty,
                                 descriptor=descriptor, due_date=due_date,
                                 amount_cents=amount_cents))
```

- [ ] **Step 5: Add `alert_key` to `ingest_file`**

In `lms/core/pipeline/ingest.py`:

Signature (line 206), add the last parameter:
```python
def ingest_file(conn, roots: filing.StorageRoots, registry: Registry,
                classifier: Classifier, source_path: Path, *,
                source: str = "photo", source_ref: str | None = None,
                artifact: Artifact | None = None,
                auth_results: dict[str, bool] | None = None,
                parent_id: int | None = None,
                run_ocr: bool = True,
                alert_key: str | None = None) -> IngestResult:
```

Both calls (lines 325 and 363) gain `key=alert_key`:
```python
        _alert_document(conn, classification, art,
                        int(db.find_artifact_by_hash(conn, sha)["id"]), parent_id,
                        key=alert_key)
```
```python
    _alert_document(conn, classification, art, filed.artifact_id, parent_id,
                    key=alert_key)
```

`_alert_document` (line 403):
```python
def _alert_document(conn, c: Classification, art: Artifact, artifact_id: int,
                    parent_id: int | None, key: str | None = None) -> None:
    _alert(conn, notify.for_document, artifact_id=artifact_id,
           parent_id=parent_id, received_date=art.received_date,
           urgency=c.urgency, category=c.category,
           counterparty=c.counterparty, descriptor=c.descriptor,
           due_date=c.due_date, amount_cents=c.amount_cents, key=key)
```

- [ ] **Step 6: Run the full suite and confirm it passes**

Run: the Global Constraints test command.
Expected: all pass. The 3 new tests pass, and mail behaviour is unchanged because `key` defaults to `None`.

- [ ] **Step 7: Commit**

```bash
git add lms/core/db/schema.sql lms/core/pipeline/notify.py lms/core/pipeline/ingest.py lms/tests/test_notify.py
git commit -m "D-079: whatsapp_messages table; ingest and notify take an alert key"
```

---

### Task 5: Chat-day formatting and the hourly urgency check

**Files:**
- Create: `lms/core/pipeline/whatsapp.py`
- Test: `lms/tests/test_whatsapp.py`

**Interfaces:**
- Consumes:
  - `whatsapp.Chat`, `Message`, `Media`, `number` (Task 1)
  - `asr.ASRUnavailable`, `asr.MAX_SECONDS` (Task 3)
  - `notify.reason_for`, `notify.describe`, `notify.enqueue`
  - the `whatsapp_messages` table (Task 4)
- Produces:
  - `TZ`, `SOURCE = "whatsapp"`, `SOURCE_ID = "chat:whatsapp"`, `RECIPIENT = "Matthew (WhatsApp)"`, `CHECKED = "WHATSAPP_CHECKED"`, `FAILED = "WHATSAPP_FAILED"`
  - `day_of(dt: datetime) -> str`
  - `closed(day: str, now: datetime) -> bool`
  - `chat_label(chat) -> str`
  - `line(msg, voice: str | None) -> str`
  - `header(chat, day: str, part: int) -> str`
  - `transcript(chat, day, part, msgs, voices: dict[str, str | None]) -> str`
  - `alert_key(chat, day) -> str`
  - `@dataclass PassResult(alerts=0, filed=0, failed=0, voice_missing=0, media_missing=0, messages=0, error: str | None = None)` with `.result -> str` and `.summary() -> str`
  - `record(conn, chats) -> None`
  - `voice_text(conn, msg, tmp_dir, transcriber, result) -> str | None`
  - `scan(conn, chats, classifier, now, tmp_dir, transcriber, result) -> None`
  - Type `Transcriber = Callable[[Path, Path], str]`

- [ ] **Step 1: Write the failing tests**

`lms/tests/test_whatsapp.py`:

```python
"""WhatsApp chat days: formatting, the hourly check, closing, the pass (D-079)."""

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from core.adapters import asr
from core.adapters.whatsapp import Chat, Media, Message
from core.db import database as db
from core.pipeline import notify
from core.pipeline import whatsapp as w

ET = ZoneInfo("America/New_York")
UTC = timezone.utc


def at(day, hour, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=ET)


def msg(stanza, when, text=None, *, kind="text", sender="Acme Supply",
        from_me=False, media=None):
    return Message(stanza, when, from_me, "Matthew" if from_me else sender,
                   text, kind, media)


def direct(*messages):
    return Chat("15550100@s.whatsapp.net", "Acme Supply", False, list(messages))


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "lms.db")
    yield c
    c.close()


class StubClassifier:
    def __init__(self, urgency="NORMAL", category="VENDORS"):
        self.urgency, self.category, self.seen = urgency, category, []

    def classify(self, art):
        self.seen.append(art)
        return SimpleNamespace(urgency=self.urgency, category=self.category,
                               counterparty="acme-supply", descriptor="final notice",
                               due_date=None, amount_cents=None)


def no_speech(path, tmp):
    raise asr.ASRUnavailable("not in tests")


# --- formatting ------------------------------------------------------------

def test_days_are_eastern_including_across_the_dst_change():
    assert w.day_of(datetime(2026, 11, 2, 4, 30, tzinfo=UTC)) == "2026-11-01"
    assert w.day_of(datetime(2026, 10, 31, 3, 30, tzinfo=UTC)) == "2026-10-30"


def test_a_day_closes_at_half_past_midnight():
    assert not w.closed("2026-11-04", at(5, 0, 29))
    assert w.closed("2026-11-04", at(5, 0, 30))
    assert not w.closed("2026-11-05", at(5, 23, 59))


def test_each_kind_has_its_line(tmp_path):
    f = tmp_path / "x.pdf"
    f.write_bytes(b"%PDF")
    t = at(5, 9, 14)
    doc = Media(f, "INV-2291.pdf", 4, None)
    lost = Media(None, "scan.pdf", None, None)
    v = Media(f, None, None, 42.0)
    long = Media(f, None, None, 730.0)
    assert w.line(msg("1", t, "Resend?", from_me=True), None) == "09:14 Matthew: Resend?"
    assert w.line(msg("2", t, kind="document", media=doc), None) == \
        "09:14 Acme Supply: [document: INV-2291.pdf]"
    assert w.line(msg("3", t, kind="document", media=lost), None) == \
        "09:14 Acme Supply: [document: scan.pdf, not downloaded on the Mac]"
    assert w.line(msg("4", t, kind="voice", media=v), "Hi Matthew") == \
        "09:14 Acme Supply: [voice 0:42] Hi Matthew"
    assert w.line(msg("5", t, kind="voice", media=v), None) == \
        "09:14 Acme Supply: [voice 0:42, not transcribed]"
    assert w.line(msg("6", t, kind="voice", media=long), None) == \
        "09:14 Acme Supply: [voice 12:10, not transcribed: over 10 minutes]"
    assert w.line(msg("7", t, kind="image", media=doc, text="the bill"), None) == \
        "09:14 Acme Supply: [photo] the bill"
    assert w.line(msg("8", t, kind="video", media=doc), None) == \
        "09:14 Acme Supply: [video, not filed]"
    assert w.line(msg("9", t, kind="sticker"), None) == "09:14 Acme Supply: [sticker]"


def test_the_header_says_whose_chat_and_which_part():
    assert w.header(direct(), "2026-11-05", 2).splitlines() == [
        "WhatsApp chat: Acme Supply (direct, +15550100)",
        "Date: 2026-11-05 (America/New_York)",
        "Part: 2", ""]
    group = Chat("1203630@g.us", "Board", True, [])
    assert w.header(group, "2026-11-05", 1).splitlines()[0] == \
        "WhatsApp chat: Board (group)"


# --- the hourly check --------------------------------------------------------

def test_an_urgent_chat_day_alerts_once(conn, tmp_path):
    chat = direct(msg("A", at(5, 9), "Final notice: pay today"))
    w.record(conn, [chat])
    clf, result = StubClassifier("CRITICAL"), w.PassResult()
    w.scan(conn, [chat], clf, at(5, 10), tmp_path, no_speech, result)
    chat.messages.append(msg("B", at(5, 10, 30), "Still waiting"))
    w.record(conn, [chat])
    w.scan(conn, [chat], clf, at(5, 11), tmp_path, no_speech, result)
    [p] = notify.pending(conn)
    assert p.critical and "Final notice" in p.text
    assert result.alerts == 1
    assert [a.body for a in clf.seen] == ["09:00 Acme Supply: Final notice: pay today",
                                          "10:30 Acme Supply: Still waiting"]


def test_a_normal_chat_day_does_not_alert(conn, tmp_path):
    chat = direct(msg("A", at(5, 9), "Thanks!"))
    w.record(conn, [chat])
    w.scan(conn, [chat], StubClassifier("NORMAL"), at(5, 10), tmp_path, no_speech,
           w.PassResult())
    assert notify.pending(conn) == []


def test_old_days_are_not_scanned(conn, tmp_path):
    chat = direct(msg("A", at(2, 9), "pay now"))
    w.record(conn, [chat])
    clf = StubClassifier("CRITICAL")
    w.scan(conn, [chat], clf, at(5, 10), tmp_path, no_speech, w.PassResult())
    assert clf.seen == []


def test_a_voice_note_is_transcribed_once_and_cached(conn, tmp_path):
    f = tmp_path / "a.opus"
    f.write_bytes(b"opus")
    chat = direct(msg("V", at(5, 9), kind="voice", media=Media(f, None, None, 42.0)))
    w.record(conn, [chat])
    calls = []

    def speech(path, tmp):
        calls.append(path)
        return "the invoice is due Friday"
    result = w.PassResult()
    assert w.voice_text(conn, chat.messages[0], tmp_path, speech, result) == \
        "the invoice is due Friday"
    assert w.voice_text(conn, chat.messages[0], tmp_path, speech, result) == \
        "the invoice is due Friday"
    assert len(calls) == 1 and result.voice_missing == 0


def test_no_speech_model_counts_as_missing_not_failure(conn, tmp_path):
    f = tmp_path / "a.opus"
    f.write_bytes(b"opus")
    chat = direct(msg("V", at(5, 9), kind="voice", media=Media(f, None, None, 42.0)))
    w.record(conn, [chat])
    result = w.PassResult()
    assert w.voice_text(conn, chat.messages[0], tmp_path, no_speech, result) is None
    assert result.voice_missing == 1 and result.result == "PARTIAL"
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `$PY -m pytest -o addopts="" -p no:cacheprovider tests/test_whatsapp.py -q`
Expected: FAIL. `ImportError: cannot import name 'whatsapp' from 'core.pipeline'`.

- [ ] **Step 3: Write the formatting and scan half of the module**

`lms/core/pipeline/whatsapp.py`:

```python
"""WhatsApp chat days: the hourly check and the day's transcript (R40, D-079).

    read (adapters/whatsapp) ─> record ─> scan  (hourly: urgent? -> alert)
                                     └──> close (day over: transcript -> ingest)

A chat day is one chat's messages on one Eastern-time day. While the day is
open, each hourly pass sends the new lines to the classifier for urgency only
and queues an alert keyed on the chat day. Nothing is filed until the day
closes, at the first pass at or after 00:30 the next day; then the day's
messages become one transcript, filed through ingest_file like an email, with
the same alert key so a chat day already alerted is not alerted again.
Messages synced after their day was filed become a "part 2"; nothing filed is
ever rewritten.
"""

from __future__ import annotations

import re
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from ..adapters import asr
from ..adapters.whatsapp import Chat, Message, number
from ..db import database as db
from . import notify
from .classify import Artifact

TZ = ZoneInfo("America/New_York")
CLOSE_AT = time(0, 30)
SOURCE = "whatsapp"
SOURCE_ID = "chat:whatsapp"
RECIPIENT = "Matthew (WhatsApp)"
CHECKED = "WHATSAPP_CHECKED"
FAILED = "WHATSAPP_FAILED"
FILED_KINDS = frozenset({"document", "image"})

Transcriber = Callable[[Path, Path], str]


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def day_of(dt: datetime) -> str:
    return dt.astimezone(TZ).date().isoformat()


def closed(day: str, now: datetime) -> bool:
    nxt = date.fromisoformat(day) + timedelta(days=1)
    return now.astimezone(TZ) >= datetime.combine(nxt, CLOSE_AT, TZ)


def chat_label(chat: Chat) -> str:
    return chat.name if chat.is_group else f"{chat.name} ({number(chat.jid)})"


def alert_key(chat: Chat, day: str) -> str:
    return f"whatsapp:{chat.jid}:{day}"


def _dur(seconds: float | None) -> str:
    s = int(round(seconds or 0))
    return f"{s // 60}:{s % 60:02d}"


def line(m: Message, voice: str | None) -> str:
    missing = m.media is None or m.media.local_path is None
    if m.kind == "document":
        body = f"[document: {(m.media.title if m.media else None) or 'untitled'}"
        body += ", not downloaded on the Mac]" if missing else "]"
    elif m.kind == "image":
        body = "[photo, not downloaded on the Mac]" if missing else "[photo]"
    elif m.kind == "video":
        body = "[video, not filed]"
    elif m.kind == "sticker":
        body = "[sticker]"
    elif m.kind == "voice":
        d = m.media.duration_s if m.media else None
        if d and d > asr.MAX_SECONDS:
            body = f"[voice {_dur(d)}, not transcribed: over 10 minutes]"
        elif voice:
            body = f"[voice {_dur(d)}] {voice}"
        else:
            body = f"[voice {_dur(d)}, not transcribed]"
    else:
        body = m.text or "[other]"
    if m.kind in {"document", "image", "video"} and m.text:
        body += f" {m.text}"
    return f"{m.sent_at.astimezone(TZ):%H:%M} {m.sender_name}: {body}"


def header(chat: Chat, day: str, part: int) -> str:
    kind = "group" if chat.is_group else f"direct, {number(chat.jid)}"
    return (f"WhatsApp chat: {chat.name} ({kind})\n"
            f"Date: {day} (America/New_York)\nPart: {part}\n\n")


def transcript(chat: Chat, day: str, part: int, msgs: list[Message],
               voices: dict[str, str | None]) -> str:
    return header(chat, day, part) + "\n".join(
        line(m, voices.get(m.stanza_id)) for m in msgs) + "\n"


# ---------------------------------------------------------------------------
# One pass's tally
# ---------------------------------------------------------------------------

@dataclass
class PassResult:
    alerts: int = 0
    filed: int = 0
    failed: int = 0
    voice_missing: int = 0
    media_missing: int = 0
    messages: int = 0
    error: str | None = None

    @property
    def result(self) -> str:
        if self.error:
            return "FAILED"
        if self.failed or self.voice_missing or self.media_missing:
            return "PARTIAL"
        return "OK"

    def summary(self) -> str:
        if self.error:
            return self.error
        s = (f"{self.messages} message(s) seen, {self.filed} chat day(s) filed, "
             f"{self.alerts} alert(s)")
        if self.failed:
            s += f", {self.failed} FAILED (see WHATSAPP_DAY_FAILED)"
        if self.voice_missing:
            s += f", {self.voice_missing} voice note(s) not transcribed"
        if self.media_missing:
            s += f", {self.media_missing} file(s) not downloaded on the Mac"
        return s


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def record(conn: sqlite3.Connection, chats: list[Chat]) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO whatsapp_messages (stanza_id, chat_jid, day) "
        "VALUES (?, ?, ?)",
        [(m.stanza_id, c.jid, day_of(m.sent_at)) for c in chats for m in c.messages])
    conn.commit()


def _row(conn, stanza_id: str) -> sqlite3.Row:
    return conn.execute("SELECT * FROM whatsapp_messages WHERE stanza_id = ?",
                        (stanza_id,)).fetchone()


def voice_text(conn: sqlite3.Connection, m: Message, tmp_dir: Path,
               transcriber: Transcriber, result: PassResult) -> str | None:
    """The cached transcription, or a new one. None if it cannot be had."""
    cached = _row(conn, m.stanza_id)["voice_text"]
    if cached:
        return cached
    if m.media is None or m.media.local_path is None:
        result.voice_missing += 1
        return None
    if (m.media.duration_s or 0) > asr.MAX_SECONDS:
        return None
    copy = Path(tmp_dir) / "voice" / m.stanza_id / m.media.local_path.name
    copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(m.media.local_path, copy)
    try:
        text = transcriber(copy, copy.parent)
    except asr.ASRUnavailable:
        result.voice_missing += 1
        return None
    conn.execute("UPDATE whatsapp_messages SET voice_text = ? WHERE stanza_id = ?",
                 (text, m.stanza_id))
    conn.commit()
    return text


def _by_day(msgs: list[Message]) -> dict[str, list[Message]]:
    out: dict[str, list[Message]] = {}
    for m in msgs:
        out.setdefault(day_of(m.sent_at), []).append(m)
    return out


def _artifact(chat: Chat, day: str, body: str) -> Artifact:
    return Artifact(source=SOURCE, subject=chat.name, sender=chat_label(chat),
                    recipient=RECIPIENT, body=body, received_date=day)


# ---------------------------------------------------------------------------
# The hourly check
# ---------------------------------------------------------------------------

def scan(conn: sqlite3.Connection, chats: list[Chat], classifier, now: datetime,
         tmp_dir: Path, transcriber: Transcriber, result: PassResult) -> None:
    """Today's and yesterday's unscanned messages, per chat day, to the
    classifier for urgency only. Files nothing."""
    recent = {day_of(now), day_of(now - timedelta(days=1))}
    for chat in chats:
        new = [m for m in chat.messages if day_of(m.sent_at) in recent
               and _row(conn, m.stanza_id)["scanned_at"] is None]
        for day, msgs in _by_day(new).items():
            voices = {m.stanza_id: voice_text(conn, m, tmp_dir, transcriber, result)
                      for m in msgs if m.kind == "voice"}
            body = "\n".join(line(m, voices.get(m.stanza_id)) for m in msgs)
            c = classifier.classify(_artifact(chat, day, body))
            reason = notify.reason_for(c.urgency, c.category)
            if reason and notify.enqueue(
                    conn, key=alert_key(chat, day), reason=reason,
                    text=notify.describe(category=c.category,
                                         counterparty=c.counterparty,
                                         descriptor=c.descriptor,
                                         due_date=c.due_date,
                                         amount_cents=c.amount_cents)):
                result.alerts += 1
            conn.executemany(
                "UPDATE whatsapp_messages SET scanned_at = ? WHERE stanza_id = ?",
                [(db.now_iso(), m.stanza_id) for m in msgs])
            conn.commit()


def _safe(name: str) -> str:
    return re.sub(r"[^\w\- ]+", "", name).strip()[:60] or "chat"
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `$PY -m pytest -o addopts="" -p no:cacheprovider tests/test_whatsapp.py -q`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add lms/core/pipeline/whatsapp.py lms/tests/test_whatsapp.py
git commit -m "R40: WhatsApp chat-day transcripts and the hourly urgency check"
```

---

### Task 6: Closing a day: transcript, media, part 2

**Files:**
- Modify: `lms/core/pipeline/whatsapp.py` (append)
- Test: `lms/tests/test_whatsapp.py` (append)

**Interfaces:**
- Consumes:
  - `ingest_file(conn, roots, registry, classifier, path, *, source, source_ref, artifact, parent_id, run_ocr, alert_key) -> IngestResult`
  - `db.find_artifact_by_hash(conn, sha) -> Row`
- Produces: `close(conn, roots, registry, classifier, chats, now, tmp_dir, transcriber, result, *, before: str | None = None, limit: int | None = None, ocr: bool = True, ingest=ingest_file) -> int`, which returns the number of chat days filed.
  - `before`: only days strictly before this ISO date.
  - `limit`: stop after this many chat days.
  - `ocr`: passed to the media ingests as `run_ocr`. Tests pass `False`, because the Windows machine has no Vision OCR.

- [ ] **Step 1: Write the failing tests**

Append to `lms/tests/test_whatsapp.py`:

```python
from core.adapters.lmstudio import Completion
from core.pipeline import filing
from core.pipeline.classify import Classifier
from core.pipeline.registry import load_registry

CONFIG = Path(__file__).resolve().parents[1] / "config"


class FakeModel:
    def __init__(self, response):
        self._response = response

    def complete(self, **kw):
        return Completion(text=json.dumps(self._response), model="fake",
                          elapsed_s=0.01, finish_reason="stop")


def answer(urgency="NORMAL"):
    return {"domain": "BUSINESS", "entity_id": "B_MRE", "category": "VENDORS",
            "subcategory": "invoices-received", "urgency": urgency,
            "confidence": 0.93, "requires_reply": False, "due_date": None,
            "counterparty": "acme-supply", "descriptor": "invoice chat",
            "amount_cents": None, "rationale": "vendor"}


@pytest.fixture
def pipe(tmp_path):
    roots = filing.StorageRoots(archive=tmp_path / "archive",
                                originals=tmp_path / "_originals",
                                quarantine=tmp_path / "quarantine")
    roots.ensure()
    registry = load_registry(CONFIG)
    return roots, registry, Classifier(registry, client=FakeModel(answer()))


def filed_ids(conn, *stanzas):
    return [conn.execute("SELECT artifact_id FROM whatsapp_messages WHERE stanza_id=?",
                         (s,)).fetchone()[0] for s in stanzas]


def test_a_closed_day_is_filed_as_one_transcript(conn, pipe, tmp_path):
    roots, registry, clf = pipe
    chat = direct(msg("A", at(4, 9), "Can you resend the invoice?", from_me=True),
                  msg("B", at(4, 9, 20), "Sent it"))
    w.record(conn, [chat])
    result = w.PassResult()
    assert w.close(conn, roots, registry, clf, [chat], at(5, 0, 29), tmp_path,
                   no_speech, result) == 0
    assert w.close(conn, roots, registry, clf, [chat], at(5, 0, 30), tmp_path,
                   no_speech, result) == 1
    a, b = filed_ids(conn, "A", "B")
    assert a == b and a is not None
    row = conn.execute("SELECT source, source_ref FROM artifacts WHERE id=?", (a,)).fetchone()
    assert row["source"] == "whatsapp"
    assert row["source_ref"] == "whatsapp:15550100@s.whatsapp.net:2026-11-04:1"


def test_a_late_message_is_filed_as_part_2(conn, pipe, tmp_path):
    roots, registry, clf = pipe
    chat = direct(msg("A", at(4, 9), "first"))
    w.record(conn, [chat])
    w.close(conn, roots, registry, clf, [chat], at(5, 1), tmp_path, no_speech, w.PassResult())
    chat.messages.append(msg("L", at(4, 22), "synced late"))
    w.record(conn, [chat])
    assert w.close(conn, roots, registry, clf, [chat], at(5, 2), tmp_path,
                   no_speech, w.PassResult()) == 1
    first, late = filed_ids(conn, "A", "L")
    assert first != late
    ref = conn.execute("SELECT source_ref FROM artifacts WHERE id=?", (late,)).fetchone()[0]
    assert ref.endswith(":2026-11-04:2")


def test_documents_and_photos_are_filed_under_the_transcript(conn, pipe, tmp_path):
    roots, registry, clf = pipe
    media = tmp_path / "container" / "INV.pdf"
    media.parent.mkdir()
    media.write_bytes(b"%PDF-1.4 invoice 2291")
    chat = direct(msg("D", at(4, 9), kind="document",
                      media=Media(media, "INV-2291.pdf", 20, None)),
                  msg("N", at(4, 9, 5), kind="document",
                      media=Media(None, "scan.pdf", None, None)),
                  msg("V", at(4, 9, 6), kind="video", media=Media(media, None, None, 5)))
    w.record(conn, [chat])
    result = w.PassResult()
    w.close(conn, roots, registry, clf, [chat], at(5, 1), tmp_path, no_speech, result,
            ocr=False)
    assert media.read_bytes() == b"%PDF-1.4 invoice 2291", "WhatsApp's file was moved"
    parent = filed_ids(conn, "D")[0]
    kids = conn.execute("SELECT source, source_ref FROM artifacts WHERE parent_id=?",
                        (parent,)).fetchall()
    assert [(k["source"], k["source_ref"]) for k in kids] == [("attachment", "whatsapp:D")]
    assert result.media_missing == 1 and result.result == "PARTIAL"


def test_backfill_limits_by_day_and_count(conn, pipe, tmp_path):
    roots, registry, clf = pipe
    chat = direct(msg("A", at(1, 9), "one"), msg("B", at(2, 9), "two"),
                  msg("C", at(3, 9), "three"), msg("D", at(4, 9), "four"))
    w.record(conn, [chat])
    n = w.close(conn, roots, registry, clf, [chat], at(5, 1), tmp_path, no_speech,
                w.PassResult(), before="2026-11-04", limit=2)
    assert n == 2
    assert [x is not None for x in filed_ids(conn, "A", "B", "C", "D")] == \
        [True, True, False, False]


def test_filing_does_not_repeat_the_hourly_alert(conn, tmp_path):
    roots = filing.StorageRoots(archive=tmp_path / "archive",
                                originals=tmp_path / "_originals",
                                quarantine=tmp_path / "quarantine")
    roots.ensure()
    registry = load_registry(CONFIG)
    clf = Classifier(registry, client=FakeModel(answer("CRITICAL")))
    now = datetime.now(ET)
    yesterday = now - timedelta(days=1)
    chat = direct(msg("A", yesterday.replace(hour=9), "Final notice, pay today"))
    w.record(conn, [chat])
    w.scan(conn, [chat], StubClassifier("CRITICAL"), yesterday.replace(hour=10),
           tmp_path, no_speech, w.PassResult())
    w.close(conn, roots, registry, clf, [chat], now.replace(hour=1), tmp_path,
            no_speech, w.PassResult())
    assert len(notify.pending(conn)) == 1


def test_one_bad_chat_day_does_not_stop_the_others(conn, pipe, tmp_path):
    roots, registry, clf = pipe
    good = direct(msg("G", at(4, 9), "fine"))
    bad = Chat("15550199@s.whatsapp.net", "Broken", False, [msg("X", at(4, 9), "boom")])
    w.record(conn, [good, bad])

    from core.pipeline.ingest import ingest_file

    def flaky(conn_, roots_, registry_, clf_, path, **kw):
        if "Broken" in path.name:
            raise RuntimeError("disk full")
        return ingest_file(conn_, roots_, registry_, clf_, path, **kw)
    result = w.PassResult()
    assert w.close(conn, roots, registry, clf, [bad, good], at(5, 1), tmp_path,
                   no_speech, result, ingest=flaky) == 1
    assert result.failed == 1
    assert filed_ids(conn, "X") == [None]
    assert conn.execute("SELECT COUNT(*) FROM actions_log WHERE action='WHATSAPP_DAY_FAILED'"
                        ).fetchone()[0] == 1
```

Add `from datetime import timedelta` to the test file's datetime import line: `from datetime import datetime, timedelta, timezone`.

- [ ] **Step 2: Run them and confirm they fail**

Run: `$PY -m pytest -o addopts="" -p no:cacheprovider tests/test_whatsapp.py -q`
Expected: the 6 new tests FAIL with `AttributeError: module 'core.pipeline.whatsapp' has no attribute 'close'`.

- [ ] **Step 3: Write `close`**

Add to the imports at the top of `lms/core/pipeline/whatsapp.py`:

```python
from .ingest import ingest_file
```

Append to `lms/core/pipeline/whatsapp.py`:

```python
# ---------------------------------------------------------------------------
# Closing a day
# ---------------------------------------------------------------------------

def _part(conn, jid: str, day: str) -> int:
    n = conn.execute(
        "SELECT COUNT(DISTINCT artifact_id) FROM whatsapp_messages "
        "WHERE chat_jid = ? AND day = ? AND artifact_id IS NOT NULL",
        (jid, day)).fetchone()[0]
    return n + 1


def _copy(src: Path, dest_dir: Path, name: str) -> Path:
    """A copy to hand to ingest: filing MOVES what it is given (filing.py)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / name
    shutil.copyfile(src, dest)
    return dest


def close(conn: sqlite3.Connection, roots, registry, classifier,
          chats: list[Chat], now: datetime, tmp_dir: Path,
          transcriber: Transcriber, result: PassResult, *,
          before: str | None = None, limit: int | None = None,
          ocr: bool = True, ingest=ingest_file) -> int:
    """File every closed chat day's unfiled messages. Returns chat days filed."""
    done = 0
    for chat in chats:
        unfiled = [m for m in chat.messages
                   if _row(conn, m.stanza_id)["artifact_id"] is None]
        for day, msgs in sorted(_by_day(unfiled).items()):
            if not closed(day, now) or (before and day >= before):
                continue
            if limit is not None and done >= limit:
                return done
            try:
                _file_day(conn, roots, registry, classifier, chat, day, msgs,
                          tmp_dir, transcriber, result, ingest, ocr)
            except Exception as exc:                          # noqa: BLE001
                result.failed += 1
                db.log_action(conn, "WHATSAPP_DAY_FAILED",
                              detail=f"{chat.jid} {day}: {type(exc).__name__}: {exc}"[:400])
                conn.commit()
                continue
            done += 1
            result.filed += 1
    return done


def _file_day(conn, roots, registry, classifier, chat: Chat, day: str,
              msgs: list[Message], tmp_dir: Path, transcriber: Transcriber,
              result: PassResult, ingest, ocr: bool) -> None:
    part = _part(conn, chat.jid, day)
    voices = {m.stanza_id: voice_text(conn, m, tmp_dir, transcriber, result)
              for m in msgs if m.kind == "voice"}
    text = transcript(chat, day, part, msgs, voices)
    work = Path(tmp_dir) / "days" / f"{_safe(chat.name)} {day} part {part}"
    work.mkdir(parents=True, exist_ok=True)
    path = work / f"WhatsApp {_safe(chat.name)} {day} part {part}.txt"
    path.write_text(text, encoding="utf-8")
    key = alert_key(chat, day)
    r = ingest(conn, roots, registry, classifier, path, source=SOURCE,
               source_ref=f"whatsapp:{chat.jid}:{day}:{part}",
               artifact=_artifact(chat, day, text), run_ocr=False, alert_key=key)
    parent = int(db.find_artifact_by_hash(conn, r.sha256)["id"])
    for m in msgs:
        if m.kind not in FILED_KINDS:
            continue
        if m.media is None or m.media.local_path is None:
            result.media_missing += 1
            continue
        name = m.media.title or m.media.local_path.name
        copy = _copy(m.media.local_path, work / "media" / m.stanza_id, name)
        try:
            ingest(conn, roots, registry, classifier, copy, source="attachment",
                   source_ref=f"whatsapp:{m.stanza_id}", parent_id=parent,
                   artifact=_artifact(chat, day, m.text or ""), run_ocr=ocr,
                   alert_key=key)
        except Exception as exc:                              # noqa: BLE001
            result.failed += 1
            db.log_action(conn, "WHATSAPP_DAY_FAILED",
                          detail=f"{chat.jid} {day} {m.stanza_id}: "
                                 f"{type(exc).__name__}: {exc}"[:400])
    conn.executemany(
        "UPDATE whatsapp_messages SET artifact_id = ?, "
        "scanned_at = COALESCE(scanned_at, ?) WHERE stanza_id = ?",
        [(parent, db.now_iso(), m.stanza_id) for m in msgs])
    conn.commit()
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `$PY -m pytest -o addopts="" -p no:cacheprovider tests/test_whatsapp.py -q`
Expected: PASS (15 tests). If `test_a_closed_day_is_filed_as_one_transcript` files to quarantine instead, it still passes: the assertions read `artifacts`, which quarantine also writes.

- [ ] **Step 5: Commit**

```bash
git add lms/core/pipeline/whatsapp.py lms/tests/test_whatsapp.py
git commit -m "R40: file each closed WhatsApp chat day, its documents, and late parts"
```

---

### Task 7: The pass, the brief's watch, the hourly job

**Files:**
- Modify: `lms/core/pipeline/whatsapp.py` (append `run_pass`, `plan_days`)
- Modify: `lms/core/reports/brief.py:439-465`
- Modify: `lms/ops/whatsapp.py` (the full job)
- Create: `lms/ops/com.lms.whatsapp.plist`
- Test: `lms/tests/test_whatsapp.py` (append)

**Interfaces:**
- Consumes: everything above; `coverage.record_check`; `_halt.read_flag`; `archive_volume_problem`
- Produces:
  - `run_pass(conn, roots, registry, classifier, *, src_dir: Path, tmp_root: Path, now: datetime, transcriber: Transcriber = asr.transcribe, since_days: int = 3, ingest=ingest_file) -> PassResult`
  - `plan_days(conn, chats, now) -> list[tuple[str, str, int, bool]]`, returning (chat label, day, unfiled messages, closed?) without writing
  - `read_chats(src_dir: Path, tmp: Path, since: datetime) -> list[Chat]`

- [ ] **Step 1: Write the failing tests**

Append to `lms/tests/test_whatsapp.py`:

```python
from core.reports import brief
from tests.wa_fixture import WA


def build_container(tmp_path, when):
    box = WA(tmp_path / "container")
    c = box.chat("15550100@s.whatsapp.net", "Acme Supply")
    box.message(c, when, "Can you resend the invoice?", from_me=True)
    box.message(c, when + timedelta(minutes=5), "Sent it")
    box.close()
    return box.container


def test_a_whole_pass_files_records_and_cleans_up(conn, pipe, tmp_path):
    roots, registry, clf = pipe
    now = datetime.now(ET)
    src = build_container(tmp_path, (now - timedelta(days=1)).replace(hour=9))
    r = w.run_pass(conn, roots, registry, clf, src_dir=src,
                   tmp_root=tmp_path / "tmp", now=now.replace(hour=1, minute=0),
                   transcriber=no_speech)
    assert r.error is None and r.filed == 1 and r.result == "OK"
    [check] = conn.execute("SELECT * FROM source_checks WHERE source_id='chat:whatsapp'")
    assert check["result"] == "OK"
    assert conn.execute("SELECT COUNT(*) FROM actions_log WHERE action='WHATSAPP_CHECKED'"
                        ).fetchone()[0] == 1
    assert list((tmp_path / "tmp").iterdir()) == [], "the copy was left behind"


def test_a_changed_schema_fails_the_pass_loudly(conn, pipe, tmp_path):
    roots, registry, clf = pipe
    src = build_container(tmp_path, at(4, 9))
    import sqlite3
    raw = sqlite3.connect(src / "ChatStorage.sqlite")
    raw.execute("ALTER TABLE ZWAMESSAGE RENAME COLUMN ZTEXT TO ZBODY")
    raw.commit()
    raw.close()
    r = w.run_pass(conn, roots, registry, clf, src_dir=src, tmp_root=tmp_path / "tmp",
                   now=at(5, 1), transcriber=no_speech)
    assert r.result == "FAILED" and "ZWAMESSAGE.ZTEXT" in r.error
    assert conn.execute("SELECT detail FROM actions_log WHERE action='WHATSAPP_FAILED'"
                        ).fetchone()[0].startswith("WhatsAppError")
    assert conn.execute("SELECT result FROM source_checks").fetchone()[0] == "FAILED"


def test_plan_days_reads_without_writing(conn, tmp_path):
    chat = direct(msg("A", at(4, 9), "x"), msg("B", at(5, 9), "y"))
    assert w.plan_days(conn, [chat], at(5, 10)) == [
        ("Acme Supply (+15550100)", "2026-11-04", 1, True),
        ("Acme Supply (+15550100)", "2026-11-05", 1, False)]
    assert conn.execute("SELECT COUNT(*) FROM whatsapp_messages").fetchone()[0] == 0


def test_every_action_the_brief_reads_is_one_this_job_writes():
    """The D-027 trap, as in test_notify.py."""
    assert brief.JOB_FAILURES[w.FAILED] == w.CHECKED
    assert brief.WATCHED_JOBS[w.CHECKED] == ("whatsapp", 3)


def test_the_job_stops_when_halted(monkeypatch, capsys):
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))
    import whatsapp as job
    monkeypatch.setattr(job._halt, "read_flag", lambda: {"ts": "now", "by": "telegram"})
    monkeypatch.setattr(job, "ensure_venv", lambda *a, **k: None)
    monkeypatch.setattr(job, "load_lms_env", lambda: {})
    monkeypatch.setattr(_sys, "argv", ["whatsapp.py"])
    assert job.main() == 0
    assert "HALTED" in capsys.readouterr().out
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `$PY -m pytest -o addopts="" -p no:cacheprovider tests/test_whatsapp.py -q`
Expected: the new tests FAIL. `run_pass`, `plan_days` and the `JOB_FAILURES` key are missing, and `_halt` is not in `ops/whatsapp.py`.

- [ ] **Step 3: Write `run_pass`, `read_chats` and `plan_days`**

Add to the imports of `lms/core/pipeline/whatsapp.py`:

```python
import tempfile

from ..adapters import whatsapp as wa
from . import coverage
```

Append:

```python
# ---------------------------------------------------------------------------
# One pass
# ---------------------------------------------------------------------------

def read_chats(src_dir: Path, tmp: Path, since: datetime) -> list[Chat]:
    copy = wa.snapshot(src_dir, tmp / "db")
    rconn = sqlite3.connect(copy)
    try:
        missing = wa.check_schema(rconn)
        if missing:
            raise wa.WhatsAppError("schema changed, missing: " + ", ".join(missing))
        return wa.read(rconn, since, Path(src_dir) / wa.MEDIA_DIR)
    finally:
        rconn.close()


def run_pass(conn: sqlite3.Connection, roots, registry, classifier, *,
             src_dir: Path, tmp_root: Path, now: datetime,
             transcriber: Transcriber = asr.transcribe, since_days: int = 3,
             ingest=ingest_file) -> PassResult:
    """Read, check the hour, close the days; record the pass however it ends.

    since_days covers yesterday's closing and late-synced messages; older
    days are the backfill's (ops/backfill_whatsapp.py)."""
    started = db.now_iso()
    result = PassResult()
    Path(tmp_root).mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="pass-", dir=tmp_root))
    try:
        chats = read_chats(src_dir, tmp, now - timedelta(days=since_days))
        result.messages = sum(len(c.messages) for c in chats)
        record(conn, chats)
        scan(conn, chats, classifier, now, tmp, transcriber, result)
        close(conn, roots, registry, classifier, chats, now, tmp, transcriber,
              result, ingest=ingest)
    except Exception as exc:                                  # noqa: BLE001
        result.error = f"{type(exc).__name__}: {exc}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    coverage.record_check(conn, SOURCE_ID, result.result, started_at=started,
                          items=result.messages, detail=result.summary())
    db.log_action(conn, "WHATSAPP_FAILED" if result.error else "WHATSAPP_CHECKED",
                  detail=result.summary()[:400])
    conn.commit()
    return result


def plan_days(conn: sqlite3.Connection, chats: list[Chat],
              now: datetime) -> list[tuple[str, str, int, bool]]:
    """For --dry-run: unfiled messages per chat day. Writes nothing."""
    out = []
    for chat in chats:
        unfiled = []
        for m in chat.messages:
            row = _row(conn, m.stanza_id)
            if row is None or row["artifact_id"] is None:
                unfiled.append(m)
        for day, msgs in sorted(_by_day(unfiled).items()):
            out.append((chat_label(chat), day, len(msgs), closed(day, now)))
    return out
```

The literals `"WHATSAPP_FAILED"` and `"WHATSAPP_CHECKED"` in `run_pass` are deliberate: `tests/test_stalled_jobs.py` greps the code for `"WHATSAPP_CHECKED"`.

- [ ] **Step 4: Arm the brief**

In `lms/core/reports/brief.py`, add the last entry of `WATCHED_JOBS` (after `"NOTIFY_CHECKED"`):

```python
    # WhatsApp (R40, D-079): hourly; every pass writes a row.
    "WHATSAPP_CHECKED": ("whatsapp", 3),
```

and to `JOB_FAILURES`:

```python
    "WHATSAPP_FAILED":     "WHATSAPP_CHECKED",
```

- [ ] **Step 5: Write the whole job**

Replace `lms/ops/whatsapp.py` from its docstring through `main()` (keep `do_probe` as written in Task 2):

```python
#!/usr/bin/env python3
"""Read WhatsApp, hourly (R40, D-079).

    ./ops/whatsapp.py              # one pass: what com.lms.whatsapp runs hourly
    ./ops/whatsapp.py --probe      # the database's shape, no content; writes nothing
    ./ops/whatsapp.py --dry-run    # unfiled chat days, open or closed; writes nothing

Reads the WhatsApp Mac app's database from a copy; never writes to WhatsApp,
never sends. Each pass: hourly urgency check on today's and yesterday's new
messages (alerts through D-077), then every closed chat day filed as one
transcript. See core/pipeline/whatsapp.py and ops/WHATSAPP_APPLY.md.

Halted (D-059), it reads nothing: com.lms.whatsapp is disabled with every
other job, and run by hand it stops at the flag.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _halt                                                # noqa: E402
from _env import archive_volume_problem, load_lms_env      # noqa: E402
from _reexec import ensure_venv                            # noqa: E402
from core.adapters import whatsapp as wa                   # noqa: E402
```

(`do_probe` from Task 2 stays here, unchanged.)

```python
def do_dry_run(conn, now: datetime) -> int:
    from core.pipeline import whatsapp as w
    tmp = Path(tempfile.mkdtemp(prefix="wa-dry-"))
    try:
        chats = w.read_chats(wa.CONTAINER, tmp, now - timedelta(days=3))
    except wa.WhatsAppError as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    rows = w.plan_days(conn, chats, now)
    for label, day, n, is_closed in rows:
        print(f"  {day}  {'closed' if is_closed else 'open  '}  {n:4d}  {label}")
    print(f"{len(rows)} chat day(s) unfiled. Nothing was written.")
    return 0


def main() -> int:
    ensure_venv("LMS_WHATSAPP_REEXEC", script=__file__)
    load_lms_env()
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--probe", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    if args.probe:
        return do_probe(wa.CONTAINER)

    flag = _halt.read_flag()
    if flag is not None:
        print(f"HALTED since {flag.get('ts')} (by {flag.get('by')}): nothing read.")
        return 0

    problem = archive_volume_problem()
    if problem:
        print(f"archive unavailable: {problem}", file=sys.stderr)
        return 1

    from core.db import database as db
    from core.pipeline import filing
    from core.pipeline import whatsapp as w
    from core.pipeline.classify import Classifier
    from core.pipeline.registry import load_registry

    roots = filing.StorageRoots.from_env()
    roots.ensure()
    conn = db.connect(os.environ.get("LMS_DB", str(roots.archive.parent / "lms.db")))
    now = datetime.now().astimezone()
    try:
        if args.dry_run:
            return do_dry_run(conn, now)
        registry = load_registry()
        result = w.run_pass(conn, roots, registry, Classifier(registry),
                            src_dir=wa.CONTAINER,
                            tmp_root=roots.archive.parent / "tmp" / "whatsapp",
                            now=now)
    finally:
        conn.close()
    print(result.summary())
    return 1 if result.result == "FAILED" or result.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Write the plist**

`lms/ops/com.lms.whatsapp.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!--
  WhatsApp, hourly. R40, D-079. ops/whatsapp.py says what one pass does.

  Install (ops/WHATSAPP_APPLY.md):
    cp ops/com.lms.whatsapp.plist ~/Library/LaunchAgents/
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.lms.whatsapp.plist

  StartInterval: a pass missed while the Mac slept is made up by the next
  one, which reads the last three days. A LaunchAgent, because the WhatsApp
  container is in Matthew's user Library (D-009). The interpreter below needs
  Full Disk Access. A halt (D-059) disables this job with the rest.
-->
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.lms.whatsapp</string>

    <key>ProgramArguments</key>
    <array>
        <string>/Users/mleca/lms-repo/lms/.venv/bin/python</string>
        <string>/Users/mleca/lms-repo/lms/ops/whatsapp.py</string>
    </array>

    <key>WorkingDirectory</key>
    <string>/Users/mleca/lms-repo/lms</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>TZ</key>
        <string>America/New_York</string>
        <key>PYTHONUNBUFFERED</key>
        <string>1</string>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>

    <key>StartInterval</key>
    <integer>3600</integer>

    <key>RunAtLoad</key>
    <true/>

    <key>StandardOutPath</key>
    <string>/Users/mleca/LMS/logs/whatsapp.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/mleca/LMS/logs/whatsapp.err</string>

    <key>ProcessType</key>
    <string>Background</string>
    <key>Nice</key>
    <integer>10</integer>
</dict>
</plist>
```

- [ ] **Step 7: Run the full suite and confirm it passes**

Run: the Global Constraints test command.
Expected: all pass, including `test_plists.py` (the new plist), `test_stalled_jobs.py` (`"WHATSAPP_CHECKED"` written), and `test_model_health.py`'s cross-check (it reads only `MODEL_` entries).

- [ ] **Step 8: Commit**

```bash
git add lms/core/pipeline/whatsapp.py lms/core/reports/brief.py lms/ops/whatsapp.py lms/ops/com.lms.whatsapp.plist lms/tests/test_whatsapp.py
git commit -m "R40: the hourly WhatsApp pass, watched by the brief"
```

---

### Task 8: Backfill command, register, setup doc, decision

**Files:**
- Create: `lms/ops/backfill_whatsapp.py`
- Create: `lms/ops/WHATSAPP_APPLY.md`
- Modify: `lms/config/sources.yaml` (the `chat:whatsapp` entry)
- Modify: `lms/tests/test_coverage.py:65-67`
- Modify: `DECISIONS.md` (add D-079 above D-078)
- Modify: `REQUIREMENTS_REMAINING.md` (R40 row)
- Modify: `docs/superpowers/specs/2026-11-05-whatsapp-reader-design.md` (temp path and group header, to match the build)
- Test: `lms/tests/test_whatsapp.py` (append)

**Interfaces:**
- Consumes: `w.read_chats`, `w.record`, `w.close`, `w.day_of`, `w.PassResult` (Tasks 5–7)
- Produces: `ops/backfill_whatsapp.py` with `backfill(conn, roots, registry, classifier, *, src_dir, tmp_root, now, days, batch, transcriber) -> int`

- [ ] **Step 1: Write the failing tests**

Append to `lms/tests/test_whatsapp.py`:

```python
def test_backfill_files_old_days_in_batches_and_leaves_yesterday(conn, pipe, tmp_path):
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))
    import backfill_whatsapp as bf
    roots, registry, clf = pipe
    now = datetime.now(ET)
    box = WA(tmp_path / "container")
    c = box.chat("15550100@s.whatsapp.net", "Acme Supply")
    for back in (5, 4, 3, 1):
        box.message(c, (now - timedelta(days=back)).replace(hour=9), f"day -{back}")
    box.close()
    filed = bf.backfill(conn, roots, registry, clf, src_dir=box.container,
                        tmp_root=tmp_path / "tmp", now=now, days=10, batch=2,
                        transcriber=no_speech)
    assert filed == 3
    days = {r[0]: r[1] for r in conn.execute(
        "SELECT day, artifact_id FROM whatsapp_messages")}
    assert days[w.day_of(now - timedelta(days=1))] is None, "yesterday is the hourly job's"
    assert notify.pending(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM whatsapp_messages WHERE scanned_at IS NULL "
                        "AND artifact_id IS NOT NULL").fetchone()[0] == 0
```

In `lms/tests/test_coverage.py`, replace `test_whatsapp_is_listed_as_having_no_supported_path`:

```python
def test_whatsapp_is_listed_with_its_reader_and_what_it_waits_on():
    wa = next(s for s in coverage.load_register() if s.id == "chat:whatsapp")
    assert "ChatStorage.sqlite" in wa.method
    assert wa.cadence_hours == 1
    assert wa.status == "blocked" and "WHATSAPP_APPLY" in wa.blocked_on
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `$PY -m pytest -o addopts="" -p no:cacheprovider tests/test_whatsapp.py tests/test_coverage.py -q`
Expected: FAIL. `No module named 'backfill_whatsapp'`, and the register still says `unsupported`.

- [ ] **Step 3: Write the backfill command**

`lms/ops/backfill_whatsapp.py`:

```python
#!/usr/bin/env python3
"""File the WhatsApp history the Mac app holds (R40, R28, D-079).

    ./ops/backfill_whatsapp.py --days 30              # last 30 days, 20 chat days a batch
    ./ops/backfill_whatsapp.py --days 365 --batch 50

Files every chat day before yesterday; yesterday and today are the hourly
job's, so its urgency check still sees them. Old days raise no alerts: they
are marked checked when filed, and notify's 2-day rule drops them anyway.
Not a check of the source: nothing goes to source_checks.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _halt                                                # noqa: E402
from _env import archive_volume_problem, load_lms_env      # noqa: E402
from _reexec import ensure_venv                            # noqa: E402
from core.adapters import asr                              # noqa: E402
from core.adapters import whatsapp as wa                   # noqa: E402
from core.pipeline import whatsapp as w                    # noqa: E402


def backfill(conn, roots, registry, classifier, *, src_dir: Path, tmp_root: Path,
             now: datetime, days: int, batch: int, transcriber=asr.transcribe) -> int:
    Path(tmp_root).mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="backfill-", dir=tmp_root))
    before = w.day_of(now - timedelta(days=1))
    total = 0
    try:
        chats = w.read_chats(src_dir, tmp, now - timedelta(days=days))
        w.record(conn, chats)
        while True:
            result = w.PassResult()
            n = w.close(conn, roots, registry, classifier, chats, now, tmp,
                        transcriber, result, before=before, limit=batch)
            total += n
            print(f"batch: {result.summary()}", flush=True)
            if n < batch:
                return total
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    ensure_venv("LMS_WHATSAPP_REEXEC", script=__file__)
    load_lms_env()
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--days", type=int, required=True)
    p.add_argument("--batch", type=int, default=20)
    args = p.parse_args()

    if _halt.read_flag() is not None:
        print("HALTED: nothing read.")
        return 0
    problem = archive_volume_problem()
    if problem:
        print(f"archive unavailable: {problem}", file=sys.stderr)
        return 1

    from core.db import database as db
    from core.pipeline import filing
    from core.pipeline.classify import Classifier
    from core.pipeline.registry import load_registry

    roots = filing.StorageRoots.from_env()
    roots.ensure()
    conn = db.connect(os.environ.get("LMS_DB", str(roots.archive.parent / "lms.db")))
    registry = load_registry()
    try:
        n = backfill(conn, roots, registry, Classifier(registry),
                     src_dir=wa.CONTAINER,
                     tmp_root=roots.archive.parent / "tmp" / "whatsapp",
                     now=datetime.now().astimezone(), days=args.days,
                     batch=args.batch)
    except wa.WhatsAppError as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    print(f"{n} chat day(s) filed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Rewrite the register entry**

Replace the `chat:whatsapp` entry in `lms/config/sources.yaml`:

```yaml
  - id: chat:whatsapp
    label: WhatsApp
    kind: chat
    account: Matthew's WhatsApp, linked to the Mac app
    method: >-
      read-only copy of the WhatsApp Mac app's ChatStorage.sqlite
      (ops/whatsapp.py, D-079). One transcript per chat per day, filed when
      the day closes; documents and photos filed under it; voice notes
      transcribed on the Mac. Unofficial: a WhatsApp update can change it,
      and the pass then fails loudly
    permissions: >-
      Full Disk Access for the LMS Python; the WhatsApp Mac app linked to his
      phone. Nothing is written to WhatsApp and nothing is sent
    cadence_hours: 1
    status: blocked
    requirement: R40
    blocked_on: >-
      Install on the Mac per ops/WHATSAPP_APPLY.md; Matthew links the Mac app
      with one QR scan from his phone.
    gaps: >-
      Only the history the Mac app synced when linked. Videos are listed, not
      filed. Files the Mac app never downloaded are listed, not filed. A
      message deleted in WhatsApp after filing stays filed.
```

- [ ] **Step 5: Write `lms/ops/WHATSAPP_APPLY.md`**

````markdown
# WhatsApp reader — apply on the Mac (R40, D-079)

Installs `com.lms.whatsapp`. Every hour it reads a copy of the WhatsApp Mac
app's database, checks today's new messages for anything urgent (Telegram
alert, D-077), and files each finished day of each chat as one transcript.
It never writes to WhatsApp and never sends.

About 40 minutes. **Step 2 needs Matthew's phone, once.**

Type commands one line at a time over RustDesk. Expected output is written
under each block, never after a `#` on the command line (zsh on this Mac
passes `#` to the command).

## 1. Install WhatsApp and set downloads

Mac App Store, WhatsApp. Then WhatsApp → Settings → Storage → Automatic
downloads: tick Photos, Audio and Documents. A file the Mac app never
downloads is listed in the transcript but cannot be filed.

## 2. Matthew links it

On his phone: WhatsApp → Settings → Linked devices → Link a device, and scan
the QR code on the Mac. Let the Mac app finish syncing (the chat list fills).

## 3. Full Disk Access

System Settings → Privacy & Security → Full Disk Access → `+`, press
Cmd-Shift-G, paste `/Users/mleca/lms-repo/lms/.venv/bin/python`, Open, turn
it on.

## 4. Probe: stop here if it fails

```bash
cd ~/lms-repo/lms && git pull
```
```bash
./ops/whatsapp.py --probe
```

`ZWAMESSAGE: <n>` with n above 0, no `MISSING:` line, and `media folder
readable: True`. Anything else: copy the output to the developer and stop.

## 5. Speech

```bash
brew install ffmpeg
```
```bash
.venv/bin/python -m pip install mlx-whisper
```
```bash
.venv/bin/python -c "from huggingface_hub import snapshot_download; snapshot_download('mlx-community/whisper-large-v3-turbo', local_dir='/Users/mleca/LMS/models/whisper-large-v3-turbo')"
```

The last command downloads about 1.6 GB once; after it, speech runs offline.

## 6. Dry run, backfill, install

```bash
./ops/whatsapp.py --dry-run
```
```bash
./ops/backfill_whatsapp.py --days 30
```
```bash
cp ops/com.lms.whatsapp.plist ~/Library/LaunchAgents/
```
```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.lms.whatsapp.plist
```
```bash
tail -3 ~/LMS/logs/whatsapp.log
```

The dry run lists unfiled chat days and writes nothing. The backfill prints
one line a batch and ends with `N chat day(s) filed.` The log line is the
pass summary, for example `412 message(s) seen, 0 chat day(s) filed, 0
alert(s)`.

Then in `config/sources.yaml` set `chat:whatsapp` to `status: active` and
remove its `blocked_on` (the developer commits that).

## Undo

```bash
launchctl bootout gui/$(id -u)/com.lms.whatsapp
```
```bash
rm ~/Library/LaunchAgents/com.lms.whatsapp.plist
```

Filed transcripts stay filed. A halt (`/halt`) stops this job with the rest.
````

- [ ] **Step 6: Record D-079 and R40**

In `DECISIONS.md`, insert above `### D-078`:

```markdown
### D-079 — WhatsApp is read from the Mac app's database, one transcript per chat per day
**Status:** Decided · Nov 5 2026 · developer · implements R40 (Matthew, Oct 19, D-065) · not yet on the Mac ([lms/ops/WHATSAPP_APPLY.md](lms/ops/WHATSAPP_APPLY.md)) · spec: [docs/superpowers/specs/2026-11-05-whatsapp-reader-design.md](docs/superpowers/specs/2026-11-05-whatsapp-reader-design.md)

- **How:** a copy of the WhatsApp Mac app's `ChatStorage.sqlite` (and its WAL), checked with `quick_check`, read with the expected `ZWA*` schema. SQLite never opens the original. Rejected: an unofficial WhatsApp Web client (ban risk, his keys on the machine, a new runtime). Fallback if the database is unreadable: chat export into the watch folder.
- **What is filed:** one transcript per chat per Eastern-time day, closed at the first pass after 00:30 the next day; documents and photos under it as attachments are under an email (D-040). Late-synced messages become a part 2. All chats, groups included (D-065).
- **Hourly:** today's and yesterday's new messages go to the classifier for urgency only; an urgent chat day is one Telegram alert, keyed `whatsapp:<chat>:<day>`, and its filing reuses the key (`ingest_file(alert_key=…)`), so it is not alerted twice.
- **Voice notes:** `ffmpeg` + `mlx-whisper` (Whisper large-v3-turbo) on the Mac, offline; LM Studio has no speech endpoint. TIER-A's model is Whisper until a Qwen3-ASR MLX runtime is confirmed. Over 10 minutes: not transcribed, listed.
- **Watched:** `WHATSAPP_CHECKED` / `WHATSAPP_FAILED`, stale after 3 h; `source_checks` `chat:whatsapp` OK, PARTIAL (voice or files missing) or FAILED (unreadable, schema changed).
- **Copies only:** `filing.file_artifact` moves what it is given, so every transcript and media file handed to ingest is a temp copy.
- **Unconfirmed until `--probe` on the Mac:** the file is unencrypted, the table and column names, the `ZMESSAGETYPE` values, and that Full Disk Access is enough to read the group container.
```

In `REQUIREMENTS_REMAINING.md`, replace the R40 row's last two cells:

```markdown
| R40 | WhatsApp, hourly, read-only | Matthew, Oct 19 (D-065) | **Built (D-079)**: the Mac app's database, read from a copy; install per [lms/ops/WHATSAPP_APPLY.md](lms/ops/WHATSAPP_APPLY.md). Needs Matthew's QR scan once |
```

- [ ] **Step 7: Bring the spec into line with the build**

In `docs/superpowers/specs/2026-11-05-whatsapp-reader-design.md`:
- Replace `under \`~/LMS/tmp/whatsapp/\`` with `under \`<LMS root>/tmp/whatsapp/\` (beside the mail spool)`.
- Replace `Groups put the group name on the first line and \`(group, N participants)\`.` with `Groups put the group name on the first line and \`(group)\`.`

- [ ] **Step 8: Run the full suite and confirm it passes**

Run: the Global Constraints test command.
Expected: all pass.

- [ ] **Step 9: Commit with the executable bit**

```bash
git add --chmod=+x lms/ops/backfill_whatsapp.py
git add lms/ops/WHATSAPP_APPLY.md lms/config/sources.yaml lms/tests/test_coverage.py lms/tests/test_whatsapp.py DECISIONS.md REQUIREMENTS_REMAINING.md docs/superpowers/specs/2026-11-05-whatsapp-reader-design.md
git commit -m "D-079: WhatsApp backfill, register entry, Mac setup"
```

---

### Task 9: On the Mac (the user, over RustDesk)

Not code. Run `lms/ops/WHATSAPP_APPLY.md` steps 4–6 after `git pull`.

Report back:
- the probe output
- one voice note's transcript line from a filed transcript in the archive
- the first `whatsapp.log` line

A failure here becomes a fix in a new task, not an edit to a finished one.
