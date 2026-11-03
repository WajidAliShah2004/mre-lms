"""Near-duplicate candidates (R25; addendum §4, demonstration scenarios 5 and 6).

The cases that matter are the two the addendum names: the same statement
arriving twice in different forms must be FOUND, and two monthly statements
from the same bank — mostly the same words — must NOT be called duplicates.
Getting the second one wrong is how a month of statements goes missing.
"""

import random
import re
from pathlib import Path

from core.db import database as db
from core.pipeline import neardup as nd
from core.pipeline.neardup import Doc

CORE = Path(__file__).resolve().parents[1] / "core"


def statement(period, opening, closing, txns, account="4521"):
    """A bank statement: a long shared template and a few facts that differ."""
    start, end = period
    lines = [
        "FIRST HARBOR SAVINGS BANK",
        "Member FDIC. Equal Housing Lender.",
        f"Statement Period: {start} through {end}",
        f"Account ending in {account}",
        "Matthew R. Epstein, 12 Harbor Lane, Oyster Bay NY 11771",
        f"Opening balance ${opening}",
        f"Closing balance ${closing}",
        "Transactions",
    ]
    lines += [f"{d}  {payee}  ${amt}" for d, payee, amt in txns]
    lines += [
        "Important information about your account. Please examine this statement "
        "carefully and report any discrepancy within sixty days of the statement "
        "date. In case of errors or questions about your electronic transfers, "
        "telephone us at the number on the front of this statement or write to us "
        "at the address shown. We must hear from you no later than sixty days after "
        "we sent the first statement on which the problem or error appeared. Tell "
        "us your name and account number, describe the error or the transfer you "
        "are unsure about, and explain as clearly as you can why you believe it is "
        "an error or why you need more information. Tell us the dollar amount of "
        "the suspected error. We will investigate your complaint and will correct "
        "any error promptly. Overdraft fees and returned item fees are listed in "
        "the schedule of fees provided when the account was opened. Deposits are "
        "insured by the FDIC up to the maximum amount allowed by law.",
    ]
    return "\n".join(lines)


MARCH = statement(("03/01/2026", "03/31/2026"), "1,204.33", "987.10", [
    ("03/04/2026", "PSEG LONG ISLAND", "142.18"),
    ("03/11/2026", "VERIZON WIRELESS", "75.05"),
])
APRIL = statement(("04/01/2026", "04/30/2026"), "987.10", "1,433.92", [
    ("04/03/2026", "PSEG LONG ISLAND", "131.40"),
    ("04/12/2026", "PAYROLL DEPOSIT", "620.00"),
])


def kinds(result):
    return [p.kind for p in result.pairs]


# ---------------------------------------------------------------------------
# The two demonstration scenarios
# ---------------------------------------------------------------------------

def test_two_monthly_statements_are_related_versions_not_duplicates():
    """Scenario 6. Same bank, same template, different month: keep both."""
    res = nd.find_candidates([Doc(1, MARCH, "march.pdf"), Doc(2, APRIL, "april.pdf")])
    assert kinds(res) == [nd.RELATED_VERSION]
    pair = res.pairs[0]
    assert "different statement period" in pair.reason
    assert "2026-03-31" in pair.reason and "2026-04-30" in pair.reason
    assert "different amounts" in pair.reason
    assert "keep both" in pair.reason
    assert not res.of_kind(nd.NEAR_DUPLICATE)


def test_the_same_statement_as_pdf_text_and_as_email_text_is_a_candidate(tmp_path):
    """Scenario 5's text form: one statement, two renderings, different bytes.

    Exercised through load_docs, so the email's header block is stripped the
    way it is for real filed mail, and the PDF's text comes from its sidecar.
    """
    conn = db.connect(tmp_path / "lms.db")
    pdf = tmp_path / "archive" / "2026-03-31__stmt.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4 not really a pdf")
    # A text layer wraps lines differently from an email body. Shingles must
    # not care.
    pdf.with_name(pdf.name + ".txt").write_text(
        re.sub(r"\s+", "\n", MARCH), encoding="utf-8")
    eml = tmp_path / "archive" / "2026-04-02__m1.eml.txt"
    eml.write_text(
        "From: First Harbor <statements@firstharbor.example>\n"
        "To: matthew@mrecai.com\n"
        "Date: 2026-04-02T09:00:00-04:00\n"
        "Subject: Your March statement\n"
        "Message-ID: <abc@firstharbor.example>\n\n"
        "Hello Matthew, your statement is below.\n\n" + MARCH, encoding="utf-8")
    for n, (path, source) in enumerate([(pdf, "attachment"), (eml, "email")]):
        db.insert_artifact(conn, sha256=f"{n:064x}", source=source, source_ref=f"r{n}",
                           byte_size=path.stat().st_size, filed_path=str(path),
                           status="FILED")

    docs = nd.load_docs(conn)
    assert not docs[1].text.startswith("From:"), "the header block was compared"
    res = nd.find_candidates(docs)
    assert kinds(res) == [nd.NEAR_DUPLICATE]
    p = res.pairs[0]
    assert p.containment >= nd.NEAR_DUP_CONTAINMENT
    assert "nothing merged" in p.reason


def test_an_exact_text_copy_with_different_bytes_is_a_candidate():
    """The same statement downloaded twice: PDF bytes differ, text does not."""
    res = nd.find_candidates([Doc(1, MARCH), Doc(2, MARCH)])
    assert kinds(res) == [nd.NEAR_DUPLICATE]
    assert res.pairs[0].jaccard == 1.0


# ---------------------------------------------------------------------------
# Distinct documents the addendum says to retain
# ---------------------------------------------------------------------------

CONTRACT = (
    "SERVICES AGREEMENT between MRE Consulting Inc and Atlase AI Inc. The "
    "consultant will provide advisory services described in Schedule A for a "
    "term of twelve months beginning on the effective date. Either party may "
    "terminate this agreement on thirty days written notice. Fees are payable "
    "within thirty days of invoice. This agreement is governed by the laws of "
    "the State of New York and is the entire agreement between the parties. "
    "IN WITNESS WHEREOF the parties have executed this agreement.\n")


def test_a_signed_and_an_unsigned_copy_are_kept_apart():
    signed = CONTRACT + "/s/ Matthew Epstein\nDate: 2026-05-01"
    unsigned = CONTRACT + "Signature: ____________\nDate: 2026-05-01"
    res = nd.find_candidates([Doc(1, signed), Doc(2, unsigned)])
    assert kinds(res) == [nd.RELATED_VERSION]
    assert "signed vs unsigned" in res.pairs[0].reason


def test_copies_signed_by_different_people_are_kept_apart():
    a = CONTRACT + "/s/ Matthew Epstein"
    b = CONTRACT + "/s/ Laura Epstein"
    res = nd.find_candidates([Doc(1, a), Doc(2, b)])
    assert kinds(res) == [nd.RELATED_VERSION]
    assert "signed by different people" in res.pairs[0].reason


def test_an_amended_copy_is_a_different_version():
    res = nd.find_candidates([Doc(1, CONTRACT), Doc(2, "AMENDED\n" + CONTRACT)])
    assert kinds(res) == [nd.RELATED_VERSION]
    assert "version" in res.pairs[0].reason


def test_the_same_month_for_two_accounts_is_not_a_duplicate():
    """Same template, same dates, same zero balances — two different cards."""
    a = statement(("03/01/2026", "03/31/2026"), "0.00", "0.00", [], account="4521")
    b = statement(("03/01/2026", "03/31/2026"), "0.00", "0.00", [], account="9907")
    res = nd.find_candidates([Doc(1, a), Doc(2, b)])
    assert kinds(res) == [nd.RELATED_VERSION]
    assert "different accounts" in res.pairs[0].reason


def test_an_email_that_quotes_a_statement_and_adds_a_date_is_not_a_conflict():
    """Containment is not disagreement: one side having MORE facts is fine."""
    wrapped = "Sent 04/02/2026. Your statement is below.\n" + MARCH
    res = nd.find_candidates([Doc(1, MARCH), Doc(2, wrapped)])
    assert kinds(res) == [nd.NEAR_DUPLICATE]


# ---------------------------------------------------------------------------
# What is not compared, and why
# ---------------------------------------------------------------------------

def test_unrelated_documents_produce_no_pair():
    res = nd.find_candidates([Doc(1, MARCH), Doc(2, CONTRACT)])
    assert res.pairs == []
    assert len(res.compared) == 2


def test_short_texts_are_not_compared():
    res = nd.find_candidates([Doc(1, "Thanks, got it."), Doc(2, "Thanks, got it.")])
    assert res.pairs == []
    assert [d.key for d in res.too_short] == [1, 2]


def test_documents_without_text_are_listed_not_dropped():
    res = nd.find_candidates([Doc(1, None, "IMG_0001.HEIC"), Doc(2, MARCH)])
    assert [d.key for d in res.no_text] == [1]
    assert res.visual is None


class FakeVisual:
    name = "fake"

    def __init__(self, scores):
        self.scores = scores

    def similarity(self, a, b):
        return self.scores.get(frozenset((a.name, b.name)))


def test_the_visual_hook_compares_text_less_scans():
    docs = [Doc(1, None, path=Path("a.jpg")), Doc(2, None, path=Path("b.jpg")),
            Doc(3, None, path=Path("c.jpg"))]
    res = nd.find_candidates(docs, visual=FakeVisual(
        {frozenset(("a.jpg", "b.jpg")): 0.99, frozenset(("a.jpg", "c.jpg")): 0.40}))
    assert [(p.a.key, p.b.key, p.kind) for p in res.pairs] == [(1, 2, nd.NEAR_DUPLICATE)]
    assert "could not be compared" in res.pairs[0].reason
    assert res.visual == "fake"


def test_matching_text_that_looks_different_is_an_annotated_copy():
    """An annotated scan OCRs to the same words. The pages are not the same."""
    docs = [Doc(1, MARCH, path=Path("clean.pdf")), Doc(2, MARCH, path=Path("notes.pdf"))]
    res = nd.find_candidates(docs, visual=FakeVisual(
        {frozenset(("clean.pdf", "notes.pdf")): 0.60}))
    assert kinds(res) == [nd.RELATED_VERSION]
    assert "look different" in res.pairs[0].reason


# ---------------------------------------------------------------------------
# The candidate search is exact
# ---------------------------------------------------------------------------

def test_prefix_filtering_misses_no_pair_above_the_threshold():
    """Brute force on random overlapping sets: every pair whose containment is
    at least the threshold must be among the candidates."""
    rng = random.Random(7)
    base = [set(rng.sample(range(5000), 60)) for _ in range(8)]
    sets = []
    for _ in range(120):
        s = set(rng.choice(base))
        drop = rng.randint(0, 45)
        s -= set(rng.sample(sorted(s), drop))
        s |= set(rng.sample(range(5000, 9000), rng.randint(0, 40)))
        sets.append(frozenset(s))
    cands, truncated = nd.candidate_pairs(sets, 0.5)
    assert not truncated
    expected = {(i, j) for i in range(len(sets)) for j in range(i + 1, len(sets))
                if sets[i] and sets[j] and
                len(sets[i] & sets[j]) >= 0.5 * min(len(sets[i]), len(sets[j]))}
    assert expected, "the fixture produced no qualifying pair"
    assert expected <= cands


# ---------------------------------------------------------------------------
# Candidates only
# ---------------------------------------------------------------------------

def test_neardup_has_no_write_path():
    """'Only as candidates' (addendum §4). The module that finds near
    duplicates must not be able to merge, mark or hide one."""
    src = (CORE / "pipeline" / "neardup.py").read_text(encoding="utf-8")
    code = re.sub(r'"""[\s\S]*?"""', "", src)
    for verb in ("INSERT", "UPDATE", "DELETE", "write_text", "write_bytes",
                 "set_artifact_status", "log_action"):
        assert verb not in code, f"neardup.py contains {verb}"


def test_facts_are_read_from_the_common_date_and_amount_forms():
    f = nd.extract_facts(
        "Statement period March 1, 2026 to 31 March 2026\n"
        "Paid $1,234.56 and $40 on 2026-03-15; card ending 7781; invoice # INV-0042")
    assert f.period_dates == {"2026-03-01", "2026-03-31"}
    assert "2026-03-15" in f.dates
    assert f.amounts == {123456, 4000}
    assert f.accounts == {"7781"}
    assert f.invoices == {"INV-0042"}


def test_a_period_label_on_its_own_line_applies_to_the_next():
    """A PDF text layer often splits 'Statement Period' from its dates."""
    f = nd.extract_facts("Statement Period\n04/01/2026 - 04/30/2026\nPaid 04/15/2026")
    assert f.period_dates == {"2026-04-01", "2026-04-30"}
