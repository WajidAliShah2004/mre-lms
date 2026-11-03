"""Approval objects, and the execution gate the ledger calls (R19, D-067).

The addendum of Sept 28, §3:

    "For any exception, the approval object states task ID, exact action,
     draft or before/after, payee and bank destination, amount, affected
     files, evidence links, anomaly, expiration and approve/revise/deny
     controls. Approval applies only to those exact details and expires if a
     material field changes. Never convert one exception approval into an
     unlimited grant."

How each clause is held:

  * "Those exact details": the approval stores the action and its
    fingerprint, and a trigger makes both unchangeable. At execution the
    action being executed is fingerprinted again; one changed field and the
    approval does not apply (`ApprovalMismatch` names the fields).
  * "Expires": `expires_at` is fixed when it is asked for. Past it, the gate
    refuses and `expire_due` returns the task for revision. Nothing ever
    approves by timing out.
  * "Never an unlimited grant": an approval is consumed by the one execution
    it authorizes. A second execution needs a second approval.
  * Approve / revise / deny are a human's (`ledger.is_human`). Deny cancels the
    task with outcome DENIED; revise sends it back to IN_PROGRESS. Neither is
    ever read as approval.

A mandate execution writes a row here too (basis MANDATE), consumed at once:
one table answers "what was executed, on whose authority", and the daily
totals, frequency limits and duplicate check all read it.

Trial mode: every approval records what the policy engine would have done
(`would_execute`, `policy_verdict`). Approving an action a hard stop forbids —
a payment, today — records Matthew's agreement and authorizes nothing; he
pays it himself and closes the task.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

from ..db import database as db
from . import ledger, policy as pol
from .policy import Action

PENDING, APPROVED, DENIED, REVISE, EXPIRED, SUPERSEDED, CONSUMED = (
    "PENDING", "APPROVED", "DENIED", "REVISE", "EXPIRED", "SUPERSEDED", "CONSUMED")
OPEN = (PENDING, APPROVED)

POLICY_ACTOR = "policy"


class ApprovalError(ledger.LedgerError):
    pass


class ApprovalMismatch(ledger.ExecutionRefused):
    """The action being executed is not the one that was approved."""


def _now(now: datetime | None) -> datetime:
    return (now or datetime.now(db.TZ)).astimezone(db.TZ)


def get(conn: sqlite3.Connection, ref: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM approvals WHERE ref = ?", (ref,)).fetchone()
    if row is None:
        raise ApprovalError(f"no approval {ref}")
    return row


def for_task(conn: sqlite3.Connection, task_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM approvals WHERE task_id = ? ORDER BY id",
                        (task_id,)).fetchall()


def pending(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM approvals WHERE status = 'PENDING' ORDER BY expires_at").fetchall()


def _next_ref(conn: sqlite3.Connection) -> str:
    n = conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM approvals").fetchone()[0]
    return f"AP-{n}"


def _insert(conn, *, ref, task_id, basis, action: Action, decision: pol.Decision,
            requested_by, requested_at, expires_at, status, draft=None,
            before_after=None, evidence_links=(), anomaly=None,
            consumed_at=None, consumed_by=None) -> None:
    conn.execute(
        "INSERT INTO approvals (ref, task_id, basis, mandate_id, kind, action_json, "
        "fingerprint, draft, before_after, recipient, destination, amount_cents, "
        "currency, affected_files, evidence_links, anomaly, policy_verdict, "
        "policy_reasons, would_execute, requested_by, requested_at, expires_at, "
        "status, consumed_at, consumed_by) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (ref, task_id, basis, decision.mandate_id, action.kind,
         json.dumps(action.as_dict(), sort_keys=True), decision.fingerprint,
         draft, before_after, action.recipient, action.destination,
         action.amount_cents, action.currency, json.dumps(list(action.files)),
         json.dumps(list(evidence_links)), anomaly, decision.verdict,
         "; ".join(decision.reasons)[:1000] or None, int(decision.would_execute),
         requested_by, requested_at, expires_at, status, consumed_at, consumed_by))


def _set_status(conn, ref: str, frm: Iterable[str], to: str, **cols) -> None:
    frm = tuple(frm)
    sets = {"status": to, **cols}
    cur = conn.execute(
        f"UPDATE approvals SET {', '.join(f'{k} = ?' for k in sets)} "
        f"WHERE ref = ? AND status IN ({','.join('?' * len(frm))})",
        (*sets.values(), ref, *frm))
    if cur.rowcount != 1:
        raise ApprovalError(f"{ref} is no longer {' or '.join(frm)}")


# ---------------------------------------------------------------------------
# Asking
# ---------------------------------------------------------------------------

def request(conn: sqlite3.Connection, task_id: int, action: Action, *, by: str,
            draft: str | None = None, before_after: str | None = None,
            evidence_links: Iterable[str] = (), anomaly: str | None = None,
            ttl_hours: int | None = None, policy: pol.Policy | None = None,
            now: datetime | None = None) -> str:
    """Put one exact action in front of Matthew. Returns the approval's ref.

    The task must be PREPARED (it moves to AWAITING_APPROVAL), or already
    awaiting approval, in which case the earlier request is superseded: a
    revised draft is a new approval, never an edit of the old one.
    """
    policy = policy or pol.load_policy()
    at = _now(now)
    missing = action.missing()
    if missing:
        raise ApprovalError(f"an approval states the exact action; this "
                            f"{action.kind} has no {', '.join(missing)}")
    with ledger._atomic(conn, "approval_request"):
        task = ledger.get(conn, task_id)
        if task["status"] not in ("PREPARED", "AWAITING_APPROVAL"):
            raise ApprovalError(f"task {task_id} is {task['status']}; approval is "
                                f"asked for a PREPARED task")
        decision = pol.evaluate(conn, task, action, policy=policy, now=at)
        ref = _next_ref(conn)
        for old in conn.execute("SELECT ref FROM approvals WHERE task_id = ? AND "
                                "status IN ('PENDING','APPROVED')", (task_id,)):
            _set_status(conn, old["ref"], OPEN, SUPERSEDED, decided_by=by,
                        decided_at=db.now_iso(), decision_note=f"superseded by {ref}")
            ledger._event(conn, task_id, by, "APPROVAL_SUPERSEDED",
                          detail=f"{old['ref']} by {ref}", approval_ref=old["ref"])
        _insert(conn, ref=ref, task_id=task_id, basis="HUMAN", action=action,
                decision=decision, requested_by=by, requested_at=at.isoformat(),
                expires_at=(at + timedelta(hours=ttl_hours or policy.approval_ttl_hours)
                            ).isoformat(),
                status=PENDING, draft=draft, before_after=before_after,
                evidence_links=evidence_links, anomaly=anomaly)
        note = f"{ref}: {action.kind}"
        if decision.verdict == pol.REFUSED:
            note += " — approval records agreement only: " + "; ".join(decision.reasons)
        elif decision.would_execute:
            note += f" — {decision.mandate_id} would have executed it"
        if task["status"] == "PREPARED":
            ledger.transition(conn, task_id, "AWAITING_APPROVAL", by=by,
                              expect="PREPARED", detail=note, approval_ref=ref,
                              doc_version=action.doc_version,
                              destination=action.destination,
                              recipient=action.recipient,
                              amount_cents=action.amount_cents)
        else:
            ledger._event(conn, task_id, by, "APPROVAL_REQUESTED", detail=note,
                          approval_ref=ref, doc_version=action.doc_version,
                          destination=action.destination, recipient=action.recipient,
                          amount_cents=action.amount_cents)
    return ref


# ---------------------------------------------------------------------------
# Answering
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Answered:
    ref: str
    task_id: int
    status: str
    authorizes: bool        # False when a hard stop means approval is agreement only


def decide(conn: sqlite3.Connection, ref: str, answer: str, *, by: str,
           note: str | None = None, now: datetime | None = None) -> Answered:
    """approve | revise | deny, by a human. Nothing else answers."""
    if not ledger.is_human(by):
        raise ApprovalError(f"{by} is not a human; only Matthew (or the developer "
                            f"on his instruction) answers an approval")
    if answer not in ("approve", "revise", "deny"):
        raise ApprovalError(f"answer is approve, revise or deny, not {answer!r}")
    at = _now(now)
    with ledger._atomic(conn, "approval_decide"):
        a = get(conn, ref)
        if a["status"] != PENDING:
            raise ApprovalError(f"{ref} is {a['status']}, not waiting for an answer")
        if datetime.fromisoformat(a["expires_at"]) <= at:
            raise ApprovalError(f"{ref} expired at {a['expires_at'][:16]}; ask again "
                                f"with the details as they are now")
        tid = int(a["task_id"])
        stamp = dict(decided_by=by, decided_at=at.isoformat(), decision_note=note)
        if answer == "approve":
            _set_status(conn, ref, (PENDING,), APPROVED, **stamp)
            ledger._event(conn, tid, by, "APPROVAL_APPROVED", detail=note,
                          approval_ref=ref)
            return Answered(ref, tid, APPROVED, a["policy_verdict"] != pol.REFUSED)
        status = REVISE if answer == "revise" else DENIED
        _set_status(conn, ref, (PENDING,), status, **stamp)
        ledger._event(conn, tid, by, f"APPROVAL_{status}", detail=note, approval_ref=ref)
        if answer == "revise":
            ledger.transition(conn, tid, "IN_PROGRESS", by=by, expect="AWAITING_APPROVAL",
                              detail=f"{ref} returned for revision"
                                     + (f": {note}" if note else ""), approval_ref=ref)
        else:
            ledger.transition(conn, tid, "CANCELED", by=by, expect="AWAITING_APPROVAL",
                              outcome=ledger.DENIED,
                              detail=f"{ref} denied" + (f": {note}" if note else ""),
                              approval_ref=ref)
        return Answered(ref, tid, status, False)


def expire_due(conn: sqlite3.Connection, now: datetime | None = None) -> list[str]:
    """Expire what is past its time, and hand each task back for revision.

    An approval that was never answered expires; so does one approved but not
    yet executed — the facts it was approved against may be stale.
    """
    at = _now(now)
    expired = []
    with ledger._atomic(conn, "approval_expire"):
        for a in conn.execute("SELECT * FROM approvals WHERE status IN "
                              "('PENDING','APPROVED')").fetchall():
            if datetime.fromisoformat(a["expires_at"]) > at:
                continue
            _set_status(conn, a["ref"], OPEN, EXPIRED, decided_at=at.isoformat(),
                        decided_by=a["decided_by"] or POLICY_ACTOR)
            tid = int(a["task_id"])
            ledger._event(conn, tid, POLICY_ACTOR, "APPROVAL_EXPIRED",
                          detail=f"{a['ref']} expired unexecuted", approval_ref=a["ref"])
            if ledger.get(conn, tid)["status"] == "AWAITING_APPROVAL":
                ledger.transition(conn, tid, "IN_PROGRESS", by=POLICY_ACTOR,
                                  expect="AWAITING_APPROVAL",
                                  detail=f"{a['ref']} expired; prepare it again",
                                  approval_ref=a["ref"])
            expired.append(a["ref"])
    return expired


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

def gate(conn: sqlite3.Connection, task: sqlite3.Row, approval_ref: str | None,
         actor: str, action: Action | None, *, policy: pol.Policy | None = None,
         now: datetime | None = None) -> str | None:
    """`ledger.transition`'s check before EXECUTED. Returns the ref that
    authorized the execution, or raises ExecutionRefused.

    Immediately before execution it rechecks what the addendum lists: the
    source version, recipient and amount (the fingerprint), the authorization
    scope and hard stops (the policy, evaluated now, not when it was asked),
    the current state (ledger's `expect`) and duplicates (a consumed approval
    with the same fingerprint).
    """
    at = _now(now)
    awaiting = task["status"] == "AWAITING_APPROVAL"
    if action is None:
        if not awaiting and not approval_ref and pol.level_of(task, None) in ("A", "B"):
            return None
        raise ledger.ExecutionRefused(
            f"task {task['id']} is level {task['action_risk'] or 'unassessed (D)'}"
            f"{' and awaiting approval' if awaiting else ''}; executing it needs "
            f"the exact action, to compare with what was authorized")

    decision = pol.evaluate(conn, task, action, policy=policy, now=at)
    if decision.verdict == pol.REFUSED:
        raise ledger.ExecutionRefused(f"task {task['id']}: " + "; ".join(decision.reasons))

    if awaiting or approval_ref:
        if not approval_ref:
            raise ledger.ExecutionRefused(
                f"task {task['id']} is awaiting approval; executing it needs the "
                f"approval's reference. A missing approval is not an approval.")
        row = conn.execute("SELECT * FROM approvals WHERE ref = ?",
                           (approval_ref,)).fetchone()
        if row is None or int(row["task_id"]) != int(task["id"]):
            raise ledger.ExecutionRefused(f"{approval_ref} is not an approval of "
                                          f"task {task['id']}")
        if row["status"] != APPROVED:
            raise ledger.ExecutionRefused(f"{approval_ref} is {row['status']}, not approved")
        if datetime.fromisoformat(row["expires_at"]) <= at:
            raise ledger.ExecutionRefused(f"{approval_ref} expired at "
                                          f"{row['expires_at'][:16]}")
        if row["fingerprint"] != decision.fingerprint:
            diff = pol.changed_fields(Action.from_json(row["action_json"]), action)
            raise ApprovalMismatch(
                f"{approval_ref} approved a different action: {', '.join(diff)} "
                f"changed. Approval applies only to the exact details; ask again.")
        _set_status(conn, approval_ref, (APPROVED,), CONSUMED,
                    consumed_at=at.isoformat(), consumed_by=actor)
        return approval_ref

    if decision.verdict == pol.ALLOW:
        if decision.mandate_id is None:          # level A or B
            return None
        ref = _next_ref(conn)
        _insert(conn, ref=ref, task_id=int(task["id"]), basis="MANDATE",
                action=action, decision=decision, requested_by=actor,
                requested_at=at.isoformat(), expires_at=at.isoformat(),
                status=CONSUMED, consumed_at=at.isoformat(), consumed_by=actor)
        return ref

    raise ledger.ExecutionRefused(
        f"task {task['id']} needs Matthew's approval: " + "; ".join(decision.reasons))
