"""Batched mail backfill (R28; addendum §4 "Backfill safely").

Sept 29: a 14-day catch-up returned exactly 100 messages and silently stopped
a week short. These pin the planner that makes that impossible — every window
ingested is known to list under the limit — and the inventory-first order the
addendum asks for. No network: Gmail is a list of timestamps.
"""

import importlib.util
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.adapters import gmail
from core.db import database as db

OPS = Path(__file__).resolve().parents[1] / "ops"
TZ = db.TZ


def _backfill():
    spec = importlib.util.spec_from_file_location("backfill_mail_r28",
                                                  OPS / "backfill_mail.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m          # dataclasses look their module up here
    spec.loader.exec_module(m)
    return m


bf = _backfill()


class FakeMailbox:
    """Message i is stamped at times[i]. Lists like Gmail: newest first, capped."""

    def __init__(self, times):
        self.times = times
        self.calls = []

    def list_ids(self, w, limit):
        self.calls.append(w)
        ids = [f"m{i}" for i, t in sorted(enumerate(self.times), key=lambda x: -x[1].timestamp())
               if w.start <= t < w.end]
        return ids[:limit]


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

def test_the_plan_covers_the_range_without_gaps_on_a_fixed_grid():
    today = date(2026, 11, 3)
    ws = bf.plan_windows(30, 7, today=today)
    assert ws[0].start <= datetime(2026, 10, 5, tzinfo=TZ)        # 30 days back
    assert ws[-1].end >= datetime(2026, 11, 4, tzinfo=TZ)         # all of today
    for a, b in zip(ws, ws[1:]):
        assert a.end == b.start, "a gap or overlap between windows"
    assert all(w.start.weekday() == 0 for w in ws), "not on the Monday grid"
    # The next day's plan reuses the same windows, so an interrupted backfill
    # resumes instead of re-planning around a moving "now".
    tomorrow = {w.key() for w in bf.plan_windows(30, 7, today=today + timedelta(days=1))}
    assert {w.key() for w in ws[1:]} <= tomorrow


def test_the_window_query_uses_epoch_seconds_with_a_one_second_overlap():
    w = bf.Window(datetime(2026, 3, 2, tzinfo=TZ), datetime(2026, 3, 9, tzinfo=TZ))
    start, end = int(w.start.timestamp()), int(w.end.timestamp())
    assert w.query() == f"after:{start - 1} before:{end}"
    now = datetime(2026, 11, 3, 12, tzinfo=TZ)
    assert now - timedelta(days=w.newer_than_days(now)) < w.start


def test_a_window_that_reaches_the_limit_is_split_until_it_does_not():
    """1200 messages in one week, limit 500: three-plus windows, every one
    under the limit, and every message listed exactly once."""
    start = datetime(2026, 9, 21, tzinfo=TZ)
    times = [start + timedelta(minutes=8 * i) for i in range(1200)]
    box = FakeMailbox(times)
    listed = bf.inventory([bf.Window(start, start + timedelta(days=7))],
                          box.list_ids, 500)
    assert len(listed) >= 3
    assert all(len(x.ids) < 500 and not x.truncated for x in listed)
    ids = [i for x in listed for i in x.ids]
    assert sorted(ids) == sorted(f"m{i}" for i in range(1200))
    for a, b in zip(listed, listed[1:]):
        assert a.window.end == b.window.start


def test_a_window_still_full_at_one_hour_is_reported_truncated():
    start = datetime(2026, 9, 29, 9, tzinfo=TZ)
    box = FakeMailbox([start + timedelta(seconds=i) for i in range(30)])
    listed = bf.inventory([bf.Window(start, start + timedelta(days=1))], box.list_ids, 10)
    full = [x for x in listed if x.truncated]
    assert len(full) == 1
    assert full[0].window.span < 2 * bf.MIN_SPAN


def test_the_client_lists_ids_with_the_same_query_recent_uses():
    class T:
        queries = []

        def list_ids(self, q, limit):
            self.queries.append(q)
            return ["a", "b", "c"][:limit]

    t = T()
    c = gmail.GmailClient(t)
    assert c.message_ids(newer_than_days=40, limit=2, query="after:1 before:2") == ["a", "b"]
    assert t.queries == ["newer_than:40d after:1 before:2"]


# ---------------------------------------------------------------------------
# Inventory first, then bounded batches
# ---------------------------------------------------------------------------

@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "lms.db")
    yield c
    c.close()


def _listed(n_windows=5, per=3):
    start = datetime(2026, 6, 1, tzinfo=TZ)
    out = []
    for k in range(n_windows):
        w = bf.Window(start + timedelta(days=7 * k), start + timedelta(days=7 * (k + 1)))
        out.append(bf.Listed(w, [f"w{k}m{i}" for i in range(per)], 500))
    return out


def test_the_inventory_records_windows_and_message_ids(conn):
    listed = _listed()
    bf.record_inventory(conn, "matthew@mrecai.com", listed)
    rows = conn.execute("SELECT listed, truncated, ingested_at FROM backfill_windows "
                        "ORDER BY window_start").fetchall()
    assert [tuple(r) for r in rows] == [(3, 0, None)] * 5
    assert conn.execute("SELECT COUNT(*) FROM backfill_items").fetchone()[0] == 15
    log = conn.execute("SELECT detail FROM actions_log WHERE action = "
                       "'BACKFILL_INVENTORY'").fetchone()[0]
    assert "5 window(s), 15 message id(s), 0 truncated" in log

    bf.record_inventory(conn, "matthew@mrecai.com", listed)      # re-run: upsert
    assert conn.execute("SELECT COUNT(*) FROM backfill_windows").fetchone()[0] == 5


def test_batches_are_bounded_oldest_first_and_resume(conn):
    box = "matthew@mrecai.com"
    listed = _listed()
    bf.record_inventory(conn, box, listed)
    seen = []

    def ingest_one(w):
        seen.append(w)
        return 0

    todo = bf.pending(conn, box, listed)
    assert [w for _, w in todo] == [x.window for x in listed]
    bf.ingest_windows(conn, box, todo[:2], ingest_one)
    assert seen == [listed[0].window, listed[1].window]

    todo = bf.pending(conn, box, listed)                          # next run
    assert [w for _, w in todo] == [x.window for x in listed[2:]]


def test_a_failed_window_does_not_stop_the_batch_or_retry_forever(conn):
    box = "matthew@mrecai.com"
    listed = _listed(3)
    bf.record_inventory(conn, box, listed)

    def ingest_one(w):
        if w == listed[0].window:
            raise AttributeError("'PDFDocument' object has no attribute 'isUnlocked'")
        return 0

    done = bf.ingest_windows(conn, box, bf.pending(conn, box, listed), ingest_one)
    assert [o for _, o in done] == ["FAILED", "OK", "OK"]
    assert bf.pending(conn, box, listed) == []
    assert len(bf.pending(conn, box, listed, retry_failed=True)) == 1
    actions = [r[0] for r in conn.execute("SELECT action FROM actions_log")]
    assert "BACKFILL_WINDOW_FAILED" in actions


def test_a_window_whose_listing_grew_is_pending_again(conn):
    box = "matthew@mrecai.com"
    listed = _listed(2)
    bf.record_inventory(conn, box, listed)
    bf.ingest_windows(conn, box, bf.pending(conn, box, listed), lambda w: 0)
    assert bf.pending(conn, box, listed) == []

    listed[1].ids.append("late-arrival")
    bf.record_inventory(conn, box, listed)
    assert [w for _, w in bf.pending(conn, box, listed)] == [listed[1].window]


def test_status_reconciles_inventoried_ids_against_artifacts(conn):
    box = "matthew@mrecai.com"
    listed = _listed(2)
    bf.record_inventory(conn, box, listed)
    for mid in ("w0m0", "w0m1"):
        db.insert_artifact(conn, sha256=mid.ljust(64, "0"), source="email",
                           source_ref=mid, byte_size=10, status="FILED")
    text = bf.status(conn, box)
    lines = [ln for ln in text.splitlines() if "→" in ln]
    assert lines[0].split()[3:5] == ["3", "2"]       # listed 3, have 2
    assert "2 window(s) pending" in text


def test_backfill_inventories_first_and_ingests_through_poll_mailbox(conn, tmp_path,
                                                                     monkeypatch):
    """End to end with Gmail and the poller faked: the inventory is written
    before the first window is ingested, and each window reaches the existing
    per-mailbox code with its own query and the same limit."""
    start = datetime(2026, 10, 5, tzinfo=TZ)
    times = [start + timedelta(hours=5 * i) for i in range(80)]
    box = FakeMailbox(times)
    client = SimpleNamespace(message_ids=lambda newer_than_days, limit, query: box.list_ids(
        _window_from(query), limit))
    calls = []

    def poll_mailbox(c, address, args, registry, classifier, roots, spool):
        assert c.execute("SELECT COUNT(*) FROM backfill_items").fetchone()[0] == 80
        calls.append(args)
        return 0

    monkeypatch.setattr(bf.poll_mail, "build_client", lambda a: client)
    monkeypatch.setattr(bf.poll_mail, "poll_mailbox", poll_mailbox)
    monkeypatch.setattr(bf, "load_registry", lambda: None)
    monkeypatch.setattr(bf, "Classifier", lambda r: None)

    now = datetime(2026, 10, 20, 12, tzinfo=TZ)
    windows = bf.plan_windows(21, 7, today=now.date())
    roots = SimpleNamespace(archive=tmp_path / "archive")
    args = SimpleNamespace(query="", limit=500, ingest=False, batch=2, retry_failed=False,
                           days=21)

    assert bf.backfill(conn, "matthew@mrecai.com", windows, args, roots, now) == 0
    assert calls == [], "inventory-only mode ingested something"

    args.ingest = True
    assert bf.backfill(conn, "matthew@mrecai.com", windows, args, roots, now) == 0
    assert len(calls) == 2
    assert all(a.limit == 500 and a.query.startswith("after:") for a in calls)
    assert calls[0].query == windows[0].query()


def _window_from(query):
    after, before = (int(p.split(":")[1]) for p in query.split()[:2])
    return bf.Window(datetime.fromtimestamp(after + 1, TZ), datetime.fromtimestamp(before, TZ))
