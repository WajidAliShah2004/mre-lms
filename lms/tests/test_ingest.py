"""End-to-end pipeline tests — a real file in, a filed document out.

The model is mocked; everything else is real. Files are written to a temp
directory, hashed, deduped, classified, filed, and read back.

Between them these encode the BUILD_PLAYBOOK Day-2 and Day-7 acceptance
checks, so they run on every change rather than once by hand in front of the
client.
"""

import json
from pathlib import Path

import pytest

from core.adapters.lmstudio import Completion
from core.db import database as db
from core.pipeline import filing
from core.pipeline.classify import Artifact, Classifier
from core.pipeline.ingest import (guess_doc_date, ingest_file, phishing_check,
                                  _task_title)
from core.pipeline.registry import load_registry

CONFIG = Path(__file__).resolve().parents[1] / "config"


@pytest.fixture
def registry():
    return load_registry(CONFIG)


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "lms.db")
    yield c
    c.close()


@pytest.fixture
def roots(tmp_path):
    r = filing.StorageRoots(archive=tmp_path / "archive",
                            originals=tmp_path / "_originals",
                            quarantine=tmp_path / "quarantine")
    r.ensure()
    return r


class FakeModel:
    def __init__(self, response):
        self._response = response

    def complete(self, **kw):
        return Completion(text=json.dumps(self._response), model="fake",
                          elapsed_s=0.01, finish_reason="stop")


# A bill FROM Acme TO us. Under D-026 that is unambiguously VENDORS: the
# money moves out. It used to read FINANCE/invoices, which was defensible only
# because the taxonomy offered a bare `invoices` under both categories and
# said nothing about which one meant what.
INVOICE = {
    "domain": "BUSINESS", "entity_id": "B_MRE", "category": "VENDORS",
    "subcategory": "invoices-received", "urgency": "HIGH", "confidence": 0.93,
    "requires_reply": False, "due_date": "2026-09-30",
    "counterparty": "acme-supply", "descriptor": "september invoice",
    "amount_cents": 124500, "rationale": "invoice with a due date",
}


def doc(tmp_path: Path, name: str, content: bytes) -> Path:
    p = tmp_path / name
    p.write_bytes(content)
    return p


def classifier(registry, response):
    return Classifier(registry, client=FakeModel(response))


# ---------------------------------------------------------------------------
# The happy path — the Day-7 acceptance test 1
# ---------------------------------------------------------------------------

def test_business_document_files_and_creates_a_task(conn, roots, registry, tmp_path):
    src = doc(tmp_path, "invoice.pdf", b"ACME SUPPLY invoice total $1,245.00")

    result = ingest_file(
        conn, roots, registry,
        classifier(registry, {**INVOICE, "doc_date": "2026-09-01"}),
        src, source="email", source_ref="msg-1",
        artifact=Artifact(recipient="matthew@mrecai.com",
                          subject="Invoice 4471", body="Invoice dated 2026-09-01"),
        run_ocr=False)

    assert result.status == "FILED"
    assert result.path.exists()
    assert result.path.parent == roots.archive / "MRECAI" / "VENDORS" / "invoices-received"
    assert result.path.name.startswith("2026-09-01__MRE__VENDORS__")
    assert "USD1245-00" in result.path.name


def test_due_date_is_not_used_as_the_document_date(conn, roots, registry, tmp_path):
    """D-007: the filename carries the DOCUMENT's date, not an obligation's.

    Filing a September invoice payable in December under December would put it
    three months from where anyone would look for it. The due date belongs on
    the task; the document date belongs in the name.
    """
    src = doc(tmp_path, "invoice.pdf", b"x")
    result = ingest_file(
        conn, roots, registry,
        classifier(registry, {**INVOICE, "doc_date": "2026-09-01",
                              "due_date": "2026-12-31"}),
        src, artifact=Artifact(recipient="matthew@mrecai.com",
                               body="ACME SUPPLY invoice"), run_ocr=False)

    assert result.path.name.startswith("2026-09-01__"), "due date leaked into the filename"
    row = conn.execute("SELECT due_date FROM tasks WHERE id = ?",
                       (result.task_id,)).fetchone()
    assert row["due_date"] == "2026-12-31", "due date lost from the task"


def test_document_with_no_date_anywhere_still_files(conn, roots, registry, tmp_path):
    """A document always lands somewhere. Falling back to today is a worse
    date, not a reason to refuse the document."""
    src = doc(tmp_path, "undated.pdf", b"no dates in here at all")
    result = ingest_file(
        conn, roots, registry,
        classifier(registry, {**INVOICE, "doc_date": None, "due_date": None}),
        src, artifact=Artifact(recipient="matthew@mrecai.com", body="no date"),
        run_ocr=False)

    assert result.status == "FILED"
    assert result.path.exists()


def test_sidecar_and_task_carry_what_the_filename_cannot(conn, roots, registry, tmp_path):
    """The filename is lossy by design; the sidecar and the database are not."""
    src = doc(tmp_path, "invoice.pdf", b"ACME SUPPLY invoice")
    result = ingest_file(
        conn, roots, registry,
        classifier(registry, {**INVOICE, "doc_date": "2026-09-01"}),
        src, source="email", source_ref="msg-1",
        artifact=Artifact(recipient="matthew@mrecai.com",
                          body="ACME SUPPLY invoice"), run_ocr=False)

    meta = json.loads(result.path.with_name(result.path.name + ".meta.json")
                      .read_text(encoding="utf-8"))
    # 0.95, not the model's own 0.93: this artifact is addressed to
    # matthew@mrecai.com, so routing decided the entity and the rule's floor
    # applies (D-023). decided_by is what makes that legible six months later —
    # without it the sidecar shows a confidence nobody can account for.
    assert meta["confidence"] == 0.95
    assert meta["decided_by"] == "rule"
    assert meta["prompt_hash"], "no provenance — a bad prompt revision would be untraceable"
    assert meta["ocr_engine"] is None

    assert result.task_id is not None
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (result.task_id,)).fetchone()
    assert row["due_date"] == "2026-09-30"
    assert "Acme Supply" in row["title"]


# ---------------------------------------------------------------------------
# D-024 — never classify a document nobody read
#
# The watched folder accepted a .txt invoice, found no extraction path for it
# (only image suffixes were handled), and passed an EMPTY body to the model.
# The model answered 0.15 — correctly, there was nothing there — and it
# quarantined as "confidence 0.15 below 0.60", which reads like a model problem
# and is a reading problem two stages earlier.
#
# An empty body is also where a confidently wrong answer costs most: with no
# content to be constrained by, whatever the model invents is unfalsifiable.
# ---------------------------------------------------------------------------

def test_a_text_file_is_read_not_ignored(conn, roots, registry, tmp_path):
    """The bug, as the smallest test that would have caught it."""
    src = doc(tmp_path, "inv.txt",
              b"ACME SUPPLY COMPANY\nINVOICE 6022\nBill to: MRECAI\nTOTAL DUE: $1,450.00\n")
    result = ingest_file(conn, roots, registry, classifier(registry, INVOICE), src)

    assert result.status == "FILED", result.reason
    assert result.ocr_engine == "plain-text", "a text file went through an OCR engine"


def test_a_text_document_is_not_archived_twice(conn, roots, registry, tmp_path):
    """Found in the first live [FILED] on the Mac: `...__hash.txt.txt`.

    The extracted-text sidecar earns its place beside a photograph, where the
    image is not searchable and the transcription is. Beside a document that
    arrived as text it is a byte-identical copy — it doubles the archive for
    every text document and puts a second file in the folder that reads as a
    separate record.
    """
    src = doc(tmp_path, "inv.txt",
              b"ACME SUPPLY COMPANY\nINVOICE 6023\nBill to: MRECAI\nTOTAL DUE: $2,100.00\n")
    result = ingest_file(conn, roots, registry, classifier(registry, INVOICE), src)

    assert result.status == "FILED", result.reason
    assert not result.path.with_name(result.path.name + ".txt").exists(), \
        "the document was archived a second time as its own OCR sidecar"

    # One document, one sidecar, and nothing else.
    siblings = sorted(p.name for p in result.path.parent.iterdir())
    assert siblings == sorted([result.path.name, result.path.name + ".meta.json"]), siblings


def test_the_sidecar_still_says_the_text_was_read(conn, roots, registry, tmp_path):
    """Not writing the copy must not become 'we never read it'.

    has_ocr_text and ocr_engine are how a future reader knows the body was
    genuine text rather than an unread file that slipped past D-024.
    """
    src = doc(tmp_path, "inv.txt",
              b"ACME SUPPLY COMPANY\nINVOICE 6023\nBill to: MRECAI\nTOTAL DUE: $2,100.00\n")
    result = ingest_file(conn, roots, registry, classifier(registry, INVOICE), src)

    meta = json.loads(result.path.with_name(result.path.name + ".meta.json")
                      .read_text(encoding="utf-8"))
    assert meta["ocr_engine"] == "plain-text"


def test_a_photograph_still_gets_its_transcription(conn, roots, registry, tmp_path,
                                                   monkeypatch):
    """The boundary. Suppressing the copy for text must not suppress it for
    images, where the sidecar is the only searchable form of the document."""
    from core.pipeline import ocr as ocr_mod

    monkeypatch.setattr(ocr_mod, "extract_text",
                        lambda p, **kw: ocr_mod.OCRResult(
                            text="ACME SUPPLY COMPANY INVOICE 6023 TOTAL DUE $2,100.00",
                            engine="apple-vision"))

    src = doc(tmp_path, "photo.jpg", b"\xff\xd8\xff not really a jpeg")
    result = ingest_file(conn, roots, registry, classifier(registry, INVOICE), src)

    assert result.status == "FILED", result.reason
    transcript = result.path.with_name(result.path.name + ".txt")
    assert transcript.exists(), "a photograph was filed with no searchable text"
    assert "ACME" in transcript.read_text(encoding="utf-8")


def test_an_unreadable_format_is_quarantined_before_the_model(conn, roots, registry,
                                                              tmp_path):
    """A PDF cannot be read yet. It must not be classified anyway.

    The model is one that would answer confidently if asked. The assertion is
    that it never gets asked.
    """
    asked = []

    class Watching(FakeModel):
        def complete(self, **kw):
            asked.append(kw)
            return super().complete(**kw)

    # .docx, not .pdf. This test used a PDF until D-029 made PDFs readable, at
    # which point it kept passing while meaning something else entirely — it
    # was asserting "a format with no reader" using a format that now has one.
    src = doc(tmp_path, "contract.docx", b"PK\x03\x04 binary bytes")
    result = ingest_file(conn, roots, registry,
                         Classifier(registry, client=Watching(INVOICE)), src)

    assert result.status == "QUARANTINED"
    assert asked == [], "the model was asked about a document nobody could read"
    assert ".docx" in result.reason, result.reason


def test_the_quarantine_reason_names_the_cause_not_the_symptom(conn, roots, registry,
                                                               tmp_path):
    """'no text extracted' sends someone to the reader. 'low confidence' sends
    them to the prompt, which is the wrong place and costs an afternoon."""
    src = doc(tmp_path, "mystery.xyz", b"whatever")
    result = ingest_file(conn, roots, registry,
                         classifier(registry, INVOICE), src)

    assert "no text extracted" in result.reason
    assert "confidence" not in result.reason


def test_an_empty_text_file_does_not_reach_the_model(conn, roots, registry, tmp_path):
    src = doc(tmp_path, "blank.txt", b"   \n\n  ")
    result = ingest_file(conn, roots, registry, classifier(registry, INVOICE), src)
    assert result.status == "QUARANTINED"
    assert "read as empty" in result.reason


def test_an_unreadable_document_is_still_kept(conn, roots, registry, tmp_path):
    """Refusing to classify is not refusing to keep. The bytes are the record."""
    src = doc(tmp_path, "scan.pdf", b"%PDF-1.4 not really a pdf")
    result = ingest_file(conn, roots, registry, classifier(registry, INVOICE), src)

    assert result.path is not None and result.path.exists()
    actions = [r["action"] for r in conn.execute("SELECT action FROM actions_log")]
    assert "UNREADABLE" in actions, "the refusal left no trace in the log"


# ---------------------------------------------------------------------------
# D-029 — PDFs, the format most real mail actually arrives in
# ---------------------------------------------------------------------------

def test_a_pdf_with_a_text_layer_is_read_without_ocr(conn, roots, registry,
                                                     tmp_path, monkeypatch):
    """Reading a generated PDF's own text is exact. OCR of the same page
    INTRODUCES errors into a document that had none — a transposed digit in an
    account number that was perfectly legible in the source."""
    from core.pipeline import ocr as ocr_mod

    monkeypatch.setattr(ocr_mod, "pdf_text_layer", lambda p: ocr_mod.OCRResult(
        text="ACME SUPPLY COMPANY\nINVOICE 7001\nBill to: MRECAI\nTOTAL DUE: $310.00",
        engine="pdfkit-text", pages=1))
    monkeypatch.setattr(ocr_mod, "pdf_ocr", _never_called)

    src = doc(tmp_path, "invoice.pdf", b"%PDF-1.7")
    result = ingest_file(conn, roots, registry, classifier(registry, INVOICE), src)

    assert result.status == "FILED", result.reason
    assert result.ocr_engine == "pdfkit-text"


def test_a_scanned_pdf_falls_through_to_ocr(conn, roots, registry, tmp_path,
                                            monkeypatch):
    """A scan carries pixels, not text. An empty text layer is the signal."""
    from core.pipeline import ocr as ocr_mod

    monkeypatch.setattr(ocr_mod, "pdf_text_layer",
                        lambda p: ocr_mod.OCRResult(text="", engine="pdfkit-text"))
    monkeypatch.setattr(ocr_mod, "pdf_ocr", lambda p: ocr_mod.OCRResult(
        text="ACME SUPPLY COMPANY INVOICE 7002 Bill to MRECAI TOTAL DUE $640.00",
        engine="pdfkit+apple-vision", pages=1))

    src = doc(tmp_path, "scan.pdf", b"%PDF-1.7")
    result = ingest_file(conn, roots, registry, classifier(registry, INVOICE), src)

    assert result.status == "FILED", result.reason
    assert result.ocr_engine == "pdfkit+apple-vision"


def test_a_cover_page_of_text_does_not_pass_for_a_whole_scan(monkeypatch, tmp_path):
    """The awkward middle case: a scan with a generated cover sheet.

    Reading only the text layer would file the document on the strength of its
    letterhead and never look at the pages that carry the content.
    """
    from core.pipeline import ocr as ocr_mod

    monkeypatch.setattr(ocr_mod, "pdf_text_layer", lambda p: ocr_mod.OCRResult(
        text="Fax cover", engine="pdfkit-text"))          # under MIN_USEFUL_CHARS
    monkeypatch.setattr(ocr_mod, "pdf_ocr", lambda p: ocr_mod.OCRResult(
        text="x" * 200, engine="pdfkit+apple-vision"))

    src = doc(tmp_path, "fax.pdf", b"%PDF-1.7")
    assert ocr_mod.extract_pdf(src).engine == "pdfkit+apple-vision"


def test_a_locked_pdf_is_never_rasterised(monkeypatch, tmp_path):
    """Rendering an encrypted PDF yields blank pages, and blank pages OCR to a
    successful empty read — a locked document reported as an unreadable one."""
    from core.pipeline import ocr as ocr_mod

    def locked(_):
        raise ocr_mod.OCRError("PDF is password-protected — it is filed unread, "
                              "nothing was guessed about its contents")

    monkeypatch.setattr(ocr_mod, "pdf_text_layer", locked)
    monkeypatch.setattr(ocr_mod, "pdf_ocr", _never_called)

    src = doc(tmp_path, "locked.pdf", b"%PDF-1.7")
    with pytest.raises(ocr_mod.OCRError) as exc:
        ocr_mod.extract_pdf(src)
    assert "password-protected" in str(exc.value)


def _never_called(*a, **kw):
    raise AssertionError("this reader should not have run")


def test_every_readable_suffix_has_a_reader(tmp_path):
    """The assertion that makes D-024 unrepeatable.

    That bug was READABLE_SUFFIXES and the code acting on it disagreeing: the
    set said a .txt was readable, the extraction branch had no arm for it, and
    the gap became an empty body the model was asked to classify. This walks
    the set and demands read_any() reach a real reader for every member —
    anything unhandled raises the "disagree" error, and anything handled fails
    later for an honest reason (no PyObjC here, no real bytes there).
    """
    from core.pipeline import ocr as ocr_mod

    # A stub client, so the image arm does not dial LM Studio nine times over.
    # A test that reaches the network is a test people start skipping.
    class Stub:
        def complete(self, **kw):
            return Completion(text="stub transcription", model="stub",
                              elapsed_s=0.0, finish_reason="stop")

    unhandled = []
    for suffix in sorted(ocr_mod.READABLE_SUFFIXES):
        p = tmp_path / f"probe{suffix}"
        p.write_bytes(b"")
        try:
            ocr_mod.read_any(p, client=Stub())
        except ocr_mod.OCRError as exc:
            if "disagree" in str(exc):
                unhandled.append(suffix)
        except Exception:
            pass          # a real reader that failed on empty bytes. Fine.

    assert not unhandled, f"in READABLE_SUFFIXES with no reader: {unhandled}"


def test_an_unknown_suffix_says_so_rather_than_falling_through(tmp_path):
    from core.pipeline import ocr as ocr_mod

    p = tmp_path / "thing.rtf"
    p.write_bytes(b"x")
    with pytest.raises(ocr_mod.OCRError) as exc:
        ocr_mod.read_any(p)
    assert ".rtf" in str(exc.value)


def test_reingesting_the_same_bytes_is_a_noop(conn, roots, registry, tmp_path):
    src = doc(tmp_path, "invoice.pdf", b"same bytes")
    args = dict(source="email", source_ref="msg-1",
                artifact=Artifact(recipient="matthew@mrecai.com",
                                  body="same bytes invoice"), run_ocr=False)

    first = ingest_file(conn, roots, registry, classifier(registry, INVOICE), src, **args)
    second = ingest_file(conn, roots, registry, classifier(registry, INVOICE), src, **args)

    assert first.status == "FILED"
    assert second.status == "DUPLICATE"
    assert second.path == first.path
    filed = list((roots.archive / "MRECAI" / "VENDORS" / "invoices-received").glob("*.pdf"))
    assert len(filed) == 1


def test_low_confidence_quarantines_rather_than_guessing(conn, roots, registry, tmp_path):
    src = doc(tmp_path, "unclear.pdf", b"illegible")
    result = ingest_file(
        conn, roots, registry,
        classifier(registry, {**INVOICE, "confidence": 0.31}),
        src, artifact=Artifact(body="???"), run_ocr=False)

    assert result.status == "QUARANTINED"
    assert result.path.parent == roots.quarantine
    assert not list(roots.archive.rglob("*.pdf")), "quarantined doc reached the archive"


# ---------------------------------------------------------------------------
# Phishing — Day-7 acceptance test 5
# ---------------------------------------------------------------------------

def test_planted_wire_fraud_is_blocked_before_the_model(conn, roots, registry, tmp_path):
    """The model is never consulted, and no action is taken."""
    src = doc(tmp_path, "urgent.pdf", b"x")
    art = Artifact(
        sender="accounts@acme-supply.com",
        recipient="matthew@mrecai.com",
        subject="URGENT: updated bank details",
        body="Please note our updated bank details. Use these new wiring "
             "instructions for the outstanding invoice. Send confirmation.")

    result = ingest_file(conn, roots, registry, classifier(registry, INVOICE),
                         src, source="email", artifact=art, run_ocr=False)

    assert result.status == "SUSPECTED_PHISHING"
    assert result.path.parent == roots.quarantine
    assert not list(roots.archive.rglob("*")), "phishing artifact reached the archive"

    actions = [r["action"] for r in conn.execute("SELECT action FROM actions_log")]
    assert "PHISHING_BLOCKED" in actions, "declining to act must still be logged"


def test_lookalike_domain_is_caught(registry):
    """mrecai.com vs mrecal.com — one character."""
    reason = phishing_check(
        Artifact(sender="billing@mrecal.com", body="invoice"), registry)
    assert reason and "look-alike" in reason


def test_authentication_failure_is_conclusive_on_its_own(registry):
    reason = phishing_check(Artifact(sender="x@example.com", body="hello"),
                            registry, auth_results={"dkim_fail": True})
    assert reason and "dkim_fail" in reason


def test_ordinary_mail_is_not_flagged(registry):
    assert phishing_check(
        Artifact(sender="jane@example.com", subject="Lunch Thursday?",
                 body="Are you free around one?"), registry) is None


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

def test_document_date_beats_ingestion_date():
    """D-007: an invoice photographed late still files under its issue date."""
    assert guess_doc_date("Invoice dated 2026-07-31, please pay") == "2026-07-31"
    assert guess_doc_date("Dated 7/31/2026") == "2026-07-31"


def test_impossible_dates_are_rejected():
    assert guess_doc_date("ref 2026-13-45", fallback="2026-01-01") == "2026-01-01"


def test_missing_date_falls_back_rather_than_failing():
    assert guess_doc_date("no date here", fallback="2026-05-05") == "2026-05-05"


# ---------------------------------------------------------------------------
# Task wording
# ---------------------------------------------------------------------------

def test_task_titles_read_like_a_human_wrote_them(registry):
    from core.pipeline.classify import Classification
    c = Classification(
        domain="PERSONAL", entity_id="P_MRE", category="LEGAL", urgency="HIGH",
        confidence=0.9, requires_reply=False, due_date="2026-09-12",
        counterparty="county-court", descriptor="jury duty summons",
        rationale="summons")
    title = _task_title(c)
    assert title == "Jury duty summons — County Court (by 2026-09-12)"
