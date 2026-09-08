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


INVOICE = {
    "domain": "BUSINESS", "entity_id": "B_MRE", "category": "FINANCE",
    "subcategory": "invoices", "urgency": "HIGH", "confidence": 0.93,
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
    assert result.path.parent == roots.archive / "MRECAI" / "FINANCE" / "invoices"
    assert result.path.name.startswith("2026-09-01__MRE__FINANCE__")
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
        src, artifact=Artifact(recipient="matthew@mrecai.com"), run_ocr=False)

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
        artifact=Artifact(recipient="matthew@mrecai.com"), run_ocr=False)

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


def test_reingesting_the_same_bytes_is_a_noop(conn, roots, registry, tmp_path):
    src = doc(tmp_path, "invoice.pdf", b"same bytes")
    args = dict(source="email", source_ref="msg-1",
                artifact=Artifact(recipient="matthew@mrecai.com"), run_ocr=False)

    first = ingest_file(conn, roots, registry, classifier(registry, INVOICE), src, **args)
    second = ingest_file(conn, roots, registry, classifier(registry, INVOICE), src, **args)

    assert first.status == "FILED"
    assert second.status == "DUPLICATE"
    assert second.path == first.path
    filed = list((roots.archive / "MRECAI" / "FINANCE" / "invoices").glob("*.pdf"))
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
