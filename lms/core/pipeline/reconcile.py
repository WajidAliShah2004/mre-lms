"""Reconciliation: what does not add up ACROSS documents (R26, addendum §4).

The addendum of Sept 28, §4, "Reconcile and surface action":

    "Link bill to statement transaction and proof of payment when supported;
     detect missing statements and duplicate invoices; create tasks for due
     dates, renewals and unanswered mail. Flag discrepancies rather than
     treating OCR as authoritative for amounts or routing details."

and two of §7's demonstration scenarios: (4) "a previously paid bill received
again" and (6) "two similar monthly statements".

Not to be confused with `filing.reconcile`, which makes the review queue agree
with ONE filed document. This module reads the whole index and reports what
the documents say about each other.

FINDINGS, NEVER EDITS
---------------------
Everything here is a reading of evidence that came from a model or from OCR,
so it produces findings and candidate links and changes nothing it read: no
document is moved or rewritten, no classification corrected, no task closed,
merged or reopened. A wrong merge loses an obligation (D-066); a wrong
"already paid" is how a real second bill goes unpaid; a wrong "duplicate" is
how one gets paid twice. Each is a human's call, made with the evidence put in
front of him — `ops/done.py 15 --duplicate-of 12` is one command once he has
looked.

The one thing it creates is a task for a renewal date read from a document,
because "create tasks for ... renewals" is the sentence and ingest does not do
it (ingest makes a task only from a due date or HIGH/CRITICAL urgency). Even
that goes through `ledger.record_event` under the document's own source key,
`artifact:<sha256>`: a second run, or a document ingest already made a task
from, creates nothing. Due dates are ingest's already (ingest.py, "task").
Unanswered mail needs a record of what was sent, which does not exist yet;
`unanswered_mail` is the check, waiting on that source, and says so rather
than guessing.

OCR IS NEVER AUTHORITATIVE HERE
-------------------------------
Amounts, invoice numbers, account endings and dates come from a model reading
a document or from a regex reading OCR text. A link inferred from them is a
CANDIDATE with a confidence below 1.0 — `recon_links` refuses 1.0 — and only a
human makes one CONFIRMED. When two readings of the same invoice disagree on
the amount, that is a finding (AMOUNT_MISMATCH), not a vote.

WHERE THE FACTS COME FROM
-------------------------
§4's extraction list is "issuer, account ending, invoice number, payee,
amount, currency, period, due date, entity, address and document type".
`classifications` holds entity, counterparty, amount, currency and due date.
The invoice number, account ending, period and document type are not stored
anywhere, and adding them to the classifier is a prompt and schema change of
its own. So `load_docs` reads them deterministically: from the descriptor,
subject and attachment name first (short, already summarised), then from the
archived text — the filed document when it is text, its `.txt` OCR sidecar
when it is not. Read-only. A missing file (the array locked, D-066's apply)
only means fewer facts, never an error.

ONE OBLIGATION, AGAIN
---------------------
The ledger already folds a covering email and the invoice attached to it into
one task when their event keys agree (D-066). Here the same question is asked
of the documents, with more evidence: two bills are one obligation when they
share an invoice number from compatible counterparties, arrived in the same
message, or are already on the same task — never across entities, and never
where that would join two different invoice numbers. The PSG invoice that
arrives as an email body and again as its PDF attachment
(`...invoice-inv17-pending-payment__USD550-00__712fce12.txt` and
`...tax-filing-invoice-inv17__USD550-00__db81678c.pdf`) is one obligation
delivered twice, and produces nothing. The same invoice number on two OPEN
tasks is a DUPLICATE_INVOICE: one bill, two chances to pay it.

THE FINDINGS
------------
    RECEIVED_AGAIN     a closed (or apparently paid) obligation arrived again.
                       Scenario 4. Second bill or resend is his question; the
                       ledger's after-close evidence (D-066) is surfaced here.
    DUPLICATE_INVOICE  one invoice on two open tasks.
    DOUBLE_BILLING     two different invoices, same counterparty, amount and
                       month. Possibly billed twice; possibly two real charges.
    MERGED_INVOICES    two different invoice numbers on ONE task — the
                       expensive direction of a wrong merge (D-066).
    AMOUNT_MISMATCH    two readings of one invoice, or a payment for it,
                       disagree on the amount.
    MISSING_STATEMENT  a monthly statement series skipped a month.
    UNANSWERED_MAIL    waiting on a sent-mail source; see `unanswered_mail`.

Findings are kept in `recon_findings`, one row per stable key, so a nightly
run reports a problem once rather than every night; it resolves itself when a
later run no longer finds it (the statement arrived), and a human can
acknowledge it. Every change of state is also an `actions_log` row.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Iterable, Protocol

from ..db import database as db
from . import ledger
from .ocr import TEXT_SUFFIXES

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

# A statement is "missing" this many days after the day of the month its
# series usually arrives. Banks drift by a few days around weekends and
# holidays; ten keeps that from being an alarm.
GRACE_DAYS = 10

# After this many missing months in a row a series is taken to have ended (an
# account closed, alerts switched off). It is reported for those months and
# then left alone, rather than raising one more finding every month forever.
DORMANT_AFTER_MONTHS = 3

# A renewal becomes a task this many days before its date. Insurance renewals
# typically arrive 30-60 days out; 45 leaves time to compare a quote.
RENEWAL_LEAD_DAYS = 45
# ...and still becomes one this long after, so a date that slipped past while
# nothing was running is caught rather than silently skipped.
RENEWAL_GRACE_DAYS = 7

# Reply expected within this many days before mail counts as unanswered.
REPLY_AFTER_DAYS = 3

# A payment receipt is linked to a bill only within this window of its date.
PAYMENT_WINDOW = (timedelta(days=-3), timedelta(days=120))
STATEMENT_WINDOW = (timedelta(days=0), timedelta(days=75))

MAX_TEXT = 200_000           # characters read from one archived document

# Document kinds. Deterministic, from words; see `doc_kind`.
BILL, PAYMENT, STATEMENT, RENEWAL, OTHER = (
    "BILL", "PAYMENT", "STATEMENT", "RENEWAL", "OTHER")

# Finding kinds, in the order the brief should show them: money that may go
# out twice first, a quiet gap last.
KINDS = ("RECEIVED_AGAIN", "DUPLICATE_INVOICE", "AMOUNT_MISMATCH",
         "MERGED_INVOICES", "DOUBLE_BILLING", "MISSING_STATEMENT",
         "UNANSWERED_MAIL")

# Link kinds.
BILL_PAYMENT, BILL_STATEMENT, PAYMENT_STATEMENT = (
    "BILL_PAYMENT", "BILL_STATEMENT", "PAYMENT_STATEMENT")

# The one confidence nothing inferred may reach (§4: never authoritative).
CEILING = 0.95

# What ledger._record writes on an EVIDENCE event when the task had already
# ended (D-066). test_reconcile pins the two together.
AFTER_CLOSE_MARK = "task had already ended"


# ---------------------------------------------------------------------------
# Reading dates and numbers out of text
# ---------------------------------------------------------------------------

_MONTHS = {name: i for i, names in enumerate((
    (), ("january", "jan"), ("february", "feb"), ("march", "mar"),
    ("april", "apr"), ("may",), ("june", "jun"), ("july", "jul"),
    ("august", "aug"), ("september", "sept", "sep"), ("october", "oct"),
    ("november", "nov"), ("december", "dec"))) for name in names}
_MONTH_RX = "|".join(sorted(_MONTHS, key=len, reverse=True))

_DATE = re.compile(
    r"\b(?:(?P<y1>20\d{2})-(?P<m1>\d{1,2})-(?P<d1>\d{1,2})"
    r"|(?P<m2>\d{1,2})/(?P<d2>\d{1,2})/(?P<y2>(?:20)?\d{2})"
    rf"|(?P<mn3>{_MONTH_RX})\.?\s+(?P<d3>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<y3>20\d{{2}})"
    rf"|(?P<d4>\d{{1,2}})\s+(?P<mn4>{_MONTH_RX})\.?,?\s+(?P<y4>20\d{{2}}))\b",
    re.I)


def dates_in(text: str) -> list[date]:
    """Every real calendar date in `text`, in order. US order for 9/10/2026."""
    out = []
    for m in _DATE.finditer(text or ""):
        g = m.groupdict()
        try:
            if g["y1"]:
                d = date(int(g["y1"]), int(g["m1"]), int(g["d1"]))
            elif g["y2"]:
                y = int(g["y2"])
                d = date(y + 2000 if y < 100 else y, int(g["m2"]), int(g["d2"]))
            elif g["y3"]:
                d = date(int(g["y3"]), _MONTHS[g["mn3"].lower()], int(g["d3"]))
            else:
                d = date(int(g["y4"]), _MONTHS[g["mn4"].lower()], int(g["d4"]))
        except ValueError:                 # 2/30/2026
            continue
        out.append(d)
    return out


_NUM = r"(?P<num>(?:[A-Z]{1,6}[-/#]?)?\d+(?:[-/]\d+)*[A-Z]?)"
# (?![.,]\d): "invoice 550.00" is an amount, not invoice number 550.
_INVOICE_LABELLED = re.compile(
    r"\binvoice\s*(?:no\.?|number|num\.?|#|id)?\s*[:#.]?\s*" + _NUM + r"\b(?![.,]\d)", re.I)
_INVOICE_BARE = re.compile(r"\b(?P<num>INV[-#/]?\d+(?:[-/]\d+)*)\b", re.I)
# A year, a date, or an amount as the filename convention writes it
# ("invoice__USD550-00", D-007) — none of them identifies an invoice.
_NOT_AN_INVOICE = re.compile(
    r"^(?:(?:19|20)\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{1,2}-\d{1,2}"
    r"|(?:USD|EUR|GBP|CAD|AUD)-?\d.*)$", re.I)


def invoice_numbers(*texts: str) -> tuple[str, ...]:
    """Every invoice number mentioned, normalised so the PDF's "INV-0017" and
    the email's "invoice inv17" are the same string ("17"). Texts in priority
    order; within one text, in order of appearance.

    A bare year ("Invoice 2026") and a date ("Invoice 09/19/2026") are refused:
    every invoice of the year sharing the "number" 2026 would make them all one
    obligation, which is the wrong-merge direction.
    """
    found: list[str] = []
    for text in texts:
        if not text:
            continue
        hits = sorted((m.start(), m["num"]) for rx in (_INVOICE_LABELLED, _INVOICE_BARE)
                      for m in rx.finditer(text))
        for _, raw in hits:
            if _NOT_AN_INVOICE.match(raw):
                continue
            s = re.sub(r"[^A-Z0-9]", "", raw.upper())
            s = re.sub(r"^(?:INV|NO)(?=\d)", "", s)
            s = re.sub(r"^0+(?=\d)", "", s)
            if s and s not in found:
                found.append(s)
    return tuple(found)


def invoice_number(*texts: str) -> str | None:
    """The document's own invoice number: the first one, headline first."""
    found = invoice_numbers(*texts)
    return found[0] if found else None


_ACCOUNT_ENDING = re.compile(
    r"(?:ending(?:\s+in)?|last\s+(?:4|four)(?:\s+digits)?|acct\.?"
    r"|account(?:\s+(?:no\.?|number|#))?)"
    r"\s*[:#]?\s*(?:[x*•.\-]+\s*)?(?P<d>\d{4})\b", re.I)
_MASKED = re.compile(r"(?<![a-z])[x*•]{2,}[\s\-]*(?P<d>\d{4})\b", re.I)


def account_ending(*texts: str) -> str | None:
    """"ending in 4321", "Acct ****4321", "XXXX-4321" -> "4321"."""
    for text in texts:
        if not text:
            continue
        for rx in (_ACCOUNT_ENDING, _MASKED):
            m = rx.search(text)
            if m:
                return m["d"]
    return None


_PERIOD_RANGE = re.compile(
    r"\b(?:statement|billing|service|coverage)\s+(?:period|cycle)\b[^\n]{0,80}", re.I)
_PERIOD_ENDING = re.compile(
    r"\b(?:period|cycle)\s+end(?:ing|s|ed)?\b[^\n]{0,40}", re.I)
_PERIOD_MONTH = re.compile(
    rf"\b(?:for|statement)\s+(?:the\s+month\s+of\s+)?"
    rf"(?P<m>{_MONTH_RX})\.?\s+(?P<y>20\d{{2}})\b"
    rf"|\b(?P<m2>{_MONTH_RX})\.?\s+(?P<y2>20\d{{2}})\s+(?:statement|invoice|bill)\b", re.I)


def period(*texts: str) -> str | None:
    """The month a document COVERS ("YYYY-MM"), when it says so.

    A statement for August usually arrives in September; this is how the two
    are told apart — and why §4's "retain ... different statement periods"
    holds for two statements that otherwise read alike (scenario 6).
    """
    for text in texts:
        if not text:
            continue
        m = _PERIOD_RANGE.search(text)
        if m and dates_in(m.group(0)):
            return dates_in(m.group(0))[-1].isoformat()[:7]
        m = _PERIOD_ENDING.search(text)
        if m and dates_in(m.group(0)):
            return dates_in(m.group(0))[0].isoformat()[:7]
        m = _PERIOD_MONTH.search(text)
        if m:
            name, year = (m["m"], m["y"]) if m["m"] else (m["m2"], m["y2"])
            return f"{int(year):04d}-{_MONTHS[name.lower()]:02d}"
    return None


_RENEWAL_PHRASE = re.compile(
    r"\b(?:renewal\s+(?:date|effective(?:\s+date)?)"
    r"|(?:will\s+)?renews?(?:\s+automatically)?\s+(?:on|by)"
    r"|renew\s+before"
    r"|expir(?:ation|y)\s+date|expires?\s+on"
    r"|(?P<range>(?:policy|coverage|contract)\s+(?:period|term))"
    r"|term\s+end(?:s|ing)?|valid\s+(?:through|thru|until))\b", re.I)


def renewal_date(text: str) -> tuple[date, str] | None:
    """(date, the words it was read from), or None.

    "Policy period 12/01/2025 to 12/01/2026" is a range and the renewal is its
    end; every other phrase ("renews on", "expiration date") names the date
    itself. The phrase is returned so the task can show what it was read from
    — the date is provisional until someone has looked (§2).
    """
    for m in _RENEWAL_PHRASE.finditer(text or ""):
        line_end = text.find("\n", m.end())
        window = text[m.end(): (line_end if line_end != -1 else len(text))][:100]
        found = dates_in(window)
        if found:
            phrase = (m.group(0) + window).strip()[:120]
            return (found[-1] if m["range"] else found[0]), phrase
    return None


# What a document IS, from its headline words. Order matters: "Invoice INV17
# paid" is proof of payment, "statement ready — amount due" is a statement.
_KIND_RULES = (
    (PAYMENT, re.compile(
        r"\b(?:payment\s+(?:confirmation|confirmed|received|processed|successful"
        r"|complete|completed|sent|posted)|receipts?|paid|thank\s+you\s+for\s+your"
        r"\s+payment|we\s+received\s+your\s+payment"
        r"|autopay\s+(?:processed|complete|completed))\b", re.I)),
    (STATEMENT, re.compile(r"\bstatements?\b", re.I)),
    (RENEWAL, re.compile(
        r"\b(?:renewal|renews?|renewing|expir(?:es|ing|ation)"
        r"|policy\s+(?:period|term))\b", re.I)),
    (BILL, re.compile(
        r"\b(?:invoices?|bill|amount\s+due|balance\s+due|payment\s+due"
        r"|past\s+due|pay\s+by)\b", re.I)),
)
_KIND_BY_SUBCATEGORY = {
    "invoices-received": BILL, "invoices-issued": BILL,
    "receipts": PAYMENT, "statements": STATEMENT, "renewals": RENEWAL,
}
# Where a renewal date in the text is worth a task even when the headline
# does not say "renewal": policies, subscriptions, licences, registrations.
_RENEWAL_SUBCATEGORIES = frozenset({
    "renewals", "policies", "insurance-property", "insurance-auto",
    "insurance-health", "subscriptions", "active", "licensing",
    "registration"})


def doc_kind(headline: str, subcategory: str | None,
             amount_cents: int | None, due_date: date | None) -> str:
    """BILL, PAYMENT, STATEMENT, RENEWAL or OTHER, from words, not a model.

    The headline only (descriptor, subject, attachment name), never the body:
    every invoice footer says "thank you for your payment", and a body search
    would make every bill its own receipt.
    """
    for kind, rx in _KIND_RULES:
        if rx.search(headline):
            return kind
    if subcategory in _KIND_BY_SUBCATEGORY:
        return _KIND_BY_SUBCATEGORY[subcategory]
    if amount_cents and due_date:
        return BILL
    return OTHER


# ---------------------------------------------------------------------------
# Counterparties
# ---------------------------------------------------------------------------

_CP_STOP = frozenset({
    "inc", "llc", "ltd", "llp", "pllc", "plc", "corp", "corporation", "co",
    "company", "the", "and", "of", "for", "services", "service", "group",
    "online", "usa", "na"})


def _cp_tokens(counterparty: str | None) -> frozenset[str]:
    words = re.split(r"[^a-z0-9]+", (counterparty or "").lower())
    return frozenset(w for w in words if len(w) >= 2 and w not in _CP_STOP)


def counterparties_match(a: str | None, b: str | None) -> bool:
    """Same counterparty, allowing for how differently a model names one.

    "psg-global" and "psg-global-tax-and-accounting" match: one's words are a
    subset of the other's. "bank-of-america" and "america-first-credit-union"
    do not. Never by first word alone, which would make every bank one bank.
    """
    ta, tb = _cp_tokens(a), _cp_tokens(b)
    if not ta or not tb:
        return False
    return ta <= tb or tb <= ta


def _cp_key(counterparty: str | None) -> str:
    tokens = _cp_tokens(counterparty)
    return "-".join(sorted(tokens)) if tokens else ledger._norm(counterparty)


def _who(counterparty: str | None) -> str:
    return (counterparty or "unknown").replace("-", " ").title()


def _money(cents: int | None, currency: str = "USD") -> str:
    if cents is None:
        return "no amount"
    sign = "-" if cents < 0 else ""
    figure = f"{abs(cents) / 100:,.2f}"
    if (currency or "USD") == "USD":
        return f"{sign}${figure}"
    return f"{sign}{currency} {figure}"


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Doc:
    """One filed document, with the facts reconciliation needs."""
    id: int
    sha256: str
    root: int                  # the message it arrived in (top of parent_id)
    source: str
    source_ref: str | None
    original_name: str | None
    ingested_at: datetime | None
    doc_date: date | None
    entity_id: str
    domain: str | None
    category: str
    subcategory: str | None
    counterparty: str
    descriptor: str
    amount_cents: int | None
    currency: str
    due_date: date | None
    requires_reply: bool
    confidence: float
    kind: str
    invoice_no: str | None
    invoice_nos: tuple[str, ...]   # every one it mentions (a payment may cover several)
    account_ending: str | None
    period: str | None
    subject: str
    text: str = field(default="", repr=False)

    @property
    def month(self) -> str | None:
        """The month it bills for: what it says it covers, else its own date."""
        return self.period or (self.doc_date.isoformat()[:7] if self.doc_date else None)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=db.TZ)


def _parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def archived_text(filed_path: str | None) -> str:
    """The document's text as filed: itself when it is text, else its OCR
    sidecar (`<name>.txt`, filing.file_artifact). "" when neither is readable."""
    if not filed_path:
        return ""
    p = Path(filed_path)
    candidates = ([p] if p.suffix.lower() in TEXT_SUFFIXES else []) + \
        [p.with_name(p.name + ".txt")]
    for c in candidates:
        try:
            if c.is_file():
                with open(c, encoding="utf-8", errors="replace") as fh:
                    return fh.read(MAX_TEXT)
        except OSError:
            continue
    return ""


_SUBJECT = re.compile(r"^Subject:[ \t]*(.*)$", re.M)


def load_docs(conn: sqlite3.Connection, *, read_text: bool = True) -> list[Doc]:
    """Every filed document with its latest classification.

    UNASSIGNED and JUNK are left out: no entity, nothing to reconcile against,
    and §0 forbids comparing across entities anyway.
    """
    parents = {r[0]: r[1] for r in conn.execute("SELECT id, parent_id FROM artifacts")}
    rows = conn.execute("""
        SELECT a.id, a.sha256, a.source, a.source_ref, a.original_name,
               a.ingested_at, a.doc_date, a.filed_path,
               c.entity_id, c.domain, c.category, c.subcategory, c.counterparty,
               c.descriptor, c.amount_cents, c.currency, c.due_date,
               c.requires_reply, c.confidence
          FROM artifacts a
          JOIN classifications c ON c.id = (
               SELECT MAX(id) FROM classifications WHERE artifact_id = a.id)
         WHERE a.status = 'FILED'
           AND c.entity_id NOT IN ('UNASSIGNED', 'JUNK')
      ORDER BY a.id
    """).fetchall()

    docs = []
    for r in rows:
        text = archived_text(r["filed_path"]) if read_text else ""
        m = _SUBJECT.search(text[:4000])
        subject = m.group(1).strip() if m else ""
        name = re.sub(r"[_\-.]+", " ", Path(r["original_name"] or "").stem)
        descriptor = (r["descriptor"] or "").replace("-", " ")
        headline = " ".join(x for x in (descriptor, subject, name) if x)
        due = _parse_date(r["due_date"])
        numbers = invoice_numbers(headline, text)
        docs.append(Doc(
            id=r["id"], sha256=r["sha256"], root=_root(parents, r["id"]),
            source=r["source"], source_ref=r["source_ref"],
            original_name=r["original_name"],
            ingested_at=_parse_dt(r["ingested_at"]),
            doc_date=_parse_date(r["doc_date"]),
            entity_id=r["entity_id"], domain=r["domain"], category=r["category"],
            subcategory=r["subcategory"], counterparty=r["counterparty"] or "",
            descriptor=r["descriptor"] or "", amount_cents=r["amount_cents"],
            currency=r["currency"] or "USD", due_date=due,
            requires_reply=bool(r["requires_reply"]),
            confidence=float(r["confidence"] or 0.0),
            kind=doc_kind(headline, r["subcategory"], r["amount_cents"], due),
            invoice_no=numbers[0] if numbers else None, invoice_nos=numbers,
            account_ending=account_ending(headline, text),
            period=period(headline, text),
            subject=subject, text=text))
    return docs


def _root(parents: dict[int, int | None], aid: int) -> int:
    seen = {aid}
    while parents.get(aid):
        aid = parents[aid]
        if aid in seen:                    # a cycle is a bug elsewhere; stop
            break
        seen.add(aid)
    return aid


@dataclass(frozen=True)
class TaskIndex:
    """Which task each artifact is evidence for, following DUPLICATE to the
    task that survived (ledger.mark_duplicate)."""
    of_artifact: dict[int, int]
    rows: dict[int, sqlite3.Row]

    def live(self, task_id: int) -> bool:
        return self.rows[task_id]["status"] not in ledger.TERMINAL

    def closed(self, task_id: int) -> bool:
        return self.rows[task_id]["status"] == "CLOSED"


def task_index(conn: sqlite3.Connection) -> TaskIndex:
    rows = {r["id"]: r for r in conn.execute("SELECT * FROM tasks")}

    def canon(tid: int) -> int:
        seen = {tid}
        while rows[tid]["status"] == "DUPLICATE" and rows[tid]["duplicate_of"]:
            tid = int(rows[tid]["duplicate_of"])
            if tid in seen:
                break
            seen.add(tid)
        return tid

    of: dict[int, int] = {}
    for aid, tid in conn.execute(
            "SELECT artifact_id, task_id FROM task_sources "
            "WHERE artifact_id IS NOT NULL ORDER BY id"):
        of.setdefault(aid, canon(tid))
    for t in rows.values():
        if t["source_artifact"] and t["source_artifact"] not in of:
            of[t["source_artifact"]] = canon(int(t["id"]))
    return TaskIndex(of_artifact=of, rows=rows)


# ---------------------------------------------------------------------------
# Obligations
# ---------------------------------------------------------------------------

@dataclass
class Obligation:
    """One bill, however many times and ways it arrived."""
    docs: list[Doc]
    invoice_no: str | None
    tasks: set[int]

    @property
    def entity_id(self) -> str:
        return self.docs[0].entity_id

    @property
    def counterparty(self) -> str:
        return Counter(d.counterparty for d in self.docs).most_common(1)[0][0]

    @property
    def first(self) -> Doc:
        return min(self.docs, key=lambda d: (d.ingested_at or _EPOCH, d.id))

    @property
    def amounts(self) -> set[tuple[str, int]]:
        return {(d.currency, d.amount_cents) for d in self.docs
                if d.amount_cents is not None}

    @property
    def amount(self) -> tuple[str, int] | None:
        a = self.amounts
        return next(iter(a)) if len(a) == 1 else None

    @property
    def month(self) -> str | None:
        return next((d.period for d in self.docs if d.period), None) or self.first.month

    @property
    def ident(self) -> str:
        """Stable across runs: the invoice number, else the oldest document."""
        if self.invoice_no:
            return f"{self.entity_id}|{_cp_key(self.counterparty)}|inv={self.invoice_no}"
        return f"{self.entity_id}|artifact={min(d.id for d in self.docs)}"

    def label(self) -> str:
        what = f"invoice {self.invoice_no}" if self.invoice_no else "bill"
        amt = f" ({_money(self.amount[1], self.amount[0])})" if self.amount else ""
        return f"{_who(self.counterparty)} {what}{amt}"


_EPOCH = datetime(1970, 1, 1, tzinfo=db.TZ)


class _Groups:
    """Union-find that refuses to join two different invoice numbers or two
    entities — the merges that would lose an obligation."""

    def __init__(self, docs: list[Doc]):
        self.up = {d.id: d.id for d in docs}
        self.num = {d.id: d.invoice_no for d in docs}
        self.ent = {d.id: d.entity_id for d in docs}

    def find(self, x: int) -> int:
        while self.up[x] != x:
            self.up[x] = self.up[self.up[x]]
            x = self.up[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb or self.ent[ra] != self.ent[rb]:
            return
        na, nb = self.num[ra], self.num[rb]
        if na and nb and na != nb:
            return
        lo, hi = sorted((ra, rb))
        self.up[hi] = lo
        self.num[lo] = na or nb


def _join_members(g: _Groups, members: list[Doc]) -> None:
    """Join documents that arrived together or sit on one task — unless they
    carry two different invoice numbers, in which case the ones with none
    cannot be placed and are left alone rather than guessed."""
    if len({d.invoice_no for d in members if d.invoice_no}) > 1:
        return
    for d in members[1:]:
        g.union(members[0].id, d.id)


def obligations(docs: Iterable[Doc], tasks: TaskIndex) -> list[Obligation]:
    bills = sorted((d for d in docs if d.kind == BILL), key=lambda d: d.id)
    g = _Groups(bills)

    by_number: dict[tuple[str, str], list[Doc]] = defaultdict(list)
    for d in bills:
        if d.invoice_no:
            by_number[(d.entity_id, d.invoice_no)].append(d)
    for group in by_number.values():
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if counterparties_match(a.counterparty, b.counterparty):
                    g.union(a.id, b.id)

    by_root: dict[int, list[Doc]] = defaultdict(list)
    by_task: dict[int, list[Doc]] = defaultdict(list)
    for d in bills:
        by_root[d.root].append(d)
        if d.id in tasks.of_artifact:
            by_task[tasks.of_artifact[d.id]].append(d)
    for members in list(by_root.values()) + list(by_task.values()):
        _join_members(g, members)

    groups: dict[int, list[Doc]] = defaultdict(list)
    for d in bills:
        groups[g.find(d.id)].append(d)
    return [Obligation(docs=members, invoice_no=g.num[top],
                       tasks={tasks.of_artifact[d.id] for d in members
                              if d.id in tasks.of_artifact})
            for top, members in sorted(groups.items())]


# ---------------------------------------------------------------------------
# Findings and links
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Finding:
    key: str                   # stable across runs: what makes it the same finding
    kind: str
    summary: str
    entity_id: str | None = None
    detail: str | None = None
    confidence: float | None = None
    task_id: int | None = None
    artifact_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class Link:
    """A candidate: these two documents are evidence of the same money."""
    kind: str
    from_artifact: int         # the bill (or, for PAYMENT_STATEMENT, the payment)
    to_artifact: int           # the payment or the statement
    confidence: float
    basis: str                 # which facts matched, in words
    amount_cents: int | None = None
    task_id: int | None = None

    @property
    def key(self) -> str:
        return f"{self.kind}|{self.from_artifact}|{self.to_artifact}"


def _ids(docs: Iterable[Doc]) -> tuple[int, ...]:
    return tuple(sorted({d.id for d in docs}))


def _tasks_txt(ids: Iterable[int]) -> str:
    return ", ".join(f"#{t}" for t in sorted(ids))


def invoice_findings(obs: list[Obligation], tasks: TaskIndex) -> list[Finding]:
    """DUPLICATE_INVOICE, AMOUNT_MISMATCH, MERGED_INVOICES, DOUBLE_BILLING,
    and the RECEIVED_AGAIN cases that only the documents (not the ledger) show.
    """
    out: list[Finding] = []

    for ob in obs:
        live = sorted(t for t in ob.tasks if tasks.live(t))
        if len(live) >= 2:
            keep, *rest = live
            how = (f"invoice {ob.invoice_no}" if ob.invoice_no
                   else "the same message")
            out.append(Finding(
                key=f"dup-invoice|{ob.ident}", kind="DUPLICATE_INVOICE",
                entity_id=ob.entity_id, task_id=keep,
                confidence=0.8 if ob.invoice_no else 0.7,
                artifact_ids=_ids(ob.docs),
                summary=(f"{ob.label()} is on {len(live)} open tasks "
                         f"({_tasks_txt(live)}). One bill: pay it once."),
                detail=(f"Matched on {how}. If they are the same obligation: "
                        + "; ".join(f"./ops/done.py {t} --duplicate-of {keep}"
                                    for t in rest))))

        amounts = ob.amounts
        if len(amounts) >= 2:
            shown = ", ".join(_money(c, cur) for cur, c in sorted(amounts))
            out.append(Finding(
                key=f"amount|{ob.ident}|" + ",".join(str(c) for _, c in sorted(amounts)),
                kind="AMOUNT_MISMATCH", entity_id=ob.entity_id,
                task_id=min(ob.tasks) if ob.tasks else None, confidence=0.9,
                artifact_ids=_ids(ob.docs),
                summary=(f"{_who(ob.counterparty)} "
                         f"{'invoice ' + ob.invoice_no if ob.invoice_no else 'bill'}"
                         f" reads as {shown} in different copies."),
                detail=("The amounts were read by a model or OCR and are not "
                        "authoritative. Check the original before paying.")))

        out += _received_again_from_tasks(ob, tasks)

    # Two invoice numbers on one task: the ledger merged two obligations.
    by_task: dict[int, list[Obligation]] = defaultdict(list)
    for ob in obs:
        for t in ob.tasks:
            by_task[t].append(ob)
    for t, group in sorted(by_task.items()):
        numbers = sorted({o.invoice_no for o in group if o.invoice_no})
        if len(numbers) >= 2:
            out.append(Finding(
                key=f"merged|task={t}|" + ",".join(numbers), kind="MERGED_INVOICES",
                entity_id=group[0].entity_id, task_id=t, confidence=0.8,
                artifact_ids=_ids(d for o in group for d in o.docs),
                summary=(f"Task #{t} holds {len(numbers)} different invoices "
                         f"({', '.join(numbers)}) from {_who(group[0].counterparty)}. "
                         f"Closing it as paid could leave one unpaid."),
                detail="Usually the same due date and amount; check each invoice."))

    # Same counterparty, same amount, same month, different obligations.
    # Bucketed first: pairs are only ever compared within one entity and amount.
    buckets: dict[tuple[str, tuple[str, int]], list[Obligation]] = defaultdict(list)
    for ob in obs:
        if ob.amount:
            buckets[(ob.entity_id, ob.amount)].append(ob)
    for _, group in sorted(buckets.items()):
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                out += _same_money(a, b, tasks)
    return out


def _same_money(a: Obligation, b: Obligation, tasks: TaskIndex) -> list[Finding]:
    if (a.entity_id != b.entity_id or not a.amount or a.amount != b.amount
            or not a.month or a.month != b.month or (a.tasks & b.tasks)
            or not counterparties_match(a.counterparty, b.counterparty)):
        return []
    a, b = sorted((a, b), key=lambda o: (o.first.ingested_at or _EPOCH, o.first.id))
    money = _money(a.amount[1], a.amount[0])
    both = _ids(a.docs + b.docs)

    if a.invoice_no and b.invoice_no:
        return [Finding(
            key=f"double-bill|{a.ident}|{b.ident}", kind="DOUBLE_BILLING",
            entity_id=a.entity_id, confidence=0.5, artifact_ids=both,
            task_id=min(b.tasks) if b.tasks else None,
            summary=(f"{_who(a.counterparty)} sent two invoices for {money} in "
                     f"{a.month} ({a.invoice_no} and {b.invoice_no}). Billed "
                     f"twice, or two real charges?"),
            detail="Different invoice numbers; same amount and month.")]

    done_a = sorted(t for t in a.tasks if tasks.closed(t))
    live_b = sorted(t for t in b.tasks if tasks.live(t))
    if done_a and live_b:
        closed_at = _parse_dt(tasks.rows[done_a[0]]["completed_at"])
        if closed_at and b.first.ingested_at and b.first.ingested_at > closed_at:
            return [_again(done_a[0], tasks, b.docs, confidence=0.5,
                           how=f"same amount ({money}) and month, no invoice "
                               f"number to tell them apart")]
    live_a = sorted(t for t in a.tasks if tasks.live(t))
    if live_a and live_b:
        return [Finding(
            key=f"dup-invoice|{a.ident}|{b.ident}", kind="DUPLICATE_INVOICE",
            entity_id=a.entity_id, confidence=0.5, artifact_ids=both,
            task_id=live_a[0],
            summary=(f"{_who(a.counterparty)}: two open tasks for {money} in "
                     f"{a.month} ({_tasks_txt(live_a + live_b)}). One bill "
                     f"recorded twice?"),
            detail=(f"No invoice number on at least one of them. If they are "
                    f"one bill: ./ops/done.py {live_b[0]} --duplicate-of {live_a[0]}"))]
    return []


def _again(task_id: int, tasks: TaskIndex, new_docs: Iterable[Doc], *,
           confidence: float, how: str, key: str | None = None,
           when: str | None = None) -> Finding:
    new_docs = list(new_docs)
    t = tasks.rows[task_id]
    first = min(new_docs, key=lambda d: (d.ingested_at or _EPOCH, d.id))
    when = when or (first.ingested_at.date().isoformat() if first.ingested_at else "later")
    money = any(d.kind == BILL or d.amount_cents for d in new_docs)
    return Finding(
        key=key or f"again|task={task_id}|root={first.root}", kind="RECEIVED_AGAIN",
        entity_id=t["entity_id"], task_id=task_id, confidence=confidence,
        artifact_ids=_ids(new_docs),
        summary=((f"Paid bill received again: " if money else
                  f"Received again after it was closed: ")
                 + f"#{task_id} {t['title']} (closed "
                 f"{(t['completed_at'] or '')[:10]}) arrived again on {when}. "
                 f"A second bill, or a resend?"),
        detail=f"Matched on {how}. Nothing was reopened or paid.")


def _received_again_from_tasks(ob: Obligation, tasks: TaskIndex) -> list[Finding]:
    """A closed obligation whose invoice number arrived again — including as a
    NEW task, which happens when the resend carries a new due date and so a
    new event key (the ledger cannot see it; the invoice number can)."""
    out = []
    for t in sorted(x for x in ob.tasks if tasks.closed(x)):
        closed_at = _parse_dt(tasks.rows[t]["completed_at"])
        if not closed_at:
            continue
        before = {d.root for d in ob.docs
                  if d.ingested_at and d.ingested_at <= closed_at}
        later: dict[int, list[Doc]] = defaultdict(list)
        for d in ob.docs:
            if d.ingested_at and d.ingested_at > closed_at and d.root not in before:
                later[d.root].append(d)
        for root, docs in sorted(later.items()):
            how = (f"invoice {ob.invoice_no}" if ob.invoice_no
                   else "the ledger's event key")
            out.append(_again(t, tasks, docs, confidence=0.8, how=how))
    return out


def ledger_received_again(conn: sqlite3.Connection, docs: list[Doc],
                          tasks: TaskIndex) -> list[Finding]:
    """Scenario 4 as the ledger saw it (D-066: "Evidence after close ... is
    attached to the closed task ... Surfacing it is R26's").

    Two routes in. Evidence the ledger merged into a task that had already
    ended — read from the ledger's own EVIDENCE event, which says so when it
    happens, rather than by comparing timestamps that have one-second
    resolution; and a byte-identical copy (duplicate_refs) of a document of a
    closed task, arriving in a DIFFERENT message — ingest stops identical bytes
    before the ledger, so the ledger never hears of those. Re-polls of the same
    message are not a resend and are ignored.

    Only while the task is still CLOSED: once a human reopens it, he has
    picked the evidence up, and the finding resolves.
    """
    by_id = {d.id: d for d in docs}
    out: list[Finding] = []

    later: dict[tuple[int, int], list[Doc]] = defaultdict(list)
    for e in conn.execute(
            "SELECT e.task_id, e.detail FROM task_events e WHERE e.event = 'EVIDENCE' "
            "AND e.detail LIKE ? ORDER BY e.id", (f"%{AFTER_CLOSE_MARK}%",)):
        source_key = str(e["detail"]).split(" · ")[0]
        src = conn.execute("SELECT artifact_id FROM task_sources WHERE source_key = ?",
                           (source_key,)).fetchone()
        doc = by_id.get(src["artifact_id"]) if src else None
        if doc is None:
            continue
        tid = tasks.of_artifact.get(doc.id, e["task_id"])
        if tid in tasks.rows and tasks.closed(tid):
            later[(tid, doc.root)].append(doc)
    for (tid, _), group in sorted(later.items()):
        out.append(_again(tid, tasks, group, confidence=0.9,
                          how="the ledger's event key (merged after close)"))

    for r in conn.execute(
            "SELECT d.id AS dref, d.seen_at, d.source_ref AS dsr, a.id AS aid, "
            "a.source_ref AS asr FROM duplicate_refs d "
            "JOIN artifacts a ON a.sha256 = d.sha256 ORDER BY d.id"):
        tid = tasks.of_artifact.get(r["aid"])
        doc = by_id.get(r["aid"])
        if tid is None or doc is None or not tasks.closed(tid) or not r["dsr"]:
            continue
        msg = str(r["dsr"]).split("/")[0]
        if msg == str(r["asr"] or "").split("/")[0]:
            continue                        # the same message polled again
        closed_at = _parse_dt(tasks.rows[tid]["completed_at"])
        seen = _parse_dt(r["seen_at"])
        if closed_at and seen and seen > closed_at:
            out.append(_again(tid, tasks, [doc], confidence=0.95,
                              how=f"an identical copy in message {msg}",
                              key=f"again|task={tid}|msg={msg}",
                              when=str(r["seen_at"])[:10]))
    return out


# ---------------------------------------------------------------------------
# Links: bill <-> proof of payment <-> statement line
# ---------------------------------------------------------------------------

def _rep(ob: Obligation, tasks: TaskIndex) -> Doc:
    """The document a link points at: one already on a task, else the oldest."""
    tasked = [d for d in ob.docs if d.id in tasks.of_artifact]
    return min(tasked or ob.docs, key=lambda d: (d.ingested_at or _EPOCH, d.id))


def _in_window(start: date | None, other: date | None,
               window: tuple[timedelta, timedelta]) -> bool:
    if not start or not other:
        return False
    return start + window[0] <= other <= start + window[1]


def payment_links(docs: list[Doc], obs: list[Obligation],
                  tasks: TaskIndex) -> tuple[list[Link], list[Finding]]:
    """Proof of payment for a bill: same entity, compatible counterparty,
    exactly equal amount, plausible dates; an invoice number makes it strong.

    One payment pays one bill. Pairs are taken best first, so two identical
    $50 monthly bills and two $50 payments pair by date rather than every
    payment linking to every bill.

    A payment that names the bill's invoice number but a different amount is
    not linked: it is an AMOUNT_MISMATCH (a partial payment, or a misread).
    """
    payments = [d for d in docs if d.kind == PAYMENT and d.amount_cents is not None]
    candidates: list[tuple[float, int, Obligation, Doc, str]] = []
    findings: list[Finding] = []

    for p in payments:
        for ob in obs:
            if ob.entity_id != p.entity_id:
                continue
            first = ob.first
            names_it = bool(ob.invoice_no) and ob.invoice_no in p.invoice_nos
            if p.invoice_nos and ob.invoice_no and not names_it:
                continue                    # it names other invoices, not this one
            cp_ok = counterparties_match(ob.counterparty, p.counterparty)
            if not (cp_ok or names_it):
                continue
            if not _in_window(first.doc_date, p.doc_date, PAYMENT_WINDOW):
                continue
            if not ob.amount or ob.amount[0] != p.currency:
                continue
            if ob.amount[1] != p.amount_cents:
                if names_it and cp_ok:
                    findings.append(Finding(
                        key=f"amount|{ob.ident}|payment={p.id}",
                        kind="AMOUNT_MISMATCH", entity_id=ob.entity_id,
                        task_id=min(ob.tasks) if ob.tasks else None,
                        confidence=0.8, artifact_ids=_ids(ob.docs + [p]),
                        summary=(f"Payment of {_money(p.amount_cents, p.currency)} "
                                 f"({(p.doc_date or '')}) for {ob.label()}."),
                        detail=("Partial payment, or a misread amount: OCR is "
                                "not authoritative. Check both originals.")))
                continue

            score, basis = 0.45, ["amount", "dates"]
            if ledger._norm(ob.counterparty) == ledger._norm(p.counterparty):
                score += 0.1
                basis.insert(0, "counterparty")
            elif cp_ok:
                score += 0.05
                basis.insert(0, "counterparty (similar)")
            if names_it:
                score += 0.3
                basis.append(f"invoice {ob.invoice_no}")
            gap = abs((p.doc_date - first.doc_date).days)
            if gap <= 45:
                score += 0.05
            candidates.append((min(score, CEILING), gap, ob, p, ", ".join(basis)))

    links: list[Link] = []
    used_p: set[int] = set()
    used_ob: set[int] = set()
    for score, gap, ob, p, basis in sorted(
            candidates, key=lambda c: (-c[0], c[1], c[3].id, c[2].first.id)):
        if p.id in used_p or id(ob) in used_ob:
            continue
        used_p.add(p.id)
        used_ob.add(id(ob))
        rep = _rep(ob, tasks)
        links.append(Link(kind=BILL_PAYMENT, from_artifact=rep.id, to_artifact=p.id,
                          confidence=round(score, 2), basis=basis,
                          amount_cents=p.amount_cents,
                          task_id=tasks.of_artifact.get(rep.id)))
    return links, findings


def _amount_rx(cents: int) -> re.Pattern:
    whole, frac = divmod(abs(cents), 100)
    plain, commas = f"{whole}", f"{whole:,}"
    alts = {plain, commas}
    return re.compile(r"(?<![\d.,])(?:" + "|".join(re.escape(a) for a in alts)
                      + rf")\.{frac:02d}(?!\d)")


def statement_links(docs: list[Doc], obs: list[Obligation],
                    tasks: TaskIndex) -> list[Link]:
    """A statement line that shows the money moving: a line of the statement's
    text with the exact amount and a word of the counterparty's name.

    Only statements with transaction lines in their text can link; the
    "your statement is ready" alerts that make up most of them have none, and
    link to nothing — "when supported", in §4's words.
    """
    statements = [d for d in docs if d.kind == STATEMENT and d.text]
    if not statements:
        return []
    # (kind, the document the link points from, the date to measure from, amount)
    subjects: list[tuple[str, Doc, Doc, int]] = []
    for ob in obs:
        if ob.amount:
            subjects.append((BILL_STATEMENT, _rep(ob, tasks), ob.first, ob.amount[1]))
    for d in docs:
        if d.kind == PAYMENT and d.amount_cents is not None:
            subjects.append((PAYMENT_STATEMENT, d, d, d.amount_cents))

    candidates = []
    for kind, subj, anchor, amount in subjects:
        words = _cp_tokens(subj.counterparty)
        rx = _amount_rx(amount)
        for st in statements:
            if st.entity_id != subj.entity_id or not _in_window(
                    anchor.doc_date, st.doc_date, STATEMENT_WINDOW):
                continue
            for n, line in enumerate(st.text.splitlines()):
                low = line.lower()
                hits = [w for w in words if re.search(rf"\b{re.escape(w)}\b", low)]
                if hits and rx.search(line):
                    score = 0.5 + (0.1 if set(hits) == set(words) else 0.0)
                    gap = (st.doc_date - anchor.doc_date).days
                    candidates.append((score, gap, kind, subj, st, n, line.strip(), amount))

    # One statement line is one movement of money: it links once, to the
    # closest-dated subject. A payment, being nearer the posting than the
    # bill, usually takes it — and the bill reaches the statement through it.
    links, used_line, used_subj = [], set(), set()
    for score, gap, kind, subj, st, n, line, amount in sorted(
            candidates, key=lambda c: (-c[0], c[1], c[3].id, c[4].id, c[5])):
        if (st.id, n) in used_line or (kind, subj.id) in used_subj:
            continue
        used_line.add((st.id, n))
        used_subj.add((kind, subj.id))
        links.append(Link(kind=kind, from_artifact=subj.id, to_artifact=st.id,
                          confidence=round(min(score, CEILING), 2),
                          basis=f"amount and name on statement line: {line[:120]}",
                          amount_cents=amount,
                          task_id=tasks.of_artifact.get(subj.id)))
    return links


def paid_then_received_again(obs: list[Obligation], links: list[Link],
                             docs: list[Doc], tasks: TaskIndex) -> list[Finding]:
    """Scenario 4 where nobody closed the task: a payment receipt is linked to
    the bill, and the bill arrived again after the receipt."""
    by_id = {d.id: d for d in docs}
    out = []
    for link in links:
        if link.kind != BILL_PAYMENT:
            continue
        ob = next((o for o in obs if any(d.id == link.from_artifact for d in o.docs)), None)
        pay = by_id.get(link.to_artifact)
        if ob is None or pay is None or pay.ingested_at is None:
            continue
        before = {d.root for d in ob.docs
                  if d.ingested_at and d.ingested_at <= pay.ingested_at}
        before.add(pay.root)
        later: dict[int, list[Doc]] = defaultdict(list)
        for d in ob.docs:
            if d.ingested_at and d.ingested_at > pay.ingested_at and d.root not in before:
                later[d.root].append(d)
        closes = [_parse_dt(tasks.rows[t]["completed_at"]) for t in ob.tasks
                  if tasks.closed(t)]
        for root, group in sorted(later.items()):
            first = min(group, key=lambda d: (d.ingested_at, d.id))
            if any(c and c < first.ingested_at for c in closes):
                continue        # the closed task already says it (one event, one finding)
            out.append(Finding(
                key=f"again|pay={pay.id}|root={root}", kind="RECEIVED_AGAIN",
                entity_id=ob.entity_id,
                confidence=round(min(link.confidence * 0.9, CEILING), 2),
                task_id=tasks.of_artifact.get(first.id),
                artifact_ids=_ids(group + [pay]),
                summary=(f"Paid bill received again: {ob.label()} looks paid "
                         f"(receipt of {pay.doc_date}) and arrived again on "
                         f"{first.ingested_at.date().isoformat()}. A second "
                         f"bill, or a resend?"),
                detail=(f"Payment link is a candidate ({link.confidence:.2f}: "
                        f"{link.basis}). Nothing was paid or closed.")))
    return out


# ---------------------------------------------------------------------------
# Missing statements
# ---------------------------------------------------------------------------

def _month_index(d: date) -> int:
    return d.year * 12 + d.month - 1


def _month_of(index: int) -> tuple[int, int]:
    return index // 12, index % 12 + 1


def _month_day(index: int, day: int) -> date:
    y, m = _month_of(index)
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return date(y, m, min(day, (nxt - timedelta(days=1)).day))


def statement_series(docs: Iterable[Doc]) -> dict[tuple[str, str, str], list[Doc]]:
    """(entity, institution, account ending or "") -> its statements.

    An institution with exactly one known account ending takes its statements
    that name none (an alert that omitted the digits is still that account's).
    With several accounts, the unnamed ones form a series of their own.
    """
    raw: dict[tuple[str, str], list[Doc]] = defaultdict(list)
    for d in docs:
        if d.kind == STATEMENT and d.doc_date:
            raw[(d.entity_id, _cp_key(d.counterparty))].append(d)
    series: dict[tuple[str, str, str], list[Doc]] = defaultdict(list)
    for (ent, inst), members in raw.items():
        endings = {d.account_ending for d in members if d.account_ending}
        only = next(iter(endings)) if len(endings) == 1 else None
        for d in members:
            series[(ent, inst, d.account_ending or only or "")].append(d)
    return series


def missing_statements(docs: Iterable[Doc], *, today: date,
                       grace_days: int = GRACE_DAYS) -> list[Finding]:
    """A month with no statement, for a series that has shown itself monthly.

    Monthly means two statements in consecutive months at least once — a
    quarterly or annual series is not checked, rather than flagged every month
    it was never going to arrive. Months are the month a statement ARRIVED (its
    document date), so August's statement issued on Sept 3 is September's
    arrival, consistently. Two statements in one month (scenario 6, or one
    account's alert and its PDF) are both kept and count once.
    """
    out = []
    for (ent, inst, acct), members in sorted(statement_series(docs).items()):
        months = sorted({_month_index(d.doc_date) for d in members})
        if len(months) < 2 or min(b - a for a, b in zip(months, months[1:])) != 1:
            continue
        first_day = {}
        for d in sorted(members, key=lambda d: d.doc_date):
            first_day.setdefault(_month_index(d.doc_date), d.doc_date.day)
        usual = int(median(first_day.values()))
        present = set(months)
        who = _who(members[0].counterparty)
        account = f" (account ending {acct})" if acct else ""

        run = 0
        for m in range(months[0] + 1, _month_index(today) + 1):
            if m in present:
                run = 0
                continue
            run += 1
            if run > DORMANT_AFTER_MONTHS:
                continue
            expected = _month_day(m, usual)
            if today <= expected + timedelta(days=grace_days):
                continue
            last = max((d.doc_date for d in members if _month_index(d.doc_date) < m))
            y, mo = _month_of(m)
            label = f"{y:04d}-{mo:02d}"
            out.append(Finding(
                key=f"missing-statement|{ent}|{inst}|{acct}|{label}",
                kind="MISSING_STATEMENT", entity_id=ent, confidence=0.7,
                artifact_ids=_ids(d for d in members if d.doc_date == last),
                summary=(f"No {who} statement{account} for {label}. They "
                         f"usually arrive around day {usual}; the last was "
                         f"{last.isoformat()}."),
                detail=(f"{len(members)} statement(s) in this series. If the "
                        f"account closed or alerts moved, acknowledge this.")))
    return out


# ---------------------------------------------------------------------------
# Unanswered mail — the check, waiting on its source
# ---------------------------------------------------------------------------

class ReplyIndex(Protocol):
    """What was sent. Nothing implements this yet.

    `replied(source_ref, since)` -> True if a message was sent in reply to (or
    in the same thread as) the received message `source_ref` after `since`;
    False if the sent mail was checked and has none; None if it cannot tell.

    The data does not exist: ingest stores no thread id (Gmail's `threadId` is
    parsed in adapters/gmail.Message.thread_id and dropped), and no mailbox's
    Sent folder is read as such. Guessing from "Re:" subjects would report
    mail as unanswered that was answered by phone or from another address, and
    a list he learns to distrust is worse than no list. So until a source
    exists, `unanswered_mail` returns how many messages are waiting to be
    checked, and no findings.
    """

    def replied(self, source_ref: str, since: date) -> bool | None: ...


def unanswered_mail(docs: Iterable[Doc], tasks: TaskIndex, *, today: date,
                    replies: ReplyIndex | None = None,
                    after_days: int = REPLY_AFTER_DAYS) -> tuple[list[Finding], int]:
    """(findings, how many messages needed checking).

    A message counts when the classifier said it needs a reply, it is at least
    `after_days` old, and no task for it has been closed.
    """
    waiting = []
    for d in docs:
        if d.source != "email" or not d.requires_reply or not d.source_ref:
            continue
        received = d.doc_date or (d.ingested_at.date() if d.ingested_at else None)
        if not received or (today - received).days < after_days:
            continue
        tid = tasks.of_artifact.get(d.id)
        if tid is not None and not tasks.live(tid):
            continue
        waiting.append((d, received))
    if replies is None:
        return [], len(waiting)

    out = []
    for d, received in waiting:
        if replies.replied(d.source_ref, received) is False:
            out.append(Finding(
                key=f"unanswered|{d.id}", kind="UNANSWERED_MAIL",
                entity_id=d.entity_id, confidence=0.6, artifact_ids=(d.id,),
                task_id=tasks.of_artifact.get(d.id),
                summary=(f"No reply sent to {_who(d.counterparty)}: "
                         f"\"{(d.subject or d.descriptor)[:80]}\" "
                         f"(received {received.isoformat()})."),
                detail=("Classified as needing a reply; no sent message "
                        "found in its thread.")))
    return out, len(waiting)


# ---------------------------------------------------------------------------
# Renewals -> tasks
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Renewal:
    doc: Doc
    renewal_date: date
    phrase: str                # the words the date was read from

    @property
    def event_key(self) -> str | None:
        return ledger.event_key(entity_id=self.doc.entity_id,
                                counterparty=self.doc.counterparty,
                                due_date=self.renewal_date.isoformat(),
                                descriptor="renewal")

    @property
    def title(self) -> str:
        what = self.doc.descriptor.replace("-", " ").strip() or "renewal"
        if not re.search(r"renew|expir", what, re.I):
            what = f"{what} renewal"
        title = what.capitalize()
        if self.doc.counterparty:
            title += f" — {_who(self.doc.counterparty)}"
        return title[:200]


def renewals_due(docs: Iterable[Doc], tasks: TaskIndex, conn: sqlite3.Connection,
                 *, today: date, lead_days: int = RENEWAL_LEAD_DAYS) -> list[Renewal]:
    """Renewal dates read from documents that are inside the lead window and
    have no task yet.

    "No task yet" is checked three ways, because a duplicate task is the
    failure here: the document itself is evidence for a task; something that
    arrived in the same message is (the covering email with a due date and the
    policy PDF without one); or a task already exists for this counterparty
    and this date under any key.
    """
    docs = list(docs)
    tasked_roots = {d.root for d in docs if d.id in tasks.of_artifact}
    out = []
    for d in docs:
        if not (d.kind == RENEWAL or d.subcategory in _RENEWAL_SUBCATEGORIES):
            continue
        if d.id in tasks.of_artifact or d.root in tasked_roots:
            continue
        found = renewal_date(f"{d.descriptor.replace('-', ' ')}\n{d.subject}\n{d.text}")
        if not found:
            continue
        when, phrase = found
        if not (when - timedelta(days=lead_days) <= today
                <= when + timedelta(days=RENEWAL_GRACE_DAYS)):
            continue
        r = Renewal(doc=d, renewal_date=when, phrase=phrase)
        prefix = (f"v1|{d.entity_id.upper()}|{ledger._norm(d.counterparty)}"
                  f"|due={when.isoformat()}|")
        if conn.execute("SELECT 1 FROM tasks WHERE event_key = ? OR "
                        "substr(event_key, 1, ?) = ?",
                        (r.event_key, len(prefix), prefix)).fetchone():
            continue
        out.append(r)
    return out


def create_renewal_tasks(conn: sqlite3.Connection, renewals: Iterable[Renewal],
                         *, today: date) -> list[ledger.Recorded]:
    """One task per renewal, through the ledger. Idempotent on the document:
    source key `artifact:<sha256>`, the same one ingest uses."""
    out = []
    for r in renewals:
        days = (r.renewal_date - today).days
        out.append(ledger.record_event(
            conn, source_key=f"artifact:{r.doc.sha256}", source=r.doc.source,
            source_link=r.doc.source_ref, artifact_id=r.doc.id,
            title=r.title, event_key=r.event_key, confidence=0.6,
            status="DETECTED", by="reconcile",
            due_date=r.renewal_date.isoformat(), due_date_verified=0,
            entity_id=r.doc.entity_id, domain=r.doc.domain,
            urgency="HIGH" if days <= 14 else "NORMAL",
            requested_outcome="Renew, or decide not to, before the date",
            detail=(f"Renewal date read from the document: \"{r.phrase}\". "
                    f"Provisional until checked against the policy (OCR is "
                    f"not authoritative).")))
    return out


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Counts:
    new: int = 0
    seen: int = 0
    reopened: int = 0
    resolved: int = 0


def record_findings(conn: sqlite3.Connection, findings: Iterable[Finding], *,
                    checked: Iterable[str]) -> Counts:
    """Upsert findings by key; resolve open ones of the `checked` kinds that
    this run did not find.

    Only kinds that were actually checked are resolved: a run that could not
    check unanswered mail must not declare yesterday's unanswered mail answered.
    An ACKNOWLEDGED finding stays acknowledged when found again — the human's
    word stands until the facts change enough to change the key.
    """
    now = db.now_iso()
    new = seen = reopened = resolved = 0
    keys = set()
    for f in findings:
        if f.key in keys:
            continue
        keys.add(f.key)
        conf = None if f.confidence is None else min(f.confidence, CEILING)
        row = conn.execute("SELECT id, status FROM recon_findings WHERE finding_key = ?",
                           (f.key,)).fetchone()
        if row is None:
            fid = conn.execute(
                "INSERT INTO recon_findings (finding_key, kind, entity_id, task_id, "
                "artifact_ids, summary, detail, confidence, status, first_seen_at, "
                "last_seen_at, status_changed_at) VALUES (?,?,?,?,?,?,?,?,'OPEN',?,?,?)",
                (f.key, f.kind, f.entity_id, f.task_id, json.dumps(list(f.artifact_ids)),
                 f.summary, f.detail, conf, now, now, now)).lastrowid
            db.log_action(conn, "RECON_FINDING",
                          artifact_id=f.artifact_ids[0] if f.artifact_ids else None,
                          detail=f"#{fid} {f.kind}: {f.summary}"[:400])
            new += 1
            continue
        status = row["status"]
        if status == "RESOLVED":
            status = "OPEN"
            reopened += 1
            db.log_action(conn, "RECON_FINDING_REOPENED",
                          detail=f"#{row['id']} {f.kind}: {f.summary}"[:400])
        else:
            seen += 1
        conn.execute(
            "UPDATE recon_findings SET summary = ?, detail = ?, confidence = ?, "
            "task_id = ?, artifact_ids = ?, last_seen_at = ?, status = ?, "
            "status_changed_at = CASE WHEN status = ? THEN status_changed_at ELSE ? END "
            "WHERE id = ?",
            (f.summary, f.detail, conf, f.task_id, json.dumps(list(f.artifact_ids)),
             now, status, status, now, row["id"]))

    checked = sorted(set(checked))
    if checked:
        marks = ",".join("?" for _ in checked)
        for row in conn.execute(
                f"SELECT id, finding_key, kind, summary FROM recon_findings "
                f"WHERE status = 'OPEN' AND kind IN ({marks})", checked).fetchall():
            if row["finding_key"] in keys:
                continue
            conn.execute("UPDATE recon_findings SET status = 'RESOLVED', "
                         "status_changed_at = ? WHERE id = ?", (now, row["id"]))
            db.log_action(conn, "RECON_FINDING_RESOLVED",
                          detail=f"#{row['id']} {row['kind']} no longer found: "
                                 f"{row['summary']}"[:400])
            resolved += 1
    return Counts(new=new, seen=seen, reopened=reopened, resolved=resolved)


def record_links(conn: sqlite3.Connection, links: Iterable[Link]) -> int:
    """Upsert candidate links; returns how many are new. A human's CONFIRMED or
    REJECTED is never overwritten; a candidate's confidence and basis follow
    the latest run."""
    now = db.now_iso()
    added = 0
    for l in links:
        row = conn.execute("SELECT id, status FROM recon_links WHERE link_key = ?",
                           (l.key,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO recon_links (link_key, kind, from_artifact, to_artifact, "
                "task_id, amount_cents, confidence, basis, first_seen_at, last_seen_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (l.key, l.kind, l.from_artifact, l.to_artifact, l.task_id,
                 l.amount_cents, min(l.confidence, CEILING), l.basis, now, now))
            added += 1
        elif row["status"] == "CANDIDATE":
            conn.execute("UPDATE recon_links SET confidence = ?, basis = ?, "
                         "task_id = ?, last_seen_at = ? WHERE id = ?",
                         (min(l.confidence, CEILING), l.basis, l.task_id, now, row["id"]))
        else:
            conn.execute("UPDATE recon_links SET last_seen_at = ? WHERE id = ?",
                         (now, row["id"]))
    return added


# ---------------------------------------------------------------------------
# Reading back, and the human's side
# ---------------------------------------------------------------------------

_KIND_ORDER = "CASE kind " + " ".join(
    f"WHEN '{k}' THEN {i}" for i, k in enumerate(KINDS)) + f" ELSE {len(KINDS)} END"


def open_findings(conn: sqlite3.Connection, *, entity_id: str | None = None,
                  include_acknowledged: bool = False) -> list[sqlite3.Row]:
    """Open findings, most expensive kind first. What the brief should read."""
    states = ("OPEN", "ACKNOWLEDGED") if include_acknowledged else ("OPEN",)
    sql = (f"SELECT * FROM recon_findings WHERE status IN "
           f"({','.join('?' for _ in states)})")
    args: list = list(states)
    if entity_id:
        sql += " AND entity_id = ?"
        args.append(entity_id)
    sql += f" ORDER BY {_KIND_ORDER}, first_seen_at, id"
    return conn.execute(sql, args).fetchall()


def brief_lines(conn: sqlite3.Connection, *, limit: int = 5) -> list[str]:
    """The brief's "check before paying" section, phone-shaped: one line per
    finding, most expensive first, and a count of the rest. [] when clear —
    the brief prints nothing for an empty section (as for stalled jobs)."""
    rows = open_findings(conn)
    lines = [f"[{r['id']}] {r['summary']}" for r in rows[:limit]]
    if len(rows) > limit:
        lines.append(f"+{len(rows) - limit} more: ./ops/reconcile.py --list")
    return lines


def acknowledge(conn: sqlite3.Connection, finding_id: int, *, by: str = "human",
                note: str | None = None) -> sqlite3.Row:
    """"Seen; nothing to do." Stays acknowledged while the facts are the same."""
    row = conn.execute("SELECT * FROM recon_findings WHERE id = ?",
                       (finding_id,)).fetchone()
    if row is None:
        raise LookupError(f"no finding {finding_id}. "
                          f"`./ops/reconcile.py --list` shows the ids.")
    if row["status"] != "ACKNOWLEDGED":
        conn.execute("UPDATE recon_findings SET status = 'ACKNOWLEDGED', "
                     "status_changed_at = ?, acknowledged_by = ?, note = ? WHERE id = ?",
                     (db.now_iso(), by, note, finding_id))
        db.log_action(conn, "RECON_FINDING_ACKNOWLEDGED",
                      detail=f"[{by}] #{finding_id} {row['kind']}"
                             + (f" — {note}" if note else ""))
    return conn.execute("SELECT * FROM recon_findings WHERE id = ?",
                        (finding_id,)).fetchone()


def decide_link(conn: sqlite3.Connection, link_id: int, status: str, *,
                by: str = "human") -> sqlite3.Row:
    """A human confirms or rejects a candidate link. Only a human: a link is
    inferred from OCR, and nothing that inferred it may promote it."""
    if status not in ("CONFIRMED", "REJECTED"):
        raise ValueError("status must be CONFIRMED or REJECTED")
    if not ledger.is_human(by):
        raise PermissionError(f"{by} is not a human; only a human confirms a link")
    row = conn.execute("SELECT * FROM recon_links WHERE id = ?", (link_id,)).fetchone()
    if row is None:
        raise LookupError(f"no link {link_id}. `./ops/reconcile.py --links` shows the ids.")
    conn.execute("UPDATE recon_links SET status = ?, decided_by = ?, decided_at = ? "
                 "WHERE id = ?", (status, by, db.now_iso(), link_id))
    db.log_action(conn, f"RECON_LINK_{status}", artifact_id=row["from_artifact"],
                  detail=f"[{by}] link #{link_id} {row['kind']} "
                         f"{row['from_artifact']} -> {row['to_artifact']}")
    return conn.execute("SELECT * FROM recon_links WHERE id = ?", (link_id,)).fetchone()


# ---------------------------------------------------------------------------
# One pass
# ---------------------------------------------------------------------------

@dataclass
class Report:
    today: date
    findings: list[Finding]
    links: list[Link]
    renewals: list[Renewal]
    created: list[ledger.Recorded] = field(default_factory=list)
    replies_checked: bool = False
    awaiting_reply: int = 0
    recorded: Counts | None = None
    new_links: int = 0


def _today() -> date:
    return datetime.now(db.TZ).date()


def run(conn: sqlite3.Connection, *, today: date | None = None,
        grace_days: int = GRACE_DAYS, lead_days: int = RENEWAL_LEAD_DAYS,
        replies: ReplyIndex | None = None, record: bool = True) -> Report:
    """Every check, once. With `record=False` nothing is written at all."""
    today = today or _today()
    docs = load_docs(conn)
    tasks = task_index(conn)
    obs = obligations(docs, tasks)

    pay_links, pay_findings = payment_links(docs, obs, tasks)
    links = pay_links + statement_links(docs, obs, tasks)

    findings = (invoice_findings(obs, tasks) + pay_findings
                + ledger_received_again(conn, docs, tasks)
                + paid_then_received_again(obs, pay_links, docs, tasks)
                + missing_statements(docs, today=today, grace_days=grace_days))
    unanswered, waiting = unanswered_mail(docs, tasks, today=today, replies=replies)
    findings += unanswered

    # One finding per key; the first route to it wins. The ledger's view and
    # the documents' view of a resend often agree, and that is one finding.
    unique: dict[str, Finding] = {}
    for f in findings:
        unique.setdefault(f.key, f)
    findings = sorted(unique.values(),
                      key=lambda f: (KINDS.index(f.kind), f.key))

    renewals = renewals_due(docs, tasks, conn, today=today, lead_days=lead_days)
    report = Report(today=today, findings=findings, links=links, renewals=renewals,
                    replies_checked=replies is not None, awaiting_reply=waiting)
    if not record:
        return report

    checked = [k for k in KINDS if k != "UNANSWERED_MAIL" or replies is not None]
    with ledger._atomic(conn, "reconcile"):
        report.recorded = record_findings(conn, findings, checked=checked)
        report.new_links = record_links(conn, links)
        report.created = create_renewal_tasks(conn, renewals, today=today)
        r = report.recorded
        made = sum(1 for c in report.created if c.outcome == "created")
        mail = ("checked" if replies is not None
                else "not checked (no sent-mail source)")
        db.log_action(conn, "RECON_COMPLETED", detail=(
            f"{len(findings)} findings ({r.new} new, {r.resolved} resolved), "
            f"{len(links)} links, {made} renewal tasks, unanswered mail {mail}"))
    return report
