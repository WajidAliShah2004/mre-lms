"""Near-duplicate CANDIDATES — addendum §4 "Detect duplicates", R25.

    filed artifacts -> extracted text -> word shingles -> candidate pairs
                                                       -> NEAR_DUPLICATE
                                                       -> RELATED_VERSION
                                                       -> SIMILAR

Exact duplicates are already handled, and handled by bytes: sha256 over the
complete file, UNIQUE in `artifacts`, a re-feed recorded in `duplicate_refs`
and never filed twice (filing.py). That is the only kind of duplicate the
system is allowed to act on. This module finds the OTHER kind — the same
statement downloaded twice with different PDF bytes, a statement that arrived
as an email body and again as a PDF — and it may only point at them:

    "Near duplicates use text similarity and visual comparison only as
     candidates; retain distinct versions, annotated scans, different
     statement periods and different signed copies."        (addendum §4)

So nothing here merges, moves, hides or marks anything. It reads the filed
text and returns pairs with a score and a reason. There is no write path to
the database in this file at all, and the report that prints it opens the
database read-only (ops/storage_report.py).

WHY THE REASON MATTERS MORE THAN THE SCORE
------------------------------------------
Two monthly statements from the same bank are mostly the same words: the
template, the fee schedule, the disclosures. By text similarity alone they are
"near duplicates", and calling them that is how the April statement gets
treated as a copy of March and dropped (demonstration scenario 6). The facts
that make them two documents are small — the period dates, the balances, the
account ending — and they are exactly what a shingle score averages away.

So every pair that clears the similarity bar is then checked for differing
facts, and a pair whose texts DISAGREE about a period, an amount, an account,
an invoice number, a signature or a version marker is reported as a
RELATED_VERSION — keep both — however similar the rest of the text is. Only a
pair with no such disagreement is a NEAR_DUPLICATE candidate.

"Disagree" means each side has a fact the other lacks (mutual difference).
One side merely having MORE — an email that quotes the whole statement and
adds "Hi Matthew, see below" and the date it was sent — is containment, not
disagreement; that is the same statement in two forms (scenario 5).

This errs toward "related version". An OCR misread of one amount turns a true
duplicate into a related pair, and that is the right direction to be wrong in:
a missed duplicate costs one extra line of review, a false one costs a
statement.

WHY EXACT SIMILARITY AND NOT MINHASH
------------------------------------
MinHash/LSH is the textbook answer for millions of documents. This archive is
hundreds to low thousands, and LSH would buy speed we do not need with recall
we cannot explain: its misses are random, and a report that says "no near
duplicates" must mean none, not "none that the hash bands happened to catch".

Instead: exact Jaccard and containment over 3-word shingles, with candidate
pairs found by PREFIX FILTERING (the standard exact similarity-join filter,
Chaudhuri et al. 2006). If two sets share at least c·|A| elements, then ANY
|A| − ⌈c·|A|⌉ + 1 elements of A include one of them — so probing an inverted
index with only A's rarest shingles finds every such partner, provably, and
looks at almost nothing else. Every pair above the search threshold is found;
every candidate is then scored exactly. Deterministic, standard library only.

THRESHOLDS
----------
3-word shingles: one changed word alters up to three shingles, so a Jaccard of
0.80 is roughly "at most ~4% of words differ" — a re-rendered PDF, a different
footer, OCR noise. Containment 0.90 is "the smaller text is 90% inside the
larger", the email-quoting-the-statement case. Search containment 0.50 is wide
on purpose: pairs between 0.50 and the near-duplicate bar are still worth
reporting as related versions when their facts differ. Fewer than 25 shingles
(about 27 words) is too short to compare — a three-line note matches anything.

VISUAL COMPARISON
-----------------
Not available portably. The Mac has Vision's VNGenerateImageFeaturePrintRequest
(the same framework ocr.py already reads text with), but there is no standard-
library image comparison, and this code is tested on Windows. So it is a hook:
`find_candidates(..., visual=...)` accepts any object with a `similarity(a, b)`
method. Without one, documents with no extracted text are listed as NOT
COMPARED, with that reason — never silently left out. With one, a text match
that LOOKS different becomes a related version (an annotated or re-signed scan
reads the same and is not the same), and text-less images are compared to
each other. The thresholds for that path must be calibrated on the Mac against
real scans; the defaults below are placeholders and say so.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable, Protocol

from .ocr import TEXT_SUFFIXES

SHINGLE_WORDS = 3
MIN_SHINGLES = 25
SEARCH_CONTAINMENT = 0.50
NEAR_DUP_JACCARD = 0.80
NEAR_DUP_CONTAINMENT = 0.90

# Placeholders until calibrated on the Mac (see VISUAL COMPARISON above).
VISUAL_SAME = 0.95          # text-less pair at or above this: candidate
VISUAL_DIFFERENT = 0.90     # text-matched pair below this: looks different

# Bounds, so a pathological archive produces a truncated report, not a hang.
MAX_TEXT_CHARS = 400_000    # ~80 pages of statement text per document
MAX_CANDIDATE_PAIRS = 200_000
MAX_VISUAL_DOCS = 500       # text-less documents compared pairwise

NEAR_DUPLICATE = "NEAR_DUPLICATE"
RELATED_VERSION = "RELATED_VERSION"
SIMILAR = "SIMILAR"
KINDS = (NEAR_DUPLICATE, RELATED_VERSION, SIMILAR)


# ---------------------------------------------------------------------------
# Documents and their text
# ---------------------------------------------------------------------------

@dataclass
class Doc:
    """One filed artifact as this module sees it. `text` None = nothing read."""
    key: int | str
    text: str | None
    label: str = ""
    source: str = ""
    parent_id: int | None = None
    path: Path | None = None


def document_text(filed: Path) -> str | None:
    """The text the pipeline extracted for a filed document, or None.

    Read from what filing already wrote, never re-extracted: a document that
    arrived as text IS its text (D-025 suppresses the duplicate sidecar), and
    everything else has `<filed name>.txt` beside it when anything was read.
    Re-running OCR here would make a report depend on Vision and LM Studio.
    """
    filed = Path(filed)
    candidates = []
    if filed.suffix.lower() in TEXT_SUFFIXES:
        candidates.append(filed)
    candidates.append(filed.with_name(filed.name + ".txt"))
    for p in candidates:
        if p.is_file():
            with open(p, encoding="utf-8", errors="replace") as fh:
                return fh.read(MAX_TEXT_CHARS)
    return None


_HEADER_KEYS = {"from", "to", "cc", "date", "subject", "message-id", "attachments"}


def strip_mail_headers(text: str) -> str:
    """The body of a rendered email (mail.render_message), else the text.

    The header block is what makes every rendering unique — Message-ID by
    design — and it is not the document. Comparing it would make an email that
    carries a statement in its body look less like the statement than it is.
    """
    if not text.startswith("From:"):
        return text
    head, sep, body = text.partition("\n\n")
    if not sep:
        return text
    for line in head.splitlines():
        key, colon, _ = line.partition(":")
        if not colon or key.strip().lower() not in _HEADER_KEYS:
            return text
    return body


def load_docs(conn) -> list[Doc]:
    """Every FILED artifact with its extracted text (None when there is none).

    Quarantined documents are not compared: they are not in the managed
    collection yet, and the review queue is where a human looks at them.
    """
    rows = conn.execute(
        "SELECT id, source, parent_id, filed_path FROM artifacts "
        "WHERE status = 'FILED' AND filed_path IS NOT NULL ORDER BY id").fetchall()
    docs = []
    for r in rows:
        path = Path(r["filed_path"])
        text = document_text(path)
        if text is not None and r["source"] == "email":
            text = strip_mail_headers(text)
        docs.append(Doc(key=int(r["id"]), text=text, label=path.name,
                        source=r["source"], parent_id=r["parent_id"], path=path))
    return docs


# ---------------------------------------------------------------------------
# Shingles
# ---------------------------------------------------------------------------

# A token keeps its inner punctuation, so "1,234.56", "03/31/2026" and
# "acct-1234" stay one token each: an amount split into three tokens would
# share two of them with every other amount in the archive.
_TOKEN = re.compile(r"[a-z0-9]+(?:[.,/:'\-][a-z0-9]+)*")


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(unicodedata.normalize("NFKC", text or "").lower())


def shingles(text: str, k: int = SHINGLE_WORDS) -> frozenset[int]:
    """Hashed k-word shingles. Line breaks and layout do not matter: a PDF's
    text layer and an email body wrap the same words differently."""
    toks = tokens(text)
    if len(toks) < k:
        return frozenset()
    return frozenset(hash(" ".join(toks[i:i + k])) for i in range(len(toks) - k + 1))


# ---------------------------------------------------------------------------
# Facts that make two similar texts two documents
# ---------------------------------------------------------------------------

_MONTHS = {m: i for i, names in enumerate((
    ("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
    ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
    ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
    ("dec", "december")), start=1) for m in names}
_MON = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|" \
       r"aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_YEAR = r"((?:19|20)\d{2})"

# Order matters: each match is blanked before the next pattern runs, so
# "31 March 2026" is one date and not also the month "March 2026".
_DATE_PATTERNS = (
    ("ymd", re.compile(rf"\b{_YEAR}-(\d{{1,2}})-(\d{{1,2}})\b")),
    ("mdy", re.compile(r"\b(\d{1,2})/(\d{1,2})/((?:19|20)?\d{2})\b")),
    ("Mdy", re.compile(rf"\b{_MON}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+{_YEAR}\b", re.I)),
    ("dMy", re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{_MON}\.?,?\s+{_YEAR}\b", re.I)),
    ("My", re.compile(rf"\b{_MON}\.?,?\s+{_YEAR}\b", re.I)),
    ("my", re.compile(rf"\b(\d{{1,2}})/{_YEAR}\b")),
)

# A date on a line that says what period it bounds — or on the line after
# one, because a PDF text layer often puts "Statement Period" and the dates
# on separate lines. Not bare "ending", "from" or "statement": "card ending
# 7781" and "a statement from us" are not periods, and a transaction date
# promoted to a period date would make every statement pair look like two
# periods.
_PERIOD = re.compile(
    r"\b(period|statement\s+date|billing|cycle|closing\s+date|opening\s+date|"
    r"through|thru|as\s+of|for\s+the\s+month)\b", re.I)

_DECIMAL_AMOUNT = re.compile(r"(?<![\d.,])(\d{1,3}(?:,\d{3})+|\d+)\.(\d{2})(?!\d)")
_DOLLAR_WHOLE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?![\d,]|\.\d)")

_ACCOUNT = re.compile(
    r"\b(?:account|acct|card)\s*(?:number|no\.?|#|ending(?:\s+in)?|ends\s+in|:)"
    r"\s*:?\s*[x*•.\- ]*(\d[\d-]*\d)", re.I)
_INVOICE = re.compile(r"\binvoice\s*(?:number|no\.?|#)\s*:?\s*([a-z0-9][a-z0-9\-/]{1,30})",
                      re.I)

_SIGNED = re.compile(
    r"/s/\s*\w|\b(?:electronically|digitally)\s+signed\b|"
    r"\bsigned\s+(?:by|on)\b(?!\s*:?\s*_)|\bsignature\s+on\s+file\b", re.I)
_UNSIGNED = re.compile(
    r"\bunsigned\b|\bnot\s+(?:yet\s+)?signed\b|"
    r"\b(?:signature|sign\s+here|signed(?:\s+by)?|by)\s*:?\s*_{4,}|\bx\s*_{4,}", re.I)
_SIGNER = re.compile(
    r"(?:/s/|(?i:signed\s+by)\s*:?)\s*([A-Z][A-Za-z.'\-]+(?:\s+[A-Z][A-Za-z.'\-]+){0,3})")

# Not "draft" or "final": "bank draft" and "final notice" are ordinary words in
# financial mail, and a marker that fires on boilerplate is noise.
_VERSION = re.compile(
    r"\b(amended|corrected|revised|superseded|version\s*\d+(?:\.\d+)*|"
    r"v\d+(?:\.\d+)*|rev(?:ision)?(?:\.\s*|\s+)(?:[a-z]|\d{1,3}))\b", re.I)


@dataclass(frozen=True)
class Facts:
    dates: frozenset[str] = frozenset()
    period_dates: frozenset[str] = frozenset()
    amounts: frozenset[int] = frozenset()        # cents
    accounts: frozenset[str] = frozenset()       # last four digits
    invoices: frozenset[str] = frozenset()
    signers: frozenset[str] = frozenset()
    signed: str | None = None                    # "signed" | "unsigned" | None
    versions: frozenset[str] = frozenset()


def _iso(y: int, m: int, d: int | None) -> str | None:
    try:
        if d is None:
            date(y, m, 1)
            return f"{y:04d}-{m:02d}"
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def _year(s: str) -> int:
    y = int(s)
    return y + 2000 if y < 100 else y


def dates_in(line: str) -> set[str]:
    """Every date on one line, as YYYY-MM-DD, or YYYY-MM when only a month."""
    found: set[str] = set()
    for kind, pattern in _DATE_PATTERNS:
        def take(m: re.Match) -> str:
            g = m.groups()
            if kind == "ymd":
                v = _iso(int(g[0]), int(g[1]), int(g[2]))
            elif kind == "mdy":
                v = _iso(_year(g[2]), int(g[0]), int(g[1]))
            elif kind == "Mdy":
                v = _iso(int(g[2]), _MONTHS[g[0].lower()[:3]], int(g[1]))
            elif kind == "dMy":
                v = _iso(int(g[2]), _MONTHS[g[1].lower()[:3]], int(g[0]))
            elif kind == "My":
                v = _iso(int(g[1]), _MONTHS[g[0].lower()[:3]], None)
            else:
                v = _iso(int(g[1]), int(g[0]), None)
            if v:
                found.add(v)
            return " " * len(m.group(0))
        line = pattern.sub(take, line)
    return found


def _cents(whole: str, frac: str = "00") -> int:
    return int(whole.replace(",", "")) * 100 + int(frac)


def _version_label(raw: str) -> str:
    s = re.sub(r"\s+", " ", raw.lower()).replace(".", " ").strip()
    s = re.sub(r"\s+", " ", s)
    m = re.fullmatch(r"(?:version ?|v)(\d+(?: \d+)*)", s)
    if m:
        return "version " + m.group(1).replace(" ", ".")
    m = re.fullmatch(r"rev(?:ision)? ?(\w+)", s)
    if m:
        return "rev " + m.group(1)
    return s


def extract_facts(text: str) -> Facts:
    dates: set[str] = set()
    period: set[str] = set()
    # A label line with no date of its own ("Statement Period") lends its
    # meaning to the next line; a line that carried its own dates does not.
    label_waiting = False
    for line in (text or "").splitlines():
        found = dates_in(line)
        is_period = bool(_PERIOD.search(line))
        dates |= found
        if found and (is_period or label_waiting):
            period |= found
        label_waiting = is_period and not found

    amounts = {_cents(m[1], m[2]) for m in _DECIMAL_AMOUNT.finditer(text)}
    amounts |= {_cents(m[1]) for m in _DOLLAR_WHOLE.finditer(text)}

    accounts = {re.sub(r"\D", "", m[1])[-4:] for m in _ACCOUNT.finditer(text)}
    accounts = {a for a in accounts if len(a) == 4}

    signed = ("signed" if _SIGNED.search(text)
              else "unsigned" if _UNSIGNED.search(text) else None)

    return Facts(
        dates=frozenset(dates), period_dates=frozenset(period),
        amounts=frozenset(amounts), accounts=frozenset(accounts),
        invoices=frozenset(m[1].upper() for m in _INVOICE.finditer(text)),
        signers=frozenset(" ".join(m[1].lower().split())
                          for m in _SIGNER.finditer(text)),
        signed=signed,
        versions=frozenset(_version_label(m[1]) for m in _VERSION.finditer(text)),
    )


def _mutual(a: frozenset, b: frozenset) -> bool:
    """Each side has something the other lacks. One side having MORE is
    containment — the same document quoted with extra words around it."""
    return bool(a - b) and bool(b - a)


def _show(values: Iterable, fmt=str, n: int = 3) -> str:
    vals = sorted(values)
    shown = ", ".join(fmt(v) for v in vals[:n])
    return shown + (f" (+{len(vals) - n} more)" if len(vals) > n else "")


def _money(cents: int) -> str:
    return f"${cents // 100:,}.{cents % 100:02d}"


def differences(a: Facts, b: Facts) -> list[str]:
    """Why two similar texts are two documents. Empty = nothing found."""
    out: list[str] = []
    if a.period_dates and b.period_dates and _mutual(a.period_dates, b.period_dates):
        out.append(f"different statement period ({_show(a.period_dates)} vs "
                   f"{_show(b.period_dates)})")
    elif _mutual(a.dates, b.dates):
        out.append(f"different dates ({_show(a.dates - b.dates)} vs "
                   f"{_show(b.dates - a.dates)})")
    if _mutual(a.amounts, b.amounts):
        out.append(f"different amounts ({_show(a.amounts - b.amounts, _money)} vs "
                   f"{_show(b.amounts - a.amounts, _money)})")
    if _mutual(a.accounts, b.accounts):
        out.append(f"different accounts (…{_show(a.accounts)} vs …{_show(b.accounts)})")
    if _mutual(a.invoices, b.invoices):
        out.append(f"different invoice numbers ({_show(a.invoices)} vs "
                   f"{_show(b.invoices)})")
    if a.signed and b.signed and a.signed != b.signed:
        out.append(f"{a.signed} vs {b.signed} copy")
    if _mutual(a.signers, b.signers):
        out.append(f"signed by different people ({_show(a.signers)} vs "
                   f"{_show(b.signers)})")
    # One-sided on purpose: "Amended" on only one of two otherwise identical
    # returns is the whole difference between them.
    if a.versions != b.versions:
        out.append(f"different version markers ({_show(a.versions) or 'none'} vs "
                   f"{_show(b.versions) or 'none'})")
    return out


# ---------------------------------------------------------------------------
# Visual comparison — a hook (see the module docstring)
# ---------------------------------------------------------------------------

class VisualComparer(Protocol):
    """1.0 = the pages look identical, 0.0 = nothing alike, None = could not
    compare these two. The backend maps its own distance onto that scale and
    owns the calibration."""
    name: str

    def similarity(self, a: Path, b: Path) -> float | None: ...


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Pair:
    a: Doc
    b: Doc
    kind: str
    jaccard: float
    containment: float
    reasons: tuple[str, ...]
    visual: float | None = None
    note: str = ""

    @property
    def reason(self) -> str:
        return "; ".join(self.reasons)


@dataclass
class NearDupResult:
    pairs: list[Pair] = field(default_factory=list)
    compared: list[Doc] = field(default_factory=list)
    too_short: list[Doc] = field(default_factory=list)
    no_text: list[Doc] = field(default_factory=list)
    visual: str | None = None
    truncated: bool = False

    def of_kind(self, kind: str) -> list[Pair]:
        return [p for p in self.pairs if p.kind == kind]


def candidate_pairs(sets: list[frozenset[int]], threshold: float = SEARCH_CONTAINMENT,
                    max_pairs: int = MAX_CANDIDATE_PAIRS) -> tuple[set[tuple[int, int]], bool]:
    """Every (i, j) with |Si ∩ Sj| >= threshold · min(|Si|, |Sj|), and a few more.

    Prefix filtering: probe the inverted index with each set's rarest
    |S| − ⌈t·|S|⌉ + 1 elements. If S shares at least ⌈t·|S|⌉ elements with T,
    at most |S| − ⌈t·|S|⌉ of S's elements lie outside T, so any larger subset
    of S — the rarest one included — contains a shared element. Probing from
    every set covers the smaller side of every pair. No pair above the
    threshold is missed; the extras are removed by exact scoring afterwards.
    """
    index: dict[int, list[int]] = {}
    for i, s in enumerate(sets):
        for h in s:
            index.setdefault(h, []).append(i)

    pairs: set[tuple[int, int]] = set()
    for i, s in enumerate(sets):
        n = len(s)
        if not n:
            continue
        prefix_len = n - math.ceil(threshold * n - 1e-9) + 1
        prefix = sorted(s, key=lambda h: (len(index[h]), h))[:prefix_len]
        for h in prefix:
            for j in index[h]:
                if j != i:
                    pairs.add((i, j) if i < j else (j, i))
            if len(pairs) > max_pairs:
                return pairs, True
    return pairs, False


def classify_pair(a: Doc, b: Doc, jaccard: float, containment: float,
                  facts_a: Facts, facts_b: Facts) -> tuple[str, list[str]]:
    diffs = differences(facts_a, facts_b)
    if diffs:
        return RELATED_VERSION, diffs + ["keep both"]
    if jaccard >= NEAR_DUP_JACCARD or containment >= NEAR_DUP_CONTAINMENT:
        return NEAR_DUPLICATE, [
            f"same text (jaccard {jaccard:.2f}, containment {containment:.2f}); "
            "no differing period, amount, account, invoice, signature or version "
            "found — a candidate for review, nothing merged"]
    return SIMILAR, [
        f"shares {containment:.0%} of the shorter text, below the near-duplicate "
        "bar; no differing facts found — same template or thread, not a duplicate"]


def find_candidates(docs: Iterable[Doc], *, visual: VisualComparer | None = None,
                    min_shingles: int = MIN_SHINGLES) -> NearDupResult:
    """Candidate pairs among `docs`, each with a kind and a reason. Read-only."""
    res = NearDupResult(visual=getattr(visual, "name", None) if visual else None)

    usable: list[Doc] = []
    sets: list[frozenset[int]] = []
    for d in docs:
        if d.text is None or not d.text.strip():
            res.no_text.append(d)
            continue
        s = shingles(d.text)
        if len(s) < min_shingles:
            res.too_short.append(d)
            continue
        usable.append(d)
        sets.append(s)
    res.compared = usable

    cands, res.truncated = candidate_pairs(sets)
    facts: dict[int, Facts] = {}

    for i, j in cands:
        a, b = sets[i], sets[j]
        inter = len(a & b)
        containment = inter / min(len(a), len(b))
        if containment < SEARCH_CONTAINMENT:
            continue
        jaccard = inter / (len(a) + len(b) - inter)
        fa = facts.setdefault(i, extract_facts(usable[i].text or ""))
        fb = facts.setdefault(j, extract_facts(usable[j].text or ""))
        kind, reasons = classify_pair(usable[i], usable[j], jaccard, containment, fa, fb)

        score = None
        if visual is not None and kind == NEAR_DUPLICATE and usable[i].path and usable[j].path:
            score = visual.similarity(usable[i].path, usable[j].path)
            if score is not None and score < VISUAL_DIFFERENT:
                kind = RELATED_VERSION
                reasons = [f"the text matches but the pages look different "
                           f"(visual {score:.2f}) — an annotated or re-signed "
                           f"copy?", "keep both"]
        res.pairs.append(Pair(usable[i], usable[j], kind, round(jaccard, 4),
                              round(containment, 4), tuple(reasons), score,
                              _relation(usable[i], usable[j])))

    if visual is not None:
        res.pairs.extend(_visual_only_pairs(res.no_text, visual))

    order = {k: n for n, k in enumerate(KINDS)}
    res.pairs.sort(key=lambda p: (order[p.kind], -p.jaccard, -(p.visual or 0),
                                  str(p.a.key), str(p.b.key)))
    return res


def _relation(a: Doc, b: Doc) -> str:
    if a.parent_id is not None and a.parent_id == b.key:
        return "an attachment and the email that carried it"
    if b.parent_id is not None and b.parent_id == a.key:
        return "an email and its own attachment"
    return ""


def _visual_only_pairs(docs: list[Doc], visual: VisualComparer) -> list[Pair]:
    """Text-less documents (photos, scans OCR could not read), pairwise.

    No facts can be checked without text, so even a near-identical pair is
    only a candidate, and the reason says the period and amounts were not
    compared. Bounded: quadratic in the number of text-less documents.
    """
    out: list[Pair] = []
    with_path = [d for d in docs if d.path is not None][:MAX_VISUAL_DOCS]
    for n, a in enumerate(with_path):
        for b in with_path[n + 1:]:
            score = visual.similarity(a.path, b.path)
            if score is None or score < VISUAL_SAME:
                continue
            out.append(Pair(a, b, NEAR_DUPLICATE, 0.0, 0.0, (
                f"visually near-identical (visual {score:.2f}); no text, so "
                "periods and amounts could not be compared — review",), score,
                _relation(a, b)))
    return out
