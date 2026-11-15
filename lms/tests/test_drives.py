"""The drive engine (R5, D-083): index everything, file settled new documents."""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from core.adapters.drive import Listing
from core.adapters.lmstudio import Completion
from core.db import database as db
from core.pipeline import drives, filing
from core.pipeline.classify import Classifier
from core.pipeline.ingest import IngestResult
from core.pipeline.registry import load_registry
from tests.drive_fixture import SID, FakeDrive

ET = ZoneInfo("America/New_York")
CONFIG = Path(__file__).resolve().parents[1] / "config"
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=ET)


class FakeModel:
    def complete(self, **kw):
        return Completion(text=json.dumps({
            "domain": "BUSINESS", "entity_id": "B_MRE", "category": "VENDORS",
            "subcategory": "invoices-received", "urgency": "NORMAL",
            "confidence": 0.93, "requires_reply": False, "due_date": None,
            "counterparty": "acme-supply", "descriptor": "drive document",
            "amount_cents": None, "rationale": "vendor"}),
            model="fake", elapsed_s=0.01, finish_reason="stop")


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "lms.db")
    yield c
    c.close()


@pytest.fixture
def pipe(tmp_path):
    roots = filing.StorageRoots(archive=tmp_path / "archive",
                                originals=tmp_path / "_originals",
                                quarantine=tmp_path / "quarantine")
    roots.ensure()
    registry = load_registry(CONFIG)
    return roots, registry, Classifier(registry, client=FakeModel())


def source(drive, **kw):
    return drives.DriveSource(SID, "Test Drive", drive, drive, **kw)


def begin(conn, day="2026-11-01"):
    conn.execute("INSERT INTO drive_cursors (source_id, cursor, started_on, updated_at) "
                 "VALUES (?, NULL, ?, ?)", (SID, day, db.now_iso()))
    conn.commit()


def run(conn, pipe, src, now=NOW, **kw):
    roots, registry, clf = pipe
    return drives.run_pass(src, conn, roots, registry, clf, now=now,
                           tmp_root=roots.archive.parent / "tmp", **kw)


def row(conn, key):
    return conn.execute("SELECT * FROM drive_files WHERE source_id = ? AND file_key = ?",
                        (SID, key)).fetchone()


def count(conn, sql, *args):
    return conn.execute(sql, args).fetchone()[0]


def test_the_first_pass_indexes_everything_and_sets_the_start_date(conn, pipe):
    d = FakeDrive()
    d.put("old.txt", b"Old invoice from Acme for 300 dollars", modified="2026-10-01T09:00:00-04:00")
    d.put("clip.mp4", b"video", modified="2026-11-18T09:00:00-05:00")
    r = run(conn, pipe, source(d))
    assert (r.result, r.listed, r.new, r.filed) == ("OK", 2, 2, 0)
    assert drives.start_date(conn, SID) == "2026-11-20"
    assert count(conn, "SELECT COUNT(*) FROM drive_files") == 2
    assert d.fetched == []


def test_settled_new_documents_are_filed_and_the_rest_only_indexed(conn, pipe):
    begin(conn)
    d = FakeDrive()
    d.put("Clients/lease.txt", b"Lease for 12 Main St, rent 2400 dollars a month",
          modified="2026-11-18T09:00:00-05:00")
    d.put("fresh.txt", b"Draft letter still being written", modified="2026-11-20T10:00:00-05:00")
    d.put("clip.mp4", b"video bytes", modified="2026-11-18T09:00:00-05:00")
    d.put("big.pdf", b"x", size=drives.MAX_BYTES + 1, modified="2026-11-18T09:00:00-05:00")
    d.put("theirs.txt", b"Quote from a broker for the renewal",
          modified="2026-11-18T09:00:00-05:00", area="shared_with_me")
    d.put("before.txt", b"An October statement", modified="2026-10-20T09:00:00-04:00")
    r = run(conn, pipe, source(d))
    assert (r.result, r.filed, r.too_large, r.duplicates) == ("OK", 1, 1, 0)
    assert d.fetched == ["Clients/lease.txt"]
    lease = row(conn, "Clients/lease.txt")
    assert lease["filed_revision"] == lease["revision"]
    assert lease["artifact_id"] and lease["sha256"]
    art = conn.execute("SELECT source, source_ref FROM artifacts WHERE id = ?",
                       (lease["artifact_id"],)).fetchone()
    assert art["source"] == SID and art["source_ref"].startswith("Clients/lease.txt@")
    check = conn.execute("SELECT result, items FROM source_checks WHERE source_id = ?",
                         (SID,)).fetchone()
    assert (check["result"], check["items"]) == ("OK", 6)
    assert count(conn, "SELECT COUNT(*) FROM actions_log WHERE action = 'DRIVES_CHECKED'") == 1
    assert "over 100 MB" in r.summary()
    assert list((pipe[0].archive.parent / "tmp").iterdir()) == []


def test_a_filed_revision_is_not_filed_again_but_a_new_one_is(conn, pipe):
    begin(conn)
    d = FakeDrive()
    d.put("a.txt", b"Invoice 100 from Acme Supply for 500 dollars",
          modified="2026-11-17T09:00:00-05:00")
    assert run(conn, pipe, source(d)).filed == 1
    assert run(conn, pipe, source(d)).filed == 0
    d.put("a.txt", b"Invoice 100 from Acme Supply for 650 dollars, corrected",
          modified="2026-11-18T09:00:00-05:00")
    r = run(conn, pipe, source(d))
    assert (r.changed, r.filed) == (1, 1)
    assert d.fetched == ["a.txt", "a.txt"]
    assert count(conn, "SELECT COUNT(*) FROM artifacts WHERE source = ?", SID) == 2


def test_bytes_already_in_the_archive_are_a_duplicate_not_a_second_copy(conn, pipe):
    begin(conn)
    d = FakeDrive()
    body = b"Insurance policy renewal notice for policy 778"
    d.put("one.txt", body, modified="2026-11-17T09:00:00-05:00")
    d.put("copy.txt", body, modified="2026-11-17T10:00:00-05:00")
    r = run(conn, pipe, source(d))
    assert (r.filed, r.duplicates) == (1, 1)
    assert row(conn, "copy.txt")["filed_revision"] is not None
    assert count(conn, "SELECT COUNT(*) FROM artifacts WHERE source = ?", SID) == 1


def test_a_file_that_cannot_be_fetched_is_tried_three_times(conn, pipe):
    begin(conn)
    d = FakeDrive()
    d.put("stuck.txt", b"Unreachable document text", modified="2026-11-17T09:00:00-05:00")
    d.fail_fetch.add("stuck.txt")
    results = [run(conn, pipe, source(d)) for _ in range(4)]
    assert [r.result for r in results] == ["PARTIAL", "PARTIAL", "PARTIAL", "OK"]
    assert row(conn, "stuck.txt")["attempts"] == 3
    assert "no bytes" in row(conn, "stuck.txt")["last_error"]
    assert count(conn, "SELECT COUNT(*) FROM actions_log WHERE action = 'DRIVE_FILE_FAILED'") == 3


def test_a_new_revision_gets_fresh_attempts(conn, pipe):
    begin(conn)
    d = FakeDrive()
    d.put("stuck.txt", b"Unreachable document text", modified="2026-11-16T09:00:00-05:00")
    d.fail_fetch.add("stuck.txt")
    for _ in range(3):
        run(conn, pipe, source(d))
    d.fail_fetch.clear()
    d.put("stuck.txt", b"Now reachable document text", modified="2026-11-17T09:00:00-05:00")
    assert run(conn, pipe, source(d)).filed == 1


def test_a_listing_that_fails_fails_the_pass(conn, pipe):
    d = FakeDrive()
    d.fail_list = "grant revoked: re-authorise"
    r = run(conn, pipe, source(d))
    assert r.result == "FAILED" and "re-authorise" in r.summary()
    assert conn.execute("SELECT result FROM source_checks").fetchone()["result"] == "FAILED"
    assert count(conn, "SELECT COUNT(*) FROM actions_log WHERE action = 'DRIVES_FAILED'") == 1


def test_deleted_files_are_marked_never_removed(conn, pipe):
    d = FakeDrive()
    for k in ("keep.txt", "Old/a.txt", "Old/b.txt", "gone.txt"):
        d.put(k, k.encode(), modified="2026-10-01T09:00:00-04:00")
    run(conn, pipe, source(d))
    del d.files["Old/a.txt"], d.files["Old/b.txt"]
    d.full, d.deleted = False, ["Old"]
    assert run(conn, pipe, source(d)).deleted == 2
    d.full, d.deleted = True, []
    del d.files["gone.txt"]
    assert run(conn, pipe, source(d)).deleted == 1
    assert count(conn, "SELECT COUNT(*) FROM drive_files") == 4
    assert row(conn, "keep.txt")["deleted_at"] is None
    assert row(conn, "gone.txt")["deleted_at"] is not None


def test_an_incremental_deletion_is_applied_before_the_files_listed_with_it(conn, pipe):
    d = FakeDrive()
    for k in ("Old/a.txt", "Old/b.txt"):
        d.put(k, k.encode(), modified="2026-10-01T09:00:00-04:00")
    run(conn, pipe, source(d))
    del d.files["Old/b.txt"]
    d.full, d.deleted = False, ["Old"]
    run(conn, pipe, source(d))
    assert row(conn, "Old/a.txt")["deleted_at"] is None
    assert row(conn, "Old/b.txt")["deleted_at"] is not None


def test_one_key_listed_twice_indexes_one_row_with_the_last_revision(conn, pipe):
    d = FakeDrive()
    a = d.put("a.txt", b"a", modified="2026-10-01T09:00:00-04:00", revision="r1")
    b = d.put("a.txt", b"a", modified="2026-10-01T09:00:00-04:00", revision="r2")
    d.list = lambda cursor: Listing((a, b), (), True, "c1")
    res = run(conn, pipe, source(d))
    assert res.result == "OK", res.error
    assert count(conn, "SELECT COUNT(*) FROM drive_files") == 1
    assert row(conn, "a.txt")["revision"] == "r2"


def test_the_cursor_is_kept_for_the_next_listing(conn, pipe):
    d = FakeDrive()
    d.cursor = "after-pass-1"
    seen = []
    real = d.list
    d.list = lambda cursor: (seen.append(cursor), real(cursor))[1]
    run(conn, pipe, source(d))
    run(conn, pipe, source(d))
    assert seen == [None, "after-pass-1"]


def test_a_source_that_files_nothing_only_indexes(conn, pipe):
    begin(conn)
    d = FakeDrive()
    d.put("a.txt", b"Statement for October from the bank", modified="2026-11-17T09:00:00-05:00")
    r = run(conn, pipe, source(d, files_documents=False))
    assert (r.listed, r.filed) == (1, 0) and d.fetched == []


def test_a_halt_stops_before_the_next_file(conn, pipe):
    begin(conn)
    d = FakeDrive()
    d.put("a.txt", b"First document text here", modified="2026-11-17T09:00:00-05:00")
    d.put("b.txt", b"Second document text here", modified="2026-11-17T10:00:00-05:00")
    r = run(conn, pipe, source(d), halted=lambda: len(d.fetched) >= 1)
    assert d.fetched == ["a.txt"] and r.filed == 1


def test_plan_writes_nothing(conn, pipe):
    begin(conn)
    d = FakeDrive()
    d.put("a.txt", b"x doc", modified="2026-11-17T09:00:00-05:00")
    d.put("b.txt", b"y doc", modified="2026-11-01T09:00:00-04:00")
    d.put("big.pdf", b"x", size=drives.MAX_BYTES + 1, modified="2026-11-17T09:00:00-05:00")
    d.put("clip.mp4", b"v", modified="2026-11-17T09:00:00-05:00")
    would, big, listed = drives.plan(source(d), conn, NOW)
    assert sorted(f.file_key for f in would) == ["a.txt", "b.txt"]
    assert (big, listed) == (1, 4)
    assert count(conn, "SELECT COUNT(*) FROM drive_files") == 0
    assert d.fetched == []


def test_choose_narrows_by_dates_and_folder_for_the_backfill(conn, pipe):
    d = FakeDrive()
    d.put("Clients/2025/a.txt", b"a", modified="2025-03-01T09:00:00-05:00")
    d.put("Clients/2025/b.txt", b"b", modified="2025-08-01T09:00:00-04:00")
    d.put("Other/c.txt", b"c", modified="2025-03-01T09:00:00-05:00")
    run(conn, pipe, source(d))
    files, _ = drives.choose(source(d), conn, NOW, start="2025-01-01",
                             until="2025-06-30", folder="Test/Clients")
    assert [f.file_key for f in files] == ["Clients/2025/a.txt"]
    files, _ = drives.choose(source(d), conn, NOW, start="2025-01-01", limit=2)
    assert [f.file_key for f in files] == ["Clients/2025/a.txt", "Other/c.txt"]


def test_when_reads_z_and_offsets():
    assert drives.when("2026-11-18T14:00:00Z").isoformat() == "2026-11-18T14:00:00+00:00"
    assert drives.local_day(drives.when("2026-11-18T03:00:00Z")) == "2026-11-17"


def test_files_sent_to_review_are_counted_apart_and_not_retried(conn, pipe):
    import hashlib
    begin(conn)
    d = FakeDrive()
    body = b"Something the classifier doubts"
    d.put("odd.txt", body, modified="2026-11-17T09:00:00-05:00")
    stub = lambda *a, **kw: IngestResult(sha256=hashlib.sha256(body).hexdigest(),
                                         status="QUARANTINED")
    r = run(conn, pipe, source(d), ingest=stub)
    assert (r.filed, r.review) == (0, 1)
    assert row(conn, "odd.txt")["filed_revision"] == row(conn, "odd.txt")["revision"]
    assert "1 to review" in r.summary()
    run(conn, pipe, source(d), ingest=stub)
    assert d.fetched == ["odd.txt"]


def test_a_fetched_path_outside_the_temp_directory_is_refused(conn, pipe, tmp_path):
    begin(conn)

    class Careless(FakeDrive):
        def fetch(self, f, dest_dir):
            out = tmp_path / "drive" / f.name
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(self.data[f.file_key])
            return out

    d = Careless()
    d.put("a.txt", b"The original, in the drive", modified="2026-11-17T09:00:00-05:00")
    calls = []
    r = run(conn, pipe, source(d), ingest=lambda *a, **kw: calls.append(a))
    assert (r.result, r.failed) == ("PARTIAL", 1)
    assert (tmp_path / "drive" / "a.txt").read_bytes() == b"The original, in the drive"
    assert calls == []
