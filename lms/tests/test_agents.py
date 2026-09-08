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

    This is the control that actually runs on the Mac. The per-agent lists in
    agents.fragment.json describe intent; OpenClaw 2026.7.1-2 has no such key.
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
    for tier, body in fragment["models"]["tiers"].items():
        assert body["provider"] == "lmstudio", f"{tier} escapes the local provider"


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
