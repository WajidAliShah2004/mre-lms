"""Filename construction — spec §6.2, decision D-007.

    YYYY-MM-DD__ENTITY__CATEGORY__COUNTERPARTY__DESCRIPTOR__AMOUNT__hash8.ext

This module is pure: no I/O, no model, no clock. Given the same inputs it
produces the same name forever, which is the only reason the tree can be
re-derived if the database is ever lost.

Why this is fussy on purpose
----------------------------
Every rule below exists because breaking it later means re-filing the entire
tree. The convention is cheap to get right now and expensive to change once
the first thousand documents are on disk. In particular:

  * The date is the DOCUMENT's date, not the ingestion date. An invoice
    photographed three weeks late still files under the day it was issued.
    The ingestion date lives in the database.
  * ENTITY drops the registry prefix: B_CHS -> CHS, P_MRE -> MRE.
  * AMOUNT is present even when there isn't one, as the literal NOAMT.
    A fixed field count means the name can be parsed by splitting, which
    matters more than saving eight characters.
  * The 200-char cap truncates DESCRIPTOR first, and only DESCRIPTOR. The
    hash must survive intact or dedupe-by-name breaks; the date, entity and
    category must survive or the name stops being sortable and greppable.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace

SEP = "__"
MAX_FILENAME = 200
COUNTERPARTY_MAX = 32
DESCRIPTOR_MAX = 40
DESCRIPTOR_MIN_WORDS = 2
DESCRIPTOR_MAX_WORDS = 5

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

NOAMT = "NOAMT"
UNKNOWN_COUNTERPARTY = "unknown"
UNKNOWN_DESCRIPTOR = "no-descriptor"


class NamingError(ValueError):
    """Raised when an input cannot produce a lawful filename."""


def slugify(text: str, max_len: int) -> str:
    """Lowercase, ASCII, [a-z0-9-] only, no leading/trailing/doubled hyphens.

    Unicode is folded rather than dropped so that a counterparty like
    "Bürger & Co." becomes "burger-co" instead of "rger-co".
    """
    if not text:
        return ""
    folded = unicodedata.normalize("NFKD", text)
    ascii_only = folded.encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_STRIP.sub("-", ascii_only.lower()).strip("-")
    if len(slug) > max_len:
        # Cut on a word boundary where one is available in the last quarter of
        # the budget, so truncation reads as a shortened phrase, not a typo.
        cut = slug[:max_len]
        boundary = cut.rfind("-")
        if boundary >= max_len * 0.75:
            cut = cut[:boundary]
        slug = cut.strip("-")
    return slug


def normalise_descriptor(text: str) -> str:
    """2-5 hyphenated words, <=40 chars.

    Fewer than two words is padded rather than rejected: a document with a
    one-word descriptor is still a document, and refusing to file it would
    turn a cosmetic problem into a lost artifact.
    """
    slug = slugify(text, DESCRIPTOR_MAX)
    if not slug:
        return UNKNOWN_DESCRIPTOR
    words = [w for w in slug.split("-") if w]
    if len(words) > DESCRIPTOR_MAX_WORDS:
        words = words[:DESCRIPTOR_MAX_WORDS]
    out = "-".join(words)
    if len(words) < DESCRIPTOR_MIN_WORDS:
        out = f"{out}-doc"
    return out[:DESCRIPTOR_MAX].strip("-")


def format_amount(amount_cents: int | None, currency: str = "USD") -> str:
    """USD1234-56, or NOAMT.

    The decimal point becomes a hyphen because a dot in a filename is a
    format boundary on too many systems to risk. Negative amounts (credits,
    refunds) keep their sign so a refund never reads as a charge.
    """
    if amount_cents is None:
        return NOAMT
    if not isinstance(amount_cents, int):
        raise NamingError("amount_cents must be an int number of cents or None")
    cur = slugify(currency or "USD", 5).upper().replace("-", "") or "USD"
    sign = "-" if amount_cents < 0 else ""
    whole, cents = divmod(abs(amount_cents), 100)
    return f"{cur}{sign}{whole}-{cents:02d}"


def entity_token(entity_id: str) -> str:
    """B_CHS -> CHS, P_MRE -> MRE, UNASSIGNED -> UNASSIGNED.

    Only the known registry prefixes are stripped. An id that merely happens
    to contain an underscore is left alone.
    """
    if not entity_id:
        raise NamingError("entity_id is required")
    for prefix in ("B_", "P_"):
        if entity_id.startswith(prefix):
            return entity_id[len(prefix):].upper()
    return entity_id.upper()


def normalise_extension(ext: str | None) -> str:
    """Return a leading-dot, lowercase extension, or empty string."""
    if not ext:
        return ""
    ext = ext.lower().lstrip(".")
    ext = re.sub(r"[^a-z0-9]", "", ext)
    return f".{ext}" if ext else ""


@dataclass(frozen=True)
class NameParts:
    doc_date: str
    entity: str
    category: str
    counterparty: str
    descriptor: str
    amount: str
    hash8: str
    ext: str

    def render(self) -> str:
        stem = SEP.join([
            self.doc_date, self.entity, self.category,
            self.counterparty, self.descriptor, self.amount, self.hash8,
        ])
        return stem + self.ext


def build_filename(*, doc_date: str, entity_id: str, category: str,
                   counterparty: str | None, descriptor: str | None,
                   amount_cents: int | None, sha256: str,
                   extension: str | None, currency: str = "USD") -> str:
    """Assemble a lawful filename, enforcing the 200-char cap.

    Raises NamingError on inputs that cannot be repaired — a malformed date,
    a missing hash, an empty category. Inputs that CAN be repaired (an empty
    counterparty, a one-word descriptor) are repaired rather than rejected,
    because the alternative is refusing to file a real document over a
    cosmetic defect.
    """
    if not _DATE_RE.match(doc_date or ""):
        raise NamingError(f"doc_date must be YYYY-MM-DD, got {doc_date!r}")
    if not sha256 or len(sha256) < 8:
        raise NamingError("sha256 must be a full hex digest")

    cat = slugify(category, 40).upper().replace("-", "_")
    if not cat:
        raise NamingError("category is required")

    parts = NameParts(
        doc_date=doc_date,
        entity=entity_token(entity_id),
        category=cat,
        counterparty=slugify(counterparty or "", COUNTERPARTY_MAX) or UNKNOWN_COUNTERPARTY,
        descriptor=normalise_descriptor(descriptor or ""),
        amount=format_amount(amount_cents, currency),
        hash8=sha256[:8].lower(),
        ext=normalise_extension(extension),
    )

    name = parts.render()
    if len(name) <= MAX_FILENAME:
        return name

    # Over budget. Shrink DESCRIPTOR only, down to a floor — never the hash,
    # never the date, never the entity.
    overrun = len(name) - MAX_FILENAME
    keep = max(len(parts.descriptor) - overrun, 3)
    shrunk = parts.descriptor[:keep].strip("-") or UNKNOWN_DESCRIPTOR[:3]
    parts = replace(parts, descriptor=shrunk)
    name = parts.render()

    if len(name) > MAX_FILENAME:
        # Only reachable via an absurd counterparty or category; the full
        # values are always safe in the database, so trim the tail-most
        # non-load-bearing field rather than failing the file.
        cp_keep = max(len(parts.counterparty) - (len(name) - MAX_FILENAME), 3)
        parts = replace(parts, counterparty=parts.counterparty[:cp_keep].strip("-") or "unk")
        name = parts.render()

    if len(name) > MAX_FILENAME:
        raise NamingError(f"cannot fit filename within {MAX_FILENAME} chars: {name!r}")
    return name


def parse_filename(name: str) -> NameParts:
    """Inverse of build_filename, for re-deriving the index from the tree.

    Fixed field count is what makes this a split rather than a regex, and
    it is why AMOUNT is emitted as NOAMT instead of being omitted.
    """
    stem, _, ext = name.rpartition(".")
    if not stem:
        stem, ext = name, ""
    fields = stem.split(SEP)
    if len(fields) != 7:
        raise NamingError(f"expected 7 fields, got {len(fields)} in {name!r}")
    return NameParts(*fields, ext=f".{ext}" if ext else "")
