"""The to-do list and the brief — the half of the job that isn't filing.

Matthew's own description of what he wanted, from the Aug 5 meeting:

    "I don't need it to do business operations… I just need it to determine if
     it's business, which business, save it, and then let me know what I need
     to do."

Everything up to "save it" is the pipeline. This module is "let me know what I
need to do", and until it existed the system was doing three quarters of the
sentence.

WHAT A BRIEF IS FOR
-------------------
It is read on a phone, before coffee, in about twenty seconds. That constraint
decides nearly every choice here:

  * Ranked, not chronological. The order IS the message. A list Matthew has to
    read in full to find the urgent thing has failed at the only job it has.
  * Seven tasks (spec §Day-5.1), because a list longer than that gets skimmed
    and skimming defeats ranking. CRITICAL items are exempt — if there are
    nine things that lapse this week, hiding two of them is not tidiness.
  * It always says something. A brief with nothing in it must SAY there is
    nothing, because an empty message and a broken one look identical on a
    phone. That is D-030's lesson arriving somewhere new: silence is the one
    outcome that cannot be acted on.

WHAT THE ADDENDUM ADDED (R15)
-----------------------------
§2: "Escalate imminent obligations immediately; provide a daily digest and
weekly backlog review." §5: "Redact taxpayer IDs, health details and
credentials from routine digests."

  * Redaction. Every line `render_text` produces passes through
    `redact.redact` (taxpayer IDs, card numbers, credentials — by pattern).
    Health details cannot be found by pattern, so they go by classification: a
    task that is `sensitivity` high/private, or has a HEALTH document behind
    it, is printed as "Private item #12 — open it at the Mac" and its title
    never reaches the renderer's output. Both happen at render time; the
    database keeps the truth, and `done.py --show` at the Mac shows it.
  * Weekly backlog. The Monday morning brief (BACKLOG_WEEKDAY) ends with every
    open task, by entity, oldest first. The daily list is ranked and capped,
    which is right for a twenty-second read and wrong for noticing the thing
    that has sat at rank 12 since August.
  * Escalation. Tasks that have become imminent (overdue, or due within 24 h)
    and have not been escalated before lead the brief under their own heading,
    and are recorded as escalated only once the brief is delivered.

WHAT IS NOT HERE
----------------
Delivery. The brief goes to Telegram (spec §Day-5.3) and Telegram is blocked
on C2 — the app is not installed on the Mac and there is no authorised sender.
So this module BUILDS and RENDERS; something else will push. That split is
worth keeping anyway: it means the brief can be read, diffed and tested
without a network, and the scheduled job is a thin wrapper rather than the
place the logic lives.

Which includes escalation. "Immediately" needs a push channel, and push is
R10. Until then the escalation list rides in the next brief — at worst a day
late, which is the gap R10 closes by sending the same `escalations_due` list
the moment it is non-empty, and calling `mark_escalated` when it has.

The ~3 interrupt/day cap is also delivery-side and belongs with the pusher.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from ..pipeline import ledger
from ..pipeline.registry import Registry
from .redact import redact

# Spec §Day-5.1. Not a hard truncation: see `top_tasks`.
SURFACE_LIMIT = 7

# Urgency contributes, but does not dominate. A CRITICAL item with no deadline
# is a worry; an overdue one is a problem. The numbers are deliberately far
# apart so the ordering is legible when you read a score, rather than being an
# emergent property of four similar weights.
URGENCY_POINTS = {"CRITICAL": 40, "HIGH": 20, "NORMAL": 5, "LOW": 1, "NONE": 0}

# Spec §5.6's values that keep a title out of the digest (R15).
PRIVATE_SENSITIVITY = frozenset({"high", "private"})
# Taxonomy categories whose documents make the task behind them private, for
# tasks whose `sensitivity` was never set — which today is all of them: ingest
# does not set it. Derived at read time, so a document re-filed into HEALTH
# hides its task from the next brief without anything being rewritten.
PRIVATE_CATEGORIES = frozenset({"HEALTH"})

# The weekly backlog review (addendum §2) rides on this day's MORNING brief.
# datetime.weekday(): Monday is 0. Monday because the review is for planning a
# week, and Friday's version arrives when nothing can be done about it.
BACKLOG_WEEKDAY = 0
# Lines, not tasks hidden: every entity's open count is printed regardless,
# and the rest are counted. Past this it is a list to work through at the Mac,
# not one to read on a phone.
BACKLOG_LIMIT = 25

# "Imminent" (addendum §2: "escalate imminent obligations immediately").
ESCALATE_WITHIN = timedelta(hours=24)
# The task_events event that says it was escalated (see `mark_escalated`).
ESCALATED = "ESCALATED"


@dataclass(frozen=True)
class ScoredTask:
    row: sqlite3.Row
    score: float
    due_in_days: int | None
    reason: str            # why it ranked here, in words, for the brief
    # Health or otherwise sensitive (R15): rendered without its title.
    private: bool = False

    @property
    def task_id(self) -> int:
        """What closes this task.

        NOT its position in the list. The list is ordered by score and the
        score moves — a task that is second this morning is first tomorrow
        because something ahead of it was completed or because its own due date
        got closer. "Done number 2" means a different task depending on when it
        is said.
        """
        return int(self.row["id"])

    @property
    def title(self) -> str:
        return self.row["title"]

    @property
    def overdue(self) -> bool:
        return self.due_in_days is not None and self.due_in_days < 0

    @property
    def shown_title(self) -> str:
        """The title as the digest may show it."""
        return private_line(self.task_id) if self.private else self.title

    @property
    def shown_reason(self) -> str:
        """Due, state and urgency stay — they are why it ranked here and say
        nothing about him. The amount goes: on a private item it is a medical
        bill's size, which is exactly the detail §5 means."""
        if not self.private:
            return self.reason
        return " · ".join(b for b in self.reason.split(" · ")
                          if not b.startswith("$"))


def private_line(task_id: int) -> str:
    """What a private task is called in a digest. Says where the rest is."""
    return f"Private item #{task_id} — open it at the Mac: ./ops/done.py {task_id} --show"


@dataclass(frozen=True)
class BacklogItem:
    task_id: int
    entity_id: str | None
    title: str
    status: str
    age_days: int
    private: bool = False


@dataclass
class Backlog:
    """Every open task, for the weekly review. `by_entity` is never capped."""
    total: int
    by_entity: dict[str | None, int]
    items: list[BacklogItem]              # oldest first, at most BACKLOG_LIMIT

    @property
    def not_listed(self) -> int:
        return self.total - len(self.items)


@dataclass
class Brief:
    kind: str                              # "morning" | "evening"
    generated_at: str
    tasks: list[ScoredTask] = field(default_factory=list)
    withheld: int = 0                      # ranked below the cut
    new_by_entity: dict[str, int] = field(default_factory=dict)
    needs_decision: list[sqlite3.Row] = field(default_factory=list)
    # Scheduled jobs that have not run when they should have. Empty is the
    # normal state and prints nothing.
    stalled: list[str] = field(default_factory=list)
    # Imminent and never escalated before (R15). Shown first, and NOT also in
    # `tasks`: the same item twice in a twenty-second read is the D-034
    # follow-on's mistake. Recorded as escalated only on delivery.
    escalations: list[ScoredTask] = field(default_factory=list)
    # The weekly review; None on every brief but the Monday morning one.
    backlog: Backlog | None = None

    @property
    def is_empty(self) -> bool:
        return not (self.tasks or self.new_by_entity or self.needs_decision
                    or self.stalled or self.escalations)


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def due_pressure(due: date | None, today: date) -> tuple[float, int | None, str]:
    """How much the calendar is pushing.

    Overdue outranks everything and keeps climbing, because the failure this
    system exists to prevent is a deadline passing unseen — and an item that
    has already slipped is evidence the earlier briefs did not work.

    Undated is NOT zero-risk, it is unknown-risk, but it cannot be ranked
    against a date. It scores nothing here and relies on urgency and money,
    which is the honest answer rather than a guessed deadline.
    """
    if due is None:
        return 0.0, None, "no date"

    days = (due - today).days
    if days < 0:
        # Cap the climb at 30 days: past a month overdue it is not getting
        # more urgent, it is getting escalated, and that is a human decision.
        return 100.0 + min(-days, 30), days, f"{-days}d OVERDUE"
    if days == 0:
        return 95.0, 0, "due TODAY"
    if days <= 3:
        return 80.0 - days * 5, days, f"due in {days}d"
    if days <= 14:
        return 50.0 - days, days, f"due in {days}d"
    return max(0.0, 20.0 - days / 7.0), days, f"due {due.isoformat()}"


def money_weight(amount_cents: int | None) -> float:
    """A tiebreaker, never a driver.

    Deliberately capped low. A $40 renewal that lapses a licence outranks a
    $50,000 invoice due in March, and any scoring that lets the amount lead
    will get that backwards — which is precisely the mistake a busy person
    makes unaided, and the reason to rank at all.
    """
    if not amount_cents:
        return 0.0
    return min(10.0, amount_cents / 100_000)      # $1,000 ≈ 1 point, cap $10k


def score_task(row: sqlite3.Row, today: date,
               amount_cents: int | None = None,
               private: bool = False) -> ScoredTask:
    due = _parse_date(row["due_date"])
    pressure, days, when = due_pressure(due, today)
    urgency = (row["urgency"] or "NORMAL").upper()

    score = pressure + URGENCY_POINTS.get(urgency, 5) + money_weight(amount_cents)

    bits = [when]
    # The ledger's exception states are why a task is stuck; say which.
    status = row["status"] if "status" in row.keys() else None
    if status in ("NEEDS_CLARIFICATION", "BLOCKED", "FAILED"):
        bits.append(status.replace("_", " ").lower())
    if urgency in ("CRITICAL", "HIGH"):
        bits.append(urgency)
    if amount_cents:
        bits.append(f"${amount_cents / 100:,.2f}")
    return ScoredTask(row=row, score=score, due_in_days=days,
                      reason=" · ".join(bits), private=private)


def _amounts_by_task(conn: sqlite3.Connection) -> dict[int, int]:
    """Task id -> amount, joined through the artifact that produced it.

    The tasks table has no amount column and should not grow one: the figure
    belongs to the document, and a task that outlives a correction to that
    document should not carry a stale copy of it.
    """
    rows = conn.execute("""
        SELECT t.id AS task_id, c.amount_cents AS amount
          FROM tasks t
          JOIN classifications c ON c.artifact_id = t.source_artifact
         WHERE t.source_artifact IS NOT NULL
    """).fetchall()
    return {r["task_id"]: r["amount"] for r in rows if r["amount"]}


def private_task_ids(conn: sqlite3.Connection) -> set[int]:
    """Tasks the digest must not name (addendum §5, health details).

    By classification, never by pattern: no regex can tell "Dr. Patel
    follow-up" from an errand. A task is private when it says so
    (`sensitivity`, spec §5.6) or when any document behind it — the first
    source or any later evidence (task_sources) — was filed as HEALTH. Any one
    is enough; erring private costs a trip to the Mac, erring public cannot be
    undone.
    """
    cats = ", ".join(repr(c) for c in sorted(PRIVATE_CATEGORIES))
    sens = ", ".join(repr(s) for s in sorted(PRIVATE_SENSITIVITY))
    rows = conn.execute(f"""
        SELECT id AS task_id FROM tasks WHERE sensitivity IN ({sens})
        UNION
        SELECT t.id FROM tasks t
          JOIN classifications c ON c.artifact_id = t.source_artifact
         WHERE c.category IN ({cats})
        UNION
        SELECT s.task_id FROM task_sources s
          JOIN classifications c ON c.artifact_id = s.artifact_id
         WHERE c.category IN ({cats})
    """).fetchall()
    return {int(r["task_id"]) for r in rows}


def todo_list(conn: sqlite3.Connection, *, today: date | None = None,
              entity_id: str | None = None,
              person: str | None = None) -> list[ScoredTask]:
    """Every open task, ranked. One list, filtered at read time (spec §Day-5.1).

    Open means every ledger state but the three that end a task — including
    BLOCKED, FAILED and NEEDS_CLARIFICATION, which need him more than most.
    """
    today = today or date.today()
    sql = f"SELECT * FROM tasks WHERE {ledger.active_sql()}"
    args: list[Any] = []
    if entity_id:
        sql += " AND entity_id = ?"
        args.append(entity_id)
    if person:
        sql += " AND person = ?"
        args.append(person)

    amounts = _amounts_by_task(conn)
    private = private_task_ids(conn)
    scored = [score_task(r, today, amounts.get(r["id"]), r["id"] in private)
              for r in conn.execute(sql, tuple(args)).fetchall()]
    # Ties broken by id so the order is stable between runs. A list that
    # reshuffles overnight teaches people not to trust the order.
    scored.sort(key=lambda s: (-s.score, s.row["id"]))
    return scored


def top_tasks(scored: list[ScoredTask],
              limit: int = SURFACE_LIMIT) -> tuple[list[ScoredTask], int]:
    """The first `limit`, plus everything CRITICAL or overdue regardless.

    The cap exists so the brief stays readable, not so it stays short. If nine
    things lapse this week, showing seven is not concision — it is choosing
    which two he finds out about the hard way.
    """
    head = scored[:limit]
    seen = {id(s) for s in head}
    forced = [s for s in scored[limit:]
              if id(s) not in seen
              and (s.overdue or (s.row["urgency"] or "").upper() == "CRITICAL")]
    shown = head + forced
    return shown, max(0, len(scored) - len(shown))


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def _new_since(conn: sqlite3.Connection, since: str) -> dict[str, int]:
    rows = conn.execute("""
        SELECT entity_id, COUNT(*) AS n
          FROM classifications
         WHERE created_at >= ?
      GROUP BY entity_id
      ORDER BY n DESC
    """, (since,)).fetchall()
    return {r["entity_id"]: r["n"] for r in rows}


def _needs_decision(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Documents the system refused to file on its own.

    This is the queue that must never grow silently. Everything in it is
    something the pipeline could have guessed at and deliberately did not.
    """
    return conn.execute("""
        SELECT id, original_name, status, source
          FROM artifacts
         WHERE status IN ('QUARANTINED', 'SUSPECTED_PHISHING')
      ORDER BY id DESC
         LIMIT 20
    """).fetchall()


def last_brief_at(conn: sqlite3.Connection, kind: str) -> str | None:
    """When a brief of this kind was last actually delivered.

    Read from actions_log, which is append-only, so this cannot be quietly
    rewritten.
    """
    row = conn.execute(
        "SELECT ts FROM actions_log WHERE action = ? ORDER BY id DESC LIMIT 1",
        (f"BRIEF_SENT_{kind.upper()}",)).fetchone()
    return row["ts"] if row else None


def mark_brief_sent(conn: sqlite3.Connection, kind: str) -> None:
    """Called by whatever DELIVERS the brief, never by whatever builds it.

    Recording a send at build time would mark documents as reported by a brief
    that was rendered to a terminal and read by nobody.
    """
    from ..db import database as db
    db.log_action(conn, f"BRIEF_SENT_{kind.upper()}")


# Scheduled jobs, and how long each may go without running before the silence
# means something. Each is generous — roughly two missed runs — because one
# skipped night is a Mac that was asleep, and crying wolf trains people to
# ignore the section.
#
#   action written by         job              says nothing has run for
WATCHED_JOBS: dict[str, tuple[str, int]] = {
    "MAIL_POLL_STOP":   ("mail",   36),      # runs 07:00 and 19:00
    "BRIEF_DELIVERED":  ("brief",  48),      # runs 06:30
    "BACKUP_COMPLETED": ("backup", 48),      # runs 02:30
    "AUDIT_COMPLETED":  ("audit", 16 * 24),  # runs Sundays 05:15 (Phase 8)
}


def stalled_jobs(conn: sqlite3.Connection, now: datetime) -> list[str]:
    """Scheduled work that has silently stopped.

    Nothing else notices. Four launchd jobs run unattended on a machine in
    Matthew's office, and if one dies — a bad plist, a revoked token, a full
    disk, a Mac sitting at a locked login screen after a power cut (C8, still
    unanswered) — the only symptom is that something stops arriving. Absence is
    the hardest signal for a person to notice, which is why the system has to
    notice it instead.

    The brief is the one thing he reads, so it is where this belongs. A job
    that has never run at all is NOT reported: on a fresh machine that is every
    job, and an alarm that fires on day one is an alarm that gets ignored by
    day two. The first successful run arms it.
    """
    # Every ts in actions_log carries an offset — now_iso() writes
    # "2026-09-10T08:31:40-04:00" — and `datetime.now()` is naive. Subtracting
    # one from the other raises, and it raised on the Mac on the first real run
    # after eleven tests had passed against naive fixtures on both sides.
    # Normalise here, once, rather than at every caller.
    if now.tzinfo is None:
        now = now.astimezone()

    out = []
    for action, (name, hours) in sorted(WATCHED_JOBS.items()):
        row = conn.execute(
            "SELECT ts FROM actions_log WHERE action = ? "
            "ORDER BY id DESC LIMIT 1", (action,)).fetchone()
        if row is None:
            continue                       # never run; not yet armed
        try:
            last = datetime.fromisoformat(row["ts"])
        except (TypeError, ValueError):
            continue
        if last.tzinfo is None:
            last = last.replace(tzinfo=now.tzinfo)
        gap = now - last
        if gap > timedelta(hours=hours):
            days = gap.days
            ago = f"{days}d ago" if days else f"{int(gap.total_seconds() // 3600)}h ago"
            out.append(f"{name}: last ran {ago} ({last.isoformat(timespec='minutes')})")
    return out


# ---------------------------------------------------------------------------
# Escalation (R15; addendum §2: "escalate imminent obligations immediately")
# ---------------------------------------------------------------------------

def is_imminent(task: ScoredTask, now: datetime) -> bool:
    """Overdue, or due within ESCALATE_WITHIN — by the brief's own due date.

    Due dates are dates, not times, so "within 24 hours" is read the careful
    way: a task is imminent if its date falls on or before the date 24 hours
    from now. At 06:30 that includes tomorrow, because a date with no time
    could fall due at tomorrow's midnight, seventeen and a half hours away.
    Erring early costs one line at the top of a brief; erring late is the
    deadline passing unseen.
    """
    due = _parse_date(task.row["due_date"])
    return due is not None and due <= (now + ESCALATE_WITHIN).date()


def escalated_ids(conn: sqlite3.Connection) -> set[int]:
    return {int(r["task_id"]) for r in conn.execute(
        "SELECT DISTINCT task_id FROM task_events WHERE event = ?", (ESCALATED,))}


def escalations_due(conn: sqlite3.Connection, now: datetime,
                    scored: list[ScoredTask] | None = None) -> list[ScoredTask]:
    """Open tasks that have become imminent and have never been escalated.

    Read-only. Recording happens in `mark_escalated`, called by whatever
    delivers — for the same reason `mark_brief_sent` is: a task marked
    escalated by a brief that never arrived is never escalated again, which is
    worse than not escalating it at all, because it looks handled.

    Once per task, ever. A task that was escalated as "due tomorrow" and is
    now overdue is not escalated twice; it stays at the top of WHAT NEEDS YOU,
    marked !!, every day until it is closed (overdue always outranks, D-034).
    Escalation is the interrupt, not the reminder.

    R10's push channel sends exactly this list, the moment it is non-empty.
    """
    if scored is None:
        scored = todo_list(conn, today=now.date())
    done = escalated_ids(conn)
    return [s for s in scored if is_imminent(s, now) and s.task_id not in done]


def mark_escalated(conn: sqlite3.Connection, tasks: Iterable[ScoredTask], *,
                   by: str = "brief") -> list[int]:
    """Record that these tasks were escalated. Idempotent; returns those newly marked.

    Recorded in task_events, the task's own audit trail (addendum §2), not in
    actions_log alone: "has this task been escalated" is then a lookup by
    task_id on an indexed column, where actions_log has no task column and
    the answer would be a LIKE over prose. A `TASK_ESCALATED` row also goes to
    actions_log, as `record_event` writes TASK_CREATED beside CREATED, so the
    system-wide log of what it did stays complete.
    """
    from ..db import database as db
    done = escalated_ids(conn)
    marked: list[int] = []
    for t in tasks:
        if t.task_id in done:
            continue
        ledger.note(conn, t.task_id, ESCALATED, by=by, detail=t.shown_reason)
        db.log_action(conn, "TASK_ESCALATED",
                      artifact_id=t.row["source_artifact"],
                      detail=f"#{t.task_id} {t.shown_reason}"[:400])
        done.add(t.task_id)
        marked.append(t.task_id)
    return marked


# ---------------------------------------------------------------------------
# The weekly backlog review (R15; addendum §2)
# ---------------------------------------------------------------------------

def _age_days(row: sqlite3.Row, today: date) -> int:
    """Days since the obligation reached the system. `received_at` first (when
    the source event arrived), `created_at` if that is missing or is not an
    ISO date (a mail header date, for instance)."""
    keys = row.keys()
    for col in ("received_at", "created_at"):
        d = _parse_date(row[col]) if col in keys else None
        if d is not None:
            return max(0, (today - d).days)
    return 0


def backlog_review(conn: sqlite3.Connection, today: date) -> Backlog:
    """Every open task (ledger.ACTIVE), oldest first.

    Not ranked. The daily list answers "what now"; this answers "what has been
    sitting there", and a ranked list is precisely what hides the answer — a
    task at rank 12 in August is at rank 12 in October and never seen.
    """
    private = private_task_ids(conn)
    rows = conn.execute(
        f"SELECT * FROM tasks WHERE {ledger.active_sql()}").fetchall()
    items = [BacklogItem(task_id=int(r["id"]), entity_id=r["entity_id"],
                         title=r["title"], status=r["status"],
                         age_days=_age_days(r, today),
                         private=int(r["id"]) in private) for r in rows]
    items.sort(key=lambda i: (-i.age_days, i.task_id))
    by_entity = Counter(i.entity_id for i in items)
    return Backlog(total=len(items), by_entity=dict(by_entity),
                   items=items[:BACKLOG_LIMIT])


def is_backlog_day(kind: str, now: datetime) -> bool:
    return kind == "morning" and now.weekday() == BACKLOG_WEEKDAY


def build_brief(conn: sqlite3.Connection, *, kind: str = "morning",
                now: datetime | None = None,
                window_hours: int | None = None) -> Brief:
    now = now or datetime.now()

    # "Since the last brief", literally — not "since N hours ago".
    #
    # A fixed window is right exactly as long as every scheduled run happens.
    # The Mac sleeps, loses power (C8 is still unanswered), gets closed for a
    # weekend; the moment one run is missed, a fixed window silently skips
    # every document that arrived in the gap. Those documents are filed and
    # findable, but Matthew is never told they arrived — which is the failure
    # this whole module exists to prevent, reintroduced at the last step.
    #
    # The window survives only as the fallback for the very first brief, where
    # there is genuinely no previous one to measure from.
    since = last_brief_at(conn, kind)
    if since is None:
        if window_hours is None:
            # Morning covers the night; evening covers the working day.
            window_hours = 14 if kind == "morning" else 10
        since = (now - timedelta(hours=window_hours)).isoformat(timespec="seconds")

    scored = todo_list(conn, today=now.date())
    escalate = escalations_due(conn, now, scored)
    lead = {e.task_id for e in escalate}
    shown, withheld = top_tasks([s for s in scored if s.task_id not in lead])

    return Brief(
        kind=kind,
        generated_at=now.isoformat(timespec="seconds"),
        tasks=shown,
        withheld=withheld,
        new_by_entity=_new_since(conn, since),
        needs_decision=_needs_decision(conn),
        stalled=stalled_jobs(conn, now),
        escalations=escalate,
        backlog=(backlog_review(conn, now.date())
                 if is_backlog_day(kind, now) else None),
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

# Telegram reflows anything wider into ribbons; the backlog truncates to it.
LINE_WIDTH = 78


def _fit(line: str, width: int = LINE_WIDTH) -> str:
    return line if len(line) <= width else line[:width - 1].rstrip() + "…"


def render_text(brief: Brief, registry: Registry | None = None) -> str:
    """Plain text, phone-shaped. No markdown tables, no columns.

    Telegram reflows anything wide into unreadable ribbons, and this is read
    one-handed. Short lines, one idea each.

    Redacted (R15): every line goes through `redact` on the way out, whatever
    section it came from, and private tasks are rendered by `shown_title`, so
    their titles never enter the text at all. What is returned here is what
    leaves the Mac.
    """
    return redact(_render(brief, registry))


def _render(brief: Brief, registry: Registry | None) -> str:
    def label(entity_id: str) -> str:
        if registry is None:
            return entity_id
        try:
            return registry.get(entity_id).short_or_name()
        except Exception:
            return entity_id

    when = brief.generated_at[:16].replace("T", " ")
    out = [f"{'Morning brief' if brief.kind == 'morning' else 'Evening close-out'}"
           f" · {when}", ""]

    # FIRST, above the work. If part of the system has stopped, everything
    # below is a report on an incomplete picture and he needs to know that
    # before he reads it — "nothing needs you" means something very different
    # when the mail poll died on Tuesday.
    if brief.stalled:
        out.append("THE SYSTEM NEEDS ATTENTION")
        for line in brief.stalled:
            out.append(f"   {line}")
        out.append("   Everything below may be incomplete.")
        out.append("")

    # The empty case is stated, never implied. An empty message and a broken
    # one are indistinguishable on a phone (D-030).
    if brief.is_empty:
        out.append("Nothing needs you. No new documents, no open tasks, "
                   "nothing waiting on a decision.")
        return "\n".join(out)

    # Second only to a stalled system: things that became imminent since the
    # last brief. Listed by id, because these are the ones he acts on today.
    if brief.escalations:
        out.append(f"ACT NOW — OVERDUE OR DUE WITHIN 24 HOURS "
                   f"({len(brief.escalations)})")
        for t in brief.escalations:
            # A private line already carries its id.
            out.append(f"!! {t.shown_title}" if t.private
                       else f"!! #{t.task_id} {t.title}")
            out.append(f"       {t.shown_reason}")
        out.append("")

    if brief.tasks:
        out.append(f"WHAT NEEDS YOU ({len(brief.tasks)})")
        for i, t in enumerate(brief.tasks, 1):
            mark = "!!" if t.overdue else "  "
            out.append(f"{mark} {i}. {t.shown_title}")
            out.append(f"       {t.shown_reason}")
        if brief.withheld:
            out.append(f"   (+{brief.withheld} more, lower priority)")
        out.append("")
    else:
        out.append("WHAT NEEDS YOU — nothing else open." if brief.escalations
                   else "WHAT NEEDS YOU — nothing open.")
        out.append("")

    if brief.new_by_entity:
        total = sum(brief.new_by_entity.values())
        out.append(f"FILED SINCE LAST BRIEF ({total})")
        for eid, n in brief.new_by_entity.items():
            out.append(f"   {label(eid)}: {n}")
        out.append("")

    if brief.needs_decision:
        out.append(f"WAITING ON YOU ({len(brief.needs_decision)})")
        out.append("   Documents the system would not guess at:")
        for r in brief.needs_decision[:5]:
            out.append(f"   - {r['original_name']} ({r['status'].lower()})")
        if len(brief.needs_decision) > 5:
            out.append(f"   - …and {len(brief.needs_decision) - 5} more")
        out.append("")

    # Last: the weekly review is the long section, and the twenty-second read
    # above it must not have to scroll past it.
    if brief.backlog is not None:
        out.extend(_render_backlog(brief.backlog, label))

    return "\n".join(out).rstrip()


def _render_backlog(backlog: Backlog, label) -> list[str]:
    out = [f"WEEKLY BACKLOG REVIEW — {backlog.total} open, oldest first"]
    if not backlog.total:
        out.append("   Nothing open.")
        return out

    # Entities in the order of their oldest listed task; any whose tasks are
    # all past the cap still get their header and count, so no entity's open
    # work is ever missing from the review entirely.
    order: list[str | None] = []
    for item in backlog.items:
        if item.entity_id not in order:
            order.append(item.entity_id)
    order += sorted((e for e in backlog.by_entity if e not in order),
                    key=lambda e: (-backlog.by_entity[e], e or ""))

    for eid in order:
        out.append(f"{label(eid) if eid else 'Unassigned'} · "
                   f"{backlog.by_entity[eid]} open")
        for item in backlog.items:
            if item.entity_id != eid:
                continue
            # Redacted BEFORE it is cut to width: truncating first could
            # leave half an SSN that no pattern recognises any more.
            title = (f"Private — ./ops/done.py {item.task_id} --show"
                     if item.private else redact(item.title))
            state = item.status.replace("_", " ").lower()
            out.append(_fit(f"   {item.age_days}d · {state} · #{item.task_id} {title}"))
    if backlog.not_listed:
        out.append(f"   (+{backlog.not_listed} more not listed — "
                   f"./ops/done.py --list at the Mac)")
    return out
