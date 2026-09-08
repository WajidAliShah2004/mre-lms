"""LM Studio response handling.

These exist because of a bug that no mocked-model test could have caught, and
that broke every classification silently.

Measured on the Mac, 2026-09-08, qwen3.6-35b-a3b-mlx:

    WITHOUT response_format:  content = the answer
                              reasoning_content = the thinking
    WITH    response_format:  content = ""
                              reasoning_content = the constrained schema JSON

The runtime routes constrained decoding into the reasoning channel. Reading
only `content` — the documented, obvious thing — returns an empty string on
every classification, which surfaces as "model error" and quarantines
everything.

The fixtures below are copied from real responses, not invented.
"""

import json

import pytest

from core.adapters.lmstudio import (Completion, LMStudio, ModelError,
                                    NotLoopbackError, _first_json_object,
                                    assert_loopback)


# ---------------------------------------------------------------------------
# The empty-content bug
# ---------------------------------------------------------------------------

def test_json_parses_from_the_reasoning_channel():
    """The exact shape the Mac returned under json_schema."""
    payload = ('{"domain": "BUSINESS", "entity_id": "B_MRE", '
               '"category": "FINANCE", "confidence": 0.9}')
    c = Completion(text=payload, model="qwen3.6-35b-a3b-mlx", elapsed_s=2.0,
                   finish_reason="stop", channel="reasoning_content")
    assert c.json()["entity_id"] == "B_MRE"


def test_the_channel_is_recorded():
    """When a classification is wrong, the first question is where the text
    came from. Guessing afterwards is not possible."""
    c = Completion(text="{}", model="m", elapsed_s=0.1, channel="reasoning_content")
    assert c.channel == "reasoning_content"


def test_error_message_names_the_channel():
    c = Completion(text="not json at all", model="m", elapsed_s=0.1,
                   finish_reason="stop", channel="reasoning_content")
    with pytest.raises(ModelError) as exc:
        c.json()
    assert "reasoning_content" in str(exc.value)


# ---------------------------------------------------------------------------
# Reasoning-model output shapes
# ---------------------------------------------------------------------------

def test_think_block_is_stripped():
    c = Completion(
        text='<think>Let me work through this. It is an invoice.</think>\n'
             '{"entity_id": "B_MRE"}',
        model="m", elapsed_s=0.1)
    assert c.json()["entity_id"] == "B_MRE"


def test_prose_around_the_object_is_tolerated():
    c = Completion(
        text='Here is the classification:\n{"entity_id": "B_MLE"}\nHope that helps.',
        model="m", elapsed_s=0.1)
    assert c.json()["entity_id"] == "B_MLE"


def test_leading_whitespace_from_a_real_response():
    """Run A on the Mac returned "\\n\\n{...}" — newlines before the object."""
    c = Completion(text='\n\n{"ok": true, "n": 1}', model="m", elapsed_s=0.1)
    assert c.json() == {"ok": True, "n": 1}


def test_nested_objects_survive_extraction():
    c = Completion(text='prose {"a": {"b": {"c": 1}}, "d": 2} more prose',
                   model="m", elapsed_s=0.1)
    assert c.json() == {"a": {"b": {"c": 1}}, "d": 2}


def test_braces_inside_strings_do_not_confuse_the_extractor():
    c = Completion(text='{"rationale": "contains a } brace", "n": 1}',
                   model="m", elapsed_s=0.1)
    assert c.json()["rationale"] == "contains a } brace"


def test_escaped_quotes_inside_strings():
    c = Completion(text=r'{"rationale": "he said \"pay now\"", "n": 1}',
                   model="m", elapsed_s=0.1)
    assert c.json()["n"] == 1


# ---------------------------------------------------------------------------
# Truncation must still fail loudly
# ---------------------------------------------------------------------------

def test_truncated_json_is_an_error_not_a_partial_parse():
    """A model that hit max_tokens returns *nearly* valid JSON.

    Half-parsing it would file a document against whichever fields happened to
    survive — which is worse than not filing it, because it looks fine.
    """
    c = Completion(text='{"entity_id": "B_MRE", "category": "FIN',
                   model="m", elapsed_s=0.1, finish_reason="length")
    with pytest.raises(ModelError) as exc:
        c.json()
    assert "length" in str(exc.value)


def test_empty_response_is_an_error():
    c = Completion(text="", model="m", elapsed_s=0.1, finish_reason="stop")
    with pytest.raises(ModelError):
        c.json()


def test_pure_prose_is_an_error():
    c = Completion(text="I think this is an invoice from Acme.",
                   model="m", elapsed_s=0.1)
    with pytest.raises(ModelError):
        c.json()


# ---------------------------------------------------------------------------
# The extractor on its own
# ---------------------------------------------------------------------------

def test_first_json_object_returns_none_when_there_is_none():
    assert _first_json_object("no braces here") is None
    assert _first_json_object("{unclosed") is None


def test_first_json_object_takes_the_first_complete_one():
    assert _first_json_object('{"a": 1} {"b": 2}') == '{"a": 1}'


# ---------------------------------------------------------------------------
# Loopback
# ---------------------------------------------------------------------------

def test_loopback_enforced_at_construction():
    LMStudio("http://127.0.0.1:1234/v1")
    with pytest.raises(NotLoopbackError):
        LMStudio("http://10.0.0.5:1234/v1")


def test_unknown_tier_is_rejected_before_any_request():
    with pytest.raises(ModelError) as exc:
        LMStudio().complete(tier="TIER-NONSENSE", system="x", user="y")
    assert "unknown tier" in str(exc.value)
