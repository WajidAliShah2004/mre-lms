"""Database access for the LMS.

Deliberately thin. There is no ORM and no query builder: the schema is small,
the queries are few, and a future maintainer reading raw SQL will understand
this faster than they would understand a mapping layer.

Everything is timestamped in America/New_York, never UTC offsets, because
scheduled jobs are declared in local time and mixing the two across a DST
boundary is the classic way to lose an hour of a nightly window.
"""

from __future__ import annotations

import sqlite3
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/New_York")
SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def now_iso() -> str:
    """Local wall-clock time, ISO8601 with offset. Used for every ts column."""
    return datetime.now(TZ).isoformat(timespec="seconds")


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    """Streamed so a 400 MB scan doesn't land in memory."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open the database, applying the schema if it isn't there yet.

    Safe to call on every process start: schema.sql is entirely
    CREATE ... IF NOT EXISTS.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

    # executescript ends any implicit transaction; re-assert the pragmas that
    # are per-connection rather than persisted in the file.
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

def already_processed(conn: sqlite3.Connection, sha256: str, stage: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM processed WHERE sha256 = ? AND stage = ?", (sha256, stage)
    ).fetchone()
    return row is not None


def mark_processed(conn: sqlite3.Connection, sha256: str, stage: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO processed (sha256, stage, ts) VALUES (?, ?, ?)",
        (sha256, stage, now_iso()),
    )


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------

def find_artifact_by_hash(conn: sqlite3.Connection, sha256: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM artifacts WHERE sha256 = ?", (sha256,)
    ).fetchone()


def insert_artifact(conn: sqlite3.Connection, **fields: Any) -> int:
    """Insert a new artifact. Caller must have checked for a duplicate first.

    Returns the new row id.
    """
    fields.setdefault("ingested_at", now_iso())
    cols = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    cur = conn.execute(
        f"INSERT INTO artifacts ({cols}) VALUES ({marks})", tuple(fields.values())
    )
    return int(cur.lastrowid)


def record_duplicate(conn: sqlite3.Connection, sha256: str, source: str,
                     source_ref: str | None, original_name: str | None) -> None:
    """A re-fed file is not filed again — but we keep the sighting.

    A document arriving twice usually means a resend or a chase, which is
    signal, not noise.
    """
    conn.execute(
        "INSERT INTO duplicate_refs (sha256, seen_at, source, source_ref, original_name) "
        "VALUES (?, ?, ?, ?, ?)",
        (sha256, now_iso(), source, source_ref, original_name),
    )


def set_artifact_status(conn: sqlite3.Connection, artifact_id: int, status: str,
                        filed_path: str | None = None) -> None:
    if filed_path is None:
        conn.execute("UPDATE artifacts SET status = ? WHERE id = ?", (status, artifact_id))
    else:
        conn.execute(
            "UPDATE artifacts SET status = ?, filed_path = ? WHERE id = ?",
            (status, filed_path, artifact_id),
        )


# ---------------------------------------------------------------------------
# Append-only log
# ---------------------------------------------------------------------------

def log_action(conn: sqlite3.Connection, action: str, *, artifact_id: int | None = None,
               detail: str | None = None, approved_by: str | None = None,
               undo_token: str | None = None, undo_expires_at: str | None = None) -> None:
    """Append to actions_log.

    Note there is no update_action or delete_action, and the schema has
    triggers that abort either. Declining to act is logged too — a phishing
    email that produced no action still leaves a row saying so.
    """
    conn.execute(
        "INSERT INTO actions_log (ts, action, artifact_id, detail, approved_by, "
        "undo_token, undo_expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (now_iso(), action, artifact_id, detail, approved_by, undo_token, undo_expires_at),
    )


def record_correction(conn: sqlite3.Connection, artifact_id: int, field: str,
                      old_value: str | None, new_value: str | None) -> None:
    conn.execute(
        "INSERT INTO corrections (artifact_id, field, old_value, new_value, ts) "
        "VALUES (?, ?, ?, ?, ?)",
        (artifact_id, field, old_value, new_value, now_iso()),
    )


# ---------------------------------------------------------------------------
# Classifications and tasks
# ---------------------------------------------------------------------------

def insert_classification(conn: sqlite3.Connection, **fields: Any) -> int:
    fields.setdefault("created_at", now_iso())
    cols = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    cur = conn.execute(
        f"INSERT INTO classifications ({cols}) VALUES ({marks})", tuple(fields.values())
    )
    return int(cur.lastrowid)


def insert_task(conn: sqlite3.Connection, **fields: Any) -> int:
    fields.setdefault("created_at", now_iso())
    cols = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    cur = conn.execute(
        f"INSERT INTO tasks ({cols}) VALUES ({marks})", tuple(fields.values())
    )
    return int(cur.lastrowid)


def open_tasks(conn: sqlite3.Connection, entity_id: str | None = None,
               person: str | None = None) -> Iterable[sqlite3.Row]:
    sql = "SELECT * FROM tasks WHERE status = 'OPEN'"
    args: list[Any] = []
    if entity_id:
        sql += " AND entity_id = ?"
        args.append(entity_id)
    if person:
        sql += " AND person = ?"
        args.append(person)
    sql += " ORDER BY (due_date IS NULL), due_date ASC"
    return conn.execute(sql, tuple(args)).fetchall()
