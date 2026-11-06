# Chat Control over WhatsApp and Telegram — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Matthew controls the LMS from WhatsApp (his own number, self-chat) and Telegram (@MREOCbot) with a fixed `/lms` command list carried out by code, and plain-text questions are answered read-only by the tool-less local agent.

**Architecture:** An OpenClaw plugin (`lms-control`) registers `/lms` (and `/halt` if free); its handler only runs `ops/remote.py` with `execFile`, no shell. `ops/remote.py` holds all the logic: second owner check, argument validation, audit, halt refusal, the two-step `yes` for changes. `ops/agent_context.py` writes an hourly read-only snapshot into a file OpenClaw injects into `lms-orchestrator`'s prompt. `ops/remote.patch.json5` adds the WhatsApp channel, the routing, the owners and the plugin.

**Tech Stack:** Python 3.12 (stdlib + the existing `core/` package), pytest, Node ESM for the plugin (OpenClaw 2026.7.1-2 loads it), launchd, OpenClaw config patches (JSON5).

**Spec:** [docs/superpowers/specs/2026-11-05-chat-control-design.md](../specs/2026-11-05-chat-control-design.md)

## Global Constraints

- Code carries out every action; the model never chooses one. `lms-orchestrator` keeps **no tools** (D-056); `verify_setup` [1]/[2] and `ops/agent_tools.py` must still pass.
- Command list is exactly: `help status brief tasks coverage mail whatsapp backup done approve deny halt`. No `resume`, `revise`, `dismiss`, `reopen`, shell, config, or messages to anyone else.
- `done`, `approve`, `deny` act only when the same message ends with `yes`; without it they only show the item.
- Numbers: `^[0-9]{1,6}$`. Only other accepted argument words: `overdue` (for `tasks`), `yes`.
- Owners: Telegram `8783061626` (Keychain `lms/telegram-owner-id`); WhatsApp his number (Keychain `lms/whatsapp-owner`). A non-owner gets **no reply at all**.
- Reply at most **3,500** characters, cut with `… more on the Dashboard.`
- Plugin calls Python with `execFile` (never `exec`, never `shell: true`), 60 s timeout, working directory `/Users/mleca/lms-repo/lms`.
- Snapshot at most **15,000** characters, written atomically between `<!-- LMS_NOW:BEGIN ... -->` and `<!-- LMS_NOW:END -->`; nothing written while halted.
- Private tasks (R15, `reports.private_task_ids`) are never named in a reply or the snapshot: use `ScoredTask.shown_title` / `reports.private_line`.
- His WhatsApp number never appears in the repo: patches carry `__WHATSAPP_OWNER__`, substituted at apply time from the Keychain.
- OpenClaw config keys come from `openclaw config schema` on the Mac, never from docs (D-021, D-056).
- `ops/_halt.py` does not import on Windows (`os.killpg`): import it only inside Mac-only code paths; tests inject.
- Run tests from `lms/`: `<venv>/python -m pytest -o addopts="" -p no:cacheprovider --ignore=tests/test_halt.py -q`. New ops scripts: `git add --chmod=+x`.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Line endings LF.

## File Structure

| File | Responsibility |
|---|---|
| `lms/ops/agent_context.py` (new, executable) | Builds the read-only snapshot; writes it between markers into the injected workspace file; `refresh()` used by `remote.py`; `read_halt_flag()` shared |
| `lms/ops/com.lms.agentcontext.plist` (new) | Hourly launchd job for the snapshot |
| `lms/ops/remote.py` (new, executable) | Every `/lms` command: owner check, parsing, audit, halt refusal, two-step changes, reply cap |
| `lms/ops/halt.py` (modify) | `--by telegram|whatsapp|local` so a chat-started halt is labelled |
| `lms/openclaw-plugin/lms-control/openclaw.plugin.json`, `index.js` (new) | Registers `/lms` (+ `/halt`); runs `remote.py` |
| `lms/ops/remote.patch.json5` (new) | WhatsApp channel, bindings, owners, plugin enable |
| `lms/ops/verify_setup.py` (modify) | `[12] Chat control`: WhatsApp allowlist of one = Keychain owner, plugin enabled, snapshot job loaded |
| `lms/tests/test_agent_context.py`, `test_remote.py`, `test_remote_patch.py` (new) | Tests |
| `lms/ops/CHAT_CONTROL_APPLY.md` (new), `DECISIONS.md` (D-080), `REQUIREMENTS_REMAINING.md` (R10, R11), `PHASES.md` (C2) | Docs |

---

### Task 1: Mac facts, before any code (gate)

Nothing in Tasks 2–6 is final until this returns. The engineer does not run anything; the user runs these on the Mac and pastes the output.

**Files:**
- Modify: this plan, section "Mac facts (filled in by Task 1)" at the end.

- [x] **Step 1: Send the user these commands** (from `~/lms-repo/lms`, after `git pull`):

```bash
openclaw --version
```

```bash
.venv/bin/python ops/schema_dump.py | grep -E '^(channels\.whatsapp|plugins|commands|agents\.defaults\.bootstrap)' | head -120
```

```bash
openclaw plugins --help
```

```bash
grep -rn "registerCommand\|reserved\|RESERVED" "$(dirname "$(readlink -f "$(which openclaw)")")/../lib/node_modules/openclaw/dist" 2>/dev/null | grep -i "command" | head -40
```

```bash
grep -rln "bootstrapMaxChars\|AGENTS.md\|USER.md" "$(dirname "$(readlink -f "$(which openclaw)")")/../lib/node_modules/openclaw/dist" 2>/dev/null | head -5
```

```bash
ls -la ~/.openclaw/workspace-lms-orchestrator/
```

(If `schema_dump.py` takes arguments, use `--help` first; it was used for Phase 7b on Oct 6.)

- [x] **Step 2: From the output, fill in "Mac facts" at the end of this plan:**
  1. The exact `channels.whatsapp` keys for allowlist, own number, self-chat, groups, config writes.
  2. The `plugins` keys to load a local folder and enable it.
  3. The plugin entry file names and manifest fields OpenClaw expects, and whether `.js` ESM with `export default function register(api)` loads.
  4. `registerCommand` options and the handler's context field names for channel, sender and arguments; what returning `{ text: "" }` does.
  5. Whether `lms` and `halt` are free names.
  6. Which workspace files OpenClaw injects into the prompt, and the per-file character limit.
  Done Nov 5 with `ops/chat_control_facts.sh`; rows 4, 5 and parts of 1–3 were left open (its greps for the handler context and reserved names found nothing). Second pass: `ops/plugin_api_facts.sh`, which reads the `.d.ts` types, a bundled plugin that calls `registerCommand`, the current `plugins`/`commands`/`bindings` config and the WhatsApp doc.
- [x] **Step 3: Adjust before Task 2.** If any fact differs from what Tasks 2–6 assume (`USER.md` injected, ≥15,000 chars allowed; context fields `channel`, `senderId`, `args`; `plugins.load.paths` and `plugins.entries.<id>.enabled`; `channels.whatsapp.{dmPolicy,allowFrom,selfChatMode,groupPolicy,groupAllowFrom,configWrites}`), edit those tasks' code in this plan first. If plugin commands do not bypass the agent at all, **stop** and report to the user: the design depends on it.
- [x] **Step 4: Commit** the filled-in plan.

```bash
git add docs/superpowers/plans/2026-11-05-chat-control.md
git commit -m "Chat control plan: Mac facts recorded"
```

---

### Task 2: The read-only snapshot (`ops/agent_context.py`)

**Files:**
- Create: `lms/ops/agent_context.py`
- Create: `lms/ops/com.lms.agentcontext.plist`
- Test: `lms/tests/test_agent_context.py`

**Interfaces:**
- Produces:
  - `read_halt_flag(flag: Path) -> dict | None` — same meaning as `_halt.read_flag` (corrupt counts as halted).
  - `FLAG: Path` (`~/LMS/HALTED.json`), `WORKSPACE: Path`, `TARGET: str` (`"USER.md"`), `BEGIN`, `END`, `MAX_CHARS = 15_000`.
  - `pending_approvals(conn, now: datetime) -> list[sqlite3.Row]` — PENDING and not expired.
  - `snapshot(conn, now: datetime, *, gaps: list[str] | None = None) -> str`
  - `write_block(path: Path, block: str) -> None`
  - `refresh(conn, now: datetime | None = None, *, workspace: Path = WORKSPACE, flag: Path = FLAG) -> bool` — `False` when halted (nothing written).

- [ ] **Step 1: Write the failing tests**

`lms/tests/test_agent_context.py`:

```python
"""The snapshot lms-orchestrator answers from (D-080, ops/agent_context.py)."""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))

import agent_context as ac                                   # noqa: E402
from core.db import database as db                         # noqa: E402

NOW = datetime(2026, 11, 5, 9, 0, tzinfo=db.TZ)


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "lms.db")
    yield c
    c.close()


def task(conn, title, *, due=None, entity="B_MRE", sensitivity="normal"):
    return db.insert_task(conn, title=title, due_date=due, urgency="NORMAL",
                          entity_id=entity, status="TRIAGED",
                          sensitivity=sensitivity)


def test_snapshot_lists_open_tasks_with_numbers_and_overdue(conn):
    late = task(conn, "Pay Con Edison", due="2026-11-01")
    soon = task(conn, "File NY biennial", due="2026-11-20")
    text = ac.snapshot(conn, NOW, gaps=[])
    assert f"#{late} OVERDUE Pay Con Edison" in text
    assert f"#{soon} File NY biennial" in text
    assert "Open tasks: 2 (1 overdue)." in text
    assert "generated Thu Nov 05 09:00" in text


def test_snapshot_never_names_a_private_task(conn):
    tid = task(conn, "Dr. Patel follow-up", sensitivity="private")
    text = ac.snapshot(conn, NOW, gaps=[])
    assert "Patel" not in text
    assert f"#{tid}" in text


def test_snapshot_says_what_the_agent_may_do(conn):
    text = ac.snapshot(conn, NOW, gaps=[])
    assert "You cannot do anything" in text
    assert "/lms help" in text


def test_snapshot_lists_source_gaps(conn):
    text = ac.snapshot(conn, NOW, gaps=["Gmail (mleca): last good check 3 days ago"])
    assert "Gmail (mleca): last good check 3 days ago" in text


def test_snapshot_is_capped(conn):
    for i in range(400):
        task(conn, f"Task number {i} " + "x" * 80, due="2026-12-01")
    text = ac.snapshot(conn, NOW, gaps=[])
    assert len(text) <= ac.MAX_CHARS


def test_write_block_creates_the_file(tmp_path):
    f = tmp_path / "USER.md"
    ac.write_block(f, "hello")
    assert f.read_text(encoding="utf-8") == f"{ac.BEGIN}\nhello\n{ac.END}\n"


def test_write_block_keeps_what_is_outside_the_markers(tmp_path):
    f = tmp_path / "USER.md"
    f.write_text("# About Matthew\nHe prefers short answers.\n", encoding="utf-8")
    ac.write_block(f, "first")
    ac.write_block(f, "second")
    text = f.read_text(encoding="utf-8")
    assert text.startswith("# About Matthew\nHe prefers short answers.\n")
    assert "second" in text and "first" not in text
    assert text.count(ac.BEGIN) == 1 and text.count(ac.END) == 1
    assert not (tmp_path / "USER.md.tmp").exists()


def test_refresh_writes_nothing_while_halted(conn, tmp_path):
    flag = tmp_path / "HALTED.json"
    flag.write_text(json.dumps({"ts": "2026-11-05T08:00", "by": "telegram"}))
    assert ac.refresh(conn, NOW, workspace=tmp_path, flag=flag) is False
    assert not (tmp_path / ac.TARGET).exists()


def test_refresh_writes_when_running(conn, tmp_path):
    task(conn, "Pay Con Edison", due="2026-11-01")
    assert ac.refresh(conn, NOW, workspace=tmp_path,
                      flag=tmp_path / "HALTED.json") is True
    assert "Pay Con Edison" in (tmp_path / ac.TARGET).read_text(encoding="utf-8")


def test_a_corrupt_halt_flag_counts_as_halted(tmp_path):
    flag = tmp_path / "HALTED.json"
    flag.write_text("{not json")
    assert ac.read_halt_flag(flag) is not None
    assert ac.read_halt_flag(tmp_path / "absent.json") is None


def test_pending_approvals_leaves_out_expired(conn):
    tid = task(conn, "Con Edison bill")
    for ref, hours in (("AP-1", 5), ("AP-2", -5)):
        conn.execute(
            "INSERT INTO approvals (ref, task_id, basis, kind, action_json, fingerprint,"
            " policy_verdict, requested_by, requested_at, expires_at, status)"
            " VALUES (?, ?, 'HUMAN', 'pay_bill', '{}', ?, 'NEEDS_APPROVAL', 'finance', ?, ?,"
            " 'PENDING')",
            (ref, tid, ref, NOW.isoformat(), (NOW + timedelta(hours=hours)).isoformat()))
    assert [a["ref"] for a in ac.pending_approvals(conn, NOW)] == ["AP-1"]
```

- [ ] **Step 2: Run to verify they fail**

Run (from `lms/`): `<venv>/python -m pytest -o addopts="" -p no:cacheprovider tests/test_agent_context.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'agent_context'`

- [ ] **Step 3: Write `lms/ops/agent_context.py`**

```python
#!/usr/bin/env python3
"""What lms-orchestrator knows when Matthew asks it something in plain words (D-080).

    ./ops/agent_context.py            # write the snapshot now
    ./ops/agent_context.py --print    # show it; write nothing

The agent has no tools (D-056), so it cannot look anything up. This writes a
short, read-only snapshot (open tasks, approvals waiting, sources behind) into
a workspace file OpenClaw puts in the agent's prompt, between two marker
lines. Everything else in that file is left as it is.

Hourly from com.lms.agentcontext, and after each /lms change (ops/remote.py).
Nothing is written while the system is halted: a halt stops writing, and the
gateway that would read it is stopped too.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

WORKSPACE = Path.home() / ".openclaw" / "workspace-lms-orchestrator"
TARGET = "USER.md"            # injected into the prompt (Task 1, Mac facts)
FLAG = Path.home() / "LMS" / "HALTED.json"      # same file as _halt.FLAG
BEGIN = "<!-- LMS_NOW:BEGIN (written by ops/agent_context.py; do not edit) -->"
END = "<!-- LMS_NOW:END -->"
MAX_CHARS = 15_000
TASK_LIMIT = 40


def read_halt_flag(flag: Path = FLAG) -> dict | None:
    """_halt.read_flag, for code that must import on Windows too.

    _halt.py does not import there (os.killpg). Same rule: a corrupt flag
    still means halted; damage is never read as running.
    """
    try:
        return json.loads(flag.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"ts": "?", "by": "?", "corrupt": True}


def money(cents: int | None) -> str:
    return "" if cents is None else f"${cents / 100:,.2f}"


def pending_approvals(conn: sqlite3.Connection, now: datetime) -> list[sqlite3.Row]:
    from core.pipeline import approvals
    return [a for a in approvals.pending(conn)
            if datetime.fromisoformat(a["expires_at"]) > now]


def snapshot(conn: sqlite3.Connection, now: datetime, *,
             gaps: list[str] | None = None) -> str:
    from core.pipeline import coverage
    from core.reports import brief as reports

    lines = [
        f"LMS snapshot, generated {now:%a %b %d %H:%M} (New York time).",
        "Answer Matthew's questions about his tasks, approvals and sources only "
        "from this snapshot, and say when it was generated. You cannot do "
        "anything: for an action, give him the /lms command (/lms help lists them).",
        "",
    ]

    scored = reports.todo_list(conn, today=now.date())
    overdue = sum(1 for t in scored if t.overdue)
    lines.append(f"Open tasks: {len(scored)} ({overdue} overdue).")
    for t in scored[:TASK_LIMIT]:
        due = t.row["due_date"] or "no due date"
        mark = "OVERDUE " if t.overdue else ""
        lines.append(f"- #{t.task_id} {mark}{t.shown_title} · due {due} · "
                     f"{t.row['entity_id'] or '?'}")
    if len(scored) > TASK_LIMIT:
        lines.append(f"- … {len(scored) - TASK_LIMIT} more (/lms tasks)")
    lines.append("")

    private = reports.private_task_ids(conn)
    waiting = pending_approvals(conn, now)
    lines.append(f"Approvals waiting: {len(waiting)}.")
    for a in waiting:
        who = "(private)" if int(a["task_id"]) in private else (a["recipient"] or "")
        lines.append(f"- {a['ref']} {a['kind']} {who} {money(a['amount_cents'])} · "
                     f"expires {a['expires_at'][:16]}".replace("  ", " "))
    lines.append("")

    if gaps is None:
        gaps = coverage.disclosures(conn, now=now, active_only=True)
    lines.append("Sources behind: " + ("none." if not gaps else f"{len(gaps)}."))
    lines.extend(f"- {g}" for g in gaps)

    text = "\n".join(lines)
    if len(text) > MAX_CHARS:
        tail = "\n… (cut; /lms tasks has the full list)"
        text = text[: MAX_CHARS - len(tail)].rsplit("\n", 1)[0] + tail
    return text


def write_block(path: Path, block: str) -> None:
    """Replace what is between the markers; keep the rest; atomic."""
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    new_block = f"{BEGIN}\n{block}\n{END}"
    if BEGIN in old and END in old:
        head, rest = old.split(BEGIN, 1)
        _, tail = rest.split(END, 1)
        text = head + new_block + tail
    else:
        text = (old.rstrip("\n") + "\n\n" if old.strip() else "") + new_block + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def refresh(conn: sqlite3.Connection, now: datetime | None = None, *,
            workspace: Path = WORKSPACE, flag: Path = FLAG) -> bool:
    if read_halt_flag(flag):
        return False
    from core.db import database as db
    now = now or datetime.now(db.TZ)
    write_block(workspace / TARGET, snapshot(conn, now))
    return True


def main() -> int:
    from _reexec import ensure_venv
    ensure_venv("LMS_AGENTCONTEXT_REEXEC", script=__file__)
    from _env import load_lms_env
    load_lms_env()

    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--print", action="store_true", help="show it; write nothing")
    args = p.parse_args()

    flag = read_halt_flag()
    if flag and not args.print:
        print(f"HALTED since {flag.get('ts')}: snapshot not written.")
        return 0

    from core.db import database as db
    from done import connect
    conn = connect()
    try:
        now = datetime.now(db.TZ)
        if args.print:
            print(snapshot(conn, now))
            return 0
        refresh(conn, now)
        print(f"wrote {WORKSPACE / TARGET}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run to verify they pass**

Run: `<venv>/python -m pytest -o addopts="" -p no:cacheprovider tests/test_agent_context.py -q`
Expected: all PASS. If `coverage.disclosures` rejects `now=` as given, read its signature (`core/pipeline/coverage.py:206`) and match it; tests pass `gaps=` so they do not depend on it.

- [ ] **Step 5: Write `lms/ops/com.lms.agentcontext.plist`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!--
  The snapshot lms-orchestrator answers plain-text questions from, hourly.
  D-080; ops/agent_context.py says what is in it.

  Install (ops/CHAT_CONTROL_APPLY.md):
    cp ops/com.lms.agentcontext.plist ~/Library/LaunchAgents/
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.lms.agentcontext.plist

  A halt (D-059) disables this job with the rest; run by hand while halted
  it writes nothing.
-->
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.lms.agentcontext</string>

    <key>ProgramArguments</key>
    <array>
        <string>/Users/mleca/lms-repo/lms/.venv/bin/python</string>
        <string>/Users/mleca/lms-repo/lms/ops/agent_context.py</string>
    </array>

    <key>WorkingDirectory</key>
    <string>/Users/mleca/lms-repo/lms</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>TZ</key>
        <string>America/New_York</string>
        <key>PYTHONUNBUFFERED</key>
        <string>1</string>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>

    <key>StartInterval</key>
    <integer>3600</integer>

    <key>RunAtLoad</key>
    <true/>

    <key>StandardOutPath</key>
    <string>/Users/mleca/LMS/logs/agentcontext.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/mleca/LMS/logs/agentcontext.err</string>

    <key>ProcessType</key>
    <string>Background</string>
    <key>Nice</key>
    <integer>10</integer>
</dict>
</plist>
```

- [ ] **Step 6: Run the whole suite** (`tests/test_plists.py` and `tests/test_ops_scripts.py` police the new files)

Run: `<venv>/python -m pytest -o addopts="" -p no:cacheprovider --ignore=tests/test_halt.py -q`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add --chmod=+x lms/ops/agent_context.py
git add lms/ops/com.lms.agentcontext.plist lms/tests/test_agent_context.py
git commit -m "D-080: read-only snapshot for lms-orchestrator (ops/agent_context.py)"
```

---

### Task 3: `ops/remote.py` — owner check, parsing, audit and the look commands

**Files:**
- Create: `lms/ops/remote.py`
- Test: `lms/tests/test_remote.py`

**Interfaces:**
- Consumes: `agent_context.read_halt_flag`, `agent_context.pending_approvals`, `agent_context.money`, `agent_context.refresh` (Task 2).
- Produces (Task 4 extends the same file):
  - `@dataclass class Env` with fields `connect: Callable[[], sqlite3.Connection]`, `owners: dict[str, str]`, `flag: Path`, `run: Callable[[list[str]], subprocess.CompletedProcess]`, `spawn_halt: Callable[[str], None]`, `status: Callable[[], dict]`, `refresh: Callable[[sqlite3.Connection], None]`, `now: Callable[[], datetime]`, `uid: int`, `log: Callable[[str], None]`.
  - `handle(words: list[str], channel: str, sender: str, env: Env) -> str`
  - `COMMANDS: dict[str, Callable[[Env, sqlite3.Connection | None, list[str], str], str]]`, `CHANGES: set[str]`, `NEEDS_DB: set[str]`, `HELP: str`, `MAX_REPLY = 3500`.
  - `sender_id(raw: str) -> str` (drop a leading `channel:`, then digits of the part before `@` and `:`).

- [ ] **Step 1: Write the failing tests**

`lms/tests/test_remote.py`:

```python
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `<venv>/python -m pytest -o addopts="" -p no:cacheprovider tests/test_remote.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'remote'`

- [ ] **Step 3: Write `lms/ops/remote.py`** (Task 4 adds the run-now, halt and change commands to `COMMANDS`)

```python
#!/usr/bin/env python3
"""Matthew's fixed commands from WhatsApp and Telegram (D-080).

    ./ops/remote.py --channel whatsapp --sender <id> -- tasks overdue

Called only by the lms-control OpenClaw plugin, once per /lms message. Prints
the reply; the plugin sends it back. Prints nothing for anyone who is not
Matthew, so a stranger gets no answer at all.

Code carries out every command; the model never chooses one (D-056 still
holds: lms-orchestrator has no tools). The list is fixed: help status brief
tasks coverage mail whatsapp backup done approve deny halt. There is no
resume: a stolen phone can stop the Mac and never restart it (D-059).
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import subprocess
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent_context                                         # noqa: E402
from core.db import database as db                         # noqa: E402

MAX_REPLY = 3500
CUT = "\n… more on the Dashboard."
NUM = re.compile(r"^[0-9]{1,6}$")
OWNER_SERVICES = {"telegram": "lms/telegram-owner-id",
                  "whatsapp": "lms/whatsapp-owner"}
LOG = Path.home() / "LMS" / "logs" / "remote.log"

HELP = """LMS commands (send in this chat):
/lms status – halted or not, and each job
/lms brief – today's brief
/lms tasks – open tasks · /lms tasks overdue
/lms coverage – sources that are behind
/lms mail – read all mailboxes now
/lms whatsapp – read WhatsApp now
/lms backup – back up now
/lms done 12 – close task 12 (shows it; add yes to close)
/lms approve 7 · /lms deny 7 – answer approval AP-7 (add yes)
/lms halt – stop everything now
Plain questions get an answer from today's snapshot.
There is no resume from a phone: resume is at the Mac."""


@dataclass
class Env:
    connect: Callable[[], sqlite3.Connection]
    owners: dict[str, str]
    flag: Path
    run: Callable[[list[str]], subprocess.CompletedProcess]
    spawn_halt: Callable[[str], None]
    status: Callable[[], dict]
    refresh: Callable[[sqlite3.Connection], None]
    now: Callable[[], datetime]
    uid: int
    log: Callable[[str], None]


def sender_id(raw: str | None) -> str:
    """'+1 555-123-4567', '15551234567@s.whatsapp.net', '1555...:12@...',
    'whatsapp:+1555...' -> digits. A leading 'channel:' is OpenClaw's own form."""
    base = re.sub(r"^[a-z]+:", "", (raw or "").strip())
    base = base.split("@", 1)[0].split(":", 1)[0]
    return re.sub(r"\D", "", base)


def is_owner(env: Env, channel: str, sender: str) -> bool:
    want = sender_id(env.owners.get(channel, ""))
    return bool(want) and want == sender_id(sender)


def cap(text: str) -> str:
    if len(text) <= MAX_REPLY:
        return text
    return text[: MAX_REPLY - len(CUT)].rsplit("\n", 1)[0] + CUT


def audit(env: Env, action: str, detail: str, approved_by: str | None = None) -> None:
    """Best effort, like halt_bot.record: never blocks a halt."""
    env.log(f"{action} {detail}")
    try:
        conn = env.connect()
        try:
            db.log_action(conn, action, detail=detail, approved_by=approved_by)
        finally:
            conn.close()
    except (Exception, SystemExit) as exc:                  # noqa: BLE001
        env.log(f"AUDIT_FAILED {action}: {type(exc).__name__}: {exc}")


# --- the commands ------------------------------------------------------------

def cmd_help(env, conn, args, channel):
    return HELP


def _last_good_checks(env) -> list[str]:
    """Best effort: status must answer even when the archive is not mounted."""
    try:
        conn = env.connect()
        try:
            rows = conn.execute(
                "SELECT CASE WHEN source_id LIKE 'email:%' THEN 'mail' "
                "ELSE 'whatsapp' END AS k, MAX(finished_at) FROM source_checks "
                "WHERE result = 'OK' AND (source_id LIKE 'email:%' "
                "OR source_id = 'chat:whatsapp') GROUP BY k ORDER BY k").fetchall()
        finally:
            conn.close()
    except (Exception, SystemExit):                          # noqa: BLE001
        return ["(last checks unavailable: database not reachable)"]
    return [f"last good {k}: {ts[:16]}" for k, ts in rows]


def cmd_status(env, conn, args, channel):
    s = env.status()
    h = s["halted"]
    head = (f"HALTED since {h.get('ts')} (by {h.get('by')})." if h
            else "Running (not halted).")
    rows = [f"{label.split('.', 2)[-1]}: {state}" for label, state in s["jobs"]]
    return "\n".join([head, *(rows or ["no jobs installed"]),
                      *_last_good_checks(env)])


def cmd_brief(env, conn, args, channel):
    from core.pipeline.registry import load_registry
    from core.reports import brief as reports
    now = env.now()
    kind = "morning" if now.hour < 14 else "evening"
    return reports.render_text(reports.build_brief(conn, kind=kind, now=now),
                               load_registry())


def cmd_tasks(env, conn, args, channel):
    from core.reports import brief as reports
    scored = reports.todo_list(conn, today=env.now().date())
    if args == ["overdue"]:
        scored = [t for t in scored if t.overdue]
    if not scored:
        return "Nothing open." if args != ["overdue"] else "Nothing overdue."
    lines = []
    for t in scored:
        mark = "!! " if t.overdue else ""
        due = t.row["due_date"] or "no due date"
        lines.append(f"#{t.task_id} {mark}{t.shown_title} · due {due}")
    lines.append(f"\n{len(scored)} shown. Close one: /lms done <number>")
    return "\n".join(lines)


def cmd_coverage(env, conn, args, channel):
    from core.pipeline import coverage
    gaps = coverage.disclosures(conn, now=env.now())
    return ("Every registered source is on schedule." if not gaps
            else "\n".join(gaps))


COMMANDS: dict[str, Callable] = {
    "help": cmd_help,
    "status": cmd_status,
    "brief": cmd_brief,
    "tasks": cmd_tasks,
    "coverage": cmd_coverage,
}
NO_ARGS = {"help", "status", "brief", "coverage", "mail", "whatsapp", "backup", "halt"}
TWO_STEP = {"done", "approve", "deny"}
CHANGES = {"done", "approve", "deny", "halt"}
NEEDS_DB = {"brief", "tasks", "coverage", "done", "approve", "deny"}


def well_formed(sub: str, args: list[str]) -> bool:
    if sub not in COMMANDS:
        return False
    if sub in NO_ARGS:
        return args == []
    if sub == "tasks":
        return args in ([], ["overdue"])
    if sub in TWO_STEP:
        return (len(args) in (1, 2) and bool(NUM.match(args[0]))
                and (len(args) == 1 or args[1] == "yes"))
    return False


def handle(words: list[str], channel: str, sender: str, env: Env) -> str:
    if channel not in OWNER_SERVICES or not is_owner(env, channel, sender):
        tail = sender_id(sender)[-4:] or "none"
        audit(env, "remote:refused", f"{channel} sender …{tail}")
        return ""

    words = [w.lower() for w in words] or ["help"]
    sub, args = words[0], words[1:]
    if not well_formed(sub, args):
        sub, args = "help", []

    by = "matthew" if sub in CHANGES and (sub == "halt" or args[-1:] == ["yes"]) else None
    try:
        if sub in NEEDS_DB:
            conn = env.connect()
            try:
                conn.execute("PRAGMA busy_timeout = 10000")
                reply = COMMANDS[sub](env, conn, args, channel)
            finally:
                conn.close()
        else:
            reply = COMMANDS[sub](env, None, args, channel)
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower():
            reply = "Busy, try again in a minute."
        else:
            env.log(traceback.format_exc())
            reply = "Failed. See ~/LMS/logs/remote.log."
    except SystemExit as exc:                    # done.connect: archive not mounted
        reply = str(exc) or "Failed. See ~/LMS/logs/remote.log."
    except Exception:                            # noqa: BLE001
        env.log(traceback.format_exc())
        reply = "Failed. See ~/LMS/logs/remote.log."

    audit(env, f"remote:{sub}",
          f"{channel} {' '.join(args)} -> {reply.splitlines()[0][:120] if reply else ''}",
          approved_by=by)
    return cap(reply)


# --- the Mac -----------------------------------------------------------------

def mac_env() -> Env:
    import _halt                                  # Mac only (os.killpg)
    from halt_bot import keychain
    from done import connect as done_connect

    def log(msg: str) -> None:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        with LOG.open("a", encoding="utf-8") as f:
            f.write(f"{stamp} {msg}\n")

    def run(argv: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(argv, capture_output=True, text=True, timeout=20)

    def spawn_halt(channel: str) -> None:
        """Detached and 2 s late, so this reply is sent before the gateway stops."""
        out = LOG.open("a", encoding="utf-8")
        subprocess.Popen(
            ["/bin/sh", "-c", 'sleep 2; exec "$0" "$1" --by "$2"',
             sys.executable, str(Path(__file__).with_name("halt.py")), channel],
            stdin=subprocess.DEVNULL, stdout=out, stderr=out,
            start_new_session=True, close_fds=True)

    def refresh(conn: sqlite3.Connection) -> None:
        agent_context.refresh(conn)

    return Env(connect=done_connect,
               owners={ch: keychain(svc) or "" for ch, svc in OWNER_SERVICES.items()},
               flag=_halt.FLAG, run=run, spawn_halt=spawn_halt,
               status=_halt.status, refresh=refresh,
               now=lambda: datetime.now(db.TZ), uid=os.getuid(), log=log)


def main(argv: list[str] | None = None) -> int:
    from _reexec import ensure_venv
    ensure_venv("LMS_REMOTE_REEXEC", script=__file__)
    from _env import load_lms_env
    load_lms_env()

    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--channel", required=True)
    p.add_argument("--sender", required=True)
    p.add_argument("words", nargs="*")
    a = p.parse_args(argv)
    reply = handle(a.words, a.channel, a.sender, mac_env())
    if reply:
        print(reply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Note on `by`: in Task 3 no command in `CHANGES` is in `COMMANDS` yet, so `by` is always `None` for accepted calls; Task 4's tests check `matthew`.

- [ ] **Step 4: Run to verify they pass**

Run: `<venv>/python -m pytest -o addopts="" -p no:cacheprovider tests/test_remote.py -q`
Expected: all PASS. `test_help_lists_every_command_and_no_resume` passes now because `HELP` already names all commands; the `["done"...]` parse tests fall through to help because `done` is not in `COMMANDS` yet (Task 4 keeps them falling through via `well_formed`).

- [ ] **Step 5: Run the whole suite**, then commit

```bash
git add --chmod=+x lms/ops/remote.py
git add lms/tests/test_remote.py
git commit -m "D-080: ops/remote.py, owner check, audit and the look commands"
```

---

### Task 4: `ops/remote.py` — run now, halt, and the two-step changes

**Files:**
- Modify: `lms/ops/remote.py` (add commands, register them in `COMMANDS`)
- Modify: `lms/ops/halt.py` (add `--by`)
- Test: `lms/tests/test_remote.py` (append)

**Interfaces:**
- Consumes: `Env`, `COMMANDS`, `handle`, `audit` (Task 3); `core.pipeline.tasks.close_task(conn, task_id, *, note, by) -> CloseResult` (`task_id`, `title`, `status`, `was_already`); `core.pipeline.ledger.get(conn, task_id)` raises `ledger.LedgerError`; `core.pipeline.approvals.get(conn, ref)` / `decide(conn, ref, answer, *, by, note)` raise `approvals.ApprovalError` and return `Answered(ref, task_id, status, authorizes)`.
- Produces: `COMMANDS` gains `mail whatsapp backup halt done approve deny`; `JOBS = {"mail": "com.lms.mail", "whatsapp": "com.lms.whatsapp", "backup": "com.lms.backup"}`.

- [ ] **Step 1: Append the failing tests to `lms/tests/test_remote.py`**

```python
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
```

- [ ] **Step 2: Run to verify the new tests fail**

Run: `<venv>/python -m pytest -o addopts="" -p no:cacheprovider tests/test_remote.py -q`
Expected: the new tests FAIL (they get the help list); Task 3's tests still PASS.

- [ ] **Step 3: Add the commands to `lms/ops/remote.py`**, directly above `COMMANDS: dict[str, Callable] = {`:

```python
JOBS = {"mail": "com.lms.mail", "whatsapp": "com.lms.whatsapp",
        "backup": "com.lms.backup"}


def _halted_reply(flag: dict) -> str:
    return (f"Halted since {flag.get('ts')}. Nothing runs until it is resumed "
            f"at the Mac: ./ops/halt.py --resume")


def cmd_run_now(name: str):
    def run_now(env, conn, args, channel):
        flag = agent_context.read_halt_flag(env.flag)
        if flag:
            return _halted_reply(flag)
        label = JOBS[name]
        r = env.run(["launchctl", "kickstart", f"gui/{env.uid}/{label}"])
        if r.returncode != 0:
            return f"{name} is not installed on this Mac ({label})."
        return f"Started {name} now. /lms status shows it."
    return run_now


def cmd_halt(env, conn, args, channel):
    flag = agent_context.read_halt_flag(env.flag)
    env.spawn_halt(channel)
    if flag:
        return (f"Was already halted since {flag.get('ts')} (by {flag.get('by')}); "
                "halting again in 2 seconds. Everything stops, including this chat. "
                "@MREOC18bot /status shows the result. Resume is at the Mac only.")
    return ("Halting in 2 seconds. Everything stops, including this chat. "
            "@MREOC18bot /status shows the result. Resume is at the Mac only.")


def _refresh_best_effort(env, conn) -> None:
    """The change is already committed; a failed snapshot refresh must not
    change the reply or the audit result. The hourly job refreshes it anyway."""
    try:
        env.refresh(conn)
    except Exception as exc:   # noqa: BLE001
        env.log(f"snapshot refresh failed after commit: {type(exc).__name__}: {exc}")


def _shown_task_title(conn, task_id: int, title: str) -> str:
    from core.reports import brief as reports
    return (reports.private_line(task_id)
            if task_id in reports.private_task_ids(conn) else title)


def cmd_done(env, conn, args, channel):
    from core.pipeline import ledger, tasks as task_ops
    tid = int(args[0])
    try:
        row = ledger.get(conn, tid)
    except ledger.LedgerError:
        return f"No task #{tid}."
    title = _shown_task_title(conn, tid, row["title"])
    if args[1:] != ["yes"]:
        return (f"#{tid} {title} ({row['status']}, due {row['due_date'] or 'none'}).\n"
                f"Send /lms done {tid} yes to close it.")
    r = task_ops.close_task(conn, tid, note=f"via {channel}", by="matthew")
    _refresh_best_effort(env, conn)
    if r.was_already:
        return f"#{tid} was already {r.was_already.lower()}: {title}"
    return f"Closed #{tid}: {title}"


def cmd_answer(answer: str):
    def respond(env, conn, args, channel):
        from core.pipeline import approvals
        from core.reports import brief as reports
        n = args[0]
        ref = f"AP-{n}"
        try:
            a = approvals.get(conn, ref)
        except approvals.ApprovalError:
            return f"No approval {ref}."
        if args[1:] != ["yes"]:
            private = int(a["task_id"]) in reports.private_task_ids(conn)
            who = "(private)" if private else (a["recipient"] or "")
            return (f"{ref} {a['kind']} {who} {agent_context.money(a['amount_cents'])}"
                    f" · {a['status']} · expires {a['expires_at'][:16]}\n"
                    f"Send /lms {answer} {n} yes to {answer} it.").replace("  ", " ")
        try:
            res = approvals.decide(conn, ref, answer, by="matthew", note=f"via {channel}")
        except approvals.ApprovalError as exc:
            return str(exc)
        _refresh_best_effort(env, conn)
        word = {"approve": "approved", "deny": "denied"}[answer]
        extra = ("" if answer == "deny" or res.authorizes
                 else " (recorded as agreement only: a hard stop still applies)")
        return f"{ref} {word}.{extra}"
    return respond
```

Then extend the `COMMANDS` dict literal with:

```python
    "mail": cmd_run_now("mail"),
    "whatsapp": cmd_run_now("whatsapp"),
    "backup": cmd_run_now("backup"),
    "halt": cmd_halt,
    "done": cmd_done,
    "approve": cmd_answer("approve"),
    "deny": cmd_answer("deny"),
```

- [ ] **Step 4: Add `--by` to `lms/ops/halt.py`.** In `main()`, after the mutually exclusive group, add:

```python
    p.add_argument("--by", default="local", choices=["local", "telegram", "whatsapp"],
                   help="who asked (ops/remote.py passes the chat it came from)")
```

and change `result = _halt.halt(by="local")` to `result = _halt.halt(by=args.by)`.

- [ ] **Step 5: Run to verify all pass**

Run: `<venv>/python -m pytest -o addopts="" -p no:cacheprovider --ignore=tests/test_halt.py -q`
Expected: all PASS. If `test_done_yes_closes_it_as_matthew_and_refreshes` reports a status other than `CLOSED` (ledger v2 names it), read `core/pipeline/tasks.py:75` and assert the status `close_task` sets for `outcome="DONE"`.

- [ ] **Step 6: Commit**

```bash
git add lms/ops/remote.py lms/ops/halt.py lms/tests/test_remote.py
git commit -m "D-080: remote run-now, halt and the two-step done/approve/deny"
```

---

### Task 5: The plugin, the OpenClaw patch and verify_setup

**Files:**
- Create: `lms/openclaw-plugin/lms-control/openclaw.plugin.json`
- Create: `lms/openclaw-plugin/lms-control/index.js`
- Create: `lms/openclaw-plugin/lms-control/package.json`
- Create: `lms/ops/remote.patch.json5`
- Modify: `lms/ops/verify_setup.py` (new `check_chat_control`, `whatsapp_problems`, call in `main`)
- Test: `lms/tests/test_remote_patch.py`

**Interfaces:**
- Consumes: `ops/remote.py` CLI: `remote.py --channel <c> --sender <id> -- <words...>` (Task 3).
- Produces: `verify_setup.whatsapp_problems(cfg: dict, owner: str) -> list[str]`.

The code below assumes the Mac facts Task 1 was expected to confirm. Use the corrected names from "Mac facts" wherever they differ.

- [ ] **Step 1: Write the failing tests** `lms/tests/test_remote_patch.py`:

```python
"""D-080: the plugin and ops/remote.patch.json5 keep chat control narrow.

Parsed, not grepped, for the patch (as test_phase7b.py). The plugin is
JavaScript, which these tests do not run; they pin the few lines that decide
what it can do.
"""

import sys
from pathlib import Path

import pytest

from tests.test_phase7b import load_json5ish

LMS = Path(__file__).resolve().parents[1]
PATCH = LMS / "ops" / "remote.patch.json5"
PLUGIN = LMS / "openclaw-plugin" / "lms-control"
TG = "8783061626"
WA = "__WHATSAPP_OWNER__"
WA_E164 = "+" + WA          # config form (Mac facts row 1); Keychain holds digits

sys.path.insert(0, str(LMS / "ops"))
import verify_setup as vs                                    # noqa: E402


@pytest.fixture(scope="module")
def patch():
    return load_json5ish(PATCH.read_text(encoding="utf-8"))


def test_whatsapp_is_an_allowlist_of_his_own_number(patch):
    wa = patch["channels"]["whatsapp"]
    assert wa["enabled"] is True
    assert wa["dmPolicy"] == "allowlist"
    assert wa["allowFrom"] == [WA_E164]
    assert wa["selfChatMode"] is True
    assert wa["groupPolicy"] == "disabled" and wa["groupAllowFrom"] == []
    assert wa["configWrites"] is False


def test_no_phone_number_is_committed():
    import re
    text = PATCH.read_text(encoding="utf-8")
    assert not re.search(r"\+?\b1\d{10}\b", text)


def test_both_chats_route_to_the_tool_less_agent_only(patch):
    routes = {(b["match"]["channel"], b["match"]["peer"]["id"]): b["agentId"]
              for b in patch["bindings"]}
    assert routes == {("telegram", TG): "lms-orchestrator",
                      ("whatsapp", WA_E164): "lms-orchestrator"}
    for b in patch["bindings"]:
        assert b["match"]["peer"]["kind"] == "direct"


def test_owners_are_exactly_matthew_on_both(patch):
    c = patch["commands"]
    assert sorted(c["ownerAllowFrom"]) == sorted([f"telegram:{TG}", f"whatsapp:{WA_E164}"])
    assert c["allowFrom"] == {"telegram": [TG], "whatsapp": [WA_E164]}


def test_dangerous_chat_commands_stay_off(patch):
    c = patch["commands"]
    for key in ("bash", "config", "mcp", "plugins", "debug", "restart"):
        assert c[key] is False, key


def test_only_our_plugin_is_loaded_from_the_repo(patch):
    p = patch["plugins"]
    assert p["load"]["paths"] == ["/Users/mleca/lms-repo/lms/openclaw-plugin/lms-control"]
    assert p["entries"] == {"lms-control": {"enabled": True},
                            "whatsapp": {"enabled": True}}


def test_the_plugin_runs_remote_py_without_a_shell():
    js = (PLUGIN / "index.js").read_text(encoding="utf-8")
    assert "execFile(" in js
    assert "shell: false" in js
    assert "child_process\").exec(" not in js and " exec(" not in js
    assert "spawn(" not in js
    assert "/Users/mleca/lms-repo/lms/ops/remote.py" in js
    assert "timeout: 60_000" in js or "timeout: 60000" in js
    assert '"--"' in js


def test_the_plugin_stays_silent_rather_than_sending_an_empty_reply():
    js = (PLUGIN / "index.js").read_text(encoding="utf-8")
    assert "suppressReply: true" in js
    assert 'text: ""' not in js
    assert "continueAgent" not in js            # a command never reaches the agent


def test_the_plugin_commands_require_an_authorised_sender():
    js = (PLUGIN / "index.js").read_text(encoding="utf-8")
    assert js.count("requireAuth: true") == js.count("registerCommand(")
    assert "requireAuth: false" not in js


def test_the_plugin_is_silent_for_unauthorised_senders_before_running_anything():
    js = (PLUGIN / "index.js").read_text(encoding="utf-8")
    assert "isAuthorizedSender" in js
    body = js[js.index("async function handle"):]
    assert body.index("isAuthorizedSender") < body.index("runRemote(")


def test_d015_baseline_counts_whatsapp_and_lms_control():
    assert vs.D015_PLUGINS == 6


def test_the_manifest_names_the_plugin():
    import json
    m = json.loads((PLUGIN / "openclaw.plugin.json").read_text(encoding="utf-8"))
    assert m["id"] == "lms-control"
    js = (PLUGIN / "index.js").read_text(encoding="utf-8")
    assert 'id: "lms-control"' in js and "register," in js
    pkg = json.loads((PLUGIN / "package.json").read_text(encoding="utf-8"))
    assert pkg["type"] == "module"
    assert pkg["openclaw"]["extensions"] == ["./index.js"]


@pytest.mark.parametrize("cfg,owner,expect_ok", [
    ({"enabled": True, "dmPolicy": "allowlist", "allowFrom": ["+15551234567"],
      "selfChatMode": True, "groupPolicy": "disabled"}, "15551234567", True),
    ({"enabled": True, "dmPolicy": "pairing", "allowFrom": ["+15551234567"],
      "selfChatMode": True, "groupPolicy": "disabled"}, "15551234567", False),
    ({"enabled": True, "dmPolicy": "allowlist", "allowFrom": ["+15551234567", "+15550000000"],
      "selfChatMode": True, "groupPolicy": "disabled"}, "15551234567", False),
    ({"enabled": True, "dmPolicy": "allowlist", "allowFrom": ["+15550000000"],
      "selfChatMode": True, "groupPolicy": "disabled"}, "15551234567", False),
    ({"enabled": True, "dmPolicy": "allowlist", "allowFrom": ["+15551234567"],
      "selfChatMode": True, "groupPolicy": "open"}, "15551234567", False),
    ({"enabled": True, "dmPolicy": "allowlist", "allowFrom": ["+15551234567"],
      "selfChatMode": True, "groupPolicy": "disabled"}, "", False),
])
def test_whatsapp_problems(cfg, owner, expect_ok):
    assert (vs.whatsapp_problems(cfg, owner) == []) is expect_ok


@pytest.mark.parametrize("owner", ["abc", "+"])
def test_an_owner_with_no_digits_is_no_owner(owner):
    cfg = {"dmPolicy": "allowlist", "allowFrom": ["*"], "selfChatMode": True,
           "groupPolicy": "disabled"}
    assert any("no WhatsApp owner" in p for p in vs.whatsapp_problems(cfg, owner))


@pytest.mark.parametrize("allow", ["+15551234567", {"a": 1}, 5])
def test_allow_from_must_be_a_list(allow):
    cfg = {"dmPolicy": "allowlist", "allowFrom": allow, "selfChatMode": True,
           "groupPolicy": "disabled"}
    assert any("allowFrom is not a list" in p
               for p in vs.whatsapp_problems(cfg, "15551234567"))
```

- [ ] **Step 2: Run to verify they fail**

Run: `<venv>/python -m pytest -o addopts="" -p no:cacheprovider tests/test_remote_patch.py -q`
Expected: FAIL (`FileNotFoundError` for the patch; `AttributeError` for `whatsapp_problems`).

- [ ] **Step 3: Write `lms/openclaw-plugin/lms-control/openclaw.plugin.json`**

```json
{
  "id": "lms-control",
  "name": "LMS control",
  "description": "Matthew's fixed /lms commands (D-080). Runs ops/remote.py; no AI involved.",
  "version": "1.0.0",
  "configSchema": {
    "type": "object",
    "additionalProperties": false,
    "properties": {}
  }
}
```

- [ ] **Step 4: Write `lms/openclaw-plugin/lms-control/index.js`**

```js
// lms-control: Matthew's fixed /lms commands from WhatsApp and Telegram (D-080).
//
// This file holds no logic. Every command is decided and carried out by
// ops/remote.py, which checks the sender a second time, refuses anything not
// on its list, writes the audit row and prints the reply. Plugin commands run
// before the agent, so the model never sees or chooses an action (D-056).
//
// execFile, never a shell: the words Matthew typed are arguments, not code.

import { execFile } from "node:child_process";

const LMS = "/Users/mleca/lms-repo/lms";
const PYTHON = `${LMS}/.venv/bin/python`;
const SCRIPT = `${LMS}/ops/remote.py`;
const CHANNELS = new Set(["telegram", "whatsapp"]);
const MAX_WORDS = 4;
// /halt is not a built-in on 2026.7.1-2 (Mac facts row 5). Set false if a
// later OpenClaw reserves it: registration would fail and take /lms with it.
const REGISTER_HALT = true;

function words(raw) {
  return String(raw ?? "").trim().split(/\s+/).filter(Boolean).slice(0, MAX_WORDS);
}

function runRemote(channel, sender, argWords) {
  return new Promise((resolve) => {
    execFile(
      PYTHON,
      [SCRIPT, "--channel", channel, "--sender", String(sender ?? ""), "--", ...argWords],
      { cwd: LMS, timeout: 60_000, maxBuffer: 64 * 1024, shell: false },
      (err, stdout) => {
        const text = String(stdout ?? "").trim();
        if (err && err.killed) return resolve("Still running. See ~/LMS/logs/remote.log.");
        if (err && !text) return resolve("Failed. See ~/LMS/logs/remote.log.");
        resolve(text);
      },
    );
  });
}

// Nothing is sent back: a stranger, another channel, or remote.py printing
// nothing. suppressReply stops OpenClaw's fallback reply (Mac facts row 4).
const SILENT = { suppressReply: true };

async function handle(ctx, argWords) {
  const channel = ctx.channel;
  if (!CHANNELS.has(channel)) return SILENT;
  // OpenClaw says this sender may not run commands: no reply, no process.
  if (ctx.isAuthorizedSender === false) return SILENT;
  const text = await runRemote(channel, ctx.senderId ?? ctx.from, argWords);
  return text ? { text } : SILENT;
}

// The shape definePluginEntry() returns (shipped docs/plugins/*.md), written
// out so the plugin has no import from OpenClaw's own install: a plugin
// outside node_modules/openclaw may not resolve "openclaw/plugin-sdk/...".
export default {
  id: "lms-control",
  name: "LMS control",
  description: "Matthew's fixed /lms commands (D-080). Runs ops/remote.py; no AI involved.",
  register,
};

function register(api) {
  api.registerCommand({
    name: "lms",
    description: "Matthew's LMS commands (/lms help)",
    acceptsArgs: true,
    requireAuth: true,
    handler: (ctx) => handle(ctx, words(ctx.args)),
  });

  if (REGISTER_HALT) {
    api.registerCommand({
      name: "halt",
      description: "Stop every LMS and OpenClaw job now",
      acceptsArgs: false,
      requireAuth: true,
      handler: (ctx) => handle(ctx, ["halt"]),
    });
  }
}
```

If a later OpenClaw reserves `/halt`, set `REGISTER_HALT = false` and leave the block in place; the tests still hold.

- [ ] **Step 4b: Write `lms/openclaw-plugin/lms-control/package.json`** (marks the folder as ESM and names the entry file for the loader):

```json
{
  "name": "lms-control",
  "version": "1.0.0",
  "private": true,
  "type": "module",
  "main": "./index.js",
  "openclaw": { "extensions": ["./index.js"] }
}
```

Whether OpenClaw loads it is checked right after the restart in `CHAT_CONTROL_APPLY.md` §4 (`openclaw plugins inspect lms-control`, `openclaw plugins doctor`). If it does not load, the fallback is `import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry"` and `export default definePluginEntry({...same object...})`.

- [ ] **Step 5: Write `lms/ops/remote.patch.json5`**

```json5
// Chat control (D-080): WhatsApp on Matthew's own number, the lms-control
// plugin, and his number as a command owner. Telegram (Phase 7b) is repeated
// here because a config patch replaces arrays: bindings and the owner lists
// must name both chats or the Telegram entries would be dropped.
//
// Every key is from `openclaw config schema` on the Mac (plan Task 1), not from
// the docs (D-021, D-056).
//
// His number is never committed. Apply (ops/CHAT_CONTROL_APPLY.md):
//     N=$(security find-generic-password -a lms -s lms/whatsapp-owner -w)
//     sed "s/__WHATSAPP_OWNER__/$N/g" ops/remote.patch.json5 > /tmp/remote.patch.json5
//     openclaw config patch --file /tmp/remote.patch.json5 --dry-run
//     openclaw config patch --file /tmp/remote.patch.json5
//     rm /tmp/remote.patch.json5
//     openclaw gateway restart

{
  channels: {
    whatsapp: {
      enabled: true,
      // His own number only; self-chat ("Message yourself") is where he types.
      // Anyone else who messages him is dropped and never answered.
      dmPolicy: "allowlist",
      // E.164 with "+", as the schema's example; the Keychain holds digits only.
      allowFrom: ["+__WHATSAPP_OWNER__"],
      selfChatMode: true,
      groupPolicy: "disabled",
      groupAllowFrom: [],
      configWrites: false,
    },
  },

  // Plain text from either chat reaches only the tool-less agent (D-056).
  bindings: [
    {
      type: "route",
      agentId: "lms-orchestrator",
      comment: "Phase 7b: Matthew's Telegram DM. No tools (D-056).",
      match: { channel: "telegram", peer: { kind: "direct", id: "8783061626" } },
    },
    {
      type: "route",
      agentId: "lms-orchestrator",
      comment: "D-080: Matthew's WhatsApp self-chat. No tools (D-056).",
      // Direct chats match E.164 numbers (shipped docs/channels/whatsapp.md).
      match: { channel: "whatsapp", peer: { kind: "direct", id: "+__WHATSAPP_OWNER__" } },
    },
  ],

  commands: {
    ownerAllowFrom: ["telegram:8783061626", "whatsapp:+__WHATSAPP_OWNER__"],
    allowFrom: { telegram: ["8783061626"], whatsapp: ["+__WHATSAPP_OWNER__"] },
    bash: false,
    config: false,
    mcp: false,
    plugins: false,
    debug: false,
    restart: false,
  },

  plugins: {
    load: { paths: ["/Users/mleca/lms-repo/lms/openclaw-plugin/lms-control"] },
    // WhatsApp is a separate plugin, @openclaw/whatsapp@2026.7.1, installed in
    // CHAT_CONTROL_APPLY.md step 3; enabled here (D015_PLUGINS 4 -> 6, D-080).
    entries: { "lms-control": { enabled: true }, whatsapp: { enabled: true } },
  },
}
```

- [ ] **Step 6: Add to `lms/ops/verify_setup.py`**, above the `# ----` line before `def main()`:

```python
def whatsapp_problems(cfg: dict, owner: str) -> list[str]:
    """What is wrong with channels.whatsapp for chat control (D-080). [] = fine."""
    digits = lambda s: "".join(ch for ch in str(s) if ch.isdigit())   # noqa: E731
    problems = []
    if not digits(owner):
        problems.append("no WhatsApp owner in Keychain (lms/whatsapp-owner)")
    if cfg.get("dmPolicy") != "allowlist":
        problems.append(f"dmPolicy is {cfg.get('dmPolicy')!r}, not 'allowlist'")
    raw_allow = cfg.get("allowFrom")
    if not isinstance(raw_allow, list):
        problems.append(f"allowFrom is not a list ({type(raw_allow).__name__})")
        allow = []
    else:
        allow = [digits(a) for a in raw_allow]
        if len(allow) != 1:
            problems.append(f"allowFrom has {len(allow)} entries, not exactly one")
    if len(allow) == 1 and digits(owner) and allow[0] != digits(owner):
        problems.append("allowFrom is not the Keychain owner's number")
    if cfg.get("groupPolicy") != "disabled":
        problems.append(f"groupPolicy is {cfg.get('groupPolicy')!r}, not 'disabled'")
    if cfg.get("selfChatMode") is not True:
        problems.append("selfChatMode is not on")
    return problems


def whatsapp_config_problems(raw_stdout: str, owner: str) -> list[str]:
    """Parse `openclaw config get channels.whatsapp --json` and judge it.

    Fails closed: output that is empty, not JSON, or not an object is a
    problem, never a pass and never a crash.
    """
    try:
        cfg = json.loads(raw_stdout)
    except (json.JSONDecodeError, TypeError):
        return ["could not parse channels.whatsapp output as JSON; check by hand"]
    if not isinstance(cfg, dict):
        return [f"channels.whatsapp is {type(cfg).__name__}, not an object"]
    return whatsapp_problems(cfg, owner)


def check_chat_control() -> None:
    """[12] WhatsApp and Telegram control (D-080) is as narrow as designed."""
    print("\n[12] Chat control (D-080)")
    if sys.platform != "darwin":
        warn(f"chat control is not checkable on {sys.platform}")
        return
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _halt
    import halt_bot

    try:
        r = subprocess.run(["openclaw", "config", "get", "channels.whatsapp", "--json"],
                           capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        warn("openclaw CLI not available; skipped")
        return
    if r.returncode != 0 and "path not found" in (r.stderr + r.stdout).lower():
        warn("channels.whatsapp is not set; chat control not applied yet "
             "(ops/CHAT_CONTROL_APPLY.md)")
        return
    problems = whatsapp_config_problems(r.stdout,
                                        halt_bot.keychain("lms/whatsapp-owner") or "")
    for p_ in problems:
        fail(f"WhatsApp: {p_}")
    if not problems:
        ok("WhatsApp: allowlist of one, his own number, groups off, self-chat")

    r = subprocess.run(["openclaw", "config", "get", "plugins.entries", "--json"],
                       capture_output=True, text=True, timeout=30)
    try:
        entries = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else {}
    except json.JSONDecodeError:
        entries = {}
    if entries.get("lms-control", {}).get("enabled") is True:
        ok("plugin lms-control enabled")
    else:
        fail("plugin lms-control is not enabled; /lms will not answer")

    if "com.lms.agentcontext" in _halt.loaded():
        ok("com.lms.agentcontext loaded")
    else:
        fail("com.lms.agentcontext not loaded; plain-text answers go stale")
```

In `main()`, after `check_kill_switch()`, add `        check_chat_control()`.

- [ ] **Step 7: Run to verify all pass**

Run: `<venv>/python -m pytest -o addopts="" -p no:cacheprovider --ignore=tests/test_halt.py -q`
Expected: all PASS. If `from tests.test_phase7b import load_json5ish` fails to import, copy `load_json5ish` from `tests/test_phase7b.py` into the new test file unchanged.

- [ ] **Step 8: Commit**

```bash
git add lms/openclaw-plugin/lms-control/openclaw.plugin.json lms/openclaw-plugin/lms-control/index.js
git add lms/ops/remote.patch.json5 lms/ops/verify_setup.py lms/tests/test_remote_patch.py
git commit -m "D-080: lms-control plugin, remote.patch.json5, verify_setup [12]"
```

---

### Task 6: Apply guide and records

**Files:**
- Create: `lms/ops/CHAT_CONTROL_APPLY.md`
- Modify: `DECISIONS.md` (new D-080 above `### D-078`), `REQUIREMENTS_REMAINING.md` (R10, R11 rows), `PHASES.md` (C2 row)

- [ ] **Step 1: Write `lms/ops/CHAT_CONTROL_APPLY.md`**

````markdown
# Chat control: apply on the Mac (D-080)

Matthew controls the LMS from WhatsApp ("Message yourself") and Telegram
(@MREOCbot) with `/lms` commands. Code carries out every command
(`ops/remote.py`); the AI only answers plain questions, read-only.
@MREOC18bot (the kill switch) is not touched.

Run from `~/lms-repo/lms`. No `#` comments after commands: zsh runs them.

## 1. Pull and test

```bash
cd ~/lms-repo/lms && git pull
```

```bash
.venv/bin/python -m pytest -q tests/test_remote.py tests/test_agent_context.py tests/test_remote_patch.py
```

`halt.py` changed, and its tests are skipped on Windows, so run them here too:

```bash
.venv/bin/python -m pytest -q tests/test_halt.py
```

## 2. Back up the OpenClaw config

```bash
cp ~/.openclaw/openclaw.json ~/.openclaw/openclaw.json.pre-chatcontrol
```

## 3. Install the WhatsApp plugin, then link WhatsApp to OpenClaw

WhatsApp is not part of OpenClaw 2026.7.1-2: it is a separate plugin, `@openclaw/whatsapp`. Its newest version (2026.11.1) needs OpenClaw 2026.11.1 or later, so install the version built for this OpenClaw, 2026.7.1 (done on the Mac Nov 6: installed and loaded). Without it, the QR login would install the newest version itself. Skip the install if `openclaw plugins inspect whatsapp` already shows `Version: 2026.7.1`.

```bash
openclaw plugins install @openclaw/whatsapp@2026.7.1
```

```bash
openclaw plugins inspect whatsapp
```

It must show `Version: 2026.7.1`. Do not run `openclaw plugins update` on it while OpenClaw is 2026.7.x.

Until step 4 writes the allowlist, WhatsApp must answer no one: by default it replies to unknown senders with a pairing code, and this is his own number. Switch its messages off before linking:

```bash
openclaw config set channels.whatsapp.dmPolicy disabled
```

```bash
openclaw config set channels.whatsapp.groupPolicy disabled
```

```bash
openclaw gateway restart
```

```bash
openclaw config get channels.whatsapp --json
```

It must show `"dmPolicy": "disabled"` and `"groupPolicy": "disabled"`. If not, stop here.

Matthew's phone is needed for one QR scan: WhatsApp → Settings → Linked Devices → Link a Device.

```bash
openclaw channels login --channel whatsapp
```

Then:

```bash
openclaw channels status
```

It shows the linked number. Store it (digits only, with the country code, for example 15551234567):

```bash
security add-generic-password -U -a lms -s lms/whatsapp-owner -w
```

(It asks for the value twice; paste the number. Nothing lands in shell history.)

## 4. Patch the config, with his number filled in

```bash
N=$(security find-generic-password -a lms -s lms/whatsapp-owner -w)
```

Write the patch with his number filled in. The file is written only when the number is all digits; otherwise the line prints a message, writes nothing, and the steps below fail harmlessly:

```bash
rm -f /tmp/remote.patch.json5; case "$N" in ''|*[^0-9]*) echo "lms/whatsapp-owner must be digits only: store it again (step 3) and stop here";; *) sed "s/__WHATSAPP_OWNER__/$N/g" ops/remote.patch.json5 > /tmp/remote.patch.json5;; esac; unset N
```

If it printed that message, stop and go back to step 3.

```bash
openclaw config patch --file /tmp/remote.patch.json5 --dry-run
```

Read the dry-run output: his number must appear with a leading `+` (for example `+15551234567`), once in `allowFrom`, in the WhatsApp binding and in the two command lists. If it does not, run the `rm -f` block below and stop.

```bash
openclaw config patch --file /tmp/remote.patch.json5
```

Remove the temporary file whether the patch worked or failed:

```bash
rm -f /tmp/remote.patch.json5
```

```bash
openclaw gateway restart
```

Confirm the plugin loaded and registered its commands (a wrong entry file shows here, not as silence on the phone):

```bash
openclaw plugins inspect lms-control
```

```bash
openclaw plugins doctor
```

`inspect` must show it enabled with the `lms` and `halt` commands; `doctor` must report no load issue for `lms-control`.

The patch enables two plugins, `whatsapp` and `lms-control`, so `verify_setup` check [5] now expects 6 enabled plugins (it was 4).

## 5. Install the snapshot job

```bash
cp ops/com.lms.agentcontext.plist ~/Library/LaunchAgents/
```

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.lms.agentcontext.plist
```

```bash
./ops/agent_context.py --print | head -20
```

## 6. Check

```bash
./ops/verify_setup.py | tail -15
```

```bash
./ops/agent_tools.py
```

Check [5] must report 6 enabled plugins. `[12] Chat control` must pass (it fails, not warns, if OpenClaw's WhatsApp config is unreadable or not an object, if the plugin is not enabled, or if `com.lms.agentcontext` is not loaded), and `agent_tools.py` must still show no tools for the lms-* agents.

Replies in WhatsApp's "Message yourself" chat start with `[openclaw]`. Leave that alone: it stops a reply that begins with `/lms ...` from ever being read back as a command. Do not set `messages.responsePrefix` to empty.

On Matthew's phone, in WhatsApp's "Message yourself" chat and in @MREOCbot:

1. `/lms help`: the list.
2. `/lms status`: "Running (not halted)" and the jobs.
3. `/lms tasks`: open tasks.
4. `/lms done <a real number>`: shows the task and asks for `yes`. Do **not** send `yes` unless he means it.
5. A plain question, "what's overdue?": an answer that names the snapshot time.
6. From **another phone**, a WhatsApp to his number saying `/lms status`: **no reply**, and `tail -3 ~/LMS/logs/remote.log` shows nothing new or `remote:refused`. OpenClaw's log shows the message dropped.

Leave `/lms halt` for the planned test below: it stops everything.

## 7. Planned halt test

While halted nothing answers on WhatsApp or @MREOCbot and the snapshot job writes nothing. Resume is only at the Mac (no remote resume, D-059).

1. From the phone, send `/lms halt` in WhatsApp's "Message yourself" chat or in @MREOCbot. Expect the "Halting in 2 seconds" reply, then those chats go quiet.
2. From the phone, send `/status` to @MREOC18bot. Expect it to report HALTED (it is the independent kill switch and keeps answering).
3. At the Mac, show the state (halted, by whatsapp or telegram):

```bash
./ops/halt.py --status
```

4. At the Mac, resume:

```bash
./ops/halt.py --resume
```

5. At the Mac, confirm it is running again:

```bash
./ops/halt.py --status
```

```bash
./ops/verify_setup.py | tail -15
```

## Undo

```bash
cp ~/.openclaw/openclaw.json.pre-chatcontrol ~/.openclaw/openclaw.json && openclaw gateway restart
```

That puts the config back. The WhatsApp plugin stays installed but, with the old config, is not used. If you instead switch chat control off in place, disable both plugin entries:

```bash
openclaw plugins disable whatsapp
```

```bash
openclaw plugins disable lms-control
```

```bash
openclaw gateway restart
```

If chat control is removed for good, set `D015_PLUGINS` in `ops/verify_setup.py` back to 4, or check [5] will fail.

```bash
launchctl bootout gui/$(id -u)/com.lms.agentcontext
```
````

- [ ] **Step 2: Add D-080 to `DECISIONS.md`**, directly above the line `### D-078 — Matthew's answers of Nov 4: ...`:

```markdown
### D-080 — Chat control from WhatsApp and Telegram: fixed commands, run by code

**Status:** Decided Nov 5 2026 · Matthew asked; designed and approved with the developer · changes D-056 · R10, R11 · spec [docs/superpowers/specs/2026-11-05-chat-control-design.md](docs/superpowers/specs/2026-11-05-chat-control-design.md)

Matthew asked to control OpenClaw and the Mac from WhatsApp and Telegram. D-056 kept the chat agent tool-less; that stays true, and control is added beside it:

- **Fixed commands, carried out by code.** An OpenClaw plugin (`lms/openclaw-plugin/lms-control`) registers `/lms`, which runs before the agent; `ops/remote.py` does the work. The list: status, brief, tasks, coverage, mail, whatsapp, backup, done, approve, deny, halt. `done`, `approve`, `deny` act only with `yes` in the same message.
- **D-015 baseline 4 → 6.** The patch enables `whatsapp` and `lms-control`; `D015_PLUGINS` is 6 in the same commit. Approved Nov 6. WhatsApp is not part of OpenClaw 2026.7.1-2: it is the separate plugin `@openclaw/whatsapp`, pinned to 2026.7.1 because its newest version needs OpenClaw 2026.11.1 (installed on the Mac Nov 6). Until the allowlist is written, its messages are switched off (`dmPolicy` and `groupPolicy` `disabled`), because its default answers unknown senders with a pairing code.
- **`/lms halt` always halts**, even if a halt flag already exists (the reply says so), so a stale or half-applied halt can be forced again.
- **The `[openclaw]` reply prefix in self-chat stays.** It is part of the safety: it stops a reply that begins with `/lms …` from ever being read back as a command. `messages.responsePrefix` is not to be emptied.
- **No resume from a phone** (D-059 unchanged). @MREOC18bot stays the separate kill switch.
- **Plain text** goes to `lms-orchestrator`, still with no tools, which answers from an hourly snapshot (`ops/agent_context.py`).
- **WhatsApp on his own number**, self-chat, allowlist of one. Accepted risk, his choice: OpenClaw's WhatsApp client is unofficial and WhatsApp can ban numbers using one; OpenClaw recommends a separate number. Recommended and declined.
- **Rejected:** giving the agent tools (an injected "approve 7" could be obeyed); our own WhatsApp client; a dedicated bot number.
```

- [ ] **Step 3: Update `REQUIREMENTS_REMAINING.md`.** At the end of the R10 row's first cell, before ` | MTG, SPEC §8.8 |`, append: ` **Nov 5 (D-080):** two-way: /lms commands on WhatsApp and @MREOCbot.` At the end of the R11 row's first cell, before ` | MTG, SPEC, ADD level B |`, append: ` **Nov 5 (D-080):** approvals answerable from his phone (/lms approve N yes); drafting replies still to build.`

- [ ] **Step 4: Update `PHASES.md` C2 row.** Before the final ` |` of the C2 row, append: ` · **Nov 5 (D-080):** /lms commands on @MREOCbot and WhatsApp, apply with ops/CHAT_CONTROL_APPLY.md`.

- [ ] **Step 5: Run the whole suite**, then commit

```bash
git add lms/ops/CHAT_CONTROL_APPLY.md DECISIONS.md REQUIREMENTS_REMAINING.md PHASES.md
git commit -m "D-080: chat control apply guide and records"
```

---

### Task 7: Apply on the Mac

The user runs `lms/ops/CHAT_CONTROL_APPLY.md` and pastes the output. Pushes to GitHub need the user's go-ahead first.

- [ ] **Step 1:** Ask the user to push (`git push origin master`) once Tasks 2–6 are reviewed.
- [ ] **Step 2:** Give the user `CHAT_CONTROL_APPLY.md` sections 1–7.
- [ ] **Step 3:** Read the pasted output. All of these must hold: `[12]` passes; `agent_tools.py` shows no tools; each phone check behaves as written; the stranger test gets no reply; `/lms halt` → @MREOC18bot `/status` reports HALTED → `./ops/halt.py --resume` → `[12]` passes; `tests/test_halt.py` passes; check [5] reports 6 enabled plugins.
- [ ] **Step 4:** Record "Applied on the Mac <date>" in D-080's status line, and commit.

---

## Mac facts (filled in by Task 1)

From `ops/chat_control_facts.sh` (Nov 5) and `ops/plugin_api_facts.sh` (Nov 6), OpenClaw 2026.7.1-2 installed at `/opt/homebrew/lib/node_modules/openclaw`. Task 1 is complete; the corrections are already in Tasks 3 and 5.

| # | Fact | Value on the Mac |
|---|---|---|
| 1 | `channels.whatsapp` keys | All the plan uses exist: `enabled`, `dmPolicy` (`pairing` default, `allowlist`, `open`, `disabled`), `allowFrom`, `selfChatMode`, `groupPolicy` (`open`, `disabled`, `allowlist`; default `allowlist`), `groupAllowFrom`, `configWrites`. Number format: E.164 with `+` everywhere: `allowFrom`, `commands.ownerAllowFrom` (`whatsapp:+…`) and bindings (`peer: { kind: "direct", id: "+…" }`, shipped `docs/channels/whatsapp.md`). The patch writes `+` before the stored digits. Self-chat: with the linked number in `allowFrom`, OpenClaw's self-chat safeguards turn on; under `dmPolicy: "allowlist"` only `allowFrom` senders are admitted, so strangers are dropped (this settles the docs' disagreement). Replies in self-chat are prefixed `[openclaw]` unless `messages.responsePrefix` is set; left as is on purpose: the prefix stops a reply that begins with `/lms …` from being read back as a command (Nov 6). |
| 2 | `plugins` keys to load and enable a local plugin | `plugins.load.paths` and `plugins.entries.<id>.enabled` (shipped `docs/tools/plugin.md`). Also `plugins.enabled` (global, default true) and `plugins.allow`: when set, only listed ids load, so `lms-control` must be added to it if it is set. Workspace-origin plugins are off until enabled or allowlisted. On the Mac (Nov 6) `plugins.allow` and `plugins.load` are both unset, so the patch adds `load.paths` without dropping anything and needs no `allow` entry. Enabled entries today: `lmstudio`, `telegram`; `entries` is an object, which a patch merges, so they stay. Current `commands` and `bindings` equal the Telegram half of the patch exactly, so repeating them loses nothing. Telegram rejects a plugin command whose name clashes with a Telegram native command; `/lms` and `/halt` are not among them. |
| 3 | Plugin entry file and manifest fields | Native plugin = `openclaw.plugin.json` + a runtime module loaded in process; manifest has at least `id`, `name`. Entry: the shipped docs use `export default definePluginEntry({ id, name, register(api) {...} })` from `openclaw/plugin-sdk/plugin-entry` (the SDK also exports the `OpenClawPluginDefinition` type it builds). Our `index.js` exports that same object directly, with no import from OpenClaw, plus a `package.json` (`"type": "module"`, `"openclaw": { "extensions": ["./index.js"] }`). Not proven until the apply step's `openclaw plugins inspect lms-control`; the `definePluginEntry` import is the fallback (Task 5, Step 4b). | |
| 4 | `registerCommand` options; handler context fields; empty reply behaviour | **Plugin commands bypass the LLM** (shipped `docs/plugins/sdk-overview.md`: "Custom command (bypasses the LLM)"), so the design holds. Definition: `name`, `description`, `acceptsArgs?`, `requireAuth?` (default true), `channels?`, `handler`, plus native/guidance extras we do not use. Context (`PluginCommandContext`): `channel`, `senderId?`, `args?` (raw text after the name), `isAuthorizedSender`, `senderIsOwner?`, `from?`, `to?`, `commandBody`. Result: `ReplyPayload & { continueAgent?, suppressReply? }`; `continueAgent` is off unless set, so the agent never sees a command. For "send nothing" the plugin returns `{ suppressReply: true }`, not `{ text: "" }`. |
| 5 | `lms` / `halt` free? | Both free: neither is in the built-in list (`/stop`, `/status`, `/approve`, `/tasks`, `/plugins`, `/config` … are). Core-reserved names can be claimed only by bundled plugins, which does not affect us. |
| 6 | Injected workspace files; per-file character limit | `AGENTS.md`, `BOOTSTRAP.md`, `HEARTBEAT.md`, `IDENTITY.md`, `MEMORY.md`, `SOUL.md`, `TOOLS.md`, `USER.md`. `agents.defaults.bootstrapMaxChars` default 20,000 per file; `bootstrapTotalMaxChars` default 60,000. The workspace's files total 11,649 bytes today, so `USER.md` + a 15,000-char block stays under both. `USER.md` (537 bytes) as planned. |
| 7 | WhatsApp link command | `openclaw channels login --channel whatsapp` (options `--account`, `--verbose`), as planned. |
| 8 | WhatsApp plugin | Not part of OpenClaw 2026.7.1-2 (`plugins enable whatsapp`: "Plugin not found"). Separate plugin `@openclaw/whatsapp`; its newest (2026.11.1) needs OpenClaw >=2026.11.1. `openclaw plugins install @openclaw/whatsapp@2026.7.1` installed and loaded it (Nov 6). |
