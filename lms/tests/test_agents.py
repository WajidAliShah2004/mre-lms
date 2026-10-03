"""Agent topology invariants — Phase 6.

Under D-009 the LMS runs as Matthew's own macOS user, so an injected agent
inherits his full session. The compensating control is that no model-facing
agent can act at all: agents return text, code does things.

These tests are that control expressed as something CI can fail on. If one of
them goes red, the sandbox is gone and the system should not be reading mail.
"""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FRAGMENT = ROOT / "openclaw" / "agents" / "agents.fragment.json"
SKILLS = ROOT / "openclaw" / "skills"

EXPECTED_AGENTS = {"lms-orchestrator", "lms-classifier", "lms-drafter"}


@pytest.fixture
def fragment():
    return json.loads(FRAGMENT.read_text(encoding="utf-8"))


@pytest.fixture
def agents(fragment):
    return {k: v for k, v in fragment["agents"].items() if not k.startswith("_")}


def test_expected_agents_present(agents):
    assert set(agents) == EXPECTED_AGENTS


@pytest.mark.parametrize("name", sorted(EXPECTED_AGENTS))
def test_every_agent_has_zero_tools(agents, name):
    """The whole security model in one assertion."""
    assert agents[name]["tools"]["allow"] == []


@pytest.mark.parametrize("name", sorted(EXPECTED_AGENTS))
def test_tools_key_is_present_not_merely_absent(agents, name):
    """An absent tools key is not the same as an empty one.

    Absent means "whatever the default is", and the default is not ours to
    assume — D-015 established that OpenClaw defaults are permissive and
    change between releases. Empty means empty.
    """
    assert "tools" in agents[name]
    assert "allow" in agents[name]["tools"]


@pytest.mark.parametrize("name", sorted(EXPECTED_AGENTS))
def test_skill_front_matter_matches_config(name):
    """A SKILL.md that claims tools it isn't given, or vice versa, is a lie
    a future maintainer will believe."""
    path = SKILLS / name / "SKILL.md"
    assert path.exists(), f"{name} has no SKILL.md"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---"), f"{name}: no front matter"
    head = text.split("---")[1]
    assert "tools: []" in head, f"{name}: front matter must declare tools: []"


def test_no_cloud_provider(fragment):
    """D-003. The guards that would make a cloud tier safe are not built."""
    banned = {"anthropic", "openai", "google", "mistral", "cohere",
              "azure", "bedrock", "vertex", "openrouter", "together", "xai"}
    providers = {k for k in fragment["models"]["providers"] if not k.startswith("_")}
    assert not (set(map(str.lower, providers)) & banned)


# ---------------------------------------------------------------------------
# The applied config — what is actually on the machine
# ---------------------------------------------------------------------------

PATCH = ROOT / "ops" / "phase6a.patch.json5"


@pytest.fixture
def patch_text():
    assert PATCH.exists(), "the applied Phase 6 patch is missing from the repo"
    return PATCH.read_text(encoding="utf-8")


def test_applied_patch_locks_tools_absolutely(patch_text):
    """tools.allow: [] replaces profile-derived defaults, it does not trim them.

    This is the control that covers every agent on the Mac, including ones a
    future update adds. Per-agent lists exist too (agents.list[].tools, D-056)
    and phase6d.patch.json5 sets them; they narrow this, never widen it.
    """
    assert "allow: []" in patch_text


def test_applied_patch_denies_shell_access(patch_text):
    """commands.bash runs host shell commands as this user.

    Under D-009 that is not a sandbox escape, it is the whole machine.
    """
    for needle in ("bash: false", "config: false", "mcp: false", "plugins: false"):
        assert needle in patch_text, f"{needle} missing from the applied patch"
    for tool in ("bash", "exec", "shell"):
        assert f'"{tool}"' in patch_text, f"{tool} not in tools.deny"


def test_models_mode_replace_is_documented_as_withheld(patch_text):
    """It validated but was deliberately not applied.

    models.providers is empty, so "replace" could yield an empty catalog and
    fail at first inference rather than at restart. If someone applies it
    later, this test should be updated deliberately, not silently.
    """
    assert "DELIBERATELY NOT INCLUDED" in patch_text
    assert "models.mode" in patch_text


def test_every_provider_is_loopback(fragment):
    for name, body in fragment["models"]["providers"].items():
        url = body.get("baseUrl", "")
        assert url.startswith(("http://localhost", "http://127.0.0.1")), \
            f"provider {name} points off-machine: {url}"


def test_every_tier_uses_lmstudio(fragment):
    """Underscore keys are commentary, not tiers — skip them."""
    for tier, body in fragment["models"]["tiers"].items():
        if tier.startswith("_"):
            continue
        assert body["provider"] == "lmstudio", f"{tier} escapes the local provider"


def test_every_tier_names_a_confirmed_model(fragment):
    """Tier names are the contract; model names are substitutable.

    But an unnamed model means nobody checked the tier is actually loaded,
    which is how you discover at acceptance that TIER-OCR was never downloaded.
    These were confirmed against /v1/models on 2026-09-08.
    """
    for tier, body in fragment["models"]["tiers"].items():
        if tier.startswith("_"):
            continue
        assert body.get("model"), f"{tier} names no model"


def test_schedule_timezone_is_a_named_zone(fragment):
    """Rec 21. An offset is right half the year and silently an hour wrong
    for the other half."""
    tz = fragment["scheduling"]["timezone"]
    assert tz == "America/New_York"
    assert not tz.startswith(("+", "-", "UTC"))


def test_fragment_contains_no_secrets(fragment):
    """The repo holds pointers only (spec 12.4)."""
    blob = json.dumps(fragment).lower()
    for needle in ("password", "secret", "bearer", "sk-", "token\":", "apikey\":"):
        if needle in blob:
            # the lmstudio placeholder is the one allowed match
            assert "not-required-loopback" in blob, f"possible secret in fragment: {needle}"


def test_owner_allowlist_is_empty_and_that_is_recorded(fragment):
    """C2 is open. This test documents the gap rather than hiding it.

    When Phase 7 lands the numeric Telegram id, flip this to assert the list
    is non-empty and it becomes the check that /halt has an owner.
    """
    assert fragment["commands"]["ownerAllowFrom"] == [], \
        "ownerAllowFrom is now populated — update this test to assert non-empty"


def test_no_third_party_skills(fragment):
    """D-002 — every skill is hand-authored in-repo."""
    on_disk = {p.parent.name for p in SKILLS.glob("*/SKILL.md")}
    referenced = {a["skill"] for a in fragment["agents"].values()
                  if isinstance(a, dict) and "skill" in a}
    assert referenced <= on_disk, f"agents reference skills not in the repo: {referenced - on_disk}"


# --- D-052: the OpenClaw provider patch and core/ agree on model ids --------

def _patch_text(name: str) -> str:
    from pathlib import Path
    return (Path(__file__).resolve().parents[1] / "ops" / name).read_text()


def test_phase6b_model_ids_match_core_tiers():
    """Three places name the models. If they drift, OpenClaw and core/ run
    different models under the same tier name and nothing errors."""
    import re
    from core.adapters.lmstudio import TIER_MODELS
    text = _patch_text("phase6b.patch.json5")
    ids = re.findall(r'^\s*id:\s*"([^"]+)"', text, re.M)
    assert ids == [TIER_MODELS["TIER-L1"], TIER_MODELS["TIER-L2"]]
    primary = re.search(r'primary:\s*"lmstudio/([^"]+)"', text).group(1)
    assert primary == TIER_MODELS["TIER-L1"]


def test_phase6b_provider_is_loopback_and_no_cloud():
    import re
    text = _patch_text("phase6b.patch.json5")
    urls = re.findall(r'baseUrl:\s*"([^"]+)"', text)
    assert urls == ["http://localhost:1234/v1"]
    code = "\n".join(l.split("//", 1)[0] if not l.strip().startswith("baseUrl") else l
                     for l in text.splitlines())
    for cloud in ("anthropic", "openai.com", "googleapis", "openrouter"):
        assert cloud not in code


def test_phase6c_is_only_the_mode_switch():
    import re
    text = _patch_text("phase6c.patch.json5")
    code = "\n".join(l.split("//", 1)[0] for l in text.splitlines())
    assert re.sub(r"\s+", "", code) == '{models:{mode:"replace"},}'


def test_phase6b_cost_has_every_field_the_catalog_schema_requires():
    """Oct 3: `cost: { input: 0, output: 0 }` passed `config patch` validation
    but the per-agent model catalog (plugins/lmstudio/catalog.json) rejected it:
    "cost.cacheRead: must have required properties cacheRead, cacheWrite".
    The catalog then failed to load and every built-in cloud model reappeared
    in `models list --all`, which is what 6c was meant to remove."""
    import re
    text = _patch_text("phase6b.patch.json5")
    costs = re.findall(r"cost:\s*\{([^}]*)\}", text)
    assert len(costs) == 2
    for c in costs:
        keys = set(re.findall(r"(\w+)\s*:", c))
        assert {"input", "output", "cacheRead", "cacheWrite"} <= keys, c


# --- D-056: the lms-* agents on the gateway, and the check that proves them --

def _agent_tools():
    import importlib.util
    path = Path(__file__).resolve().parents[1] / "ops" / "agent_tools.py"
    spec = importlib.util.spec_from_file_location("agent_tools", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _phase6d_entries():
    """(id, block) for each agents.list entry, comments stripped."""
    import re
    text = _patch_text("phase6d.patch.json5")
    code = "\n".join(l.split("//", 1)[0] for l in text.splitlines())
    ids = list(re.finditer(r'id:\s*"([^"]+)"', code))
    return [(m.group(1), code[m.end():ids[i + 1].start() if i + 1 < len(ids) else len(code)])
            for i, m in enumerate(ids)]


def test_phase6d_keeps_main_as_the_default():
    """Once agents.list exists it is the whole set; leaving main out would
    change which agent answers, silently."""
    entries = _phase6d_entries()
    assert entries[0][0] == "main"
    assert "default: true" in entries[0][1]
    assert sum("default: true" in body for _, body in entries) == 1


def test_phase6d_defines_exactly_the_expected_agents():
    assert {i for i, _ in _phase6d_entries()} == EXPECTED_AGENTS | {"main"}


@pytest.mark.parametrize("name", sorted(EXPECTED_AGENTS))
def test_phase6d_agent_has_no_tools_and_no_skills(name):
    body = dict(_phase6d_entries())[name]
    assert "allow: []" in body
    assert 'deny: ["session_status"]' in body
    assert "skills: []" in body
    assert "alsoAllow" not in body


def test_phase6d_models_match_core_tiers_and_the_checker():
    import re
    from core.adapters.lmstudio import TIER_MODELS
    want = {"lms-orchestrator": "TIER-L1", "lms-classifier": "TIER-L1",
            "lms-drafter": "TIER-L2"}
    entries = dict(_phase6d_entries())
    checker = _agent_tools().EXPECTED_MODEL
    for agent, tier in want.items():
        model = re.search(r'model:\s*"lmstudio/([^"]+)"', entries[agent]).group(1)
        assert model == TIER_MODELS[tier] == checker[agent], agent


GATEWAY_JSON = """│
\U0001f99e OpenClaw 2026.7.1-2 (0790d9f) — banner
{
  "result": {"payloads": [{"text": "READY"}]},
  "meta": {
    "winnerProvider": "lmstudio",
    "winnerModel": "qwen3.6-35b-a3b-mlx",
    "systemPromptReport": {
      "skills": {"entries": []},
      "tools": {"listChars": 0, "schemaChars": 89,
                "entries": [{"name": "session_status"}]}
    }
  }
}"""


def test_checker_reads_tools_past_the_banner():
    """Shape as seen on the Mac Oct 3: `main` was offered session_status."""
    r = _agent_tools().report(GATEWAY_JSON)
    assert r == {"tools": ["session_status"], "text": "READY",
                 "provider": "lmstudio", "model": "qwen3.6-35b-a3b-mlx"}


def test_checker_fails_an_agent_offered_any_tool():
    at = _agent_tools()
    bad = at.problems("lms-classifier", at.report(GATEWAY_JSON))
    assert bad == ["offered tools: session_status"]
    clean = at.report(GATEWAY_JSON.replace('{"name": "session_status"}', ""))
    assert at.problems("lms-classifier", clean) == []


def test_checker_fails_the_wrong_model():
    at = _agent_tools()
    r = at.report(GATEWAY_JSON.replace('{"name": "session_status"}', ""))
    assert at.problems("lms-drafter", r) == [
        "answered by model 'qwen3.6-35b-a3b-mlx', expected 'qwen3.5-122b-a10b'"]
