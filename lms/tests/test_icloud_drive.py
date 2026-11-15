"""iCloud Drive, read from its folder on the Mac (R5, D-083)."""

import os
import shutil

import pytest

from core.adapters import icloud_drive as ic
from core.adapters import watchfolder
from core.adapters.drive import DriveError, FetchError


def tree(tmp_path):
    mobile = tmp_path / "Mobile Documents"
    docs = mobile / "com~apple~CloudDocs"
    (docs / "Clients").mkdir(parents=True)
    (docs / "Clients" / "lease.pdf").write_bytes(b"%PDF lease")
    (docs / "Clients" / ".tax.pdf.icloud").write_bytes(b"stub")
    (docs / "LMS" / "inbox").mkdir(parents=True)
    (docs / "LMS" / "inbox" / "photo.jpg").write_bytes(b"jpg")
    (docs / ".Trash").mkdir()
    (docs / ".Trash" / "old.pdf").write_bytes(b"old")
    (docs / ".DS_Store").write_bytes(b"x")
    (docs / "Old" / "LMS").mkdir(parents=True)
    (docs / "Old" / "LMS" / "note.txt").write_text("kept: LMS is skipped only at the top")
    pages = mobile / "com~apple~Pages" / "Documents"
    pages.mkdir(parents=True)
    (pages / "Plan.pages").write_bytes(b"pages")
    return mobile


def drive(mobile):
    return ic.ICloudDrive("storage:icloud-drive", ic.icloud_roots(mobile))


def test_it_lists_every_file_and_skips_the_lms_folder_and_hidden_ones(tmp_path):
    listing = drive(tree(tmp_path)).list(None)
    keys = {f.file_key for f in listing.files}
    assert keys == {"iCloud Drive/Clients/lease.pdf", "iCloud Drive/Clients/tax.pdf",
                    "iCloud Drive/Old/LMS/note.txt", "Pages/Plan.pages"}
    assert listing.full and listing.cursor is None and listing.deleted == ()
    lease = next(f for f in listing.files if f.name == "lease.pdf")
    assert (lease.size, lease.path, lease.area, lease.source_id) == (
        10, "iCloud Drive/Clients/lease.pdf", "own", "storage:icloud-drive")
    assert ":" in lease.revision and "T" in lease.modified


def test_a_placeholder_is_listed_under_its_real_name_without_a_size(tmp_path):
    listing = drive(tree(tmp_path)).list(None)
    tax = next(f for f in listing.files if f.name == "tax.pdf")
    assert tax.size is None and tax.is_document


def test_real_name():
    assert ic.real_name(".tax.pdf.icloud") == "tax.pdf"
    assert ic.real_name("lease.pdf") == "lease.pdf"
    assert ic.real_name(".DS_Store") is None
    assert ic.real_name(".icloud") is None


def test_no_icloud_folder_at_all_is_a_drive_error(tmp_path):
    with pytest.raises(DriveError, match="iCloud Drive on"):
        drive(tmp_path / "nothing").list(None)


def test_archive_roots_are_the_saved_copies_only(tmp_path):
    (tmp_path / "iCloudDrive-iCloudDrive (10-5-26 11.47 AM)").mkdir()
    (tmp_path / "iCloudDrive-iCloudDrive (7-30-26 5.55 PM)").mkdir()
    (tmp_path / "GoogleDrive-someone").mkdir()
    labels = [r.label for r in ic.archive_roots(tmp_path)]
    assert labels == ["iCloudDrive-iCloudDrive (10-5-26 11.47 AM)",
                      "iCloudDrive-iCloudDrive (7-30-26 5.55 PM)"]
    assert ic.archive_roots(tmp_path / "missing") == []


def test_fetch_copies_the_bytes_and_leaves_the_original(tmp_path):
    mobile = tree(tmp_path)
    d = drive(mobile)
    lease = next(f for f in d.list(None).files if f.name == "lease.pdf")
    out = d.fetch(lease, tmp_path / "work")
    assert out.read_bytes() == b"%PDF lease"
    assert out.parent == tmp_path / "work"
    assert (mobile / "com~apple~CloudDocs" / "Clients" / "lease.pdf").read_bytes() == b"%PDF lease"


def test_fetch_asks_icloud_for_a_placeholder_first(tmp_path, monkeypatch):
    mobile = tree(tmp_path)
    d = drive(mobile)
    tax = next(f for f in d.list(None).files if f.name == "tax.pdf")
    real = mobile / "com~apple~CloudDocs" / "Clients" / "tax.pdf"
    asked = []

    def materialise(path, timeout=120):
        asked.append(path.name)
        real.write_bytes(b"%PDF tax")
        return real

    monkeypatch.setattr(watchfolder, "materialise", materialise)
    assert d.fetch(tax, tmp_path / "work").read_bytes() == b"%PDF tax"
    assert asked == [".tax.pdf.icloud"]


def test_a_file_gone_since_listing_is_a_fetch_error(tmp_path):
    mobile = tree(tmp_path)
    d = drive(mobile)
    lease = next(f for f in d.list(None).files if f.name == "lease.pdf")
    os.remove(mobile / "com~apple~CloudDocs" / "Clients" / "lease.pdf")
    with pytest.raises(FetchError, match="no longer there"):
        d.fetch(lease, tmp_path / "work")


def test_listing_and_fetching_never_change_the_drive(tmp_path, monkeypatch):
    mobile = tree(tmp_path)
    d = drive(mobile)

    def refuse(*a, **k):
        raise AssertionError("the iCloud reader tried to change the drive")

    for name in ("rename", "replace", "remove", "unlink", "rmdir"):
        monkeypatch.setattr(os, name, refuse)
    monkeypatch.setattr(shutil, "move", refuse)
    files = d.list(None).files
    d.fetch(next(f for f in files if f.name == "lease.pdf"), tmp_path / "work")


def test_a_missing_main_icloud_folder_is_an_error_even_with_an_app_folder(tmp_path):
    mobile = tmp_path / "Mobile Documents"
    (mobile / "com~apple~Pages" / "Documents").mkdir(parents=True)
    (mobile / "com~apple~Pages" / "Documents" / "Plan.pages").write_bytes(b"pages")
    with pytest.raises(DriveError, match="iCloud Drive on"):
        drive(mobile).list(None)


def test_an_unreadable_folder_is_an_error_not_a_silent_partial_listing(tmp_path, monkeypatch):
    mobile = tree(tmp_path)
    d = drive(mobile)
    real_walk = os.walk

    def walk(top, *a, onerror=None, **k):
        if onerror is not None:
            onerror(PermissionError(13, "Permission denied", "<some path>"))
        yield from real_walk(top, *a, onerror=onerror, **k)

    monkeypatch.setattr(ic.os, "walk", walk)
    with pytest.raises(DriveError, match="could not be read"):
        d.list(None)


def test_archive_roots_where_only_one_exists_still_list(tmp_path):
    present = tmp_path / "iCloudDrive-iCloudDrive (10-5-26 11.47 AM)"
    (present / "Clients").mkdir(parents=True)
    (present / "Clients" / "lease.pdf").write_bytes(b"%PDF lease")
    roots = [ic.Root("iCloudDrive-iCloudDrive (10-5-26 11.47 AM)", present),
             ic.Root("iCloudDrive-iCloudDrive (7-30-26 5.55 PM)", tmp_path / "gone")]
    listing = ic.ICloudDrive("storage:archive", roots).list(None)
    assert {f.name for f in listing.files} == {"lease.pdf"}


def test_a_package_document_is_one_entry_and_is_not_walked_into(tmp_path):
    mobile = tree(tmp_path)
    pkg = mobile / "com~apple~CloudDocs" / "Clients" / "Plan.pages"
    (pkg / "Data").mkdir(parents=True)
    (pkg / "preview.jpg").write_bytes(b"jpg")
    (pkg / "Data" / "image.png").write_bytes(b"png")
    listing = drive(mobile).list(None)
    keys = {f.file_key for f in listing.files}
    assert "iCloud Drive/Clients/Plan.pages" in keys
    assert not [k for k in keys if "Plan.pages/" in k]
    entry = next(f for f in listing.files if f.file_key == "iCloud Drive/Clients/Plan.pages")
    assert (entry.name, entry.size, entry.path) == (
        "Plan.pages", None, "iCloud Drive/Clients/Plan.pages")
    assert entry.revision.endswith(":pkg")
    assert not entry.is_document


def test_a_folder_removed_mid_walk_does_not_fail_the_listing(tmp_path, monkeypatch):
    d = drive(tree(tmp_path))
    real_walk = os.walk

    def walk(top, *a, onerror=None, **k):
        if onerror is not None:
            onerror(FileNotFoundError(2, "No such file", "<some path>"))
        yield from real_walk(top, *a, onerror=onerror, **k)

    monkeypatch.setattr(ic.os, "walk", walk)
    assert d.list(None).files
