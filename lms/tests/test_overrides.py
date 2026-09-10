"""Deterministic overrides — the cases where the model is not consulted.

D-027 was this block existing in taxonomy.yaml while nothing read it: jury
duty, the client's own worked example, was decided by the model for weeks. The
lesson was not "wire it up" — it was that a rule nobody tests is a rule that
can quietly stop running.

Changing `match_overrides` to return (override, signal) pairs broke nothing in
342 tests, because nothing called it. That is the same gap, so these are
direct.
"""

from pathlib import Path

import pytest

from core.pipeline.registry import Override, RegistryError, load_registry

CONFIG = Path(__file__).resolve().parents[1] / "config"


@pytest.fixture
def reg():
    return load_registry(CONFIG)


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def test_any_one_of_several_headers_fires_it():
    """List-Unsubscribe ALONE matched 2 messages out of a real week of 22, and
    both were from senders already behaving well."""
    ov = Override(match_headers=("List-Unsubscribe", "List-Id", "Feedback-ID"),
                  tag="BULK", urgency="NONE")

    for header in ("List-Id", "Feedback-ID", "list-unsubscribe"):
        fired, why = ov.matches("", {header: "x"})
        assert fired, f"{header} did not fire it"


def test_the_signal_that_fired_is_the_one_reported():
    """With several possible headers, "header List-Unsubscribe" and "header
    List-Id" are different facts. A rationale naming the wrong one sends a
    reviewer looking for something that is not in the message."""
    ov = Override(match_headers=("List-Unsubscribe", "List-Id"), tag="BULK")
    _, why = ov.matches("", {"List-Id": "<news.example.com>"})
    assert why == "header List-Id"


def test_a_header_the_message_lacks_does_not_fire_it():
    ov = Override(match_headers=("List-Unsubscribe",), tag="BULK")
    fired, why = ov.matches("buy now", {"From": "a@b.c"})
    assert not fired and why == ""


def test_text_matching_reports_only_what_was_actually_found():
    ov = Override(match_any_text=("jury duty", "summons", "subpoena"),
                  category="LEGAL", subcategory="court", urgency="HIGH")
    fired, why = ov.matches("You are hereby summoned — SUMMONS enclosed", {})
    assert fired
    assert "summons" in why
    assert "jury duty" not in why, "it reported a phrase that is not there"


def test_matching_is_case_insensitive_both_ways():
    ov = Override(match_any_text=("final notice",), urgency="CRITICAL")
    assert ov.matches("FINAL NOTICE: policy lapses Friday", {})[0]

    ov2 = Override(match_headers=("List-Unsubscribe",), tag="BULK")
    assert ov2.matches("", {"LIST-UNSUBSCRIBE": "<mailto:x>"})[0]


# ---------------------------------------------------------------------------
# The shipped configuration
# ---------------------------------------------------------------------------

def test_the_bulk_override_is_wired_to_real_headers(reg):
    bulk = [o for o in reg.overrides if o.tag == "BULK"]
    assert bulk, "nothing tags BULK — bulk mail files as business records"
    assert "List-Unsubscribe" in bulk[0].match_headers
    assert len(bulk[0].match_headers) > 1, (
        "List-Unsubscribe alone matched 2 of 22 real messages")


def test_bulk_does_not_force_a_category(reg):
    """A newsletter from an accountant is still about accounting. BULK says how
    LOUD it should be, not what it is about."""
    bulk = [o for o in reg.overrides if o.tag == "BULK"][0]
    assert bulk.category is None
    assert bulk.urgency == "NONE"


def test_the_jury_duty_override_still_exists(reg):
    """The client's own worked example, and the reason D-027 exists."""
    fired = reg.match_overrides("A SUMMONS for jury duty", {})
    assert fired, "jury duty no longer overrides anything"
    ov, why = fired[0]
    assert ov.category == "LEGAL" and ov.subcategory == "court"
    assert ov.urgency == "HIGH"


def test_match_overrides_returns_pairs(reg):
    """The contract classify.py depends on. Changing this broke nothing in 342
    tests because nothing called it."""
    fired = reg.match_overrides("summons", {"List-Unsubscribe": "<mailto:x>"})
    assert len(fired) >= 2
    for item in fired:
        ov, why = item
        assert isinstance(why, str) and why


def test_nothing_fires_on_an_ordinary_message(reg):
    assert reg.match_overrides(
        "Please find the signed page attached. Thanks, Matthew",
        {"From": "matthew@mrecai.com"}) == []


# ---------------------------------------------------------------------------
# Load-time validation
# ---------------------------------------------------------------------------

def test_an_override_matching_nothing_is_refused(tmp_path):
    from core.pipeline.registry import _load_overrides
    with pytest.raises(RegistryError, match="never fire"):
        _load_overrides([{"category": "LEGAL"}], personal={}, business={})


def test_an_unknown_urgency_is_refused():
    from core.pipeline.registry import _load_overrides
    with pytest.raises(RegistryError, match="urgency"):
        _load_overrides([{"match": {"any_text": ["x"]}, "urgency": "VERY_HIGH"}],
                        personal={}, business={})


def test_a_single_header_string_still_loads():
    """The old one-name form must keep working — taxonomy.yaml is edited by
    hand and a config change should not need a code change."""
    from core.pipeline.registry import _load_overrides
    out = _load_overrides([{"match": {"header": "List-Id"}, "tag": "BULK"}],
                          personal={}, business={})
    assert out[0].match_headers == ("List-Id",)
    assert out[0].matches("", {"list-id": "<x>"})[0]
