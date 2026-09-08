"""Watched-folder tests.

The two that matter are the ones about files that are not ready yet — a
half-uploaded photo and an iCloud placeholder. Both look like ordinary files
to `os.listdir`, and ingesting either produces a document that is wrong in a
way nobody notices: a truncated image OCRs to nothing, and its hash never
matches the finished file, so the finished file is later filed a second time
as a different document.
"""

import json
from pathlib import Path

import pytest

from core.adapters import watchfolder
from core.adapters.lmstudio import Completion
from core.db import database as db
from core.pipeline import filing
from core.pipeline.classify import Classifier
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


@pytest.fixture
def inbox(tmp_path):
    d = tmp_path / "inbox"
    for tag in watchfolder.TAG_DIRS:
        (d / tag).mkdir(parents=True)
    return d


class FakeModel:
    def __init__(self, response):
        self._response = response

    def complete(self, **kw):
        return Completion(text=json.dumps(self._response), model="fake",
                          elapsed_s=0.01, finish_reason="stop")


BILL = {
    "domain": "PERSONAL", "entity_id": "P_HH", "category": "HOME",
    "subcategory": "utilities", "urgency": "NORMAL", "confidence": 0.88,
    "requires_reply": False, "due_date": None, "doc_date": "2026-09-01",
    "counterparty": "con-edison", "descriptor": "electric bill",
    "amount_cents": 18742, "rationale": "utility bill",
}


def clf(registry, response=BILL):
    return Classifier(registry, client=FakeModel(response))


def scan(conn, roots, registry, inbox, state, response=BILL):
    return watchfolder.process_once(conn, roots, registry, clf(registry, response),
                                    inbox, state)


# ---------------------------------------------------------------------------
# Stability — the half-written file problem
# ---------------------------------------------------------------------------

def test_a_growing_file_is_not_touched_until_it_settles(conn, roots, registry, inbox):
    """A photo still uploading must not be ingested mid-write."""
    f = inbox / "personal" / "bill.pdf"
    state = watchfolder.WatchState()

    f.write_bytes(b"partial")
    assert scan(conn, roots, registry, inbox, state) == []      # first sighting

    f.write_bytes(b"partial and then some more")                 # still growing
    assert scan(conn, roots, registry, inbox, state) == []

    # Now stable across two consecutive polls
    assert scan(conn, roots, registry, inbox, state) == []
    results = scan(conn, roots, registry, inbox, state)
    assert len(results) == 1
    assert results[0].status == "FILED"


def test_zero_byte_files_are_ignored(conn, roots, registry, inbox):
    (inbox / "personal" / "empty.pdf").touch()
    state = watchfolder.WatchState()
    for _ in range(4):
        assert scan(conn, roots, registry, inbox, state) == []


# ---------------------------------------------------------------------------
# Routing and retirement
# ---------------------------------------------------------------------------

def test_subfolder_supplies_the_personal_business_tag(inbox):
    assert watchfolder.tag_for(inbox / "business" / "x.pdf", inbox) == "BUSINESS"
    assert watchfolder.tag_for(inbox / "personal" / "x.pdf", inbox) == "PERSONAL"
    assert watchfolder.tag_for(inbox / "x.pdf", inbox) is None


def test_handled_files_are_moved_aside_never_deleted(conn, roots, registry, inbox):
    f = inbox / "personal" / "bill.pdf"
    f.write_bytes(b"an electric bill from con edison")
    state = watchfolder.WatchState()

    for _ in range(3):
        scan(conn, roots, registry, inbox, state)

    assert not f.exists(), "file was left in the inbox and will be rescanned"
    moved = list((inbox / "_done").glob("*.pdf"))
    assert len(moved) == 1, "the file was deleted rather than retired"


def test_retired_files_are_not_rescanned(conn, roots, registry, inbox):
    f = inbox / "personal" / "bill.pdf"
    f.write_bytes(b"an electric bill")
    state = watchfolder.WatchState()
    for _ in range(3):
        scan(conn, roots, registry, inbox, state)

    assert scan(conn, roots, registry, inbox, state) == []
    assert len(list(roots.archive.rglob("*.pdf"))) == 1


def test_a_name_collision_in_done_does_not_overwrite(conn, roots, registry, inbox):
    """Two photos both called IMG_0001.jpg is the normal case, not an edge one."""
    state = watchfolder.WatchState()
    for content in (b"first bill", b"second different bill"):
        f = inbox / "personal" / "IMG_0001.pdf"
        f.write_bytes(content)
        for _ in range(3):
            scan(conn, roots, registry, inbox, state)

    assert len(list((inbox / "_done").glob("*.pdf"))) == 2


# ---------------------------------------------------------------------------
# Failure containment
# ---------------------------------------------------------------------------

def test_quarantined_files_go_to_failed_not_done(conn, roots, registry, inbox):
    f = inbox / "personal" / "unclear.pdf"
    f.write_bytes(b"illegible scrawl")
    state = watchfolder.WatchState()

    for _ in range(3):
        scan(conn, roots, registry, inbox, state, {**BILL, "confidence": 0.2})

    assert list((inbox / "_failed").glob("*.pdf"))
    assert not list((inbox / "_done").glob("*.pdf"))


def test_one_bad_file_does_not_stop_the_others(conn, roots, registry, inbox, monkeypatch):
    """A daemon that dies on one malformed file stops processing everything."""
    good = inbox / "personal" / "good.pdf"
    bad = inbox / "personal" / "bad.pdf"
    good.write_bytes(b"a perfectly good electric bill")
    bad.write_bytes(b"boom")

    real = watchfolder.ingest.ingest_file

    def explode(*a, **kw):
        if Path(kw.get("source_ref") or a[4]).name == "bad.pdf":
            raise RuntimeError("simulated failure")
        return real(*a, **kw)

    monkeypatch.setattr(watchfolder.ingest, "ingest_file", explode)

    state = watchfolder.WatchState()
    results = []
    for _ in range(3):
        results = scan(conn, roots, registry, inbox, state)

    assert any(r.status == "FILED" for r in results), "the good file was not processed"
    actions = [r["action"] for r in conn.execute("SELECT action FROM actions_log")]
    assert "INGEST_ERROR" in actions, "the failure was swallowed without a trace"


# ---------------------------------------------------------------------------
# iCloud placeholders
# ---------------------------------------------------------------------------

def test_classic_icloud_stub_is_recognised(inbox):
    stub = inbox / "personal" / ".bill.pdf.icloud"
    stub.write_bytes(b"placeholder")
    assert watchfolder.is_dataless(stub)


def test_a_normal_local_file_is_not_dataless(inbox):
    f = inbox / "personal" / "bill.pdf"
    f.write_bytes(b"real bytes on disk")
    assert not watchfolder.is_dataless(f)


def test_dot_underscore_resource_forks_are_skipped(inbox):
    (inbox / "personal" / "._bill.pdf").write_bytes(b"resource fork")
    assert watchfolder.candidates(inbox) == []
