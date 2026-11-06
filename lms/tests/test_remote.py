"""ops/remote.py: Matthew's fixed commands from WhatsApp and Telegram (D-080)."""

import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))

import remote                                                # noqa: E402
from core.db import database as db                         # noqa: E402

NOW = datetime(2026, 11, 5, 9, 0, tzinfo=db.TZ)
TG = "8783061626"
WA = "15551234567"


@pytest.fixture
def dbfile(tmp_path):
    path = tmp_path / "lms.db"
    db.connect(path).close()
    return path


@pytest.fixture
def env(tmp_path, dbfile):
    calls = {"run": [], "halt": [], "refresh": 0, "log": []}

    def run(argv):
        calls["run"].append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    def refresh(conn):
        calls["refresh"] += 1

    e = remote.Env(
        connect=lambda: db.connect(dbfile),
        owners={"telegram": TG, "whatsapp": WA},
        flag=tmp_path / "HALTED.json",
        run=run,
        spawn_halt=lambda channel: calls["halt"].append(channel),
        status=lambda: {"halted": None,
                        "jobs": [("com.lms.mail", "loaded"),
                                 ("ai.openclaw.gateway", "running (pid 9)")],
                        "haltbot": 123},
        refresh=refresh,
        now=lambda: NOW,
        uid=501,
        log=lambda msg: calls["log"].append(msg),
    )
    e.calls = calls
    e.dbfile = dbfile
    return e


def actions(env):
    c = db.connect(env.dbfile)
    try:
        return [(r["action"], r["detail"], r["approved_by"])
                for r in c.execute("SELECT * FROM actions_log ORDER BY id")]
    finally:
        c.close()


def task(env, title, *, due=None, status="TRIAGED", sensitivity="normal"):
    c = db.connect(env.dbfile)
    try:
        return db.insert_task(c, title=title, due_date=due, urgency="NORMAL",
                              entity_id="B_MRE", status=status,
                              sensitivity=sensitivity)
    finally:
        c.close()


# --- who may use it ----------------------------------------------------------

@pytest.mark.parametrize("channel,sender", [
    ("telegram", TG), ("whatsapp", "+1 555 123 4567"),
    ("whatsapp", "15551234567@s.whatsapp.net"),
    ("whatsapp", "15551234567:12@s.whatsapp.net"),
    ("whatsapp", "whatsapp:+15551234567"), ("telegram", f"telegram:{TG}"),
])
def test_the_owner_is_recognised(env, channel, sender):
    assert remote.handle(["help"], channel, sender, env).startswith("LMS commands")


@pytest.mark.parametrize("channel,sender", [
    ("telegram", "1234"), ("whatsapp", "15559999999"), ("whatsapp", ""),
    ("whatsapp", TG), ("sms", TG),
])
def test_a_stranger_gets_nothing_and_is_logged(env, channel, sender):
    assert remote.handle(["halt"], channel, sender, env) == ""
    assert env.calls["halt"] == []
    assert actions(env)[-1][0] == "remote:refused"


def test_no_owner_in_keychain_means_nobody(env):
    env.owners = {"telegram": "", "whatsapp": ""}
    assert remote.handle(["status"], "telegram", TG, env) == ""


# --- parsing -----------------------------------------------------------------

@pytest.mark.parametrize("words", [
    [], ["HELP"], ["frobnicate"], ["done"], ["done", "12x"], ["done", "1234567"],
    ["done", "12", "please"], ["tasks", "all"], ["status", "now"], ["rm", "-rf"],
])
def test_anything_else_gets_the_help_list(env, words):
    assert remote.handle(words, "telegram", TG, env).startswith("LMS commands")


def test_help_lists_every_command_and_no_resume(env):
    text = remote.handle(["help"], "telegram", TG, env)
    for name in ("status", "brief", "tasks", "coverage", "mail", "whatsapp",
                 "backup", "done", "approve", "deny", "halt"):
        assert f"/lms {name}" in text
    assert "resume" in text.lower() and "at the Mac" in text


def test_every_accepted_call_is_audited(env):
    remote.handle(["status"], "whatsapp", WA, env)
    action, detail, by = actions(env)[-1]
    assert action == "remote:status"
    assert "whatsapp" in detail and by is None


# --- look ----------------------------------------------------------------------

def test_status_shows_halt_state_and_jobs(env):
    text = remote.handle(["status"], "telegram", TG, env)
    assert "Running (not halted)." in text
    assert "mail: loaded" in text and "gateway: running (pid 9)" in text


def test_status_shows_the_last_good_mail_and_whatsapp_checks(env):
    c = db.connect(env.dbfile)
    for sid, ts, result in (("email:matthew@mrecai.com", "2026-11-05T07:01:00", "OK"),
                            ("email:matthew@mleca.com", "2026-11-05T07:02:00", "OK"),
                            ("email:mre@atlase.ai", "2026-11-05T08:00:00", "FAILED"),
                            ("chat:whatsapp", "2026-11-05T08:30:00", "OK")):
        c.execute("INSERT INTO source_checks (source_id, finished_at, result)"
                  " VALUES (?, ?, ?)", (sid, ts, result))
    c.close()
    text = remote.handle(["status"], "telegram", TG, env)
    assert "last good mail: 2026-11-05T07:02" in text
    assert "last good whatsapp: 2026-11-05T08:30" in text


def test_status_still_answers_without_the_database(env):
    def gone():
        raise SystemExit("NOT FILING: /Volumes/MacStudioHD is not mounted.")
    env.connect = gone
    text = remote.handle(["status"], "telegram", TG, env)
    assert "Running (not halted)." in text
    assert "database not reachable" in text


def test_tasks_lists_open_tasks_and_hides_private_titles(env):
    a = task(env, "Pay Con Edison", due="2026-11-01")
    b = task(env, "Dr. Patel follow-up", sensitivity="private")
    text = remote.handle(["tasks"], "telegram", TG, env)
    assert f"#{a} !! Pay Con Edison" in text
    assert f"#{b}" in text and "Patel" not in text


def test_tasks_overdue_filters(env):
    a = task(env, "Pay Con Edison", due="2026-11-01")
    b = task(env, "File NY biennial", due="2026-11-20")
    text = remote.handle(["tasks", "overdue"], "telegram", TG, env)
    assert f"#{a}" in text and f"#{b}" not in text


def test_no_open_tasks(env):
    assert "Nothing open." in remote.handle(["tasks"], "telegram", TG, env)


def test_reply_is_cut_at_the_limit(env):
    for i in range(300):
        task(env, f"Task {i} " + "y" * 60, due="2026-12-01")
    text = remote.handle(["tasks"], "telegram", TG, env)
    assert len(text) <= remote.MAX_REPLY
    assert text.endswith("… more on the Dashboard.")


def test_a_locked_database_says_busy(env):
    import sqlite3

    def locked():
        raise sqlite3.OperationalError("database is locked")
    env.connect = locked
    assert remote.handle(["tasks"], "telegram", TG, env) == "Busy, try again in a minute."


def test_an_unmounted_archive_is_reported(env):
    def gone():
        raise SystemExit("NOT FILING: /Volumes/MacStudioHD is not mounted.")
    env.connect = gone
    assert "not mounted" in remote.handle(["tasks"], "telegram", TG, env)


def test_a_crash_is_logged_not_shown(env):
    def boom():
        raise ValueError("secret detail")
    env.connect = boom
    text = remote.handle(["tasks"], "telegram", TG, env)
    assert text == "Failed. See ~/LMS/logs/remote.log."
    assert any("secret detail" in m for m in env.calls["log"])


# --- run now -----------------------------------------------------------------

@pytest.mark.parametrize("name,label", [("mail", "com.lms.mail"),
                                        ("whatsapp", "com.lms.whatsapp"),
                                        ("backup", "com.lms.backup")])
def test_run_now_kickstarts_the_existing_job(env, name, label):
    text = remote.handle([name], "telegram", TG, env)
    assert env.calls["run"] == [["launchctl", "kickstart", f"gui/501/{label}"]]
    assert text.startswith(f"Started {name} now.")


def test_run_now_reports_a_job_that_is_not_installed(env):
    env.run = lambda argv: subprocess.CompletedProcess(argv, 113, "", "not found")
    assert "not installed" in remote.handle(["whatsapp"], "telegram", TG, env)


def test_run_now_is_refused_while_halted(env):
    env.flag.write_text('{"ts": "2026-11-05T08:00", "by": "telegram"}')
    text = remote.handle(["mail"], "telegram", TG, env)
    assert text.startswith("Halted since 2026-11-05T08:00")
    assert "./ops/halt.py --resume" in text
    assert env.calls["run"] == []


# --- halt ----------------------------------------------------------------------

def test_halt_starts_the_detached_halt_and_says_so(env):
    text = remote.handle(["halt"], "whatsapp", WA, env)
    assert env.calls["halt"] == ["whatsapp"]
    assert "Halting in 2 seconds" in text and "@MREOC18bot" in text
    action, _, by = actions(env)[-1]
    assert action == "remote:halt" and by == "matthew"


def test_halt_when_already_halted_still_halts_and_mentions_it(env):
    env.flag.write_text('{"ts": "2026-11-05T08:00", "by": "local"}')
    text = remote.handle(["halt"], "telegram", TG, env)
    assert env.calls["halt"] == ["telegram"]
    assert "Was already halted since 2026-11-05T08:00 (by local)" in text
    assert "halting again in 2 seconds" in text


# --- done ----------------------------------------------------------------------

def test_done_without_yes_only_shows(env):
    tid = task(env, "Pay Con Edison", due="2026-11-01")
    text = remote.handle(["done", str(tid)], "telegram", TG, env)
    assert f"#{tid} Pay Con Edison" in text
    assert f"/lms done {tid} yes" in text
    c = db.connect(env.dbfile)
    assert c.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()[0] == "TRIAGED"
    c.close()
    assert env.calls["refresh"] == 0


def test_done_yes_closes_it_as_matthew_and_refreshes(env):
    tid = task(env, "Pay Con Edison", due="2026-11-01")
    text = remote.handle(["done", str(tid), "yes"], "whatsapp", WA, env)
    assert text == f"Closed #{tid}: Pay Con Edison"
    c = db.connect(env.dbfile)
    assert c.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()[0] == "CLOSED"
    c.close()
    assert env.calls["refresh"] == 1
    assert actions(env)[-1][2] == "matthew"


def test_done_still_closes_when_the_refresh_fails(env):
    def boom(conn):
        raise RuntimeError("snapshot broke")
    env.refresh = boom
    tid = task(env, "Pay Con Edison", due="2026-11-01")
    text = remote.handle(["done", str(tid), "yes"], "whatsapp", WA, env)
    assert text == f"Closed #{tid}: Pay Con Edison"
    assert any("snapshot broke" in m for m in env.calls["log"])
    assert actions(env)[-1][0] == "remote:done"


def test_done_private_task_is_not_named(env):
    tid = task(env, "Dr. Patel follow-up", sensitivity="private")
    assert "Patel" not in remote.handle(["done", str(tid)], "telegram", TG, env)
    assert "Patel" not in remote.handle(["done", str(tid), "yes"], "telegram", TG, env)


def test_done_unknown_task(env):
    assert remote.handle(["done", "999"], "telegram", TG, env) == "No task #999."


def test_done_twice_says_it_was_already(env):
    tid = task(env, "Pay Con Edison")
    remote.handle(["done", str(tid), "yes"], "telegram", TG, env)
    assert "already" in remote.handle(["done", str(tid), "yes"], "telegram", TG, env)


# --- approve / deny -----------------------------------------------------------------

def approval(env, ref="AP-7", hours=5, recipient="Con Edison", sensitivity="normal"):
    # approvals.decide checks expiry against the real clock, so these times
    # are relative to it, not to NOW.
    from datetime import timedelta
    base = datetime.now(db.TZ)
    tid = task(env, "Con Edison bill", status="AWAITING_APPROVAL",
               sensitivity=sensitivity)
    c = db.connect(env.dbfile)
    c.execute(
        "INSERT INTO approvals (ref, task_id, basis, kind, action_json, fingerprint,"
        " recipient, amount_cents, policy_verdict, requested_by, requested_at,"
        " expires_at, status) VALUES (?, ?, 'HUMAN', 'pay_bill', '{}', ?, ?, 18250,"
        " 'NEEDS_APPROVAL', 'finance', ?, ?, 'PENDING')",
        (ref, tid, ref, recipient, base.isoformat(),
         (base + timedelta(hours=hours)).isoformat()))
    c.close()
    return tid


def test_approve_without_yes_only_shows(env):
    approval(env)
    text = remote.handle(["approve", "7"], "telegram", TG, env)
    assert "AP-7" in text and "pay_bill" in text and "Con Edison" in text
    assert "$182.50" in text and "/lms approve 7 yes" in text
    c = db.connect(env.dbfile)
    assert c.execute("SELECT status FROM approvals WHERE ref='AP-7'").fetchone()[0] == "PENDING"
    c.close()


def test_approve_yes_decides_as_matthew(env):
    approval(env)
    text = remote.handle(["approve", "7", "yes"], "whatsapp", WA, env)
    assert text.startswith("AP-7 approved")
    c = db.connect(env.dbfile)
    row = c.execute("SELECT status, decided_by, decision_note FROM approvals"
                    " WHERE ref='AP-7'").fetchone()
    c.close()
    assert tuple(row) == ("APPROVED", "matthew", "via whatsapp")
    assert env.calls["refresh"] == 1


def test_approve_still_decides_when_the_refresh_fails(env):
    def boom(conn):
        raise RuntimeError("snapshot broke")
    env.refresh = boom
    approval(env)
    text = remote.handle(["approve", "7", "yes"], "whatsapp", WA, env)
    assert text.startswith("AP-7 approved")
    assert any("snapshot broke" in m for m in env.calls["log"])


def test_deny_yes_denies(env):
    approval(env)
    assert remote.handle(["deny", "7", "yes"], "telegram", TG, env).startswith("AP-7 denied")


def test_approve_unknown(env):
    assert remote.handle(["approve", "8"], "telegram", TG, env) == "No approval AP-8."


def test_approve_private_hides_the_recipient(env):
    approval(env, recipient="Dr. Patel", sensitivity="private")
    assert "Patel" not in remote.handle(["approve", "7"], "telegram", TG, env)


def test_an_approval_error_is_shown(env):
    approval(env, hours=-1)
    text = remote.handle(["approve", "7", "yes"], "telegram", TG, env)
    assert "expired" in text
