"""What the model is allowed to choose from, and what leaves the queue.

Both defects here came from the same live run: the model was asked a question
it could not answer correctly, and the review queue then described work that
had already been done.
"""

from pathlib import Path

import pytest

from core.db import database as db
from core.pipeline import classify, filing
from core.pipeline.classify import Artifact
from core.pipeline.registry import load_registry

CONFIG = Path(__file__).resolve().parents[1] / "config"


@pytest.fixture
def reg():
    return load_registry(CONFIG)


# ---------------------------------------------------------------------------
# Only offer answers that can validate
# ---------------------------------------------------------------------------

def test_a_business_entity_is_not_offered_personal_categories(reg):
    """The quarantine reason from the live run:

        category 'VEHICLES' is not valid for 'B_MRE'

    A GEICO insurance card and a Toyota signature page arrived at
    matthew@mrecai.com, so recipient_email routed them to a business. The
    prompt then listed the PERSONAL tree as well, the model reasonably chose
    VEHICLES, and validate_category refused it.

    The model was not wrong about the documents. It was answering a question we
    asked badly: twenty categories, nine of them guaranteed to be rejected.
    """
    business = [e for e in reg.entities.values() if e.kind == "business"]
    if not business:
        pytest.skip("no business entities")

    offered = classify._offered_categories(reg, business[0])
    personal_only = set(reg.personal_categories) - set(reg.business_categories)
    assert personal_only, "the trees are identical; this test proves nothing"

    for name in personal_only:
        assert name not in offered, (
            f"{name} is offered to a business entity and cannot ever validate")
    for name in reg.business_categories:
        assert name in offered


def test_a_person_is_not_offered_business_categories(reg):
    people = [e for e in reg.entities.values() if e.kind == "person"]
    if not people:
        pytest.skip("no person entities")

    offered = classify._offered_categories(reg, people[0])
    business_only = set(reg.business_categories) - set(reg.personal_categories)
    assert business_only, "the trees are identical; this test proves nothing"

    for name in business_only:
        assert name not in offered


def test_both_trees_are_offered_when_routing_did_not_decide(reg):
    """Choosing between them IS the question when nothing routed."""
    offered = classify._offered_categories(reg, None)
    for name in set(reg.personal_categories) | set(reg.business_categories):
        assert name in offered


def test_every_offered_category_would_validate(reg):
    """The general property, rather than the one case that failed.

    Whatever the model picks from the menu it is shown must be capable of
    being accepted. Anything else is a quarantine we caused.
    """
    from core.pipeline.registry import RegistryError

    for eid, ent in reg.entities.items():
        if ent.kind == "system":
            continue
        offered = classify._offered_categories(reg, ent)
        for name in reg.categories_for(eid):
            assert name in offered, f"{eid} cannot reach its own {name}"
        # and nothing outside the entity's own tree is on the menu
        others = (set(reg.personal_categories) | set(reg.business_categories)
                  ) - set(reg.categories_for(eid))
        for name in others:
            if name in offered:
                with pytest.raises(RegistryError):
                    reg.validate_category(eid, name, None)


def test_the_prompt_actually_uses_the_narrowed_list(reg):
    """_offered_categories being right is no use if render_prompt ignores it."""
    business = [e for e in reg.entities.values() if e.kind == "business"]
    if not business:
        pytest.skip("no business entities")

    art = Artifact(body="a document", subject="s")
    system, _, _ = classify.render_prompt(reg, art, routed=business[0])
    personal_only = set(reg.personal_categories) - set(reg.business_categories)
    for name in personal_only:
        assert name not in system


# ---------------------------------------------------------------------------
# The review queue describes the present
# ---------------------------------------------------------------------------

def test_filing_retires_the_quarantine_copy(tmp_path, reg):
    """A document that quarantined on Tuesday and filed on Wednesday left its
    Tuesday copy in the queue, sidecar and all, saying `most likely a blank
    scan` about a document now correctly filed in FINANCE."""
    conn = db.connect(tmp_path / "lms.db")
    roots = filing.StorageRoots(archive=tmp_path / "a", originals=tmp_path / "o",
                                quarantine=tmp_path / "q")
    roots.ensure()

    src = tmp_path / "policy.txt"
    src.write_text("an insurance document", encoding="utf-8")
    sha = db.sha256_file(src)

    filing.quarantine_artifact(conn, roots, source_path=src, sha256=sha,
                               source="email", reason="low confidence")
    assert list(roots.quarantine.glob(f"{sha[:8]}__*")), "nothing quarantined"

    eid = sorted(e for e, ent in reg.entities.items() if ent.kind != "system")[0]
    filing.file_artifact(conn, roots, reg, source_path=src, sha256=sha,
                         source="email", entity_id=eid,
                         category=sorted(reg.categories_for(eid))[0],
                         doc_date="2026-09-09")

    assert not list(roots.quarantine.glob(f"{sha[:8]}__*")), (
        "the review queue still shows a document that has filed")
    assert list((roots.quarantine / "_resolved").glob(f"{sha[:8]}__*")), (
        "it must be retired, not deleted — the record that it was once "
        "refused, and why, is worth keeping")
    conn.close()


def test_a_rerendered_message_does_not_nag_forever(tmp_path, reg):
    """The phantom in WAITING ON YOU.

    Identity is the sha256 of the bytes, and for mail those bytes are a
    RENDERING. Improving the renderer — dropping signature images from the
    Attachments: line — changed the hash, so the State Farm reply filed as a
    new row while the old one stayed at SUSPECTED_PHISHING with no filed_path
    and nothing that would ever resolve it. The brief showed it under WAITING
    ON YOU, permanently, describing a document sitting correctly filed.
    """
    conn = db.connect(tmp_path / "lms.db")
    roots = filing.StorageRoots(archive=tmp_path / "a", originals=tmp_path / "o",
                                quarantine=tmp_path / "q")
    roots.ensure()
    ref = "1a0879e46550679e"

    old = tmp_path / "v1.txt"
    old.write_text("From: a\nAttachments: image001.gif\n\nbody", encoding="utf-8")
    old_sha = db.sha256_file(old)
    filing.quarantine_artifact(conn, roots, source_path=old, sha256=old_sha,
                               source="email", reason="SUSPECTED_PHISHING: x",
                               source_ref=ref)

    new = tmp_path / "v2.txt"
    new.write_text("From: a\n\nbody", encoding="utf-8")
    eid = sorted(e for e, ent in reg.entities.items() if ent.kind != "system")[0]
    filing.file_artifact(conn, roots, reg, source_path=new,
                         sha256=db.sha256_file(new), source="email",
                         source_ref=ref, entity_id=eid,
                         category=sorted(reg.categories_for(eid))[0],
                         doc_date="2026-09-09")

    stale = conn.execute("SELECT status FROM artifacts WHERE sha256 = ?",
                         (old_sha,)).fetchone()
    assert stale["status"] == "DUPLICATE", (
        "the superseded rendering still claims to need attention")
    assert not list(roots.quarantine.glob(f"{old_sha[:8]}__*")), (
        "its file is still in the review queue")
    conn.close()


def test_a_different_message_is_never_superseded(tmp_path, reg):
    """source_ref is exact — the Gmail message id. Two rows sharing one are
    two renderings of one thing. Two rows with DIFFERENT ones are two
    documents, and quarantining one must not clear the other."""
    conn = db.connect(tmp_path / "lms.db")
    roots = filing.StorageRoots(archive=tmp_path / "a", originals=tmp_path / "o",
                                quarantine=tmp_path / "q")
    roots.ensure()

    other = tmp_path / "other.txt"
    other.write_text("a genuinely suspicious message", encoding="utf-8")
    other_sha = db.sha256_file(other)
    filing.quarantine_artifact(conn, roots, source_path=other, sha256=other_sha,
                               source="email", reason="SUSPECTED_PHISHING: y",
                               source_ref="some-other-message-id")

    doc = tmp_path / "doc.txt"
    doc.write_text("an unrelated document", encoding="utf-8")
    eid = sorted(e for e, ent in reg.entities.items() if ent.kind != "system")[0]
    filing.file_artifact(conn, roots, reg, source_path=doc,
                         sha256=db.sha256_file(doc), source="email",
                         source_ref="1a0879e46550679e", entity_id=eid,
                         category=sorted(reg.categories_for(eid))[0],
                         doc_date="2026-09-09")

    row = conn.execute("SELECT status FROM artifacts WHERE sha256 = ?",
                       (other_sha,)).fetchone()
    assert row["status"] == "QUARANTINED", (
        "an unrelated message in the review queue was cleared")
    assert list(roots.quarantine.glob(f"{other_sha[:8]}__*")), (
        "its file was retired out of the queue too")
    conn.close()


def test_a_photograph_without_a_source_ref_supersedes_nothing(tmp_path, reg):
    """A phone names files `IMG_0001.HEIC` and reuses the name. There the
    filename is not an identity, so nothing may be inferred from it."""
    conn = db.connect(tmp_path / "lms.db")
    roots = filing.StorageRoots(archive=tmp_path / "a", originals=tmp_path / "o",
                                quarantine=tmp_path / "q")
    roots.ensure()
    assert filing.supersede_earlier_versions(conn, roots, None, keep=1) == []
    assert filing.supersede_earlier_versions(conn, roots, "", keep=1) == []
    conn.close()


def test_a_document_already_filed_still_gets_its_queue_copy_retired(tmp_path, reg):
    """The duplicate short-circuit used to return before the clean-up.

    On the Mac that left four filed documents in the review queue with an
    empty _resolved/ beside them: they had quarantined, then filed, and every
    run since returned DUPLICATE and went home.
    """
    conn = db.connect(tmp_path / "lms.db")
    roots = filing.StorageRoots(archive=tmp_path / "a", originals=tmp_path / "o",
                                quarantine=tmp_path / "q")
    roots.ensure()

    doc = tmp_path / "policy.txt"
    doc.write_text("an insurance document", encoding="utf-8")
    sha = db.sha256_file(doc)
    eid = sorted(e for e, ent in reg.entities.items() if ent.kind != "system")[0]
    cat = sorted(reg.categories_for(eid))[0]

    filing.file_artifact(conn, roots, reg, source_path=doc, sha256=sha,
                         source="email", entity_id=eid, category=cat,
                         doc_date="2026-09-09")

    # A stale copy from before the clean-up existed.
    (roots.quarantine / f"{sha[:8]}__policy.txt").write_text("stale",
                                                             encoding="utf-8")

    # Second sighting: takes the duplicate path.
    filing.file_artifact(conn, roots, reg, source_path=doc, sha256=sha,
                         source="email", entity_id=eid, category=cat,
                         doc_date="2026-09-09")

    assert not list(roots.quarantine.glob(f"{sha[:8]}__*")), (
        "a filed document is still sitting in the review queue")
    conn.close()


def test_the_queue_is_tidied_on_the_path_that_actually_runs(tmp_path, reg):
    """Through ingest_file, which is what the poller calls.

    The first version of this fix went into filing.file_artifact's duplicate
    branch — and changed nothing, because ingest_file has its OWN duplicate
    short-circuit and returns before filing is reached. On the Mac, _resolved/
    held exactly the two documents that had filed fresh; everything that came
    back through ingest's early return was still in the queue.

    So this test goes through the front door. A test one layer too low is how
    the first fix passed while the queue stayed full.
    """
    import json

    from core.pipeline import ingest
    from core.pipeline.classify import Classifier

    conn = db.connect(tmp_path / "lms.db")
    roots = filing.StorageRoots(archive=tmp_path / "a", originals=tmp_path / "o",
                                quarantine=tmp_path / "q")
    roots.ensure()

    eid = sorted(e for e, ent in reg.entities.items() if ent.kind != "system")[0]
    answer = {"domain": "BUSINESS", "entity_id": eid,
              "category": sorted(reg.categories_for(eid))[0], "subcategory": None,
              "urgency": "NORMAL", "confidence": 0.9, "requires_reply": False,
              "due_date": None, "counterparty": "State Farm",
              "descriptor": "policy notice", "amount_cents": None,
              "currency": "USD", "rationale": "a policy notice"}

    class Stub:
        def complete(self, **kw):
            return type("C", (), {"json": lambda s=None: json.loads(json.dumps(answer)),
                                  "model": "stub"})()

    clf = Classifier(reg, client=Stub())

    doc = tmp_path / "reply.eml.txt"
    doc.write_text("From: matthew@mrecai.com\n\nPolicy notice reply, attached.",
                   encoding="utf-8")
    ref = "1a0879e46550679e"

    first = ingest.ingest_file(conn, roots, reg, clf, doc,
                               source="email", source_ref=ref)
    assert first.status == "FILED", first.reason

    # A stale copy left by an earlier run, before any of this existed.
    stale = roots.quarantine / f"{db.sha256_file(doc)[:8]}__reply.eml.txt"
    stale.write_text("stale", encoding="utf-8")

    second = ingest.ingest_file(conn, roots, reg, clf, doc,
                                source="email", source_ref=ref)
    assert second.status == "DUPLICATE"
    assert not stale.exists(), (
        "ingest's duplicate short-circuit returned without tidying the queue")
    conn.close()


def test_retiring_never_deletes(tmp_path, reg):
    roots = filing.StorageRoots(archive=tmp_path / "a", originals=tmp_path / "o",
                                quarantine=tmp_path / "q")
    roots.ensure()
    sha = "abcdef1234567890" * 4
    p = roots.quarantine / f"{sha[:8]}__thing.pdf"
    p.write_bytes(b"bytes that must survive")

    filing.retire_quarantine_copy(roots, sha)

    survivors = list((roots.quarantine / "_resolved").glob("*"))
    assert [s.read_bytes() for s in survivors] == [b"bytes that must survive"]


def test_retiring_an_absent_copy_is_not_an_error(tmp_path):
    """Most filings never quarantined."""
    roots = filing.StorageRoots(archive=tmp_path / "a", originals=tmp_path / "o",
                                quarantine=tmp_path / "q")
    roots.ensure()
    assert filing.retire_quarantine_copy(roots, "0" * 64) == []
