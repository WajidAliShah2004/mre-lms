"""Reconciliation (R26, addendum §4): links, duplicate invoices, a paid bill
received again, missing statements, renewal tasks, unanswered mail.

The fixtures are the shapes the Mac actually holds: the PSG invoice that
arrived as an email body and again as its PDF attachment, Bluevine's monthly
"online statement ready" alerts, a Bank of America statement with transaction
lines. Where the pipeline can produce the fixture (ingest_file with a fake
model) it does; the rest are rows written the way filing writes them.

Addendum demonstration scenarios held here: (4) a previously paid bill
received again; (6) two similar monthly statements.
"""

import json
import os
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from core.adapters.lmstudio import Completion
from core.db import database as db
from core.pipeline import filing, ledger, reconcile
from core.pipeline import tasks as task_ops
from core.pipeline.classify import Artifact, Classifier
from core.pipeline.ingest import ingest_file
from core.pipeline.registry import load_registry

CONFIG = Path(__file__).resolve().parents[1] / "config"
OPS = Path(__file__).resolve().parents[1] / "ops"

PSG = "psg-global-tax-and-accounting"


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "lms.db")
    yield c
    c.close()


def at(day: str, hour: int = 9) -> str:
    """An ingested_at as now_iso() writes it: local time with an offset."""
    return f"{day}T{hour:02d}:00:00-04:00"


class Archive:
    """Filed documents, written the way filing.file_artifact leaves them: the
    file in the tree, an OCR sidecar beside anything that is not text, an
    artifacts row and a classifications row."""

    def __init__(self, conn, root: Path):
        self.conn, self.root, self.n = conn, root, 0
        root.mkdir(parents=True, exist_ok=True)

    def put(self, *, cp, descriptor, doc_date, text="", ocr=None, amount=None,
            due=None, entity="B_MRE", category="VENDORS",
            subcategory="invoices-received", source="email", source_ref=None,
            parent=None, ingested=None, requires_reply=False, ext=".txt",
            domain="BUSINESS"):
        self.n += 1
        sha = db.sha256_bytes(f"{self.n}|{descriptor}|{text}|{ocr}".encode())
        name = (f"{doc_date}__{entity[2:]}__{category}__{cp}__{descriptor}__"
                f"{'NOAMT' if amount is None else f'USD{amount // 100}-{amount % 100:02d}'}"
                f"__{sha[:8]}{ext}")
        path = self.root / name
        if ext == ".txt":
            path.write_text(text, encoding="utf-8")
        else:
            path.write_bytes(b"%PDF-1.4 not really")
            if ocr is not None:
                path.with_name(path.name + ".txt").write_text(ocr, encoding="utf-8")
        source_ref = source_ref or f"msg-{self.n}"
        # What arrived, as mail.py names it — not the filed name.
        original = (f"{source_ref}.eml.txt" if ext == ".txt"
                    else f"{source_ref.split('/')[0]}__{descriptor.title()}{ext}")
        aid = db.insert_artifact(
            self.conn, sha256=sha, source=source,
            source_ref=source_ref, parent_id=parent,
            original_name=original, doc_date=doc_date,
            ingested_at=ingested or at(doc_date), filed_path=str(path),
            status="FILED")
        db.insert_classification(
            self.conn, artifact_id=aid, domain=domain, entity_id=entity,
            category=category, subcategory=subcategory, urgency="NORMAL",
            confidence=0.95, requires_reply=int(requires_reply), due_date=due,
            counterparty=cp, descriptor=descriptor, amount_cents=amount,
            currency="USD", rationale="fixture", model="fake",
            prompt_hash="fixture", decided_by="rule")
        return aid

    def task(self, aid, *, title="Invoice — PSG", key=None, status="TRIAGED",
             entity="B_MRE", due=None):
        sha = self.conn.execute("SELECT sha256 FROM artifacts WHERE id = ?",
                                (aid,)).fetchone()[0]
        return ledger.record_event(
            self.conn, source_key=f"artifact:{sha}", source="email",
            artifact_id=aid, title=title, event_key=key, status=status,
            entity_id=entity, due_date=due).task_id


def close(conn, task_id, on: str):
    """Done, as done.py records it — with the close dated `on`, because the
    fixtures' documents are dated in the past and now_iso() is today."""
    task_ops.close_task(conn, task_id, note="paid")
    conn.execute("UPDATE tasks SET completed_at = ? WHERE id = ?", (at(on, 17), task_id))


def kinds(report):
    return sorted(f.kind for f in report.findings)


@pytest.fixture
def archive(conn, tmp_path):
    return Archive(conn, tmp_path / "archive")


def psg_email(n=1, *, extra=""):
    return (f"From: PSG Global Tax and Accounting <billing@psgglobal.example>\n"
            f"To: matthew@mrecai.com\n"
            f"Date: Sat, 19 Sep 2026 10:12:00 -0400\n"
            f"Subject: Invoice INV17 - pending payment\n"
            f"Message-ID: <inv17-{n}@psgglobal.example>\n"
            f"Attachments: Tax_Filing_Invoice_INV17.pdf\n\n"
            f"Hi Matthew,\n\nPlease find attached invoice INV17 for the 2025 "
            f"tax filing. Amount due: $550.00 by October 3, 2026.{extra}\n")


PSG_PDF_OCR = ("PSG GLOBAL TAX AND ACCOUNTING\nINVOICE\nInvoice No: INV-0017\n"
               "Invoice Date: 09/19/2026\nBill to: MRECAI\n"
               "2025 Tax filing preparation ........ 550.00\nTOTAL DUE  $550.00\n"
               "Thank you for your payment.\n")


# ---------------------------------------------------------------------------
# Reading facts out of text
# ---------------------------------------------------------------------------

def test_the_email_and_the_pdf_read_the_same_invoice_number():
    """"invoice inv17" in the descriptor and "Invoice No: INV-0017" in the
    PDF's OCR must be the same string, or the pair is two obligations."""
    email = reconcile.invoice_number("invoice inv17 pending payment")
    pdf = reconcile.invoice_number("tax filing invoice inv17", PSG_PDF_OCR)
    ocr_only = reconcile.invoice_number(PSG_PDF_OCR)
    assert email == pdf == ocr_only == "17"


def test_a_year_or_a_date_is_not_an_invoice_number():
    """Every invoice of 2026 sharing the "number" 2026 would merge them all."""
    assert reconcile.invoice_number("Invoice 2026 for services") is None
    assert reconcile.invoice_number("Invoice 09/19/2026") is None
    assert reconcile.invoice_number("Invoice Date: 09/19/2026") is None
    assert reconcile.invoice_number("Invoice #2026-0451") == "20260451"
    # The filed name's amount field, and an amount in prose.
    assert reconcile.invoice_number("compliance reminder invoice USD50 00") is None
    assert reconcile.invoice_number("Invoice 550.00 due Friday") is None


def test_a_payment_can_name_several_invoices():
    assert reconcile.invoice_numbers("Payment for invoices INV17 and INV-18") == ("17", "18")


def test_account_endings_period_and_renewal_dates():
    assert reconcile.account_ending("Bluevine checking account ending in 4321") == "4321"
    assert reconcile.account_ending("Card ****9876 statement") == "9876"
    assert reconcile.account_ending("account number 123456789") is None
    assert reconcile.period("Statement period: 08/01/2026 - 08/31/2026") == "2026-08"
    assert reconcile.period("for the period ending Sept 30, 2026") == "2026-09"
    assert reconcile.period("Your August 2026 statement is ready") == "2026-08"
    assert reconcile.renewal_date("Policy period: 12/01/2025 to 12/01/2026")[0] == date(2026, 12, 1)
    assert reconcile.renewal_date("Your policy renews on December 1, 2026.")[0] == date(2026, 12, 1)
    assert reconcile.renewal_date("Thanks for being a customer") is None


def test_kind_comes_from_the_headline_not_the_footer():
    """Every invoice footer says "thank you for your payment". If the body
    decided, every bill would be its own receipt."""
    assert reconcile.doc_kind("invoice inv17 pending payment", None, 55000, None) == "BILL"
    assert reconcile.doc_kind("payment received invoice inv17", None, 55000, None) == "PAYMENT"
    assert reconcile.doc_kind("online statement ready", None, None, None) == "STATEMENT"
    assert reconcile.doc_kind("policy renewal notice", None, None, None) == "RENEWAL"
    assert reconcile.doc_kind("letter", "invoices-received", None, None) == "BILL"


def test_counterparty_matching_allows_a_shorter_name_but_not_a_shared_word():
    assert reconcile.counterparties_match("psg-global", PSG)
    assert reconcile.counterparties_match("bluevine-inc", "bluevine")
    assert not reconcile.counterparties_match("bank-of-america", "america-first-credit-union")


def test_the_after_close_marker_is_what_the_ledger_writes(conn):
    """reconcile reads the ledger's EVIDENCE event to know evidence arrived
    after close. If ledger.py rewords it, this fails rather than scenario 4
    going quiet."""
    first = ledger.record_event(conn, source_key="a:1", source="email", title="x",
                                event_key="k", status="TRIAGED")
    task_ops.close_task(conn, first.task_id)
    ledger.record_event(conn, source_key="a:2", source="email", title="x",
                        event_key="k", status="TRIAGED")
    details = [r["detail"] for r in ledger.history(conn, first.task_id)
               if r["event"] == "EVIDENCE"]
    assert any(reconcile.AFTER_CLOSE_MARK in d for d in details)


# ---------------------------------------------------------------------------
# The PSG pair, through the real pipeline
# ---------------------------------------------------------------------------

@pytest.fixture
def registry():
    return load_registry(CONFIG)


@pytest.fixture
def roots(tmp_path):
    r = filing.StorageRoots(archive=tmp_path / "filed", originals=tmp_path / "_originals",
                            quarantine=tmp_path / "quarantine")
    r.ensure()
    return r


class FakeModel:
    def __init__(self, response):
        self.response = response

    def complete(self, **kw):
        return Completion(text=json.dumps(self.response), model="fake",
                          elapsed_s=0.01, finish_reason="stop")


def psg_answer(**kw):
    base = {"domain": "BUSINESS", "entity_id": "B_MRE", "category": "VENDORS",
            "subcategory": "invoices-received", "urgency": "NORMAL",
            "confidence": 0.9, "requires_reply": False, "due_date": "2026-10-03",
            "doc_date": "2026-09-19", "counterparty": PSG,
            "descriptor": "invoice inv17 pending payment", "amount_cents": 55000,
            "rationale": "invoice with a due date"}
    return {**base, **kw}


def ingest_psg(conn, roots, registry, tmp_path, *, n=1, pdf_answer=None,
               email_answer=None, extra=""):
    """The email body, then its PDF attachment, as mail.ingest_message does."""
    email = tmp_path / f"inv17-{n}.eml.txt"
    email.write_text(psg_email(n, extra=extra), encoding="utf-8")
    e = ingest_file(conn, roots, registry,
                    Classifier(registry, client=FakeModel(email_answer or psg_answer())),
                    email, source="email", source_ref=f"inv17-{n}",
                    artifact=Artifact(recipient="matthew@mrecai.com",
                                      subject="Invoice INV17 - pending payment"))
    assert e.status == "FILED", e.reason
    parent = db.find_artifact_by_hash(conn, e.sha256)["id"]
    pdf = tmp_path / f"Tax_Filing_Invoice_INV17-{n}.pdf"
    pdf.write_bytes(f"%PDF-1.4 PSG invoice copy {n}".encode())
    p = ingest_file(conn, roots, registry,
                    Classifier(registry, client=FakeModel(pdf_answer or psg_answer(
                        descriptor="tax filing invoice inv17"))),
                    pdf, source="attachment", source_ref=f"inv17-{n}/att1",
                    artifact=Artifact(recipient="matthew@mrecai.com",
                                      subject="Tax_Filing_Invoice_INV17.pdf",
                                      body=PSG_PDF_OCR),
                    parent_id=parent, run_ocr=False)
    assert p.status == "FILED", p.reason
    return e, p


def test_psg_email_and_its_pdf_are_one_obligation(conn, roots, registry, tmp_path):
    """The real filed pair: `...invoice-inv17-pending-payment__USD550-00__...txt`
    and `...tax-filing-invoice-inv17__USD550-00__...pdf`. One bill, delivered
    twice. Not a duplicate invoice, and nothing to report."""
    e, p = ingest_psg(conn, roots, registry, tmp_path)
    assert e.task_id == p.task_id                    # the ledger agrees (D-066)

    docs = reconcile.load_docs(conn)
    obs = reconcile.obligations(docs, reconcile.task_index(conn))
    assert len(obs) == 1 and obs[0].invoice_no == "17" and len(obs[0].docs) == 2

    report = reconcile.run(conn, today=date(2026, 9, 25))
    assert report.findings == []


def test_the_same_invoice_on_two_open_tasks_is_a_duplicate(conn, roots, registry, tmp_path):
    """The PDF read a different due date, so its event key differed and ingest
    made a second task. The invoice number says it is one bill."""
    e, p = ingest_psg(conn, roots, registry, tmp_path,
                      pdf_answer=psg_answer(descriptor="tax filing invoice inv17",
                                            counterparty="psg-global",
                                            due_date="2026-10-19"))
    assert e.task_id != p.task_id

    report = reconcile.run(conn, today=date(2026, 9, 25))
    assert kinds(report) == ["DUPLICATE_INVOICE"]
    f = report.findings[0]
    assert f.task_id == e.task_id
    assert f"./ops/done.py {p.task_id} --duplicate-of {e.task_id}" in f.detail
    # Reported, not acted on: both tasks are still open.
    assert {ledger.get(conn, t)["status"] for t in (e.task_id, p.task_id)} == {"TRIAGED"}


def test_a_resent_copy_before_payment_is_still_one_obligation(conn, roots, registry, tmp_path):
    """PSG chases the unpaid invoice with the same email and PDF. The ledger
    puts the chase on the same task; there is nothing to report."""
    first, _ = ingest_psg(conn, roots, registry, tmp_path, n=1)
    again, _ = ingest_psg(conn, roots, registry, tmp_path, n=2,
                          extra="\n\nFriendly reminder: this is still open.")
    assert first.task_id == again.task_id
    report = reconcile.run(conn, today=date(2026, 9, 30))
    assert report.findings == []


# ---------------------------------------------------------------------------
# Scenario 4: a previously paid bill received again
# ---------------------------------------------------------------------------

def test_paid_bill_received_again_is_a_finding_not_a_new_task(conn, roots, registry, tmp_path):
    first, _ = ingest_psg(conn, roots, registry, tmp_path, n=1)
    task_ops.close_task(conn, first.task_id, note="paid Sept 28")
    tasks_before = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]

    again, _ = ingest_psg(conn, roots, registry, tmp_path, n=2)
    assert again.task_id == first.task_id
    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == tasks_before

    report = reconcile.run(conn, today=date(2026, 10, 15))
    assert kinds(report) == ["RECEIVED_AGAIN"]
    f = report.findings[0]
    assert f.task_id == first.task_id
    assert f.summary.startswith("Paid bill received again")
    assert "second bill, or a resend" in f.summary
    # The email and its PDF arrived together: one finding, both as evidence.
    assert len(f.artifact_ids) == 2
    assert ledger.get(conn, first.task_id)["status"] == "CLOSED"     # not reopened


def test_paid_bill_resent_with_a_new_due_date_is_caught_by_its_number(conn, archive):
    """The resend carries a new due date, so a new event key and a new task.
    The ledger cannot see that it is the same bill; the invoice number can."""
    a = archive.put(cp=PSG, descriptor="invoice-inv17-pending-payment",
                    doc_date="2026-09-19", amount=55000, due="2026-10-03",
                    text=psg_email())
    t1 = archive.task(a, key="v1|B_MRE|psg|due=2026-10-03|amt=55000", due="2026-10-03")
    close(conn, t1, on="2026-09-28")
    b = archive.put(cp=PSG, descriptor="invoice-inv17-past-due",
                    doc_date="2026-10-14", amount=55000, due="2026-10-24",
                    text="Subject: PAST DUE invoice INV17\n\nAmount due $550.00")
    t2 = archive.task(b, key="v1|B_MRE|psg|due=2026-10-24|amt=55000", due="2026-10-24")

    report = reconcile.run(conn, today=date(2026, 10, 15))
    assert kinds(report) == ["RECEIVED_AGAIN"]
    assert report.findings[0].task_id == t1
    assert report.findings[0].artifact_ids == (b,)
    assert ledger.get(conn, t2)["status"] == "TRIAGED"              # his call


def test_an_identical_pdf_in_a_new_message_after_close(conn, archive):
    """Identical bytes never reach the ledger (ingest stops them as DUPLICATE),
    so the sighting in duplicate_refs is the only trace. A re-poll of the SAME
    message is not a resend."""
    a = archive.put(cp=PSG, descriptor="tax-filing-invoice-inv17", ext=".pdf",
                    ocr=PSG_PDF_OCR, doc_date="2026-09-19", amount=55000,
                    source="attachment", source_ref="m1/att1")
    t = archive.task(a)
    close(conn, t, on="2026-09-28")
    sha = conn.execute("SELECT sha256 FROM artifacts WHERE id = ?", (a,)).fetchone()[0]
    for seen, ref in (("2026-09-20T07:00:00-04:00", "m1/att1"),     # re-poll, before close
                      ("2026-10-02T07:00:00-04:00", "m1/att1"),     # re-poll, after close
                      ("2026-10-14T07:00:00-04:00", "m9/att1")):    # a new message
        conn.execute("INSERT INTO duplicate_refs (sha256, seen_at, source, source_ref) "
                     "VALUES (?, ?, 'attachment', ?)", (sha, seen, ref))

    report = reconcile.run(conn, today=date(2026, 10, 15))
    assert kinds(report) == ["RECEIVED_AGAIN"]
    assert "identical copy in message m9" in report.findings[0].detail
    assert "2026-10-14" in report.findings[0].summary


def test_paid_by_receipt_then_billed_again_while_the_task_is_open(conn, archive):
    """Nobody closed the task, but a payment receipt matches the bill — and
    then the bill came again."""
    bill = archive.put(cp=PSG, descriptor="invoice-inv17-pending-payment",
                       doc_date="2026-09-19", amount=55000, text=psg_email())
    archive.put(cp=PSG, descriptor="payment-received-invoice-inv17",
                doc_date="2026-09-25", amount=55000, subcategory="receipts",
                category="VENDORS",
                text="Subject: Payment received - Invoice INV17\n\nWe received $550.00.")
    again = archive.put(cp=PSG, descriptor="invoice-inv17-reminder",
                        doc_date="2026-10-14", amount=55000,
                        text="Subject: Reminder: invoice INV17\n\n$550.00 due")

    report = reconcile.run(conn, today=date(2026, 10, 15))
    assert kinds(report) == ["RECEIVED_AGAIN"]
    f = report.findings[0]
    assert "looks paid" in f.summary and again in f.artifact_ids
    assert bill not in f.artifact_ids


def test_receipt_and_close_both_saying_paid_is_still_one_finding(conn, archive):
    """The task was closed AND a receipt is linked. One resend, one finding —
    the closed task's, since his close is the stronger evidence."""
    bill, tid, pay = psg_bill_and_payment(archive)
    close(conn, tid, on="2026-09-28")
    again = archive.put(cp=PSG, descriptor="invoice-inv17-reminder",
                        doc_date="2026-10-14", amount=55000,
                        text="Subject: Reminder: invoice INV17\n")
    report = reconcile.run(conn, today=date(2026, 10, 15))
    assert kinds(report) == ["RECEIVED_AGAIN"]
    assert report.findings[0].task_id == tid
    assert report.findings[0].artifact_ids == (again,)


# ---------------------------------------------------------------------------
# Duplicate invoices that really are two
# ---------------------------------------------------------------------------

HARTWELL = "hartwell-office-supply"


def test_two_invoices_same_amount_same_month_is_possible_double_billing(conn, archive):
    for n, day in (("INV-2201", "2026-09-02"), ("INV-2207", "2026-09-16")):
        aid = archive.put(cp=HARTWELL, descriptor=f"invoice {n} toner",
                          doc_date=day, amount=124500,
                          text=f"Subject: Invoice {n}\n\nToner, 12 cartridges. Total $1,245.00")
        archive.task(aid, title=f"Invoice {n}", key=f"v1|B_MRE|hartwell|ref={n}")

    report = reconcile.run(conn, today=date(2026, 9, 20))
    assert kinds(report) == ["DOUBLE_BILLING"]
    assert "2201" in report.findings[0].summary and "2207" in report.findings[0].summary


def test_the_same_amount_in_different_months_is_a_recurring_bill(conn, archive):
    for n, day in (("INV-2201", "2026-08-02"), ("INV-2290", "2026-09-02")):
        archive.put(cp=HARTWELL, descriptor=f"invoice {n} toner", doc_date=day,
                    amount=124500, text=f"Subject: Invoice {n}\n")
    assert reconcile.run(conn, today=date(2026, 9, 20)).findings == []


def test_two_invoice_numbers_on_one_task_is_a_wrong_merge(conn, archive):
    """Same due date, same amount, so one event key: the ledger merged two
    bills. Closing that task as paid would leave one unpaid (D-066)."""
    key = "v1|B_MRE|hartwell|due=2026-10-01|amt=124500"
    for n in ("INV-2201", "INV-2207"):
        aid = archive.put(cp=HARTWELL, descriptor=f"invoice {n}", doc_date="2026-09-02",
                          amount=124500, due="2026-10-01", text=f"Subject: Invoice {n}\n")
        tid = archive.task(aid, key=key, due="2026-10-01")
    report = reconcile.run(conn, today=date(2026, 9, 20))
    assert "MERGED_INVOICES" in kinds(report)
    assert next(f for f in report.findings if f.kind == "MERGED_INVOICES").task_id == tid


def test_copies_of_one_invoice_disagreeing_on_the_amount(conn, archive):
    """§4: flag it, do not pick one. OCR read $5,500.00 off the PDF."""
    email = archive.put(cp=PSG, descriptor="invoice-inv17-pending-payment",
                        doc_date="2026-09-19", amount=55000, text=psg_email())
    archive.put(cp=PSG, descriptor="tax-filing-invoice-inv17", ext=".pdf",
                ocr=PSG_PDF_OCR, doc_date="2026-09-19", amount=550000,
                source="attachment", parent=email)
    report = reconcile.run(conn, today=date(2026, 9, 25))
    assert kinds(report) == ["AMOUNT_MISMATCH"]
    assert "$550.00" in report.findings[0].summary
    assert "$5,500.00" in report.findings[0].summary


def test_the_same_invoice_number_in_two_entities_never_joins(conn, archive):
    """Entity separation (§0): ATLASE's INV17 and MRECAI's INV17 are two bills."""
    for ent in ("B_MRE", "B_ATL"):
        aid = archive.put(cp=PSG, descriptor="invoice-inv17", entity=ent,
                          doc_date="2026-09-19", amount=55000, text=psg_email())
        archive.task(aid, entity=ent, key=f"v1|{ent}|psg|ref=17")
    obs = reconcile.obligations(reconcile.load_docs(conn), reconcile.task_index(conn))
    assert len(obs) == 2
    assert reconcile.run(conn, today=date(2026, 9, 25)).findings == []


# ---------------------------------------------------------------------------
# Links: bill <-> proof of payment <-> statement line
# ---------------------------------------------------------------------------

BOA_STATEMENT = """BANK OF AMERICA
Business Advantage checking, account ending 7781
Statement period: 09/01/2026 - 09/30/2026

Date   Description                                  Amount
09/24  PSG GLOBAL TAX ACCOUNTING  ACH DEBIT         -550.00
09/26  HARTWELL OFFICE SUPPLY                       -1,245.00
"""


def psg_bill_and_payment(archive, *, paid=55000):
    bill = archive.put(cp=PSG, descriptor="invoice-inv17-pending-payment",
                       doc_date="2026-09-19", amount=55000, text=psg_email())
    tid = archive.task(bill)
    pay = archive.put(cp=PSG, descriptor="payment-confirmation-invoice-inv17",
                      doc_date="2026-09-24", amount=paid, subcategory="receipts",
                      text="Subject: Payment confirmation\n\nInvoice INV17 paid.")
    return bill, tid, pay


def test_a_payment_receipt_links_to_its_bill_as_a_candidate(conn, archive):
    bill, tid, pay = psg_bill_and_payment(archive)
    report = reconcile.run(conn, today=date(2026, 9, 25))

    (link,) = [l for l in report.links if l.kind == "BILL_PAYMENT"]
    assert (link.from_artifact, link.to_artifact, link.task_id) == (bill, pay, tid)
    assert 0.8 <= link.confidence < 1.0
    assert "invoice 17" in link.basis
    row = conn.execute("SELECT * FROM recon_links WHERE link_key = ?", (link.key,)).fetchone()
    assert row["status"] == "CANDIDATE"
    # Linking is not closing: the task is untouched.
    assert ledger.get(conn, tid)["status"] == "TRIAGED"


def test_only_a_human_confirms_a_link_and_the_schema_refuses_certainty(conn, archive):
    psg_bill_and_payment(archive)
    reconcile.run(conn, today=date(2026, 9, 25))
    link_id = conn.execute("SELECT id FROM recon_links WHERE kind = 'BILL_PAYMENT'").fetchone()[0]

    with pytest.raises(PermissionError):
        reconcile.decide_link(conn, link_id, "CONFIRMED", by="reconcile")
    row = reconcile.decide_link(conn, link_id, "CONFIRMED", by="human")
    assert row["status"] == "CONFIRMED" and row["decided_by"] == "human"

    # A re-run does not demote his answer.
    reconcile.run(conn, today=date(2026, 9, 26))
    assert conn.execute("SELECT status FROM recon_links WHERE id = ?",
                        (link_id,)).fetchone()[0] == "CONFIRMED"

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE recon_links SET confidence = 1.0 WHERE id = ?", (link_id,))


def test_a_payment_for_a_different_amount_is_a_discrepancy_not_a_link(conn, archive):
    psg_bill_and_payment(archive, paid=50000)
    report = reconcile.run(conn, today=date(2026, 9, 25))
    assert not [l for l in report.links if l.kind == "BILL_PAYMENT"]
    assert kinds(report) == ["AMOUNT_MISMATCH"]
    assert "$500.00" in report.findings[0].summary


def test_two_monthly_bills_and_two_payments_pair_by_date(conn, archive):
    """Identical $50 bills and $50 payments: each payment pays one bill."""
    for month in ("08", "09"):
        archive.put(cp="legalzoom", descriptor="compliance-reminder-invoice",
                    doc_date=f"2026-{month}-04", amount=5000, text="Subject: Invoice\n")
        archive.put(cp="legalzoom", descriptor="payment-receipt",
                    doc_date=f"2026-{month}-06", amount=5000, subcategory="receipts",
                    text="Subject: Receipt\n")
    report = reconcile.run(conn, today=date(2026, 9, 20))
    pairs = sorted((l.from_artifact, l.to_artifact) for l in report.links
                   if l.kind == "BILL_PAYMENT")
    assert pairs == [(1, 2), (3, 4)]


def test_a_statement_line_links_the_money_moving(conn, archive):
    bill, _, pay = psg_bill_and_payment(archive)
    statement = archive.put(cp="bank-of-america", descriptor="business-checking-statement",
                            doc_date="2026-10-03", category="FINANCE",
                            subcategory="statements", ext=".pdf", ocr=BOA_STATEMENT,
                            source="attachment")
    report = reconcile.run(conn, today=date(2026, 10, 5))
    to_statement = [l for l in report.links if l.to_artifact == statement]
    assert [l.kind for l in to_statement] == ["PAYMENT_STATEMENT"]
    assert to_statement[0].from_artifact == pay
    assert "PSG GLOBAL" in to_statement[0].basis
    assert to_statement[0].confidence < 1.0


def test_a_statement_alert_with_no_lines_links_to_nothing(conn, archive):
    """"When supported": the "statement ready" alerts have no transactions."""
    psg_bill_and_payment(archive)
    archive.put(cp="bluevine", descriptor="online-statement-ready", category="FINANCE",
                subcategory="statements", doc_date="2026-10-03",
                text="Subject: Your Bluevine online statement is ready\n\n"
                     "Your statement for account ending 4321 is ready. Log in to view it.")
    report = reconcile.run(conn, today=date(2026, 10, 5))
    assert [l.kind for l in report.links] == ["BILL_PAYMENT"]


# ---------------------------------------------------------------------------
# Missing statements; scenario 6
# ---------------------------------------------------------------------------

def bluevine(archive, day, acct="4321", entity="B_MRE"):
    return archive.put(
        cp="bluevine", descriptor="online-statement-ready", category="FINANCE",
        subcategory="statements", doc_date=day, entity=entity,
        text=(f"From: Bluevine <no-reply@bluevine.example>\n"
              f"Subject: Your Bluevine online statement is ready\n\n"
              f"Your monthly statement for Business Checking account ending in "
              f"{acct} is now available. Log in to view it.\n"))


def test_a_missing_month_in_a_monthly_series(conn, archive):
    bluevine(archive, "2026-07-03")
    bluevine(archive, "2026-08-03")
    report = reconcile.run(conn, today=date(2026, 9, 20))
    assert kinds(report) == ["MISSING_STATEMENT"]
    f = report.findings[0]
    assert "2026-09" in f.summary and "4321" in f.summary and "day 3" in f.summary
    assert f.key == "missing-statement|B_MRE|bluevine|4321|2026-09"


def test_not_missing_inside_the_grace_period(conn, archive):
    bluevine(archive, "2026-07-03")
    bluevine(archive, "2026-08-03")
    assert reconcile.run(conn, today=date(2026, 9, 12)).findings == []


def test_two_similar_monthly_statements_are_two_statements(conn, archive):
    """Scenario 6. Same bank, same wording, consecutive months: two documents,
    no duplicate, no double billing, nothing missing."""
    bluevine(archive, "2026-08-03")
    bluevine(archive, "2026-09-03")
    report = reconcile.run(conn, today=date(2026, 9, 20))
    assert report.findings == []
    docs = reconcile.load_docs(conn)
    assert [d.kind for d in docs] == ["STATEMENT", "STATEMENT"]
    assert reconcile.obligations(docs, reconcile.task_index(conn)) == []


def test_the_statement_arriving_late_resolves_the_finding(conn, archive):
    bluevine(archive, "2026-07-03")
    bluevine(archive, "2026-08-03")
    reconcile.run(conn, today=date(2026, 9, 20))
    bluevine(archive, "2026-09-22")
    report = reconcile.run(conn, today=date(2026, 9, 23))
    assert report.findings == [] and report.recorded.resolved == 1
    row = conn.execute("SELECT status FROM recon_findings").fetchone()
    assert row["status"] == "RESOLVED"


def test_a_quarterly_series_is_not_checked_monthly(conn, archive):
    for day in ("2026-01-05", "2026-04-05", "2026-07-05"):
        bluevine(archive, day)
    assert reconcile.run(conn, today=date(2026, 9, 20)).findings == []


def test_a_series_that_stopped_is_reported_for_three_months_then_left(conn, archive):
    bluevine(archive, "2026-01-03")
    bluevine(archive, "2026-02-03")
    report = reconcile.run(conn, today=date(2026, 9, 20))
    assert [f.key.rsplit("|", 1)[1] for f in report.findings] == [
        "2026-03", "2026-04", "2026-05"]


def test_two_accounts_at_one_bank_are_two_series(conn, archive):
    for day in ("2026-07-03", "2026-08-03", "2026-09-03"):
        bluevine(archive, day, acct="4321")
    for day in ("2026-07-10", "2026-08-10"):
        bluevine(archive, day, acct="8890")
    report = reconcile.run(conn, today=date(2026, 9, 25))
    assert [f.key for f in report.findings] == [
        "missing-statement|B_MRE|bluevine|8890|2026-09"]


# ---------------------------------------------------------------------------
# Renewals -> tasks
# ---------------------------------------------------------------------------

GEICO_RENEWAL = ("From: GEICO <service@geico.example>\n"
                 "Subject: Your auto policy renewal\n\n"
                 "Policy 4471-22-19. Your policy renews on December 1, 2026. "
                 "Your new premium is $1,212.40 for six months.\n")


def geico(archive, *, day="2026-10-20"):
    return archive.put(cp="geico", descriptor="auto-policy-renewal-notice",
                       entity="P_MRE", category="VEHICLES",
                       subcategory="insurance-auto", domain="PERSONAL",
                       doc_date=day, text=GEICO_RENEWAL)


def test_a_renewal_date_in_a_document_becomes_one_task(conn, archive):
    aid = geico(archive)
    report = reconcile.run(conn, today=date(2026, 11, 3))
    (rec,) = report.created
    assert rec.outcome == "created"
    t = ledger.get(conn, rec.task_id)
    assert (t["status"], t["due_date"], t["due_date_verified"]) == ("DETECTED", "2026-12-01", 0)
    assert t["entity_id"] == "P_MRE" and t["source_artifact"] == aid
    assert "renews on December 1, 2026" in t["detail"]
    assert t["title"] == "Auto policy renewal notice — Geico"

    # Again tomorrow: nothing new. The document's source key is taken.
    again = reconcile.run(conn, today=date(2026, 11, 4))
    assert again.created == [] and again.renewals == []
    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1


def test_a_renewal_far_off_waits_for_its_lead_window(conn, archive):
    geico(archive)
    assert reconcile.run(conn, today=date(2026, 10, 1)).created == []
    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


def test_a_renewal_ingest_already_made_a_task_for_is_left_alone(conn, archive):
    """Ingest made a task from the covering email (it had a due date); the
    policy PDF that came with it has the renewal date and no task of its own.
    A second task would be the duplicate D-066 exists to prevent."""
    email = archive.put(cp="geico", descriptor="renewal-payment-due", entity="P_MRE",
                        category="VEHICLES", subcategory="insurance-auto",
                        doc_date="2026-10-20", due="2026-11-20", amount=121240,
                        text="Subject: Renewal payment due\n")
    archive.task(email, entity="P_MRE", title="Renewal payment due — Geico")
    archive.put(cp="geico", descriptor="auto-policy-declarations", entity="P_MRE",
                category="VEHICLES", subcategory="insurance-auto",
                doc_date="2026-10-20", ext=".pdf", ocr=GEICO_RENEWAL,
                source="attachment", parent=email)
    report = reconcile.run(conn, today=date(2026, 11, 3))
    assert report.renewals == []
    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1


def test_a_dry_run_writes_nothing(conn, archive):
    geico(archive)
    bluevine(archive, "2026-07-03")
    bluevine(archive, "2026-08-03")
    actions = conn.execute("SELECT COUNT(*) FROM actions_log").fetchone()[0]
    report = reconcile.run(conn, today=date(2026, 11, 3), record=False)
    assert report.renewals and report.findings and report.created == []
    for table in ("tasks", "recon_findings", "recon_links"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM actions_log").fetchone()[0] == actions


# ---------------------------------------------------------------------------
# Unanswered mail: the check exists; its source does not
# ---------------------------------------------------------------------------

class SentMail:
    def __init__(self, answered):
        self.answered = answered

    def replied(self, source_ref, since):
        return source_ref in self.answered


def client_mail(archive, ref, day="2026-10-20"):
    return archive.put(cp="atlas-dental", descriptor="question-about-renewal",
                       category="CLIENTS", subcategory="correspondence",
                       doc_date=day, source_ref=ref, requires_reply=True,
                       text="Subject: Question about our renewal\n\nCan you call me?")


def test_without_a_sent_mail_source_nothing_is_claimed(conn, archive):
    client_mail(archive, "m-1")
    report = reconcile.run(conn, today=date(2026, 11, 3))
    assert report.findings == []
    assert (report.replies_checked, report.awaiting_reply) == (False, 1)
    detail = conn.execute("SELECT detail FROM actions_log WHERE action = "
                          "'RECON_COMPLETED'").fetchone()[0]
    assert "not checked (no sent-mail source)" in detail


def test_with_a_sent_mail_source_unanswered_mail_is_found(conn, archive):
    client_mail(archive, "m-1")
    client_mail(archive, "m-2")
    client_mail(archive, "m-3", day="2026-11-02")          # too recent to chase
    report = reconcile.run(conn, today=date(2026, 11, 3), replies=SentMail({"m-2"}))
    assert kinds(report) == ["UNANSWERED_MAIL"]
    assert report.awaiting_reply == 2


def test_a_run_that_could_not_check_does_not_resolve_unanswered_mail(conn, archive):
    client_mail(archive, "m-1")
    reconcile.run(conn, today=date(2026, 11, 3), replies=SentMail(set()))
    reconcile.run(conn, today=date(2026, 11, 4))                        # no source
    assert conn.execute("SELECT status FROM recon_findings").fetchone()[0] == "OPEN"


# ---------------------------------------------------------------------------
# Recording: once per problem, not once per night
# ---------------------------------------------------------------------------

def test_findings_are_recorded_once_across_runs(conn, archive):
    bluevine(archive, "2026-07-03")
    bluevine(archive, "2026-08-03")
    first = reconcile.run(conn, today=date(2026, 9, 20))
    second = reconcile.run(conn, today=date(2026, 9, 21))
    assert (first.recorded.new, second.recorded.new, second.recorded.seen) == (1, 0, 1)
    assert conn.execute("SELECT COUNT(*) FROM recon_findings").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM actions_log WHERE action = "
                        "'RECON_FINDING'").fetchone()[0] == 1


def test_an_acknowledged_finding_stays_acknowledged(conn, archive):
    bluevine(archive, "2026-07-03")
    bluevine(archive, "2026-08-03")
    reconcile.run(conn, today=date(2026, 9, 20))
    fid = conn.execute("SELECT id FROM recon_findings").fetchone()[0]
    reconcile.acknowledge(conn, fid, note="account moved to paper statements")
    reconcile.run(conn, today=date(2026, 9, 21))
    row = conn.execute("SELECT * FROM recon_findings WHERE id = ?", (fid,)).fetchone()
    assert row["status"] == "ACKNOWLEDGED" and row["note"].startswith("account moved")
    assert reconcile.open_findings(conn) == []
    assert reconcile.brief_lines(conn) == []


def test_brief_lines_put_money_first_and_stay_short(conn, archive):
    for day in ("2026-01-03", "2026-02-03"):
        bluevine(archive, day)                     # three missing months
    psg_bill_and_payment(archive, paid=50000)      # an amount mismatch
    reconcile.run(conn, today=date(2026, 9, 25))
    lines = reconcile.brief_lines(conn, limit=2)
    assert "$500.00" in lines[0]                   # AMOUNT_MISMATCH before MISSING
    assert "statement" in lines[1]
    assert lines[2] == "+2 more: ./ops/reconcile.py --list"


def test_a_reopened_task_resolves_its_received_again_finding(conn, archive):
    """He reopened it: he has picked the evidence up."""
    a = archive.put(cp=PSG, descriptor="tax-filing-invoice-inv17", ext=".pdf",
                    ocr=PSG_PDF_OCR, doc_date="2026-09-19", amount=55000,
                    source="attachment", source_ref="m1/att1")
    t = archive.task(a)
    close(conn, t, on="2026-09-28")
    sha = conn.execute("SELECT sha256 FROM artifacts WHERE id = ?", (a,)).fetchone()[0]
    conn.execute("INSERT INTO duplicate_refs (sha256, seen_at, source, source_ref) "
                 "VALUES (?, '2026-10-14T07:00:00-04:00', 'attachment', 'm9/att1')", (sha,))
    assert kinds(reconcile.run(conn, today=date(2026, 10, 15))) == ["RECEIVED_AGAIN"]
    task_ops.reopen_task(conn, t)
    report = reconcile.run(conn, today=date(2026, 10, 16))
    assert report.findings == [] and report.recorded.resolved == 1


# ---------------------------------------------------------------------------
# The CLI
# ---------------------------------------------------------------------------

def _load_cli():
    import importlib.util
    os.environ["LMS_RECONCILE_REEXEC"] = "1"         # no re-exec inside pytest
    spec = importlib.util.spec_from_file_location("ops_reconcile", OPS / "reconcile.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_the_cli_runs_lists_and_acknowledges(tmp_path, monkeypatch, capsys):
    archive_root = tmp_path / "LMS" / "archive"
    archive_root.mkdir(parents=True)
    monkeypatch.setenv("LMS_ARCHIVE_ROOT", str(archive_root))
    monkeypatch.setenv("LMS_DB", str(tmp_path / "LMS" / "lms.db"))
    conn = db.connect(tmp_path / "LMS" / "lms.db")
    a = Archive(conn, archive_root)
    bluevine(a, "2026-07-03")
    bluevine(a, "2026-08-03")
    conn.close()

    cli = _load_cli()
    monkeypatch.setattr("sys.argv", ["reconcile.py", "--dry-run", "--today", "2026-09-20"])
    assert cli.main() == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "MISSING STATEMENT" in out
    assert "Unanswered mail: NOT CHECKED" in out

    monkeypatch.setattr("sys.argv", ["reconcile.py", "--today", "2026-09-20"])
    assert cli.main() == 0
    out = capsys.readouterr().out
    assert "1 new" in out and "#1" in out

    monkeypatch.setattr("sys.argv", ["reconcile.py", "--ack", "1", "--note", "paper"])
    assert cli.main() == 0
    monkeypatch.setattr("sys.argv", ["reconcile.py", "--list"])
    assert cli.main() == 0
    assert "nothing to look at" in capsys.readouterr().out
