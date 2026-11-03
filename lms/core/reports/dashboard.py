"""The task dashboard (R22) — the whole backlog on one page.

The addendum of Sept 28, §6:

    "The dashboard should show business versus personal work, separate
     entities, overdue and upcoming tasks, blocked items, approval-ready
     proposals, delegated assistant, estimated human hours remaining,
     estimated AI hours saved, and proof of completion."

Each clause is a section here, in that order of need rather than that order of
words: what waits on his answer first, what is late second, the breakdowns
after. The brief (reports/brief.py) is twenty seconds of "what needs you
today"; this is the page he opens when he wants to see everything, and it
answers the question the brief cannot — "is anything slipping that the top
seven did not show me?"

WHAT IT READS
-------------
Only the ledger's hooks (D-066): `ledger.ACTIVE` for "open", the indexed
`entity_id` / `owner` / `status` columns, `task_events` for why a task is
stuck and who verified it, and `approvals.pending()` (D-067). Ranking is the
brief's `todo_list`, so the two never disagree about what is most urgent. It
writes nothing: an approval past its time is left out, not expired here —
expiring returns a task for revision, and that is the brief job's write.

WHERE IT GOES
-------------
One self-contained static HTML file, written by ops/dashboard.py beside the
brief in iCloud Drive (no push channel yet; see ops/deliver_brief.py). It is
opened from the Files app on a phone, possibly offline, so:

  * No scripts, fonts, images or stylesheets from anywhere. A Content-Security-
    Policy of `default-src 'none'` says so to the browser as well, so a
    reference that slipped through would still load nothing. The system's
    posture is no egress (§8), and a page that phoned home when opened would
    break it from the one device that is outside the office.
  * Phone width first; light and dark from `prefers-color-scheme`.
  * No JavaScript at all. Nothing on the page needs it, and a page without
    script cannot be made to run one by a hostile title.

PRIVACY
-------
Titles come from email subjects, which are written by whoever sent the mail:
hostile input. Every piece of text reaches the page through `_safe`, one
function, which escapes HTML and masks the two identifier shapes a subject
line most often carries (SSN, EIN). It is one function on purpose: the shared
digest redactor being built for the brief can replace its body and every
string on this page is covered at once.

A task marked `sensitivity` high or private (spec §5.6; Q5, Q59: "excluded
from briefs and summaries") appears as "Private item #id": counted, dated and
ranked, because hiding that it exists would hide a deadline, but no title, no
detail, no evidence text and no approval counterparty. Those are at the Mac
(`./ops/done.py --show`, `./ops/approve.py`).

HOURS
-----
"Estimated human hours remaining" and "estimated AI hours saved" are R21's
capability estimates, which do not exist yet. The page says "not estimated yet
(R21)" rather than show a number nobody estimated. `Estimate` and `Hours` are
the shape R21 fills: pass `estimates` to `build_dashboard` and the section
shows the sums, with how many of the open tasks they cover.
"""

from __future__ import annotations

import html
import json
import re
import sqlite3
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Callable, Iterable, Mapping

from ..db import database as db
from ..pipeline import approvals as approvals_mod
from ..pipeline import ledger
from ..pipeline.registry import Registry
from . import brief as brief_mod

# "Upcoming", in the two horizons the requirement names.
UPCOMING_NEAR = 7
UPCOMING_FAR = 14

# Proof of completion looks back this far. Two weeks: long enough to cover a
# week he did not open the page, short enough to stay one screen.
PROOF_DAYS = 14
PROOF_LIMIT = 20

# Paused or stopped: the tasks that need a human to move at all.
STUCK = ("BLOCKED", "NEEDS_CLARIFICATION", "FAILED")

# Spec §5.6. `normal` is the only value a title may be shown for.
PRIVATE_SENSITIVITY = frozenset({"high", "private"})

SIDES = ("business", "personal", "mixed", "unsorted")
SIDE_LABEL = {"business": "Business", "personal": "Personal",
              "mixed": "Mixed", "unsorted": "Not yet sorted"}


# ---------------------------------------------------------------------------
# The one door text goes through
# ---------------------------------------------------------------------------

# Taxpayer identifiers in the two shapes a subject line carries them. Not a
# redactor: the shared one replaces this body (see the module docstring).
_SSN = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
_EIN = re.compile(r"(?<!\d)\d{2}-\d{7}(?!\d)")


def _safe(text: object) -> str:
    """Text as it may appear on the page: identifiers masked, HTML escaped.

    Every string the page shows comes through here, including ones that look
    harmless (entity names, agent names, status words): the rule is easier to
    check when it has no exceptions.
    """
    if text is None:
        return ""
    s = _EIN.sub("**-*******", _SSN.sub("***-**-****", str(text)))
    return html.escape(s, quote=True)


# ---------------------------------------------------------------------------
# The data
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Estimate:
    """One task's R21 estimate. Nothing produces these yet."""
    task_id: int
    human_minutes_remaining: float
    ai_minutes_saved: float
    confidence: str | None = None


@dataclass(frozen=True)
class Hours:
    open_tasks: int
    estimated_tasks: int = 0
    human_hours_remaining: float | None = None
    ai_hours_saved: float | None = None

    @property
    def estimated(self) -> bool:
        return self.estimated_tasks > 0


@dataclass(frozen=True)
class Item:
    """One task as the page shows it. `label` is already privacy-checked."""
    task_id: int
    label: str
    private: bool
    entity_id: str | None
    entity: str
    side: str
    status: str
    due_date: str | None
    due_in_days: int | None
    due_verified: bool
    reason: str               # why it ranks where it does (brief's words)
    agent: str | None
    note: str | None = None   # why it is stuck, or the proof; None if private

    @property
    def overdue(self) -> bool:
        return self.due_in_days is not None and self.due_in_days < 0


@dataclass(frozen=True)
class Group:
    """One row of a breakdown: business/personal, an entity, an assistant."""
    key: str
    label: str
    open: int = 0
    overdue: int = 0
    due_soon: int = 0          # due within UPCOMING_NEAR days, not overdue
    stuck: int = 0
    awaiting: int = 0          # AWAITING_APPROVAL


@dataclass(frozen=True)
class ApprovalItem:
    ref: str
    task_id: int
    task_label: str
    private: bool
    kind: str
    summary: list[tuple[str, str]]     # (field, value) of the exact action
    expires_at: str
    agreement_only: bool               # a hard stop: approving authorizes nothing
    reasons: str | None


@dataclass
class Dashboard:
    generated_at: str
    today: date
    open_count: int = 0
    sides: list[Group] = field(default_factory=list)
    entities: list[Group] = field(default_factory=list)
    agents: list[Group] = field(default_factory=list)
    overdue: list[Item] = field(default_factory=list)
    due_near: list[Item] = field(default_factory=list)     # 0..7 days
    due_far: list[Item] = field(default_factory=list)      # 8..14 days
    stuck: list[Item] = field(default_factory=list)
    approvals: list[ApprovalItem] = field(default_factory=list)
    approvals_expired: int = 0       # past their time, not yet expired by a job
    proof: list[Item] = field(default_factory=list)
    hours: Hours = field(default_factory=lambda: Hours(open_tasks=0))
    stalled: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """Nothing open and nothing waiting. Recent proof does not count:
        "nothing open" is still true on a day things were finished."""
        return not (self.open_count or self.approvals)


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------

def is_private(row: sqlite3.Row) -> bool:
    return (row["sensitivity"] or "normal").lower() in PRIVATE_SENSITIVITY


def task_label(row: sqlite3.Row) -> str:
    """The title, or the placeholder that stands in for it (spec §5.6)."""
    if is_private(row):
        return f"Private item #{row['id']}"
    return row["title"]


def _entity_labeler(registry: Registry | None) -> Callable[[str | None], str]:
    def label(entity_id: str | None) -> str:
        if not entity_id:
            return "Unassigned"
        if registry is None:
            return entity_id
        try:
            return registry.get(entity_id).short_or_name()
        except Exception:
            return entity_id
    return label


def side_of(row: sqlite3.Row, registry: Registry | None) -> str:
    """Business or personal: the task's own `domain` first (set by the
    classifier, spec §5.1), else the kind of its entity in entities.yaml.
    UNASSIGNED and JUNK, and an entity nobody knows, are "unsorted" — not
    guessed into one side."""
    domain = (row["domain"] or "").upper()
    if domain in ("BUSINESS", "PERSONAL", "MIXED"):
        return domain.lower()
    if registry is not None and row["entity_id"]:
        try:
            kind = registry.get(row["entity_id"]).kind
        except Exception:
            return "unsorted"
        return {"business": "business", "person": "personal"}.get(kind, "unsorted")
    return "unsorted"


def _parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _last_transition(conn: sqlite3.Connection, task_id: int,
                     to: Iterable[str]) -> sqlite3.Row | None:
    to = tuple(to)
    return conn.execute(
        f"SELECT * FROM task_events WHERE task_id = ? AND event = 'TRANSITION' "
        f"AND to_status IN ({','.join('?' * len(to))}) ORDER BY id DESC LIMIT 1",
        (task_id, *to)).fetchone()


def _item(row: sqlite3.Row, *, today: date, label: Callable[[str | None], str],
          registry: Registry | None, reason: str | None = None,
          note: str | None = None) -> Item:
    """`reason` None: the due date and the state, nothing else. That is what
    a private task gets in place of the brief's ranking reason, which can
    carry the amount."""
    private = is_private(row)
    _, days, when = brief_mod.due_pressure(_parse_date(row["due_date"]), today)
    if reason is None:
        bits = [when]
        if row["status"] in STUCK:
            bits.append(row["status"].replace("_", " ").lower())
        reason = " · ".join(bits)
    return Item(
        task_id=int(row["id"]), label=task_label(row), private=private,
        entity_id=row["entity_id"], entity=label(row["entity_id"]),
        side=side_of(row, registry), status=row["status"],
        due_date=row["due_date"], due_in_days=days,
        due_verified=bool(row["due_date_verified"]), reason=reason,
        agent=row["assigned_agent"], note=None if private else note)


def _tally(items: list[Item], key: Callable[[Item], str | None],
           label: Callable[[str | None], str]) -> dict[str | None, Group]:
    out: dict[str | None, Group] = {}
    for it in items:
        k = key(it)
        g = out.get(k) or Group(key=k or "", label=label(k))
        d = it.due_in_days
        out[k] = Group(
            key=g.key, label=g.label, open=g.open + 1,
            overdue=g.overdue + (1 if it.overdue else 0),
            due_soon=g.due_soon + (1 if d is not None and 0 <= d <= UPCOMING_NEAR else 0),
            stuck=g.stuck + (1 if it.status in STUCK else 0),
            awaiting=g.awaiting + (1 if it.status == "AWAITING_APPROVAL" else 0))
    return out


def _money(cents: int | None) -> str:
    return "" if cents is None else f"${cents / 100:,.2f}"


# The exact action, as approve.py shows it, in the order he reads a payment.
_ACTION_FIELDS = (("recipient", "to"), ("amount_cents", "amount"),
                  ("destination", "destination"), ("source_account", "from account"),
                  ("rail", "rail"), ("category", "category"),
                  ("reply_class", "reply"), ("doc_version", "document"),
                  ("draft_sha256", "draft"), ("files", "files"))


def _action_summary(a: sqlite3.Row, private: bool) -> list[tuple[str, str]]:
    try:
        action = json.loads(a["action_json"])
    except (TypeError, ValueError):
        action = {}
    out: list[tuple[str, str]] = []
    for key, name in _ACTION_FIELDS:
        v = action.get(key)
        if v in (None, "", []):
            continue
        if private and key != "amount_cents":
            continue
        if key == "amount_cents":
            v = _money(v) + ("" if action.get("currency", "USD") == "USD"
                             else f" {action.get('currency')}")
        elif key in ("doc_version", "draft_sha256"):
            v = f"{str(v)[:12]}…"           # enough to compare with approve.py
        elif key == "files":
            v = ", ".join(v)
        out.append((name, str(v)))
    if private:
        out.append(("details", "private task: shown at the Mac only"))
    return out


def _approval_items(conn: sqlite3.Connection, now: datetime
                    ) -> tuple[list[ApprovalItem], int]:
    """Waiting approvals, soonest expiry first; and how many are past their
    time but not yet expired by the brief job (not shown as live)."""
    live, stale = [], 0
    for a in approvals_mod.pending(conn):
        if datetime.fromisoformat(a["expires_at"]) <= now:
            stale += 1
            continue
        task = ledger.get(conn, int(a["task_id"]))
        private = is_private(task)
        live.append(ApprovalItem(
            ref=a["ref"], task_id=int(a["task_id"]), task_label=task_label(task),
            private=private, kind=a["kind"],
            summary=_action_summary(a, private), expires_at=a["expires_at"],
            agreement_only=a["policy_verdict"] == "REFUSED",
            reasons=None if private else a["policy_reasons"]))
    return live, stale


def _proof(conn: sqlite3.Connection, now: datetime, today: date,
           label: Callable[[str | None], str],
           registry: Registry | None) -> list[Item]:
    """Recently verified or closed, with the evidence that says so (§6:
    "completion has evidence"). The newest first."""
    since = (now - timedelta(days=PROOF_DAYS)).isoformat(timespec="seconds")
    rows = conn.execute(
        "SELECT * FROM tasks WHERE status IN ('VERIFIED','CLOSED') "
        "AND COALESCE(verification_evidence, '') != '' "
        "AND COALESCE(completed_at, status_changed_at, created_at) >= ? "
        "ORDER BY COALESCE(completed_at, status_changed_at, created_at) DESC, id DESC "
        "LIMIT ?", (since, PROOF_LIMIT)).fetchall()
    out = []
    for r in rows:
        ev = _last_transition(conn, int(r["id"]), ("VERIFIED", "CLOSED"))
        when = (r["completed_at"] or r["status_changed_at"] or "")[:16].replace("T", " ")
        by = f" · by {ev['actor']}" if ev else ""
        out.append(_item(r, today=today, label=label, registry=registry,
                         reason=f"{r['status'].lower()} {when}{by}",
                         note=r["verification_evidence"]))
    return out


def hours_summary(open_ids: Iterable[int],
                  estimates: Mapping[int, Estimate] | None) -> Hours:
    ids = list(open_ids)
    if not estimates:
        return Hours(open_tasks=len(ids))
    have = [estimates[i] for i in ids if i in estimates]
    if not have:
        return Hours(open_tasks=len(ids))
    return Hours(open_tasks=len(ids), estimated_tasks=len(have),
                 human_hours_remaining=sum(e.human_minutes_remaining for e in have) / 60,
                 ai_hours_saved=sum(e.ai_minutes_saved for e in have) / 60)


def build_dashboard(conn: sqlite3.Connection, *, registry: Registry | None = None,
                    now: datetime | None = None,
                    estimates: Mapping[int, Estimate] | None = None) -> Dashboard:
    """Everything the page shows, from the database. Reads only."""
    now = now or datetime.now(db.TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=db.TZ)
    today = now.date()
    label = _entity_labeler(registry)

    scored = brief_mod.todo_list(conn, today=today)
    items = [_item(s.row, today=today, label=label, registry=registry,
                   reason=None if is_private(s.row) else s.reason) for s in scored]

    stuck = []
    for it in items:
        if it.status in STUCK:
            ev = _last_transition(conn, it.task_id, (it.status,))
            note = (ev["detail"] if ev and ev["detail"] else None)
            if ev:
                note = f"{note} ({ev['actor']}, {ev['ts'][:10]})" if note \
                    else f"since {ev['ts'][:10]}, {ev['actor']}"
            stuck.append(replace(it, note=None if it.private else note))

    sides = _tally(items, lambda i: i.side, lambda k: SIDE_LABEL.get(k or "", k or ""))
    # Business and personal always shown, at zero too: "0 business" is an answer.
    side_rows = [sides.get(s) or Group(key=s, label=SIDE_LABEL[s]) for s in SIDES
                 if s in ("business", "personal") or s in sides]

    ent = _tally(items, lambda i: i.entity_id, label)
    agents = _tally(items, lambda i: i.agent,
                    lambda k: k or "Not delegated")
    by_size = lambda g: (-g.open, -g.overdue, g.label.lower())      # noqa: E731

    pending, stale = _approval_items(conn, now)

    return Dashboard(
        generated_at=now.isoformat(timespec="seconds"),
        today=today,
        open_count=len(items),
        sides=side_rows,
        entities=sorted(ent.values(), key=by_size),
        # Undelegated last: it is the remainder, not an assistant.
        agents=sorted(agents.values(), key=lambda g: (g.key == "",) + by_size(g)),
        overdue=[i for i in items if i.overdue],
        due_near=[i for i in items if i.due_in_days is not None
                  and 0 <= i.due_in_days <= UPCOMING_NEAR],
        due_far=[i for i in items if i.due_in_days is not None
                 and UPCOMING_NEAR < i.due_in_days <= UPCOMING_FAR],
        stuck=stuck,
        approvals=pending,
        approvals_expired=stale,
        proof=_proof(conn, now, today, label, registry),
        hours=hours_summary((i.task_id for i in items), estimates),
        stalled=brief_mod.stalled_jobs(conn, now),
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

# Inline, because nothing may be fetched. Colour tokens once, redefined for
# dark. System fonts only: -apple-system is the phone's own.
_CSS = """
:root{--bg:#f7f7f5;--card:#ffffff;--fg:#1c1c1a;--muted:#66665f;--line:#e2e1dc;
--warn:#a8261b;--warn-bg:#fcebe8;--note:#7a4b00;--note-bg:#fdf3dc;--ok:#2d6a33;
--ok-bg:#e7f3e8;--accent:#1d5bb8}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--card:#1e1e1c;--fg:#ecebe6;
--muted:#a3a29b;--line:#34332f;--warn:#ff8a7d;--warn-bg:#3a1d19;--note:#f2c46b;
--note-bg:#352a12;--ok:#86c98d;--ok-bg:#1a2e1c;--accent:#8db6ff}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);
font:16px/1.45 -apple-system,BlinkMacSystemFont,"Helvetica Neue",Helvetica,Arial,sans-serif}
main{max-width:720px;margin:0 auto;padding:16px}
h1{font-size:1.5rem;margin:0 0 2px}
h2{font-size:1.1rem;margin:0 0 8px}
h3{font-size:.95rem;margin:12px 0 6px;color:var(--muted)}
p{margin:0 0 8px}
.meta,.m,.empty,footer{color:var(--muted);font-size:.875rem}
section{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:14px;margin:12px 0}
.alarm{background:var(--warn-bg);border-color:var(--warn);color:var(--warn)}
.calm{background:var(--ok-bg);border-color:var(--ok);color:var(--ok)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(96px,1fr));gap:8px;
margin:12px 0}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:8px 10px}
.tile b{display:block;font-size:1.4rem;font-variant-numeric:tabular-nums}
.tile span{font-size:.8rem;color:var(--muted)}
.tile.hot b{color:var(--warn)}
ul{list-style:none;margin:0;padding:0}
li{padding:8px 0;border-top:1px solid var(--line)}
li:first-child{border-top:0}
.t{font-weight:600;overflow-wrap:anywhere}
.m,.note{overflow-wrap:anywhere}
.note{font-size:.875rem;margin-top:2px}
.overdue .m{color:var(--warn)}
.badge{display:inline-block;font-size:.75rem;font-weight:600;border-radius:6px;
padding:1px 6px;margin-left:4px;vertical-align:1px}
.agree{background:var(--note-bg);color:var(--note)}
.prov{background:var(--line);color:var(--muted)}
dl{display:grid;grid-template-columns:max-content 1fr;gap:2px 10px;margin:6px 0 0;
font-size:.875rem}
dt{color:var(--muted)}
dd{margin:0;overflow-wrap:anywhere}
table{width:100%;border-collapse:collapse;font-size:.875rem;font-variant-numeric:tabular-nums}
th,td{padding:6px 4px;border-top:1px solid var(--line);text-align:right}
th{color:var(--muted);font-weight:600;border-top:0}
th:first-child,td:first-child{text-align:left;overflow-wrap:anywhere}
td.hot{color:var(--warn);font-weight:600}
"""

_HEAD = ("<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
         "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
         "<meta http-equiv=\"Content-Security-Policy\" "
         "content=\"default-src 'none'; style-src 'unsafe-inline'\">\n"
         "<meta name=\"color-scheme\" content=\"light dark\">\n"
         "<meta name=\"robots\" content=\"noindex\">\n")


def _page(title: str, body: list[str]) -> str:
    return (_HEAD + f"<title>{_safe(title)}</title>\n<style>{_CSS}</style>\n"
            "</head>\n<body>\n<main>\n" + "\n".join(body) + "\n</main>\n</body>\n</html>\n")


def _stamp(iso: str) -> str:
    return iso[:16].replace("T", " ")


def _task_li(it: Item, *, show_entity: bool = True) -> str:
    cls = ' class="overdue"' if it.overdue else ""
    meta = [it.entity] if show_entity else []
    meta.append(it.reason)
    if it.agent:
        meta.append(f"with {it.agent}")
    prov = ""
    if it.due_date and not it.due_verified and it.status not in ledger.TERMINAL:
        # "A detected due date is provisional until verified against the source."
        prov = ' <span class="badge prov">date unverified</span>'
    note = f'\n<div class="note">{_safe(it.note)}</div>' if it.note else ""
    head = _safe(it.label) if it.private else f"#{it.task_id} · {_safe(it.label)}"
    return (f"<li{cls}><div class=\"t\">{head}</div>"
            f"<div class=\"m\">{' · '.join(_safe(m) for m in meta)}{prov}</div>{note}</li>")


def _task_list(items: list[Item], empty: str, **kw) -> str:
    if not items:
        return f'<p class="empty">{_safe(empty)}</p>'
    return "<ul>\n" + "\n".join(_task_li(i, **kw) for i in items) + "\n</ul>"


def _group_table(groups: list[Group], first: str) -> str:
    if not groups:
        return '<p class="empty">Nothing open.</p>'
    def hot(n: int) -> str:
        return f'<td class="hot">{n}</td>' if n else f"<td>{n}</td>"

    rows = []
    for g in groups:
        rows.append(f"<tr><td>{_safe(g.label)}</td><td>{g.open}</td>{hot(g.overdue)}"
                    f"<td>{g.due_soon}</td>{hot(g.stuck)}<td>{g.awaiting}</td></tr>")
    return (f"<table>\n<tr><th>{_safe(first)}</th><th>open</th><th>overdue</th>"
            f"<th>≤{UPCOMING_NEAR}d</th><th>stuck</th><th>approval</th></tr>\n"
            + "\n".join(rows) + "\n</table>")


def _approval_li(a: ApprovalItem, now: datetime) -> str:
    left = datetime.fromisoformat(a.expires_at) - now
    hours = max(0, int(left.total_seconds() // 3600))
    expiry = f"expires {_stamp(a.expires_at)} (in {hours}h)" if hours < 48 \
        else f"expires {_stamp(a.expires_at)}"
    badge = ""
    explain = ""
    if a.agreement_only:
        badge = ' <span class="badge agree">Agreement only</span>'
        explain = ("<div class=\"note\">A hard stop forbids the system doing this. "
                   "Approving records your agreement; nothing will execute it. "
                   "Do it yourself, then close the task."
                   + (f" ({_safe(a.reasons)})" if a.reasons else "") + "</div>")
    dl = "".join(f"<dt>{_safe(k)}</dt><dd>{_safe(v)}</dd>" for k, v in a.summary)
    cls = "approval agreement-only" if a.agreement_only else "approval"
    return (f"<li class=\"{cls}\"><div class=\"t\">{_safe(a.ref)} · "
            f"{_safe(a.kind.replace('_', ' '))}{badge}</div>"
            f"<div class=\"m\">"
            f"{'' if a.private else f'task #{a.task_id} · '}{_safe(a.task_label)} · "
            f"{_safe(expiry)}</div>"
            + (f"<dl>{dl}</dl>" if dl else "") + explain + "</li>")


def _hours_section(h: Hours) -> list[str]:
    out = ['<section id="hours">', "<h2>Hours</h2>"]
    if not h.estimated:
        out.append("<dl><dt>Human hours remaining</dt><dd>not estimated yet (R21)</dd>"
                   "<dt>AI hours saved</dt><dd>not estimated yet (R21)</dd></dl>")
        out.append('<p class="empty">Per-task capability estimates are not built. '
                   "No number is shown until one is estimated.</p>")
    else:
        out.append(f"<dl><dt>Human hours remaining</dt>"
                   f"<dd>{h.human_hours_remaining:,.1f} h</dd>"
                   f"<dt>AI hours saved</dt><dd>{h.ai_hours_saved:,.1f} h</dd></dl>")
        out.append(f'<p class="empty">From {h.estimated_tasks} of {h.open_tasks} '
                   f"open tasks; the rest are not estimated yet.</p>")
    out.append("</section>")
    return out


def render_html(d: Dashboard) -> str:
    now = datetime.fromisoformat(d.generated_at)
    body = ["<header>", "<h1>Tasks</h1>",
            f'<p class="meta">As of {_safe(_stamp(d.generated_at))}. A snapshot: '
            f"it changes only when the next one is written.</p>", "</header>"]

    # First, above the work, as in the brief: if a job has stopped, every
    # number below describes an incomplete picture.
    if d.stalled:
        body += ['<section id="stalled" class="alarm">',
                 "<h2>The system needs attention</h2>", "<ul>"]
        body += [f"<li>{_safe(s)}</li>" for s in d.stalled]
        body += ["</ul>", "<p>Everything below may be incomplete.</p>", "</section>"]

    tiles = [("open", d.open_count, False), ("overdue", len(d.overdue), True),
             (f"due ≤{UPCOMING_NEAR}d", len(d.due_near), False),
             ("stuck", len(d.stuck), True), ("approvals", len(d.approvals), True)]
    body.append('<div class="tiles">' + "".join(
        f'<div class="tile{" hot" if hot and n else ""}"><b>{n}</b>'
        f"<span>{_safe(name)}</span></div>" for name, n, hot in tiles) + "</div>")

    # Stated, never implied (D-030): an empty page and a broken one look alike.
    if d.is_empty:
        body += ['<section id="nothing-open" class="calm">',
                 "<h2>Nothing open</h2>",
                 "<p>No open tasks and nothing waiting on an approval.</p>",
                 "</section>"]

    body += ['<section id="approvals">',
             f"<h2>Approvals waiting ({len(d.approvals)})</h2>"]
    if d.approvals:
        body.append("<ul>\n" + "\n".join(_approval_li(a, now) for a in d.approvals)
                    + "\n</ul>")
        body.append('<p class="empty">Each is for these exact details only, used once, '
                    "and expires. Answer at the Mac: ./ops/approve.py AP-n</p>")
    else:
        body.append('<p class="empty">Nothing is waiting for approval.</p>')
    if d.approvals_expired:
        body.append(f'<p class="empty">{d.approvals_expired} past its expiry and not '
                    f"shown; the next brief returns it for revision.</p>")
    body.append("</section>")

    body += ['<section id="overdue">', "<h2>Overdue and upcoming</h2>",
             f"<h3>Overdue ({len(d.overdue)})</h3>",
             _task_list(d.overdue, "Nothing overdue."),
             f"<h3>Next {UPCOMING_NEAR} days ({len(d.due_near)})</h3>",
             _task_list(d.due_near, f"Nothing due in the next {UPCOMING_NEAR} days."),
             f"<h3>{UPCOMING_NEAR + 1}–{UPCOMING_FAR} days ({len(d.due_far)})</h3>",
             _task_list(d.due_far, "Nothing due in the week after."),
             "</section>"]

    body += ['<section id="blocked">',
             f"<h2>Blocked and needs clarification ({len(d.stuck)})</h2>",
             _task_list(d.stuck, "Nothing is blocked or waiting on a question."),
             "</section>"]

    body += ['<section id="split">', "<h2>Business and personal</h2>",
             _group_table(d.sides, "side"), "</section>"]
    body += ['<section id="entities">', "<h2>By entity</h2>",
             _group_table(d.entities, "entity"), "</section>"]
    body += ['<section id="agents">', "<h2>Delegated assistant</h2>",
             _group_table(d.agents, "assistant"), "</section>"]

    body += _hours_section(d.hours)

    body += ['<section id="proof">',
             f"<h2>Proof of completion (last {PROOF_DAYS} days)</h2>",
             _task_list(d.proof, f"Nothing verified or closed with evidence in the "
                                 f"last {PROOF_DAYS} days."),
             "</section>"]

    body.append("<footer><p>Close a task: ./ops/done.py &lt;id&gt; · answer an "
                "approval: ./ops/approve.py AP-n · the full record of any task: "
                "./ops/done.py --show &lt;id&gt;. Private items are named at the "
                "Mac only.</p></footer>")
    return _page("LMS tasks", body)


def render_alarm(problem: str, now: datetime) -> str:
    """The page written when the database cannot be read (archive volume
    missing). It replaces the last dashboard rather than leaving it up: a
    stale page showing yesterday's backlog as current is worse than none."""
    return _page("LMS tasks", [
        "<header><h1>Tasks</h1>",
        f'<p class="meta">As of {_safe(now.isoformat(timespec="minutes")[:16].replace("T", " "))}'
        "</p></header>",
        '<section id="stalled" class="alarm"><h2>The system needs attention</h2>',
        f"<p>{_safe(problem)}</p>",
        "<p>Nothing can be shown until this is fixed: without the archive there "
        "are no tasks, approvals or job history to report.</p></section>"])


def render_summary(d: Dashboard) -> str:
    """Counts only, for a terminal or a launchd log. No titles: the log is
    not where private items should be named either."""
    side = " · ".join(f"{g.label} {g.open} (overdue {g.overdue})" for g in d.sides)
    h = d.hours
    hours = (f"{h.human_hours_remaining:,.1f} h human remaining, "
             f"{h.ai_hours_saved:,.1f} h AI saved ({h.estimated_tasks}/{h.open_tasks})"
             if h.estimated else "not estimated yet (R21)")
    out = [f"Dashboard · {_stamp(d.generated_at)}"]
    if d.stalled:
        out.append("THE SYSTEM NEEDS ATTENTION: " + "; ".join(d.stalled))
    if d.is_empty:
        out.append("Nothing open.")
    out += [
        f"open {d.open_count} · overdue {len(d.overdue)} · due in {UPCOMING_NEAR}d "
        f"{len(d.due_near)} · {UPCOMING_NEAR + 1}–{UPCOMING_FAR}d {len(d.due_far)}",
        f"blocked/clarify/failed {len(d.stuck)} · approvals waiting {len(d.approvals)}"
        + (f" ({sum(a.agreement_only for a in d.approvals)} agreement only)"
           if d.approvals else "")
        + (f" · {d.approvals_expired} past expiry" if d.approvals_expired else ""),
        side,
        f"entities {len(d.entities)} · assistants "
        + (", ".join(f"{g.label} {g.open}" for g in d.agents) or "none"),
        f"hours: {hours}",
        f"proof of completion, last {PROOF_DAYS} days: {len(d.proof)}",
    ]
    return "\n".join(out)
