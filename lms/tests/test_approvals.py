"""The policy engine, approval objects and mandates (R19, D-067).

The addendum's §3 is the specification; each test names the sentence it holds
the code to. Phase 1's acceptance test is among them: "unauthorized action
blocked".
"""

from datetime import datetime, timedelta
from functools import partial

import pytest

from core.db import database as db
from core.pipeline import approvals, ledger, policy as pol
from core.pipeline.policy import Action

NOW = datetime(2026, 11, 3, 10, 0, tzinfo=db.TZ)
STOPS = {"never_move_money": True, "never_send_without_approval": True,
         "never_delete_file": True, "never_sign_or_bind": True}


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "lms.db")
    yield c
    c.close()


def a_task(conn, status="PREPARED", risk="C", entity="P_MRE", **kw):
    kw.setdefault("title", "Con Edison bill, October")
    return db.insert_task(conn, status=status, action_risk=risk, entity_id=entity, **kw)


def bill(**kw):
    kw = {"doc_version": "ab" * 32, "recipient": "Con Edison",
          "destination": "acct ending 6789", "amount_cents": 18250,
          "source_account": "chase-1234", "rail": "chase-billpay",
          "category": "utilities", **kw}
    return Action("pay_bill", **kw)


def record_update(**kw):
    kw = {"recipient": "Con Edison", "destination": "OpenClaw/01 Personal/ledger.csv",
          "amount_cents": 18250, **kw}
    return Action("update_record", **kw)


def mandate(**kw):
    raw = {"id": "M-1", "decided_in": "D-999", "mode": "live",
           "kinds": ["update_record"], "entity": "P_MRE",
           "effective_from": "2026-11-01", "effective_until": "2027-02-01",
           "counterparties": {"Con Edison": {"destination": "OpenClaw/01 Personal/ledger.csv",
                                             "max_cents": 40000}}}
    raw.update(kw)
    return pol._mandate(raw)


def policy_with(*mandates, stops=STOPS):
    return pol.Policy(hard_stops=dict(stops), mandates=tuple(mandates),
                      approval_ttl_hours=48)


def execute(conn, tid, action, ref=None, *, policy=None, by="finance", now=NOW):
    gate = partial(approvals.gate, policy=policy or policy_with(), now=now)
    return ledger.transition(conn, tid, "EXECUTED", by=by, approval_ref=ref,
                             action=action, gate=gate)


def approved(conn, tid, action, *, policy=None, now=NOW):
    ref = approvals.request(conn, tid, action, by="finance",
                            policy=policy or policy_with(), now=now)
    approvals.decide(conn, ref, "approve", by="matthew", now=now)
    return ref


# ---------------------------------------------------------------------------
# The shipped configuration
# ---------------------------------------------------------------------------

def test_no_mandate_exists_until_matthew_decides():
    """Oct 19: trial mode accepted in principle; bills and bank on a call."""
    p = pol.load_policy()
    assert p.mandates == ()
    for stop in ("never_move_money", "never_send_without_approval"):
        assert p.hard_stops[stop] is True


# ---------------------------------------------------------------------------
# Levels
# ---------------------------------------------------------------------------

def test_the_action_sets_a_floor_on_the_level(conn):
    """An agent can raise a task's level, never lower it below the action's."""
    tid = a_task(conn, risk="A")
    assert pol.level_of(ledger.get(conn, tid), bill()) == "C"
    tid = a_task(conn, risk=None)
    assert pol.level_of(ledger.get(conn, tid), Action("report")) == "D"


def test_an_unassessed_task_is_never_inside_a_mandate(conn):
    tid = a_task(conn, risk=None)
    d = pol.evaluate(conn, ledger.get(conn, tid), record_update(),
                     policy=policy_with(mandate()), now=NOW)
    assert d.verdict == pol.NEEDS_APPROVAL and d.level == "D"


def test_level_b_executes_without_approval_or_action(conn):
    tid = a_task(conn, risk="B")
    ledger.transition(conn, tid, "EXECUTED", by="documents")
    assert ledger.get(conn, tid)["status"] == "EXECUTED"


# ---------------------------------------------------------------------------
# "Unauthorized action blocked" (Phase 1 acceptance)
# ---------------------------------------------------------------------------

def test_level_c_without_mandate_or_approval_is_blocked_and_logged(conn):
    tid = a_task(conn)
    with pytest.raises(ledger.ExecutionRefused, match="approval"):
        execute(conn, tid, record_update())
    assert ledger.get(conn, tid)["status"] == "PREPARED"
    refused = [e for e in ledger.history(conn, tid) if e["event"] == "EXECUTION_REFUSED"]
    assert len(refused) == 1, "declining to act leaves a trace"
    assert refused[0]["recipient"] == "Con Edison"


def test_execution_above_b_must_state_the_exact_action(conn):
    tid = a_task(conn, risk="D")
    with pytest.raises(ledger.ExecutionRefused, match="exact action"):
        ledger.transition(conn, tid, "EXECUTED", by="finance")


def test_a_missing_approval_is_not_an_approval(conn):
    """"An unavailable approval channel must not silently authorize it." """
    tid = a_task(conn)
    approvals.request(conn, tid, record_update(), by="finance", policy=policy_with(), now=NOW)
    with pytest.raises(ledger.ExecutionRefused, match="missing approval"):
        execute(conn, tid, record_update())


def test_an_unanswered_approval_authorizes_nothing(conn):
    tid = a_task(conn)
    ref = approvals.request(conn, tid, record_update(), by="finance",
                            policy=policy_with(), now=NOW)
    with pytest.raises(ledger.ExecutionRefused, match="PENDING"):
        execute(conn, tid, record_update(), ref)


def test_an_approval_of_another_task_does_not_transfer(conn):
    one, two = a_task(conn), a_task(conn)
    ref = approved(conn, one, record_update())
    ledger.transition(conn, two, "AWAITING_APPROVAL", by="finance")
    with pytest.raises(ledger.ExecutionRefused, match="not an approval of"):
        execute(conn, two, record_update(), ref)


# ---------------------------------------------------------------------------
# Approval objects
# ---------------------------------------------------------------------------

def test_the_approval_object_states_the_exact_action(conn):
    """"Task ID, exact action, draft or before/after, payee and bank
    destination, amount, affected files, evidence links, anomaly, expiration." """
    tid = a_task(conn)
    ref = approvals.request(
        conn, tid, bill(files=("OpenClaw/01 Personal/coned-oct.pdf",)), by="finance",
        before_after="unpaid -> paid", evidence_links=["artifact:ab"],
        anomaly="amount 40% above the 12-month average", policy=policy_with(), now=NOW)
    a = approvals.get(conn, ref)
    assert a["task_id"] == tid and a["kind"] == "pay_bill"
    assert (a["recipient"], a["destination"], a["amount_cents"]) == (
        "Con Edison", "acct ending 6789", 18250)
    assert "coned-oct.pdf" in a["affected_files"] and "artifact:ab" in a["evidence_links"]
    assert a["anomaly"].startswith("amount 40%")
    assert a["expires_at"] == (NOW + timedelta(hours=48)).isoformat()
    assert ledger.get(conn, tid)["status"] == "AWAITING_APPROVAL"


def test_an_approval_needs_every_material_field(conn):
    tid = a_task(conn)
    with pytest.raises(approvals.ApprovalError, match="destination"):
        approvals.request(conn, tid, bill(destination=None), by="finance",
                          policy=policy_with(), now=NOW)


def test_approved_details_execute_once(conn):
    """"Never convert one exception approval into an unlimited grant." """
    tid = a_task(conn)
    ref = approved(conn, tid, record_update())
    execute(conn, tid, record_update(), ref)
    assert approvals.get(conn, ref)["status"] == approvals.CONSUMED
    assert ledger.get(conn, tid)["status"] == "EXECUTED"

    # The same action again, on a second task: the duplicate check, not a new grant.
    again = a_task(conn)
    ref2 = approvals.request(conn, again, record_update(), by="finance",
                             policy=policy_with(), now=NOW)
    assert approvals.get(conn, ref2)["policy_verdict"] == pol.REFUSED
    approvals.decide(conn, ref2, "approve", by="matthew", now=NOW)
    with pytest.raises(ledger.ExecutionRefused, match=f"already executed under {ref}"):
        execute(conn, again, record_update(), ref2)


@pytest.mark.parametrize("changed", [
    {"amount_cents": 18251},
    {"destination": "acct ending 0000"},       # changed bank details
    {"recipient": "Con Edison Billing"},
    {"doc_version": "ef" * 32},                # a different version of the bill
])
def test_approval_applies_only_to_those_exact_details(conn, changed):
    tid = a_task(conn)
    ref = approved(conn, tid, record_update())
    with pytest.raises(approvals.ApprovalMismatch) as exc:
        execute(conn, tid, record_update(**changed), ref)
    assert list(changed)[0] in str(exc.value)
    assert approvals.get(conn, ref)["status"] == approvals.APPROVED, "not consumed"


def test_an_approval_expires(conn):
    tid = a_task(conn)
    ref = approved(conn, tid, record_update())
    later = NOW + timedelta(hours=49)
    with pytest.raises(ledger.ExecutionRefused, match="expired"):
        execute(conn, tid, record_update(), ref, now=later)
    assert approvals.expire_due(conn, now=later) == [ref]
    assert approvals.get(conn, ref)["status"] == approvals.EXPIRED
    assert ledger.get(conn, tid)["status"] == "IN_PROGRESS"


def test_nothing_is_approved_by_waiting(conn):
    tid = a_task(conn)
    ref = approvals.request(conn, tid, record_update(), by="finance",
                            policy=policy_with(), now=NOW)
    later = NOW + timedelta(days=30)
    approvals.expire_due(conn, now=later)
    assert approvals.get(conn, ref)["status"] == approvals.EXPIRED
    with pytest.raises(approvals.ApprovalError, match="EXPIRED"):
        approvals.decide(conn, ref, "approve", by="matthew", now=later)


def test_a_denial_cancels_the_task(conn):
    """"A denial ... never becomes an implicit approval." """
    tid = a_task(conn)
    ref = approvals.request(conn, tid, record_update(), by="finance",
                            policy=policy_with(), now=NOW)
    approvals.decide(conn, ref, "deny", by="matthew", note="not this month", now=NOW)
    row = ledger.get(conn, tid)
    assert (row["status"], row["final_outcome"]) == ("CANCELED", ledger.DENIED)
    with pytest.raises(ledger.IllegalTransition):
        execute(conn, tid, record_update(), ref)


def test_revise_returns_it_for_revision(conn):
    tid = a_task(conn)
    ref = approvals.request(conn, tid, record_update(), by="finance",
                            policy=policy_with(), now=NOW)
    approvals.decide(conn, ref, "revise", by="matthew", note="use the new account", now=NOW)
    assert ledger.get(conn, tid)["status"] == "IN_PROGRESS"
    assert approvals.get(conn, ref)["status"] == approvals.REVISE


def test_only_a_human_answers(conn):
    tid = a_task(conn)
    ref = approvals.request(conn, tid, record_update(), by="finance",
                            policy=policy_with(), now=NOW)
    with pytest.raises(approvals.ApprovalError, match="not a human"):
        approvals.decide(conn, ref, "approve", by="coordinator", now=NOW)


def test_a_new_request_supersedes_the_old_one(conn):
    tid = a_task(conn)
    old = approved(conn, tid, record_update())
    new = approvals.request(conn, tid, record_update(amount_cents=19000), by="finance",
                            policy=policy_with(), now=NOW)
    assert approvals.get(conn, old)["status"] == approvals.SUPERSEDED
    with pytest.raises(ledger.ExecutionRefused, match="SUPERSEDED"):
        execute(conn, tid, record_update(), old)
    approvals.decide(conn, new, "approve", by="matthew", now=NOW)
    execute(conn, tid, record_update(amount_cents=19000), new)


def test_approval_details_cannot_be_edited_or_deleted(conn):
    tid = a_task(conn)
    ref = approvals.request(conn, tid, record_update(), by="finance",
                            policy=policy_with(), now=NOW)
    with pytest.raises(Exception, match="fixed"):
        conn.execute("UPDATE approvals SET amount_cents = 1 WHERE ref = ?", (ref,))
    with pytest.raises(Exception, match="append-only"):
        conn.execute("DELETE FROM approvals WHERE ref = ?", (ref,))
    approvals.decide(conn, ref, "deny", by="matthew", now=NOW)
    with pytest.raises(Exception, match="settled"):
        conn.execute("UPDATE approvals SET status = 'APPROVED' WHERE ref = ?", (ref,))


# ---------------------------------------------------------------------------
# Hard stops and trial mode
# ---------------------------------------------------------------------------

def test_no_approval_pays_a_bill_while_never_move_money_stands(conn):
    """Trial mode: the payment is prepared and shown; approving records his
    agreement; nothing is paid."""
    tid = a_task(conn)
    ref = approvals.request(conn, tid, bill(), by="finance", policy=policy_with(), now=NOW)
    a = approvals.get(conn, ref)
    assert a["policy_verdict"] == pol.REFUSED and "never_move_money" in a["policy_reasons"]
    answer = approvals.decide(conn, ref, "approve", by="matthew", now=NOW)
    assert answer.authorizes is False
    with pytest.raises(ledger.ExecutionRefused, match="never_move_money"):
        execute(conn, tid, bill(), ref)
    assert ledger.get(conn, tid)["status"] == "AWAITING_APPROVAL"
    # He pays it himself; his word closes it.
    ledger.transition(conn, tid, "CLOSED", by="matthew", evidence="paid it myself")


def test_a_mandate_cannot_send_while_never_send_without_approval_stands(conn):
    m = mandate(kinds=["send_reply"], reply_classes=["acknowledge_receipt"],
                counterparties={"Con Edison": {"destination": "billing@coned.example"}})
    p = policy_with(m)
    reply = Action("send_reply", recipient="Con Edison", destination="billing@coned.example",
                   reply_class="acknowledge_receipt", draft_sha256="cd" * 32)
    tid = a_task(conn)
    with pytest.raises(ledger.ExecutionRefused, match="never_send_without_approval"):
        execute(conn, tid, reply, policy=p)
    ref = approved(conn, tid, reply, policy=p)
    assert approvals.get(conn, ref)["would_execute"] == 1
    execute(conn, tid, reply, ref, policy=p)


def test_a_shadow_mandate_records_what_it_would_have_done(conn):
    p = policy_with(mandate(mode="shadow"))
    tid = a_task(conn)
    with pytest.raises(ledger.ExecutionRefused, match="shadow"):
        execute(conn, tid, record_update(), policy=p)
    ref = approvals.request(conn, tid, record_update(), by="finance", policy=p, now=NOW)
    a = approvals.get(conn, ref)
    assert (a["would_execute"], a["mandate_id"]) == (1, "M-1")


# ---------------------------------------------------------------------------
# Mandates
# ---------------------------------------------------------------------------

def test_a_live_mandate_executes_inside_its_scope_and_says_so(conn):
    p = policy_with(mandate())
    tid = a_task(conn)
    execute(conn, tid, record_update(), policy=p)
    executed = [e for e in ledger.history(conn, tid) if e["to_status"] == "EXECUTED"][0]
    a = approvals.get(conn, executed["approval_ref"])
    assert (a["basis"], a["mandate_id"], a["status"]) == ("MANDATE", "M-1", "CONSUMED")


@pytest.mark.parametrize("action,why", [
    (record_update(recipient="Someone New"), "not a counterparty"),
    (record_update(destination="OpenClaw/99 elsewhere.csv"), "not the verified one"),
    (record_update(amount_cents=40001), "ceiling"),
])
def test_outside_the_mandate_pauses_for_approval(conn, action, why):
    """"A task outside scope must pause." """
    tid = a_task(conn)
    with pytest.raises(ledger.ExecutionRefused, match=why):
        execute(conn, tid, action, policy=policy_with(mandate()))


def test_a_mandate_is_one_entitys(conn):
    tid = a_task(conn, entity="B_MRE")
    with pytest.raises(ledger.ExecutionRefused, match="no mandate covers"):
        execute(conn, tid, record_update(), policy=policy_with(mandate()))


@pytest.mark.parametrize("kw,today,why", [
    ({"revoked": "2026-11-02"}, NOW, "revoked"),
    ({}, NOW + timedelta(days=120), "in effect"),
    ({}, datetime(2026, 10, 31, 12, tzinfo=db.TZ), "in effect"),
])
def test_a_mandate_has_an_effective_period_and_can_be_revoked(conn, kw, today, why):
    tid = a_task(conn)
    with pytest.raises(ledger.ExecutionRefused, match=why):
        execute(conn, tid, record_update(), policy=policy_with(mandate(**kw)), now=today)


def test_frequency_limit_per_counterparty(conn):
    p = policy_with(mandate(frequency={"count": 1, "per": "month"}))
    first, second = a_task(conn), a_task(conn)
    execute(conn, first, record_update(), policy=p)
    with pytest.raises(ledger.ExecutionRefused, match="this month"):
        execute(conn, second, record_update(doc_version="ef" * 32), policy=p)


def test_daily_aggregate_exposure(conn):
    p = policy_with(mandate(daily_aggregate_cents=30000))
    first, second = a_task(conn), a_task(conn)
    execute(conn, first, record_update(), policy=p)
    with pytest.raises(ledger.ExecutionRefused, match="today's total"):
        execute(conn, second, record_update(doc_version="ef" * 32), policy=p)
    # Tomorrow is a new day.
    execute(conn, second, record_update(doc_version="ef" * 32), policy=p,
            now=NOW + timedelta(days=1))


@pytest.mark.parametrize("raw,why", [
    ({"kinds": ["transfer"]}, "not a level C kind"),
    ({"kinds": ["delete"]}, "not a level C kind"),
    ({"max_cent": 5}, "unknown keys"),
    ({"mode": "on"}, "mode"),
    ({"decided_in": ""}, "decided_in is required"),
    ({"effective_until": "2026-10-01"}, "ends before"),
    ({"kinds": ["pay_bill"], "source_accounts": ["c"], "rails": ["r"],
      "daily_aggregate_cents": 1, "counterparties": {"X": {"max_cents": 5}}},
     "verified destination"),
    ({"kinds": ["pay_bill"], "counterparties": {"X": {"destination": "d", "max_cents": 5}}},
     "needs source_accounts"),
    ({"kinds": ["send_reply"]}, "reply_classes"),
])
def test_a_mandate_that_is_not_bounded_does_not_load(raw, why):
    with pytest.raises(pol.PolicyConfigError, match=why):
        mandate(**raw)


# ---------------------------------------------------------------------------
# Where Matthew sees them
# ---------------------------------------------------------------------------

def test_the_brief_lists_live_approvals_only(conn):
    from core.reports import brief as reports
    tid = a_task(conn)
    ref = approvals.request(conn, tid, bill(), by="finance", policy=policy_with(), now=NOW)
    text = reports.render_text(reports.build_brief(conn, now=NOW + timedelta(hours=1)))
    assert "APPROVALS WAITING (1)" in text and ref in text and "$182.50" in text
    assert "agreement only" in text            # a payment, under never_move_money
    later = reports.build_brief(conn, now=NOW + timedelta(hours=49))
    assert later.approvals == []
