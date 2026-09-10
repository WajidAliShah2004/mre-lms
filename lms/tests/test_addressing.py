"""Addresses as they actually arrive.

Every fixture in this suite used a bare `matthew@mrecai.com` until Sept 10,
which is the one form real mail does not use. The first live run produced four
failures from one missing function:

  * recipient_email — the FIRST rule in D-005's precedence chain — never fired,
    because the whole display-name string was compared against entities.yaml
  * sender_domain never fired, because `rpartition("@")[2]` returns
    `mrecai.com>` with the bracket
  * so every message fell through to the model, which is precisely what the
    precedence chain exists to prevent
  * and phishing_check reported
    `sender 'mrecai.com>' is a look-alike of 'mrecai.com'`, flagging every
    message from a known domain as an impersonation of itself

So these tests are written in the shape the mailbox actually produced.
"""

from pathlib import Path

import pytest

from core import addressing
from core.pipeline import ingest
from core.pipeline.classify import Artifact, route_deterministically
from core.pipeline.registry import load_registry

CONFIG = Path(__file__).resolve().parents[1] / "config"

REAL = '"Matthew R. Epstein" <matthew@mrecai.com>'


@pytest.fixture
def reg():
    return load_registry(CONFIG)


# ---------------------------------------------------------------------------
# The parsing itself
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    (REAL, "matthew@mrecai.com"),
    ("<matthew@mrecai.com>", "matthew@mrecai.com"),
    ("matthew@mrecai.com", "matthew@mrecai.com"),
    ("Matthew <MATTHEW@MRECAI.COM>", "matthew@mrecai.com"),
    ('"Epstein, Matthew" <matthew@mrecai.com>', "matthew@mrecai.com"),
    ("  matthew@mrecai.com  ", "matthew@mrecai.com"),
    ("", ""),
    (None, ""),
])
def test_the_address_comes_out_of_the_header(raw, expected):
    assert addressing.address(raw) == expected


@pytest.mark.parametrize("raw", [
    REAL, "<matthew@mrecai.com>", "matthew@mrecai.com", "mrecai.com",
    "MRECAI.COM", '"service@paypal.com" <service@mrecai.com>',
])
def test_the_domain_never_keeps_the_bracket(raw):
    """The whole bug, in one assertion."""
    assert addressing.domain(raw) == "mrecai.com"


def test_a_display_name_that_looks_like_an_address_does_not_win():
    """`"service@paypal.com" <service@spoofer.example>` is the standard
    display-name spoof, and four of these are sitting in the real mailbox
    right now — legitimately, but the shape is the shape."""
    raw = '"service@paypal.com" <noreply@spoofer.example>'
    assert addressing.address(raw) == "noreply@spoofer.example"
    assert addressing.domain(raw) == "spoofer.example"


def test_every_recipient_of_a_multi_address_header_is_returned():
    """`To:` routinely carries a dozen. Routing on only the first means a
    message addressed to a client with Matthew in copy resolves to the
    client."""
    header = ('"Epstein, Matthew" <matthew@mrecai.com>, '
              'adjuster@statefarm.com, "A B" <a@b.example>')
    assert addressing.addresses(header) == [
        "matthew@mrecai.com", "adjuster@statefarm.com", "a@b.example"]


def test_a_comma_inside_a_display_name_does_not_split_the_address():
    """"Epstein, Matthew" is one recipient, not two. A naive split(",") makes
    it two, and one of them has no @ at all."""
    assert addressing.addresses('"Epstein, Matthew" <matthew@mrecai.com>') == [
        "matthew@mrecai.com"]


@pytest.mark.parametrize("junk", ["", None, "not an address", "<>", "  ",
                                  "Matthew", "(no sender)"])
def test_junk_produces_nothing_rather_than_something_wrong(junk):
    """`parseaddr("not an address")` returns `("", "not")` — the first word,
    confidently, as though it were an address. A domain we invented is exactly
    the kind of value the look-alike check then has an opinion about.
    """
    assert addressing.address(junk) == ""
    assert addressing.addresses(junk) == []


def test_a_bare_domain_is_still_a_domain():
    """`domain()` takes either form, because callers have both: a From: header
    and a domain straight out of entities.yaml."""
    assert addressing.domain("mrecai.com") == "mrecai.com"
    assert addressing.domain("not an address") == "not an address", (
        "a caller passing a bare domain gets it back; only address() is strict")


# ---------------------------------------------------------------------------
# What it was breaking
# ---------------------------------------------------------------------------

def test_routing_fires_on_a_real_header(reg):
    """D-005's first rule, against the form real mail uses.

    Deterministic routing beating the model is the load-bearing claim of the
    whole classification design. It had never once fired on a real message.
    """
    known = sorted({e for ent in reg.entities.values() for e in ent.emails})
    if not known:
        pytest.skip("entities.yaml declares no addresses")
    addr = known[0]

    art = Artifact(recipient=f'"Matthew R. Epstein" <{addr}>')
    ent, how = route_deterministically(reg, art)
    assert ent is not None and how == "recipient_email"


def test_routing_finds_the_entity_when_it_is_not_the_first_recipient(reg):
    known = sorted({e for ent in reg.entities.values() for e in ent.emails})
    if not known:
        pytest.skip("entities.yaml declares no addresses")

    art = Artifact(recipient=f'adjuster@statefarm.com, "M E" <{known[0]}>')
    ent, how = route_deterministically(reg, art)
    assert ent is not None and how == "recipient_email"


def test_a_known_domain_is_not_a_lookalike_of_itself(reg):
    """The quarantine reason that stopped every message on the first live run:

        SUSPECTED_PHISHING: sender 'mrecai.com>' is a look-alike of 'mrecai.com'
    """
    known = sorted({d for ent in reg.entities.values() for d in ent.domains})
    if not known:
        pytest.skip("entities.yaml declares no domains")

    art = Artifact(sender=f'"Matthew R. Epstein" <matthew@{known[0]}>',
                   subject="Re: SIGNATURE PAGE", body="Signed, attached.")
    assert ingest.phishing_check(art, reg) is None


def test_two_of_his_own_domains_are_not_lookalikes_of_each_other(reg):
    """The second reason the first live run quarantined everything:

        SUSPECTED_PHISHING: sender 'mrecai.com' is a look-alike of 'mleca.com'

    Both are Matthew's. MRECAI and MLECA are deliberately similar brand names
    and happen to be edit distance 2 apart. The old guard only excused a domain
    from being a look-alike of ITSELF, so his real domains impersonated each
    other forever.
    """
    known = sorted({d for ent in reg.entities.values() for d in ent.domains})
    for d in known:
        art = Artifact(sender=f'"Matthew R. Epstein" <matthew@{d}>',
                       subject="Re: SIGNATURE PAGE", body="Signed, attached.")
        assert ingest.phishing_check(art, reg) is None, (
            f"{d} is one of ours and cannot be impersonating one of ours")


def test_the_entity_domains_really_are_close_together(reg):
    """Guards the test above against becoming vacuous.

    If entities.yaml is ever edited so no two domains are within the edit
    distance, the assertion above would pass for the wrong reason and stop
    protecting anything.
    """
    import itertools
    known = sorted({d for ent in reg.entities.values() for d in ent.domains})
    close = [(a, b) for a, b in itertools.combinations(known, 2)
             if ingest._confusable(a, b, 2)]
    assert close, (
        "no two entity domains are within edit distance 2 any more — the "
        "look-alike regression test above is no longer exercising anything")


def test_an_actual_lookalike_is_still_caught(reg):
    """The check must still do its job — this is the one case that matters."""
    known = sorted({d for ent in reg.entities.values() for d in ent.domains})
    if not known:
        pytest.skip("entities.yaml declares no domains")

    name, _, tld = known[0].rpartition(".")
    typo = f"{name}s.{tld}"                       # one insertion
    art = Artifact(sender=f'"Matthew R. Epstein" <billing@{typo}>',
                   subject="Updated invoice", body="See attached.")
    reason = ingest.phishing_check(art, reg)
    assert reason and "look-alike" in reason
