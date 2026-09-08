"""Naming convention tests — D-007.

These are the highest-value tests in the repo. Getting the convention wrong
is not a bug you patch; it is a re-file of the entire tree. The spec's own
worked example is pinned first, so a future edit that "improves" the format
fails loudly.
"""

import pytest

from core.pipeline import naming

SPEC_EXAMPLE = (
    "2026-07-31__CHS__COMMISSIONS__foundation-risk-partners__"
    "july-renewal-statement__USD14208-33__a3f91b2c.pdf"
)


def test_spec_example_reproduces_exactly():
    """D-007's worked example. If this fails, the convention changed."""
    got = naming.build_filename(
        doc_date="2026-07-31",
        entity_id="B_CHS",
        category="COMMISSIONS",
        counterparty="Foundation Risk Partners",
        descriptor="July Renewal Statement",
        amount_cents=1420833,
        sha256="a3f91b2c" + "0" * 56,
        extension=".pdf",
    )
    assert got == SPEC_EXAMPLE


def test_entity_prefix_is_stripped():
    assert naming.entity_token("B_CHS") == "CHS"
    assert naming.entity_token("P_MRE") == "MRE"
    # System buckets have no prefix and must survive untouched.
    assert naming.entity_token("UNASSIGNED") == "UNASSIGNED"
    assert naming.entity_token("JUNK") == "JUNK"


def test_amount_formats():
    assert naming.format_amount(1420833) == "USD14208-33"
    assert naming.format_amount(0) == "USD0-00"
    assert naming.format_amount(5) == "USD0-05"
    assert naming.format_amount(None) == "NOAMT"
    # A credit must not read as a charge.
    assert naming.format_amount(-2500) == "USD-25-00"


def test_amount_rejects_floats():
    """Cents are integers. A float here means someone did money in binary."""
    with pytest.raises(naming.NamingError):
        naming.format_amount(12.34)


def test_slug_allowlist_is_strict():
    assert naming.slugify("Foundation Risk Partners, LLC.", 32) == "foundation-risk-partners-llc"
    assert naming.slugify("A/B  &  C", 32) == "a-b-c"
    assert naming.slugify("---", 32) == ""
    # Unicode folds rather than vanishing.
    assert naming.slugify("Bürger & Co.", 32) == "burger-co"


def test_descriptor_word_bounds():
    # Too many words is truncated to five.
    d = naming.normalise_descriptor("one two three four five six seven")
    assert d.split("-") == ["one", "two", "three", "four", "five"]
    # One word is padded, not rejected — a thin descriptor must not lose a doc.
    assert naming.normalise_descriptor("invoice") == "invoice-doc"
    assert naming.normalise_descriptor("") == naming.UNKNOWN_DESCRIPTOR


def test_missing_counterparty_is_repaired_not_rejected():
    name = naming.build_filename(
        doc_date="2026-01-02", entity_id="P_MRE", category="LEGAL",
        counterparty=None, descriptor="jury duty summons",
        amount_cents=None, sha256="f" * 64, extension=".pdf",
    )
    assert "__unknown__" in name
    assert "__NOAMT__" in name


def test_bad_date_is_rejected():
    for bad in ("31-07-2026", "2026/07/31", "", None, "2026-7-1"):
        with pytest.raises(naming.NamingError):
            naming.build_filename(
                doc_date=bad, entity_id="P_MRE", category="HOME",
                counterparty="x", descriptor="a b", amount_cents=None,
                sha256="a" * 64, extension=".pdf",
            )


def test_cap_truncates_descriptor_and_preserves_hash():
    """The 200-char cap must never eat the hash, date, or entity."""
    name = naming.build_filename(
        doc_date="2026-07-31",
        entity_id="B_CHS",
        category="COMMISSIONS",
        counterparty="a-very-long-counterparty-name-x",
        descriptor=" ".join(["extraordinarily"] * 5),
        amount_cents=1420833,
        sha256="a3f91b2c" + "0" * 56,
        extension=".pdf",
    )
    assert len(name) <= naming.MAX_FILENAME
    assert name.startswith("2026-07-31__CHS__COMMISSIONS__")
    assert "__a3f91b2c.pdf" in name


def test_roundtrip_parse():
    parts = naming.parse_filename(SPEC_EXAMPLE)
    assert parts.doc_date == "2026-07-31"
    assert parts.entity == "CHS"
    assert parts.category == "COMMISSIONS"
    assert parts.counterparty == "foundation-risk-partners"
    assert parts.amount == "USD14208-33"
    assert parts.hash8 == "a3f91b2c"
    assert parts.ext == ".pdf"
    assert parts.render() == SPEC_EXAMPLE


def test_noamt_keeps_field_count_parseable():
    """Why NOAMT exists instead of omitting the field."""
    name = naming.build_filename(
        doc_date="2026-03-04", entity_id="P_HH", category="HOME",
        counterparty="con-ed", descriptor="electric bill",
        amount_cents=None, sha256="b" * 64, extension=".pdf",
    )
    assert naming.parse_filename(name).amount == "NOAMT"


def test_extension_is_normalised():
    for given, want in ((".PDF", ".pdf"), ("pdf", ".pdf"), (None, ""), (".", "")):
        assert naming.normalise_extension(given) == want
