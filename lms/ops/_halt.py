"""The kill switch: stop every job that can act, and keep it stopped.

Used by ops/halt_bot.py (/halt from Matthew's phone) and ops/halt.py (the same
thing typed at the Mac). One code path, so the thing tested locally is the
thing the phone triggers.

WHAT "HALT" HAS TO MEAN (spec §8.9, acceptance A16)
---------------------------------------------------
"Within 10 seconds stops all cron jobs, disables all outbound channels ... and
puts the gateway into read-only mode." A16: "All autonomous action stops
within 10 s; confirmed by log."

The first Phase 7 plan measured `openclaw gateway stop`. That would have passed
while the system kept working: nothing that acts on its own goes through the
gateway. Mail, the watched folder, the brief and the backup are launchd jobs
that call LM Studio from core/ directly (D-056). Stopping the gateway alone
stops the one component that currently does nothing unattended. So the target
set is every LaunchAgent in the LMS and OpenClaw namespaces, discovered rather
than listed, so a job added next month is halted without anyone remembering to
add it here.

WHY `disable` AND NOT ONLY `bootout`
------------------------------------
`launchctl bootout` unloads a job until the next login. Every plist in
~/Library/LaunchAgents is loaded again at login, so after a power cut a halted
system would quietly start itself again, which is the one outcome a kill switch
must not have. `launchctl disable` is persistent (launchd keeps it in its own
database, not in the plist), and launchd refuses to load a disabled job.

Disable comes BEFORE bootout, for every job. A KeepAlive job (the gateway, the
watcher) that is booted out while still enabled can be respawned in the gap.

WHY THE FLAG FILE IS WRITTEN FIRST
----------------------------------
If this process dies halfway (the Mac loses power mid-halt), the record of
what was being halted must already exist, or `halt.py --resume` cannot know
what to bring back. It is on the internal disk (~/LMS), not the array: the
kill switch must work on the day the array is not mounted.

RESUME IS LOCAL ONLY (decided Oct 6, D-059)
-------------------------------------------
A stolen or hijacked phone can stop the system; it can never restart it.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence

SELF_LABEL = "com.lms.haltbot"
PREFIXES = ("com.lms.", "ai.openclaw.")

# Bootout waits for the job to exit, and launchd's default ExitTimeOut is 20 s,
# twice the whole budget. Anything still alive at this point is SIGKILLed.
BOOTOUT_DEADLINE_S = 7.0
LIMIT_S = 10.0

LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
FLAG = Path.home() / "LMS" / "HALTED.json"

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess]


def _run(argv: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(list(argv), capture_output=True, text=True, timeout=30)


def domain() -> str:
    return f"gui/{os.getuid()}"


def is_target(label: str) -> bool:
    return label.startswith(PREFIXES) and label != SELF_LABEL


# ---------------------------------------------------------------------------
# Reading launchd
# ---------------------------------------------------------------------------

def parse_list(stdout: str) -> dict[str, int | None]:
    """`launchctl list` -> {label: pid or None}. Columns: PID  Status  Label."""
    jobs: dict[str, int | None] = {}
    for line in stdout.splitlines()[1:]:
        parts = line.split(None, 2)
        if len(parts) != 3:
            continue
        pid, _status, label = parts
        jobs[label.strip()] = int(pid) if pid.isdigit() else None
    return jobs


def parse_disabled(stdout: str) -> set[str]:
    """`launchctl print-disabled gui/N` -> labels currently disabled.

    macOS has printed both `"x" => true` and `"x" => disabled` over the years.
    """
    out = set()
    for m in re.finditer(r'"([^"]+)"\s*=>\s*(\w+)', stdout):
        if m.group(2) in ("true", "disabled"):
            out.add(m.group(1))
    return out


def loaded(run: Runner = _run) -> dict[str, int | None]:
    return parse_list(run(["launchctl", "list"]).stdout)


def disabled(run: Runner = _run) -> set[str]:
    return parse_disabled(run(["launchctl", "print-disabled", domain()]).stdout)


def discover_targets(run: Runner = _run, agents_dir: Path = LAUNCH_AGENTS) -> list[str]:
    """Every LMS/OpenClaw job, loaded or merely installed, except this one.

    Installed-but-not-loaded counts too: it would load at the next login.
    """
    labels = {lbl for lbl in loaded(run) if is_target(lbl)}
    if agents_dir.is_dir():
        labels |= {p.stem for p in agents_dir.glob("*.plist") if is_target(p.stem)}
    return sorted(labels)


# ---------------------------------------------------------------------------
# The flag
# ---------------------------------------------------------------------------

def read_flag(flag: Path = FLAG) -> dict | None:
    try:
        return json.loads(flag.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        # A corrupt flag still means "halted". Never read damage as "running".
        return {"ts": "?", "by": "?", "targets": [], "corrupt": True}


def write_flag(data: dict, flag: Path = FLAG) -> None:
    flag.parent.mkdir(parents=True, exist_ok=True)
    tmp = flag.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, flag)


# ---------------------------------------------------------------------------
# Halt
# ---------------------------------------------------------------------------

@dataclass
class HaltResult:
    elapsed: float
    targets: list[str]
    still_running: list[str] = field(default_factory=list)
    killed: list[str] = field(default_factory=list)
    already_halted: bool = False

    @property
    def ok(self) -> bool:
        return not self.still_running and self.elapsed <= LIMIT_S


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def halt(by: str, *, run: Runner = _run, flag: Path = FLAG,
         agents_dir: Path = LAUNCH_AGENTS, clock=time.monotonic,
         alive=_alive, kill=os.killpg) -> HaltResult:
    t0 = clock()
    before = loaded(run)
    targets = discover_targets(run, agents_dir)
    prior = read_flag(flag)

    # 1. The record, before any action (see module docstring).
    recorded = sorted(set(targets) | set((prior or {}).get("targets", [])))
    write_flag({
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "by": by,
        "targets": recorded,
        "previous": prior,
    }, flag)

    # 2. Persistently disable everything first, so nothing can respawn.
    for label in targets:
        run(["launchctl", "disable", f"{domain()}/{label}"])

    # 3. Unload, concurrently: each bootout may block until its job exits.
    threads = [threading.Thread(
        target=lambda l=label: run(["launchctl", "bootout", f"{domain()}/{l}"]),
        daemon=True) for label in targets if label in before]
    for t in threads:
        t.start()
    deadline = t0 + BOOTOUT_DEADLINE_S
    for t in threads:
        t.join(max(0.0, deadline - clock()))

    # 4. Anything that ignored SIGTERM gets SIGKILL, whole process group.
    killed = []
    for label in targets:
        pid = before.get(label)
        if pid and alive(pid):
            try:
                kill(os.getpgid(pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
            killed.append(label)

    # 5. Verify from launchd's side, not from what we asked for. Still LOADED
    #    counts as a failure even with no pid: a loaded scheduled job is one
    #    that fires at its next calendar slot.
    after = loaded(run)
    still = sorted(lbl for lbl in targets
                   if lbl in after or alive(before.get(lbl)))

    return HaltResult(elapsed=clock() - t0, targets=targets,
                      still_running=still, killed=killed,
                      already_halted=prior is not None)


# ---------------------------------------------------------------------------
# Resume (local only)
# ---------------------------------------------------------------------------

def resume(*, run: Runner = _run, flag: Path = FLAG,
           agents_dir: Path = LAUNCH_AGENTS) -> tuple[list[str], list[str]]:
    """Re-enable and reload exactly what the halt recorded.

    Returns (started, problems). The flag is removed only if every job came
    back, so a partial resume still reads as halted.
    """
    state = read_flag(flag)
    if state is None:
        return [], ["not halted (no flag file)"]
    targets = state.get("targets") or discover_targets(run, agents_dir)

    started, problems = [], []
    for label in targets:
        run(["launchctl", "enable", f"{domain()}/{label}"])
        plist = agents_dir / f"{label}.plist"
        if not plist.exists():
            problems.append(f"{label}: no plist at {plist}, enabled but not loaded")
            continue
        r = run(["launchctl", "bootstrap", domain(), str(plist)])
        # 5 / 37 / "already" = already loaded, which is the state we want.
        if r.returncode == 0 or "already" in (r.stderr or "").lower() \
                or r.returncode in (5, 37):
            started.append(label)
        else:
            problems.append(f"{label}: bootstrap exit {r.returncode}: "
                            f"{(r.stderr or '').strip()}")

    if not problems:
        history = flag.with_name(
            f"halted-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json")
        os.replace(flag, history)
    return started, problems


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def status(*, run: Runner = _run, flag: Path = FLAG,
           agents_dir: Path = LAUNCH_AGENTS) -> dict:
    jobs = loaded(run)
    off = disabled(run)
    rows = []
    for label in discover_targets(run, agents_dir):
        if label in off:
            state = "disabled"
        elif label in jobs:
            state = f"running (pid {jobs[label]})" if jobs[label] else "loaded"
        else:
            state = "not loaded"
        rows.append((label, state))
    return {"halted": read_flag(flag), "jobs": rows,
            "haltbot": jobs.get(SELF_LABEL, "absent")}


def short(label: str) -> str:
    """com.lms.mail -> mail, ai.openclaw.gateway -> gateway."""
    return label.split(".", 2)[-1]


def describe(r: HaltResult) -> str:
    names = ", ".join(short(t) for t in r.targets) or "nothing was installed"
    if r.ok:
        head = f"HALTED in {r.elapsed:.1f}s."
    elif r.still_running:
        head = (f"HALT INCOMPLETE after {r.elapsed:.1f}s. STILL RUNNING: "
                f"{', '.join(short(s) for s in r.still_running)}. "
                f"Someone must go to the Mac.")
    else:
        head = f"HALTED, but slowly: {r.elapsed:.1f}s (limit {LIMIT_S:.0f}s)."
    lines = [head, f"Stopped: {names}."]
    if r.killed:
        lines.append(f"Force-killed (ignored the stop): "
                     f"{', '.join(short(k) for k in r.killed)}.")
    lines.append("Nothing was deleted or sent. It stays stopped through a restart.")
    lines.append("Resume only at the Mac: ./ops/halt.py --resume")
    return "\n".join(lines)
