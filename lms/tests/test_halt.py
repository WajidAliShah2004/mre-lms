"""The kill switch (D-059): ops/_halt.py and the bot's gatekeeping.

launchd is faked with a small state machine that behaves the way the real one
does where it matters here: a disabled job cannot be bootstrapped, bootout
removes a job from `launchctl list`, and a KeepAlive job booted out while still
ENABLED comes straight back. That last rule is why disable must come first,
and a fake without it would pass a halt that the Mac would undo.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))

import _halt                                                 # noqa: E402
import halt_bot                                              # noqa: E402

UID_DOMAIN = _halt.domain()


class FakeLaunchd:
    def __init__(self, jobs: dict, keepalive=(), stubborn=()):
        self.jobs = dict(jobs)          # label -> pid or None (loaded)
        self.disabled: set[str] = set()
        self.keepalive = set(keepalive)
        self.stubborn = set(stubborn)   # bootout does nothing
        self.calls: list[list[str]] = []

    def __call__(self, argv):
        argv = list(argv)
        self.calls.append(argv)
        verb = argv[1]
        out, err, rc = "", "", 0
        if verb == "list":
            rows = ["PID\tStatus\tLabel"] + [
                f"{pid if pid else '-'}\t0\t{lbl}" for lbl, pid in self.jobs.items()]
            out = "\n".join(rows)
        elif verb == "print-disabled":
            out = "disabled services = {\n" + "".join(
                f'\t"{l}" => disabled\n' for l in sorted(self.disabled)) + "}"
        elif verb == "disable":
            self.disabled.add(argv[2].rsplit("/", 1)[1])
        elif verb == "enable":
            self.disabled.discard(argv[2].rsplit("/", 1)[1])
        elif verb == "bootout":
            label = argv[2].rsplit("/", 1)[1]
            if label not in self.stubborn:
                self.jobs.pop(label, None)
                if label in self.keepalive and label not in self.disabled:
                    self.jobs[label] = 9999          # respawned
        elif verb == "bootstrap":
            label = Path(argv[3]).stem
            if label in self.disabled:
                rc, err = 119, "Service is disabled"
            elif label in self.jobs:
                rc, err = 37, "Operation already in progress"
            else:
                self.jobs[label] = 4242
        return subprocess.CompletedProcess(argv, rc, out, err)


@pytest.fixture
def env(tmp_path):
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    for lbl in ("com.lms.mail", "com.lms.watchfolder", "com.lms.brief",
                "com.lms.backup", "com.lms.haltbot", "ai.openclaw.gateway",
                "com.apple.unrelated"):
        (agents / f"{lbl}.plist").write_text("<plist/>")
    fake = FakeLaunchd(
        {"com.lms.mail": None, "com.lms.watchfolder": 101, "com.lms.brief": None,
         "com.lms.backup": None, "com.lms.haltbot": 102,
         "ai.openclaw.gateway": 103, "com.apple.unrelated": 104},
        keepalive={"ai.openclaw.gateway", "com.lms.watchfolder"})
    return fake, agents, tmp_path / "HALTED.json"


def do_halt(fake, agents, flag):
    return _halt.halt("test", run=fake, flag=flag, agents_dir=agents,
                      alive=lambda pid: False, kill=lambda *a: None)


def test_targets_are_every_lms_and_openclaw_job_except_the_switch(env):
    fake, agents, _ = env
    (agents / "com.lms.healthping.plist").write_text("<plist/>")   # installed, not loaded
    t = _halt.discover_targets(fake, agents)
    assert "com.lms.haltbot" not in t, "a halt that stops the kill switch"
    assert "com.apple.unrelated" not in t
    assert {"ai.openclaw.gateway", "com.lms.mail", "com.lms.healthping"} <= set(t)


def test_halt_stops_everything_and_survives_a_restart(env):
    fake, agents, flag = env
    r = do_halt(fake, agents, flag)
    assert r.ok, _halt.describe(r)
    assert set(fake.jobs) == {"com.lms.haltbot", "com.apple.unrelated"}
    assert set(r.targets) <= fake.disabled, "not disabled: back after next login"


def test_disable_comes_before_bootout_for_every_job(env):
    """Otherwise a KeepAlive job respawns in the gap. The fake does that."""
    fake, agents, flag = env
    do_halt(fake, agents, flag)
    verbs = [(c[1], c[2].rsplit("/", 1)[-1]) for c in fake.calls
             if c[1] in ("disable", "bootout")]
    first_bootout = next(i for i, (v, _) in enumerate(verbs) if v == "bootout")
    assert all(v == "disable" for v, _ in verbs[:first_bootout])
    assert {l for v, l in verbs if v == "disable"} >= {l for v, l in verbs if v == "bootout"}


def test_bootout_without_disable_would_have_failed():
    """Guards the fake: if it did not respawn, the test above proves nothing."""
    fake = FakeLaunchd({"ai.openclaw.gateway": 1}, keepalive={"ai.openclaw.gateway"})
    fake(["launchctl", "bootout", f"{UID_DOMAIN}/ai.openclaw.gateway"])
    assert "ai.openclaw.gateway" in fake.jobs


def test_the_flag_is_written_before_launchd_is_touched(env, monkeypatch):
    fake, agents, flag = env
    seen = []

    def spy(argv):
        if argv[1] in ("disable", "bootout"):
            seen.append(flag.exists())
        return fake(argv)
    do_halt(spy, agents, flag)
    assert seen and all(seen)
    data = json.loads(flag.read_text())
    assert "ai.openclaw.gateway" in data["targets"] and data["by"] == "test"


def test_a_job_that_will_not_unload_is_reported_not_hidden(env):
    fake, agents, flag = env
    fake.stubborn.add("com.lms.mail")
    r = do_halt(fake, agents, flag)
    assert not r.ok and r.still_running == ["com.lms.mail"]
    assert "STILL RUNNING: mail" in _halt.describe(r)


def test_resume_brings_back_exactly_what_was_halted(env):
    fake, agents, flag = env
    do_halt(fake, agents, flag)
    started, problems = _halt.resume(run=fake, flag=flag, agents_dir=agents)
    assert not problems
    assert set(started) == {"com.lms.mail", "com.lms.watchfolder", "com.lms.brief",
                            "com.lms.backup", "ai.openclaw.gateway"}
    assert not fake.disabled
    assert not flag.exists()
    assert list(flag.parent.glob("halted-*.json")), "the halt left no history"


def test_a_partial_resume_still_reads_as_halted(env):
    fake, agents, flag = env
    do_halt(fake, agents, flag)
    (agents / "com.lms.brief.plist").unlink()
    _, problems = _halt.resume(run=fake, flag=flag, agents_dir=agents)
    assert problems and flag.exists()


def test_a_corrupt_flag_means_halted(tmp_path):
    flag = tmp_path / "HALTED.json"
    flag.write_text("{not json")
    assert _halt.read_flag(flag) is not None


def test_both_print_disabled_formats():
    out = '\t"com.lms.mail" => disabled\n\t"com.lms.brief" => true\n\t"x" => enabled\n'
    assert _halt.parse_disabled(out) == {"com.lms.mail", "com.lms.brief"}


# ---------------------------------------------------------------------------
# The bot: who is obeyed
# ---------------------------------------------------------------------------

OWNER = 555000111


def msg(text, frm=OWNER, chat=OWNER, kind="private"):
    return {"text": text, "from": {"id": frm}, "chat": {"id": chat, "type": kind},
            "date": 0}


@pytest.mark.parametrize("text,want", [
    ("/halt", "halt"), ("/HALT", "halt"), ("/halt@MREOC18bot", "halt"),
    ("/halt now", "halt"), ("/halting", "halting"), ("halt", None),
    ("/", None), ("", None), (None, None)])
def test_command_parsing(text, want):
    assert halt_bot.command_of(text) == want


@pytest.mark.parametrize("m,ok", [
    (msg("/halt"), True),
    (msg("/halt", frm=1), False),                       # someone else
    (msg("/halt", chat=-100, kind="group"), False),     # owner, but in a group
    (msg("/halt", chat=1), False),                      # owner id, wrong chat
    ({"text": "/halt", "chat": {"id": OWNER, "type": "private"}}, False),
])
def test_only_the_owner_in_a_private_chat_is_obeyed(m, ok):
    assert halt_bot.authorised(m, OWNER) is ok


class FakeTG:
    def __init__(self):
        self.sent = []

    def send(self, chat, text):
        self.sent.append((chat, text))


def test_a_stranger_gets_no_reply_and_no_halt(monkeypatch):
    called = []
    monkeypatch.setattr(halt_bot, "do_halt", lambda *a: called.append(1))
    tg = FakeTG()
    halt_bot.handle(msg("/halt", frm=42, chat=42), OWNER, tg, set())
    assert not called and not tg.sent


def test_the_owner_halts(monkeypatch):
    r = _halt.HaltResult(elapsed=1.2, targets=["com.lms.mail"])
    monkeypatch.setattr(halt_bot, "do_halt", lambda d: ("HALTED in 1.2s.", r, 0.4))
    monkeypatch.setattr(halt_bot, "record", lambda *a: None)
    tg = FakeTG()
    halt_bot.handle(msg("/halt"), OWNER, tg, set())
    assert [t for _, t in tg.sent][-1] == "HALTED in 1.2s."
    assert all(c == OWNER for c, _ in tg.sent)


def test_there_is_no_remote_resume(monkeypatch):
    """D-059: a hijacked phone can stop the system, never restart it."""
    monkeypatch.setattr(_halt, "resume", lambda **k: pytest.fail("resumed remotely"))
    tg = FakeTG()
    halt_bot.handle(msg("/resume"), OWNER, tg, set())
    assert "at the Mac" in tg.sent[-1][1]


def test_the_token_never_reaches_an_error_message(monkeypatch):
    import urllib.error
    import urllib.request

    def boom(url, **kw):
        raise urllib.error.URLError(f"failed for {url}")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    tg = halt_bot.Telegram("123:SECRETSECRET")
    with pytest.raises(RuntimeError) as e:
        tg.call("getMe")
    # URLError's reason here embeds the URL on purpose, to prove the risk.
    assert "SECRETSECRET" not in str(e.value) or pytest.fail(
        "token leaked into an exception that gets logged")


def test_the_poll_loop_runs_against_the_real_call_signature(monkeypatch):
    """Oct 6: the daemon died on its first poll on the Mac with "got multiple
    values for keyword argument 'timeout'". getUpdates takes a `timeout` of
    its own, and call() had an HTTP timeout of the same name. Every helper was
    tested; the loop that joins them was not. This drives serve() through the
    real Telegram.call, with only urlopen faked."""
    import io
    import urllib.parse
    import urllib.request

    seen = []

    def fake_urlopen(url, data=None, timeout=None):
        body = dict(urllib.parse.parse_qsl(data.decode()))
        seen.append((url.rsplit("/", 1)[1], body, timeout))
        result = ([{"update_id": 7, "message": msg("/status")}]
                  if url.endswith("getUpdates") else {})
        return io.BytesIO(json.dumps({"ok": True, "result": result}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(halt_bot, "do_status", lambda: "Running (not halted).")
    halt_bot.serve(halt_bot.Telegram("1:x"), OWNER, max_polls=2)

    polls = [x for x in seen if x[0] == "getUpdates"]
    assert len(polls) == 2
    assert polls[0][1]["timeout"] == str(halt_bot.POLL_S)       # Telegram's
    assert polls[0][2] == halt_bot.POLL_S + 15                  # the HTTP one
    assert polls[1][1]["offset"] == "8", "the update was never confirmed"
    sends = [x for x in seen if x[0] == "sendMessage"]
    assert sends and sends[0][1]["text"] == "Running (not halted)."



class Proc:
    """Processes that exit a set time after SIGTERM, on a fake clock."""

    def __init__(self, exit_after: dict[int, float]):
        self.now = 0.0
        self.exit_after = exit_after          # pid -> seconds after bootout
        self.term_at: float | None = None
        self.killed: list[int] = []

    def clock(self):
        return self.now

    def sleep(self, s):
        self.now += s

    def alive(self, pid):
        if pid in self.killed:
            return False
        if self.term_at is None:
            return True
        return self.now - self.term_at < self.exit_after.get(pid, 0)


def halt_with(proc: Proc, fake, agents, flag):
    def run(argv):
        if argv[1] == "bootout" and proc.term_at is None:
            proc.term_at = proc.now           # returns at once, like Tahoe
        return fake(argv)

    def kill(pgid, sig):
        proc.killed.append(pgid)
    return _halt.halt("test", run=run, flag=flag, agents_dir=agents,
                      clock=proc.clock, alive=proc.alive, kill=kill,
                      sleep=proc.sleep)


def test_a_job_gets_its_graceful_exit_before_anything_is_killed(env, monkeypatch):
    """Oct 6, first run on the Mac: bootout returned in 0.0 s, the halt
    checked immediately, and SIGKILLed a gateway that was in the middle of
    shutting down cleanly. A job that exits within the deadline is never
    killed."""
    fake, agents, flag = env
    monkeypatch.setattr(_halt, "_force_kill", lambda pid, kill: kill(pid, 9))
    proc = Proc({103: 1.5, 101: 0.4})        # gateway 1.5 s, watcher 0.4 s
    r = halt_with(proc, fake, agents, flag)
    assert r.killed == [] and r.ok, _halt.describe(r)
    assert 1.5 <= r.elapsed < 2.0


def test_only_a_job_that_overstays_the_deadline_is_killed(env, monkeypatch):
    fake, agents, flag = env
    monkeypatch.setattr(_halt, "_force_kill", lambda pid, kill: kill(pid, 9))
    proc = Proc({103: 60.0, 101: 0.4})       # the gateway hangs
    r = halt_with(proc, fake, agents, flag)
    assert r.killed == ["ai.openclaw.gateway"]
    assert r.still_running == [] and r.ok
    assert r.elapsed <= _halt.LIMIT_S


def test_a_zombie_is_not_alive(monkeypatch):
    """kill(pid, 0) succeeds on a zombie; ps says Z."""
    monkeypatch.setattr(_halt.os, "kill", lambda pid, sig: None)
    monkeypatch.setattr(_halt.subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess(a, 0, "Z    \n", ""))
    assert _halt._alive(1234) is False
    monkeypatch.setattr(_halt.subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess(a, 0, "Ss   \n", ""))
    assert _halt._alive(1234) is True


def test_a_process_group_is_killed_only_when_the_job_leads_it(monkeypatch):
    calls = []
    monkeypatch.setattr(_halt.os, "kill", lambda pid, sig: calls.append(("pid", pid)))
    monkeypatch.setattr(_halt.os, "getpgid", lambda p: {0: 50, 500: 500, 600: 50}[p])
    _halt._force_kill(500, kill=lambda g, s: calls.append(("group", g)))
    _halt._force_kill(600, kill=lambda g, s: calls.append(("group", g)))
    assert calls == [("group", 500), ("pid", 600)], (
        "pid 600 shares group 50 with this process; killpg(50) kills the halt")
