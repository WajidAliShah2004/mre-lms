"""The to-do list and the brief.

These test the ORDER, because the order is the message. A brief that contains
the right seven items in the wrong sequence has failed at the only job it has:
being read in twenty seconds on a phone and leaving the reader knowing what
matters most.
"""

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from core.db import database as db
from core.pipeline.registry import load_registry
from core.reports import brief as reports

CONFIG = Path(__file__).resolve().parents[1] / "config"
TODAY = date(2026, 9, 8)


@pytest.fixture
def registry():
    return load_registry(CONFIG)


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "lms.db")
    yield c
    c.close()


def task(conn, title, *, due=None, urgency="NORMAL", entity="B_MRE",
         status="OPEN") -> int:
    tid = db.insert_task(conn, title=title, due_date=due, urgency=urgency,
                         entity_id=entity, status=status)
    return tid


def sent_brief_at(conn, kind: str, when: datetime) -> None:
    """Record a past delivery.

    Inserted directly rather than logged-then-updated, because actions_log has
    triggers that abort any UPDATE — the first draft of these tests tried to
    backdate a row and the database correctly refused. Appending a row with an
    older timestamp is the honest way to say "this happened earlier", and it
    leaves the append-only guarantee intact.
    """
    conn.execute(
        "INSERT INTO actions_log (ts, action) VALUES (?, ?)",
        (when.isoformat(timespec="seconds"), f"BRIEF_SENT_{kind.upper()}"))


_sha = 0


def filed(conn, *, entity="B_MRE", when=None, name="doc.pdf",
          status="FILED") -> int:
    """An artifact and its classification.

    classifications.artifact_id is NOT NULL — rightly, since a classification
    with no document is a claim about nothing — so a brief fixture has to
    build the pair.
    """
    global _sha
    _sha += 1
    when = when or db.now_iso()
    cur = conn.execute(
        "INSERT INTO artifacts (sha256, source, status, original_name, ingested_at)"
        " VALUES (?,?,?,?,?)",
        (f"{_sha:064d}", "photo", status, name, when))
    aid = int(cur.lastrowid)
    db.insert_classification(conn, artifact_id=aid, domain="BUSINESS",
                             entity_id=entity, category="VENDORS",
                             urgency="NORMAL", confidence=0.95,
                             model="fake", prompt_hash="0" * 16,
                             decided_by="rule", created_at=when)
    return aid


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

def test_overdue_outranks_everything(conn):
    """The failure the system exists to prevent is a deadline passing unseen.

    An item that has already slipped is also evidence the earlier briefs did
    not work, which is the strongest possible reason to lead with it.
    """
    task(conn, "quarterly filing", due="2026-12-01", urgency="CRITICAL")
    task(conn, "policy renewal", due="2026-09-01")          # a week overdue

    ranked = reports.todo_list(conn, today=TODAY)
    assert ranked[0].title == "policy renewal"
    assert ranked[0].overdue


def test_further_overdue_ranks_higher(conn):
    task(conn, "slipped a week", due="2026-09-01")
    task(conn, "slipped a month", due="2026-08-08")

    ranked = reports.todo_list(conn, today=TODAY)
    assert [t.title for t in ranked] == ["slipped a month", "slipped a week"]


def test_a_small_urgent_thing_beats_a_large_distant_one(conn):
    """The mistake a busy person makes unaided, and the reason to rank at all.

    A $40 renewal that lapses a licence next week matters more than a $50,000
    invoice due in March. Any scoring where the amount leads gets this exactly
    backwards.
    """
    small = task(conn, "licence renewal $40", due="2026-09-11", urgency="HIGH")
    large = task(conn, "big invoice", due="2027-03-01")

    amounts = {small: 4_000, large: 5_000_000}
    monkeypatched = [reports.score_task(r, TODAY, amounts.get(r["id"]))
                     for r in conn.execute(
                         "SELECT * FROM tasks WHERE status='OPEN'").fetchall()]
    monkeypatched.sort(key=lambda s: -s.score)
    assert monkeypatched[0].title == "licence renewal $40"


def test_money_cannot_outrank_a_deadline(conn):
    """The cap, stated as a property. Ten points is the whole budget."""
    assert reports.money_weight(500_000_000) <= 10.0
    assert reports.money_weight(None) == 0.0
    # A CRITICAL due today must beat any amount of money with no date at all.
    soon, _, _ = reports.due_pressure(TODAY, TODAY)
    assert soon > reports.money_weight(10**12) + reports.URGENCY_POINTS["CRITICAL"]


def test_an_undated_task_is_not_treated_as_urgent(conn):
    task(conn, "undated", due=None, urgency="NORMAL")
    task(conn, "due next week", due="2026-09-15")

    ranked = reports.todo_list(conn, today=TODAY)
    assert ranked[0].title == "due next week"
    assert ranked[-1].due_in_days is None


def test_the_order_is_stable_between_runs(conn):
    """A list that reshuffles overnight teaches people not to trust the order."""
    for i in range(6):
        task(conn, f"same shape {i}", due="2026-09-20")
    first = [t.title for t in reports.todo_list(conn, today=TODAY)]
    second = [t.title for t in reports.todo_list(conn, today=TODAY)]
    assert first == second


def test_done_tasks_are_not_in_the_list(conn):
    task(conn, "finished", due="2026-09-01", status="DONE")
    assert reports.todo_list(conn, today=TODAY) == []


def test_the_list_filters_by_entity(conn):
    task(conn, "mrecai thing", entity="B_MRE")
    task(conn, "atlase thing", entity="B_ATL")
    ranked = reports.todo_list(conn, today=TODAY, entity_id="B_ATL")
    assert [t.title for t in ranked] == ["atlase thing"]


# ---------------------------------------------------------------------------
# The cap
# ---------------------------------------------------------------------------

def test_only_seven_are_surfaced(conn):
    for i in range(12):
        task(conn, f"routine {i:02d}", due="2026-10-01")
    shown, withheld = reports.top_tasks(reports.todo_list(conn, today=TODAY))
    assert len(shown) == reports.SURFACE_LIMIT
    assert withheld == 5


def test_the_cap_never_hides_something_overdue(conn):
    """If nine things lapse this week, showing seven is not concision — it is
    choosing which two he finds out about the hard way."""
    for i in range(9):
        task(conn, f"routine {i:02d}", due="2026-10-01", urgency="HIGH")
    task(conn, "LAPSED", due="2026-08-20")

    shown, _ = reports.top_tasks(reports.todo_list(conn, today=TODAY))
    assert "LAPSED" in [t.title for t in shown]


def test_the_cap_never_hides_a_critical_item(conn):
    for i in range(10):
        task(conn, f"routine {i:02d}", due="2026-09-09", urgency="HIGH")
    task(conn, "POLICY LAPSING", due=None, urgency="CRITICAL")

    shown, _ = reports.top_tasks(reports.todo_list(conn, today=TODAY))
    assert "POLICY LAPSING" in [t.title for t in shown]


def test_withheld_is_counted_not_forgotten(conn):
    for i in range(20):
        task(conn, f"routine {i:02d}", due="2026-10-01")
    shown, withheld = reports.top_tasks(reports.todo_list(conn, today=TODAY))
    assert len(shown) + withheld == 20


# ---------------------------------------------------------------------------
# The brief itself
# ---------------------------------------------------------------------------

def test_an_empty_brief_says_so(conn, registry):
    """An empty message and a broken one are indistinguishable on a phone.

    D-030's lesson, arriving somewhere new: silence is the one outcome that
    cannot be acted on.
    """
    text = reports.render_text(
        reports.build_brief(conn, now=datetime(2026, 9, 8, 6, 30)), registry)
    assert "Nothing needs you" in text
    assert text.strip()


def test_the_brief_leads_with_what_needs_him(conn, registry):
    task(conn, "renew the licence", due="2026-09-01", urgency="HIGH")
    text = reports.render_text(
        reports.build_brief(conn, now=datetime(2026, 9, 8, 6, 30)), registry)

    assert text.index("WHAT NEEDS YOU") < text.index("renew the licence") + 1
    assert "OVERDUE" in text


def test_overdue_items_are_marked_in_the_text(conn, registry):
    task(conn, "slipped", due="2026-09-01")
    text = reports.render_text(
        reports.build_brief(conn, now=datetime(2026, 9, 8, 6, 30)), registry)
    assert "!!" in text


def test_the_brief_names_the_entity_not_its_id(conn, registry):
    """B_MRE means nothing at 06:30. The trading name does."""
    filed(conn, entity="B_MRE")
    text = reports.render_text(
        reports.build_brief(conn, now=datetime.now()), registry)
    assert "MRECAI" in text
    assert "B_MRE" not in text


def test_quarantined_documents_appear_as_waiting_on_him(conn, registry):
    """The queue that must never grow silently — everything in it is something
    the pipeline could have guessed at and deliberately did not."""
    conn.execute(
        "INSERT INTO artifacts (sha256, source, status, original_name, ingested_at)"
        " VALUES (?,?,?,?,?)",
        ("a" * 64, "photo", "QUARANTINED", "mystery.pdf", db.now_iso()))
    text = reports.render_text(
        reports.build_brief(conn, now=datetime.now()), registry)
    assert "WAITING ON YOU" in text
    assert "mystery.pdf" in text


def test_the_brief_is_narrow_enough_for_a_phone(conn, registry):
    """Telegram reflows anything wide into unreadable ribbons."""
    for i in range(9):
        task(conn, f"a task with a reasonably long descriptive title {i}",
             due="2026-09-01")
    text = reports.render_text(
        reports.build_brief(conn, now=datetime(2026, 9, 8, 6, 30)), registry)
    too_wide = [l for l in text.splitlines() if len(l) > 78]
    assert not too_wide, too_wide


def test_evening_and_morning_are_labelled_differently(conn, registry):
    morning = reports.render_text(reports.build_brief(conn, kind="morning"), registry)
    evening = reports.render_text(reports.build_brief(conn, kind="evening"), registry)
    assert "Morning brief" in morning
    assert "Evening close-out" in evening


def test_a_missed_run_does_not_lose_documents(conn, registry):
    """The gap a fixed window opens, and why the log closes it.

    If the Mac sleeps through a morning brief, everything that arrived in the
    meantime is filed and findable — and Matthew is never told it arrived.
    That is the failure this module exists to prevent, reintroduced at the
    last step.
    """
    sent_brief_at(conn, "morning", datetime.now() - timedelta(days=3))
    filed(conn, when=(datetime.now() - timedelta(days=2)).isoformat())

    b = reports.build_brief(conn, kind="morning", now=datetime.now())
    assert b.new_by_entity == {"B_MRE": 1}, \
        "a document from a skipped day was never reported"


def test_the_window_is_only_a_first_run_fallback(conn, registry):
    """With a delivery recorded, the window must not narrow the range."""
    sent_brief_at(conn, "morning", datetime.now() - timedelta(days=5))
    filed(conn, when=(datetime.now() - timedelta(days=4)).isoformat())

    b = reports.build_brief(conn, kind="morning", now=datetime.now(),
                            window_hours=1)
    assert b.new_by_entity == {"B_MRE": 1}, "the window overrode the log"


def test_morning_and_evening_track_separately(conn):
    reports.mark_brief_sent(conn, "morning")
    assert reports.last_brief_at(conn, "morning") is not None
    assert reports.last_brief_at(conn, "evening") is None


def test_building_a_brief_does_not_record_a_delivery(conn):
    """Marking a send at build time would report documents as delivered by a
    brief that was rendered to a terminal and read by nobody."""
    reports.build_brief(conn, kind="morning")
    assert reports.last_brief_at(conn, "morning") is None


def test_the_window_excludes_older_documents(conn, registry):
    filed(conn, when=(datetime.now() - timedelta(days=3)).isoformat())
    b = reports.build_brief(conn, kind="morning", now=datetime.now())
    assert b.new_by_entity == {}, "a three-day-old document counted as new"

    filed(conn)                                   # arrived just now
    b = reports.build_brief(conn, kind="morning", now=datetime.now())
    assert b.new_by_entity == {"B_MRE": 1}, "today's document was not counted"
