"""The policy engine (R19, D-067): the approval matrix and the routine-action
mandate, decided in code.

The addendum of Sept 28, §3:

    "The policy engine, separate from the language model, checks the mandate
     at execution time. A task outside scope must pause; an unavailable
     approval channel must not silently authorize it."

Nothing here calls a model. What an agent proposes arrives as an `Action`, and
every field of it is compared with something a human wrote: a mandate in
config/mandates.yaml, or an approval Matthew gave (core/pipeline/approvals.py).

LEVELS
------
Every action has a kind and every kind a level (`KIND_LEVEL`). The level that
applies is the higher of the kind's and the task's assessed `action_risk`
(unassessed counts as D): an agent can raise a task's level, never lower it
below what the action is.

    A observe     automatic
    B prepare     automatic (staging only)
    C routine     automatic only inside a LIVE mandate whose every check passes
    D exception   Matthew approves the exact action

D kinds are never inside a mandate. A C action that fails a mandate check
(a new payee, a changed bank destination, an amount over the ceiling) is
treated as D: it pauses for approval.

HARD STOPS
----------
rules.yaml's hard stops sit above all four levels. While `never_move_money` is
true no level, mandate or approval executes a payment; while
`never_send_without_approval` is true a mandate cannot stand in for Matthew's
approval of a send. Lifting one is a recorded decision per action class (gap
analysis X1), not an edit to this file or to a mandate.

SHADOW MODE
-----------
"Trial mode" in the Oct 19 message. A mandate in mode `shadow` is evaluated
exactly as a live one would be, and what it would have done is stored on the
approval Matthew is asked for (`Decision.would_execute`). Its record — how
often the mandate would have acted, and whether he ever disagreed — is the
evidence for making it live.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from ..db import database as db

CONFIG = Path(__file__).resolve().parents[2] / "config"

LEVELS = "ABCD"

KIND_LEVEL: dict[str, str] = {
    # A — observe
    "read": "A", "classify": "A", "summarize": "A", "search": "A",
    # B — prepare
    "draft_reply": "B", "prepare_invite": "B", "copy_to_managed": "B",
    "report": "B",
    # C — routine execution, inside a mandate
    "pay_bill": "C", "send_reply": "C", "create_event": "C",
    "update_record": "C",
    # D — exception; never inside a mandate
    "transfer": "D", "delete": "D", "access_change": "D",
    "submit_filing": "D", "disclose": "D",
}

# A hard stop that forbids the kind outright, approval or not.
REFUSED_BY: dict[str, str] = {
    "pay_bill": "never_move_money",
    "transfer": "never_move_money",
    "delete": "never_delete_file",
    "submit_filing": "never_sign_or_bind",
}
# A hard stop under which only Matthew's approval authorizes the kind.
APPROVAL_ONLY_BY: dict[str, str] = {
    "send_reply": "never_send_without_approval",
}

MONEY_KINDS = frozenset({"pay_bill", "transfer"})

# "The approval object states ... payee and bank destination, amount": an
# action of these kinds that leaves one out is not an exact action.
REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "pay_bill": ("doc_version", "recipient", "destination", "amount_cents",
                 "source_account", "rail"),
    "transfer": ("recipient", "destination", "amount_cents", "source_account",
                 "rail"),
    "send_reply": ("recipient", "draft_sha256", "reply_class"),
    "create_event": ("destination",),
    "update_record": ("destination",),
    "delete": ("destination",),
    "access_change": ("destination",),
    "submit_filing": ("recipient", "doc_version"),
    "disclose": ("recipient", "doc_version"),
}

ALLOW, NEEDS_APPROVAL, REFUSED = "ALLOW", "NEEDS_APPROVAL", "REFUSED"
SHADOW, LIVE = "shadow", "live"
PERIODS = ("day", "week", "month")


# ---------------------------------------------------------------------------
# The action
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Action:
    """One exact action. Every field is material: change one and an approval
    given for the old value no longer applies."""
    kind: str
    doc_version: str | None = None       # sha256 of the document acted on
    recipient: str | None = None         # payee or correspondent
    destination: str | None = None       # bank account, address, path, calendar
    amount_cents: int | None = None
    currency: str = "USD"
    source_account: str | None = None    # the account paying
    rail: str | None = None              # how it is paid
    category: str | None = None          # e.g. utilities
    reply_class: str | None = None       # e.g. acknowledge_receipt
    draft_sha256: str | None = None      # the exact text to be sent
    files: tuple[str, ...] = ()

    def __post_init__(self):
        if self.kind not in KIND_LEVEL:
            raise ValueError(f"unknown action kind {self.kind!r}")
        if self.amount_cents is not None and self.amount_cents < 0:
            raise ValueError("amount_cents is never negative; a refund is not a payment")
        object.__setattr__(self, "files", tuple(sorted(self.files)))

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["files"] = list(self.files)
        return d

    @classmethod
    def from_json(cls, text: str) -> "Action":
        d = json.loads(text)
        d["files"] = tuple(d.get("files") or ())
        return cls(**d)

    def missing(self) -> list[str]:
        return [f for f in REQUIRED_FIELDS.get(self.kind, ())
                if getattr(self, f) in (None, "")]


def fingerprint(action: Action) -> str:
    """The identity of the action, not of the task.

    The task is left out on purpose: the same bill recorded as two tasks gives
    the same fingerprint, so the second payment is caught as a duplicate
    (addendum demonstration scenario 4).
    """
    blob = json.dumps(action.as_dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def changed_fields(approved: Action, now: Action) -> list[str]:
    a, b = approved.as_dict(), now.as_dict()
    return sorted(k for k in a if a[k] != b[k])


# ---------------------------------------------------------------------------
# Mandates
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Counterparty:
    name: str
    destination: str | None = None       # the verified destination; None = any
    max_cents: int | None = None
    min_cents: int = 0


@dataclass(frozen=True)
class Mandate:
    id: str
    decided_in: str                      # the DECISIONS.md entry: provenance
    mode: str
    kinds: tuple[str, ...]
    entity: str
    effective_from: date
    effective_until: date
    counterparties: dict[str, Counterparty]
    source_accounts: tuple[str, ...] = ()
    rails: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    reply_classes: tuple[str, ...] = ()
    max_cents: int | None = None
    frequency: tuple[int, str] | None = None     # (count, per) per counterparty
    daily_aggregate_cents: int | None = None
    revoked: date | None = None


@dataclass(frozen=True)
class Policy:
    hard_stops: dict[str, bool]
    mandates: tuple[Mandate, ...]
    approval_ttl_hours: int


class PolicyConfigError(ValueError):
    pass


_MANDATE_KEYS = {"id", "decided_in", "mode", "kinds", "entity", "effective_from",
                 "effective_until", "counterparties", "source_accounts", "rails",
                 "categories", "reply_classes", "max_cents", "frequency",
                 "daily_aggregate_cents", "revoked"}


def _as_date(v: Any, what: str) -> date:
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v))
    except ValueError:
        raise PolicyConfigError(f"{what}: {v!r} is not a date") from None


def _mandate(raw: dict[str, Any]) -> Mandate:
    mid = raw.get("id") or "?"
    unknown = set(raw) - _MANDATE_KEYS
    if unknown:
        # A misspelt limit is a limit that silently does not apply.
        raise PolicyConfigError(f"mandate {mid}: unknown keys {sorted(unknown)}")
    for key in ("id", "decided_in", "mode", "kinds", "entity",
                "effective_from", "effective_until", "counterparties"):
        if not raw.get(key):
            raise PolicyConfigError(f"mandate {mid}: {key} is required")
    if raw["mode"] not in (SHADOW, LIVE):
        raise PolicyConfigError(f"mandate {mid}: mode is {SHADOW} or {LIVE}")
    kinds = tuple(raw["kinds"])
    for k in kinds:
        if KIND_LEVEL.get(k) != "C":
            raise PolicyConfigError(
                f"mandate {mid}: {k!r} is not a level C kind; only routine "
                f"execution can be mandated")
    start = _as_date(raw["effective_from"], f"mandate {mid} effective_from")
    until = _as_date(raw["effective_until"], f"mandate {mid} effective_until")
    if until < start:
        raise PolicyConfigError(f"mandate {mid}: ends before it starts")

    parties = {}
    for name, cp in (raw["counterparties"] or {}).items():
        cp = cp or {}
        extra = set(cp) - {"destination", "max_cents", "min_cents"}
        if extra:
            raise PolicyConfigError(f"mandate {mid}, {name}: unknown keys {sorted(extra)}")
        parties[str(name)] = Counterparty(
            name=str(name), destination=cp.get("destination"),
            max_cents=cp.get("max_cents"), min_cents=int(cp.get("min_cents") or 0))
    if set(kinds) & MONEY_KINDS:
        for cp in parties.values():
            # "Historic paid bills may establish a recurring pattern, but they
            # do not authorize a new bank destination."
            if not cp.destination or cp.max_cents is None:
                raise PolicyConfigError(
                    f"mandate {mid}, {cp.name}: a payee needs its verified "
                    f"destination and a ceiling")
        for key in ("source_accounts", "rails", "daily_aggregate_cents"):
            if not raw.get(key):
                raise PolicyConfigError(f"mandate {mid}: a payment mandate needs {key}")
    if "send_reply" in kinds and not raw.get("reply_classes"):
        raise PolicyConfigError(f"mandate {mid}: a reply mandate needs reply_classes")

    freq = raw.get("frequency")
    frequency = None
    if freq:
        if freq.get("per") not in PERIODS or int(freq.get("count", 0)) < 1:
            raise PolicyConfigError(f"mandate {mid}: frequency is {{count: n, per: day|week|month}}")
        frequency = (int(freq["count"]), freq["per"])

    return Mandate(
        id=str(raw["id"]), decided_in=str(raw["decided_in"]), mode=raw["mode"],
        kinds=kinds, entity=str(raw["entity"]), effective_from=start,
        effective_until=until, counterparties=parties,
        source_accounts=tuple(raw.get("source_accounts") or ()),
        rails=tuple(raw.get("rails") or ()),
        categories=tuple(raw.get("categories") or ()),
        reply_classes=tuple(raw.get("reply_classes") or ()),
        max_cents=raw.get("max_cents"), frequency=frequency,
        daily_aggregate_cents=raw.get("daily_aggregate_cents"),
        revoked=_as_date(raw["revoked"], f"mandate {mid} revoked")
        if raw.get("revoked") else None)


def load_policy(config_dir: Path | str = CONFIG) -> Policy:
    config_dir = Path(config_dir)
    rules = yaml.safe_load((config_dir / "rules.yaml").read_text(encoding="utf-8"))
    path = config_dir / "mandates.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    raw = raw or {}
    mandates = tuple(_mandate(m) for m in (raw.get("mandates") or ()))
    ids = [m.id for m in mandates]
    if len(ids) != len(set(ids)):
        raise PolicyConfigError("two mandates share an id")
    return Policy(hard_stops=dict(rules.get("hard_stops") or {}),
                  mandates=mandates,
                  approval_ttl_hours=int(raw.get("approval_ttl_hours") or 48))


# ---------------------------------------------------------------------------
# Deciding
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Decision:
    verdict: str                          # ALLOW | NEEDS_APPROVAL | REFUSED
    level: str
    reasons: tuple[str, ...] = ()
    mandate_id: str | None = None
    # Every check of a covering mandate passed. True for a shadow mandate too:
    # that is what it would have done, recorded for Matthew to compare.
    would_execute: bool = False
    fingerprint: str = ""
    checks: tuple[str, ...] = field(default=())


def level_of(task: sqlite3.Row, action: Action | None) -> str:
    assessed = task["action_risk"] or "D"
    kind = KIND_LEVEL[action.kind] if action else "A"
    return max(assessed, kind, key=LEVELS.index)


def _period_start(today: date, per: str) -> date:
    if per == "day":
        return today
    if per == "week":
        return today - timedelta(days=today.weekday())
    return today.replace(day=1)


def _executed_under(conn: sqlite3.Connection, mandate_id: str, since: date,
                    recipient: str | None = None) -> tuple[int, int]:
    sql = ("SELECT COUNT(*) AS n, COALESCE(SUM(amount_cents), 0) AS total "
           "FROM approvals WHERE mandate_id = ? AND status = 'CONSUMED' "
           "AND consumed_at >= ?")
    args: list[Any] = [mandate_id, since.isoformat()]
    if recipient is not None:
        sql += " AND recipient = ?"
        args.append(recipient)
    row = conn.execute(sql, args).fetchone()
    return int(row["n"]), int(row["total"])


def _mandate_failures(conn: sqlite3.Connection, m: Mandate, task: sqlite3.Row,
                      action: Action, today: date) -> list[str]:
    fail = []
    if m.revoked and m.revoked <= today:
        fail.append(f"revoked {m.revoked}")
    if not (m.effective_from <= today <= m.effective_until):
        fail.append(f"in effect {m.effective_from} to {m.effective_until} only")
    cp = m.counterparties.get(action.recipient or "")
    if cp is None:
        fail.append(f"{action.recipient!r} is not a counterparty of this mandate")
    else:
        if cp.destination is not None and action.destination != cp.destination:
            fail.append(f"destination {action.destination!r} is not the verified "
                        f"one for {cp.name}")
        amt = action.amount_cents
        if amt is not None:
            if cp.max_cents is not None and amt > cp.max_cents:
                fail.append(f"{amt} cents is over {cp.name}'s ceiling of {cp.max_cents}")
            if amt < cp.min_cents:
                fail.append(f"{amt} cents is under {cp.name}'s floor of {cp.min_cents}")
    if m.max_cents is not None and (action.amount_cents or 0) > m.max_cents:
        fail.append(f"{action.amount_cents} cents is over the mandate's ceiling")
    if action.kind in MONEY_KINDS:
        if action.source_account not in m.source_accounts:
            fail.append(f"source account {action.source_account!r} is not permitted")
        if action.rail not in m.rails:
            fail.append(f"rail {action.rail!r} is not permitted")
    if m.categories and action.category not in m.categories:
        fail.append(f"category {action.category!r} is not permitted")
    if action.kind == "send_reply" and action.reply_class not in m.reply_classes:
        fail.append(f"reply class {action.reply_class!r} is not permitted")
    if m.frequency:
        count, per = m.frequency
        n, _ = _executed_under(conn, m.id, _period_start(today, per), action.recipient)
        if n >= count:
            fail.append(f"already {n} for {action.recipient} this {per} (limit {count})")
    if m.daily_aggregate_cents is not None:
        _, total = _executed_under(conn, m.id, today)
        if total + (action.amount_cents or 0) > m.daily_aggregate_cents:
            fail.append(f"today's total would be {total + (action.amount_cents or 0)} "
                        f"cents, over {m.daily_aggregate_cents}")
    return fail


def evaluate(conn: sqlite3.Connection, task: sqlite3.Row, action: Action, *,
             policy: Policy | None = None, now: datetime | None = None) -> Decision:
    """What may happen to this exact action, now. Pure apart from reading the
    approvals already consumed (frequency, daily total, duplicates)."""
    policy = policy or load_policy()
    today = (now or datetime.now(db.TZ)).astimezone(db.TZ).date()
    level = level_of(task, action)
    fp = fingerprint(action)

    missing = action.missing()
    if missing:
        return Decision(REFUSED, level, (f"not an exact action: {action.kind} "
                                         f"without {', '.join(missing)}",),
                        fingerprint=fp)
    stop = REFUSED_BY.get(action.kind)
    if stop and policy.hard_stops.get(stop, True):
        refused = (f"hard stop {stop}: no approval or mandate executes "
                   f"{action.kind} while it stands",)
    else:
        refused = ()
    done = conn.execute(
        "SELECT ref, task_id FROM approvals WHERE fingerprint = ? AND status = 'CONSUMED'",
        (fp,)).fetchone()
    if done:
        refused += (f"already executed under {done['ref']} (task {done['task_id']})",)

    # What a mandate would do, worked out even when refused or D, so the
    # approval Matthew sees can say it.
    mandate_id, would, checks = None, False, ()
    if level == "C":
        covering = [m for m in policy.mandates
                    if action.kind in m.kinds and m.entity == task["entity_id"]]
        if not covering:
            checks = (f"no mandate covers {action.kind} for {task['entity_id']}",)
        for m in covering:
            failures = _mandate_failures(conn, m, task, action, today)
            if not failures:
                mandate_id, would, checks = m.id, True, ()
                break
            if mandate_id is None and not checks:
                checks = tuple(f"{m.id}: {f}" for f in failures)

    if refused:
        return Decision(REFUSED, level, refused, mandate_id, would, fp, checks)
    if level in ("A", "B"):
        return Decision(ALLOW, level, fingerprint=fp)
    if level == "D":
        return Decision(NEEDS_APPROVAL, level, ("level D: Matthew approves the exact action",),
                        fingerprint=fp)
    if not would:
        return Decision(NEEDS_APPROVAL, level, ("outside every mandate",) + checks,
                        fingerprint=fp, checks=checks)
    m = next(m for m in policy.mandates if m.id == mandate_id)
    stop = APPROVAL_ONLY_BY.get(action.kind)
    if stop and policy.hard_stops.get(stop, True):
        return Decision(NEEDS_APPROVAL, level,
                        (f"hard stop {stop}: {m.id} would send it, but only "
                         f"Matthew's approval can",), mandate_id, True, fp)
    if m.mode != LIVE:
        return Decision(NEEDS_APPROVAL, level,
                        (f"{m.id} is in shadow mode: it would have executed this",),
                        mandate_id, True, fp)
    return Decision(ALLOW, level, (f"inside {m.id} ({m.decided_in})",), mandate_id, True, fp)
