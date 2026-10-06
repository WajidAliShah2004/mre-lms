"""Phase 7b: ops/phase7b.patch.json5, the gateway's Telegram channel (D-060).

Parsed into real structure, not grepped: these assertions are the security
properties of the channel, and a regex that matched a comment would pass a
patch that does the opposite.
"""

import json
import re
from pathlib import Path

import pytest

PATCH = Path(__file__).resolve().parents[1] / "ops" / "phase7b.patch.json5"
OWNER = "8783061626"


def load_json5ish(text: str) -> dict:
    """Enough JSON5 for our patches: // comments, bare keys, trailing commas."""
    out = []
    for line in text.splitlines():
        # strip // comments that are not inside a string
        in_str, i = False, 0
        while i < len(line):
            c = line[i]
            if c == '"' and (i == 0 or line[i - 1] != "\\"):
                in_str = not in_str
            elif not in_str and line.startswith("//", i):
                line = line[:i]
                break
            i += 1
        out.append(line)
    s = "\n".join(out)
    s = re.sub(r'([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:', r'\1"\2":', s)
    s = re.sub(r",(\s*[}\]])", r"\1", s)
    return json.loads(s)


@pytest.fixture(scope="module")
def patch():
    return load_json5ish(PATCH.read_text(encoding="utf-8"))


def test_the_parser_is_not_vacuous():
    assert load_json5ish('{ a: { b: [1, 2,], }, // x\n c: "d//e", }') == \
        {"a": {"b": [1, 2]}, "c": "d//e"}


def test_strangers_cannot_pair(patch):
    """The schema default is "pairing": anyone can ask. Allowlist, one id."""
    tg = patch["channels"]["telegram"]
    assert tg["dmPolicy"] == "allowlist"
    assert tg["allowFrom"] == [OWNER]
    assert tg["groupPolicy"] == "disabled" and tg["groupAllowFrom"] == []


def test_the_token_is_a_reference_through_its_own_provider(patch):
    ref = patch["channels"]["telegram"]["botToken"]
    assert isinstance(ref, dict), "a literal token in openclaw.json"
    assert ref == {"source": "exec", "provider": "telegram", "id": "telegram-bot-token"}
    prov = patch["secrets"]["providers"]["telegram"]
    assert prov["command"] == "/usr/bin/security"
    assert "lms/telegram-bot-token" in prov["args"]
    assert "default" not in patch["secrets"]["providers"], (
        "the default provider holds the gateway token; never touch it here")


def test_the_halt_bot_token_is_not_the_chat_bot_token(patch):
    """Two programs cannot both poll one bot (D-059)."""
    args = patch["secrets"]["providers"]["telegram"]["args"]
    assert "lms/telegram-halt-token" not in args


def test_telegram_routes_to_a_tool_less_agent(patch):
    b = patch["bindings"]
    assert len(b) == 1
    assert b[0]["agentId"].startswith("lms-") and b[0]["agentId"] != "main"
    assert b[0]["match"] == {"channel": "telegram",
                             "peer": {"kind": "direct", "id": OWNER}}


def test_no_chat_command_can_change_the_machine(patch):
    c = patch["commands"]
    for k in ("bash", "config", "mcp", "plugins", "debug", "restart"):
        assert c[k] is False, f"/{k} enabled from chat"
    assert c["ownerAllowFrom"] == [f"telegram:{OWNER}"]
    assert c["allowFrom"] == {"telegram": [OWNER]}
    assert patch["channels"]["telegram"]["configWrites"] is False


def test_the_owner_is_the_same_person_everywhere(patch):
    found = set(re.findall(r"\d{9,}", json.dumps(patch)))
    assert found == {OWNER}
