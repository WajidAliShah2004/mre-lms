"""Shared drive shapes and tables (R5, D-083)."""

from core.adapters.drive import DriveFile, safe_name, wrong_account
from core.db import database as db


def f(name, native=False):
    return DriveFile("storage:test", name, f"Test/{name}", name, 10,
                     "2026-11-18T09:00:00-05:00", "r1", native=native)


def test_documents_are_known_by_extension_or_as_google_files():
    assert f("Lease.PDF").is_document
    assert f("scan.heic").is_document
    assert f("notes.md").is_document
    assert f("Notes", native=True).is_document
    assert not f("clip.mp4").is_document
    assert not f("archive.zip").is_document
    assert not f("README").is_document


def test_safe_name_keeps_a_file_name_a_file_name():
    assert safe_name("a/b\\c:d.pdf") == "a_b_c_d.pdf"
    assert safe_name("") == "file"
    long = "x" * 300 + ".pdf"
    assert len(safe_name(long)) <= 200
    assert safe_name(long).endswith(".pdf")


def test_wrong_account_names_both_accounts():
    assert wrong_account("matthew@mrecai.com", "Matthew@MRECAI.com", "Google Drive") is None
    msg = wrong_account("matthew@mrecai.com", "mre@atlase.ai", "Google Drive")
    assert "mre@atlase.ai" in msg and "matthew@mrecai.com" in msg
    assert "(unknown)" in wrong_account("matthew@mrecai.com", None, "Dropbox")


def test_the_tables_exist(tmp_path):
    conn = db.connect(tmp_path / "lms.db")
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(drive_files)")}
        assert {"source_id", "file_key", "path", "name", "area", "mime", "native",
                "size", "modified", "revision", "first_seen", "last_seen",
                "deleted_at", "filed_revision", "artifact_id", "sha256",
                "attempts", "last_error"} == cols
        cols = {r[1] for r in conn.execute("PRAGMA table_info(drive_cursors)")}
        assert cols == {"source_id", "cursor", "started_on", "updated_at"}
    finally:
        conn.close()
