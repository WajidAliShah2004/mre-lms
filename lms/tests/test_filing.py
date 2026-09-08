"""Filing and idempotency tests.

The BUILD_PLAYBOOK Day-2 check is: feed 10 documents, each files correctly
with a sidecar, and re-feeding the same file is a no-op. That check is
encoded here so it runs on every change instead of once by hand.
"""

import json
from pathlib import Path

import pytest

from core.db import database as db
from core.pipeline import filing
from core.pipeline.registry import load_registry

CONFIG = Path(__file__).resolve().parents[1] / "config"


@pytest.fixture
def registry():
    return load_registry(CONFIG)


@pytest.fixture
def roots(tmp_path):
    r = filing.StorageRoots(
        archive=tmp_path / "archive",
        originals=tmp_path / "_originals",
        quarantine=tmp_path / "quarantine",
    )
    r.ensure()
    return r


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "lms.db")
    yield c
    c.close()


def make_doc(tmp_path: Path, name: str, content: bytes) -> tuple[Path, str]:
    p = tmp_path / name
    p.write_bytes(content)
    return p, db.sha256_bytes(content)


def test_files_into_correct_tree(conn, roots, registry, tmp_path):
    src, sha = make_doc(tmp_path, "statement.pdf", b"july commissions")

    result = filing.file_artifact(
        conn, roots, registry,
        source_path=src, sha256=sha, source="email",
        entity_id="B_CHS", category="FINANCE", subcategory="commissions",
        doc_date="2026-07-31", counterparty="Foundation Risk Partners",
        descriptor="July Renewal Statement", amount_cents=1420833,
    )

    assert result.filed_path.exists()
    assert result.filed_path.parent == roots.archive / "CHSHINK" / "FINANCE" / "commissions"
    assert result.filed_path.name.startswith("2026-07-31__CHS__FINANCE__")
    assert not result.was_duplicate


def test_sidecar_holds_untruncated_values(conn, roots, registry, tmp_path):
    src, sha = make_doc(tmp_path, "bill.pdf", b"electric")
    long_counterparty = "Consolidated Edison Company of New York, Incorporated"

    result = filing.file_artifact(
        conn, roots, registry,
        source_path=src, sha256=sha, source="photo",
        entity_id="P_HH", category="HOME", subcategory="utilities",
        doc_date="2026-03-04", counterparty=long_counterparty,
        descriptor="March Electric Bill", amount_cents=18742,
    )

    meta = json.loads(result.sidecar_path.read_text(encoding="utf-8"))
    # The filename is lossy; the sidecar must not be.
    assert meta["counterparty_full"] == long_counterparty
    assert meta["amount_cents"] == 18742
    assert meta["entity_id"] == "P_HH"
    assert meta["sha256"] == sha


def test_refeeding_same_bytes_is_a_noop(conn, roots, registry, tmp_path):
    src, sha = make_doc(tmp_path, "invoice.pdf", b"same bytes")
    kwargs = dict(
        source_path=src, sha256=sha, source="email", entity_id="B_MRE",
        category="FINANCE", doc_date="2026-05-01", counterparty="acme",
        descriptor="acme invoice", amount_cents=1000,
    )

    first = filing.file_artifact(conn, roots, registry, **kwargs)
    second = filing.file_artifact(conn, roots, registry, **kwargs)

    assert second.was_duplicate
    assert second.filed_path == first.filed_path
    assert second.artifact_id == first.artifact_id

    # Exactly one file in the tree, and the re-sighting is recorded.
    filed = list((roots.archive / "MRECAI" / "FINANCE").glob("*.pdf"))
    assert len(filed) == 1
    dupes = conn.execute("SELECT COUNT(*) c FROM duplicate_refs WHERE sha256 = ?", (sha,)).fetchone()
    assert dupes["c"] == 1


def test_same_content_different_filename_still_dedupes(conn, roots, registry, tmp_path):
    """Dedupe is on bytes, not names — a resend usually gets renamed."""
    a, sha = make_doc(tmp_path, "invoice.pdf", b"identical")
    b = tmp_path / "invoice-FINAL-v2.pdf"
    b.write_bytes(b"identical")

    common = dict(
        source="email", entity_id="B_MRE", category="FINANCE",
        doc_date="2026-05-01", counterparty="acme", descriptor="acme invoice",
        amount_cents=None,
    )
    filing.file_artifact(conn, roots, registry, source_path=a, sha256=sha, **common)
    second = filing.file_artifact(conn, roots, registry, source_path=b, sha256=sha, **common)

    assert second.was_duplicate


def test_original_is_copied_never_moved(conn, roots, registry, tmp_path):
    """The source belongs to iCloud or the mail store. We never move it."""
    src, sha = make_doc(tmp_path, "photo.heic", b"bytes")

    filing.file_artifact(
        conn, roots, registry, source_path=src, sha256=sha, source="photo",
        entity_id="P_MRE", category="LEGAL", doc_date="2026-09-01",
        counterparty="county-court", descriptor="jury duty summons",
        amount_cents=None,
    )

    assert src.exists(), "source file was moved or deleted"
    assert (roots.originals / f"{sha}.heic").exists()


def test_ocr_text_written_alongside(conn, roots, registry, tmp_path):
    src, sha = make_doc(tmp_path, "scan.pdf", b"scanned")

    result = filing.file_artifact(
        conn, roots, registry, source_path=src, sha256=sha, source="photo",
        entity_id="P_MRE", category="LEGAL", doc_date="2026-09-01",
        counterparty="county-court", descriptor="jury duty summons",
        amount_cents=None, ocr_text="REPORT FOR JURY SERVICE ON SEPTEMBER 12",
    )

    txt = result.filed_path.with_name(result.filed_path.name + ".txt")
    assert "JURY SERVICE" in txt.read_text(encoding="utf-8")


def test_invalid_category_for_entity_is_rejected(conn, roots, registry, tmp_path):
    """WEDDING is a personal category. A business must not be able to use it."""
    src, sha = make_doc(tmp_path, "x.pdf", b"x")
    with pytest.raises(Exception):
        filing.file_artifact(
            conn, roots, registry, source_path=src, sha256=sha, source="email",
            entity_id="B_MRE", category="WEDDING", doc_date="2026-01-01",
            counterparty="x", descriptor="a b", amount_cents=None,
        )


def test_quarantine_keeps_file_and_reason(conn, roots, tmp_path):
    src, sha = make_doc(tmp_path, "sketchy.pdf", b"wire your money")

    target = filing.quarantine_artifact(
        conn, roots, source_path=src, sha256=sha, source="email",
        reason="SUSPECTED_PHISHING: lookalike domain",
    )

    assert target.exists()
    meta = json.loads(target.with_name(target.name + ".meta.json").read_text(encoding="utf-8"))
    assert "lookalike" in meta["reason"]
    row = conn.execute("SELECT status FROM artifacts WHERE sha256 = ?", (sha,)).fetchone()
    assert row["status"] == "QUARANTINED"


def test_ten_documents_land_in_ten_distinct_paths(conn, roots, registry, tmp_path):
    """The Day-2 acceptance check, automated."""
    specs = [
        ("B_CHS", "FINANCE", "commissions"), ("B_MRE", "CLIENTS", "policies"),
        ("B_ATL", "OPERATIONS", "technology"), ("B_MLE", "TAX", "filings"),
        ("P_MRE", "LEGAL", "court"), ("P_JG", "WEDDING", "vendors"),
        ("P_HH", "HOME", "utilities"), ("P_AE", "HEALTH", "medical"),
        ("P_PE", "FINANCE", "banking"), ("B_MRE", "COMPLIANCE", "licensing"),
    ]
    paths = set()
    for i, (entity, category, sub) in enumerate(specs):
        src, sha = make_doc(tmp_path, f"doc{i}.pdf", f"contents {i}".encode())
        r = filing.file_artifact(
            conn, roots, registry, source_path=src, sha256=sha, source="email",
            entity_id=entity, category=category, subcategory=sub,
            doc_date="2026-06-15", counterparty=f"sender-{i}",
            descriptor=f"test document {i}", amount_cents=i * 100 or None,
        )
        assert r.filed_path.exists()
        assert r.sidecar_path.exists()
        paths.add(r.filed_path)

    assert len(paths) == 10
