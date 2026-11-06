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
    assert p["entries"] == {"lms-control": {"enabled": True}}


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


@pytest.mark.parametrize("raw", ["not json", "", "null", "[]", "42"])
def test_unreadable_whatsapp_config_fails_closed(raw):
    assert vs.whatsapp_config_problems(raw, "15551234567") != []


def test_good_whatsapp_config_text_passes():
    raw = ('{"enabled": true, "dmPolicy": "allowlist", "allowFrom": ["+15551234567"],'
           ' "selfChatMode": true, "groupPolicy": "disabled"}')
    assert vs.whatsapp_config_problems(raw, "15551234567") == []
