"""The storage report measures drives from their index (R28, D-083)."""

from core.db import database as db
from core.pipeline import filing
from core.reports import storage


def add_file(conn, key, size, *, sha=None, filed=False, deleted=False):
    conn.execute(
        "INSERT INTO drive_files (source_id, file_key, path, name, area, size, modified, "
        "revision, first_seen, last_seen, deleted_at, filed_revision, sha256) "
        "VALUES ('storage:test', ?, ?, ?, 'own', ?, '2026-11-17T09:00:00-05:00', 'r1', "
        "'2026-11-20', '2026-11-20', ?, ?, ?)",
        (key, f"Test/{key}", key, size, "2026-11-20" if deleted else None,
         "r1" if filed else None, sha))


def test_drive_sources_are_measured_from_the_index(tmp_path):
    conn = db.connect(tmp_path / "lms.db")
    roots = filing.StorageRoots(archive=tmp_path / "archive", originals=tmp_path / "_originals",
                                quarantine=tmp_path / "quarantine")
    roots.ensure()
    archived = tmp_path / "archive" / "a.txt"
    archived.write_bytes(b"x" * 1000)
    db.insert_artifact(conn, sha256="s" * 64, source="storage:test", source_ref="a.txt@r1",
                       original_name="a.txt", byte_size=1000, ingested_at="2026-11-20T09:00:00-05:00",
                       filed_path=str(archived), status="FILED")
    add_file(conn, "a.txt", 1000, sha="s" * 64, filed=True)
    add_file(conn, "Notes", None)
    add_file(conn, "old.pdf", 5000, deleted=True)
    conn.commit()

    r = storage.build_report(conn, roots, hash_sources=False)
    line = {s.source: s for s in r.sources}["storage:test"]
    assert (line.at_source.files, line.at_source.bytes) == (2, 1000)
    assert "no size" in line.at_source.note
    rec = [x for x in r.reclaimable if x.label.startswith("storage:test")]
    assert len(rec) == 1 and (rec[0].files, rec[0].bytes) == (1, 1000)
    assert "read-only" in rec[0].note
    assert "NOT reclaimable" in rec[0].label
    assert not any("Dropbox, the wider iCloud Drive" in n for n in r.not_measured)
    conn.close()
