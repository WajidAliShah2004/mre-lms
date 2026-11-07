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
