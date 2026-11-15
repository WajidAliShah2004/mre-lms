"""The drive readers' engine (R5, D-083): index every file, file new documents.

One pass over one drive:

1. Index. The drive's lister returns its files: all of them, or the changes
   since the stored cursor. Each is upserted into drive_files; files gone
   from the drive get deleted_at. No row is ever removed.
2. Choose. A file is filed when it is a document, in an area that is filed,
   created or changed on or after the drive's start date, unchanged for 24
   hours, at most 100 MB, its current revision is not already filed, and it
   has failed fewer than 3 times.
3. File. The fetcher copies its bytes into a temp directory, and ingest_file
   files that copy (filing moves what it is given, so it is always a copy).
   The original in the drive is never touched.

The start date is the day of the drive's first pass (drive_cursors). Its
history is the backfill's (ops/backfill_drives.py).
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from ..adapters.drive import DriveFile, FetchError, Fetcher, Lister
from ..db import database as db
from . import coverage
from .classify import Artifact
from .ingest import ingest_file

TZ = ZoneInfo("America/New_York")
SETTLE = timedelta(hours=24)
MAX_BYTES = 100 * 1024 * 1024
MAX_ATTEMPTS = 3
FILED_AREAS = frozenset({"own", "shared_drive"})
CHECKED = "DRIVES_CHECKED"
FAILED = "DRIVES_FAILED"
FILE_FAILED = "DRIVE_FILE_FAILED"


def _never() -> bool:
    return False


@dataclass(frozen=True)
class DriveSource:
    source_id: str
    label: str
    lister: Lister
    fetcher: Fetcher
    files_documents: bool = True
    filed_areas: frozenset[str] = FILED_AREAS


@dataclass
class PassResult:
    source_id: str
    listed: int = 0
    new: int = 0
    changed: int = 0
    deleted: int = 0
    full: bool = False
    filed: int = 0
    duplicates: int = 0
    review: int = 0
    failed: int = 0
    too_large: int = 0
    error: str | None = None
    failures: list[str] = field(default_factory=list)

    @property
    def result(self) -> str:
        if self.error:
            return "FAILED"
        return "PARTIAL" if self.failed else "OK"

    def summary(self) -> str:
        if self.error:
            return self.error
        s = (f"{self.listed} file(s) listed ({'full' if self.full else 'changes'}): "
             f"{self.new} new, {self.changed} changed, {self.deleted} deleted; "
             f"{self.filed} filed, {self.duplicates} already in the archive")
        if self.review:
            s += f", {self.review} to review"
        if self.too_large:
            s += f", {self.too_large} over 100 MB not filed"
        if self.failed:
            s += f", {self.failed} FAILED (see {FILE_FAILED} in actions_log)"
        return s


def when(iso: str) -> datetime:
    d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=TZ)


def local_day(d: datetime) -> str:
    return d.astimezone(TZ).date().isoformat()


def start_date(conn: sqlite3.Connection, source_id: str) -> str | None:
    row = conn.execute("SELECT started_on FROM drive_cursors WHERE source_id = ?",
                       (source_id,)).fetchone()
    return row["started_on"] if row else None


def started_on(conn: sqlite3.Connection, source_id: str, now: datetime) -> str:
    """The drive's start date; the first pass sets it to today."""
    day = start_date(conn, source_id)
    if day is None:
        day = local_day(now)
        conn.execute("INSERT INTO drive_cursors (source_id, cursor, started_on, updated_at) "
                     "VALUES (?, NULL, ?, ?)", (source_id, day, db.now_iso()))
        conn.commit()
    return day


def verdict(src: DriveSource, f: DriveFile, *, filed_revision: str | None,
            attempts: int, start: str, now: datetime, until: str | None = None,
            folder: str | None = None) -> str:
    """'file', 'too_large' or '' (not filed): the spec's rules, in order."""
    if not src.files_documents or f.area not in src.filed_areas or not f.is_document:
        return ""
    if filed_revision == f.revision or attempts >= MAX_ATTEMPTS:
        return ""
    changed = when(f.modified)
    day = local_day(changed)
    if day < start or (until is not None and day > until):
        return ""
    if folder is not None:
        top = folder.rstrip("/")
        if f.path != top and not f.path.startswith(top + "/"):
            return ""
    if changed > now - SETTLE:
        return ""
    if f.size is not None and f.size > MAX_BYTES:
        return "too_large"
    return "file"


def _under(key: str) -> str:
    """A LIKE pattern for everything below `key`."""
    esc = key.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return esc.rstrip("/") + "/%"


def index(src: DriveSource, conn: sqlite3.Connection, now: datetime,
          result: PassResult) -> None:
    row = conn.execute("SELECT cursor FROM drive_cursors WHERE source_id = ?",
                       (src.source_id,)).fetchone()
    listing = src.lister.list(row["cursor"] if row else None)
    stamp = now.isoformat(timespec="seconds")
    result.full = listing.full
    result.listed = len(listing.files)
    known = {r["file_key"]: r for r in conn.execute(
        "SELECT file_key, revision, deleted_at FROM drive_files WHERE source_id = ?",
        (src.source_id,))}
    def mark_gone(keys) -> None:
        for key in sorted(keys):
            result.deleted += conn.execute(
                "UPDATE drive_files SET deleted_at = ? WHERE source_id = ? "
                "AND deleted_at IS NULL AND (file_key = ? OR file_key LIKE ? ESCAPE '\\')",
                (stamp, src.source_id, key, _under(key))).rowcount

    if not listing.full:
        # Deletions first: a file listed in the same delta is live again below.
        mark_gone(set(listing.deleted))
    for f in {f.file_key: f for f in listing.files}.values():
        old = known.get(f.file_key)
        values = (f.path, f.name, f.area, f.mime, int(f.native), f.size, f.modified,
                  f.revision)
        if old is None:
            conn.execute(
                "INSERT INTO drive_files (source_id, file_key, path, name, area, mime, "
                "native, size, modified, revision, first_seen, last_seen) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (src.source_id, f.file_key, *values, stamp, stamp))
            result.new += 1
            continue
        changed = old["revision"] != f.revision
        if changed:
            result.changed += 1
        conn.execute(
            "UPDATE drive_files SET path = ?, name = ?, area = ?, mime = ?, native = ?, "
            "size = ?, modified = ?, revision = ?, last_seen = ?, deleted_at = NULL"
            + (", attempts = 0, last_error = NULL" if changed else "")
            + " WHERE source_id = ? AND file_key = ?",
            (*values, stamp, src.source_id, f.file_key))
    if listing.full:
        seen = {f.file_key for f in listing.files}
        mark_gone(set(listing.deleted)
                  | {k for k, r in known.items() if k not in seen and r["deleted_at"] is None})
    conn.execute(
        "INSERT INTO drive_cursors (source_id, cursor, started_on, updated_at) "
        "VALUES (?, ?, ?, ?) ON CONFLICT(source_id) DO UPDATE SET "
        "cursor = excluded.cursor, updated_at = excluded.updated_at",
        (src.source_id, listing.cursor, local_day(now), db.now_iso()))
    conn.commit()


def _row_file(source_id: str, r: sqlite3.Row) -> DriveFile:
    return DriveFile(source_id, r["file_key"], r["path"], r["name"], r["size"],
                     r["modified"], r["revision"], r["area"], r["mime"], bool(r["native"]))


def choose(src: DriveSource, conn: sqlite3.Connection, now: datetime, *, start: str,
           until: str | None = None, folder: str | None = None,
           limit: int | None = None) -> tuple[list[DriveFile], int]:
    """(files to file, oldest first; how many documents are over 100 MB)."""
    if not src.files_documents:
        return [], 0
    chosen: list[DriveFile] = []
    big = 0
    for r in conn.execute("SELECT * FROM drive_files WHERE source_id = ? "
                          "AND deleted_at IS NULL", (src.source_id,)).fetchall():
        f = _row_file(src.source_id, r)
        v = verdict(src, f, filed_revision=r["filed_revision"], attempts=r["attempts"],
                    start=start, now=now, until=until, folder=folder)
        if v == "file":
            chosen.append(f)
        elif v == "too_large":
            big += 1
    chosen.sort(key=lambda f: (when(f.modified), f.file_key))
    return (chosen[:limit] if limit else chosen), big


def file_rows(src: DriveSource, conn: sqlite3.Connection, roots, registry, classifier,
              files: list[DriveFile], tmp: Path, result: PassResult, *,
              ingest=ingest_file, halted: Callable[[], bool] = _never) -> int:
    """File each file from a temp copy. Returns how many were handled."""
    done = 0
    for f in files:
        if halted():
            break
        ref = f"{f.file_key}@{f.revision}"
        work = Path(tempfile.mkdtemp(prefix="file-", dir=tmp))
        try:
            copy = src.fetcher.fetch(f, work)
            if not Path(copy).resolve().is_relative_to(work.resolve()):
                raise FetchError(f"{f.path}: the reader returned a path outside its temp directory")
            art = Artifact(source=src.source_id, source_ref=ref,
                           subject=f"{src.label}: {f.path}",
                           received_date=local_day(when(f.modified)))
            res = ingest(conn, roots, registry, classifier, copy, source=src.source_id,
                         source_ref=ref, artifact=art)
        except Exception as exc:                              # noqa: BLE001
            why = f"{type(exc).__name__}: {exc}"
            result.failed += 1
            result.failures.append(f"{f.file_key}: {why}")
            conn.execute("UPDATE drive_files SET attempts = attempts + 1, last_error = ? "
                         "WHERE source_id = ? AND file_key = ?",
                         (why[:400], src.source_id, f.file_key))
            db.log_action(conn, FILE_FAILED, detail=f"{src.source_id} {f.file_key}: {why}"[:400])
            conn.commit()
            continue
        finally:
            shutil.rmtree(work, ignore_errors=True)
        art_row = db.find_artifact_by_hash(conn, res.sha256)
        conn.execute("UPDATE drive_files SET filed_revision = ?, artifact_id = ?, sha256 = ?, "
                     "attempts = 0, last_error = NULL WHERE source_id = ? AND file_key = ?",
                     (f.revision, int(art_row["id"]) if art_row else None, res.sha256,
                      src.source_id, f.file_key))
        conn.commit()
        if res.status == "DUPLICATE":
            result.duplicates += 1
        elif res.status in ("QUARANTINED", "SUSPECTED_PHISHING"):
            result.review += 1
        else:
            result.filed += 1
        done += 1
    return done


def plan(src: DriveSource, conn: sqlite3.Connection,
         now: datetime) -> tuple[list[DriveFile], int, int]:
    """For --dry-run: (would file, over 100 MB, listed), from a full live
    listing and what is already filed. Writes nothing."""
    listing = src.lister.list(None)
    start = start_date(conn, src.source_id) or local_day(now)
    rows = {r["file_key"]: r for r in conn.execute(
        "SELECT file_key, revision, filed_revision, attempts FROM drive_files "
        "WHERE source_id = ?", (src.source_id,))}
    would: list[DriveFile] = []
    big = 0
    for f in listing.files:
        r = rows.get(f.file_key)
        attempts = r["attempts"] if r is not None and r["revision"] == f.revision else 0
        v = verdict(src, f, filed_revision=r["filed_revision"] if r is not None else None,
                    attempts=attempts, start=start, now=now)
        if v == "file":
            would.append(f)
        elif v == "too_large":
            big += 1
    return would, big, len(listing.files)


def run_pass(src: DriveSource, conn: sqlite3.Connection, roots, registry, classifier, *,
             now: datetime, tmp_root: Path, ingest=ingest_file,
             halted: Callable[[], bool] = _never) -> PassResult:
    """Index, choose, file; record the pass however it ends."""
    started = db.now_iso()
    result = PassResult(src.source_id)
    Path(tmp_root).mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="pass-", dir=tmp_root))
    try:
        start = started_on(conn, src.source_id, now)
        index(src, conn, now, result)
        files, result.too_large = choose(src, conn, now, start=start)
        file_rows(src, conn, roots, registry, classifier, files, tmp, result,
                  ingest=ingest, halted=halted)
    except Exception as exc:                                  # noqa: BLE001
        result.error = f"{type(exc).__name__}: {exc}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    coverage.record_check(conn, src.source_id, result.result, started_at=started,
                          items=result.listed, detail=result.summary())
    db.log_action(conn, FAILED if result.error else CHECKED,
                  detail=f"{src.source_id}: {result.summary()}"[:400])
    conn.commit()
    return result
