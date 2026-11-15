"""ops/drives.py and ops/backfill_drives.py (R5, D-083)."""

import os
import sys
from pathlib import Path

import pytest

from core.db import database as db
from core.pipeline import coverage, drives
from tests.drive_fixture import SID, FakeDrive
from tests.test_drives import NOW, begin, conn, pipe  # noqa: F401  (fixtures)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))

# _halt binds os.killpg as a default argument, which Windows lacks; a
# temporary stand-in lets it import there (as test_imessage_job does).
_SHIM = not hasattr(os, "killpg")
if _SHIM:
    os.killpg = None
import backfill_drives as bf        # noqa: E402
import drives as job                # noqa: E402
if _SHIM:
    del os.killpg


def src(d, **kw):
    return drives.DriveSource(SID, "Test Drive", d, d, **kw)


def register_line(sid, status, cadence):
    return coverage.Source(id=sid, label=sid, kind="storage", method="m", permissions="p",
                           status=status, requirement="R5", cadence_hours=cadence,
                           blocked_on=None if status == "active" else "not yet")


def test_the_job_stops_when_halted(monkeypatch, capsys):
    monkeypatch.setattr(job._halt, "read_flag", lambda: {"ts": "now", "by": "telegram"})
    monkeypatch.setattr(job, "ensure_venv", lambda *a, **k: None)
    monkeypatch.setattr(job, "load_lms_env", lambda: {})
    monkeypatch.setattr(sys, "argv", ["drives.py"])
    assert job.main() == 0
    assert "HALTED" in capsys.readouterr().out


def test_the_job_runs_the_active_drives_that_have_a_cadence():
    reg = [register_line("storage:icloud-drive", "active", 3),
           register_line("storage:icloud-archive", "active", None),
           register_line("storage:gdrive:matthew@mleca.com", "pending", 3),
           register_line("email:matthew@mrecai.com", "active", 12)]
    assert job.scheduled_sources(reg) == ["storage:icloud-drive"]


def test_every_storage_line_in_the_register_has_a_reader():
    ids = [s.id for s in coverage.load_register() if s.id.startswith("storage:")]
    assert "storage:icloud-drive" in ids and "storage:icloud-archive" in ids
    assert "storage:gdrive:matthew@mrecai.com" in ids
    assert "storage:gdrive:matthew@mleca.com" in ids
    assert "storage:dropbox:matthew@mrecai.com" in ids
    assert all(job.has_reader(i) for i in ids)
    assert not job.has_reader("storage:google-drive")


def test_the_probe_prints_counts_and_no_names(capsys):
    d = FakeDrive()
    d.put("Clients/secret-lease.txt", b"lease text", modified="2026-11-17T09:00:00-05:00")
    d.put("Private/holiday.mp4", b"video", modified="2026-11-18T09:00:00-05:00")
    assert job.do_probe(src(d)) == 0
    out = capsys.readouterr().out
    assert "2 file(s)" in out and "documents: txt 1" in out
    assert "other files (indexed, not filed): 1" in out
    for secret in ("secret-lease", "Clients", "holiday", "Private"):
        assert secret not in out


def test_the_dry_run_writes_nothing(conn, capsys):
    begin(conn)
    d = FakeDrive()
    d.put("a.txt", b"invoice text", modified="2026-11-17T09:00:00-05:00")
    assert job.do_dry_run(src(d), conn, NOW) == 0
    out = capsys.readouterr().out
    assert "1 document(s) would be filed now" in out and "Nothing was written." in out
    assert "a.txt" not in out
    assert conn.execute("SELECT COUNT(*) FROM drive_files").fetchone()[0] == 0


def test_a_drive_that_cannot_be_built_is_recorded_and_the_rest_still_run(conn, pipe, capsys):
    roots, registry, clf = pipe
    d = FakeDrive()

    def build(sid):
        if sid == "storage:broken":
            raise job.DriveError("no Drive grant for someone@example.com")
        return src(d)

    assert job.run(conn, roots, registry, clf, ["storage:broken", SID], NOW, build=build) == 1
    rows = dict(conn.execute("SELECT source_id, result FROM source_checks").fetchall())
    assert rows == {"storage:broken": "FAILED", SID: "OK"}
    assert "no Drive grant" in capsys.readouterr().out


def test_the_backfill_files_older_documents_in_batches_and_is_not_a_check(conn, pipe, capsys):
    roots, registry, clf = pipe
    d = FakeDrive()
    d.put("Clients/a.txt", b"Old lease number one", modified="2025-03-01T09:00:00-05:00")
    d.put("Clients/b.txt", b"Old lease number two", modified="2025-04-01T09:00:00-04:00")
    d.put("Other/c.txt", b"Old statement three", modified="2025-05-01T09:00:00-04:00")
    drives.run_pass(src(d), conn, roots, registry, clf, now=NOW, tmp_root=roots.archive.parent / "tmp")
    checks = conn.execute("SELECT COUNT(*) FROM source_checks").fetchone()[0]
    n = bf.backfill(src(d), conn, roots, registry, clf, now=NOW,
                    tmp_root=roots.archive.parent / "tmp", since="2025-01-01",
                    folder="Test/Clients", batch=1)
    assert n == 2 and d.fetched == ["Clients/a.txt", "Clients/b.txt"]
    assert capsys.readouterr().out.count("batch: ") == 2
    assert conn.execute("SELECT COUNT(*) FROM source_checks").fetchone()[0] == checks
    assert list((roots.archive.parent / "tmp").iterdir()) == []


def test_the_backfill_stops_between_batches_when_halted(conn, pipe):
    roots, registry, clf = pipe
    d = FakeDrive()
    d.put("a.txt", b"Old document one", modified="2025-03-01T09:00:00-05:00")
    d.put("b.txt", b"Old document two", modified="2025-04-01T09:00:00-04:00")
    drives.run_pass(src(d), conn, roots, registry, clf, now=NOW, tmp_root=roots.archive.parent / "tmp")
    assert bf.backfill(src(d), conn, roots, registry, clf, now=NOW,
                       tmp_root=roots.archive.parent / "tmp", since="2025-01-01",
                       batch=1, halted=lambda: True) == 1


def test_the_backfill_needs_an_index_first(conn):
    assert "drives.py --source storage:test" in bf.index_problem(conn, SID)
    begin(conn)
    assert bf.index_problem(conn, SID) is None


def test_a_failed_drive_is_the_newest_row_even_when_a_later_drive_succeeds(conn, pipe):
    roots, registry, clf = pipe
    d = FakeDrive()

    def build(sid):
        if sid == "storage:broken":
            raise job.DriveError("no Drive grant for someone@example.com")
        return src(d)

    assert job.run(conn, roots, registry, clf, ["storage:broken", SID], NOW, build=build) == 1
    row = conn.execute("SELECT action, detail FROM actions_log WHERE action IN "
                       "('DRIVES_CHECKED', 'DRIVES_FAILED') ORDER BY id DESC LIMIT 1").fetchone()
    assert row["action"] == "DRIVES_FAILED" and "storage:broken" in row["detail"]


def test_the_probe_reads_nothing_when_halted(monkeypatch, capsys):
    def boom(sid):
        raise AssertionError("build_source was called")

    monkeypatch.setattr(job._halt, "read_flag", lambda: {"ts": "now", "by": "telegram"})
    monkeypatch.setattr(job, "ensure_venv", lambda *a, **k: None)
    monkeypatch.setattr(job, "load_lms_env", lambda: {})
    monkeypatch.setattr(job, "build_source", boom)
    monkeypatch.setattr(sys, "argv", ["drives.py", "--source", "storage:icloud-drive", "--probe"])
    assert job.main() == 0
    assert "HALTED" in capsys.readouterr().out
