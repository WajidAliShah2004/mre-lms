# iMessage Reader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Read every iMessage, SMS and RCS text on the Mac read-only, check it hourly for urgent items, and file one transcript per chat per day, using the same engine as the WhatsApp reader.

**Architecture:** The body of `core/pipeline/whatsapp.py` moves into a generic chat-day engine, `core/pipeline/chatdays.py`. The engine is driven by a frozen `ChatSource` settings object. WhatsApp keeps a thin module with all of its current names. A new adapter, `core/adapters/imessage.py`, reads a checked copy of `~/Library/Messages/chat.db` and of the Contacts databases. A new thin module, `core/pipeline/imessage.py`, gives the engine iMessage's settings and records the SMS line of the coverage register after each pass. The job is `ops/imessage.py` with `com.lms.imessage`, run hourly, and the history load is `ops/backfill_imessage.py`.

**Tech Stack:** Python 3.12 on the Mac (tests run on Windows with Python 3.14), sqlite3, pytest, launchd.

**Spec:** [docs/superpowers/specs/2026-11-07-imessage-reader-design.md](../specs/2026-11-07-imessage-reader-design.md)

## Global Constraints

- **The refactor's acceptance test:** `lms/tests/test_whatsapp.py` and `lms/tests/test_whatsapp_adapter.py` pass with **no edits**. `ops/whatsapp.py` does not change.
- **Read-only by construction:** only copies of `chat.db` and of `AddressBook-v22.abcddb` are opened. Nothing calls Messages, nothing is sent, and nothing is written to `~/Library`.
- **Table names** come only from `ChatSource.table`, which must be one of `{"whatsapp_messages", "imessage_messages"}`. They are never taken from input.
- **Action strings must appear as literals** in source code, because `tests/test_stalled_jobs.py` greps for them (the D-027 trap): `"WHATSAPP_CHECKED"`, `"WHATSAPP_FAILED"`, `"IMESSAGE_CHECKED"`, `"IMESSAGE_FAILED"`.
- **Probes print no message text, number, address or name.** Only counts, table and column names, and dates.
- **Exact strings to use:**
  - Transcript header: `iMessage chat: <name> (direct, <identifier>)` or `iMessage chat: <name> (group)`.
  - File name: `iMessage <chat> <day> part <n>.txt`.
  - Alert key: `imessage:<chat_identifier>:<day>`.
  - `source_ref`: `imessage:<chat_identifier>:<day>:<part>`.
  - Stale text: `Messages on the Mac has not synced since <YYYY-MM-DD HH:MM> (is it signed in?)`.
  - SMS partial text: `no SMS or RCS since <YYYY-MM-DD> while iMessage arrives: is Text Message Forwarding on for this Mac?`
- **Thresholds:**
  - Stale after 48 hours.
  - SMS quiet after 7 days.
  - The hourly pass reads the last 3 days.
  - The backfill default is 20 chat days per batch; the first load is `--days 90`.
  - A `date` value under 10^11 is seconds, not nanoseconds.
- **One behaviour change, for both sources:** a voice note whose `duration_s is None` shows `[voice]` (`[voice] text`, `[voice, not transcribed]`) instead of `[voice 0:00]`. Nothing else changes for WhatsApp.
- **Run tests from `lms/`:**
  ```
  C:/Users/murta/AppData/Local/Temp/claude/C--Users-murta-Documents-source-codes-openclaw-mathew/8a3c1626-c8ef-4ae5-8761-7c8430ab64c0/scratchpad/venv/Scripts/python.exe -m pytest -o addopts="" -p no:cacheprovider <tests> -q
  ```
  The whole suite is the same command with `--ignore=tests/test_halt.py` and no test paths. The baseline before Task 1 is 1252 passed, 1 skipped, 1 xfailed.
- **Commits:**
  - Work on `master`, one commit per task, staging files by name (`git add <paths>`, never `git add -A`). The developer's uncommitted Nov 7 edits (DECISIONS.md D-068, REQUIREMENTS_REMAINING.md, `lms/config/sources.yaml`, the spec, the PDF) must not be swept into a task commit; Task 7 edits the same three files, so ask the developer to commit those first.
  - Never push; the developer says when.
  - End every commit message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Privacy:** Matthew's numbers never appear in code, tests or docs. Tests use 555-01xx numbers only.

## Refinements to the spec, decided while planning

1. **`ChatSource.read` returns a `ReadResult(chats, newest, by_service)`, not a tuple.** It also gains an optional `after_pass(conn, result, now, started)`. The SMS line needs each service's newest message from the same copy, and this carries it without reading `chat.db` twice. WhatsApp's `by_service` is empty and its `after_pass` is `None`.
2. **`chat_handle_join` (`chat_id`, `handle_id`) is a required table.** It names a group that has no `display_name` ("Group with A, B, C") from its members rather than from whoever happened to write in the window that was read. It is a standard `chat.db` table; the probe confirms it.
3. **An attachment of unknown type** becomes the line text `[file: <transfer_name>]`, followed by the caption if there is one. This is done in the adapter, so the engine's line format for WhatsApp does not change.
4. **The backfill loop moves to `ops/_backfill.py`**, shared by `backfill_whatsapp.py` (same names and behaviour) and the new `backfill_imessage.py`.
5. **A message with no text, no readable `attributedBody` blob and no attachment is skipped.** Such rows are app messages or retracted messages: there is nothing to file. A blob that exists but cannot be decoded is kept and shows `[message not readable]`.
6. **`/lms status`** also shows `last good imessage:`, beside mail and whatsapp.

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `lms/core/adapters/chat.py` | new | `Media`, `Message`, `Chat`, `ChatDBError`, `snapshot()`: what every chat reader shares |
| `lms/core/adapters/whatsapp.py` | modify | imports those from `chat.py`; `WhatsAppError(ChatDBError)`; `snapshot()` wraps `chat.snapshot` |
| `lms/core/pipeline/chatdays.py` | new | the engine: `ChatSource`, `ReadResult`, `PassResult`, record/scan/close/run_pass/plan_days |
| `lms/core/pipeline/whatsapp.py` | rewrite (thin) | `CHAT_SOURCE` and the old names, delegating to the engine |
| `lms/core/db/schema.sql` | modify | `imessage_messages` table and index |
| `lms/tests/test_chatdays.py` | new | the engine with two sources side by side |
| `lms/tests/test_chat_adapter.py` | new | `chat.snapshot` with any database name and error type |
| `lms/tests/test_hard_stops.py` | modify | the `shutil.rmtree` exemption moves from `pipeline/whatsapp.py` to `pipeline/chatdays.py` |
| `lms/core/adapters/imessage.py` | new | schema check, dates, `attributedBody` decoder, Contacts names, `read`, `newest`, `newest_by_service`, `probe` |
| `lms/tests/im_fixture.py` | new | a fake `chat.db` and Contacts database |
| `lms/tests/test_imessage_adapter.py` | new | adapter tests |
| `lms/core/pipeline/imessage.py` | new | iMessage's `CHAT_SOURCE`, `sms_line`, thin functions |
| `lms/core/reports/brief.py` | modify | `IMESSAGE_CHECKED` watched, `IMESSAGE_FAILED` failure |
| `lms/tests/test_imessage.py` | new | the pass, the SMS line, the brief strings |
| `lms/ops/_backfill.py` | new | the shared backfill loop and `main` |
| `lms/ops/backfill_whatsapp.py` | modify | delegates to `_backfill` |
| `lms/ops/backfill_imessage.py` | new | iMessage's backfill |
| `lms/ops/imessage.py` | new | the hourly job, `--probe`, `--dry-run` |
| `lms/ops/com.lms.imessage.plist` | new | launchd, hourly |
| `lms/ops/remote.py` | modify | `/lms imessage`; `last good imessage` in status |
| `lms/tests/test_imessage_job.py`, `lms/tests/test_remote.py` | new / modify | job, backfill, probe, remote |
| `lms/ops/IMESSAGE_APPLY.md` | new | setup on the Mac |
| `lms/config/sources.yaml`, `lms/tests/test_coverage.py` | modify | the register's iMessage and SMS lines |
| `DECISIONS.md`, `REQUIREMENTS_REMAINING.md` | modify | D-081; R2 |

---

### Task 1: Shared chat types and the checked copy

**Files:**
- Create: `lms/core/adapters/chat.py`
- Modify: `lms/core/adapters/whatsapp.py` (lines 16–128: imports, `COPY_ATTEMPTS`, `WhatsAppError`, the three dataclasses, `snapshot`)
- Test: `lms/tests/test_chat_adapter.py`

**Interfaces:**
- Produces:
  - `core.adapters.chat.Media(local_path: Path|None, title: str|None, size: int|None, duration_s: float|None)` (frozen).
  - `Message(stanza_id: str, sent_at: datetime, from_me: bool, sender_name: str, text: str|None, kind: str, media: Media|None)` (frozen).
  - `Chat(jid: str, name: str, is_group: bool, messages: list[Message] = [])`.
  - `class ChatDBError(RuntimeError)`.
  - `snapshot(src_dir: Path, tmp_dir: Path, db_name: str, attempts: int = 3, error: type[Exception] = ChatDBError) -> Path`.
  - `core.adapters.whatsapp` still exports `Media`, `Message`, `Chat`, `WhatsAppError` (now a subclass of `ChatDBError`), and `snapshot(src_dir, tmp_dir, attempts=3)`.

- [ ] **Step 1: Write the failing tests**

`lms/tests/test_chat_adapter.py`:

```python
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
```

- [ ] **Step 2: Run them to see them fail**

Run: `<python> -m pytest -o addopts="" -p no:cacheprovider tests/test_chat_adapter.py -q`
Expected: FAIL. `ImportError: cannot import name 'chat' from 'core.adapters'`

- [ ] **Step 3: Create `lms/core/adapters/chat.py`**

```python
"""What every chat reader shares: the message types and the checked copy
(R40, R2, D-079, D-081).

A chat reader (WhatsApp, iMessage) reads an app's SQLite database from a
COPY: the file and its -wal and -shm are copied into a private directory and
checked with quick_check. SQLite never opens the original, so an app writing
beside us cannot be disturbed and nothing of his is ever written.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

COPY_ATTEMPTS = 3


class ChatDBError(RuntimeError):
    """The database is missing, torn, or not the shape the reader expects."""


@dataclass(frozen=True)
class Media:
    local_path: Path | None         # None: the file is not on the Mac
    title: str | None
    size: int | None
    duration_s: float | None        # None: not known (iMessage stores none)


@dataclass(frozen=True)
class Message:
    stanza_id: str                  # the source's own stable message id
    sent_at: datetime               # (WhatsApp ZSTANZAID, iMessage guid)
    from_me: bool
    sender_name: str
    text: str | None
    kind: str
    media: Media | None


@dataclass
class Chat:
    jid: str                        # the source's chat id (WhatsApp JID,
    name: str                       # iMessage chat_identifier)
    is_group: bool
    messages: list[Message] = field(default_factory=list)


def snapshot(src_dir: Path, tmp_dir: Path, db_name: str,
             attempts: int = COPY_ATTEMPTS,
             error: type[Exception] = ChatDBError) -> Path:
    """Copy the database and its WAL into tmp_dir; return the checked copy.

    The app may be writing while the three files are copied, so a copy can be
    torn. quick_check catches that; the copy is retried, then `error` is
    raised."""
    src = Path(src_dir) / db_name
    if not src.is_file():
        raise error(f"no {db_name} in {src_dir}")
    tmp_dir = Path(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(tmp_dir, 0o700)
    last = ""
    for n in range(attempts):
        dest = tmp_dir / f"copy{n}"
        dest.mkdir(mode=0o700)
        for suffix in ("", "-wal", "-shm"):
            f = Path(src_dir) / (db_name + suffix)
            if f.exists():
                shutil.copyfile(f, dest / f.name)
        copy = dest / db_name
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
    raise error(f"copy failed quick_check {attempts} times: {last}")
```

- [ ] **Step 4: Point the WhatsApp adapter at it**

In `lms/core/adapters/whatsapp.py`:
- Replace the import block (lines 16–24) with the block below. `os` stays because `media_files` uses `os.walk`; `subprocess` stays for `_keychain`; `shutil` and `dataclass`/`field` go.

```python
from __future__ import annotations

import os
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import chat as _chat
from .chat import Chat, Media, Message   # noqa: F401  (re-exported: existing imports)
```

- Replace `class WhatsAppError(RuntimeError): pass` with `class WhatsAppError(_chat.ChatDBError): pass`.
- Delete the three dataclasses (`Media`, `Message`, `Chat`). They are now imported.
- Replace the whole `snapshot` function with:

```python
def snapshot(src_dir: Path, tmp_dir: Path, attempts: int = COPY_ATTEMPTS) -> Path:
    """Copy ChatStorage.sqlite and its WAL into tmp_dir; return the checked
    copy (core/adapters/chat.py)."""
    return _chat.snapshot(src_dir, tmp_dir, DB_NAME, attempts, error=WhatsAppError)
```

Keep `COPY_ATTEMPTS = 3` and every other name in the module.

- [ ] **Step 5: Run the new tests and the unchanged WhatsApp tests**

Run: `<python> -m pytest -o addopts="" -p no:cacheprovider tests/test_chat_adapter.py tests/test_whatsapp_adapter.py tests/test_whatsapp.py -q`
Expected: all pass, with `git diff --stat` showing no change to either `test_whatsapp*.py`.

- [ ] **Step 6: Commit**

```bash
git add lms/core/adapters/chat.py lms/core/adapters/whatsapp.py lms/tests/test_chat_adapter.py
git commit -m "D-081: chat readers share their message types and the checked copy"
```

---

### Task 2: The chat-day engine; WhatsApp becomes a thin module

**Files:**
- Create: `lms/core/pipeline/chatdays.py`
- Rewrite: `lms/core/pipeline/whatsapp.py`
- Modify: `lms/core/db/schema.sql` (add `imessage_messages` after the `whatsapp_messages` block, around line 545)
- Modify: `lms/tests/test_hard_stops.py:269-277` (the exemption key and its reason)
- Test: `lms/tests/test_chatdays.py`

**Interfaces:**
- Consumes: `core.adapters.chat.{Chat, Media, Message}` (Task 1).
- Produces, in `core.pipeline.chatdays`:
  - Constants `TZ`, `CLOSE_AT`, `FILED_KINDS`, `STALE_AFTER`, `TABLES`, and the type `Transcriber`.
  - `ReadResult(chats: list[Chat], newest: datetime|None, by_service: dict[str, datetime] = {})`.
  - `ChatSource(name, label, source_id, table, action, recipient, read, ident, not_synced, after_pass=None)`, with properties `.checked`, `.failed` and `.day_failed`. `read(src_dir: Path, tmp: Path, since: datetime) -> ReadResult`. `not_synced` is a format string with `{since}`. `after_pass(conn, result, now, started) -> None`.
  - `PassResult(source: ChatSource, alerts=0, filed=0, failed=0, voice_missing=0, media_missing=0, messages=0, stale_since=None, by_service={}, error=None)`.
  - Functions that need no source: `day_of(dt)`, `yesterday(now)`, `closed(day, now)`, `line(m, voice)`, `_slot(id)`, `_media_name(title, local)`.
  - Functions that take the source first:
    - `chat_label(src, chat)`
    - `alert_key(src, chat, day)`
    - `header(src, chat, day, part)`
    - `transcript(src, chat, day, part, msgs, voices)`
    - `record(src, conn, chats)`
    - `voice_text(src, conn, m, tmp_dir, transcriber, result)`
    - `scan(src, conn, chats, classifier, now, tmp_dir, transcriber, result)`
    - `close(src, conn, roots, registry, classifier, chats, now, tmp_dir, transcriber, result, *, before=None, limit=None, ocr=True, ingest=ingest_file) -> int`
    - `read_chats(src, src_dir, tmp, since) -> list[Chat]`
    - `run_pass(src, conn, roots, registry, classifier, *, src_dir, tmp_root, now, transcriber=asr.transcribe, since_days=3, ingest=ingest_file) -> PassResult`
    - `plan_days(src, conn, chats, now)`
- Produces, in `core.pipeline.whatsapp`: `CHAT_SOURCE`, plus every name the module has today, with the same signatures.

- [ ] **Step 1: Write the failing engine tests**

`lms/tests/test_chatdays.py`:

```python
"""The chat-day engine with two sources side by side (D-081)."""

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from core.adapters import asr
from core.adapters.chat import Chat, Media, Message
from core.adapters.lmstudio import Completion
from core.db import database as db
from core.pipeline import chatdays as cd
from core.pipeline import filing, notify
from core.pipeline import whatsapp as w
from core.pipeline.classify import Classifier
from core.pipeline.registry import load_registry

ET = ZoneInfo("America/New_York")
CONFIG = Path(__file__).resolve().parents[1] / "config"


def at(day, hour, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=ET)


def texts_source(**kw):
    """A second source, shaped like iMessage, built only from the engine."""
    base = dict(name="imessage", label="iMessage", source_id="messages:imessage",
                table="imessage_messages", action="IMESSAGE",
                recipient="Matthew (iMessage)",
                read=lambda src_dir, tmp, since: cd.ReadResult([], None),
                ident=str,
                not_synced="Messages on the Mac has not synced since {since} "
                           "(is it signed in?)")
    base.update(kw)
    return cd.ChatSource(**base)


def msg(stanza, when, text=None, *, kind="text", media=None, sender="Rhonda"):
    return Message(stanza, when, False, sender, text, kind, media)


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "lms.db")
    yield c
    c.close()


class FakeModel:
    def complete(self, **kw):
        return Completion(text=json.dumps({
            "domain": "BUSINESS", "entity_id": "B_MRE", "category": "VENDORS",
            "subcategory": "invoices-received", "urgency": "NORMAL",
            "confidence": 0.93, "requires_reply": False, "due_date": None,
            "counterparty": "acme-supply", "descriptor": "invoice chat",
            "amount_cents": None, "rationale": "vendor"}),
            model="fake", elapsed_s=0.01, finish_reason="stop")


@pytest.fixture
def pipe(tmp_path):
    roots = filing.StorageRoots(archive=tmp_path / "archive",
                                originals=tmp_path / "_originals",
                                quarantine=tmp_path / "quarantine")
    roots.ensure()
    registry = load_registry(CONFIG)
    return roots, registry, Classifier(registry, client=FakeModel())


class Critical:
    def classify(self, art):
        return SimpleNamespace(urgency="CRITICAL", category="VENDORS",
                               counterparty="acme-supply", descriptor="final notice",
                               due_date=None, amount_cents=None)


def no_speech(path, tmp):
    raise asr.ASRUnavailable("not in tests")


def test_a_table_that_is_not_a_chat_table_is_refused():
    with pytest.raises(ValueError, match="artifacts"):
        texts_source(table="artifacts")


def test_the_action_names_are_the_ones_the_brief_reads():
    src = texts_source()
    assert (src.checked, src.failed, src.day_failed) == \
        ("IMESSAGE_CHECKED", "IMESSAGE_FAILED", "IMESSAGE_DAY_FAILED")
    assert w.CHAT_SOURCE.checked == w.CHECKED == "WHATSAPP_CHECKED"
    assert w.CHAT_SOURCE.failed == w.FAILED == "WHATSAPP_FAILED"


def test_the_header_uses_the_sources_label_and_ident():
    src = texts_source()
    chat = Chat("+15550100100", "Aunt Rhonda", False, [])
    assert cd.header(src, chat, "2026-11-05", 1).splitlines()[0] == \
        "iMessage chat: Aunt Rhonda (direct, +15550100100)"
    assert cd.alert_key(src, chat, "2026-11-05") == "imessage:+15550100100:2026-11-05"


def test_a_voice_note_with_no_known_length_says_voice_only():
    v = Media(None, None, None, None)
    m = msg("V", at(5, 9), kind="voice", media=v)
    assert cd.line(m, None) == "09:00 Rhonda: [voice, not transcribed]"
    assert cd.line(m, "call me back") == "09:00 Rhonda: [voice] call me back"
    zero = msg("Z", at(5, 9), kind="voice", media=Media(None, None, None, 0.0))
    assert cd.line(zero, None) == "09:00 Rhonda: [voice 0:00, not transcribed]"


def test_the_summary_uses_the_sources_words():
    r = cd.PassResult(texts_source(), failed=1, stale_since=at(1, 9))
    s = r.summary()
    assert "see IMESSAGE_DAY_FAILED" in s
    assert "Messages on the Mac has not synced since 2026-11-01 09:00 " \
           "(is it signed in?)" in s


def test_two_sources_with_the_same_ids_do_not_collide(conn, pipe, tmp_path):
    roots, registry, clf = pipe
    im = texts_source()
    wa_chat = Chat("+15550100100", "Rhonda", False, [msg("A", at(4, 9), "whatsapp says")])
    im_chat = Chat("+15550100100", "Rhonda", False, [msg("A", at(4, 9), "imessage says")])
    w.record(conn, [wa_chat])
    cd.record(im, conn, [im_chat])
    cd.scan(im, conn, [im_chat], Critical(), at(4, 10), tmp_path, no_speech,
            cd.PassResult(im))
    w.scan(conn, [wa_chat], Critical(), at(4, 10), tmp_path, no_speech, w.PassResult())
    assert w.close(conn, roots, registry, clf, [wa_chat], at(5, 1), tmp_path,
                   no_speech, w.PassResult()) == 1
    assert cd.close(im, conn, roots, registry, clf, [im_chat], at(5, 1), tmp_path,
                    no_speech, cd.PassResult(im)) == 1
    refs = sorted(r[0] for r in conn.execute(
        "SELECT source || ' ' || source_ref FROM artifacts WHERE parent_id IS NULL"))
    assert refs == ["imessage imessage:+15550100100:2026-11-04:1",
                    "whatsapp whatsapp:+15550100100:2026-11-04:1"]
    keys = sorted(r[0] for r in conn.execute("SELECT key FROM notifications"))
    assert keys == ["imessage:+15550100100:2026-11-04",
                    "whatsapp:+15550100100:2026-11-04"]
    for table in ("whatsapp_messages", "imessage_messages"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table} "
                            "WHERE artifact_id IS NOT NULL").fetchone()[0] == 1


def test_a_pass_records_its_own_line_and_hands_the_result_on(conn, pipe, tmp_path):
    roots, registry, clf = pipe
    seen = []
    newest = {"iMessage": at(5, 8)}
    src = texts_source(
        read=lambda src_dir, tmp, since: cd.ReadResult([], at(5, 8), newest),
        after_pass=lambda c, r, now, started: seen.append((r.result, r.by_service)))
    r = cd.run_pass(src, conn, roots, registry, clf, src_dir=tmp_path,
                    tmp_root=tmp_path / "tmp", now=at(5, 9), transcriber=no_speech)
    assert r.result == "OK" and seen == [("OK", newest)]
    assert conn.execute("SELECT source_id FROM source_checks").fetchall()[0][0] == \
        "messages:imessage"
    assert conn.execute("SELECT COUNT(*) FROM actions_log "
                        "WHERE action='IMESSAGE_CHECKED'").fetchone()[0] == 1
```

- [ ] **Step 2: Run them to see them fail**

Run: `<python> -m pytest -o addopts="" -p no:cacheprovider tests/test_chatdays.py -q`
Expected: FAIL. `ImportError: cannot import name 'chatdays'`

- [ ] **Step 3: Add the table to `lms/core/db/schema.sql`**

Insert after the `idx_whatsapp_unfiled` index (the end of the `whatsapp_messages` block):

```sql

-- ---------------------------------------------------------------------------
-- imessage_messages — what the iMessage reader has seen (R2, D-081). The same
-- columns as whatsapp_messages, so the chat-day engine's SQL is the same.
-- stanza_id is Messages' own message.guid (a second attachment of one message
-- is "<guid>#1"); chat_jid is chat.chat_identifier, which one person's
-- iMessage and SMS threads share. No message text is kept here.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS imessage_messages (
    stanza_id       TEXT    PRIMARY KEY,
    chat_jid        TEXT    NOT NULL,
    day             TEXT    NOT NULL,          -- YYYY-MM-DD, America/New_York
    scanned_at      TEXT,                      -- hourly urgency check done
    artifact_id     INTEGER REFERENCES artifacts(id),   -- transcript it is filed in
    voice_text      TEXT
);

CREATE INDEX IF NOT EXISTS idx_imessage_unfiled
    ON imessage_messages(chat_jid, day) WHERE artifact_id IS NULL;
```

- [ ] **Step 4: Create `lms/core/pipeline/chatdays.py`**

This is today's `core/pipeline/whatsapp.py` with every WhatsApp-specific value read from `src`. Write it out in full:

```python
"""Chat days, for any chat source: the hourly check and the day's transcript
(R40, R2, D-079, D-081).

    src.read ─> record ─> scan  (hourly: urgent? -> alert)
                     └──> close (day over: transcript -> ingest)

A chat day is one chat's messages on one Eastern-time day. While the day is
open, each hourly pass sends the new lines to the classifier for urgency only
and queues an alert keyed on the chat day. Nothing is filed until the day
closes, at the first pass at or after 00:30 the next day; then the day's
messages become one transcript, filed through ingest_file like an email, with
the same alert key so a chat day already alerted is not alerted again.
Messages synced after their day was filed become a "part 2"; nothing filed is
ever rewritten.

A ChatSource holds everything that differs between sources (WhatsApp,
iMessage): names, its state table, its action rows, how it reads its
database. core/pipeline/whatsapp.py and core/pipeline/imessage.py each hold
one and keep their module-level names.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from ..adapters import asr
from ..adapters.chat import Chat, Message
from ..db import database as db
from . import coverage, notify
from .classify import Artifact
from .ingest import ingest_file

TZ = ZoneInfo("America/New_York")
CLOSE_AT = time(0, 30)
FILED_KINDS = frozenset({"document", "image"})
STALE_AFTER = timedelta(hours=48)       # no message newer: the app is not syncing
TABLES = frozenset({"whatsapp_messages", "imessage_messages"})

Transcriber = Callable[[Path, Path], str]


@dataclass(frozen=True)
class ReadResult:
    """One read of a source's database copy."""
    chats: list[Chat]
    newest: datetime | None                 # the latest message in the copy
    by_service: dict[str, datetime] = field(default_factory=dict)  # iMessage only


@dataclass(frozen=True)
class ChatSource:
    name: str               # Artifact.source, alert-key and source_ref prefix
    label: str              # transcript header and file name
    source_id: str          # the coverage register's id
    table: str              # its state table: one of TABLES, never input
    action: str             # prefix of its action rows
    recipient: str
    read: Callable[[Path, Path, datetime], ReadResult]
    ident: Callable[[str], str]             # how a chat id is shown
    not_synced: str                         # stale wording, with {since}
    after_pass: Callable[..., None] | None = None   # (conn, result, now, started)

    def __post_init__(self):
        if self.table not in TABLES:
            raise ValueError(f"not a chat table: {self.table!r}")

    @property
    def checked(self) -> str:
        return f"{self.action}_CHECKED"

    @property
    def failed(self) -> str:
        return f"{self.action}_FAILED"

    @property
    def day_failed(self) -> str:
        return f"{self.action}_DAY_FAILED"


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def day_of(dt: datetime) -> str:
    return dt.astimezone(TZ).date().isoformat()


def yesterday(now: datetime) -> str:
    """The Eastern calendar day before now's: not 24 hours ago, which is the
    wrong day just after a DST change when now has a fixed offset."""
    return (now.astimezone(TZ).date() - timedelta(days=1)).isoformat()


def closed(day: str, now: datetime) -> bool:
    nxt = date.fromisoformat(day) + timedelta(days=1)
    return now.astimezone(TZ) >= datetime.combine(nxt, CLOSE_AT, TZ)


def chat_label(src: ChatSource, chat: Chat) -> str:
    return chat.name if chat.is_group else f"{chat.name} ({src.ident(chat.jid)})"


def alert_key(src: ChatSource, chat: Chat, day: str) -> str:
    return f"{src.name}:{chat.jid}:{day}"


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
        tag = "voice" if d is None else f"voice {_dur(d)}"
        if d and d > asr.MAX_SECONDS:
            body = f"[{tag}, not transcribed: over 10 minutes]"
        elif voice:
            body = f"[{tag}] {voice}"
        else:
            body = f"[{tag}, not transcribed]"
    else:
        body = m.text or "[other]"
    if m.kind in {"document", "image", "video"} and m.text:
        body += f" {m.text}"
    return f"{m.sent_at.astimezone(TZ):%H:%M} {m.sender_name}: {body}"


def header(src: ChatSource, chat: Chat, day: str, part: int) -> str:
    kind = "group" if chat.is_group else f"direct, {src.ident(chat.jid)}"
    return (f"{src.label} chat: {chat.name} ({kind})\n"
            f"Date: {day} (America/New_York)\nPart: {part}\n\n")


def transcript(src: ChatSource, chat: Chat, day: str, part: int,
               msgs: list[Message], voices: dict[str, str | None]) -> str:
    return header(src, chat, day, part) + "\n".join(
        line(m, voices.get(m.stanza_id)) for m in msgs) + "\n"


# ---------------------------------------------------------------------------
# One pass's tally
# ---------------------------------------------------------------------------

@dataclass
class PassResult:
    source: ChatSource
    alerts: int = 0
    filed: int = 0
    failed: int = 0
    voice_missing: int = 0
    media_missing: int = 0
    messages: int = 0
    stale_since: datetime | None = None     # newest message, when too old
    by_service: dict[str, datetime] = field(default_factory=dict)
    error: str | None = None

    @property
    def result(self) -> str:
        if self.error:
            return "FAILED"
        if (self.failed or self.voice_missing or self.media_missing
                or self.stale_since):
            return "PARTIAL"
        return "OK"

    def summary(self) -> str:
        if self.error:
            return self.error
        s = (f"{self.messages} message(s) seen, {self.filed} chat day(s) filed, "
             f"{self.alerts} alert(s)")
        if self.failed:
            s += f", {self.failed} FAILED (see {self.source.day_failed})"
        if self.voice_missing:
            s += f", {self.voice_missing} voice note(s) not transcribed"
        if self.media_missing:
            s += f", {self.media_missing} file(s) not downloaded on the Mac"
        if self.stale_since:
            s += ", " + self.source.not_synced.format(
                since=f"{self.stale_since.astimezone(TZ):%Y-%m-%d %H:%M}")
        return s


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def record(src: ChatSource, conn: sqlite3.Connection, chats: list[Chat]) -> None:
    conn.executemany(
        f"INSERT OR IGNORE INTO {src.table} (stanza_id, chat_jid, day) "
        "VALUES (?, ?, ?)",
        [(m.stanza_id, c.jid, day_of(m.sent_at)) for c in chats for m in c.messages])
    conn.commit()


def _row(src: ChatSource, conn, stanza_id: str) -> sqlite3.Row:
    return conn.execute(f"SELECT * FROM {src.table} WHERE stanza_id = ?",
                        (stanza_id,)).fetchone()


def _slot(stanza_id: str) -> str:
    """A temp directory name for a message. The id is the sender's client's
    choice ("../../x", an absolute path): never a path segment itself."""
    return hashlib.sha1(stanza_id.encode("utf-8")).hexdigest()[:16]


def voice_text(src: ChatSource, conn: sqlite3.Connection, m: Message,
               tmp_dir: Path, transcriber: Transcriber,
               result: PassResult) -> str | None:
    """The cached transcription, or a new one. None if it cannot be had."""
    cached = _row(src, conn, m.stanza_id)["voice_text"]
    if cached:
        return cached
    if m.media is None or m.media.local_path is None:
        result.voice_missing += 1
        return None
    if (m.media.duration_s or 0) > asr.MAX_SECONDS:
        return None
    copy = Path(tmp_dir) / "voice" / _slot(m.stanza_id) / m.media.local_path.name
    try:
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(m.media.local_path, copy)
        text = transcriber(copy, copy.parent)
    except (asr.ASRUnavailable, OSError):
        result.voice_missing += 1
        return None
    conn.execute(f"UPDATE {src.table} SET voice_text = ? WHERE stanza_id = ?",
                 (text, m.stanza_id))
    conn.commit()
    return text


def _by_day(msgs: list[Message]) -> dict[str, list[Message]]:
    out: dict[str, list[Message]] = {}
    for m in msgs:
        out.setdefault(day_of(m.sent_at), []).append(m)
    return out


def _artifact(src: ChatSource, chat: Chat, day: str, body: str,
              subject: str | None = None) -> Artifact:
    return Artifact(source=src.name, subject=subject or chat.name,
                    sender=chat_label(src, chat), recipient=src.recipient,
                    body=body, received_date=day)


# ---------------------------------------------------------------------------
# The hourly check
# ---------------------------------------------------------------------------

def scan(src: ChatSource, conn: sqlite3.Connection, chats: list[Chat], classifier,
         now: datetime, tmp_dir: Path, transcriber: Transcriber,
         result: PassResult) -> None:
    """Today's and yesterday's unscanned messages, per chat day, to the
    classifier for urgency only. Files nothing.

    A chat day that fails (the model down included) is logged, counted, and
    left unscanned for the next hour; the other chats go on."""
    recent = {day_of(now), yesterday(now)}
    for chat in chats:
        new = [m for m in chat.messages if day_of(m.sent_at) in recent
               and _row(src, conn, m.stanza_id)["scanned_at"] is None]
        for day, msgs in _by_day(new).items():
            try:
                _scan_day(src, conn, chat, day, msgs, classifier, tmp_dir,
                          transcriber, result)
            except Exception as exc:                          # noqa: BLE001
                result.failed += 1
                db.log_action(conn, src.day_failed,
                              detail=f"{chat.jid} {day}: check: "
                                     f"{type(exc).__name__}: {exc}"[:400])
                conn.commit()


def _model_down(c) -> bool:
    """Classifier.classify turns a model error into a review Classification
    with no model name (classify.py); that is "unknown", not "not urgent"."""
    return bool(getattr(c, "needs_review", False)) and getattr(c, "model", None) == ""


def _downloading(m: Message) -> bool:
    """A voice note the app has not downloaded yet: checked again next hour."""
    return m.kind == "voice" and (m.media is None or m.media.local_path is None)


def _scan_day(src: ChatSource, conn, chat: Chat, day: str, msgs: list[Message],
              classifier, tmp_dir: Path, transcriber: Transcriber,
              result: PassResult) -> None:
    voices = {m.stanza_id: voice_text(src, conn, m, tmp_dir, transcriber, result)
              for m in msgs if m.kind == "voice"}
    body = "\n".join(line(m, voices.get(m.stanza_id)) for m in msgs)
    c = classifier.classify(_artifact(src, chat, day, body))
    if _model_down(c):
        raise RuntimeError(c.review_reason or "model error")
    reason = notify.reason_for(c.urgency, c.category)
    if reason and notify.enqueue(
            conn, key=alert_key(src, chat, day), reason=reason,
            text=notify.describe(category=c.category,
                                 counterparty=c.counterparty,
                                 descriptor=c.descriptor,
                                 due_date=c.due_date,
                                 amount_cents=c.amount_cents)):
        result.alerts += 1
    conn.executemany(
        f"UPDATE {src.table} SET scanned_at = ? WHERE stanza_id = ?",
        [(db.now_iso(), m.stanza_id) for m in msgs if not _downloading(m)])
    conn.commit()


def _safe(name: str) -> str:
    return re.sub(r"[^\w\- ]+", "", name).strip()[:60] or "chat"


# ---------------------------------------------------------------------------
# Closing a day
# ---------------------------------------------------------------------------

def _part(src: ChatSource, conn, jid: str, day: str) -> int:
    n = conn.execute(
        f"SELECT COUNT(DISTINCT artifact_id) FROM {src.table} "
        "WHERE chat_jid = ? AND day = ? AND artifact_id IS NOT NULL",
        (jid, day)).fetchone()[0]
    return n + 1


def _media_name(title: str | None, local: Path) -> str:
    """A file name safe to create in the temp dir, from a sender-chosen title."""
    base = Path((title or "").replace("\\", "/")).name
    stem = re.sub(r"[^\w\- ]+", "", Path(base).stem).strip()[:60]
    suffix = re.sub(r"[^\w.]+", "", Path(base).suffix)[:10] or local.suffix
    return f"{stem}{suffix}" if stem else local.name


def _copy(path: Path, dest_dir: Path, name: str) -> Path:
    """A copy to hand to ingest: filing MOVES what it is given (filing.py)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / name
    shutil.copyfile(path, dest)
    return dest


def close(src: ChatSource, conn: sqlite3.Connection, roots, registry, classifier,
          chats: list[Chat], now: datetime, tmp_dir: Path,
          transcriber: Transcriber, result: PassResult, *,
          before: str | None = None, limit: int | None = None,
          ocr: bool = True, ingest=ingest_file) -> int:
    """File every closed chat day's unfiled messages. Returns chat days filed."""
    done = 0
    for chat in chats:
        unfiled = [m for m in chat.messages
                   if _row(src, conn, m.stanza_id)["artifact_id"] is None]
        for day, msgs in sorted(_by_day(unfiled).items()):
            if not closed(day, now) or (before and day >= before):
                continue
            if limit is not None and done >= limit:
                return done
            try:
                _file_day(src, conn, roots, registry, classifier, chat, day, msgs,
                          tmp_dir, transcriber, result, ingest, ocr)
            except Exception as exc:                          # noqa: BLE001
                result.failed += 1
                db.log_action(conn, src.day_failed,
                              detail=f"{chat.jid} {day}: {type(exc).__name__}: {exc}"[:400])
                conn.commit()
                continue
            done += 1
            result.filed += 1
    return done


def _file_day(src: ChatSource, conn, roots, registry, classifier, chat: Chat,
              day: str, msgs: list[Message], tmp_dir: Path,
              transcriber: Transcriber, result: PassResult, ingest,
              ocr: bool) -> None:
    part = _part(src, conn, chat.jid, day)
    voices = {m.stanza_id: voice_text(src, conn, m, tmp_dir, transcriber, result)
              for m in msgs if m.kind == "voice"}
    text = transcript(src, chat, day, part, msgs, voices)
    work = Path(tmp_dir) / "days" / f"{_safe(chat.name)} {day} part {part}"
    work.mkdir(parents=True, exist_ok=True)
    path = work / f"{src.label} {_safe(chat.name)} {day} part {part}.txt"
    path.write_text(text, encoding="utf-8")
    key = alert_key(src, chat, day)
    r = ingest(conn, roots, registry, classifier, path, source=src.name,
               source_ref=f"{src.name}:{chat.jid}:{day}:{part}",
               artifact=_artifact(src, chat, day, text), run_ocr=False,
               alert_key=key)
    parent = int(db.find_artifact_by_hash(conn, r.sha256)["id"])
    failures: list[str] = []
    for m in msgs:
        if m.kind not in FILED_KINDS:
            continue
        if m.media is None or m.media.local_path is None:
            result.media_missing += 1
            continue
        try:
            copy = _copy(m.media.local_path, work / "media" / _slot(m.stanza_id),
                         _media_name(m.media.title, m.media.local_path))
            # A fresh Artifact with an EMPTY body, as mail does: the file's
            # own text fills it in. The caption ("here you go") stays in the
            # transcript; handed down, it would classify the document and
            # defeat ingest_file's emptiness check (D-024).
            ingest(conn, roots, registry, classifier, copy, source="attachment",
                   source_ref=f"{src.name}:{m.stanza_id}", parent_id=parent,
                   artifact=_artifact(src, chat, day, "",
                                      m.media.title or chat_label(src, chat)),
                   run_ocr=ocr, alert_key=key)
        except Exception as exc:                              # noqa: BLE001
            failures.append(f"{m.stanza_id}: {type(exc).__name__}: {exc}")
    if failures:
        # Leave the day unfiled so the next pass retries; ingest_file answers
        # DUPLICATE for what was already filed.
        raise RuntimeError(f"{len(failures)} attachment(s) failed: " + "; ".join(failures))
    conn.executemany(
        f"UPDATE {src.table} SET artifact_id = ?, "
        "scanned_at = COALESCE(scanned_at, ?) WHERE stanza_id = ?",
        [(parent, db.now_iso(), m.stanza_id) for m in msgs])
    conn.commit()


# ---------------------------------------------------------------------------
# One pass
# ---------------------------------------------------------------------------

def read_chats(src: ChatSource, src_dir: Path, tmp: Path,
               since: datetime) -> list[Chat]:
    return src.read(src_dir, tmp, since).chats


def run_pass(src: ChatSource, conn: sqlite3.Connection, roots, registry,
             classifier, *, src_dir: Path, tmp_root: Path, now: datetime,
             transcriber: Transcriber = asr.transcribe, since_days: int = 3,
             ingest=ingest_file) -> PassResult:
    """Read, check the hour, close the days; record the pass however it ends.

    since_days covers yesterday's closing and late-synced messages; older
    days are the backfill's (ops/_backfill.py)."""
    started = db.now_iso()
    result = PassResult(src)
    Path(tmp_root).mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="pass-", dir=tmp_root))
    try:
        got = src.read(src_dir, tmp, now - timedelta(days=since_days))
        chats = got.chats
        result.messages = sum(len(c.messages) for c in chats)
        result.by_service = dict(got.by_service)
        if got.newest is not None and now - got.newest > STALE_AFTER:
            # Not a quiet two days: the app is closed, signed out or not syncing.
            result.stale_since = got.newest
        record(src, conn, chats)
        scan(src, conn, chats, classifier, now, tmp, transcriber, result)
        close(src, conn, roots, registry, classifier, chats, now, tmp,
              transcriber, result, ingest=ingest)
    except Exception as exc:                                  # noqa: BLE001
        result.error = f"{type(exc).__name__}: {exc}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    coverage.record_check(conn, src.source_id, result.result, started_at=started,
                          items=result.messages, detail=result.summary())
    db.log_action(conn, src.failed if result.error else src.checked,
                  detail=result.summary()[:400])
    if src.after_pass is not None:
        src.after_pass(conn, result, now, started)
    conn.commit()
    return result


def plan_days(src: ChatSource, conn: sqlite3.Connection, chats: list[Chat],
              now: datetime) -> list[tuple[str, str, int, bool]]:
    """For --dry-run: unfiled messages per chat day. Writes nothing."""
    out = []
    for chat in chats:
        unfiled = []
        for m in chat.messages:
            row = _row(src, conn, m.stanza_id)
            if row is None or row["artifact_id"] is None:
                unfiled.append(m)
        for day, msgs in sorted(_by_day(unfiled).items()):
            out.append((chat_label(src, chat), day, len(msgs), closed(day, now)))
    return out
```

- [ ] **Step 5: Rewrite `lms/core/pipeline/whatsapp.py` as the thin module**

Replace the whole file:

```python
"""WhatsApp chat days (R40, D-079): the chat-day engine with WhatsApp's
settings.

The engine is core/pipeline/chatdays.py (D-081): the hourly check, closing
a day, the pass. This module holds WhatsApp's ChatSource and keeps the names
ops/whatsapp.py, ops/backfill_whatsapp.py and the tests use.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from functools import partial
from pathlib import Path

from ..adapters import asr
from ..adapters import whatsapp as wa
from ..adapters.chat import Chat, Message
from . import chatdays as cd
from .chatdays import (CLOSE_AT, FILED_KINDS, STALE_AFTER, TZ,  # noqa: F401
                       Transcriber, _media_name, _slot, closed, day_of, line,
                       yesterday)
from .ingest import ingest_file

SOURCE = "whatsapp"
SOURCE_ID = "chat:whatsapp"
RECIPIENT = "Matthew (WhatsApp)"
CHECKED = "WHATSAPP_CHECKED"
FAILED = "WHATSAPP_FAILED"


def _read(src_dir: Path, tmp: Path, since: datetime) -> cd.ReadResult:
    """The chats since `since`, and the newest message time in the copy."""
    copy = wa.snapshot(src_dir, tmp / "db")
    rconn = sqlite3.connect(copy)
    try:
        missing = wa.check_schema(rconn)
        if missing:
            raise wa.WhatsAppError("schema changed, missing: " + ", ".join(missing))
        # His own chat holds the /lms commands and the LMS's replies (D-080):
        # never read back in.
        return cd.ReadResult(
            wa.read(rconn, since, Path(src_dir) / wa.MEDIA_DIR,
                    skip=wa.own_chats(rconn)),
            wa.newest(rconn))
    finally:
        rconn.close()


CHAT_SOURCE = cd.ChatSource(
    name=SOURCE, label="WhatsApp", source_id=SOURCE_ID,
    table="whatsapp_messages", action="WHATSAPP", recipient=RECIPIENT,
    read=_read, ident=wa.number,
    not_synced="WhatsApp on the Mac has not synced since {since} (is it running?)")

PassResult = partial(cd.PassResult, CHAT_SOURCE)


def chat_label(chat: Chat) -> str:
    return cd.chat_label(CHAT_SOURCE, chat)


def alert_key(chat: Chat, day: str) -> str:
    return cd.alert_key(CHAT_SOURCE, chat, day)


def header(chat: Chat, day: str, part: int) -> str:
    return cd.header(CHAT_SOURCE, chat, day, part)


def transcript(chat: Chat, day: str, part: int, msgs: list[Message],
               voices: dict[str, str | None]) -> str:
    return cd.transcript(CHAT_SOURCE, chat, day, part, msgs, voices)


def record(conn: sqlite3.Connection, chats: list[Chat]) -> None:
    cd.record(CHAT_SOURCE, conn, chats)


def voice_text(conn: sqlite3.Connection, m: Message, tmp_dir: Path,
               transcriber: Transcriber, result: cd.PassResult) -> str | None:
    return cd.voice_text(CHAT_SOURCE, conn, m, tmp_dir, transcriber, result)


def scan(conn: sqlite3.Connection, chats: list[Chat], classifier, now: datetime,
         tmp_dir: Path, transcriber: Transcriber, result: cd.PassResult) -> None:
    cd.scan(CHAT_SOURCE, conn, chats, classifier, now, tmp_dir, transcriber, result)


def close(conn: sqlite3.Connection, roots, registry, classifier,
          chats: list[Chat], now: datetime, tmp_dir: Path,
          transcriber: Transcriber, result: cd.PassResult, *,
          before: str | None = None, limit: int | None = None,
          ocr: bool = True, ingest=ingest_file) -> int:
    return cd.close(CHAT_SOURCE, conn, roots, registry, classifier, chats, now,
                    tmp_dir, transcriber, result, before=before, limit=limit,
                    ocr=ocr, ingest=ingest)


def read_chats(src_dir: Path, tmp: Path, since: datetime) -> list[Chat]:
    return cd.read_chats(CHAT_SOURCE, src_dir, tmp, since)


def run_pass(conn: sqlite3.Connection, roots, registry, classifier, *,
             src_dir: Path, tmp_root: Path, now: datetime,
             transcriber: Transcriber = asr.transcribe, since_days: int = 3,
             ingest=ingest_file) -> cd.PassResult:
    return cd.run_pass(CHAT_SOURCE, conn, roots, registry, classifier,
                       src_dir=src_dir, tmp_root=tmp_root, now=now,
                       transcriber=transcriber, since_days=since_days,
                       ingest=ingest)


def plan_days(conn: sqlite3.Connection, chats: list[Chat],
              now: datetime) -> list[tuple[str, str, int, bool]]:
    return cd.plan_days(CHAT_SOURCE, conn, chats, now)
```

- [ ] **Step 6: Move the hard-stop exemption with the code**

In `lms/tests/test_hard_stops.py`, replace the `("pipeline/whatsapp.py", "shutil.rmtree")` entry with:

```python
    ("pipeline/chatdays.py", "shutil.rmtree"): (
        "run_pass removes only its own scratch directory: the exact path "
        "tempfile.mkdtemp returned under tmp_root in the same call. That "
        "directory holds copies only: the database snapshot (WhatsApp's "
        "ChatStorage.sqlite, Messages' chat.db, Contacts), the voice and "
        "media copies, and transcript files whose originals filing already "
        "moved into the archive or _originals. It never touches the archive, "
        "_originals, the WhatsApp container or ~/Library/Messages, which are "
        "read-only by construction (D-079, D-081). Leaving it would grow disk "
        "use every hour and keep plaintext copies of messages on disk."
    ),
```

`test_every_exemption_is_still_used` would otherwise fail: `pipeline/whatsapp.py` no longer calls `shutil.rmtree`.

- [ ] **Step 7: Run the engine tests, the unchanged WhatsApp tests, and the guards**

Run: `<python> -m pytest -o addopts="" -p no:cacheprovider tests/test_chatdays.py tests/test_whatsapp.py tests/test_whatsapp_adapter.py tests/test_hard_stops.py tests/test_stalled_jobs.py tests/test_notify.py -q`
Expected: all pass. `git diff --stat lms/tests/test_whatsapp.py lms/tests/test_whatsapp_adapter.py` prints nothing.

- [ ] **Step 8: Run the whole suite**

Run: the whole-suite command (Global Constraints).
Expected: 1252 + 4 (Task 1) + 7 (this task) passed, 1 skipped, 1 xfailed.

- [ ] **Step 9: Commit**

```bash
git add lms/core/pipeline/chatdays.py lms/core/pipeline/whatsapp.py lms/core/db/schema.sql lms/tests/test_chatdays.py lms/tests/test_hard_stops.py
git commit -m "D-081: the WhatsApp pipeline becomes a chat-day engine any source can drive"
```

---

### Task 3: The iMessage adapter: schema, dates, and the attributedBody decoder

**Files:**
- Create: `lms/core/adapters/imessage.py` (the first half: constants, `IMessageError`, `snapshot`, `check_schema`, `apple_date`, `decode_attributed_body`)
- Create: `lms/tests/im_fixture.py`
- Test: `lms/tests/test_imessage_adapter.py`

**Interfaces:**
- Consumes: `core.adapters.chat.{ChatDBError, snapshot}`.
- Produces:
  - Constants: `CONTAINER`, `DB_NAME = "chat.db"`, `CONTACTS_ROOT`, `CONTACTS_DB = "AddressBook-v22.abcddb"`, `GROUP_STYLE = 43`, `ME = "Matthew"`, `APPLE_EPOCH`, `SECONDS_LIMIT = 10**11`, `UNREADABLE = "[message not readable]"`, `REQUIRED`.
  - `class IMessageError(ChatDBError)`.
  - `snapshot(src_dir, tmp_dir, attempts=3) -> Path`.
  - `check_schema(conn) -> list[str]`.
  - `apple_date(value: int) -> datetime`.
  - `decode_attributed_body(blob: bytes | None) -> str | None`.
- Produces, in `tests/im_fixture.py`:
  - `IM(root)` with methods `.handle(ident, service="iMessage") -> int`, `.chat(identifier, *, group=False, name=None, members=(), service="iMessage") -> int`, `.attachment(name, *, mime=None, content=b"x", on_disk=True) -> int`, `.message(chat_id, when, text=None, *, from_me=False, handle=None, service="iMessage", guid=None, body=None, reaction=0, item_type=0, audio=False, seconds=False, attachments=()) -> str` and `.close()`. `IM.root` is the Messages folder; `IM.conn` is its connection.
  - `contacts(root, *people) -> Path`, `attributed(text) -> bytes`, `PREFIX`.

- [ ] **Step 1: Write the fixture `lms/tests/im_fixture.py`**

```python
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
```

- [ ] **Step 2: Write the failing tests**

`lms/tests/test_imessage_adapter.py` (Task 4 appends to it):

```python
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
```

- [ ] **Step 3: Run them to see them fail**

Run: `<python> -m pytest -o addopts="" -p no:cacheprovider tests/test_imessage_adapter.py -q`
Expected: FAIL. `ImportError: cannot import name 'imessage' from 'core.adapters'`

- [ ] **Step 4: Create `lms/core/adapters/imessage.py`, first half**

```python
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
```

(`os`, `Chat`, `Media` and `Message` are first used in Task 4. Leave the imports in place.)

- [ ] **Step 5: Run the tests**

Run: `<python> -m pytest -o addopts="" -p no:cacheprovider tests/test_imessage_adapter.py -q`
Expected: 18 passed (4 + 10 parametrised, then 1 date test, 2 schema tests, 1 snapshot test).

- [ ] **Step 6: Commit**

```bash
git add lms/core/adapters/imessage.py lms/tests/im_fixture.py lms/tests/test_imessage_adapter.py
git commit -m "D-081: iMessage adapter reads chat.db's schema, dates and attributedBody text"
```

---

### Task 4: The iMessage adapter: Contacts, chats and messages, and the probe

**Files:**
- Modify: `lms/core/adapters/imessage.py` (append)
- Test: `lms/tests/test_imessage_adapter.py` (append)

**Interfaces:**
- Consumes: Task 3's names, plus `Chat`, `Media` and `Message`.
- Produces:
  - `phone_key(number: str) -> str | None`
  - `name_for(names: dict[str, str], ident: str) -> str`
  - `contact_names(root: Path, tmp: Path) -> dict[str, str]`
  - `kind_of(mime: str | None, is_audio: bool) -> str`
  - `read(conn, since: datetime, names: dict[str, str] | None = None) -> list[Chat]`
  - `newest(conn) -> datetime | None`
  - `newest_by_service(conn) -> dict[str, datetime]`
  - `probe(conn) -> dict` with the keys `missing`, `tables`, `counts`, `services`, `styles`, `attachment_types`, `reactions`, `system`, `attributed` (needed, decoded), `shared_identifiers`, `files` (found, filled) and `newest`.

- [ ] **Step 1: Append the failing tests to `lms/tests/test_imessage_adapter.py`**

```python
# --- Contacts ------------------------------------------------------------------

def test_contacts_name_by_last_10_digits_and_by_case_folded_email(tmp_path):
    root = contacts(tmp_path / "ab",
                    {"first": "Rhonda", "last": "Eps",
                     "phones": ["(555) 010-0100"], "emails": ["Rhonda@Example.com"]},
                    {"org": "Acme Supply", "phones": ["+1 555 010 0199"]})
    names = im.contact_names(root, tmp_path / "tmp")
    assert im.name_for(names, "+15550100100") == "Rhonda Eps"
    assert im.name_for(names, "rhonda@EXAMPLE.com") == "Rhonda Eps"
    assert im.name_for(names, "+15550100199") == "Acme Supply"
    assert im.name_for(names, "+15550100777") == "+15550100777"
    assert im.name_for(names, "12345") == "12345"


def test_unreadable_or_missing_contacts_fall_back_to_numbers(tmp_path):
    bad = tmp_path / "ab" / "Sources" / "X"
    bad.mkdir(parents=True)
    (bad / "AddressBook-v22.abcddb").write_bytes(b"not a database" * 50)
    assert im.contact_names(tmp_path / "ab", tmp_path / "tmp") == {}
    assert im.contact_names(tmp_path / "nowhere", tmp_path / "tmp") == {}


# --- chats and messages --------------------------------------------------------

def read(box, names=None, since=SINCE):
    return im.read(box.conn, since, names or {})


def test_a_direct_chat_reads_text_and_names_both_sides(box):
    h = box.handle("+15550100100")
    c = box.chat("+15550100100")
    box.message(c, T, "Can you call me?", handle=h, guid="g1")
    box.message(c, T + timedelta(minutes=1), "Yes at 5", from_me=True, guid="g2")
    [chat] = read(box, {"5550100100": "Rhonda Eps"})
    assert (chat.jid, chat.name, chat.is_group) == ("+15550100100", "Rhonda Eps", False)
    assert [(m.stanza_id, m.sender_name, m.text, m.kind, m.from_me)
            for m in chat.messages] == [
        ("g1", "Rhonda Eps", "Can you call me?", "text", False),
        ("g2", "Matthew", "Yes at 5", "text", True)]
    assert chat.messages[0].sent_at == T


def test_text_kept_only_in_attributed_body_is_read(box):
    h = box.handle("+15550100100")
    c = box.chat("+15550100100")
    box.message(c, T, None, body=attributed("Invoice attached, due Friday"),
                handle=h, guid="a")
    box.message(c, T, None, body=b"\x04\x0bstreamtyped garbage", handle=h, guid="b")
    assert [m.text for m in read(box)[0].messages] == \
        ["Invoice attached, due Friday", "[message not readable]"]


def test_reactions_system_rows_and_empty_rows_are_not_conversation(box):
    h = box.handle("+15550100100")
    c = box.chat("+15550100100")
    box.message(c, T, "hello", handle=h, guid="keep")
    box.message(c, T, "Loved “hello”", handle=h, reaction=2000, guid="tapback")
    box.message(c, T, None, item_type=2, guid="renamed")
    box.message(c, T, None, handle=h, guid="empty")
    assert [m.stanza_id for m in read(box)[0].messages] == ["keep"]


def test_one_persons_imessage_and_sms_threads_are_one_chat(box):
    h1 = box.handle("+15550100100")
    h2 = box.handle("+15550100100", service="SMS")
    c1 = box.chat("+15550100100")
    c2 = box.chat("+15550100100", service="SMS")
    box.message(c1, T, "over iMessage", handle=h1)
    box.message(c2, T + timedelta(minutes=1), "over SMS", handle=h2, service="SMS")
    [chat] = read(box)
    assert [m.text for m in chat.messages] == ["over iMessage", "over SMS"]


def test_groups_are_named_and_their_senders_too(box):
    a = box.handle("+15550100100")
    b = box.handle("dee@example.com")
    d = box.handle("+15550100300")
    e = box.handle("+15550100400")
    named = box.chat("chat111", group=True, name="Board", members=(a, b))
    unnamed = box.chat("chat222", group=True, members=(a, b, d, e))
    box.message(named, T, "Agenda?", handle=b)
    box.message(unnamed, T, "hi all", handle=a)
    names = {"5550100100": "Rhonda Eps", "dee@example.com": "Dee"}
    chats = {c.jid: c for c in read(box, names)}
    assert chats["chat111"].name == "Board" and chats["chat111"].is_group
    assert chats["chat111"].messages[0].sender_name == "Dee"
    assert chats["chat222"].name == "Group with Rhonda Eps, Dee, +15550100300"


def test_attachments_each_become_a_line_of_their_kind(box):
    h = box.handle("+15550100100")
    c = box.chat("+15550100100")
    img = box.attachment("IMG_1.HEIC", mime="image/heic")
    pdf = box.attachment("INV-2291.pdf", mime="application/pdf", content=b"%PDF")
    box.message(c, T, "\ufffc\ufffcHere you go", handle=h, guid="m",
                attachments=(img, pdf))
    vid = box.attachment("clip.mov", mime="video/quicktime")
    voice = box.attachment("Audio Message.caf", mime="audio/x-caf")
    gone = box.attachment("scan.pdf", mime="application/pdf", on_disk=False)
    odd = box.attachment("card.vcf", mime=None)
    box.message(c, T, None, handle=h, guid="v", attachments=(vid,))
    box.message(c, T, None, handle=h, guid="a", audio=True, attachments=(voice,))
    box.message(c, T, None, handle=h, guid="g", attachments=(gone,))
    box.message(c, T, None, handle=h, guid="o", attachments=(odd,))
    msgs = read(box)[0].messages
    assert [(m.stanza_id, m.kind, m.text) for m in msgs] == [
        ("m", "image", "Here you go"), ("m#1", "document", None),
        ("v", "video", None), ("a", "voice", None), ("g", "document", None),
        ("o", "other", "[file: card.vcf]")]
    assert msgs[1].media.local_path.read_bytes() == b"%PDF"
    assert msgs[1].media.title == "INV-2291.pdf"
    assert msgs[4].media.local_path is None and msgs[4].media.title == "scan.pdf"
    assert all(m.media.duration_s is None for m in msgs)


@pytest.mark.parametrize("mime,audio,kind", [
    ("image/jpeg", False, "image"), ("video/mp4", False, "video"),
    ("application/pdf", False, "document"), ("text/plain", False, "document"),
    ("audio/x-caf", True, "voice"), ("audio/mpeg", False, "other"),
    (None, False, "other")])
def test_kinds_come_from_the_mime_type(mime, audio, kind):
    assert im.kind_of(mime, audio) == kind


def test_old_rows_in_seconds_and_new_rows_in_nanoseconds_are_both_read(box):
    h = box.handle("+15550100100")
    c = box.chat("+15550100100")
    box.message(c, datetime(2015, 3, 1, 12, tzinfo=UTC), "from 2015", handle=h,
                seconds=True, guid="old")
    box.message(c, SINCE - timedelta(days=1), "before the window", handle=h,
                guid="before")
    box.message(c, T, "from 2026", handle=h, guid="new")
    assert [m.stanza_id for m in read(box)[0].messages] == ["new"]
    everything = read(box, since=datetime(2015, 1, 1, tzinfo=UTC))[0].messages
    assert [m.stanza_id for m in everything] == ["old", "before", "new"]
    assert everything[0].sent_at == datetime(2015, 3, 1, 12, tzinfo=UTC)


def test_newest_overall_and_per_service(box):
    h = box.handle("+15550100100")
    c1 = box.chat("+15550100100")
    c2 = box.chat("+15550100100", service="SMS")
    box.message(c1, T, "imsg", handle=h)
    box.message(c2, T + timedelta(hours=1), "sms", handle=h, service="SMS")
    assert im.newest(box.conn) == T + timedelta(hours=1)
    assert im.newest_by_service(box.conn) == {"iMessage": T,
                                              "SMS": T + timedelta(hours=1)}


def test_the_probe_counts_and_never_shows_content(box):
    h = box.handle("+15550100100")
    c = box.chat("+15550100100")
    g = box.chat("chat333", group=True, name="Secret Board", members=(h,))
    box.message(c, T, "SECRET-TEXT", handle=h)
    box.message(c, T, None, body=attributed("SECRET-BODY"), handle=h)
    box.message(c, T, None, body=b"NSString junk", handle=h)
    box.message(c, T, "Loved it", handle=h, reaction=2000)
    box.message(g, T, "hi", handle=h, service="SMS",
                attachments=(box.attachment("x.pdf", mime="application/pdf"),))
    p = im.probe(box.conn)
    assert p["missing"] == []
    assert p["services"] == {"iMessage": 4, "SMS": 1}
    assert p["styles"] == {43: 1, 45: 1}
    assert p["attachment_types"] == {"application": 1}
    assert (p["reactions"], p["system"]) == (1, 0)
    assert p["attributed"] == (2, 1)
    assert p["files"] == (1, 1)
    assert p["shared_identifiers"] == 0
    shown = repr(p)
    for secret in ("SECRET", "+15550100100", "Secret Board", "x.pdf", "chat333"):
        assert secret not in shown
```

- [ ] **Step 2: Run them to see them fail**

Run: `<python> -m pytest -o addopts="" -p no:cacheprovider tests/test_imessage_adapter.py -q`
Expected: FAIL with `AttributeError: module 'core.adapters.imessage' has no attribute 'contact_names'` (and the same for the other new names).

- [ ] **Step 3: Append the second half to `lms/core/adapters/imessage.py`**

```python
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
    return (text or "").replace("\ufffc", "").strip() or None


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
```

- [ ] **Step 4: Run the adapter tests**

Run: `<python> -m pytest -o addopts="" -p no:cacheprovider tests/test_imessage_adapter.py -q`
Expected: 36 passed (Task 3's 18; then 2 Contacts tests, 6 read tests, 7 parametrised kinds, and the seconds, newest and probe tests).

- [ ] **Step 5: Commit**

```bash
git add lms/core/adapters/imessage.py lms/tests/test_imessage_adapter.py
git commit -m "D-081: iMessage adapter reads chats, attachments and Contacts names; probe shows counts only"
```

---

### Task 5: iMessage's settings, the SMS line, and the brief

**Files:**
- Create: `lms/core/pipeline/imessage.py`
- Modify: `lms/core/reports/brief.py` (`WATCHED_JOBS` around line 454, `JOB_FAILURES` around line 467)
- Test: `lms/tests/test_imessage.py`

**Interfaces:**
- Consumes:
  - `core.pipeline.chatdays.{ChatSource, ReadResult, PassResult, run_pass, record, close, read_chats, plan_days, day_of, yesterday, TZ}` (Task 2).
  - `core.adapters.imessage.{snapshot, check_schema, read, newest, newest_by_service, contact_names, CONTACTS_ROOT, IMessageError}` (Tasks 3–4).
- Produces, in `core.pipeline.imessage`:
  - Constants `SOURCE`, `SOURCE_ID`, `SMS_ID = "messages:sms"`, `RECIPIENT`, `CHECKED`, `FAILED`, `SMS_QUIET`, `CHAT_SOURCE` and `PassResult`.
  - `sms_line(result, now) -> tuple[str, str]`.
  - The same signatures as WhatsApp's: `record(conn, chats)`, `close(conn, roots, registry, classifier, chats, now, tmp_dir, transcriber, result, *, before=None, limit=None, ocr=True, ingest=ingest_file) -> int`, `read_chats(src_dir, tmp, since)`, `run_pass(conn, roots, registry, classifier, *, src_dir, tmp_root, now, transcriber=asr.transcribe, since_days=3, ingest=ingest_file)` and `plan_days(conn, chats, now)`.
  - Re-exports `day_of` and `yesterday`.

- [ ] **Step 1: Write the failing tests**

`lms/tests/test_imessage.py`:

```python
"""iMessage chat days: the pass, the SMS line, the brief (R2, D-081)."""

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from core.adapters import asr
from core.adapters import imessage as im
from core.adapters.lmstudio import Completion
from core.db import database as db
from core.pipeline import filing
from core.pipeline import imessage as i
from core.pipeline.classify import Classifier
from core.pipeline.registry import load_registry
from core.reports import brief
from tests.im_fixture import IM, contacts

ET = ZoneInfo("America/New_York")
CONFIG = Path(__file__).resolve().parents[1] / "config"
NOW = datetime(2026, 11, 7, 12, tzinfo=ET)


class FakeModel:
    def complete(self, **kw):
        return Completion(text=json.dumps({
            "domain": "BUSINESS", "entity_id": "B_MRE", "category": "VENDORS",
            "subcategory": "invoices-received", "urgency": "NORMAL",
            "confidence": 0.93, "requires_reply": False, "due_date": None,
            "counterparty": "acme-supply", "descriptor": "invoice chat",
            "amount_cents": None, "rationale": "vendor"}),
            model="fake", elapsed_s=0.01, finish_reason="stop")


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "lms.db")
    yield c
    c.close()


@pytest.fixture
def pipe(tmp_path):
    roots = filing.StorageRoots(archive=tmp_path / "archive",
                                originals=tmp_path / "_originals",
                                quarantine=tmp_path / "quarantine")
    roots.ensure()
    registry = load_registry(CONFIG)
    return roots, registry, Classifier(registry, client=FakeModel())


@pytest.fixture(autouse=True)
def no_contacts(tmp_path, monkeypatch):
    monkeypatch.setattr(im, "CONTACTS_ROOT", tmp_path / "no-contacts")


def no_speech(path, tmp):
    raise asr.ASRUnavailable("not in tests")


class Seeing:
    def __init__(self, real):
        self.real, self.seen = real, []

    def classify(self, art):
        self.seen.append(art)
        return self.real.classify(art)


def build(tmp_path, when, service="iMessage"):
    box = IM(tmp_path / "Messages")
    h = box.handle("+15550100100", service=service)
    c = box.chat("+15550100100", service=service)
    box.message(c, when, "Can you resend the invoice?", from_me=True, service=service)
    box.message(c, when + timedelta(minutes=5), "Sent it", handle=h, service=service)
    box.close()
    return box.root


def checks(conn):
    return dict(conn.execute("SELECT source_id, result FROM source_checks").fetchall())


def test_a_whole_pass_files_the_day_under_imessage(conn, pipe, tmp_path, monkeypatch):
    roots, registry, real = pipe
    monkeypatch.setattr(im, "CONTACTS_ROOT", contacts(
        tmp_path / "ab", {"first": "Aunt", "last": "Rhonda", "phones": ["555-010-0100"]}))
    now = datetime.now(ET)
    src = build(tmp_path, (now - timedelta(days=1)).replace(hour=9, minute=0))
    clf = Seeing(real)
    r = i.run_pass(conn, roots, registry, clf, src_dir=src, tmp_root=tmp_path / "tmp",
                   now=now.replace(hour=1, minute=0), transcriber=no_speech)
    assert r.error is None and r.filed == 1 and r.result == "OK"
    filed = clf.seen[-1]
    assert filed.body.splitlines()[0] == "iMessage chat: Aunt Rhonda (direct, +15550100100)"
    assert filed.recipient == "Matthew (iMessage)"
    assert "Aunt Rhonda: Sent it" in filed.body
    [ref] = [r[0] for r in conn.execute(
        "SELECT source_ref FROM artifacts WHERE source='imessage'")]
    assert ref.startswith("imessage:+15550100100:") and ref.endswith(":1")
    assert checks(conn) == {"messages:imessage": "OK", "messages:sms": "PARTIAL"}
    assert conn.execute("SELECT COUNT(*) FROM imessage_messages").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM whatsapp_messages").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM actions_log "
                        "WHERE action='IMESSAGE_CHECKED'").fetchone()[0] == 1
    assert list((tmp_path / "tmp").iterdir()) == [], "the copy was left behind"


def test_messages_signed_out_is_partial_with_the_sign_in_hint(conn, pipe, tmp_path):
    roots, registry, clf = pipe
    src = build(tmp_path, datetime(2026, 10, 5, 9, tzinfo=ET))
    r = i.run_pass(conn, roots, registry, clf, src_dir=src, tmp_root=tmp_path / "tmp",
                   now=NOW, transcriber=no_speech)
    assert r.result == "PARTIAL"
    assert ("Messages on the Mac has not synced since 2026-10-05 09:05 "
            "(is it signed in?)") in r.summary()
    assert checks(conn) == {"messages:imessage": "PARTIAL", "messages:sms": "OK"}


def test_a_changed_schema_fails_both_lines_loudly(conn, pipe, tmp_path):
    roots, registry, clf = pipe
    src = build(tmp_path, datetime(2026, 11, 4, 9, tzinfo=ET))
    raw = sqlite3.connect(src / "chat.db")
    raw.execute("ALTER TABLE message RENAME COLUMN attributedBody TO body")
    raw.commit()
    raw.close()
    r = i.run_pass(conn, roots, registry, clf, src_dir=src, tmp_root=tmp_path / "tmp",
                   now=datetime(2026, 11, 5, 1, tzinfo=ET), transcriber=no_speech)
    assert r.result == "FAILED" and "message.attributedBody" in r.error
    assert conn.execute("SELECT detail FROM actions_log WHERE action='IMESSAGE_FAILED'"
                        ).fetchone()[0].startswith("IMessageError")
    assert checks(conn) == {"messages:imessage": "FAILED", "messages:sms": "FAILED"}


def test_no_chat_db_fails_the_pass(conn, pipe, tmp_path):
    roots, registry, clf = pipe
    r = i.run_pass(conn, roots, registry, clf, src_dir=tmp_path / "empty",
                   tmp_root=tmp_path / "tmp", now=NOW, transcriber=no_speech)
    assert r.result == "FAILED" and "no chat.db" in r.error


# --- the SMS line ----------------------------------------------------------------

def result(**by):
    r = i.PassResult()
    r.by_service = by
    return r


def test_the_sms_line_is_ok_while_texts_arrive():
    assert i.sms_line(result(iMessage=NOW, SMS=NOW - timedelta(days=6)), NOW)[0] == "OK"
    assert i.sms_line(result(iMessage=NOW, RCS=NOW), NOW)[0] == "OK"


def test_the_sms_line_is_partial_when_only_imessage_arrives():
    state, detail = i.sms_line(
        result(iMessage=NOW - timedelta(hours=2), SMS=datetime(2026, 10, 5, 9, tzinfo=ET)),
        NOW)
    assert state == "PARTIAL"
    assert detail == ("no SMS or RCS since 2026-10-05 while iMessage arrives: "
                      "is Text Message Forwarding on for this Mac?")
    state, detail = i.sms_line(result(iMessage=NOW), NOW)
    assert state == "PARTIAL" and detail.startswith("no SMS or RCS in this Mac's Messages")


def test_the_sms_line_is_ok_when_the_whole_database_is_stale():
    r = result(iMessage=NOW - timedelta(days=30))
    r.stale_since = NOW - timedelta(days=30)
    assert i.sms_line(r, NOW)[0] == "OK"


def test_the_sms_line_fails_with_the_pass():
    assert i.sms_line(i.PassResult(error="IMessageError: x"), NOW) == \
        ("FAILED", "IMessageError: x")


# --- the brief -------------------------------------------------------------------

def test_every_action_the_brief_reads_is_one_this_job_writes():
    """The D-027 trap, as in test_whatsapp.py."""
    assert brief.JOB_FAILURES[i.FAILED] == i.CHECKED
    assert brief.WATCHED_JOBS[i.CHECKED] == ("imessage", 3)
    assert (i.CHAT_SOURCE.checked, i.CHAT_SOURCE.failed) == (i.CHECKED, i.FAILED)
```

- [ ] **Step 2: Run them to see them fail**

Run: `<python> -m pytest -o addopts="" -p no:cacheprovider tests/test_imessage.py -q`
Expected: FAIL. `ImportError: cannot import name 'imessage' from 'core.pipeline'`

- [ ] **Step 3: Create `lms/core/pipeline/imessage.py`**

```python
"""iMessage and texts (R2, D-081): the chat-day engine with iMessage's
settings.

Reads a copy of Messages' chat.db on the Mac (core/adapters/imessage.py),
with names from a copy of Contacts. The engine (core/pipeline/chatdays.py)
checks each hour and files each chat day, as for WhatsApp.

After every pass the SMS line of the coverage register (messages:sms) is
recorded too, from the same read. SMS and RCS reach the Mac only while Text
Message Forwarding is on for it, so "iMessage arrives and no SMS does" is
the sign it is off.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from functools import partial
from pathlib import Path

from ..adapters import asr
from ..adapters import imessage as im
from ..adapters.chat import Chat
from . import chatdays as cd
from . import coverage
from .chatdays import Transcriber, day_of, yesterday  # noqa: F401
from .ingest import ingest_file

SOURCE = "imessage"
SOURCE_ID = "messages:imessage"
SMS_ID = "messages:sms"
RECIPIENT = "Matthew (iMessage)"
CHECKED = "IMESSAGE_CHECKED"
FAILED = "IMESSAGE_FAILED"
SMS_QUIET = timedelta(days=7)
TEXT_SERVICES = ("SMS", "RCS")


def _read(src_dir: Path, tmp: Path, since: datetime) -> cd.ReadResult:
    copy = im.snapshot(src_dir, tmp / "db")
    names = im.contact_names(im.CONTACTS_ROOT, tmp / "contacts")
    rconn = sqlite3.connect(copy)
    try:
        missing = im.check_schema(rconn)
        if missing:
            raise im.IMessageError("schema changed, missing: " + ", ".join(missing))
        return cd.ReadResult(im.read(rconn, since, names), im.newest(rconn),
                             im.newest_by_service(rconn))
    finally:
        rconn.close()


def sms_line(result: cd.PassResult, now: datetime) -> tuple[str, str]:
    """messages:sms for this pass: (result, detail)."""
    if result.error:
        return "FAILED", result.error
    if result.stale_since:
        return "OK", "Messages on the Mac is not syncing: see messages:imessage"
    texts = [result.by_service[s] for s in TEXT_SERVICES if s in result.by_service]
    last_text = max(texts) if texts else None
    last_im = result.by_service.get("iMessage")
    if last_text and now - last_text <= SMS_QUIET:
        return "OK", f"newest SMS or RCS {last_text.astimezone(cd.TZ):%Y-%m-%d %H:%M}"
    if last_im and now - last_im <= SMS_QUIET:
        since = (f"since {last_text.astimezone(cd.TZ):%Y-%m-%d}" if last_text
                 else "in this Mac's Messages")
        return "PARTIAL", (f"no SMS or RCS {since} while iMessage arrives: "
                           "is Text Message Forwarding on for this Mac?")
    return "OK", "no recent iMessage, SMS or RCS"


def _after_pass(conn: sqlite3.Connection, result: cd.PassResult, now: datetime,
                started: str) -> None:
    state, detail = sms_line(result, now)
    coverage.record_check(conn, SMS_ID, state, started_at=started, detail=detail)


CHAT_SOURCE = cd.ChatSource(
    name=SOURCE, label="iMessage", source_id=SOURCE_ID,
    table="imessage_messages", action="IMESSAGE", recipient=RECIPIENT,
    read=_read, ident=str,
    not_synced="Messages on the Mac has not synced since {since} (is it signed in?)",
    after_pass=_after_pass)

PassResult = partial(cd.PassResult, CHAT_SOURCE)


def record(conn: sqlite3.Connection, chats: list[Chat]) -> None:
    cd.record(CHAT_SOURCE, conn, chats)


def close(conn: sqlite3.Connection, roots, registry, classifier,
          chats: list[Chat], now: datetime, tmp_dir: Path,
          transcriber: Transcriber, result: cd.PassResult, *,
          before: str | None = None, limit: int | None = None,
          ocr: bool = True, ingest=ingest_file) -> int:
    return cd.close(CHAT_SOURCE, conn, roots, registry, classifier, chats, now,
                    tmp_dir, transcriber, result, before=before, limit=limit,
                    ocr=ocr, ingest=ingest)


def read_chats(src_dir: Path, tmp: Path, since: datetime) -> list[Chat]:
    return cd.read_chats(CHAT_SOURCE, src_dir, tmp, since)


def run_pass(conn: sqlite3.Connection, roots, registry, classifier, *,
             src_dir: Path, tmp_root: Path, now: datetime,
             transcriber: Transcriber = asr.transcribe, since_days: int = 3,
             ingest=ingest_file) -> cd.PassResult:
    return cd.run_pass(CHAT_SOURCE, conn, roots, registry, classifier,
                       src_dir=src_dir, tmp_root=tmp_root, now=now,
                       transcriber=transcriber, since_days=since_days,
                       ingest=ingest)


def plan_days(conn: sqlite3.Connection, chats: list[Chat],
              now: datetime) -> list[tuple[str, str, int, bool]]:
    return cd.plan_days(CHAT_SOURCE, conn, chats, now)
```

- [ ] **Step 4: Watch the job in the brief**

In `lms/core/reports/brief.py`, under the WhatsApp entry of `WATCHED_JOBS`:

```python
    # iMessage and texts (R2, D-081): hourly; every pass writes a row.
    "IMESSAGE_CHECKED": ("imessage", 3),
```

and in `JOB_FAILURES`:

```python
    "IMESSAGE_FAILED":     "IMESSAGE_CHECKED",
```

A watched job that has never run is not reported, so nothing is said before `com.lms.imessage` is installed.

- [ ] **Step 5: Run the tests, the guards and the brief tests**

Run: `<python> -m pytest -o addopts="" -p no:cacheprovider tests/test_imessage.py tests/test_stalled_jobs.py tests/test_chatdays.py tests/test_whatsapp.py -q`
Expected: all pass.

Run: `<python> -m pytest -o addopts="" -p no:cacheprovider -q -k "brief or report"`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add lms/core/pipeline/imessage.py lms/core/reports/brief.py lms/tests/test_imessage.py
git commit -m "D-081: iMessage chat days through the shared engine; SMS line flags forwarding off"
```

---

### Task 6: The hourly job, the backfill, launchd, and `/lms imessage`

**Files:**
- Create: `lms/ops/_backfill.py`, `lms/ops/backfill_imessage.py`, `lms/ops/imessage.py`, `lms/ops/com.lms.imessage.plist`
- Modify: `lms/ops/backfill_whatsapp.py` (whole file), `lms/ops/remote.py` (HELP at line 49, `_last_good_checks` at 110–124, JOBS at 169, COMMANDS at 270, NO_ARGS at 277)
- Test: `lms/tests/test_imessage_job.py`; `lms/tests/test_remote.py` (lines 116 and 214–216)

**Interfaces:**
- Consumes: `core.pipeline.imessage.{read_chats, record, close, PassResult, yesterday, run_pass, plan_days}` and `core.adapters.imessage.{snapshot, probe, contact_names, CONTAINER, CONTACTS_ROOT, DB_NAME, GROUP_STYLE, IMessageError}`.
- Produces:
  - `ops/_backfill.backfill(pipe, conn, roots, registry, classifier, *, src_dir, tmp_root, now, days, batch, transcriber) -> int`
  - `ops/_backfill.main(pipe, *, doc, reexec, script, container, tmp_name, error) -> int`
  - `ops/backfill_imessage.backfill(conn, roots, registry, classifier, *, src_dir, tmp_root, now, days, batch, transcriber=asr.transcribe) -> int`
  - `ops/imessage.do_probe(src_dir) -> int`
  - `ops/imessage.do_dry_run(conn, now, src_dir=im.CONTAINER) -> int`
  - `ops/imessage.main() -> int`

- [ ] **Step 1: Write the failing tests**

`lms/tests/test_imessage_job.py`:

```python
"""ops/imessage.py and ops/backfill_imessage.py (R2, D-081)."""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from core.adapters import asr
from core.adapters import imessage as im
from core.adapters.lmstudio import Completion
from core.db import database as db
from core.pipeline import filing, notify
from core.pipeline import imessage as i
from core.pipeline.classify import Classifier
from core.pipeline.registry import load_registry
from tests.im_fixture import IM, contacts

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))

import backfill_imessage as bf      # noqa: E402
import imessage as job              # noqa: E402

ET = ZoneInfo("America/New_York")
CONFIG = Path(__file__).resolve().parents[1] / "config"


class FakeModel:
    def complete(self, **kw):
        return Completion(text=json.dumps({
            "domain": "BUSINESS", "entity_id": "B_MRE", "category": "VENDORS",
            "subcategory": "invoices-received", "urgency": "NORMAL",
            "confidence": 0.93, "requires_reply": False, "due_date": None,
            "counterparty": "acme-supply", "descriptor": "invoice chat",
            "amount_cents": None, "rationale": "vendor"}),
            model="fake", elapsed_s=0.01, finish_reason="stop")


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "lms.db")
    yield c
    c.close()


@pytest.fixture
def pipe(tmp_path):
    roots = filing.StorageRoots(archive=tmp_path / "archive",
                                originals=tmp_path / "_originals",
                                quarantine=tmp_path / "quarantine")
    roots.ensure()
    registry = load_registry(CONFIG)
    return roots, registry, Classifier(registry, client=FakeModel())


@pytest.fixture(autouse=True)
def no_contacts(tmp_path, monkeypatch):
    monkeypatch.setattr(im, "CONTACTS_ROOT", tmp_path / "no-contacts")


def no_speech(path, tmp):
    raise asr.ASRUnavailable("not in tests")


def history(tmp_path, days_back):
    box = IM(tmp_path / "Messages")
    h = box.handle("+15550100100")
    c = box.chat("+15550100100")
    now = datetime.now(ET)
    for back in days_back:
        box.message(c, (now - timedelta(days=back)).replace(hour=9, minute=0),
                    f"day -{back}", handle=h)
    box.close()
    return box.root, now


def test_the_job_stops_when_halted(monkeypatch, capsys):
    monkeypatch.setattr(job._halt, "read_flag", lambda: {"ts": "now", "by": "telegram"})
    monkeypatch.setattr(job, "ensure_venv", lambda *a, **k: None)
    monkeypatch.setattr(job, "load_lms_env", lambda: {})
    monkeypatch.setattr(sys, "argv", ["imessage.py"])
    assert job.main() == 0
    assert "HALTED" in capsys.readouterr().out


def test_the_probe_prints_counts_and_no_content(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(im, "CONTACTS_ROOT", contacts(
        tmp_path / "ab", {"first": "Rhonda", "phones": ["555-010-0100"]}))
    src, _ = history(tmp_path, (1, 2))
    assert job.do_probe(src) == 0
    out = capsys.readouterr().out
    assert "messages by service: iMessage 2" in out
    assert "contacts read: 1" in out
    for secret in ("+15550100100", "Rhonda", "day -1"):
        assert secret not in out


def test_the_probe_fails_on_a_changed_schema(tmp_path, capsys):
    import sqlite3
    src, _ = history(tmp_path, (1,))
    raw = sqlite3.connect(src / "chat.db")
    raw.execute("ALTER TABLE message RENAME COLUMN text TO body")
    raw.commit()
    raw.close()
    assert job.do_probe(src) == 1
    assert "MISSING: message.text" in capsys.readouterr().out


def test_the_dry_run_writes_nothing(conn, tmp_path, capsys):
    src, now = history(tmp_path, (1, 2))
    assert job.do_dry_run(conn, now, src_dir=src) == 0
    assert "Nothing was written." in capsys.readouterr().out
    assert conn.execute("SELECT COUNT(*) FROM imessage_messages").fetchone()[0] == 0


def test_backfill_files_old_days_in_batches_and_leaves_yesterday(conn, pipe, tmp_path):
    roots, registry, clf = pipe
    src, now = history(tmp_path, (5, 4, 3, 1))
    filed = bf.backfill(conn, roots, registry, clf, src_dir=src,
                        tmp_root=tmp_path / "tmp", now=now, days=10, batch=2,
                        transcriber=no_speech)
    assert filed == 3
    days = {r[0]: r[1] for r in conn.execute(
        "SELECT day, artifact_id FROM imessage_messages")}
    assert days[i.day_of(now - timedelta(days=1))] is None, "yesterday is the hourly job's"
    assert notify.pending(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM source_checks").fetchone()[0] == 0
    assert list((tmp_path / "tmp").iterdir()) == []


def test_the_backfill_stops_between_batches_when_halted(conn, pipe, tmp_path, monkeypatch):
    roots, registry, clf = pipe
    src, now = history(tmp_path, (5, 4, 3))
    monkeypatch.setattr(bf._halt, "read_flag", lambda: {"ts": "now", "by": "telegram"})
    assert bf.backfill(conn, roots, registry, clf, src_dir=src,
                       tmp_root=tmp_path / "tmp", now=now, days=10, batch=1,
                       transcriber=no_speech) == 1
```

In `lms/tests/test_remote.py`:
- Add `("imessage", "com.lms.imessage"),` to the parametrize list of `test_run_now_kickstarts_the_existing_job`.
- Add `"imessage"` to the tuple in `test_help_lists_every_command_and_no_resume`.
- Append:

```python
def test_status_shows_the_last_good_imessage_check(env):
    c = db.connect(env.dbfile)
    c.execute("INSERT INTO source_checks (source_id, finished_at, result)"
              " VALUES ('messages:imessage', '2026-11-07T09:00:00', 'OK')")
    c.close()
    assert "last good imessage: 2026-11-07T09:00" in \
        remote.handle(["status"], "telegram", TG, env)
```

- [ ] **Step 2: Run them to see them fail**

Run: `<python> -m pytest -o addopts="" -p no:cacheprovider tests/test_imessage_job.py tests/test_remote.py -q`
Expected: FAIL. `ModuleNotFoundError: No module named 'backfill_imessage'`, and the remote tests fail on `imessage`.

- [ ] **Step 3: Create `lms/ops/_backfill.py`**

```python
"""File a chat source's history in batches (R28; D-079, D-081).

Shared by backfill_whatsapp.py and backfill_imessage.py. Files every chat
day before yesterday; yesterday and today are the hourly job's, so its
urgency check still sees them. Days older than 2 days raise no alerts:
notify's 2-day rule drops them. The day before yesterday is still fresh, so
an urgent one alerts once as it is filed. Not a check of the source: nothing
goes to source_checks.
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


def backfill(pipe, conn, roots, registry, classifier, *, src_dir: Path,
             tmp_root: Path, now: datetime, days: int, batch: int,
             transcriber) -> int:
    """`pipe` is core.pipeline.whatsapp or core.pipeline.imessage."""
    Path(tmp_root).mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="backfill-", dir=tmp_root))
    before = pipe.yesterday(now)
    total = 0
    try:
        chats = pipe.read_chats(src_dir, tmp, now - timedelta(days=days))
        pipe.record(conn, chats)
        while True:
            result = pipe.PassResult()
            n = pipe.close(conn, roots, registry, classifier, chats, now, tmp,
                           transcriber, result, before=before, limit=batch)
            total += n
            print(f"batch: {result.summary()}", flush=True)
            if n < batch:
                return total
            if _halt.read_flag() is not None:
                print("HALTED: stopped between batches.", flush=True)
                return total
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(pipe, *, doc: str, reexec: str, script: str, container: Path,
         tmp_name: str, error: type[Exception]) -> int:
    ensure_venv(reexec, script=script)
    load_lms_env()
    p = argparse.ArgumentParser(
        description=doc, formatter_class=argparse.RawDescriptionHelpFormatter)
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
        n = backfill(pipe, conn, roots, registry, Classifier(registry),
                     src_dir=container,
                     tmp_root=roots.archive.parent / "tmp" / tmp_name,
                     now=datetime.now().astimezone(), days=args.days,
                     batch=args.batch, transcriber=asr.transcribe)
    except error as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    print(f"{n} chat day(s) filed.")
    return 0
```

- [ ] **Step 4: Point `lms/ops/backfill_whatsapp.py` at it**

Replace the whole file. `_halt` stays imported because the tests patch `bf._halt.read_flag`; it is the same module object `_backfill` uses.

```python
#!/usr/bin/env python3
"""File the WhatsApp history the Mac app holds (R40, R28, D-079).

    ./ops/backfill_whatsapp.py --days 30              # last 30 days, 20 chat days a batch
    ./ops/backfill_whatsapp.py --days 365 --batch 50

Files every chat day before yesterday; yesterday and today are the hourly
job's, so its urgency check still sees them. Days older than 2 days raise no
alerts: notify's 2-day rule drops them. The day before yesterday is still
fresh, so an urgent one alerts once as it is filed.
Not a check of the source: nothing goes to source_checks. The loop is
ops/_backfill.py, shared with backfill_imessage.py.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _backfill                                            # noqa: E402
import _halt                                                # noqa: E402,F401
from core.adapters import asr                              # noqa: E402
from core.adapters import whatsapp as wa                   # noqa: E402
from core.pipeline import whatsapp as w                    # noqa: E402


def backfill(conn, roots, registry, classifier, *, src_dir: Path, tmp_root: Path,
             now: datetime, days: int, batch: int, transcriber=asr.transcribe) -> int:
    return _backfill.backfill(w, conn, roots, registry, classifier,
                              src_dir=src_dir, tmp_root=tmp_root, now=now,
                              days=days, batch=batch, transcriber=transcriber)


def main() -> int:
    return _backfill.main(w, doc=__doc__, reexec="LMS_WHATSAPP_REEXEC",
                          script=__file__, container=wa.CONTAINER,
                          tmp_name="whatsapp", error=wa.WhatsAppError)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Create `lms/ops/backfill_imessage.py`**

```python
#!/usr/bin/env python3
"""File the iMessage and text history Messages on the Mac holds (R2, R28, D-081).

    ./ops/backfill_imessage.py --days 90              # the first load: 90 days
    ./ops/backfill_imessage.py --days 365 --batch 50  # older years, later

Files every chat day before yesterday; yesterday and today are the hourly
job's. Days older than 2 days raise no alerts (notify's 2-day rule), so 90
days of history does not flood his phone. Not a check of the source:
nothing goes to source_checks. The loop is ops/_backfill.py.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _backfill                                            # noqa: E402
import _halt                                                # noqa: E402,F401
from core.adapters import asr                              # noqa: E402
from core.adapters import imessage as im                   # noqa: E402
from core.pipeline import imessage as i                    # noqa: E402


def backfill(conn, roots, registry, classifier, *, src_dir: Path, tmp_root: Path,
             now: datetime, days: int, batch: int, transcriber=asr.transcribe) -> int:
    return _backfill.backfill(i, conn, roots, registry, classifier,
                              src_dir=src_dir, tmp_root=tmp_root, now=now,
                              days=days, batch=batch, transcriber=transcriber)


def main() -> int:
    return _backfill.main(i, doc=__doc__, reexec="LMS_IMESSAGE_REEXEC",
                          script=__file__, container=im.CONTAINER,
                          tmp_name="imessage", error=im.IMessageError)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Create `lms/ops/imessage.py`**

```python
#!/usr/bin/env python3
"""Read iMessage and texts, hourly (R2, D-081).

    ./ops/imessage.py              # one pass: what com.lms.imessage runs hourly
    ./ops/imessage.py --probe      # the database's shape, no content; writes nothing
    ./ops/imessage.py --dry-run    # unfiled chat days, open or closed; writes nothing

Reads a copy of Messages' chat.db and of Contacts; never writes to Messages,
never sends. Each pass: hourly urgency check on today's and yesterday's new
messages (alerts through D-077), then every closed chat day filed as one
transcript, then the SMS line of the coverage register. See
core/pipeline/imessage.py and ops/IMESSAGE_APPLY.md.

Halted (D-059), it reads nothing: com.lms.imessage is disabled with every
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
from core.adapters import imessage as im                   # noqa: E402


def _tally(d: dict) -> str:
    return ", ".join(f"{k} {n}" for k, n in d.items())


def do_probe(src_dir: Path) -> int:
    tmp = Path(tempfile.mkdtemp(prefix="im-probe-"))
    try:
        copy = im.snapshot(src_dir, tmp / "db")
        conn = sqlite3.connect(copy)
        try:
            p = im.probe(conn)
        finally:
            conn.close()
        print(f"database: {Path(src_dir) / im.DB_NAME} (copied, checked)")
        print("tables: " + ", ".join(p["tables"]))
        for table, n in p["counts"].items():
            print(f"{table}: {n}")
        for m in p["missing"]:
            print(f"MISSING: {m}")
        if p["missing"]:
            return 1
        print("messages by service: " + _tally(p["services"]))
        print("chats by style: " + ", ".join(f"{s}={n}" for s, n in p["styles"].items())
              + f"; the reader takes {im.GROUP_STYLE} as group")
        print("attachments by type: " + _tally(p["attachment_types"]))
        print(f"skipped: {p['reactions']} reactions, {p['system']} system rows")
        need, decoded = p["attributed"]
        print(f"text only in attributedBody: {need}, decoded {decoded}")
        print(f"chat identifiers with more than one chat row: {p['shared_identifiers']}")
        found, filled = p["files"]
        print(f"attachment files on the Mac: {found} of {filled}")
        print("newest: " + _tally(p["newest"]))
        names = im.contact_names(im.CONTACTS_ROOT, tmp / "contacts")
        print(f"contacts read: {len(names)}"
              + ("" if names else " (names fall back to numbers)"))
        return 0
    except (im.IMessageError, OSError, sqlite3.Error) as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def do_dry_run(conn, now: datetime, src_dir: Path = im.CONTAINER) -> int:
    from core.pipeline import imessage as i
    tmp = Path(tempfile.mkdtemp(prefix="im-dry-"))
    try:
        chats = i.read_chats(src_dir, tmp, now - timedelta(days=3))
    except (im.IMessageError, OSError) as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    rows = i.plan_days(conn, chats, now)
    for label, day, n, is_closed in rows:
        print(f"  {day}  {'closed' if is_closed else 'open  '}  {n:4d}  {label}")
    print(f"{len(rows)} chat day(s) unfiled. Nothing was written.")
    return 0


def main() -> int:
    ensure_venv("LMS_IMESSAGE_REEXEC", script=__file__)
    load_lms_env()
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--probe", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    if args.probe:
        return do_probe(im.CONTAINER)

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
    from core.pipeline import imessage as i
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
        result = i.run_pass(conn, roots, registry, Classifier(registry),
                            src_dir=im.CONTAINER,
                            tmp_root=roots.archive.parent / "tmp" / "imessage",
                            now=now)
    finally:
        conn.close()
    print(result.summary())
    return 1 if result.result == "FAILED" or result.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 7: Create `lms/ops/com.lms.imessage.plist`**

Comments must not contain two hyphens in a row (`tests/test_plists.py`).

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!--
  iMessage and texts, hourly. R2, D-081. ops/imessage.py says what one pass does.

  Install (ops/IMESSAGE_APPLY.md):
    cp ops/com.lms.imessage.plist ~/Library/LaunchAgents/
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.lms.imessage.plist

  StartInterval: a pass missed while the Mac slept is made up by the next
  one, which reads the last three days. A LaunchAgent, because chat.db is in
  Matthew's user Library (D-009). The interpreter below is a link: Full Disk
  Access goes to the Homebrew python3.12 and Python.app it runs, the same
  grant WhatsApp uses (WHATSAPP_APPLY.md step 3). A halt (D-059) disables
  this job with the rest.
-->
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.lms.imessage</string>

    <key>ProgramArguments</key>
    <array>
        <string>/Users/mleca/lms-repo/lms/.venv/bin/python</string>
        <string>/Users/mleca/lms-repo/lms/ops/imessage.py</string>
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
    <string>/Users/mleca/LMS/logs/imessage.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/mleca/LMS/logs/imessage.err</string>

    <key>ProcessType</key>
    <string>Background</string>
    <key>Nice</key>
    <integer>10</integer>
</dict>
</plist>
```

- [ ] **Step 8: Add `/lms imessage` to `lms/ops/remote.py`**

- Update the `HELP` text: below `/lms whatsapp – read WhatsApp now`, add `/lms imessage – read iMessage and texts now`.
- `JOBS = {"mail": "com.lms.mail", "whatsapp": "com.lms.whatsapp", "imessage": "com.lms.imessage", "backup": "com.lms.backup"}`
- In `COMMANDS`, after `"whatsapp"`, add `"imessage": cmd_run_now("imessage"),`.
- Add `"imessage"` to `NO_ARGS`.
- Update the module docstring's command list (line 12): `tasks coverage mail whatsapp imessage backup done approve deny halt`.
- Replace the query in `_last_good_checks` with:

```python
            rows = conn.execute(
                "SELECT CASE WHEN source_id LIKE 'email:%' THEN 'mail' "
                "WHEN source_id = 'chat:whatsapp' THEN 'whatsapp' "
                "ELSE 'imessage' END AS k, MAX(finished_at) FROM source_checks "
                "WHERE result = 'OK' AND (source_id LIKE 'email:%' "
                "OR source_id IN ('chat:whatsapp', 'messages:imessage')) "
                "GROUP BY k ORDER BY k").fetchall()
```

- [ ] **Step 9: Run the job tests, remote, plists and the WhatsApp backfill**

Run: `<python> -m pytest -o addopts="" -p no:cacheprovider tests/test_imessage_job.py tests/test_remote.py tests/test_plists.py tests/test_ops_env.py tests/test_whatsapp.py -q`
Expected: all pass, with `test_whatsapp.py` still unedited.

- [ ] **Step 10: Run the whole suite**

Run: the whole-suite command (Global Constraints).
Expected: all pass, 1 skipped, 1 xfailed.

- [ ] **Step 11: Commit**

```bash
git add lms/ops/_backfill.py lms/ops/backfill_whatsapp.py lms/ops/backfill_imessage.py lms/ops/imessage.py lms/ops/com.lms.imessage.plist lms/ops/remote.py lms/tests/test_imessage_job.py lms/tests/test_remote.py
git commit -m "D-081: com.lms.imessage hourly job, 90-day backfill, /lms imessage"
```

---

### Task 7: Setup guide, coverage register, decision and requirement

**Files:**
- Create: `lms/ops/IMESSAGE_APPLY.md`
- Modify:
  - `lms/config/sources.yaml` (the `messages:imessage` and `messages:sms` entries, lines 126–151)
  - `lms/tests/test_coverage.py` (append after line 69)
  - `DECISIONS.md` (new D-081 above `### D-080`)
  - `REQUIREMENTS_REMAINING.md` (the R2 row, line 26)

**Precondition:** the developer's uncommitted Nov 7 edits to these three files are committed first (Global Constraints).

- [ ] **Step 1: Write the failing register test**

Append to `lms/tests/test_coverage.py`:

```python
def test_imessage_and_sms_are_listed_with_their_reader_and_read_hourly():
    reg = {s.id: s for s in coverage.load_register()}
    im, sms = reg["messages:imessage"], reg["messages:sms"]
    assert "chat.db" in im.method and "ops/imessage.py" in im.method
    assert "chat.db" in sms.method
    assert im.cadence_hours == sms.cadence_hours == 1
    # Built, not installed: Messages on the Mac is signed out (D-068).
    assert im.status == "blocked" and "signed out" in im.blocked_on
    assert "Text Message Forwarding" in sms.blocked_on
```

Run: `<python> -m pytest -o addopts="" -p no:cacheprovider tests/test_coverage.py -q`
Expected: FAIL. `"ops/imessage.py" in im.method` is false.

- [ ] **Step 2: Update the two register entries in `lms/config/sources.yaml`**

Replace the `messages:imessage` and `messages:sms` entries with:

```yaml
  - id: messages:imessage
    label: iMessage
    kind: messages
    account: Matthew's Apple Account in Messages on the Mac (D-068, D-078)
    method: >-
      read-only copy of Messages' ~/Library/Messages/chat.db on the Mac
      (ops/imessage.py, D-081). One transcript per chat per day, filed when
      the day closes; documents and photos filed under it; voice messages
      transcribed on the Mac; names from a read-only copy of Contacts
    permissions: >-
      Full Disk Access for Homebrew's python3.12 and Python.app (already
      granted for WhatsApp); Messages on the Mac signed in to his account
      with Messages in iCloud on. Nothing is written to Messages and nothing
      is sent
    cadence_hours: 1
    status: blocked
    requirement: R2
    blocked_on: >-
      Messages on the Mac has been signed out since Oct 5 (D-068, Nov 7
      probe). Matthew signs it in with the account his iPhone uses and turns
      on Messages in iCloud; then ops/IMESSAGE_APPLY.md installs the reader.
      Scope is all chats, hourly (D-065).
    gaps: >-
      Only what this Mac's Messages holds; nothing while it is signed out.
      Videos are listed, not filed. Attachments not on the Mac are listed,
      not filed. A message deleted after filing stays filed. The hourly job
      reads the last 3 days; a message that reaches the Mac later is filed
      only by a backfill run. A pass with nothing newer than 48 hours is
      PARTIAL.

  - id: messages:sms
    label: Text messages (SMS and RCS)
    kind: messages
    account: his iPhone, forwarded to the Mac
    method: >-
      the same chat.db and the same reader as iMessage (D-081), once Text
      Message Forwarding is on; each iMessage pass records this line too
    permissions: Text Message Forwarding on the iPhone for this Mac
    cadence_hours: 1
    status: blocked
    requirement: R2
    blocked_on: >-
      The iMessage reader's install (messages:imessage), and Matthew turning
      on Text Message Forwarding for the Mac Studio.
    gaps: >-
      SMS and RCS only while Text Message Forwarding is on: a pass where
      iMessage arrives and no SMS or RCS has for 7 days is PARTIAL.
```

Run: `<python> -m pytest -o addopts="" -p no:cacheprovider tests/test_coverage.py -q`
Expected: all pass.

- [ ] **Step 3: Write `lms/ops/IMESSAGE_APPLY.md`**

````markdown
# Installing the iMessage reader on the Mac (R2, D-081)

Installs `com.lms.imessage`. Every hour it reads a copy of Messages' database
(`~/Library/Messages/chat.db`) and of Contacts, checks today's and yesterday's
new texts for anything urgent, and files each chat's day as one transcript
once the day is over. It never writes to Messages and never sends anything.
iMessage, SMS and RCS all come from the same database.

**Before you start:** Messages on the Mac has been signed out since Oct 5
(D-068). Until it is signed in, the reader would file only old history. Steps
1 and 2 are Matthew's own; do not sign Messages in or out for him.

## 1. Matthew signs Messages in on the Mac

1. On his iPhone: **Settings → Apps → Messages → Send & Receive**. Note the
   Apple Account shown at the bottom (expected `mattyeps@gmail.com`).
2. On the Mac: open **Messages** and sign in with that same account.
3. **Messages → Settings → iMessage → Enable Messages in iCloud.** This should
   also bring back Oct 5 to now. Leave the Mac on and Messages open until the
   newest conversations appear.

## 2. Matthew turns on Text Message Forwarding

On his iPhone: **Settings → Apps → Messages → Text Message Forwarding** →
turn on **Mac Studio**. Without it, SMS and RCS texts never reach the Mac, and
the register's SMS line says so.

## 3. Pull and probe

```bash
cd ~/lms-repo && git pull && cd lms
```

```bash
./ops/imessage.py --probe
```

The probe prints counts and dates only, never a message, number or name.
Check:
- no `MISSING:` line;
- `newest:` shows iMessage today or yesterday. If not, Messages is still not
  syncing: stop and go back to step 1;
- `text only in attributedBody: N, decoded M`: M close to N;
- `contacts read:` more than 0. If it is 0, names fall back to numbers; tell
  the developer.

## 4. Full Disk Access

Nothing new to grant: the job runs the same Homebrew `python3.12` and
`Python.app` that were given Full Disk Access for WhatsApp
(`WHATSAPP_APPLY.md` step 3). Step 6 confirms the job can read `chat.db`.

## 5. Dry run, then the 90-day history

```bash
./ops/imessage.py --dry-run
```

It lists unfiled chat days and writes nothing. Then:

```bash
./ops/backfill_imessage.py --days 90
```

It files every chat day before yesterday in batches of 20 and prints a line
per batch. Old days raise no alerts. A halt stops it between batches.

## 6. Install the hourly job

```bash
cp ops/com.lms.imessage.plist ~/Library/LaunchAgents/
```

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.lms.imessage.plist
```

```bash
launchctl kickstart -k gui/$(id -u)/com.lms.imessage
```

```bash
tail -3 ~/LMS/logs/imessage.log
```

The last line is a summary such as `41 message(s) seen, 3 chat day(s)
filed, 0 alert(s)`. A `PermissionError` in `~/LMS/logs/imessage.err` means
Full Disk Access is not effective for the job: redo `WHATSAPP_APPLY.md`
step 3 (a Homebrew Python update needs it again), then kickstart again.

## 7. Mark it active

Tell the developer. They set `messages:imessage` to `active` in
`config/sources.yaml`, and `messages:sms` too once a text has arrived.

## Troubleshooting

- `Messages on the Mac has not synced since … (is it signed in?)` in the log
  or the brief: Messages is signed out or closed. Step 1.
- The brief's coverage section says `no SMS or RCS since … while iMessage
  arrives`: Text Message Forwarding is off. Step 2.
- `FAILED … schema changed, missing: …`: a macOS update changed Messages'
  database. The pass fails loudly rather than reading nothing; run
  `--probe` and send the developer its output.
- `/lms imessage` in WhatsApp or Telegram runs one pass now.

## Removing it

```bash
launchctl bootout gui/$(id -u)/com.lms.imessage
```

```bash
rm ~/Library/LaunchAgents/com.lms.imessage.plist
```

What was filed stays filed.
````

- [ ] **Step 4: Record D-081 in `DECISIONS.md`**

Insert above `### D-080`:

```markdown
### D-081 — iMessage and texts are read from the Mac's chat.db through a chat-day engine shared with WhatsApp
**Status:** Decided Nov 7 2026 · designed and approved with the developer · R2, R7, R16 · spec [docs/superpowers/specs/2026-11-07-imessage-reader-design.md](docs/superpowers/specs/2026-11-07-imessage-reader-design.md) · plan [docs/superpowers/plans/2026-11-07-imessage-reader.md](docs/superpowers/plans/2026-11-07-imessage-reader.md)

- **What is read.** Every iMessage, SMS and RCS text in Messages on the Mac, from a checked copy of `~/Library/Messages/chat.db` (D-009). Names come from a copy of Contacts. Nothing calls Messages; nothing is sent.
- **One engine, two sources.** The WhatsApp pipeline became `core/pipeline/chatdays.py`, driven by a `ChatSource` settings object. WhatsApp and iMessage are two settings. The proof the refactor changed nothing is that WhatsApp's tests passed unedited. The one change for both is that a voice note of unknown length shows `[voice]`. Rejected: copying the pipeline (every fix twice), and one table for all sources (migrating a live table on the Mac).
- **Same document as WhatsApp.** One chat, one Eastern-time day, one transcript, with photos and documents filed under it. Chats are keyed on `chat_identifier`, so one person's iMessage and SMS threads are one chat. Groups are included (D-065).
- **The SMS line.** Each pass also records `messages:sms`. It is PARTIAL when iMessage arrives and no SMS or RCS has for 7 days, which means Text Message Forwarding is off.
- **Stale is not quiet.** Nothing newer than 48 hours makes the pass PARTIAL with "is it signed in?". This would have caught the Oct 5 sign-out within two days.
- **History.** The first load is 90 days (developer, Nov 7); older years can be loaded later with `--days`.
- **Install waits on Matthew.** He signs Messages in on the Mac, turns on Messages in iCloud and turns on Text Message Forwarding (`ops/IMESSAGE_APPLY.md`). Until then `messages:imessage` and `messages:sms` stay `blocked`.
```

- [ ] **Step 5: Update R2 in `REQUIREMENTS_REMAINING.md`**

Replace the last cell of the R2 row (line 26) with:

```
**Reader built Nov 7 (D-081)**, not installed: Messages on the Mac has been signed out since Oct 5 (D-068). Matthew signs it in with his iPhone's account and turns on Messages in iCloud and Text Message Forwarding; then `lms/ops/IMESSAGE_APPLY.md` (probe, 90-day backfill, hourly job). Scope: **all chats, hourly** (D-065)
```

- [ ] **Step 6: Run the whole suite**

Run: the whole-suite command (Global Constraints).
Expected: all pass, 1 skipped, 1 xfailed.

- [ ] **Step 7: Commit**

```bash
git add lms/ops/IMESSAGE_APPLY.md lms/config/sources.yaml lms/tests/test_coverage.py DECISIONS.md REQUIREMENTS_REMAINING.md
git commit -m "D-081: iMessage setup guide, register lines, decision and R2"
```

---

## On the Mac only (not part of this plan's tasks)

- The probe against the real `chat.db` and Contacts (IMESSAGE_APPLY step 3).
- One real voice message transcribed: `.caf` through ffmpeg and mlx-whisper.
- After Matthew signs in and forwarding is on: the backfill, the install, and `messages:imessage` / `messages:sms` set to `active`.
