"""Registry and configuration invariants.

Config errors must stop the daemon starting, not surface weeks later as
documents in a folder named after a typo.
"""

from pathlib import Path

import pytest

from core.pipeline.registry import RegistryError, load_registry

CONFIG = Path(__file__).resolve().parents[1] / "config"


@pytest.fixture
def registry():
    return load_registry(CONFIG)


def test_all_four_businesses_and_five_people_present(registry):
    for eid in ("B_MRE", "B_CHS", "B_ATL", "B_MLE"):
        assert registry.get(eid).kind == "business"
    for eid in ("P_MRE", "P_JG", "P_AE", "P_PE", "P_HH"):
        assert registry.get(eid).kind == "person"


def test_system_buckets_exist(registry):
    """Low-confidence and bulk must have somewhere real to land."""
    assert registry.get("UNASSIGNED").kind == "system"
    assert registry.get("JUNK").kind == "system"


def test_trees_are_unique(registry):
    trees = [e.tree for e in registry.entities.values()]
    assert len(trees) == len(set(trees))


def test_both_taxonomies_have_unsorted(registry):
    assert "UNSORTED" in registry.personal_categories
    assert "UNSORTED" in registry.business_categories


def test_business_cannot_use_personal_category(registry):
    with pytest.raises(RegistryError):
        registry.validate_category("B_MRE", "WEDDING")
    with pytest.raises(RegistryError):
        registry.validate_category("P_MRE", "COMMISSIONS")


def test_email_routing_beats_guessing(registry):
    assert registry.resolve_by_email("matthew@mrecai.com").entity_id == "B_MRE"
    assert registry.resolve_by_email("mre@atlase.ai").entity_id == "B_ATL"
    assert registry.resolve_by_email("mattyeps@gmail.com").entity_id == "P_MRE"
    assert registry.resolve_by_email("nobody@example.com") is None


def test_domain_routing(registry):
    assert registry.resolve_by_domain("billing@mleca.com").entity_id == "B_MLE"
    assert registry.resolve_by_domain("atlase.ai").entity_id == "B_ATL"


def test_unknown_entity_raises(registry):
    with pytest.raises(RegistryError):
        registry.get("B_NOPE")


def test_min_confidence_matches_d007_threshold(registry):
    assert registry.min_confidence == 0.60


def test_placeholders_are_reported_not_hidden(registry):
    """C16 is unanswered, so this SHOULD list entities today.

    The point of the test is that placeholders stay visible. When C16 lands,
    flip the assertion to `== []` and it becomes the acceptance gate.
    """
    outstanding = registry.placeholders()
    assert isinstance(outstanding, list)
    assert "B_CHS" in outstanding, "C.H. Shink legal name/EIN still outstanding"


@pytest.mark.xfail(reason="C16 unanswered: legal names and EINs not supplied", strict=False)
def test_no_placeholders_remain(registry):
    """Acceptance gate. Passes only once C16 is filled in."""
    assert registry.placeholders() == []
