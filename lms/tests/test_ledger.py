"""Task ledger v2 (R13, D-066): the state machine, the event key, one task per
obligation across sources.

The addendum's §2 is the specification. Each test names the sentence it holds
the code to.
"""

import sqlite3

import pytest

from core.db import database as db
from core.pipeline import ledger
from core.reports import brief as reports


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "lms.db")
    yield c
    c.close()


def a_task(conn, status="TRIAGED", **fields):
    fields.setdefault("title", "Acme invoice 7010")
    return db.insert_task(conn, status=status, **fields)


def record(conn, source_key, *, key="v1|B_MRE|acme|due=2026-10-06|amt=12500",
           **kw):
    kw.setdefault("title", "Invoice — Acme")
    kw.setdefault("source", "email")
    kw.setdefault("status", "TRIAGED")
    return ledger.record_event(conn, source_key=source_key, event_key=key, **kw)


def walk(conn, tid, *states, by="coordinator", **kw):
    for s in states:
        ledger.transition(conn, tid, s, by=by, **kw)


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------

def test_the_addendum_fields_all_have_a_column(conn):
    """§2's "required task fields", each one somewhere a query can reach."""
    task_cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
    required = {
        "id", "source", "source_link", "received_at", "owner", "entity_id",
        "domain", "title", "requested_outcome", "extraction_confidence",
        "urgency", "due_date", "assigned_agent", "permitted_tools",
        "sensitivity", "action_risk", "approval_policy", "approver", "status",
        "retry_count", "artifact_links", "verification_evidence",
        "final_outcome", "event_key"}
    assert required <= task_cols
    # evidence, dependencies and the audit trail are tables of their own
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"task_sources", "task_dependencies", "task_events"} <= tables
    event_cols = {r[1] for r in conn.execute("PRAGMA table_info(task_events)")}
    # "Every action stores the exact document version, destination, recipient
    # and amount where applicable."
    assert {"doc_version", "destination", "recipient", "amount_cents"} <= event_cols


def test_the_states_are_the_addendums_ten_and_five():
    assert ledger.LIFECYCLE == (
        "DETECTED", "CLASSIFIED", "TRIAGED", "DELEGATED", "IN_PROGRESS",
        "PREPARED", "AWAITING_APPROVAL", "EXECUTED", "VERIFIED", "CLOSED")
    assert set(ledger.EXCEPTIONS) == {
        "NEEDS_CLARIFICATION", "BLOCKED", "FAILED", "DUPLICATE", "CANCELED"}


def test_the_schema_refuses_a_state_that_does_not_exist(conn):
    with pytest.raises(sqlite3.IntegrityError):
        a_task(conn, status="OPEN")


def test_the_audit_trail_is_append_only(conn):
    tid = a_task(conn)
    ledger.transition(conn, tid, "DELEGATED", by="coordinator")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE task_events SET actor = 'someone else'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM task_events")


# ---------------------------------------------------------------------------
# The state machine: legal moves
# ---------------------------------------------------------------------------

def test_the_whole_happy_path(conn):
    """detected → classified → triaged → delegated → in progress → prepared →
    awaiting approval → executed → independently verified → closed."""
    tid = a_task(conn, status="DETECTED")
    walk(conn, tid, "CLASSIFIED", "TRIAGED", "DELEGATED", "IN_PROGRESS",
         "PREPARED", "AWAITING_APPROVAL")
    ledger.transition(conn, tid, "EXECUTED", by="finance", approval_ref="appr-1",
                      doc_version="ab" * 32, recipient="Acme Corp",
                      amount_cents=12500)
    ledger.transition(conn, tid, "VERIFIED", by="reviewer",
                      evidence="bank posting 2026-10-07 ref 99812")
    ledger.transition(conn, tid, "CLOSED", by="coordinator")

    row = ledger.get(conn, tid)
    assert row["status"] == "CLOSED"
    assert row["final_outcome"] == "DONE"
    assert row["completed_at"]
    moves = [(e["from_status"], e["to_status"]) for e in ledger.history(conn, tid)]
    assert len(moves) == 9
    executed = [e for e in ledger.history(conn, tid) if e["to_status"] == "EXECUTED"][0]
    assert (executed["recipient"], executed["amount_cents"]) == ("Acme Corp", 12500)
    assert executed["approval_ref"] == "appr-1"


def test_level_a_and_b_execute_without_approval(conn):
    tid = a_task(conn, status="PREPARED", action_risk="B")
    ledger.transition(conn, tid, "EXECUTED", by="documents")
    assert ledger.get(conn, tid)["status"] == "EXECUTED"


def test_a_denial_returns_the_task_for_revision(conn):
    """"A denial returns the task for revision or closes it." """
    tid = a_task(conn, status="AWAITING_APPROVAL")
    ledger.transition(conn, tid, "IN_PROGRESS", by="coordinator", detail="denied: wrong payee")
    assert ledger.get(conn, tid)["status"] == "IN_PROGRESS"


def test_a_denial_can_close_it(conn):
    tid = a_task(conn, status="AWAITING_APPROVAL")
    ledger.transition(conn, tid, "CANCELED", by="coordinator", outcome=ledger.DENIED)
    row = ledger.get(conn, tid)
    assert (row["status"], row["final_outcome"]) == ("CANCELED", "DENIED")


def test_a_retry_is_counted(conn):
    tid = a_task(conn, status="IN_PROGRESS")
    ledger.transition(conn, tid, "FAILED", by="finance")
    ledger.transition(conn, tid, "IN_PROGRESS", by="finance")
    ledger.transition(conn, tid, "FAILED", by="finance")
    ledger.transition(conn, tid, "IN_PROGRESS", by="finance")
    assert ledger.get(conn, tid)["retry_count"] == 2


def test_a_paused_task_resumes_where_it_was(conn):
    tid = a_task(conn, status="PREPARED")
    ledger.transition(conn, tid, "NEEDS_CLARIFICATION", by="coordinator",
                      detail="which entity pays this?")
    assert ledger.allowed(conn, ledger.get(conn, tid), "coordinator") >= {"PREPARED"}
    with pytest.raises(ledger.IllegalTransition):
        ledger.transition(conn, tid, "EXECUTED", by="coordinator")
    ledger.transition(conn, tid, "PREPARED", by="coordinator")
    assert ledger.get(conn, tid)["status"] == "PREPARED"


def test_blocked_resumes_where_it_was_too(conn):
    tid = a_task(conn, status="DELEGATED")
    ledger.transition(conn, tid, "BLOCKED", by="coordinator")
    with pytest.raises(ledger.IllegalTransition):
        ledger.transition(conn, tid, "TRIAGED", by="coordinator")
    ledger.transition(conn, tid, "DELEGATED", by="coordinator")


# ---------------------------------------------------------------------------
# The state machine: illegal moves
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("start,to", [
    ("DETECTED", "TRIAGED"),               # skips classification
    ("TRIAGED", "IN_PROGRESS"),            # skips delegation
    ("DELEGATED", "EXECUTED"),             # skips preparation
    ("IN_PROGRESS", "EXECUTED"),
    ("EXECUTED", "CLOSED"),                # skips independent verification
    ("PREPARED", "VERIFIED"),
    ("CLOSED", "TRIAGED"),                 # only a human reopens
    ("CANCELED", "IN_PROGRESS"),
    ("VERIFIED", "BLOCKED"),
])
def test_the_system_cannot_skip_a_step(conn, start, to):
    tid = a_task(conn, status=start)
    with pytest.raises(ledger.IllegalTransition):
        ledger.transition(conn, tid, to, by="coordinator", evidence="x",
                          approval_ref="y")
    assert ledger.get(conn, tid)["status"] == start, "a refused move changed the row"
    assert ledger.history(conn, tid) == []


def test_every_pair_outside_the_table_is_refused(conn):
    """Exhaustive: 15 × 15 moves for a non-human actor, against the table."""
    for start in ledger.STATES:
        if start == "DUPLICATE":
            continue                       # needs duplicate_of; covered below
        for to in ledger.STATES:
            tid = a_task(conn, status=start, action_risk="A")
            legal = to in ledger.allowed(conn, ledger.get(conn, tid), "agent")
            try:
                ledger.transition(conn, tid, to, by="agent", evidence="e",
                                  approval_ref="a")
                moved = True
            except ledger.IllegalTransition:
                moved = False
            assert moved == legal, f"{start} -> {to}: moved={moved}, table says {legal}"


def test_a_missing_approval_is_not_an_approval(conn):
    """"An unavailable approval channel must not silently authorize it." """
    tid = a_task(conn, status="AWAITING_APPROVAL")
    with pytest.raises(ledger.IllegalTransition, match="approval"):
        ledger.transition(conn, tid, "EXECUTED", by="finance")


@pytest.mark.parametrize("risk", ["C", "D", None])
def test_level_c_d_and_unassessed_cannot_skip_approval(conn, risk):
    """C needs a mandate (R19) and there are none yet; unassessed counts as D."""
    tid = a_task(conn, status="PREPARED", action_risk=risk)
    with pytest.raises(ledger.IllegalTransition):
        ledger.transition(conn, tid, "EXECUTED", by="finance")


def test_the_execution_gate_is_replaceable(conn):
    """The hook R19's policy engine plugs into."""
    tid = a_task(conn, status="PREPARED", action_risk="C")
    calls = []

    def mandate_gate(conn, task, approval_ref, actor):
        calls.append((task["id"], actor))

    ledger.transition(conn, tid, "EXECUTED", by="finance", gate=mandate_gate)
    assert calls == [(tid, "finance")]


def test_verification_needs_evidence(conn):
    """"A tool reporting success alone is insufficient." """
    tid = a_task(conn, status="EXECUTED")
    with pytest.raises(ledger.IllegalTransition, match="evidence"):
        ledger.transition(conn, tid, "VERIFIED", by="reviewer")


def test_the_executor_cannot_verify_its_own_work(conn):
    tid = a_task(conn, status="PREPARED", action_risk="A")
    ledger.transition(conn, tid, "EXECUTED", by="communications")
    with pytest.raises(ledger.IllegalTransition, match="someone else"):
        ledger.transition(conn, tid, "VERIFIED", by="communications",
                          evidence="it said it sent")
    ledger.transition(conn, tid, "VERIFIED", by="reviewer",
                      evidence="found in Sent, To: acme@example.com")


def test_execution_waits_for_dependencies(conn):
    first = a_task(conn, status="TRIAGED", title="get the W-9")
    then = a_task(conn, status="PREPARED", action_risk="A", title="pay the vendor")
    ledger.add_dependency(conn, then, first, by="coordinator")
    with pytest.raises(ledger.IllegalTransition, match="depends on"):
        ledger.transition(conn, then, "EXECUTED", by="finance")


def test_dependencies_cannot_loop(conn):
    a, b = a_task(conn), a_task(conn)
    ledger.add_dependency(conn, a, b, by="coordinator")
    with pytest.raises(ledger.LedgerError, match="cycle"):
        ledger.add_dependency(conn, b, a, by="coordinator")


def test_a_stale_view_is_a_conflict_not_an_action(conn):
    """"Conflict check before execution": the caller says what it last saw."""
    tid = a_task(conn, status="AWAITING_APPROVAL")
    ledger.transition(conn, tid, "IN_PROGRESS", by="coordinator")    # denied meanwhile
    with pytest.raises(ledger.TaskConflict):
        ledger.transition(conn, tid, "EXECUTED", by="finance",
                          expect="AWAITING_APPROVAL", approval_ref="appr-1")


def test_duplicate_is_only_reached_by_saying_of_what(conn):
    tid = a_task(conn)
    with pytest.raises(ledger.IllegalTransition, match="mark_duplicate"):
        ledger.transition(conn, tid, "DUPLICATE", by="human")


def test_a_human_closes_with_evidence_and_reopens(conn):
    tid = a_task(conn, status="DELEGATED")
    with pytest.raises(ledger.IllegalTransition, match="evidence"):
        ledger.transition(conn, tid, "CLOSED", by="human")
    ledger.transition(conn, tid, "CLOSED", by="human", evidence="attested by human")
    ledger.transition(conn, tid, "TRIAGED", by="human")
    row = ledger.get(conn, tid)
    assert (row["status"], row["final_outcome"], row["completed_at"]) == (
        "TRIAGED", None, None)


# ---------------------------------------------------------------------------
# The event key
# ---------------------------------------------------------------------------

def test_the_same_obligation_gets_the_same_key_from_any_source():
    """The email says "Acme Corp", the PDF "ACME CORP." — same obligation."""
    a = ledger.event_key(entity_id="B_MRE", counterparty="Acme Corp",
                         due_date="2026-10-06", amount_cents=12500)
    b = ledger.event_key(entity_id="b_mre", counterparty="acme-corp",
                         due_date="2026-10-06T00:00:00", amount_cents=12500)
    assert a == b == "v1|B_MRE|acme-corp|due=2026-10-06|amt=12500"


def test_a_reference_number_beats_dates():
    k = ledger.event_key(entity_id="B_MRE", counterparty="Acme", reference="INV 7010",
                         due_date="2026-10-06", amount_cents=1)
    assert k == "v1|B_MRE|acme|ref=inv-7010"


def test_different_entities_never_share_a_key():
    """§0: separate the entities in records. Alike is not the same."""
    kw = dict(counterparty="Con Edison", due_date="2026-10-06", amount_cents=8800)
    assert ledger.event_key(entity_id="P_MRE", **kw) != \
        ledger.event_key(entity_id="B_MRE", **kw)


def test_next_months_bill_is_a_different_obligation():
    kw = dict(entity_id="P_MRE", counterparty="Con Edison", amount_cents=8800)
    assert ledger.event_key(due_date="2026-10-06", **kw) != \
        ledger.event_key(due_date="2026-11-06", **kw)


@pytest.mark.parametrize("kw", [
    dict(entity_id=None, counterparty="acme", due_date="2026-10-06"),
    dict(entity_id="UNASSIGNED", counterparty="acme", due_date="2026-10-06"),
    dict(entity_id="B_MRE", counterparty="", due_date="2026-10-06"),
    dict(entity_id="B_MRE", counterparty="acme"),                  # which instance?
    dict(entity_id="B_MRE", counterparty="acme", due_date="soon"),
])
def test_too_little_evidence_gives_no_key(kw):
    """Better two tasks a human merges than two obligations merged into one."""
    assert ledger.event_key(**kw) is None


# ---------------------------------------------------------------------------
# One task per obligation
# ---------------------------------------------------------------------------

def test_the_email_and_its_invoice_are_one_task(conn):
    """"A forwarded email, attached invoice and bank transaction can be evidence
    for the same obligation rather than three tasks." """
    email = record(conn, "artifact:aaa", source="email")
    pdf = record(conn, "artifact:bbb", source="attachment")
    bank = record(conn, "bank:txn-99812", source="bank")

    assert email.outcome == "created"
    assert pdf.outcome == bank.outcome == "merged"
    assert email.task_id == pdf.task_id == bank.task_id
    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
    srcs = ledger.sources(conn, email.task_id)
    assert [s["source"] for s in srcs] == ["email", "attachment", "bank"]
    assert len(reports.todo_list(conn)) == 1


def test_mail_then_a_photo_of_the_same_letter(conn):
    """The cross-source case R13 names: mail, then the paper copy photographed."""
    key = ledger.event_key(entity_id="P_MRE", counterparty="Nassau County Court",
                           due_date="2026-10-03", descriptor="jury summons")
    a = record(conn, "artifact:mail", key=key, source="email")
    b = record(conn, "artifact:photo", key=key, source="photo")
    assert a.task_id == b.task_id


def test_a_rerun_is_idempotent(conn):
    """"Repeated runs must be idempotent." The same source event twice is a no-op."""
    first = record(conn, "artifact:aaa")
    again = record(conn, "artifact:aaa")
    assert again.outcome == "seen"
    assert again.task_id == first.task_id
    assert len(ledger.sources(conn, first.task_id)) == 1
    assert [e["event"] for e in ledger.history(conn, first.task_id)] == ["CREATED"]


def test_a_rerun_without_a_key_is_still_idempotent(conn):
    first = record(conn, "imessage:guid-1", key=None, source="imessage")
    again = record(conn, "imessage:guid-1", key=None, source="imessage")
    assert again.task_id == first.task_id
    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1


def test_without_a_key_two_sources_stay_two_tasks(conn):
    a = record(conn, "artifact:aaa", key=None)
    b = record(conn, "artifact:bbb", key=None)
    assert a.task_id != b.task_id


def test_more_urgent_evidence_raises_the_task_never_lowers_it(conn):
    t = record(conn, "artifact:aaa", urgency="NORMAL")
    record(conn, "artifact:bbb", urgency="CRITICAL")
    assert ledger.get(conn, t.task_id)["urgency"] == "CRITICAL"
    record(conn, "artifact:ccc", urgency="LOW")
    assert ledger.get(conn, t.task_id)["urgency"] == "CRITICAL"


def test_a_paid_bill_received_again_is_evidence_not_a_new_task(conn):
    """Demonstration scenario 4. It is recorded against the closed task and
    flagged; it does not reappear on the list as something to pay."""
    t = record(conn, "artifact:aaa")
    ledger.transition(conn, t.task_id, "CLOSED", by="human", evidence="paid 10/01")
    again = record(conn, "artifact:resent")
    assert (again.task_id, again.outcome, again.after_close) == (t.task_id, "merged", True)
    assert reports.todo_list(conn) == []
    logged = conn.execute(
        "SELECT detail FROM actions_log WHERE action = 'TASK_EVIDENCE_ADDED'").fetchone()
    assert "after close" in logged["detail"]


def test_a_new_task_starts_only_where_a_task_can_start(conn):
    with pytest.raises(ledger.LedgerError, match="cannot start"):
        record(conn, "artifact:aaa", status="EXECUTED")


def test_record_event_refuses_fields_that_do_not_exist(conn):
    with pytest.raises(ledger.LedgerError, match="not task fields"):
        record(conn, "artifact:aaa", colour="red")


def test_a_new_task_is_matthews_unless_said_otherwise(conn):
    t = record(conn, "artifact:aaa")
    assert ledger.get(conn, t.task_id)["owner"] == ledger.OWNER_MATTHEW


# ---------------------------------------------------------------------------
# Duplicates found by a human
# ---------------------------------------------------------------------------

def test_marking_a_duplicate_moves_its_evidence(conn):
    a = record(conn, "artifact:aaa", key=None)
    b = record(conn, "artifact:bbb", key="v1|B_MRE|acme|ref=7010")
    ledger.mark_duplicate(conn, b.task_id, a.task_id, by="human")

    assert len(ledger.sources(conn, a.task_id)) == 2
    assert ledger.sources(conn, b.task_id) == []
    # The survivor inherits the key, so the next mention lands on it.
    assert ledger.get(conn, a.task_id)["event_key"] == "v1|B_MRE|acme|ref=7010"
    nxt = record(conn, "artifact:ccc", key="v1|B_MRE|acme|ref=7010")
    assert nxt.task_id == a.task_id


def test_duplicates_never_merge_across_entities(conn):
    a = a_task(conn, entity_id="B_MRE")
    b = a_task(conn, entity_id="B_MLE")
    with pytest.raises(ledger.IllegalTransition, match="different entities"):
        ledger.mark_duplicate(conn, b, a, by="human")


def test_a_duplicate_of_a_duplicate_lands_on_the_survivor(conn):
    a, b, c = a_task(conn), a_task(conn), a_task(conn)
    ledger.mark_duplicate(conn, b, a, by="human")
    ledger.mark_duplicate(conn, c, b, by="human")
    assert ledger.get(conn, c)["duplicate_of"] == a


# ---------------------------------------------------------------------------
# Fields
# ---------------------------------------------------------------------------

def test_field_changes_are_in_the_trail(conn):
    tid = a_task(conn)
    ledger.update_fields(conn, tid, by="coordinator", assigned_agent="finance",
                         permitted_tools=["read:statements"], action_risk="C")
    row = ledger.get(conn, tid)
    assert row["assigned_agent"] == "finance"
    assert row["permitted_tools"] == '["read:statements"]'
    details = [e["detail"] for e in ledger.history(conn, tid)]
    assert any("assigned_agent" in d for d in details)


def test_artifact_links_accumulate(conn):
    tid = a_task(conn)
    ledger.add_artifact_link(conn, tid, "drafts/reply-7010.eml", by="communications")
    ledger.add_artifact_link(conn, tid, "drafts/reply-7010.eml", by="communications")
    ledger.add_artifact_link(conn, tid, "filed/2026-10-06__MRE__invoice.pdf", by="documents")
    assert ledger.get(conn, tid)["artifact_links"] == (
        '["drafts/reply-7010.eml", "filed/2026-10-06__MRE__invoice.pdf"]')
