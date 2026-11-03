#!/usr/bin/env python3
"""Are the resident models loaded and answering? Every five minutes (R34).

    ./ops/model_health.py            # what com.lms.modelhealth runs, every 300 s

Spec §4.6.5: "Keep-alive indefinite for all resident tiers; health-ping every
5 minutes with reload + alert on failure." Addendum §5 asks for alerting on
failed jobs.

WHY
---
Every unattended job reaches a model through LM Studio, and nothing else
notices when a model is no longer there. An evicted classifier does not stop
the watcher or the mail poll: each document fails classification and is
quarantined as "model error", and the brief reports a quiet day. The drafter
(TIER-L2) has already been found unloaded on the Mac and put back by hand with
`lms load`. This does that unattended, and leaves a record.

PER RESIDENT TIER
-----------------
  1. Its state, from LM Studio's /api/v0/models (LMStudio.model_states).
  2. Not loaded: `lms load <id> --context-length 32768`, then read the state
     again until it says loaded.
  3. A tiny completion through the adapter. Loaded is not the same as
     answering.

The state is read BEFORE the probe on purpose. A completion request for a
model that is not loaded makes LM Studio load it just in time, at its default
context length and with its idle TTL, which is one way a "resident" model
comes to be evicted in the first place. `lms load` with no TTL is the
"keep-alive indefinite" of §4.6.5.

WHICH TIERS
-----------
Those marked `"resident": true` in openclaw/agents/agents.fragment.json, which
is TIER-L1 (classifier, orchestrator) and TIER-L2 (drafter) today. The model
ids come from core/adapters/lmstudio.py TIER_MODELS, which is what the adapter
actually sends; tests/test_agents.py keeps the two in agreement. The spec's
ladder also lists OCR, ASR and embedding as resident, but on this machine they
are not (the fragment says so), and loading them every five minutes would pin
memory for work that is not running.

CONTEXT LENGTH
--------------
32768 is the contextWindow OpenClaw budgets prompts against
(ops/phase6b.patch.json5). Loading smaller would let the gateway send prompts
the server truncates. tests/test_model_health.py keeps the two equal.

A LOADED MODEL THAT DOES NOT ANSWER IS REPORTED, NOT RELOADED
-------------------------------------------------------------
Unloading a 70 GB model mid-request would kill a draft in progress, and a
probe that timed out behind a long request is a busy model, not a broken one.
The next run, five minutes later, probes again; a failure that persists stays
in the brief until a run succeeds.

THE ALERT
---------
There is no push channel to Matthew yet: Telegram push is R10. So the alert is
the brief's "THE SYSTEM NEEDS ATTENTION" section (core/reports/brief.py,
D-051): a MODEL_HEALTH_FAILED with no later MODEL_HEALTH_OK is printed there,
and a job that stops running at all shows as stalled. The exit status is
non-zero on failure, so `launchctl list` shows it too.

WHAT IT WRITES (actions_log)
----------------------------
  MODEL_RELOADED       every reload, always. A reload is a side effect.
  MODEL_HEALTH_FAILED  a resident tier could not be recovered, and why.
  MODEL_HEALTH_OK      every resident tier loaded and answering.

An unchanged outcome is written at most once an hour. 288 rows a day saying
"fine" would bury the side effects the log exists to record, and one an hour
is plenty to arm the stall alarm, which is read twice a day.

HALT
----
/halt (D-059) disables this job with the rest. Run by hand while
~/LMS/HALTED.json exists, it does nothing: a reload is the system acting.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _env import archive_volume_problem, load_lms_env                # noqa: E402
from _reexec import ensure_venv                                      # noqa: E402
from core.adapters.lmstudio import TIER_MODELS, LMStudio, ModelError  # noqa: E402

FRAGMENT = (Path(__file__).resolve().parents[1]
            / "openclaw" / "agents" / "agents.fragment.json")

CONTEXT_LENGTH = 32768          # = contextWindow in ops/phase6b.patch.json5
LOAD_TIMEOUT_S = 600            # the 122B takes minutes to come off disk
PROBE_TIMEOUT_S = 120           # waits behind a request already running
PROBE_TOKENS = 16
RECHECKS = 5                    # state reads after a load, 2 s apart
HEARTBEAT = timedelta(hours=1)  # an unchanged outcome is rewritten this often

OK = "MODEL_HEALTH_OK"
RELOADED = "MODEL_RELOADED"
FAILED = "MODEL_HEALTH_FAILED"

Loader = Callable[[str], "tuple[bool, str]"]


# ---------------------------------------------------------------------------
# What to check, and how to reload it
# ---------------------------------------------------------------------------

def resident_tiers(fragment: Path = FRAGMENT) -> dict[str, str]:
    """{tier: model id} for every tier the fragment marks resident."""
    tiers = json.loads(fragment.read_text(encoding="utf-8"))["models"]["tiers"]
    return {tier: TIER_MODELS[tier] for tier, body in tiers.items()
            if isinstance(body, dict) and body.get("resident")}


def find_cli() -> str | None:
    """LM Studio's own CLI, `lms` (no relation to this project's name).

    Its installer puts it in ~/.lmstudio/bin and adds that to the login
    shell's PATH, which launchd does not read. So the home path first.
    """
    home = Path.home() / ".lmstudio" / "bin" / "lms"
    if home.exists():
        return str(home)
    return shutil.which("lms")


def _last_line(text: str) -> str:
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    return lines[-1][:200] if lines else ""


def lms_load(model: str, *, run=subprocess.run,
             cli: str | None = None) -> tuple[bool, str]:
    """`lms load <model> --context-length 32768`, the command used by hand.

    No --ttl: a model loaded without one stays until it is unloaded. stdin is
    closed so a prompt from the CLI fails instead of hanging the job.
    """
    cli = cli or find_cli()
    if not cli:
        return False, "LM Studio CLI (lms) not found in ~/.lmstudio/bin or on PATH"
    argv = [cli, "load", model, "--context-length", str(CONTEXT_LENGTH)]
    try:
        p = run(argv, capture_output=True, text=True, timeout=LOAD_TIMEOUT_S,
                stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return False, f"lms load timed out after {LOAD_TIMEOUT_S}s"
    except OSError as exc:
        return False, f"could not run {cli}: {exc}"
    said = _last_line((p.stdout or "") + "\n" + (p.stderr or ""))
    if p.returncode != 0:
        return False, f"lms load exit {p.returncode}: {said}"
    return True, said


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------

@dataclass
class TierResult:
    tier: str
    model: str
    ok: bool
    reason: str = ""            # why it failed
    reloaded: bool = False
    was: str = ""               # the state that made it reload
    load_said: str = ""         # what `lms load` printed last
    elapsed_s: float = 0.0      # the probe

    def line(self) -> str:
        head = f"{self.tier} {self.model}: "
        if self.ok:
            return head + (f"OK after reload ({self.elapsed_s:.1f}s)" if self.reloaded
                           else f"OK ({self.elapsed_s:.1f}s)")
        return head + "FAILED, " + self.reason


def _state(client, model: str) -> str:
    return client.model_states().get(model, "absent")


def check_tier(client, tier: str, model: str, *, load: Loader,
               sleep=time.sleep) -> TierResult:
    try:
        state = _state(client, model)
    except ModelError as exc:
        return TierResult(tier, model, False, reason=f"LM Studio not answering: {exc}")

    if state == "absent":
        # A load cannot fix a wrong id; LM Studio may have prefixed `author/`
        # (PHASE6B_APPLY.md step 2). Say so rather than try.
        return TierResult(tier, model, False,
                          reason="not in LM Studio's model list (id changed?)")

    reload: dict = {}
    if state != "loaded":
        done, said = load(model)
        if not done:
            return TierResult(tier, model, False,
                              reason=f"was {state}; reload failed: {said}")
        reload = {"reloaded": True, "was": state, "load_said": said}
        for i in range(RECHECKS):
            if i:
                sleep(2)
            try:
                state = _state(client, model)
            except ModelError as exc:
                state = f"unreadable ({exc})"
            if state == "loaded":
                break
        else:
            return TierResult(tier, model, False, **reload,
                              reason=f"reloaded, but LM Studio still says {state}")

    started = time.monotonic()
    try:
        client.complete(tier=tier, system="Health check. Reply with the word ok.",
                        user="ping", max_tokens=PROBE_TOKENS, retries=0)
    except (ModelError, OSError, ValueError) as exc:
        return TierResult(tier, model, False, **reload,
                          reason=f"loaded but not answering: {exc}"[:300])
    return TierResult(tier, model, True, **reload,
                      elapsed_s=time.monotonic() - started)


def check(client, tiers: dict[str, str], *, load: Loader,
          sleep=time.sleep) -> list[TierResult]:
    if not tiers:
        return [TierResult("-", "-", False,
                           reason="no resident tier in agents.fragment.json")]
    return [check_tier(client, t, m, load=load, sleep=sleep)
            for t, m in tiers.items()]


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------

def last_outcome(conn) -> tuple[str, str, str] | None:
    """(action, ts, detail) of the most recent OK or FAILED row."""
    row = conn.execute(
        "SELECT action, ts, detail FROM actions_log WHERE action IN (?, ?) "
        "ORDER BY id DESC LIMIT 1", (OK, FAILED)).fetchone()
    return (row[0], row[1], row[2] or "") if row else None


def _age(ts: str, now: datetime) -> timedelta:
    try:
        then = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return timedelta.max
    if then.tzinfo is None:
        then = then.replace(tzinfo=now.tzinfo)
    return now - then


def rows_to_write(results: list[TierResult], last: tuple[str, str, str] | None,
                  now: datetime) -> list[tuple[str, str]]:
    """What this run adds to actions_log. Pure, so the policy is testable.

    Reloads always. The outcome when it changed, when its reason changed, when
    something was reloaded, or when the last one is an hour old.
    """
    rows = []
    for r in results:
        if r.reloaded:
            said = f": {r.load_said}" if r.load_said else ""
            rows.append((RELOADED, f"{r.tier} {r.model} was {r.was}; reloaded "
                                   f"with context {CONTEXT_LENGTH}{said}"))

    failed = [r for r in results if not r.ok]
    if failed:
        outcome = FAILED
        detail = "; ".join(f"{r.tier} {r.model}: {r.reason}" for r in failed)
    else:
        outcome = OK
        detail = ", ".join(f"{r.tier} {r.elapsed_s:.1f}s" for r in results)

    if (rows or last is None or last[0] != outcome
            or (outcome == FAILED and last[2] != detail[:400])
            or _age(last[1], now) >= HEARTBEAT):
        rows.append((outcome, detail))
    return [(a, d[:400]) for a, d in rows]


def record(conn, results: list[TierResult], now: datetime) -> list[tuple[str, str]]:
    from core.db import database as db
    rows = rows_to_write(results, last_outcome(conn), now)
    for action, detail in rows:
        db.log_action(conn, action, detail=detail)
    return rows


def run(client, tiers: dict[str, str], conn, *, load: Loader = lms_load,
        sleep=time.sleep, now: datetime | None = None) -> int:
    """Check, print, record. Returns the exit status."""
    results = check(client, tiers, load=load, sleep=sleep)
    for r in results:
        print(r.line())
    if conn is not None:
        record(conn, results, now or datetime.now().astimezone())
    else:
        print("result not recorded in the database (see above)", file=sys.stderr)
    return 0 if all(r.ok for r in results) else 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def halt_flag() -> dict | None:
    """~/LMS/HALTED.json, through the kill switch's own reader, so a corrupt
    flag reads as halted here too.

    Imported here, not at the top: _halt binds os.killpg at import, which is
    POSIX-only, and the tests import this module on the development machine.
    """
    import _halt
    return _halt.read_flag()


def open_db():
    """The database, or None if it cannot be reached. A model check still
    runs, and still reloads, with the array unmounted; it just cannot say so
    in the log."""
    problem = archive_volume_problem()
    if problem:
        print(f"archive unavailable: {problem}", file=sys.stderr)
        return None
    try:
        from core.db import database as db
        archive = os.environ["LMS_ARCHIVE_ROOT"]
        return db.connect(os.environ.get("LMS_DB",
                                         str(Path(archive).parent / "lms.db")))
    except Exception as exc:                                  # noqa: BLE001
        print(f"could not open the database: {exc}", file=sys.stderr)
        return None


def main() -> int:
    ensure_venv("LMS_MODELHEALTH_REEXEC", script=__file__)
    load_lms_env()

    flag = halt_flag()
    if flag is not None:
        print(f"HALTED since {flag.get('ts')} (by {flag.get('by')}): "
              f"models not checked or reloaded.")
        return 0

    conn = open_db()
    try:
        return run(LMStudio(timeout=PROBE_TIMEOUT_S), resident_tiers(), conn)
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
