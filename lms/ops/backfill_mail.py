#!/usr/bin/env python3
"""Historical mail, inventoried first and ingested in bounded batches (R28).

    ./ops/backfill_mail.py matthew@mrecai.com --days 365 --plan     # windows only; no Gmail, no DB
    ./ops/backfill_mail.py matthew@mrecai.com --days 365            # inventory: list and count, file nothing
    ./ops/backfill_mail.py matthew@mrecai.com --days 365 --ingest   # inventory, then the 4 oldest windows
    ./ops/backfill_mail.py matthew@mrecai.com --days 365 --ingest --batch 10
    ./ops/backfill_mail.py matthew@mrecai.com --status              # what is inventoried and ingested

Addendum §4, "Backfill safely": "Inventory all accessible files and mail
attachments first, record source IDs, paths, owners, dates, sizes and
checksums, then ingest in bounded batches."

WHY NOT JUST `poll_mail.py --days 365`
--------------------------------------
Sept 29: a 14-day catch-up returned exactly 100 messages and stopped at
Sept 22. A week of mail was absent from a listing that looked complete. The
poller now defaults to 500 and warns when it hits the limit, which turns a
silent gap into a loud one — but a year of mail is thousands of messages, and
raising the limit until it fits makes one run unbounded in time, API quota and
damage if it dies halfway. The fix is to cut the range, not to widen the pipe.

HOW
---
1. PLAN. The range is cut into --window-days windows on a FIXED calendar grid
   (local midnights counted from Monday 2000-01-03), so a window has the same
   bounds on every run, and an interrupted backfill resumes instead of
   re-planning around a moving "now".
2. INVENTORY. Each window is LISTED — ids only, no message fetched. A window
   whose listing reaches --limit is split in half and listed again, down to one
   hour, so every window that is ingested is known to be under the limit. Its
   count and every message id go into backfill_windows / backfill_items
   before anything is ingested. A window still at the limit at one hour is
   recorded as truncated and printed as a gap.
3. INGEST. The oldest --batch windows not yet ingested go through
   poll_mail.poll_mailbox — the exact code the scheduled poll runs, with the
   window as its query — so failure handling, logging and truncation warnings
   are the poller's, not a second copy of them. Re-running is safe: dedupe is
   the sha256 of the rendered message (core/pipeline/mail.py).
4. RECONCILE. --status shows, per window, how many inventoried ids now have an
   artifact. Sizes, dates and checksums are recorded at ingest in `artifacts`
   (Gmail's listing returns ids only), keyed back by source_ref = message id.

Gmail is asked `after:<epoch − 1> before:<epoch>` — seconds, not dates. The
Gmail API reads `after:2026/03/01` as midnight PACIFIC time, which would shift
every window three hours against the New York calendar it was planned on.
The one-second overlap means a message stamped exactly on a boundary is
listed twice (harmless, deduped) rather than by neither window.

Read-only on the mailbox like poll_mail.py: the grant is gmail.readonly.
"""

from __future__ import annotations

import argparse
import math
import os
import socket
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import poll_mail                                           # noqa: E402
from _env import archive_volume_problem, load_lms_env      # noqa: E402
from _reexec import ensure_venv                            # noqa: E402
from core.db import database as db                         # noqa: E402
from core.pipeline import filing                           # noqa: E402
from core.pipeline.classify import Classifier              # noqa: E402
from core.pipeline.registry import load_registry           # noqa: E402

TZ = db.TZ
ANCHOR = date(2000, 1, 3)          # a Monday: weekly windows run Monday-Sunday
MIN_SPAN = timedelta(hours=1)
DEFAULT_LIMIT = 500                # poll_mail.py's default, for the same reason
DEFAULT_BATCH = 4


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Window:
    """[start, end) in America/New_York."""
    start: datetime
    end: datetime

    @property
    def span(self) -> timedelta:
        return self.end - self.start

    def query(self) -> str:
        return (f"after:{int(self.start.timestamp()) - 1} "
                f"before:{int(self.end.timestamp())}")

    def newer_than_days(self, now: datetime) -> int:
        """`recent()` always ANDs newer_than:Nd; N must reach past the start."""
        return max(1, math.ceil((now - self.start).total_seconds() / 86400) + 1)

    def halves(self) -> tuple["Window", "Window"]:
        mid = datetime.fromtimestamp(
            (int(self.start.timestamp()) + int(self.end.timestamp())) // 2, TZ)
        return Window(self.start, mid), Window(mid, self.end)

    def key(self) -> tuple[str, str]:
        return (self.start.isoformat(timespec="seconds"),
                self.end.isoformat(timespec="seconds"))

    def label(self) -> str:
        fmt = ("%Y-%m-%d" if self.start.hour == self.end.hour == 0
               and self.start.minute == self.end.minute == 0 else "%Y-%m-%d %H:%M")
        return f"{self.start.strftime(fmt)} → {self.end.strftime(fmt)}"


def _midnight(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=TZ)


def plan_windows(days: int, window_days: int = 7, *, today: date) -> list[Window]:
    """Oldest first, contiguous, on the fixed grid, covering at least `days`
    up to the end of `today`. The newest window may end in the future."""
    if days < 1 or window_days < 1:
        raise ValueError("days and window_days must be at least 1")
    end_day = today + timedelta(days=1)
    start_day = end_day - timedelta(days=days)
    d = ANCHOR + timedelta(days=((start_day - ANCHOR).days // window_days) * window_days)
    out = []
    while d < end_day:
        nxt = d + timedelta(days=window_days)
        out.append(Window(_midnight(d), _midnight(nxt)))
        d = nxt
    return out


@dataclass
class Listed:
    window: Window
    ids: list[str] = field(default_factory=list)
    limit: int = DEFAULT_LIMIT

    @property
    def truncated(self) -> bool:
        return len(self.ids) >= self.limit


def inventory(windows: list[Window], list_ids: Callable[[Window, int], list[str]],
              limit: int, *, min_span: timedelta = MIN_SPAN) -> list[Listed]:
    """List every window; split any that reaches `limit` until it does not.

    Reaching the limit EXACTLY is treated as truncated: a listing of exactly
    500 cannot say whether there were 501.
    """
    out: list[Listed] = []
    todo = list(windows)
    while todo:
        w = todo.pop(0)
        ids = list(list_ids(w, limit))
        if len(ids) >= limit and w.span >= 2 * min_span:
            todo[:0] = list(w.halves())          # older half first
            continue
        out.append(Listed(w, ids, limit))
    return out


# ---------------------------------------------------------------------------
# The inventory, recorded
# ---------------------------------------------------------------------------

def record_inventory(conn, mailbox: str, listed: list[Listed]) -> None:
    """Upsert one row per window and one per message id. A window whose count
    changed since it was ingested is reset to pending, so it is read again."""
    now = db.now_iso()
    conn.execute("BEGIN")
    try:
        for item in listed:
            start, end = item.window.key()
            conn.execute(
                "INSERT INTO backfill_windows (mailbox, window_start, window_end, "
                "query, listed, list_limit, truncated, inventoried_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(mailbox, window_start, window_end) DO UPDATE SET "
                "  ingested_at = CASE WHEN excluded.listed != backfill_windows.listed "
                "                     THEN NULL ELSE backfill_windows.ingested_at END, "
                "  outcome = CASE WHEN excluded.listed != backfill_windows.listed "
                "                 THEN NULL ELSE backfill_windows.outcome END, "
                "  query = excluded.query, listed = excluded.listed, "
                "  list_limit = excluded.list_limit, truncated = excluded.truncated, "
                "  inventoried_at = excluded.inventoried_at",
                (mailbox, start, end, item.window.query(), len(item.ids), item.limit,
                 int(item.truncated), now))
            wid = window_id(conn, mailbox, item.window)
            conn.executemany(
                "INSERT INTO backfill_items (mailbox, message_id, window_id, "
                "inventoried_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(mailbox, message_id) DO UPDATE SET "
                "  window_id = excluded.window_id, "
                "  inventoried_at = excluded.inventoried_at",
                [(mailbox, mid, wid, now) for mid in item.ids])
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    gaps = sum(1 for i in listed if i.truncated)
    db.log_action(conn, "BACKFILL_INVENTORY", detail=(
        f"{mailbox}: {len(listed)} window(s), {sum(len(i.ids) for i in listed)} "
        f"message id(s), {gaps} truncated"))


def window_id(conn, mailbox: str, w: Window) -> int:
    start, end = w.key()
    return int(conn.execute(
        "SELECT id FROM backfill_windows WHERE mailbox = ? AND window_start = ? "
        "AND window_end = ?", (mailbox, start, end)).fetchone()[0])


def pending(conn, mailbox: str, listed: list[Listed], *,
            retry_failed: bool = False) -> list[tuple[int, Window]]:
    """Windows of THIS plan not yet ingested, oldest first."""
    out = []
    for item in listed:
        wid = window_id(conn, mailbox, item.window)
        outcome = conn.execute("SELECT outcome FROM backfill_windows WHERE id = ?",
                               (wid,)).fetchone()[0]
        if outcome is None or (retry_failed and outcome == "FAILED"):
            out.append((wid, item.window))
    return out


def ingest_windows(conn, mailbox: str, todo: list[tuple[int, Window]],
                   ingest_one: Callable[[Window], int]) -> list[tuple[Window, str]]:
    """Run each window through `ingest_one` (poll_mail.poll_mailbox in main).

    A failed window is recorded FAILED and the next one still runs — the same
    rule as poll_mail.file_messages, one level up. A failed window is not
    retried automatically, or the same broken message would take the whole
    batch every run; --retry-failed asks for it.
    """
    done = []
    for wid, w in todo:
        db.log_action(conn, "BACKFILL_WINDOW_START", detail=f"{mailbox} {w.label()}")
        try:
            bad = ingest_one(w)
        except Exception as exc:
            db.log_action(conn, "BACKFILL_WINDOW_FAILED",
                          detail=f"{mailbox} {w.label()}: {type(exc).__name__}: "
                                 f"{exc}"[:400])
            bad = 1
        outcome = "FAILED" if bad else "OK"
        conn.execute("UPDATE backfill_windows SET ingested_at = ?, outcome = ? "
                     "WHERE id = ?", (db.now_iso(), outcome, wid))
        db.log_action(conn, "BACKFILL_WINDOW_DONE",
                      detail=f"{mailbox} {w.label()}: {outcome}")
        done.append((w, outcome))
    return done


def status(conn, mailbox: str) -> str:
    """Per window: listed, how many of those ids now have an artifact, outcome."""
    have = {r[0] for r in conn.execute(
        "SELECT source_ref FROM artifacts WHERE source = 'email'")}
    rows = conn.execute(
        "SELECT id, window_start, window_end, listed, truncated, ingested_at, outcome "
        "FROM backfill_windows WHERE mailbox = ? ORDER BY window_start, window_end",
        (mailbox,)).fetchall()
    if not rows:
        return f"{mailbox}: nothing inventoried yet\n"
    out = [f"{mailbox}",
           f"  {'window':<36} {'listed':>6} {'have':>6}  {'ingested':<10} outcome"]
    totals = [0, 0]
    for r in rows:
        ids = [x[0] for x in conn.execute(
            "SELECT message_id FROM backfill_items WHERE window_id = ?", (r["id"],))]
        seen = sum(1 for i in ids if i in have)
        totals[0] += r["listed"]
        totals[1] += seen
        w = Window(datetime.fromisoformat(r["window_start"]),
                   datetime.fromisoformat(r["window_end"]))
        flag = "  TRUNCATED — a gap" if r["truncated"] else ""
        out.append(f"  {w.label():<36} {r['listed']:>6} {seen:>6}  "
                   f"{(r['ingested_at'] or 'pending')[:10]:<10} "
                   f"{r['outcome'] or '—'}{flag}")
    left = sum(1 for r in rows if r["outcome"] is None)
    out.append(f"  {'total':<36} {totals[0]:>6} {totals[1]:>6}  "
               f"{left} window(s) pending")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    ensure_venv("LMS_BACKFILL_REEXEC", script=__file__)
    socket.setdefaulttimeout(120)        # same reason as poll_mail.py (Oct 3)
    load_lms_env()

    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("address")
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--window-days", type=int, default=7)
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                   help="per-window listing limit; a window that reaches it is split")
    p.add_argument("--query", default="",
                   help="extra Gmail search terms, ANDed with each window")
    p.add_argument("--plan", action="store_true",
                   help="print the windows and stop; touches neither Gmail nor the DB")
    p.add_argument("--status", action="store_true",
                   help="print what is inventoried and ingested, and stop")
    p.add_argument("--ingest", action="store_true",
                   help="after the inventory, ingest up to --batch pending windows")
    p.add_argument("--batch", type=int, default=DEFAULT_BATCH,
                   help=f"windows ingested per run (default {DEFAULT_BATCH})")
    p.add_argument("--retry-failed", action="store_true",
                   help="also ingest windows whose last ingest FAILED")
    args = p.parse_args()
    address = args.address.lower()

    now = datetime.now(TZ)
    windows = plan_windows(args.days, args.window_days, today=now.date())
    if args.plan:
        for w in windows:
            print(f"  {w.label()}   {w.query()}")
        print(f"{len(windows)} window(s). Nothing was listed or written.")
        return 0

    problem = archive_volume_problem()
    if problem:
        print(f"NOT BACKFILLING: {problem}", file=sys.stderr)
        return 1
    roots = filing.StorageRoots.from_env()
    roots.ensure()
    conn = db.connect(os.environ.get("LMS_DB", str(roots.archive.parent / "lms.db")))
    try:
        if args.status:
            print(status(conn, address), end="")
            return 0
        return backfill(conn, address, windows, args, roots, now)
    finally:
        conn.close()


def backfill(conn, address, windows, args, roots, now) -> int:
    try:
        client = poll_mail.build_client(address)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 1

    def list_ids(w: Window, limit: int) -> list[str]:
        q = f"{w.query()} {args.query}".strip()
        return client.message_ids(newer_than_days=w.newer_than_days(now),
                                  limit=limit, query=q)

    listed = inventory(windows, list_ids, args.limit)
    record_inventory(conn, address, listed)
    total = sum(len(i.ids) for i in listed)
    print(f"{address}: {len(listed)} window(s), {total} message(s) listed "
          f"over {args.days}d", flush=True)
    for item in listed:
        if item.truncated:
            print(f"WARNING: {item.window.label()} still lists {len(item.ids)} at "
                  f"--limit {args.limit} after splitting to one hour. Messages "
                  f"beyond the limit in that hour are NOT in this backfill; "
                  f"re-run with a larger --limit.", flush=True)

    if not args.ingest:
        print(status(conn, address), end="")
        print("Inventory only. Nothing was ingested; add --ingest to file "
              f"the oldest {args.batch} pending window(s).")
        return 0

    registry = load_registry()
    classifier = Classifier(registry)
    spool = Path(os.environ.get(
        "LMS_MAIL_SPOOL", str(roots.archive.parent / "spool" / "mail")))

    def ingest_one(w: Window) -> int:
        poll_args = SimpleNamespace(days=w.newer_than_days(now), limit=args.limit,
                                    query=f"{w.query()} {args.query}".strip())
        print(f"\n--- {w.label()} ---", flush=True)
        # Not a check of the mailbox now (coverage register, D-069).
        return poll_mail.poll_mailbox(conn, address, poll_args, registry,
                                      classifier, roots, spool,
                                      checks_source=False)

    todo = pending(conn, address, listed, retry_failed=args.retry_failed)
    done = ingest_windows(conn, address, todo[:args.batch], ingest_one)
    print()
    print(status(conn, address), end="")
    left = len(todo) - len(done)
    print(f"{len(done)} window(s) ingested this run; {left} still pending"
          + (" — run again to continue." if left else "."))
    return 1 if any(o == "FAILED" for _, o in done) else 0


if __name__ == "__main__":
    raise SystemExit(main())
