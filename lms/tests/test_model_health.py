"""The model health ping (R34, spec §4.6.5): reload, and say so when it cannot.

LM Studio and its `lms` CLI are faked. Nothing here opens a socket or starts a
process: the fake server answers the two calls the check makes (model_states
and complete), and the loader is a callable that flips a model to "loaded" the
way a successful `lms load` does. lms_load itself is tested against a fake
subprocess.run, so the argv it would hand the real CLI is asserted exactly.

What no fake can show, and the first run on the Mac must: that `lms` lives at
~/.lmstudio/bin, and that /api/v0/models reports `state` as "loaded" /
"not-loaded" (PHASE6B_APPLY.md step 1 reads the same field by hand).
"""

import ast
import io
import json
import os
import plistlib
import re
import subprocess
import sys
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parents[1] / "ops"
sys.path.insert(0, str(OPS))

import model_health as mh                                    # noqa: E402
from core.adapters import lmstudio                           # noqa: E402
from core.adapters.lmstudio import (TIER_MODELS, Completion,  # noqa: E402
                                    LMStudio, ModelError)
from core.db import database as db                           # noqa: E402
from core.reports import brief as reports                    # noqa: E402

L1, L2 = TIER_MODELS["TIER-L1"], TIER_MODELS["TIER-L2"]
TIERS = {"TIER-L1": L1, "TIER-L2": L2}
PLIST = OPS / "com.lms.modelhealth.plist"

NY = timezone(timedelta(hours=-4))
NOW = datetime(2026, 11, 3, 6, 30, tzinfo=NY)


class FakeLMStudio:
    """The two calls the check makes, with LM Studio's real vocabulary."""

    def __init__(self, states: dict, *, mute=(), down=False):
        self.states = dict(states)
        self.mute = set(mute)           # loaded, but the completion fails
        self.down = down                # the server itself is not answering
        self.probes: list[dict] = []

    def model_states(self):
        if self.down:
            raise ModelError("cannot reach LM Studio at http://localhost:1234: "
                             "[Errno 61] Connection refused")
        return dict(self.states)

    def complete(self, *, tier, **kw):
        self.probes.append({"tier": tier, **kw})
        if tier in self.mute:
            raise ModelError("HTTP 500 from LM Studio: model crashed")
        return Completion(text="ok", model=TIER_MODELS[tier], elapsed_s=0.2)


class FakeLoader:
    """`lms load`, as the check sees it: (succeeded, last line printed)."""

    def __init__(self, lm: FakeLMStudio, *, fail: str | None = None, sticks=True):
        self.lm, self.fail, self.sticks = lm, fail, sticks
        self.calls: list[str] = []

    def __call__(self, model):
        self.calls.append(model)
        if self.fail:
            return False, self.fail
        if self.sticks:
            self.lm.states[model] = "loaded"
        return True, "Model loaded successfully in 84.20s."


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "lms.db")
    yield c
    c.close()


def actions(conn) -> list[tuple[str, str]]:
    return [(r[0], r[1] or "") for r in conn.execute(
        "SELECT action, detail FROM actions_log ORDER BY id")]


def go(lm, conn, load=None):
    load = load or FakeLoader(lm)
    rc = mh.run(lm, TIERS, conn, load=load, sleep=lambda s: None)
    return rc, load


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------

def test_both_resident_models_loaded_and_answering_is_ok(conn):
    lm = FakeLMStudio({L1: "loaded", L2: "loaded", "glm-ocr": "not-loaded"})
    rc, load = go(lm, conn)

    assert rc == 0
    assert load.calls == []
    assert [p["tier"] for p in lm.probes] == ["TIER-L1", "TIER-L2"]
    assert [a for a, _ in actions(conn)] == ["MODEL_HEALTH_OK"]


def test_the_probe_is_tiny_and_does_not_retry(conn):
    """A retry with backoff inside a five-minute job only delays the alarm."""
    lm = FakeLMStudio({L1: "loaded", L2: "loaded"})
    go(lm, conn)
    for p in lm.probes:
        assert p["max_tokens"] <= 32 and p["retries"] == 0


def test_an_unloaded_model_is_reloaded_then_probed(conn):
    """The Mac, before this job: the drafter found not-loaded and put back by
    hand with `lms load`."""
    lm = FakeLMStudio({L1: "loaded", L2: "not-loaded"})
    rc, load = go(lm, conn)

    assert rc == 0
    assert load.calls == [L2]
    assert "TIER-L2" in [p["tier"] for p in lm.probes]
    rows = actions(conn)
    assert [a for a, _ in rows] == ["MODEL_RELOADED", "MODEL_HEALTH_OK"]
    reloaded = rows[0][1]
    assert L2 in reloaded and "not-loaded" in reloaded and "32768" in reloaded


def test_a_failed_reload_fails_the_run_and_says_why(conn):
    lm = FakeLMStudio({L1: "loaded", L2: "not-loaded"})
    load = FakeLoader(lm, fail="lms load exit 1: Not enough memory to load model")
    rc, _ = go(lm, conn, load)

    assert rc != 0, "launchctl list must show the failure too"
    rows = actions(conn)
    assert [a for a, _ in rows] == ["MODEL_HEALTH_FAILED"], "no reload happened"
    assert L2 in rows[0][1] and "Not enough memory" in rows[0][1]
    assert L1 not in rows[0][1], "the healthy tier is not part of the failure"


def test_an_unloaded_model_is_never_probed(conn):
    """Probing first would make LM Studio load it just in time, at its default
    context length and with its idle TTL: the eviction, all over again."""
    lm = FakeLMStudio({L1: "loaded", L2: "not-loaded"})
    go(lm, conn, FakeLoader(lm, fail="lms load exit 1: x"))
    assert "TIER-L2" not in [p["tier"] for p in lm.probes]


def test_a_reload_that_does_not_take_is_a_failure(conn):
    lm = FakeLMStudio({L1: "loaded", L2: "not-loaded"})
    sleeps = []
    rc = mh.run(lm, TIERS, conn, load=FakeLoader(lm, sticks=False),
                sleep=sleeps.append)

    assert rc != 0
    rows = actions(conn)
    assert [a for a, _ in rows] == ["MODEL_RELOADED", "MODEL_HEALTH_FAILED"]
    assert "still says not-loaded" in rows[1][1]
    assert len(sleeps) == mh.RECHECKS - 1


def test_a_loaded_model_that_does_not_answer_is_reported_not_reloaded(conn):
    """Unloading 70 GB mid-request would kill a draft in progress."""
    lm = FakeLMStudio({L1: "loaded", L2: "loaded"}, mute={"TIER-L1"})
    rc, load = go(lm, conn)

    assert rc != 0
    assert load.calls == []
    rows = actions(conn)
    assert [a for a, _ in rows] == ["MODEL_HEALTH_FAILED"]
    assert "not answering" in rows[0][1] and "model crashed" in rows[0][1]


def test_lm_studio_down_is_a_failure_without_a_reload(conn):
    lm = FakeLMStudio({}, down=True)
    rc, load = go(lm, conn)
    assert rc != 0 and load.calls == []
    assert "Connection refused" in actions(conn)[0][1]


def test_a_model_lm_studio_has_never_heard_of_is_not_loaded_blindly(conn):
    """A changed id (LM Studio sometimes prefixes `author/`) is a config fix
    in three files, not something a reload can do."""
    lm = FakeLMStudio({L1: "loaded", "qwen/" + L2: "loaded"})
    rc, load = go(lm, conn)
    assert rc != 0 and load.calls == []
    assert "id changed" in actions(conn)[0][1]


def test_no_resident_tier_is_a_failure_not_a_pass(conn):
    """An empty list checks nothing, and all() of nothing is True."""
    rc = mh.run(FakeLMStudio({}), {}, conn, load=FakeLoader(None))
    assert rc != 0
    assert actions(conn)[0][0] == "MODEL_HEALTH_FAILED"


def test_without_the_database_the_check_still_runs_and_fails_loudly(capsys):
    """The array unmounted: nothing to log to, but the exit status still says."""
    lm = FakeLMStudio({L1: "loaded", L2: "not-loaded"})
    rc = mh.run(lm, TIERS, None, load=FakeLoader(lm, fail="no"), sleep=lambda s: None)
    assert rc != 0
    assert "not recorded" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# How often it writes
# ---------------------------------------------------------------------------

def ok(*tiers):
    return [mh.TierResult(t, TIERS[t], True, elapsed_s=0.4) for t in tiers]


def bad(reason="loaded but not answering: HTTP 500"):
    return [mh.TierResult("TIER-L1", L1, True),
            mh.TierResult("TIER-L2", L2, False, reason=reason)]


def last(action, ago, detail=""):
    return (action, (NOW - ago).isoformat(timespec="seconds"), detail)


def test_an_unchanged_ok_is_written_once_an_hour():
    """288 rows a day saying "fine" would bury the side effects."""
    res = ok("TIER-L1", "TIER-L2")
    assert mh.rows_to_write(res, None, NOW)[0][0] == "MODEL_HEALTH_OK"
    assert mh.rows_to_write(res, last("MODEL_HEALTH_OK", timedelta(minutes=5)), NOW) == []
    assert mh.rows_to_write(res, last("MODEL_HEALTH_OK", timedelta(minutes=61)), NOW)


def test_recovery_is_written_at_once():
    res = ok("TIER-L1", "TIER-L2")
    rows = mh.rows_to_write(res, last("MODEL_HEALTH_FAILED", timedelta(minutes=5)), NOW)
    assert [a for a, _ in rows] == ["MODEL_HEALTH_OK"]


def test_the_same_failure_is_not_repeated_every_five_minutes():
    first = mh.rows_to_write(bad(), last("MODEL_HEALTH_OK", timedelta(minutes=5)), NOW)
    assert [a for a, _ in first] == ["MODEL_HEALTH_FAILED"]
    again = mh.rows_to_write(
        bad(), last("MODEL_HEALTH_FAILED", timedelta(minutes=5), first[0][1]), NOW)
    assert again == []


def test_a_different_failure_is_written():
    prior = mh.rows_to_write(bad(), None, NOW)[0][1]
    rows = mh.rows_to_write(bad("was not-loaded; reload failed: lms load exit 1: x"),
                            last("MODEL_HEALTH_FAILED", timedelta(minutes=5), prior), NOW)
    assert [a for a, _ in rows] == ["MODEL_HEALTH_FAILED"]


def test_a_reload_is_always_written_with_its_outcome():
    res = [mh.TierResult("TIER-L2", L2, True, reloaded=True, was="not-loaded",
                         load_said="Model loaded.")]
    rows = mh.rows_to_write(res, last("MODEL_HEALTH_OK", timedelta(minutes=5)), NOW)
    assert [a for a, _ in rows] == ["MODEL_RELOADED", "MODEL_HEALTH_OK"]


def test_record_reads_its_own_last_row(conn):
    lm = FakeLMStudio({L1: "loaded", L2: "loaded"})
    go(lm, conn)
    go(lm, conn)
    assert [a for a, _ in actions(conn)] == ["MODEL_HEALTH_OK"]


# ---------------------------------------------------------------------------
# The alert: the brief
# ---------------------------------------------------------------------------

def test_a_failed_check_is_in_the_brief_above_the_work(conn):
    lm = FakeLMStudio({L1: "loaded", L2: "not-loaded"})
    go(lm, conn, FakeLoader(lm, fail="lms load exit 1: Not enough memory"))

    stalled = reports.stalled_jobs(conn, datetime.now())
    assert len(stalled) == 1
    assert stalled[0].startswith("models: FAILED 0m ago: ")
    assert "Not enough memory" in stalled[0]

    text = reports.render_text(reports.build_brief(conn, kind="morning"))
    assert "THE SYSTEM NEEDS ATTENTION" in text
    assert text.index("THE SYSTEM NEEDS ATTENTION") < text.index("models: FAILED")


def test_a_later_success_clears_it(conn):
    lm = FakeLMStudio({L1: "loaded", L2: "not-loaded"})
    go(lm, conn, FakeLoader(lm, fail="lms load exit 1: x"))
    go(lm, conn)                                     # this time the load works
    assert reports.stalled_jobs(conn, datetime.now()) == []


def test_a_failure_with_no_success_ever_is_still_reported(conn):
    """Unlike a job that has never run, this is a fact, not an empty table:
    the very first check on a fresh install can find the drafter missing."""
    db.log_action(conn, "MODEL_HEALTH_FAILED", detail="TIER-L2 x: reload failed")
    assert reports.stalled_jobs(conn, datetime.now())[0].startswith("models: FAILED")


def test_the_failure_line_stays_phone_shaped(conn):
    db.log_action(conn, "MODEL_HEALTH_FAILED", detail="x" * 400)
    line = reports.stalled_jobs(conn, datetime.now())[0]
    assert len(line) < 160 and line.endswith("...")


def test_a_ping_that_stops_running_is_reported_as_stalled(conn):
    conn.execute("INSERT INTO actions_log (ts, action, detail) VALUES (?, ?, ?)",
                 ((NOW - timedelta(hours=5)).isoformat(timespec="seconds"),
                  "MODEL_HEALTH_OK", "x"))
    assert reports.stalled_jobs(conn, NOW) == ["models: last ran 5h ago "
                                               "(2026-11-03T01:30-04:00)"]


def test_an_hourly_heartbeat_is_not_mistaken_for_a_stall(conn):
    """The row is written hourly; the threshold must allow for that."""
    stale_after = timedelta(hours=reports.WATCHED_JOBS["MODEL_HEALTH_OK"][1])
    assert stale_after > 2 * mh.HEARTBEAT, "one late heartbeat would cry wolf"
    conn.execute("INSERT INTO actions_log (ts, action, detail) VALUES (?, ?, ?)",
                 ((NOW - timedelta(minutes=70)).isoformat(timespec="seconds"),
                  "MODEL_HEALTH_OK", "x"))
    assert reports.stalled_jobs(conn, NOW) == []


def test_every_action_the_brief_reads_is_one_this_job_writes():
    """The D-027 trap: an alarm armed by a string nothing writes never fires."""
    src = (OPS / "model_health.py").read_text(encoding="utf-8")
    for failed, ok_action in reports.JOB_FAILURES.items():
        if not failed.startswith("MODEL_"):
            continue                       # another job's (test_notify.py)
        assert f'"{failed}"' in src and f'"{ok_action}"' in src
        assert ok_action in reports.WATCHED_JOBS


# ---------------------------------------------------------------------------
# `lms load`
# ---------------------------------------------------------------------------

class FakeRun:
    def __init__(self, rc=0, out="", err="", raises=None):
        self.rc, self.out, self.err, self.raises = rc, out, err, raises
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))
        if self.raises:
            raise self.raises
        return subprocess.CompletedProcess(argv, self.rc, self.out, self.err)


def test_lms_load_is_the_command_used_by_hand():
    run = FakeRun(out="Loading model...\nModel loaded successfully in 84.2s.\n")
    done, said = mh.lms_load(L2, run=run, cli="/Users/mleca/.lmstudio/bin/lms")

    assert done and said == "Model loaded successfully in 84.2s."
    argv, kw = run.calls[0]
    assert argv == ["/Users/mleca/.lmstudio/bin/lms", "load", L2,
                    "--context-length", "32768"]
    assert "--ttl" not in argv, "a TTL is the opposite of keep-alive indefinite"
    assert kw["stdin"] is subprocess.DEVNULL, "a CLI prompt must not hang the job"
    assert kw["timeout"] == mh.LOAD_TIMEOUT_S


def test_lms_load_failure_carries_the_cli_message():
    run = FakeRun(rc=1, err="Error: Not enough memory to load model\n")
    done, said = mh.lms_load(L2, run=run, cli="lms")
    assert not done and "exit 1" in said and "Not enough memory" in said


def test_lms_load_timeout_and_missing_binary_are_failures(monkeypatch):
    run = FakeRun(raises=subprocess.TimeoutExpired("lms", 600))
    assert mh.lms_load(L2, run=run, cli="lms") == (False, "lms load timed out after 600s")
    assert mh.lms_load(L2, run=FakeRun(raises=FileNotFoundError("lms")), cli="lms")[0] is False
    monkeypatch.setattr(mh, "find_cli", lambda: None)
    done, said = mh.lms_load(L2, run=FakeRun())
    assert not done and "not found" in said


def test_the_cli_is_found_in_lmstudio_home_before_path(tmp_path, monkeypatch):
    """launchd does not read the login shell's PATH, where the installer put it."""
    cli = tmp_path / ".lmstudio" / "bin" / "lms"
    cli.parent.mkdir(parents=True)
    cli.write_text("")
    monkeypatch.setattr(mh.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(mh.shutil, "which", lambda name: "/opt/elsewhere/lms")
    assert mh.find_cli() == str(cli)
    cli.unlink()
    assert mh.find_cli() == "/opt/elsewhere/lms"


# ---------------------------------------------------------------------------
# Derived, not retyped
# ---------------------------------------------------------------------------

def test_the_resident_tiers_come_from_the_fragment():
    assert mh.resident_tiers() == {"TIER-L1": L1, "TIER-L2": L2}


def test_the_context_length_is_what_openclaw_budgets_against():
    """Loaded smaller, the gateway would send prompts the server truncates."""
    patch = (OPS / "phase6b.patch.json5").read_text(encoding="utf-8")
    for model in mh.resident_tiers().values():
        m = re.search(r'id:\s*"%s".*?contextWindow:\s*(\d+)' % re.escape(model),
                      patch, re.S)
        assert m, f"{model} not in phase6b.patch.json5"
        assert int(m.group(1)) == mh.CONTEXT_LENGTH


# ---------------------------------------------------------------------------
# The adapter call it relies on
# ---------------------------------------------------------------------------

def test_model_states_reads_lm_studios_own_api(monkeypatch):
    """/api/v0/models, outside /v1, on the same loopback server."""
    seen = []
    body = {"data": [{"id": L1, "state": "loaded"},
                     {"id": L2, "state": "not-loaded"}]}

    def fake_urlopen(url, timeout):
        seen.append(url)
        return io.BytesIO(json.dumps(body).encode())

    monkeypatch.setattr(lmstudio.urllib.request, "urlopen", fake_urlopen)
    assert LMStudio("http://localhost:1234/v1").model_states() == {
        L1: "loaded", L2: "not-loaded"}
    assert seen == ["http://localhost:1234/api/v0/models"]


def test_model_states_unreachable_is_a_model_error(monkeypatch):
    def refuse(url, timeout):
        raise urllib.error.URLError("[Errno 61] Connection refused")

    monkeypatch.setattr(lmstudio.urllib.request, "urlopen", refuse)
    with pytest.raises(ModelError):
        LMStudio().model_states()


# ---------------------------------------------------------------------------
# launchd and the kill switch
# ---------------------------------------------------------------------------

def test_the_plist_is_one_test_plists_checks():
    from tests.test_plists import PLISTS
    assert PLIST in PLISTS


def test_the_plist_runs_this_script_every_five_minutes():
    p = plistlib.loads(PLIST.read_bytes())
    assert p["Label"] == "com.lms.modelhealth"
    assert Path(p["ProgramArguments"][-1]).name == "model_health.py"
    assert p["StartInterval"] == 300
    assert not p.get("KeepAlive"), "a one-shot that KeepAlive would loop"
    assert p["StandardOutPath"].endswith("/LMS/logs/modelhealth.log")
    assert p["StandardErrorPath"].endswith("/LMS/logs/modelhealth.err")


def _halt_constants() -> dict:
    """PREFIXES and SELF_LABEL, read from _halt.py without importing it:
    it binds os.killpg at import, which exists only on POSIX."""
    tree = ast.parse((OPS / "_halt.py").read_text(encoding="utf-8"))
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id in ("PREFIXES", "SELF_LABEL"):
            out[node.targets[0].id] = ast.literal_eval(node.value)
    return out


def test_a_halt_stops_this_job():
    """/halt discovers jobs by label prefix rather than by list (D-059)."""
    c = _halt_constants()
    label = plistlib.loads(PLIST.read_bytes())["Label"]
    assert label.startswith(tuple(c["PREFIXES"])) and label != c["SELF_LABEL"]


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="_halt is POSIX-only")
def test_a_halt_stops_this_job_by_the_halt_modules_own_rule():
    import _halt
    assert _halt.is_target(plistlib.loads(PLIST.read_bytes())["Label"])


def test_run_by_hand_while_halted_it_touches_nothing(monkeypatch, capsys):
    monkeypatch.setattr(mh, "ensure_venv", lambda *a, **k: None)
    monkeypatch.setattr(mh, "load_lms_env", lambda *a, **k: {})
    monkeypatch.setattr(mh, "halt_flag", lambda: {"ts": "2026-10-19T09:12:00-04:00",
                                                  "by": "telegram"})

    def forbidden(*a, **k):
        raise AssertionError("halted, and it reached for LM Studio or the log")

    monkeypatch.setattr(mh, "LMStudio", forbidden)
    monkeypatch.setattr(mh, "open_db", forbidden)
    monkeypatch.setattr(mh, "lms_load", forbidden)

    assert mh.main() == 0
    assert "HALTED" in capsys.readouterr().out
