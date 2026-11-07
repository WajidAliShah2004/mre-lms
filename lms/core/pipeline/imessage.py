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
        problems = ()
        if not names and im.contact_databases(im.CONTACTS_ROOT):
            problems = ("names from Contacts unavailable (Contacts could not be read)",)
        return cd.ReadResult(im.read(rconn, since, names), im.newest(rconn),
                             im.newest_by_service(rconn), problems)
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
