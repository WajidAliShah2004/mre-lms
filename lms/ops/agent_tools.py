#!/usr/bin/env python3
"""Prove, from the gateway's own report, that each LMS agent has no tools.

    ./ops/agent_tools.py                    # the three lms-* agents
    ./ops/agent_tools.py main               # any agent id

Sends each agent one harmless message through the gateway (`openclaw agent`,
never `agent exec` — D-052) and reads the tool list OpenClaw reports it
offered the model. verify_setup [1]/[2] check the repo; this checks the
machine. Exit 1 if any agent was offered a tool or answered from the wrong
model.

Standard library only: it runs before, and independently of, the .venv.
"""

from __future__ import annotations

import json
import subprocess
import sys

PROMPT = "Reply with exactly the word READY and nothing else."

# Must match ops/phase6d.patch.json5 — tests/test_agents.py enforces it.
EXPECTED_MODEL = {
    "lms-orchestrator": "qwen3.6-35b-a3b-mlx",
    "lms-classifier": "qwen3.6-35b-a3b-mlx",
    "lms-drafter": "qwen3.5-122b-a10b",
}


def _walk(node):
    if isinstance(node, dict):
        for k, v in node.items():
            yield k, v
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def report(raw: str) -> dict:
    """Tool names, reply text and winning model from `openclaw agent --json`.

    The CLI prints a banner before the JSON, so parsing starts at the first
    brace. Tools appear as {"tools": {"entries": [{"name": ...}]}}; every
    such list is collected, so a tool reported anywhere counts.
    """
    data = json.loads(raw[raw.index("{"):])
    tools: list[str] = []
    out = {"tools": tools, "text": None, "provider": None, "model": None}
    for key, value in _walk(data):
        if key == "tools" and isinstance(value, dict):
            tools += [e.get("name", "?") for e in value.get("entries", [])
                      if isinstance(e, dict)]
        elif key == "text" and out["text"] is None and isinstance(value, str):
            out["text"] = value
        elif key == "winnerProvider" and out["provider"] is None:
            out["provider"] = value
        elif key == "winnerModel" and out["model"] is None:
            out["model"] = value
    return out


def problems(agent: str, r: dict) -> list[str]:
    found = []
    if r["tools"]:
        found.append(f"offered tools: {', '.join(r['tools'])}")
    if r["provider"] != "lmstudio":
        found.append(f"answered by provider {r['provider']!r}, not lmstudio")
    want = EXPECTED_MODEL.get(agent)
    if want and r["model"] != want:
        found.append(f"answered by model {r['model']!r}, expected {want!r}")
    return found


def main() -> int:
    agents = sys.argv[1:] or list(EXPECTED_MODEL)
    failed = 0
    for agent in agents:
        proc = subprocess.run(
            ["openclaw", "agent", "--agent", agent, "--json", "--message", PROMPT],
            capture_output=True, text=True, timeout=600)
        try:
            r = report(proc.stdout)
        except ValueError:
            print(f"FAIL  {agent}: no JSON from openclaw (exit {proc.returncode})")
            print("      " + (proc.stderr or proc.stdout).strip()[-400:])
            failed += 1
            continue
        bad = problems(agent, r)
        status = "FAIL" if bad else "PASS"
        print(f"{status}  {agent}: tools={r['tools'] or 'none'}  "
              f"model={r['provider']}/{r['model']}  reply={r['text']!r}")
        for b in bad:
            print(f"      {b}")
        failed += bool(bad)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
