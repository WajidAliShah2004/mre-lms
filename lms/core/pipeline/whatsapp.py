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
