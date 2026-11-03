"""The coverage register, measured (R7, D-069).

config/sources.yaml says what every source is, how it is read and what it
cannot see. This module adds what only the running system knows: when each
one was last checked successfully, whether the last check failed, and how
many scheduled checks have been missed since. The addendum, §6:

    "Log successful checks and disclose or retry missed intervals."

    "Never report the day as fully reviewed unless every registered source
     completed or its gap is explicitly disclosed."

So the output that matters is `disclosures()`: one line for every source that
is not fully covered right now, for whatever reason. An empty list is the
only thing that may be called "fully reviewed", and today it is not empty —
most sources are pending an answer from Matthew. That is the point of
listing them.

Readers record each pass with `record_check` (source_checks, append-only like
actions_log). A reader that cannot tell success from a partial pass reports
PARTIAL: a mail poll that hit its --limit is a missed-mail event, not a quiet
success.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from ..db import database as db

CONFIG = Path(__file__).resolve().parents[2] / "config"

KINDS = ("email", "messages", "calls", "chat", "calendar", "storage",
         "capture", "planning")
STATUSES = ("active", "pending", "blocked", "unsupported")
OK, PARTIAL, FAILED = "OK", "PARTIAL", "FAILED"
RESULTS = (OK, PARTIAL, FAILED)

# How late a scheduled check may be before it counts as missed. A 12-hourly
# poll that runs at 07:00 and lands at 07:04 is on time.
GRACE_FRACTION = 0.25
MIN_GRACE = timedelta(minutes=15)


class RegisterError(ValueError):
    pass


@dataclass(frozen=True)
class Source:
    id: str
    label: str
    kind: str
    method: str
    permissions: str
    status: str
    requirement: str
    cadence_hours: float | None = None
    account: str | None = None
    blocked_on: str | None = None
    gaps: str | None = None

    @property
    def scheduled(self) -> bool:
        """Active and polled on a schedule, so it can miss a check."""
        return self.status == "active" and self.cadence_hours is not None


_REQUIRED = ("id", "label", "kind", "method", "permissions", "status", "requirement")
_ALLOWED = set(_REQUIRED) | {"cadence_hours", "account", "blocked_on", "gaps"}


def _source(raw: dict[str, Any]) -> Source:
    sid = raw.get("id") or "?"
    unknown = set(raw) - _ALLOWED
    if unknown:
        raise RegisterError(f"source {sid}: unknown keys {sorted(unknown)}")
    for key in _REQUIRED:
        if not raw.get(key):
            raise RegisterError(f"source {sid}: {key} is required")
    if raw["kind"] not in KINDS:
        raise RegisterError(f"source {sid}: kind is one of {KINDS}")
    if raw["status"] not in STATUSES:
        raise RegisterError(f"source {sid}: status is one of {STATUSES}")
    if raw["status"] != "active" and not raw.get("blocked_on"):
        # A source that does not work without saying why is the silent gap
        # this register exists to prevent.
        raise RegisterError(f"source {sid}: a {raw['status']} source says what "
                            f"it is blocked on")
    cadence = raw.get("cadence_hours")
    if cadence is not None and float(cadence) <= 0:
        raise RegisterError(f"source {sid}: cadence_hours must be positive")
    return Source(
        id=str(raw["id"]), label=str(raw["label"]), kind=raw["kind"],
        method=str(raw["method"]).strip(), permissions=str(raw["permissions"]),
        status=raw["status"], requirement=str(raw["requirement"]),
        cadence_hours=float(cadence) if cadence is not None else None,
        account=raw.get("account"), blocked_on=raw.get("blocked_on"),
        gaps=raw.get("gaps"))


def load_register(config_dir: Path | str = CONFIG) -> tuple[Source, ...]:
    raw = yaml.safe_load((Path(config_dir) / "sources.yaml").read_text(encoding="utf-8"))
    sources = tuple(_source(s) for s in (raw or {}).get("sources") or ())
    ids = [s.id for s in sources]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise RegisterError(f"duplicate source ids: {dupes}")
    return sources


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------

def record_check(conn: sqlite3.Connection, source_id: str, result: str, *,
                 started_at: str | None = None, items: int | None = None,
                 detail: str | None = None) -> None:
    """One pass of one reader over one source. Called for every pass,
    including failed ones: a failure nobody recorded looks like a pass that
    never ran."""
    if result not in RESULTS:
        raise ValueError(f"result is one of {RESULTS}, not {result!r}")
    conn.execute(
        "INSERT INTO source_checks (source_id, started_at, finished_at, result, "
        "items, detail) VALUES (?,?,?,?,?,?)",
        (source_id, started_at, db.now_iso(), result, items,
         detail[:400] if detail else None))


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Coverage:
    source: Source
    last_ok: datetime | None
    last_result: str | None
    last_at: datetime | None
    last_detail: str | None
    missed: int                  # scheduled checks missed since the last OK

    @property
    def current(self) -> bool:
        """Checked successfully within its schedule, last pass clean."""
        return (self.source.scheduled and self.last_ok is not None
                and self.missed == 0 and self.last_result == OK)


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    dt = datetime.fromisoformat(ts)
    return dt if dt.tzinfo else dt.replace(tzinfo=db.TZ)


def _missed(source: Source, last_ok: datetime | None, now: datetime) -> int:
    if not source.scheduled:
        return 0
    cadence = timedelta(hours=source.cadence_hours)
    if last_ok is None:
        return 1                 # never checked: at least one is owed
    grace = max(cadence * GRACE_FRACTION, MIN_GRACE)
    late = now - last_ok - grace
    return max(0, int(late / cadence)) if late > timedelta(0) else 0


def coverage(conn: sqlite3.Connection, register: tuple[Source, ...] | None = None,
             now: datetime | None = None) -> list[Coverage]:
    register = register if register is not None else load_register()
    now = (now or datetime.now(db.TZ)).astimezone(db.TZ)
    out = []
    for s in register:
        ok = conn.execute(
            "SELECT finished_at FROM source_checks WHERE source_id = ? AND "
            "result = 'OK' ORDER BY id DESC LIMIT 1", (s.id,)).fetchone()
        last = conn.execute(
            "SELECT finished_at, result, detail FROM source_checks WHERE "
            "source_id = ? ORDER BY id DESC LIMIT 1", (s.id,)).fetchone()
        last_ok = _parse(ok["finished_at"]) if ok else None
        out.append(Coverage(
            source=s, last_ok=last_ok,
            last_result=last["result"] if last else None,
            last_at=_parse(last["finished_at"]) if last else None,
            last_detail=last["detail"] if last else None,
            missed=_missed(s, last_ok, now)))
    return out


def unregistered(conn: sqlite3.Connection,
                 register: tuple[Source, ...] | None = None) -> list[str]:
    """Sources some reader checks that the register does not list."""
    register = register if register is not None else load_register()
    known = {s.id for s in register}
    rows = conn.execute("SELECT DISTINCT source_id FROM source_checks").fetchall()
    return sorted(r["source_id"] for r in rows if r["source_id"] not in known)


def disclosures(conn: sqlite3.Connection, register: tuple[Source, ...] | None = None,
                now: datetime | None = None, *, active_only: bool = False) -> list[str]:
    """Every gap in coverage right now, one line each, worst first.

    Empty means every registered source completed its schedule — the only
    state the daily reconciliation may call fully reviewed.

    `active_only` is the brief's daily view: sources that are meant to be
    read and are not being read on time. It leaves out the sources not
    connected yet (the Monday brief counts those) and "never checked", which
    on a new install is every source and would be an alarm on day one, the
    same rule as the brief's stalled jobs.
    """
    lines: list[tuple[int, str]] = []
    for c in coverage(conn, register, now):
        s = c.source
        if s.status != "active":
            if active_only:
                continue
            lines.append((3, f"{s.label}: not read — {s.status}, "
                             f"{s.blocked_on or 'no route'} ({s.requirement})"))
            continue
        if not s.scheduled:
            continue             # pushed to us; nothing to miss
        if c.last_result == FAILED:
            lines.append((0, f"{s.label}: last check FAILED "
                             f"{c.last_at:%b %d %H:%M}"
                             + (f" — {c.last_detail}" if c.last_detail else "")))
        elif c.last_result == PARTIAL:
            lines.append((2, f"{s.label}: last check was partial"
                             + (f" — {c.last_detail}" if c.last_detail else "")
                             + ("" if c.last_ok else "; no complete check yet")))
        elif c.last_ok is None:
            if not active_only:
                lines.append((1, f"{s.label}: never checked"))
        elif c.missed:
            lines.append((1, f"{s.label}: {c.missed} scheduled check(s) missed; "
                             f"last good {c.last_ok:%b %d %H:%M}"))
    for sid in unregistered(conn, register):
        lines.append((2, f"{sid}: checked but not in the coverage register"))
    return [text for _, text in sorted(lines, key=lambda x: x[0])]


def fully_reviewed(conn: sqlite3.Connection, register: tuple[Source, ...] | None = None,
                   now: datetime | None = None) -> bool:
    return not disclosures(conn, register, now)
