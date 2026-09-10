"""Corrections — spec §Day-5.5.

The property under test throughout: after a correction, the database and the
archive agree. A correction that updates one and not the other is worse than
none, because it produces a system that knows the truth and a folder tree that
doesn't, and Matthew looks in folders.
"""

import json
from pathlib import Path

import pytest

from core.db import database as db
from core.pipeline import corrections, filing
from core.pipeline.corrections import UNSET, CorrectionError, apply_correction
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


def filed(conn, roots, registry, tmp_path, *, entity="B_MRE", category="VENDORS",
          subcategory="invoices-received", doc_date="2026-09-06",
          body=b"ACME SUPPLY invoice") -> filing.FilingResult:
    src = tmp_path / "invoice.pdf"
    src.write_bytes(body)
    sha = db.sha256_file(src)
    result = filing.file_artifact(
        conn, roots, registry, source_path=src, sha256=sha, source="email",
        entity_id=entity, category=category, subcategory=subcategory,
        doc_date=doc_date, counterparty="acme-supply", descriptor="invoice",
        amount_cents=324000, currency="USD",
        extra_metadata={"ocr_engine": "pdfkit-text", "confidence": 0.95,
                        "prompt_hash": "abc123", "decided_by": "model"})
    db.insert_classification(
        conn, artifact_id=result.artifact_id, domain="BUSINESS",
        entity_id=entity, category=category, subcategory=subcategory,
        urgency="NORMAL", confidence=0.95, counterparty="acme-supply",
        descriptor="invoice", amount_cents=324000, currency="USD",
        model="qwen", prompt_hash="abc123", decided_by="model")
    conn.commit()
    return result


# ---------------------------------------------------------------------------
# The archive follows the database
# ---------------------------------------------------------------------------

def test_a_correction_moves_the_document(conn, roots, registry, tmp_path):
    """Recording the truth and leaving the file where it was produces a
    database that knows and an archive that doesn't."""
    r = filed(conn, roots, registry, tmp_path)
    assert r.filed_path.exists()

    out = apply_correction(conn, roots, registry, r.artifact_id,
                           entity_id="B_ATL")

    assert out.refiled
    assert not r.filed_path.exists(), "the document was left in the wrong place"
    assert out.filed_path.exists()
    assert "ATLASE" in str(out.filed_path).upper()


def test_the_sidecar_and_transcription_travel_with_it(conn, roots, registry,
                                                      tmp_path):
    """A document without its sidecar is a well-named file nobody can audit."""
    r = filed(conn, roots, registry, tmp_path)
    transcript = r.filed_path.with_name(r.filed_path.name + ".txt")
    transcript.write_text("ACME SUPPLY invoice", encoding="utf-8")

    out = apply_correction(conn, roots, registry, r.artifact_id,
                           entity_id="B_ATL")

    assert out.filed_path.with_name(out.filed_path.name + ".meta.json").exists()
    assert out.filed_path.with_name(out.filed_path.name + ".txt").exists()
    assert not transcript.exists()


def test_the_database_points_at_where_the_file_actually_is(conn, roots,
                                                           registry, tmp_path):
    r = filed(conn, roots, registry, tmp_path)
    out = apply_correction(conn, roots, registry, r.artifact_id,
                           entity_id="B_ATL")

    row = conn.execute("SELECT filed_path FROM artifacts WHERE id = ?",
                       (r.artifact_id,)).fetchone()
    assert Path(row["filed_path"]) == out.filed_path
    assert Path(row["filed_path"]).exists()


def test_the_filename_is_rebuilt_not_just_the_folder(conn, roots, registry,
                                                     tmp_path):
    """The entity code is IN the filename (D-007). Moving the file without
    renaming it leaves MRE in the name of a document filed under Atlase."""
    r = filed(conn, roots, registry, tmp_path)
    out = apply_correction(conn, roots, registry, r.artifact_id,
                           entity_id="B_ATL")
    assert "__MRE__" not in out.filed_path.name
    assert "__ATL__" in out.filed_path.name


# ---------------------------------------------------------------------------
# What gets recorded
# ---------------------------------------------------------------------------

def test_the_old_value_is_kept_because_it_is_the_training_signal(conn, roots,
                                                                 registry,
                                                                 tmp_path):
    """The classification row is overwritten, so `corrections` is the only
    remaining record of what the model actually said."""
    r = filed(conn, roots, registry, tmp_path)
    apply_correction(conn, roots, registry, r.artifact_id, entity_id="B_ATL")

    rows = corrections.history(conn, r.artifact_id)
    entity = [row for row in rows if row["field"] == "entity_id"][0]
    assert entity["old_value"] == "B_MRE"
    assert entity["new_value"] == "B_ATL"


def test_the_classification_stops_claiming_the_model_decided(conn, roots,
                                                             registry, tmp_path):
    """Nothing downstream should mistake Matthew's judgement for the model's."""
    r = filed(conn, roots, registry, tmp_path)
    apply_correction(conn, roots, registry, r.artifact_id, entity_id="B_ATL")

    row = conn.execute("SELECT decided_by, entity_id FROM classifications "
                       "WHERE artifact_id = ?", (r.artifact_id,)).fetchone()
    assert row["decided_by"] == "human"
    assert row["entity_id"] == "B_ATL"


def test_agreeing_with_the_system_is_not_a_correction(conn, roots, registry,
                                                      tmp_path):
    """Recording one would put noise into the only table that says where the
    classifier was wrong."""
    r = filed(conn, roots, registry, tmp_path)
    out = apply_correction(conn, roots, registry, r.artifact_id,
                           entity_id="B_MRE", category="VENDORS")

    assert not out.changed
    assert not out.refiled
    assert corrections.history(conn, r.artifact_id) == []


def test_the_correction_is_in_the_audit_log(conn, roots, registry, tmp_path):
    r = filed(conn, roots, registry, tmp_path)
    apply_correction(conn, roots, registry, r.artifact_id, entity_id="B_ATL")
    actions = [row["action"] for row in
               conn.execute("SELECT action FROM actions_log")]
    assert "CORRECTED" in actions


def test_provenance_survives_a_correction(conn, roots, registry, tmp_path):
    """Losing prompt_hash because someone fixed a category would destroy the
    trail that makes a filing auditable."""
    r = filed(conn, roots, registry, tmp_path)
    out = apply_correction(conn, roots, registry, r.artifact_id,
                           entity_id="B_ATL")

    meta = json.loads(out.filed_path.with_name(out.filed_path.name + ".meta.json")
                      .read_text(encoding="utf-8"))
    assert meta["prompt_hash"] == "abc123"
    assert meta["ocr_engine"] == "pdfkit-text"
    assert meta["entity_id"] == "B_ATL", "the sidecar still names the old entity"
    assert meta["corrected_by"] == "human"


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------

def test_an_impossible_correction_is_refused_before_anything_moves(
        conn, roots, registry, tmp_path):
    """A half-applied correction is worse than a refused one: the whole point
    of this path is that the database and the archive end up agreeing."""
    r = filed(conn, roots, registry, tmp_path)

    with pytest.raises(CorrectionError):
        apply_correction(conn, roots, registry, r.artifact_id,
                         category="WEDDING")          # personal-only

    assert r.filed_path.exists(), "the document moved despite the refusal"
    assert corrections.history(conn, r.artifact_id) == []


def test_an_unknown_entity_is_refused(conn, roots, registry, tmp_path):
    r = filed(conn, roots, registry, tmp_path)
    with pytest.raises(CorrectionError):
        apply_correction(conn, roots, registry, r.artifact_id,
                         entity_id="B_NOPE")
    assert r.filed_path.exists()


def test_a_missing_document_is_refused_rather_than_guessed_at(conn, roots,
                                                              registry, tmp_path):
    r = filed(conn, roots, registry, tmp_path)
    r.filed_path.unlink()

    with pytest.raises(CorrectionError) as exc:
        apply_correction(conn, roots, registry, r.artifact_id, entity_id="B_ATL")
    assert "not there" in str(exc.value)


def test_a_quarantined_document_is_not_a_correction(conn, roots, registry,
                                                    tmp_path):
    """It was never classified, so there is nothing to correct — that is a
    first classification, which is a different operation."""
    cur = conn.execute(
        "INSERT INTO artifacts (sha256, source, status, original_name, ingested_at)"
        " VALUES (?,?,?,?,?)", ("b" * 64, "photo", "QUARANTINED", "x.pdf",
                                db.now_iso()))
    with pytest.raises(CorrectionError) as exc:
        apply_correction(conn, roots, registry, int(cur.lastrowid),
                         entity_id="B_MRE")
    assert "first time" in str(exc.value)


def test_a_name_collision_refuses_rather_than_overwrites(conn, roots, registry,
                                                         tmp_path):
    """Losing a document silently is the one failure this system must not have."""
    r = filed(conn, roots, registry, tmp_path)

    dest_dir = filing.destination_dir(roots, registry, "B_ATL", "VENDORS",
                                      "invoices-received")
    dest_dir.mkdir(parents=True, exist_ok=True)
    # Pre-create exactly the name the correction will want.
    from core.pipeline import naming
    clash = dest_dir / naming.build_filename(
        doc_date="2026-09-06", entity_id="B_ATL", category="VENDORS",
        counterparty="acme-supply", descriptor="invoice", amount_cents=324000,
        sha256=db.sha256_file(r.filed_path), extension=".pdf", currency="USD")
    clash.write_bytes(b"someone else's document")

    with pytest.raises(CorrectionError) as exc:
        apply_correction(conn, roots, registry, r.artifact_id, entity_id="B_ATL")
    assert "refusing to overwrite" in str(exc.value)
    assert clash.read_bytes() == b"someone else's document"


# ---------------------------------------------------------------------------
# Due dates
# ---------------------------------------------------------------------------

def test_a_due_date_can_be_corrected(conn, roots, registry, tmp_path):
    r = filed(conn, roots, registry, tmp_path)
    tid = db.insert_task(conn, title="Acme invoice", due_date="2026-10-06",
                         entity_id="B_MRE", source_artifact=r.artifact_id)
    conn.commit()

    out = apply_correction(conn, roots, registry, r.artifact_id,
                           due_date="2026-11-01")

    assert out.changed
    assert not out.refiled, "a due date is not a filing field"
    row = conn.execute("SELECT due_date FROM tasks WHERE id = ?", (tid,)).fetchone()
    assert row["due_date"] == "2026-11-01"


def test_correcting_a_due_date_with_no_task_is_refused(conn, roots, registry,
                                                       tmp_path):
    r = filed(conn, roots, registry, tmp_path)
    with pytest.raises(CorrectionError) as exc:
        apply_correction(conn, roots, registry, r.artifact_id,
                         due_date="2026-11-01")
    assert "no task" in str(exc.value)


def test_subcategory_can_be_cleared_which_is_not_the_same_as_unset(
        conn, roots, registry, tmp_path):
    """`subcategory=None` means "file it at the category level" and has to be
    distinguishable from "don't touch the subcategory"."""
    r = filed(conn, roots, registry, tmp_path)

    untouched = apply_correction(conn, roots, registry, r.artifact_id,
                                 subcategory=UNSET)
    assert not untouched.changed

    cleared = apply_correction(conn, roots, registry, r.artifact_id,
                               subcategory=None)
    assert cleared.changed
    assert cleared.filed_path.parent.name == "VENDORS"
