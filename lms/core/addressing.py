"""What an email address actually looks like.

    "Matthew R. Epstein" <matthew@mrecai.com>

Not `matthew@mrecai.com`. Almost never `matthew@mrecai.com`. Every mail client
in use sends a display name, and the raw `From:` header carries it.

WHY THIS MODULE EXISTS
----------------------
Three places computed a domain by hand, all the same way:

    address.rpartition("@")[2]

which on a real header returns `mrecai.com>` — with the bracket. On the first
run against Matthew's live mailbox that produced, in the same batch:

  * `resolve_by_email` never matching, because the whole display-name string
    was compared against the address list in entities.yaml
  * `resolve_by_domain` never matching, because `mrecai.com>` is not
    `mrecai.com`
  * and therefore EVERY message falling through to the model, when the entire
    point of D-005's precedence chain is that code decides before the model is
    asked
  * and then `phishing_check` reporting
    `sender 'mrecai.com>' is a look-alike of 'mrecai.com'` — edit distance 1 —
    so every message from a domain we know was flagged as an impersonation of
    itself

Four failures, one missing function. It never showed up in 302 tests because
every fixture ever written used a bare address, which is the one form real mail
does not use.

`email.utils.parseaddr` is in the standard library and has handled this since
1999. The rule from here: no module computes a domain from a header itself.
"""

from __future__ import annotations

from email.utils import getaddresses, parseaddr


def address(raw: str | None) -> str:
    """Just the address part, lowercased. "" when there isn't one.

    >>> address('"Matthew R. Epstein" <matthew@mrecai.com>')
    'matthew@mrecai.com'

    An `@` is required. `parseaddr("not an address")` returns `("", "not")` —
    the first word, confidently, as though it were an address. Handing that on
    means a garbage `From:` produces a garbage DOMAIN rather than nothing, and
    a domain we invented is exactly the kind of value the look-alike check will
    then have an opinion about.
    """
    _, addr = parseaddr(raw or "")
    addr = addr.strip().strip("<>").lower()
    return addr if "@" in addr else ""


def domain(raw: str | None) -> str:
    """The domain part of an address or a bare domain. "" when there isn't one.

    Accepts either form because callers have both: a `From:` header, and a
    domain straight out of entities.yaml.
    """
    text = (raw or "").strip().lower()
    if not text:
        return ""
    addr = address(text) or text
    return addr.rpartition("@")[2].strip("<>").strip()


def addresses(raw: str | None) -> list[str]:
    """Every address in a header that may list several.

    `To:` and `Cc:` routinely carry a dozen. Routing on only the first would
    mean a message addressed to a client with Matthew in copy resolves to the
    client — and `getaddresses` is also the only thing that parses a display
    name containing a comma correctly, which "Epstein, Matthew" does.
    """
    if not raw:
        return []
    out = []
    for _, addr in getaddresses([raw]):
        addr = addr.strip().strip("<>").lower()
        if addr and "@" in addr:
            out.append(addr)
    return out
