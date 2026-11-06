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
