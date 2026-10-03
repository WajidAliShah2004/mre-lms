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


# --- Oct 3: legal names from the owner records; EINs kept out of git --------

import re
import shutil

from core.pipeline.registry import LOCAL_FILE


def test_legal_names_filled_from_owner_records(registry):
    assert registry.get("B_MRE").name == "MRE Consulting & Insurance LLC"
    assert registry.get("B_MLE").name == "MLE Consulting Agency LLC"
    assert registry.get("B_ATL").name == "Atlase AI Inc."
    outstanding = registry.placeholders()
    assert not {"B_MRE", "B_MLE", "B_ATL"} & set(outstanding)
    assert {"B_CHS", "P_AE", "P_PE"} <= set(outstanding)


def test_committed_registry_holds_no_ein():
    """entities.yaml is in git. An EIN in it is in the history forever."""
    text = (CONFIG / "entities.yaml").read_text(encoding="utf-8")
    assert not re.search(r"\b\d{2}-\d{7}\b", text)


def test_local_ein_file_is_gitignored():
    gitignore = (CONFIG.parents[1] / ".gitignore").read_text(encoding="utf-8")
    assert f"lms/config/{LOCAL_FILE}" in gitignore.splitlines()


@pytest.fixture
def config_copy(tmp_path):
    for name in ("entities.yaml", "taxonomy.yaml"):
        shutil.copy(CONFIG / name, tmp_path / name)
    return tmp_path


def test_local_file_supplies_eins_and_routing_aliases(config_copy):
    (config_copy / LOCAL_FILE).write_text(
        'businesses:\n'
        '  B_MLE: { ein: "12-3456789", other_eins: ["98-7654321"] }\n',
        encoding="utf-8")
    reg = load_registry(config_copy)
    mle = reg.get("B_MLE")
    assert mle.ein == "12-3456789"
    assert {"12-3456789", "98-7654321"} <= set(mle.aliases)

    from core.pipeline.classify import _match_alias
    form = "Form 1099-NEC  PAYER'S TIN 98-7654321  RECIPIENT ..."
    assert _match_alias(reg, form).entity_id == "B_MLE"


def test_without_the_local_file_nothing_breaks(config_copy):
    assert load_registry(config_copy).get("B_MLE").ein is None


@pytest.mark.parametrize("body", [
    'businesses:\n  B_MLE: { ein: "123456789" }\n',        # no hyphen
    'businesses:\n  B_MLE: { ein: "12-345678" }\n',        # short
    'businesses:\n  P_MRE: { ein: "12-3456789" }\n',       # not a business
    'businesses:\n  B_NOPE: { ein: "12-3456789" }\n',      # unknown
])
def test_malformed_local_file_stops_the_load(config_copy, body):
    """A mistyped EIN routes a tax form to the wrong business."""
    (config_copy / LOCAL_FILE).write_text(body, encoding="utf-8")
    with pytest.raises(RegistryError):
        load_registry(config_copy)


def test_legal_name_aliases_match_whole_words(registry):
    from core.pipeline.classify import _match_alias
    assert _match_alias(registry, "Invoice to Atlase AI Inc. for services").entity_id == "B_ATL"
    assert _match_alias(registry, "MLE Consulting Agency LLC — policy N8PL").entity_id == "B_MLE"
    assert _match_alias(registry, "Licence BR-1832230").entity_id == "B_MRE"
