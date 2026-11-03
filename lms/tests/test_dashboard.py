"""The task dashboard (R22, addendum §6).

The page is opened from iCloud Drive on a phone. What must hold: every §6
section is there; nothing on it is fetched from anywhere; a hostile email
subject is text, never markup; a private task is counted but never named; an
approval a hard stop forbids is marked as agreement only; and an empty ledger
says so instead of rendering a blank page.
"""

import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.db import database as db
from core.pipeline import approvals, ledger, policy as pol
from core.pipeline.policy import Action
from core.pipeline.registry import load_registry
from core.reports import dashboard as dash

OPS = Path(__file__).resolve().parents[1] / "ops"
CONFIG = Path(__file__).resolve().parents[1] / "config"
NOW = datetime(2026, 11, 3, 10, 0, tzinfo=db.TZ)
TODAY = NOW.date()
STOPS = {"never_move_money": True, "never_send_without_approval": True,
         "never_delete_file": True, "never_sign_or_bind": True}
POLICY = pol.Policy(hard_stops=STOPS, mandates=(), approval_ttl_hours=48)


@pytest.fixture
def registry():
    return load_registry(CONFIG)


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "lms.db")
    yield c
    c.close()


def day(n: int) -> str:
    return (TODAY + timedelta(days=n)).isoformat()


def task(conn, title, *, due=None, entity="B_MRE", status="TRIAGED", **kw) -> int:
    return db.insert_task(conn, title=title, due_date=due, entity_id=entity,
                          status=status, **kw)


def build(conn, registry=None, **kw):
    return dash.build_dashboard(conn, registry=registry, now=NOW, **kw)


def page(conn, registry=None, **kw) -> str:
    return dash.render_html(build(conn, registry, **kw))


def section(html: str, sid: str) -> str:
    m = re.search(rf'<section id="{sid}"[^>]*>(.*?)</section>', html, re.S)
    assert m, f"no section {sid}"
    return m.group(1)


def waiting_approval(conn, title, action, **kw) -> tuple[int, str]:
    tid = task(conn, title, status="PREPARED", action_risk="C", **kw)
    ref = approvals.request(conn, tid, action, by="finance", policy=POLICY,
                            now=NOW - timedelta(hours=1))
    return tid, ref


def bill(**kw):
    return Action("pay_bill", **{
        "doc_version": "ab" * 32, "recipient": "Con Edison",
        "destination": "acct ending 6789", "amount_cents": 18250,
        "source_account": "chase-1234", "rail": "chase-billpay",
        "category": "utilities", **kw})


def record_update(**kw):
    return Action("update_record", **{
        "recipient": "PSG Global", "destination": "MRECAI/FINANCE/ledger.csv",
        "amount_cents": 925500, **kw})


@pytest.fixture
def full(conn):
    """One of everything §6 names."""
    ids = {
        "overdue": task(conn, "LegalZoom NY compliance", due=day(-3), urgency="HIGH"),
        "near": task(conn, "Hartford renewal", due=day(5), due_date_verified=1),
        "far": task(conn, "Atlase domain renewal", due=day(12), entity="B_ATL"),
        "personal": task(conn, "Con Edison account review", due=day(2), entity="P_HH",
                         domain="PERSONAL", assigned_agent="lms-finance"),
        "blocked": task(conn, "PSG Global tax return", entity="B_MRE"),
        "clarify": task(conn, "Unknown letter from NYS", entity="UNASSIGNED",
                        status="NEEDS_CLARIFICATION"),
        "done": task(conn, "Send W-9 to Hartford", status="EXECUTED"),
    }
    ledger.transition(conn, ids["blocked"], "BLOCKED", by="coordinator",
                      detail="waiting on the K-1 from PSG")
    ledger.transition(conn, ids["done"], "VERIFIED", by="auditor",
                      evidence="Sent folder: message 2026-11-02 to hartford.com")
    waiting_approval(conn, "Con Edison bill, October", bill(), entity="P_HH")
    waiting_approval(conn, "Record PSG invoice", record_update())
    return ids


# ---------------------------------------------------------------------------
# The sections §6 names
# ---------------------------------------------------------------------------

def test_every_section_the_addendum_names_is_on_the_page(conn, registry, full):
    html = page(conn, registry)
    for sid in ("approvals", "overdue", "blocked", "split", "entities", "agents",
                "hours", "proof"):
        section(html, sid)
    assert "Nothing open" not in html


def test_overdue_and_upcoming_go_to_their_horizon(conn, registry, full):
    d = build(conn, registry)
    assert [i.task_id for i in d.overdue] == [full["overdue"]]
    assert {i.task_id for i in d.due_near} == {full["near"], full["personal"]}
    assert [i.task_id for i in d.due_far] == [full["far"]]
    over = section(dash.render_html(d), "overdue")
    assert "3d OVERDUE" in over and "LegalZoom NY compliance" in over


def test_a_detected_due_date_is_marked_provisional(conn, registry, full):
    html = section(page(conn, registry), "overdue")
    near = next(li for li in html.split("<li") if "Hartford renewal" in li)
    far = next(li for li in html.split("<li") if "Atlase domain renewal" in li)
    assert "date unverified" not in near
    assert "date unverified" in far


def test_business_and_personal_are_split(conn, registry, full):
    d = build(conn, registry)
    sides = {g.key: g for g in d.sides}
    # The UNASSIGNED letter is neither, and is not guessed into one.
    assert sides["personal"].open == 2       # the P_HH review and the P_HH bill
    assert sides["unsorted"].open == 1
    assert sides["business"].open == d.open_count - 3 == 6


def test_business_and_personal_are_shown_at_zero(conn, registry):
    task(conn, "Only business", entity="B_MLE")
    d = build(conn, registry)
    assert [g.key for g in d.sides] == ["business", "personal"]
    assert d.sides[1].open == 0


def test_entities_are_separate_and_named_from_the_registry(conn, registry, full):
    d = build(conn, registry)
    by_id = {g.key: g for g in d.entities}
    assert by_id["B_ATL"].open == 1 and by_id["B_ATL"].due_soon == 0
    assert by_id["B_MRE"].overdue == 1 and by_id["B_MRE"].stuck == 1
    assert by_id["B_MRE"].awaiting == 1
    assert "MRECAI Consulting &amp; Insurance" in section(dash.render_html(d), "entities")


def test_blocked_shows_why_from_the_task_events(conn, registry, full):
    html = section(page(conn, registry), "blocked")
    assert "PSG Global tax return" in html
    assert "waiting on the K-1 from PSG" in html
    assert "Unknown letter from NYS" in html


def test_delegated_assistant_is_counted_and_undelegated_is_last(conn, registry, full):
    d = build(conn, registry)
    assert d.agents[0].label == "lms-finance" and d.agents[0].open == 1
    assert d.agents[-1].label == "Not delegated"


def test_proof_of_completion_carries_the_evidence_and_who(conn, registry, full):
    html = section(page(conn, registry), "proof")
    assert "Send W-9 to Hartford" in html
    assert "Sent folder: message 2026-11-02 to hartford.com" in html
    assert "by auditor" in html


def test_a_task_verified_without_being_recent_is_not_proof(conn, registry):
    tid = task(conn, "Old one", status="CLOSED", verification_evidence="x",
               completed_at=(NOW - timedelta(days=30)).isoformat())
    assert tid not in [i.task_id for i in build(conn, registry).proof]


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------

def test_approvals_list_the_exact_action_and_expiry(conn, registry, full):
    html = section(page(conn, registry), "approvals")
    assert "AP-1" in html and "AP-2" in html
    assert "Con Edison" in html and "$182.50" in html and "acct ending 6789" in html
    assert "expires 2026-11-05 09:00" in html


def test_a_hard_stop_marks_the_approval_agreement_only(conn, registry, full):
    html = section(page(conn, registry), "approvals")
    items = {m.group(1): m.group(0) for m in
             re.finditer(r'<li class="[^"]*">.*?(AP-\d+).*?</li>', html, re.S)}
    assert "Agreement only" in items["AP-1"]          # pay_bill, never_move_money
    assert "agreement-only" in items["AP-1"]
    assert "Agreement only" not in items["AP-2"]      # update_record: approvable


def test_an_approval_past_its_time_is_not_shown_as_live(conn, registry, full):
    later = dash.build_dashboard(conn, registry=registry, now=NOW + timedelta(days=3))
    assert later.approvals == [] and later.approvals_expired == 2
    # Read-only: still PENDING until the brief job expires it.
    assert len(approvals.pending(conn)) == 2


# ---------------------------------------------------------------------------
# Hostile input and privacy
# ---------------------------------------------------------------------------

def test_a_hostile_title_is_escaped(conn, registry):
    task(conn, '<script>alert(1)</script><img src=x onerror=alert(2)>"&', due=day(1))
    html = page(conn, registry)
    assert "<script" not in html.lower()
    assert "<img" not in html.lower()
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_a_hostile_approval_field_is_escaped(conn, registry):
    waiting_approval(conn, "bill", bill(recipient="<b onmouseover=x>Con Ed</b>"))
    html = page(conn, registry)
    assert "<b onmouseover" not in html and "&lt;b onmouseover=x&gt;" in html


@pytest.mark.parametrize("sensitivity", ["high", "private"])
def test_a_private_task_is_counted_but_never_named(conn, registry, sensitivity):
    tid = task(conn, "Dr. Kaplan biopsy results", due=day(-1), entity="P_MRE",
               sensitivity=sensitivity, detail="pathology lab")
    done = task(conn, "Rosenthal settlement signed", status="EXECUTED",
                sensitivity=sensitivity)
    ledger.transition(conn, done, "VERIFIED", by="auditor",
                      evidence="signed copy from Rosenthal counsel")
    ledger.transition(conn, done, "CLOSED", by="matthew")
    d = build(conn, registry)
    html = dash.render_html(d)
    assert "Kaplan" not in html and "biopsy" not in html and "pathology" not in html
    assert "Rosenthal" not in html
    assert f"Private item #{tid}" in html and f"Private item #{done}" in html
    assert d.open_count == 1 and len(d.overdue) == 1        # still counted
    assert "Kaplan" not in dash.render_summary(d)


def test_a_private_tasks_approval_withholds_the_counterparty(conn, registry):
    tid, ref = waiting_approval(conn, "Divorce attorney retainer",
                                bill(recipient="Berger Family Law"),
                                sensitivity="private")
    html = section(page(conn, registry), "approvals")
    assert ref in html and f"Private item #{tid}" in html
    assert "Berger" not in html and "Divorce" not in html and "acct ending" not in html
    assert "$182.50" in html and "Agreement only" in html


def test_taxpayer_ids_are_masked(conn, registry):
    task(conn, "W-2 for SSN 123-45-6789, employer EIN 12-3456789", due=day(1))
    html = page(conn, registry)
    assert "123-45-6789" not in html and "12-3456789" not in html
    # the shared digest redactor (R15): last four kept, so he can tell them apart
    assert "SSN •••-••-6789" in html and "EIN ••-•••6789" in html


def test_safe_is_the_escape_and_the_mask():
    assert dash._safe('<a href="x">') == "&lt;a href=&quot;x&quot;&gt;"
    assert dash._safe(None) == ""
    assert dash._safe("ref 1234-56-78901") == "ref 1234-56-78901"    # not an SSN


# ---------------------------------------------------------------------------
# Nothing loads from anywhere
# ---------------------------------------------------------------------------

def test_no_external_resource_is_referenced(conn, registry, full):
    html = page(conn, registry)
    assert "http://" not in html and "https://" not in html
    assert not re.search(r"<(script|link|img|iframe|object|embed|a)\b", html, re.I)
    assert not RESOURCE_ATTR.search(html)
    assert "@import" not in html and "url(" not in html
    assert "default-src 'none'" in html


# An attribute inside a real tag. Escaped text has no "<", so cannot match.
RESOURCE_ATTR = re.compile(r"<[^>]*\b(src|href|action|srcset)\s*=", re.I)


def test_a_url_in_a_title_stays_text(conn, registry):
    task(conn, 'Click <a href="https://evil.example/x">here</a>', due=day(1))
    html = page(conn, registry)
    assert not RESOURCE_ATTR.search(html)
    assert not re.search(r"<a\b", html, re.I)
    assert "&lt;a href=&quot;https://evil.example/x&quot;&gt;" in html


def test_it_is_phone_shaped_and_follows_the_theme(conn, registry, full):
    html = page(conn, registry)
    assert 'name="viewport"' in html
    assert "prefers-color-scheme:dark" in html
    assert html.startswith("<!doctype html>")


# ---------------------------------------------------------------------------
# Empty, and hours
# ---------------------------------------------------------------------------

def test_an_empty_ledger_says_nothing_is_open(conn, registry):
    d = build(conn, registry)
    assert d.is_empty
    html = dash.render_html(d)
    assert "Nothing open" in section(html, "nothing-open")
    assert "Nothing is waiting for approval." in html
    assert "Nothing open." in dash.render_summary(d)


def test_finished_work_alone_is_still_nothing_open(conn, registry):
    tid = task(conn, "Done", status="EXECUTED")
    ledger.transition(conn, tid, "VERIFIED", by="auditor", evidence="receipt")
    assert not build(conn, registry).is_empty        # VERIFIED is still open
    ledger.transition(conn, tid, "CLOSED", by="matthew")
    d = build(conn, registry)
    assert d.is_empty and len(d.proof) == 1


def test_hours_are_not_invented_before_r21(conn, registry, full):
    d = build(conn, registry)
    assert d.hours.human_hours_remaining is None and d.hours.ai_hours_saved is None
    hours = section(dash.render_html(d), "hours")
    assert hours.count("not estimated yet (R21)") == 2
    assert "not estimated yet (R21)" in dash.render_summary(d)


def test_hours_show_what_r21_estimates(conn, registry, full):
    est = {full["overdue"]: dash.Estimate(full["overdue"], 90, 30),
           full["near"]: dash.Estimate(full["near"], 30, 60),
           9999: dash.Estimate(9999, 600, 600)}           # not open: not counted
    d = build(conn, registry, estimates=est)
    assert d.hours.estimated_tasks == 2
    assert d.hours.human_hours_remaining == 2.0 and d.hours.ai_hours_saved == 1.5
    hours = section(dash.render_html(d), "hours")
    assert "2.0 h" in hours and f"2 of {d.open_count}" in hours


def test_a_stalled_job_is_above_everything(conn, registry):
    conn.execute("INSERT INTO actions_log (ts, action) VALUES (?, ?)",
                 ((NOW - timedelta(days=5)).isoformat(), "MAIL_POLL_STOP"))
    html = page(conn, registry)
    assert html.index('id="stalled"') < html.index('id="approvals"')


# ---------------------------------------------------------------------------
# ops/dashboard.py
# ---------------------------------------------------------------------------

@pytest.fixture
def ops_dash(monkeypatch):
    monkeypatch.setenv("LMS_DASHBOARD_REEXEC", "1")
    sys.path.insert(0, str(OPS))
    import dashboard as script
    return script


@pytest.fixture
def env(tmp_path, monkeypatch):
    archive = tmp_path / "LMS"
    archive.mkdir()
    monkeypatch.setenv("LMS_ARCHIVE_ROOT", str(archive))
    monkeypatch.setenv("LMS_DB", str(tmp_path / "lms.db"))
    monkeypatch.setenv("LMS_BRIEF_DIR", str(tmp_path / "briefs"))
    return tmp_path


def test_it_goes_where_the_brief_goes(ops_dash, env):
    import deliver_brief
    assert ops_dash.default_target() == deliver_brief.brief_dir() / "Dashboard.html"
    assert ops_dash.default_target().parent == env / "briefs"


def test_the_script_writes_the_page_and_logs_it(ops_dash, env):
    c = db.connect(env / "lms.db")
    task(c, "Hartford renewal", due=day(5))
    c.close()
    assert ops_dash.main([]) == 0
    html = (env / "briefs" / "Dashboard.html").read_text(encoding="utf-8")
    assert "Hartford renewal" in html
    assert not list((env / "briefs").glob(".*tmp"))          # no temp left behind
    c = db.connect(env / "lms.db")
    assert c.execute("SELECT COUNT(*) FROM actions_log WHERE action = "
                     "'DASHBOARD_WRITTEN'").fetchone()[0] == 1
    c.close()


def test_dry_run_writes_nothing_and_names_no_task(ops_dash, env, capsys):
    c = db.connect(env / "lms.db")
    task(c, "Hartford renewal", due=day(5))
    c.close()
    out_path = env / "elsewhere" / "d.html"
    assert ops_dash.main(["--dry-run", "--out", str(out_path)]) == 0
    out = capsys.readouterr().out
    assert str(out_path) in out and "open 1" in out
    assert "Hartford" not in out
    assert not out_path.exists() and not (env / "briefs").exists()


def test_a_missing_archive_replaces_the_page_with_an_alarm(ops_dash, env, monkeypatch):
    target = env / "briefs" / "Dashboard.html"
    target.parent.mkdir()
    target.write_text("yesterday's backlog", encoding="utf-8")
    monkeypatch.setenv("LMS_ARCHIVE_ROOT", str(env / "gone"))
    assert ops_dash.main([]) == 1
    html = target.read_text(encoding="utf-8")
    assert "The system needs attention" in html and "yesterday" not in html


def test_a_short_read_back_is_a_failure(ops_dash, tmp_path, monkeypatch):
    import builtins
    real_open = builtins.open
    target = tmp_path / "Dashboard.html"

    class Short:
        def __init__(self, f):
            self.f = f

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.f.close()

        def read(self):
            return self.f.read()[:5]

    def fake_open(path, *a, **k):
        f = real_open(path, *a, **k)
        mode = a[0] if a else k.get("mode", "r")
        return Short(f) if Path(path) == target and "r" in mode else f

    monkeypatch.setattr(builtins, "open", fake_open)
    with pytest.raises(ops_dash.deliver_brief.DeliveryError, match="did not complete"):
        ops_dash.write_atomic(target, "a page much longer than five")
