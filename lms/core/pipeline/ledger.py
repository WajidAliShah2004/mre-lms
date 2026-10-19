"""The task ledger, v2 (R13, D-066).

The addendum of Sept 28, §2:

    "Each source event creates or updates one durable task record. The
     coordinator deduplicates events before work starts; a forwarded email,
     attached invoice and bank transaction can be evidence for the same
     obligation rather than three tasks."

This module is that sentence, and the state machine the same section lists.
Closing a task by hand (ops/done.py) is in `tasks.py`, built on this.

THE FIELDS
----------
The addendum's list, and where each one is kept:

    task ID                          tasks.id (stable; never reused or renumbered)
    source and link                  tasks.source / source_link / source_artifact
                                     for the first; task_sources for all of them
    received time                    tasks.received_at
    owner and entity                 tasks.owner, tasks.entity_id
    personal or business             tasks.domain
    subject                          tasks.title
    requested outcome                tasks.requested_outcome
    evidence, extraction confidence  task_sources, tasks.extraction_confidence
    priority and due date            tasks.urgency, tasks.due_date (+ due_date_verified)
    dependencies                     task_dependencies
    assigned agent                   tasks.assigned_agent
    permitted tools                  tasks.permitted_tools (JSON)
    sensitivity                      tasks.sensitivity (spec §5.6: normal|high|private)
    action risk                      tasks.action_risk (approval-matrix level A-D)
    approval policy and approver     tasks.approval_policy, tasks.approver
    status                           tasks.status
    retry count                      tasks.retry_count
    artifact links                   tasks.artifact_links (JSON)
    audit trail                      task_events (append-only)
    verification evidence            tasks.verification_evidence
    final outcome                    tasks.final_outcome

"Every action stores the exact document version, destination, recipient and
amount where applicable": columns of task_events, so the conflict check before
execution can compare them rather than parse prose.

ONE OBLIGATION, ONE KEY
-----------------------
Two keys, because the addendum asks for two different things:

  * `source_key` — one per SOURCE EVENT ("artifact:<sha256>"). Re-reading the
    same message is a no-op. That is "repeated runs must be idempotent".
  * `event_key` — one per OBLIGATION, built from what the obligation is: whose
    (entity), owed to or by whom (counterparty), and which instance (a
    reference number, or the due date and amount). The email and the invoice
    attached to it produce the same key and so the same task.

When the evidence is too thin to say two mentions are the same obligation, the
key is None and they stay two tasks. A duplicate a human can merge in one
command (`done.py 12 --duplicate-of 7`) costs a minute; two obligations merged
into one costs a missed deadline, and nobody notices until it is missed.

The entity is in the key, so two entities' obligations never merge, however
alike they look (addendum §0: entity separation in records).

THE STATES
----------
    DETECTED -> CLASSIFIED -> TRIAGED -> DELEGATED -> IN_PROGRESS -> PREPARED
      -> AWAITING_APPROVAL (if required) -> EXECUTED -> VERIFIED -> CLOSED

    exceptions: NEEDS_CLARIFICATION, BLOCKED, FAILED, DUPLICATE, CANCELED

`TRANSITIONS` is the whole of what the system may do. A human (Matthew, or the
developer at done.py) may also close or cancel any open task and reopen a
closed one: it is his list, and he is the one person whose "it's done" is
evidence. Nothing else skips a step.

Rules the addendum states, enforced here rather than hoped for:

  * "A denial returns the task for revision or closes it; it never becomes an
    implicit approval": AWAITING_APPROVAL -> IN_PROGRESS or CANCELED.
  * Execution needs an approval reference unless the action is level A or B.
    Level C needs a mandate, and mandates are R19; until they exist C goes
    through AWAITING_APPROVAL like D. `default_gate` is that rule, and the
    hook R19's policy engine replaces.
  * "Independently verified": VERIFIED needs evidence, and an actor other than
    the one that executed.
  * "Conflict check before execution": every move names the state it expects
    to move from, and the UPDATE only lands if the row is still in it.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

from ..db import database as db

# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------

LIFECYCLE = ("DETECTED", "CLASSIFIED", "TRIAGED", "DELEGATED", "IN_PROGRESS",
             "PREPARED", "AWAITING_APPROVAL", "EXECUTED", "VERIFIED", "CLOSED")
EXCEPTIONS = ("NEEDS_CLARIFICATION", "BLOCKED", "FAILED", "DUPLICATE", "CANCELED")
STATES = LIFECYCLE + EXCEPTIONS

TERMINAL = frozenset({"CLOSED", "CANCELED", "DUPLICATE"})
# What the brief, done.py --list and any future dashboard mean by "open".
ACTIVE = tuple(s for s in STATES if s not in TERMINAL)

# Paused, and resumed to wherever they were paused from.
PAUSES = frozenset({"NEEDS_CLARIFICATION", "BLOCKED"})

# What a task may be created in. Ingest has already classified and ranked what
# it records, so it creates at TRIAGED; a source that only noticed something
# creates at DETECTED.
INITIAL = frozenset({"DETECTED", "CLASSIFIED", "TRIAGED", "NEEDS_CLARIFICATION"})

_WORKING = ("DELEGATED", "IN_PROGRESS", "PREPARED", "AWAITING_APPROVAL", "EXECUTED")
_PAUSABLE = ("DETECTED", "CLASSIFIED", "TRIAGED") + _WORKING + ("FAILED",)

TRANSITIONS: dict[str, frozenset[str]] = {
    "DETECTED":          frozenset({"CLASSIFIED"}),
    "CLASSIFIED":        frozenset({"TRIAGED"}),
    "TRIAGED":           frozenset({"DELEGATED"}),
    "DELEGATED":         frozenset({"IN_PROGRESS"}),
    "IN_PROGRESS":       frozenset({"PREPARED"}),
    "PREPARED":          frozenset({"AWAITING_APPROVAL", "EXECUTED"}),
    # approved / denied-revise / denied-close
    "AWAITING_APPROVAL": frozenset({"EXECUTED", "IN_PROGRESS", "CANCELED"}),
    "EXECUTED":          frozenset({"VERIFIED"}),
    "VERIFIED":          frozenset({"CLOSED"}),
    # retry (counted) / give up
    "FAILED":            frozenset({"IN_PROGRESS", "CANCELED"}),
    "NEEDS_CLARIFICATION": frozenset({"CANCELED"}),    # + resume, see allowed()
    "BLOCKED":           frozenset({"CANCELED"}),      # + resume, see allowed()
    "CLOSED":            frozenset(),
    "CANCELED":          frozenset(),
    "DUPLICATE":         frozenset(),
}
for _s in _PAUSABLE:
    TRANSITIONS[_s] = TRANSITIONS[_s] | PAUSES
for _s in _WORKING:
    TRANSITIONS[_s] = TRANSITIONS[_s] | {"FAILED"}
del _s

# Who counts as a human. Their word closes a task; nobody else's does.
HUMANS = frozenset({"human", "matthew", "developer"})
REOPEN_TO = "TRIAGED"

URGENCY_ORDER = ("NONE", "LOW", "NORMAL", "HIGH", "CRITICAL")
OWNER_MATTHEW = "P_MRE"           # entities.yaml id; the default owner of his to-do list

# Final outcomes. The status says the task ended; this says how.
DONE, DISMISSED, DENIED, CANCELED, DUPLICATE = (
    "DONE", "DISMISSED", "DENIED", "CANCELED", "DUPLICATE")


class LedgerError(RuntimeError):
    pass


class IllegalTransition(LedgerError):
    pass


class TaskConflict(LedgerError):
    """The task moved since the caller looked at it. Re-read; do not retry blind."""


def is_human(actor: str) -> bool:
    return actor.lower() in HUMANS or actor.lower().startswith("human:")


# ---------------------------------------------------------------------------
# Event key
# ---------------------------------------------------------------------------

def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")


def _iso_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        return None


def event_key(*, entity_id: str | None, counterparty: str | None,
              due_date: str | None = None, amount_cents: int | None = None,
              reference: str | None = None,
              descriptor: str | None = None) -> str | None:
    """The obligation's identity, or None when there is not enough to know it.

    In order of strength:
      1. a reference number (invoice, case, policy): entity + counterparty + ref
      2. a due date and an amount: entity + counterparty + date + amount
      3. a due date alone: the same, plus what the document is, because one
         counterparty can want two different things on the same day

    No entity, no counterparty, or nothing that picks out one instance (a
    monthly bill without a date or reference) -> None, and the task is not
    merged with anything. Readable on purpose: `done.py --show` prints it.
    """
    ent, who = str(entity_id or "").strip().upper(), _norm(counterparty)
    if not ent or not who or ent in ("UNASSIGNED", "JUNK"):
        return None
    ref = _norm(reference)
    if ref:
        return f"v1|{ent}|{who}|ref={ref}"
    due = _iso_date(due_date)
    if not due:
        return None
    if amount_cents:
        return f"v1|{ent}|{who}|due={due}|amt={int(amount_cents)}"
    return f"v1|{ent}|{who}|due={due}|what={_norm(descriptor)}"


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def get(conn: sqlite3.Connection, task_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise LedgerError(f"no task {task_id}. `ops/done.py --list` shows the ids.")
    return row


def active_sql(alias: str = "") -> str:
    """`status IN (...)` for the open states, for callers writing their own query."""
    col = f"{alias}.status" if alias else "status"
    return f"{col} IN ({', '.join(repr(s) for s in ACTIVE)})"


def sources(conn: sqlite3.Connection, task_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM task_sources WHERE task_id = ? ORDER BY id",
                        (task_id,)).fetchall()


def history(conn: sqlite3.Connection, task_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM task_events WHERE task_id = ? ORDER BY id",
                        (task_id,)).fetchall()


def dependencies(conn: sqlite3.Connection, task_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT t.* FROM task_dependencies d JOIN tasks t ON t.id = d.depends_on "
        "WHERE d.task_id = ? ORDER BY t.id", (task_id,)).fetchall()


def _event(conn: sqlite3.Connection, task_id: int, actor: str, event: str, *,
           from_status: str | None = None, to_status: str | None = None,
           detail: str | None = None, doc_version: str | None = None,
           destination: str | None = None, recipient: str | None = None,
           amount_cents: int | None = None, approval_ref: str | None = None) -> None:
    conn.execute(
        "INSERT INTO task_events (task_id, ts, actor, event, from_status, "
        "to_status, detail, doc_version, destination, recipient, amount_cents, "
        "approval_ref) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (task_id, db.now_iso(), actor, event, from_status, to_status,
         detail[:400] if detail else None, doc_version, destination, recipient,
         amount_cents, approval_ref))


class _atomic:
    """A savepoint: works inside or outside a caller's transaction."""

    def __init__(self, conn: sqlite3.Connection, name: str):
        self.conn, self.name = conn, name

    def __enter__(self):
        self.conn.execute(f"SAVEPOINT {self.name}")

    def __exit__(self, exc_type, *_):
        if exc_type is not None:
            self.conn.execute(f"ROLLBACK TO {self.name}")
        self.conn.execute(f"RELEASE {self.name}")
        return False


# ---------------------------------------------------------------------------
# Recording source events
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Recorded:
    task_id: int
    # created: a new task · merged: evidence for an existing one ·
    # seen: this exact source event was already recorded (a re-run)
    outcome: str
    # Evidence arrived for a task that had already ended — e.g. a bill that was
    # paid, received again (addendum demonstration scenario 4). Recorded, not
    # re-opened: whether it is a second bill or a resend is a human question.
    after_close: bool = False


_TASK_FIELDS = frozenset({
    "entity_id", "domain", "person", "owner", "detail", "requested_outcome",
    "urgency", "due_date", "due_date_verified", "assigned_agent",
    "permitted_tools", "sensitivity", "action_risk", "approval_policy",
    "approver", "artifact_links"})
_JSON_FIELDS = frozenset({"permitted_tools", "artifact_links"})


def _encode(field: str, value: Any) -> Any:
    if field in _JSON_FIELDS and not isinstance(value, str):
        return json.dumps(list(value))
    return value


def _higher(a: str | None, b: str | None) -> str:
    def rank(u):
        u = (u or "NORMAL").upper()
        return URGENCY_ORDER.index(u) if u in URGENCY_ORDER else 2
    return (a or "NORMAL") if rank(a) >= rank(b) else (b or "NORMAL")


def record_event(conn: sqlite3.Connection, *, source_key: str, source: str,
                 title: str, event_key: str | None = None,
                 source_link: str | None = None, artifact_id: int | None = None,
                 received_at: str | None = None, confidence: float | None = None,
                 status: str = "DETECTED", by: str = "ingest",
                 **fields: Any) -> Recorded:
    """One source event in. Creates a task, adds evidence to one, or does nothing.

    Never opens, moves or edits the source: it stores the link (R8).
    """
    unknown = set(fields) - _TASK_FIELDS
    if unknown:
        raise LedgerError(f"not task fields: {sorted(unknown)}")
    if status not in INITIAL:
        raise LedgerError(f"a task cannot start in {status}; one of {sorted(INITIAL)}")

    try:
        return _record(conn, source_key, source, title, event_key, source_link,
                       artifact_id, received_at, confidence, status, by, fields)
    except sqlite3.IntegrityError:
        # Another process recorded the same source event or obligation between
        # our read and our write. Its row is there now; this pass finds it.
        return _record(conn, source_key, source, title, event_key, source_link,
                       artifact_id, received_at, confidence, status, by, fields)


def _record(conn, source_key, source, title, event_key, source_link,
            artifact_id, received_at, confidence, status, by, fields) -> Recorded:
    seen = conn.execute("SELECT task_id FROM task_sources WHERE source_key = ?",
                        (source_key,)).fetchone()
    if seen:
        return Recorded(task_id=int(seen["task_id"]), outcome="seen")

    with _atomic(conn, "record_event"):
        existing = None
        if event_key:
            existing = conn.execute("SELECT * FROM tasks WHERE event_key = ?",
                                    (event_key,)).fetchone()

        if existing is None:
            row = {
                "event_key": event_key, "source": source, "source_link": source_link,
                "source_artifact": artifact_id,
                "received_at": received_at or db.now_iso(),
                "title": title, "extraction_confidence": confidence,
                "status": status, "status_changed_at": db.now_iso(),
                "created_at": db.now_iso(),
                **{k: _encode(k, v) for k, v in fields.items()},
            }
            row.setdefault("owner", OWNER_MATTHEW)
            cols = ", ".join(row)
            marks = ", ".join("?" for _ in row)
            task_id = int(conn.execute(f"INSERT INTO tasks ({cols}) VALUES ({marks})",
                                       tuple(row.values())).lastrowid)
            _add_source(conn, task_id, source_key, source, source_link,
                        artifact_id, received_at, confidence)
            _event(conn, task_id, by, "CREATED", to_status=status,
                   detail=f"{source_key}" + (f" · key {event_key}" if event_key else ""))
            db.log_action(conn, "TASK_CREATED", artifact_id=artifact_id,
                          detail=title[:400])
            return Recorded(task_id=task_id, outcome="created")

        task_id = int(existing["id"])
        _add_source(conn, task_id, source_key, source, source_link,
                    artifact_id, received_at, confidence)
        after_close = existing["status"] in TERMINAL
        new_urgency = _higher(existing["urgency"], fields.get("urgency"))
        if new_urgency != existing["urgency"]:
            conn.execute("UPDATE tasks SET urgency = ? WHERE id = ?",
                         (new_urgency, task_id))
            _event(conn, task_id, by, "FIELD",
                   detail=f"urgency {existing['urgency']} -> {new_urgency} "
                          f"(raised by {source_key})")
        _event(conn, task_id, by, "EVIDENCE",
               detail=f"{source_key}" + (" · task had already ended" if after_close else ""))
        db.log_action(conn, "TASK_EVIDENCE_ADDED", artifact_id=artifact_id,
                      detail=f"#{task_id} {existing['title']}"
                             + (" (after close)" if after_close else ""))
        return Recorded(task_id=task_id, outcome="merged", after_close=after_close)


def _add_source(conn, task_id, source_key, source, source_link, artifact_id,
                received_at, confidence) -> None:
    conn.execute(
        "INSERT INTO task_sources (task_id, source_key, source, source_link, "
        "artifact_id, received_at, confidence, seen_at) VALUES (?,?,?,?,?,?,?,?)",
        (task_id, source_key, source, source_link, artifact_id, received_at,
         confidence, db.now_iso()))


# ---------------------------------------------------------------------------
# Moving a task
# ---------------------------------------------------------------------------

# The execution gate: (conn, task row, approval_ref, actor) -> None, raising
# IllegalTransition to refuse. R19's policy engine replaces `default_gate`; it
# must stay code, outside the model (addendum §3).
ExecutionGate = Callable[[sqlite3.Connection, sqlite3.Row, "str | None", str], None]


def default_gate(conn: sqlite3.Connection, task: sqlite3.Row,
                 approval_ref: str | None, actor: str) -> None:
    """Until mandates exist, only levels A and B execute without an approval."""
    if task["status"] == "AWAITING_APPROVAL":
        if not approval_ref:
            raise IllegalTransition(
                f"task {task['id']} is awaiting approval; executing it needs the "
                f"approval's reference. A missing approval is not an approval.")
        return
    if (task["action_risk"] or "D") not in ("A", "B"):
        raise IllegalTransition(
            f"task {task['id']} is level {task['action_risk'] or 'unassessed'}; "
            f"it goes through AWAITING_APPROVAL. Level C needs a mandate (R19), "
            f"and none exists yet.")


def _paused_from(conn: sqlite3.Connection, task_id: int, status: str) -> str | None:
    row = conn.execute(
        "SELECT from_status FROM task_events WHERE task_id = ? AND to_status = ? "
        "AND event = 'TRANSITION' ORDER BY id DESC LIMIT 1",
        (task_id, status)).fetchone()
    return row["from_status"] if row else None


def allowed(conn: sqlite3.Connection, task: sqlite3.Row, actor: str) -> frozenset[str]:
    """Every state this actor may move this task to, from where it is now."""
    current = task["status"]
    targets = set(TRANSITIONS[current])
    if current in PAUSES:
        # Back to where it was paused from. A task created already paused has
        # no "from"; once the question is answered it has been triaged.
        targets.add(_paused_from(conn, int(task["id"]), current) or REOPEN_TO)
    if is_human(actor):
        if current in TERMINAL:
            targets.add(REOPEN_TO)
        else:
            targets |= {"CLOSED", "CANCELED"}
    return frozenset(targets)


@dataclass(frozen=True)
class Moved:
    task_id: int
    from_status: str
    to_status: str


def transition(conn: sqlite3.Connection, task_id: int, to: str, *, by: str,
               expect: str | None = None, detail: str | None = None,
               evidence: str | None = None, outcome: str | None = None,
               approval_ref: str | None = None, doc_version: str | None = None,
               destination: str | None = None, recipient: str | None = None,
               amount_cents: int | None = None,
               gate: ExecutionGate = default_gate) -> Moved:
    """Move one task one step. Refuses anything not in the table.

    `expect` is the state the caller last saw. If the task has moved since,
    this raises TaskConflict instead of acting on a stale view — the addendum's
    "conflict check before execution", applied to every move.
    """
    if to not in STATES:
        raise IllegalTransition(f"{to!r} is not a state")
    if to == "DUPLICATE":
        raise IllegalTransition("use mark_duplicate(): a duplicate must say of what")

    with _atomic(conn, "transition"):
        task = get(conn, task_id)
        current = task["status"]
        if expect is not None and expect != current:
            raise TaskConflict(f"task {task_id} is {current}, not {expect}. "
                               f"Re-read it before acting.")
        if to not in allowed(conn, task, by):
            raise IllegalTransition(f"task {task_id}: {current} -> {to} is not "
                                    f"allowed for {by}")

        sets: dict[str, Any] = {"status": to, "status_changed_at": db.now_iso()}

        if to == "EXECUTED":
            gate(conn, task, approval_ref, by)
            waiting = [d["id"] for d in dependencies(conn, task_id)
                       if d["status"] not in ("VERIFIED", "CLOSED")]
            if waiting:
                raise IllegalTransition(f"task {task_id} depends on unfinished "
                                        f"tasks {waiting}")
        if to == "VERIFIED":
            if not evidence:
                raise IllegalTransition(
                    "verification needs evidence from the destination. A tool "
                    "reporting success is not verification.")
            executed_by = conn.execute(
                "SELECT actor FROM task_events WHERE task_id = ? AND "
                "to_status = 'EXECUTED' ORDER BY id DESC LIMIT 1",
                (task_id,)).fetchone()
            if executed_by and executed_by["actor"] == by and not is_human(by):
                raise IllegalTransition(f"{by} executed task {task_id}; someone "
                                        f"else has to verify it")
        if to == "CLOSED" and not (evidence or task["verification_evidence"]):
            raise IllegalTransition(f"task {task_id} has no verification evidence")
        if evidence:
            sets["verification_evidence"] = evidence[:1000]
        if current == "FAILED" and to == "IN_PROGRESS":
            sets["retry_count"] = int(task["retry_count"]) + 1

        if to in TERMINAL:
            sets["completed_at"] = db.now_iso()
            sets["final_outcome"] = outcome or (DONE if to == "CLOSED" else CANCELED)
        elif current in TERMINAL:        # a human reopening it
            sets.update(completed_at=None, final_outcome=None,
                        verification_evidence=None, duplicate_of=None)

        assignments = ", ".join(f"{k} = ?" for k in sets)
        cur = conn.execute(
            f"UPDATE tasks SET {assignments} WHERE id = ? AND status = ?",
            (*sets.values(), task_id, current))
        if cur.rowcount != 1:
            raise TaskConflict(f"task {task_id} changed while moving it to {to}")

        _event(conn, task_id, by, "TRANSITION", from_status=current, to_status=to,
               detail=detail or evidence, doc_version=doc_version,
               destination=destination, recipient=recipient,
               amount_cents=amount_cents, approval_ref=approval_ref)
    return Moved(task_id=task_id, from_status=current, to_status=to)


def mark_duplicate(conn: sqlite3.Connection, task_id: int, of: int, *,
                   by: str) -> Moved:
    """Fold task `task_id` into task `of`. Its evidence moves; nothing is lost.

    Refused across entities: "merge duplicate mentions; preserve all evidence
    and entity boundaries" (addendum §6).
    """
    with _atomic(conn, "mark_duplicate"):
        dup, canon = get(conn, task_id), get(conn, of)
        while canon["status"] == "DUPLICATE":            # follow to the survivor
            canon = get(conn, int(canon["duplicate_of"]))
        if int(canon["id"]) == task_id:
            raise IllegalTransition(f"task {task_id} cannot duplicate itself")
        if dup["status"] in TERMINAL:
            raise IllegalTransition(f"task {task_id} has already ended "
                                    f"({dup['status']}); reopen it first")
        if dup["entity_id"] and canon["entity_id"] and \
                dup["entity_id"] != canon["entity_id"]:
            raise IllegalTransition(
                f"task {task_id} is {dup['entity_id']} and task {canon['id']} is "
                f"{canon['entity_id']}; tasks of different entities never merge")

        canon_id = int(canon["id"])
        conn.execute("UPDATE task_sources SET task_id = ? WHERE task_id = ?",
                     (canon_id, task_id))
        conn.execute(
            "UPDATE tasks SET status = 'DUPLICATE', duplicate_of = ?, event_key = NULL, "
            "final_outcome = 'DUPLICATE', completed_at = ?, status_changed_at = ? "
            "WHERE id = ? AND status = ?",
            (canon_id, db.now_iso(), db.now_iso(), task_id, dup["status"]))
        updates = {"urgency": _higher(canon["urgency"], dup["urgency"])}
        if not canon["event_key"] and dup["event_key"]:
            updates["event_key"] = dup["event_key"]
        conn.execute(f"UPDATE tasks SET {', '.join(f'{k} = ?' for k in updates)} "
                     f"WHERE id = ?", (*updates.values(), canon_id))

        _event(conn, task_id, by, "TRANSITION", from_status=dup["status"],
               to_status="DUPLICATE", detail=f"duplicate of #{canon_id}")
        _event(conn, canon_id, by, "MERGED",
               detail=f"#{task_id} folded in: {dup['title']}")
        db.log_action(conn, "TASK_DUPLICATE", artifact_id=dup["source_artifact"],
                      detail=f"[{by}] #{task_id} -> #{canon_id}: {dup['title']}"[:400])
    return Moved(task_id=task_id, from_status=dup["status"], to_status="DUPLICATE")


# ---------------------------------------------------------------------------
# Fields and dependencies
# ---------------------------------------------------------------------------

def update_fields(conn: sqlite3.Connection, task_id: int, *, by: str,
                  **fields: Any) -> None:
    """Change task fields, each change in the audit trail with old and new."""
    unknown = set(fields) - _TASK_FIELDS
    if unknown:
        raise LedgerError(f"not editable task fields: {sorted(unknown)}")
    with _atomic(conn, "update_fields"):
        task = get(conn, task_id)
        for field, value in fields.items():
            new = _encode(field, value)
            if task[field] == new:
                continue
            conn.execute(f"UPDATE tasks SET {field} = ? WHERE id = ?", (new, task_id))
            _event(conn, task_id, by, "FIELD",
                   detail=f"{field}: {task[field]!r} -> {new!r}")


def add_artifact_link(conn: sqlite3.Connection, task_id: int, link: str, *,
                      by: str) -> None:
    links = json.loads(get(conn, task_id)["artifact_links"] or "[]")
    if link not in links:
        update_fields(conn, task_id, by=by, artifact_links=links + [link])


def add_dependency(conn: sqlite3.Connection, task_id: int, depends_on: int, *,
                   by: str) -> None:
    get(conn, task_id), get(conn, depends_on)
    if task_id == depends_on or task_id in _upstream(conn, depends_on):
        raise LedgerError(f"task {task_id} -> {depends_on} would make a cycle")
    with _atomic(conn, "add_dependency"):
        cur = conn.execute(
            "INSERT OR IGNORE INTO task_dependencies (task_id, depends_on, created_at) "
            "VALUES (?, ?, ?)", (task_id, depends_on, db.now_iso()))
        if cur.rowcount:
            _event(conn, task_id, by, "DEPENDENCY", detail=f"waits on #{depends_on}")


def _upstream(conn: sqlite3.Connection, task_id: int) -> set[int]:
    """Every task `task_id` waits on, transitively."""
    seen: set[int] = set()
    todo = [task_id]
    while todo:
        for (dep,) in conn.execute(
                "SELECT depends_on FROM task_dependencies WHERE task_id = ?",
                (todo.pop(),)).fetchall():
            if dep not in seen:
                seen.add(dep)
                todo.append(dep)
    return seen


def counts_by_status(conn: sqlite3.Connection) -> dict[str, int]:
    return {r["status"]: r["n"] for r in conn.execute(
        "SELECT status, COUNT(*) AS n FROM tasks GROUP BY status")}


