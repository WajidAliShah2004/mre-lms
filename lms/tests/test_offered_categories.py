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
