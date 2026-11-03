"""The coverage register (R7, D-069): config/sources.yaml plus source_checks.

Addendum §6: "Log successful checks and disclose or retry missed intervals",
and "never report the day as fully reviewed unless every registered source
completed or its gap is explicitly disclosed."
"""

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.db import database as db
from core.pipeline import coverage
from core.pipeline.registry import load_registry

NOW = datetime(2026, 11, 3, 20, 0, tzinfo=db.TZ)


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "lms.db")
    yield c
    c.close()


def src(**kw):
    raw = {"id": "email:a@x.com", "label": "A mail", "kind": "email",
           "method": "Gmail API", "permissions": "gmail.readonly",
           "status": "active", "requirement": "R16", "cadence_hours": 12}
    raw.update(kw)
    return coverage._source(raw)


def check_at(conn, source_id, when, result=coverage.OK, detail=None):
    """record_check stamps now; tests need a chosen time."""
    conn.execute("INSERT INTO source_checks (source_id, finished_at, result, detail) "
                 "VALUES (?,?,?,?)", (source_id, when.isoformat(), result, detail))


# ---------------------------------------------------------------------------
# The register as shipped
# ---------------------------------------------------------------------------

def test_the_register_loads_and_names_what_section_6_lists():
    reg = coverage.load_register()
    kinds = {s.kind for s in reg}
    # §6: email, texts/iMessage, Google Voice/voicemail/transcripts, phone
    # logs, WhatsApp, calendar — each must be on the list, working or not.
    assert {"email", "messages", "calls", "chat", "calendar"} <= kinds
    ids = {s.id for s in reg}
    assert {"messages:imessage", "chat:whatsapp", "calls:phone-log"} <= ids


def test_every_mailbox_the_registry_routes_on_is_registered():
    """A mailbox entities.yaml knows about but the register does not would be
    polled (or not) with nobody accounting for it."""
    ids = {s.id for s in coverage.load_register()}
    for ent in load_registry().entities.values():
        for e in ent.emails:
            assert f"email:{e.lower()}" in ids, f"{e} is not in config/sources.yaml"


def test_whatsapp_is_listed_as_having_no_supported_path():
    wa = next(s for s in coverage.load_register() if s.id == "chat:whatsapp")
    assert wa.status == "unsupported" and wa.blocked_on


@pytest.mark.parametrize("raw,why", [
    ({"status": "pending"}, "blocked on"),
    ({"kind": "fax"}, "kind"),
    ({"status": "broken"}, "status"),
    ({"cadance_hours": 1}, "unknown keys"),
    ({"cadence_hours": 0}, "positive"),
    ({"method": ""}, "method is required"),
])
def test_a_source_that_does_not_say_enough_does_not_load(raw, why):
    with pytest.raises(coverage.RegisterError, match=why):
        src(**raw)


def test_duplicate_ids_are_refused(tmp_path):
    (tmp_path / "sources.yaml").write_text(
        "sources:\n" + "".join(
            "  - {id: x, label: X, kind: email, method: m, permissions: p, "
            "status: active, requirement: R1}\n" for _ in range(2)),
        encoding="utf-8")
    with pytest.raises(coverage.RegisterError, match="duplicate"):
        coverage.load_register(tmp_path)


# ---------------------------------------------------------------------------
# Checks and missed intervals
# ---------------------------------------------------------------------------

def test_source_checks_is_append_only(conn):
    coverage.record_check(conn, "email:a@x.com", coverage.OK, items=3)
    with pytest.raises(Exception, match="append-only"):
        conn.execute("UPDATE source_checks SET result = 'FAILED'")
    with pytest.raises(Exception, match="append-only"):
        conn.execute("DELETE FROM source_checks")


def test_a_recent_good_check_is_current(conn):
    reg = (src(),)
    check_at(conn, "email:a@x.com", NOW - timedelta(hours=11))
    [c] = coverage.coverage(conn, reg, NOW)
    assert c.current and c.missed == 0
    assert coverage.disclosures(conn, reg, NOW) == []
    assert coverage.fully_reviewed(conn, reg, NOW)


def test_a_late_check_inside_the_grace_is_not_missed(conn):
    reg = (src(),)
    check_at(conn, "email:a@x.com", NOW - timedelta(hours=14))   # 12 h + 3 h grace
    assert coverage.coverage(conn, reg, NOW)[0].missed == 0


def test_missed_intervals_are_counted_and_disclosed(conn):
    """"Disclose ... missed intervals." """
    reg = (src(),)
    check_at(conn, "email:a@x.com", NOW - timedelta(hours=40))
    [c] = coverage.coverage(conn, reg, NOW)
    assert c.missed == 3
    [line] = coverage.disclosures(conn, reg, NOW)
    assert "3 scheduled check(s) missed" in line
    assert not coverage.fully_reviewed(conn, reg, NOW)


def test_a_failed_last_check_is_disclosed_first(conn):
    reg = (src(), src(id="email:b@x.com", label="B mail"))
    check_at(conn, "email:a@x.com", NOW - timedelta(hours=40))
    check_at(conn, "email:b@x.com", NOW - timedelta(hours=1), coverage.OK)
    check_at(conn, "email:b@x.com", NOW - timedelta(minutes=5), coverage.FAILED,
             detail="RefreshError: token revoked")
    lines = coverage.disclosures(conn, reg, NOW)
    assert lines[0].startswith("B mail: last check FAILED") and "revoked" in lines[0]
    assert "missed" in lines[1]


def test_a_partial_pass_is_not_a_clean_one(conn):
    reg = (src(),)
    check_at(conn, "email:a@x.com", NOW - timedelta(hours=1), coverage.PARTIAL,
             detail="500 message(s) — truncated by --limit")
    [line] = coverage.disclosures(conn, reg, NOW)
    assert "partial" in line and "truncated" in line


def test_never_checked_is_disclosed(conn):
    assert coverage.disclosures(conn, (src(),), NOW) == ["A mail: never checked"]


def test_a_source_that_is_not_read_is_disclosed_with_its_reason(conn):
    """Listed whether or not it works, so the gap stays visible."""
    reg = (src(id="chat:whatsapp", label="WhatsApp", kind="chat",
               status="unsupported", blocked_on="no supported path",
               requirement="R40"),)
    [line] = coverage.disclosures(conn, reg, NOW)
    assert line == "WhatsApp: not read — unsupported, no supported path (R40)"


def test_a_pushed_source_has_nothing_to_miss(conn):
    reg = (src(id="capture:mac-inbox", label="Inbox", kind="capture",
               cadence_hours=None),)
    assert coverage.disclosures(conn, reg, NOW) == []


def test_a_check_for_an_unregistered_source_is_flagged(conn):
    coverage.record_check(conn, "email:new@x.com", coverage.OK)
    assert coverage.disclosures(conn, (), NOW) == [
        "email:new@x.com: checked but not in the coverage register"]


def test_the_shipped_register_is_not_fully_reviewed_today(conn):
    """Most sources wait on Matthew. Saying "fully reviewed" now would be the
    exact false claim §6 forbids."""
    assert not coverage.fully_reviewed(conn, now=NOW)


# ---------------------------------------------------------------------------
# The mail poller records every pass
# ---------------------------------------------------------------------------

def _poll_mail():
    import importlib.util
    p = Path(__file__).resolve().parents[1] / "ops" / "poll_mail.py"
    spec = importlib.util.spec_from_file_location("poll_mail_r7", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _results(conn):
    return [(r["source_id"], r["result"]) for r in
            conn.execute("SELECT source_id, result FROM source_checks ORDER BY id")]


def test_poll_mail_records_ok_and_failed_checks(conn, tmp_path, monkeypatch):
    pm = _poll_mail()

    def build(address):
        if address == "bad@x.com":
            raise SystemExit("no OAuth grant for bad@x.com")
        return SimpleNamespace(recent=lambda **kw: [])

    monkeypatch.setattr(pm, "build_client", build)
    args = SimpleNamespace(days=2, limit=500, query="")
    for a in ("bad@x.com", "good@x.com"):
        pm.poll_mailbox(conn, a, args, None, None, None, tmp_path)
    assert _results(conn) == [("email:bad@x.com", "FAILED"),
                              ("email:good@x.com", "OK")]


def test_poll_mail_records_a_truncated_pass_as_partial(conn, tmp_path, monkeypatch):
    pm = _poll_mail()
    msgs = [SimpleNamespace(message_id=f"m{i}", subject="s", date="2026-11-01")
            for i in range(2)]
    monkeypatch.setattr(pm, "build_client",
                        lambda a: SimpleNamespace(recent=lambda **kw: msgs))
    monkeypatch.setattr(pm, "file_messages", lambda conn, m, ingest: {"filed": 2})
    args = SimpleNamespace(days=2, limit=2, query="")
    pm.poll_mailbox(conn, "a@x.com", args, None, None, None, tmp_path)
    assert _results(conn) == [("email:a@x.com", "PARTIAL")]


# ---------------------------------------------------------------------------
# The brief
# ---------------------------------------------------------------------------

def test_the_brief_reports_a_failed_source_with_the_stalled_jobs(conn):
    from core.reports import brief as reports
    check_at(conn, "email:matthew@mrecai.com", NOW - timedelta(minutes=30),
             coverage.FAILED, detail="RefreshError: token revoked")
    text = reports.render_text(reports.build_brief(conn, now=NOW))
    head, _, rest = text.partition("THE SYSTEM NEEDS ATTENTION")
    assert "MRECAI mail: last check FAILED" in rest and "token revoked" in rest


def test_the_daily_brief_does_not_alarm_on_never_checked_or_unconnected(conn):
    """Day one after install, and the sources Matthew has not connected yet,
    are not a system failure every morning."""
    from core.reports import brief as reports
    b = reports.build_brief(conn, now=NOW)            # a Tuesday evening
    assert b.coverage == [] and b.sources_unread == 0


def test_the_monday_brief_counts_the_sources_not_read(conn):
    from core.reports import brief as reports
    monday = datetime(2026, 11, 2, 6, 30, tzinfo=db.TZ)
    b = reports.build_brief(conn, kind="morning", now=monday)
    unread = sum(1 for s in coverage.load_register() if s.status != "active")
    assert b.sources_unread == unread > 0
    assert f"NOT READ AT ALL: {unread} source(s)" in reports.render_text(b)


def test_a_backfill_pass_is_not_a_check_of_the_mailbox(conn, tmp_path, monkeypatch):
    """An old window re-read must not make a stopped daily poll look current."""
    pm = _poll_mail()
    monkeypatch.setattr(pm, "build_client",
                        lambda a: SimpleNamespace(recent=lambda **kw: []))
    args = SimpleNamespace(days=400, limit=500, query="after:1 before:2")
    pm.poll_mailbox(conn, "a@x.com", args, None, None, None, tmp_path,
                    checks_source=False)
    assert _results(conn) == []
