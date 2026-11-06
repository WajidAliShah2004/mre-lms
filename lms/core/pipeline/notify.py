"""Telegram alerts: tell Matthew when something important arrives (R10, D-077).

    ingest ── enqueue() ──> notifications (pending)
                                  │
    ops/notify.py, every 15 min ──┴─ due() ─> one Telegram message ─> sent_at

Spec §8.8 sets both halves. WHAT interrupts: CRITICAL urgency, suspected
phishing, regulatory/legal mail, system failure (also an anomaly >= $2,500 and
an egress-firewall block; neither exists yet, see D-077). Everything else
waits for the brief. HOW OFTEN: at most 3 pushes a day and 1 an hour, nothing
between 22:00 and 07:00 ET (§7.8), and a CRITICAL item breaks every one of
those rules, because the tuning sprint's exit criterion is "zero missed
CRITICALs" and a budget that can hold one back fails it.

The two halves are apart on purpose. Ingest knows what a document is and when
it was received; it does not know what has been sent today. The sender knows
the budget and nothing about documents. A pending row is the whole contract,
so a push that fails is retried by the next run instead of lost, and nothing
held back by the budget is ever dropped: it rides in the next push.

Only mail RECEIVED in the last FRESH_DAYS is queued. A backfill ingests weeks
of old mail in one evening, and three weeks of CRITICAL notices arriving as
alerts at once would be the over-notification the spec names as a top risk
(R12 of its risk table): an owner who learns to ignore the alerts.

Every line is redacted (R15) and capped before it is stored, and a HEALTH
document is never named. The text goes through Telegram's servers; it is
what a lock screen shows.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from ..db import database as db
from ..reports.redact import redact

TZ = ZoneInfo("America/New_York")

FRESH_DAYS = 2                  # received within this many days, or not queued
MAX_PER_DAY = 3                 # §8.8 / autonomy.yaml notifications.max_per_day
MIN_GAP = timedelta(hours=1)    # max_per_hour_outside_briefs
QUIET_START = time(22, 0)       # §7.8 quiet hours, ET
QUIET_END = time(7, 0)
LINE_MAX = 140

SENT = "NOTIFY_SENT"            # actions_log: one row per push, the budget's count

# Categories whose HIGH mail is "regulatory/legal" (§8.8). A summons is set to
# LEGAL/court HIGH by a taxonomy override; a tax authority's notice is TAX.
# Formation papers and client agreements are LEGAL too, but arrive at NORMAL.
LEGAL_CATEGORIES = frozenset({"LEGAL", "TAX"})
PRIVATE_CATEGORIES = frozenset({"HEALTH"})       # as the brief (R15)

LABEL = {"CRITICAL": "URGENT", "LEGAL": "Legal/tax", "PHISHING": "Phishing blocked",
         "SYSTEM": "System"}


@dataclass(frozen=True)
class Pending:
    id: int
    reason: str
    critical: bool
    text: str
    created_at: str


# ---------------------------------------------------------------------------
# Enqueue — called by ingest, and by ops/notify.py for system failures
# ---------------------------------------------------------------------------

def is_fresh(received_date: str | None, today: date) -> bool:
    """Received recently enough to be news. Unknown counts as fresh: a photo
    from the phone Shortcut has no received date and was taken just now."""
    if not received_date:
        return True
    try:
        received = date.fromisoformat(received_date[:10])
    except ValueError:
        return True
    return (today - received).days <= FRESH_DAYS


def reason_for(urgency: str | None, category: str | None) -> str | None:
    """Why a classified document interrupts, or None if it waits for the brief."""
    if urgency == "CRITICAL":
        return "CRITICAL"
    if urgency == "HIGH" and category in LEGAL_CATEGORIES:
        return "LEGAL"
    return None


def _clean(text: str) -> str:
    """One redacted line. The fields are model output read from untrusted
    mail, so a domain is defanged (Telegram links bare domains) and nothing
    longer than a line survives."""
    text = " ".join(redact(text or "").split())
    text = re.sub(r"https?://", "", text)
    text = re.sub(r"\b([a-z0-9-]+)\.([a-z]{2,})\b", r"\1[.]\2", text, flags=re.I)
    return text if len(text) <= LINE_MAX else text[:LINE_MAX - 1] + "…"


def describe(*, category: str, counterparty: str | None, descriptor: str | None,
             due_date: str | None, amount_cents: int | None) -> str:
    """What the document is, in a lock-screen line. Never a HEALTH title."""
    if category in PRIVATE_CATEGORIES:
        return "a private item (details in the brief)"
    what = (descriptor or category.lower()).replace("-", " ").strip()
    who = (counterparty or "").replace("-", " ").strip().title()
    parts = [what.capitalize() + (f", {who}" if who else "")]
    if due_date:
        parts.append(f"due {due_date}")
    if amount_cents:
        parts.append(f"${amount_cents / 100:,.2f}")
    return " · ".join(parts)


def enqueue(conn: sqlite3.Connection, *, key: str, reason: str, text: str,
            artifact_id: int | None = None) -> bool:
    """Queue one alert. Idempotent on `key`: re-ingesting, or the covering
    email and its attachment, add nothing. True when a row was added."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO notifications "
        "(key, reason, critical, artifact_id, text, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (key, reason, int(reason == "CRITICAL"), artifact_id,
         _clean(f"{LABEL[reason]}: {text}"), db.now_iso()))
    conn.commit()
    return cur.rowcount == 1


def for_document(conn: sqlite3.Connection, *, artifact_id: int,
                 parent_id: int | None, received_date: str | None,
                 urgency: str | None, category: str, counterparty: str | None,
                 descriptor: str | None, due_date: str | None,
                 amount_cents: int | None, today: date | None = None,
                 key: str | None = None) -> bool:
    """Queue a classified document if it interrupts and is news.

    Keyed on the EMAIL, so a notice and its covering note are one alert. A
    caller with a better key passes it: WhatsApp keys on the chat day, so the
    hourly check's alert and the day's filing are one alert (D-079)."""
    reason = reason_for(urgency, category)
    if reason is None or not is_fresh(received_date, today or datetime.now(TZ).date()):
        return False
    return enqueue(conn, key=key or f"artifact:{parent_id or artifact_id}",
                   reason=reason, artifact_id=artifact_id,
                   text=describe(category=category, counterparty=counterparty,
                                 descriptor=descriptor, due_date=due_date,
                                 amount_cents=amount_cents))


def for_phishing(conn: sqlite3.Connection, *, artifact_id: int,
                 parent_id: int | None, received_date: str | None,
                 why: str, today: date | None = None) -> bool:
    if not is_fresh(received_date, today or datetime.now(TZ).date()):
        return False
    why = why.removeprefix("SUSPECTED_PHISHING:").strip()
    return enqueue(conn, key=f"artifact:{parent_id or artifact_id}",
                   reason="PHISHING", artifact_id=artifact_id,
                   text=f"{why}. Quarantined; do not act on it.")


def for_system(conn: sqlite3.Connection, stalled: list[str], today: date) -> int:
    """One alert per stalled job per day (brief.stalled_jobs lines)."""
    added = 0
    for line in stalled:
        job = line.split(":", 1)[0].strip()
        added += enqueue(conn, key=f"system:{job}:{today.isoformat()}",
                         reason="SYSTEM", text=line)
    return added


# ---------------------------------------------------------------------------
# Sending — the budget
# ---------------------------------------------------------------------------

def pending(conn: sqlite3.Connection) -> list[Pending]:
    return [Pending(r["id"], r["reason"], bool(r["critical"]), r["text"],
                    r["created_at"])
            for r in conn.execute(
                "SELECT * FROM notifications WHERE sent_at IS NULL ORDER BY id")]


def sent_times(conn: sqlite3.Connection) -> list[datetime]:
    """The recent pushes. Compared as datetimes, never as strings: the stored
    offset changes at the DST boundary, and "-04:00" sorts after "-05:00"."""
    return [datetime.fromisoformat(r["ts"]) for r in conn.execute(
        "SELECT ts FROM actions_log WHERE action = ? ORDER BY id DESC LIMIT 20",
        (SENT,))]


def quiet(now: datetime) -> bool:
    t = now.astimezone(TZ).time()
    return t >= QUIET_START or t < QUIET_END


def why_not(items: list[Pending], sent: list[datetime], now: datetime) -> str | None:
    """None if `items` go out now, else why they wait. Pure.

    A CRITICAL in the batch sends the whole batch, whatever the time or the
    count: holding the others back would cost a second push later."""
    if not items:
        return "nothing pending"
    if any(i.critical for i in items):
        return None
    if quiet(now):
        return "quiet hours (22:00-07:00)"
    local = now.astimezone(TZ)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    today = [t for t in sent if t.astimezone(TZ) >= midnight]
    if len(today) >= MAX_PER_DAY:
        return f"{MAX_PER_DAY} pushes already today"
    if sent and now - max(sent) < MIN_GAP:
        return "a push within the last hour"
    return None


def message(items: list[Pending]) -> str:
    head = "LMS alert" if len(items) == 1 else f"LMS alerts ({len(items)})"
    lines = [head, ""] + [f"• {i.text}" for i in items]
    lines += ["", "Nothing has been done about these. Details in the brief."]
    return "\n".join(lines)


def mark_sent(conn: sqlite3.Connection, items: list[Pending]) -> None:
    ts = db.now_iso()
    conn.executemany("UPDATE notifications SET sent_at = ? WHERE id = ?",
                     [(ts, i.id) for i in items])
    db.log_action(conn, SENT, detail=f"{len(items)}: " +
                  ", ".join(sorted({i.reason for i in items})))
    conn.commit()
