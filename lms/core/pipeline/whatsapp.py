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

import hashlib
import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from ..adapters import asr
from ..adapters import whatsapp as wa
from ..adapters.whatsapp import Chat, Message, number
from ..db import database as db
from . import coverage, notify
from .classify import Artifact
from .ingest import ingest_file

TZ = ZoneInfo("America/New_York")
CLOSE_AT = time(0, 30)
SOURCE = "whatsapp"
SOURCE_ID = "chat:whatsapp"
RECIPIENT = "Matthew (WhatsApp)"
CHECKED = "WHATSAPP_CHECKED"
FAILED = "WHATSAPP_FAILED"
FILED_KINDS = frozenset({"document", "image"})
STALE_AFTER = timedelta(hours=48)       # no message newer: the Mac app is not syncing

Transcriber = Callable[[Path, Path], str]


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
    stale_since: datetime | None = None     # newest message, when too old
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
            s += f", {self.failed} FAILED (see WHATSAPP_DAY_FAILED)"
        if self.voice_missing:
            s += f", {self.voice_missing} voice note(s) not transcribed"
        if self.media_missing:
            s += f", {self.media_missing} file(s) not downloaded on the Mac"
        if self.stale_since:
            s += (", WhatsApp on the Mac has not synced since "
                  f"{self.stale_since.astimezone(TZ):%Y-%m-%d %H:%M} (is it running?)")
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


def _slot(stanza_id: str) -> str:
    """A temp directory name for a message. The id is the sender's client's
    choice ("../../x", an absolute path): never a path segment itself."""
    return hashlib.sha1(stanza_id.encode("utf-8")).hexdigest()[:16]


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
    copy = Path(tmp_dir) / "voice" / _slot(m.stanza_id) / m.media.local_path.name
    try:
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(m.media.local_path, copy)
        text = transcriber(copy, copy.parent)
    except (asr.ASRUnavailable, OSError):
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


def _artifact(chat: Chat, day: str, body: str, subject: str | None = None) -> Artifact:
    return Artifact(source=SOURCE, subject=subject or chat.name,
                    sender=chat_label(chat), recipient=RECIPIENT, body=body,
                    received_date=day)


# ---------------------------------------------------------------------------
# The hourly check
# ---------------------------------------------------------------------------

def scan(conn: sqlite3.Connection, chats: list[Chat], classifier, now: datetime,
         tmp_dir: Path, transcriber: Transcriber, result: PassResult) -> None:
    """Today's and yesterday's unscanned messages, per chat day, to the
    classifier for urgency only. Files nothing.

    A chat day that fails (the model down included) is logged, counted, and
    left unscanned for the next hour; the other chats go on."""
    recent = {day_of(now), yesterday(now)}
    for chat in chats:
        new = [m for m in chat.messages if day_of(m.sent_at) in recent
               and _row(conn, m.stanza_id)["scanned_at"] is None]
        for day, msgs in _by_day(new).items():
            try:
                _scan_day(conn, chat, day, msgs, classifier, tmp_dir,
                          transcriber, result)
            except Exception as exc:                          # noqa: BLE001
                result.failed += 1
                db.log_action(conn, "WHATSAPP_DAY_FAILED",
                              detail=f"{chat.jid} {day}: check: "
                                     f"{type(exc).__name__}: {exc}"[:400])
                conn.commit()


def _model_down(c) -> bool:
    """Classifier.classify turns a model error into a review Classification
    with no model name (classify.py); that is "unknown", not "not urgent"."""
    return bool(getattr(c, "needs_review", False)) and getattr(c, "model", None) == ""


def _downloading(m: Message) -> bool:
    """A voice note the Mac app has not downloaded yet: checked again next hour."""
    return m.kind == "voice" and (m.media is None or m.media.local_path is None)


def _scan_day(conn, chat: Chat, day: str, msgs: list[Message], classifier,
              tmp_dir: Path, transcriber: Transcriber, result: PassResult) -> None:
    voices = {m.stanza_id: voice_text(conn, m, tmp_dir, transcriber, result)
              for m in msgs if m.kind == "voice"}
    body = "\n".join(line(m, voices.get(m.stanza_id)) for m in msgs)
    c = classifier.classify(_artifact(chat, day, body))
    if _model_down(c):
        raise RuntimeError(c.review_reason or "model error")
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
        [(db.now_iso(), m.stanza_id) for m in msgs if not _downloading(m)])
    conn.commit()


def _safe(name: str) -> str:
    return re.sub(r"[^\w\- ]+", "", name).strip()[:60] or "chat"


# ---------------------------------------------------------------------------
# Closing a day
# ---------------------------------------------------------------------------

def _part(conn, jid: str, day: str) -> int:
    n = conn.execute(
        "SELECT COUNT(DISTINCT artifact_id) FROM whatsapp_messages "
        "WHERE chat_jid = ? AND day = ? AND artifact_id IS NOT NULL",
        (jid, day)).fetchone()[0]
    return n + 1


def _media_name(title: str | None, local: Path) -> str:
    """A file name safe to create in the temp dir, from a sender-chosen title."""
    base = Path((title or "").replace("\\", "/")).name
    stem = re.sub(r"[^\w\- ]+", "", Path(base).stem).strip()[:60]
    suffix = re.sub(r"[^\w.]+", "", Path(base).suffix)[:10] or local.suffix
    return f"{stem}{suffix}" if stem else local.name


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
                   source_ref=f"whatsapp:{m.stanza_id}", parent_id=parent,
                   artifact=_artifact(chat, day, "", m.media.title or chat_label(chat)),
                   run_ocr=ocr, alert_key=key)
        except Exception as exc:                              # noqa: BLE001
            failures.append(f"{m.stanza_id}: {type(exc).__name__}: {exc}")
    if failures:
        # Leave the day unfiled so the next pass retries; ingest_file answers
        # DUPLICATE for what was already filed.
        raise RuntimeError(f"{len(failures)} attachment(s) failed: " + "; ".join(failures))
    conn.executemany(
        "UPDATE whatsapp_messages SET artifact_id = ?, "
        "scanned_at = COALESCE(scanned_at, ?) WHERE stanza_id = ?",
        [(parent, db.now_iso(), m.stanza_id) for m in msgs])
    conn.commit()


# ---------------------------------------------------------------------------
# One pass
# ---------------------------------------------------------------------------

def read_chats(src_dir: Path, tmp: Path, since: datetime) -> list[Chat]:
    return _read(src_dir, tmp, since)[0]


def _read(src_dir: Path, tmp: Path,
          since: datetime) -> tuple[list[Chat], datetime | None]:
    """The chats since `since`, and the newest message time in the copy."""
    copy = wa.snapshot(src_dir, tmp / "db")
    rconn = sqlite3.connect(copy)
    try:
        missing = wa.check_schema(rconn)
        if missing:
            raise wa.WhatsAppError("schema changed, missing: " + ", ".join(missing))
        return (wa.read(rconn, since, Path(src_dir) / wa.MEDIA_DIR),
                wa.newest(rconn))
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
        chats, newest = _read(src_dir, tmp, now - timedelta(days=since_days))
        result.messages = sum(len(c.messages) for c in chats)
        if newest is not None and now - newest > STALE_AFTER:
            # Not a quiet two days: the Mac app is closed or not syncing.
            result.stale_since = newest
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
