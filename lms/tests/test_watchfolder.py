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

# Fixtures are .txt, not .pdf.
#
# They used to be .pdf files containing plain-text bytes — a fiction that
# nothing checked, because ingest never tried to read them. D-024 added the
# rule that a document nobody could read is quarantined rather than
# classified, and these tests started failing: correctly, since the pipeline
# still cannot read a PDF. .txt makes the fixture honest and exercises the
# real extraction path end to end.


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

def age(path: Path, seconds: int) -> None:
    """Backdate mtime, so a settled file can be simulated without sleeping."""
    import os
    st = path.stat()
    os.utime(path, (st.st_atime, st.st_mtime - seconds))


def test_a_file_still_being_written_is_not_touched(conn, roots, registry, inbox):
    """A photo mid-upload has a fresh mtime and must be left alone."""
    f = inbox / "personal" / "bill.txt"
    f.write_bytes(b"partial upload")
    assert scan(conn, roots, registry, inbox, watchfolder.WatchState()) == []


def test_a_quiet_file_is_ingested(conn, roots, registry, inbox):
    f = inbox / "personal" / "bill.txt"
    f.write_bytes(b"a complete electric bill from con edison")
    age(f, watchfolder.QUIET_SECONDS + 1)

    results = scan(conn, roots, registry, inbox, watchfolder.WatchState())
    assert len(results) == 1
    assert results[0].status == "FILED"


def test_settledness_survives_a_fresh_process(conn, roots, registry, inbox):
    """The bug the unit tests missed and the Mac caught.

    Stability used to be an in-memory counter, so every `--once` invocation
    started from zero and could never reach the threshold. Three scans in
    three separate processes ingested nothing at all.

    A NEW WatchState per scan simulates exactly that. It must still work.
    """
    f = inbox / "personal" / "bill.txt"
    f.write_bytes(b"an electric bill")
    age(f, watchfolder.QUIET_SECONDS + 1)

    results = scan(conn, roots, registry, inbox, watchfolder.WatchState())
    assert len(results) == 1, "a fresh process could not ingest a settled file"


def test_an_empty_file_that_has_settled_is_reported_not_ignored(conn, roots,
                                                                registry, inbox):
    """This test used to be `test_zero_byte_files_are_ignored`, and it asserted
    the defect as though it were the design.

    A zero-byte file was skipped with a bare `continue`, ahead of the settle
    check and with no log line — so it was re-skipped on every scan forever and
    left no trace anywhere. Found when `cupsfilter` wrote a 0-byte PDF into the
    inbox and the watcher printed neither FILED nor QUARANTINED. Silence is the
    worst outcome available: a document that is loudly refused gets dealt with,
    and one that vanishes does not.
    """
    f = inbox / "personal" / "empty.txt"
    f.touch()
    age(f, watchfolder.QUIET_SECONDS + 1)

    results = scan(conn, roots, registry, inbox, watchfolder.WatchState())

    assert [r.status for r in results] == ["EMPTY"]
    assert not results[0].ok
    assert "no content" in results[0].reason
    actions = [r["action"] for r in conn.execute("SELECT action FROM actions_log")]
    assert "EMPTY_FILE" in actions, "the skip left no trace in the log"


def test_an_empty_file_is_moved_aside_so_it_is_not_seen_again(conn, roots,
                                                              registry, inbox):
    """Retiring it is what stops the silent re-skip on every future scan."""
    f = inbox / "personal" / "empty.txt"
    f.touch()
    age(f, watchfolder.QUIET_SECONDS + 1)
    state = watchfolder.WatchState()

    scan(conn, roots, registry, inbox, state)
    assert not f.exists(), "left in the inbox to be skipped again forever"
    assert (inbox / "_failed" / "empty.txt").exists(), "the bytes were not kept"
    assert scan(conn, roots, registry, inbox, state) == []


def test_a_file_still_being_written_is_not_called_empty(conn, roots, registry, inbox):
    """The settle check must come FIRST.

    A file created and not yet written to is momentarily zero bytes. Calling
    that a failed delivery would retire a document mid-flight — the original
    bug's guard was right about this case and wrong about every other one.
    """
    f = inbox / "personal" / "arriving.txt"
    f.touch()                                     # brand new, size 0
    assert scan(conn, roots, registry, inbox, watchfolder.WatchState()) == []
    assert f.exists(), "a file still being written was retired"


def test_is_settled_reads_mtime_not_a_counter(inbox):
    f = inbox / "personal" / "x.txt"
    f.write_bytes(b"data")
    assert not watchfolder.is_settled(f)
    age(f, watchfolder.QUIET_SECONDS + 1)
    assert watchfolder.is_settled(f)


def test_missing_file_is_not_settled(inbox):
    assert not watchfolder.is_settled(inbox / "personal" / "gone.txt")


# ---------------------------------------------------------------------------
# Routing and retirement
# ---------------------------------------------------------------------------

def test_subfolder_supplies_the_personal_business_tag(inbox):
    assert watchfolder.tag_for(inbox / "business" / "x.txt", inbox) == "BUSINESS"
    assert watchfolder.tag_for(inbox / "personal" / "x.txt", inbox) == "PERSONAL"
    assert watchfolder.tag_for(inbox / "x.txt", inbox) is None


def test_handled_files_are_moved_aside_never_deleted(conn, roots, registry, inbox):
    f = inbox / "personal" / "bill.txt"
    f.write_bytes(b"an electric bill from con edison")
    age(f, watchfolder.QUIET_SECONDS + 1)
    scan(conn, roots, registry, inbox, watchfolder.WatchState())

    assert not f.exists(), "file was left in the inbox and will be rescanned"
    moved = list((inbox / "_done").glob("*.txt"))
    assert len(moved) == 1, "the file was deleted rather than retired"


def test_retired_files_are_not_rescanned(conn, roots, registry, inbox):
    f = inbox / "personal" / "bill.txt"
    f.write_bytes(b"an electric bill")
    age(f, watchfolder.QUIET_SECONDS + 1)
    state = watchfolder.WatchState()
    scan(conn, roots, registry, inbox, state)

    assert scan(conn, roots, registry, inbox, state) == []
    # Count sidecars, not *.txt. Filing writes the extracted text beside the
    # document, so with .txt fixtures a *.txt glob counts both and reads as a
    # double-file. One .meta.json is written per filed artifact, so it says
    # what this test actually means: exactly one document was filed.
    assert len(list(roots.archive.rglob("*.meta.json"))) == 1


def test_a_name_collision_in_done_does_not_overwrite(conn, roots, registry, inbox):
    """Two photos both called IMG_0001.jpg is the normal case, not an edge one."""
    for content in (b"first bill", b"second different bill"):
        f = inbox / "personal" / "IMG_0001.txt"
        f.write_bytes(content)
        age(f, watchfolder.QUIET_SECONDS + 1)
        scan(conn, roots, registry, inbox, watchfolder.WatchState())

    assert len(list((inbox / "_done").glob("*.txt"))) == 2


# ---------------------------------------------------------------------------
# Failure containment
# ---------------------------------------------------------------------------

def test_quarantined_files_go_to_failed_not_done(conn, roots, registry, inbox):
    f = inbox / "personal" / "unclear.txt"
    f.write_bytes(b"illegible scrawl")
    age(f, watchfolder.QUIET_SECONDS + 1)
    scan(conn, roots, registry, inbox, watchfolder.WatchState(),
         {**BILL, "confidence": 0.2})

    assert list((inbox / "_failed").glob("*.txt"))
    assert not list((inbox / "_done").glob("*.txt"))


def test_one_bad_file_does_not_stop_the_others(conn, roots, registry, inbox, monkeypatch):
    """A daemon that dies on one malformed file stops processing everything."""
    good = inbox / "personal" / "good.txt"
    bad = inbox / "personal" / "bad.txt"
    good.write_bytes(b"a perfectly good electric bill")
    bad.write_bytes(b"boom")
    age(good, watchfolder.QUIET_SECONDS + 1)
    age(bad, watchfolder.QUIET_SECONDS + 1)

    real = watchfolder.ingest.ingest_file

    def explode(*a, **kw):
        if Path(kw.get("source_ref") or a[4]).name == "bad.txt":
            raise RuntimeError("simulated failure")
        return real(*a, **kw)

    monkeypatch.setattr(watchfolder.ingest, "ingest_file", explode)

    results = scan(conn, roots, registry, inbox, watchfolder.WatchState())

    assert any(r.status == "FILED" for r in results), "the good file was not processed"
    actions = [r["action"] for r in conn.execute("SELECT action FROM actions_log")]
    assert "INGEST_ERROR" in actions, "the failure was swallowed without a trace"


# ---------------------------------------------------------------------------
# iCloud placeholders
# ---------------------------------------------------------------------------

def test_classic_icloud_stub_is_recognised(inbox):
    stub = inbox / "personal" / ".bill.txt.icloud"
    stub.write_bytes(b"placeholder")
    assert watchfolder.is_dataless(stub)


def test_a_normal_local_file_is_not_dataless(inbox):
    f = inbox / "personal" / "bill.txt"
    f.write_bytes(b"real bytes on disk")
    assert not watchfolder.is_dataless(f)


def test_dot_underscore_resource_forks_are_skipped(inbox):
    (inbox / "personal" / "._bill.txt").write_bytes(b"resource fork")
    assert watchfolder.candidates(inbox) == []
