"""Google Drive, read-only through its API (R5, D-083)."""

import pytest

from core.adapters import gdrive
from core.adapters.drive import DriveError, FetchError

DOC = "application/vnd.google-apps.document"


class RefreshError(Exception):
    """Named like google.auth.exceptions.RefreshError, which explain() recognises."""


class FakeAPI:
    def __init__(self):
        self.pages = []
        self.changes = {}
        self.start = "s1"
        self.meta = {"root": {"id": "root", "name": "My Drive"},
                     "SD1": {"id": "SD1", "name": "MRECAI Team"}}
        self.blobs = {}
        self.exports = {}
        self.email = "matthew@mrecai.com"
        self.fail = None

    def list_files(self, page_token):
        if self.fail:
            raise self.fail
        i = int(page_token or 0)
        page = dict(self.pages[i])
        if i + 1 < len(self.pages):
            page["nextPageToken"] = str(i + 1)
        return page

    def start_page_token(self):
        return self.start

    def list_changes(self, token):
        if token == "expired":
            raise gdrive.CursorExpired()
        return self.changes[token]

    def get_file(self, file_id):
        return self.meta[file_id]

    def download(self, file_id):
        return self.blobs[file_id]

    def export_pdf(self, file_id):
        return self.exports[file_id]

    def user_email(self):
        return self.email


LEASE = {"id": "A", "name": "lease.pdf", "mimeType": "application/pdf", "size": "1200",
         "modifiedTime": "2026-11-18T14:00:00.000Z", "version": "7", "parents": ["F1"],
         "ownedByMe": True}
NOTES = {"id": "D", "name": "Notes", "mimeType": DOC, "modifiedTime": "2026-11-18T15:00:00.000Z",
         "version": "3", "parents": ["root"], "ownedByMe": True}
POLICY = {"id": "S", "name": "policy.pdf", "mimeType": "application/pdf", "size": "900",
          "modifiedTime": "2026-11-17T10:00:00.000Z", "version": "2", "parents": ["SD1"],
          "driveId": "SD1", "ownedByMe": False}
QUOTE = {"id": "W", "name": "quote.pdf", "mimeType": "application/pdf", "size": "50",
         "modifiedTime": "2026-11-16T10:00:00.000Z", "version": "1", "parents": ["X9"],
         "ownedByMe": False}
CLIENTS = {"id": "F1", "name": "Clients", "mimeType": gdrive.FOLDER, "parents": ["root"],
           "modifiedTime": "2026-01-01T00:00:00.000Z", "ownedByMe": True}


def drive(api):
    return gdrive.GDrive("storage:gdrive:matthew@mrecai.com", "matthew@mrecai.com", api)


def full_api():
    api = FakeAPI()
    api.pages = [{"files": [CLIENTS, LEASE, NOTES]}, {"files": [POLICY, QUOTE]}]
    return api


def test_the_first_listing_is_every_file_in_every_area():
    listing = drive(full_api()).list(None)
    by = {f.file_key: f for f in listing.files}
    assert set(by) == {"A", "D", "S", "W"}, "folders are not files"
    assert listing.full and listing.cursor == "s1" and listing.deleted == ()
    a = by["A"]
    assert (a.path, a.size, a.revision, a.area) == ("My Drive/Clients/lease.pdf", 1200, "7", "own")
    assert a.modified == "2026-11-18T14:00:00+00:00"
    assert by["D"].native and by["D"].size is None and by["D"].is_document
    assert (by["S"].area, by["S"].path) == ("shared_drive", "MRECAI Team/policy.pdf")
    assert (by["W"].area, by["W"].path) == ("shared_with_me", "Shared with me/quote.pdf")


def test_later_listings_are_the_changes_since_the_cursor():
    api = full_api()
    api.changes["s1"] = {
        "changes": [
            {"fileId": "A", "removed": False, "file": dict(LEASE, version="8")},
            {"fileId": "W2", "removed": True},
            {"fileId": "T", "file": dict(QUOTE, id="T", trashed=True)},
            {"changeType": "drive", "driveId": "SD1"},
        ],
        "nextPageToken": "s1b"}
    api.changes["s1b"] = {"changes": [{"fileId": "F1", "file": CLIENTS}],
                          "newStartPageToken": "s2"}
    listing = drive(api).list("s1")
    assert [(f.file_key, f.revision) for f in listing.files] == [("A", "8")]
    assert listing.files[0].path == "My Drive/Clients/lease.pdf"
    assert set(listing.deleted) == {"W2", "T"}
    assert (listing.full, listing.cursor) == (False, "s2")


def test_an_expired_cursor_means_a_full_listing():
    listing = drive(full_api()).list("expired")
    assert listing.full and len(listing.files) == 4


def test_cursor_gone_reads_the_http_status():
    class Resp:
        status = 410

    class HttpError(Exception):
        resp = Resp()

    assert gdrive.cursor_gone(HttpError())
    assert not gdrive.cursor_gone(ValueError())


class HttpErr(Exception):
    """An exception carrying a fake HTTP response with a status, like HttpError."""

    def __init__(self, status, message=""):
        super().__init__(message)
        self.resp = type("Resp", (), {"status": status})()


def test_http_status_reads_the_status_or_none():
    assert gdrive.http_status(HttpErr(410)) == 410
    assert gdrive.http_status(HttpErr(400)) == 400
    assert gdrive.http_status(HttpErr("404")) == 404
    assert gdrive.http_status(HttpErr("oops")) is None
    assert gdrive.http_status(ValueError()) is None


def test_a_parent_folder_that_is_gone_stops_the_path_without_failing():
    api = FakeAPI()
    api.pages = [{"files": [dict(LEASE, parents=["GONE"])]}]

    def get_file(file_id):
        raise HttpErr(404, "File not found")

    api.get_file = get_file
    listing = drive(api).list(None)
    assert [f.path for f in listing.files] == ["lease.pdf"]


def test_a_folder_lookup_failing_for_another_reason_fails_the_listing():
    api = FakeAPI()
    api.pages = [{"files": [LEASE]}]

    def get_file(file_id):
        raise ConnectionError("network down")

    api.get_file = get_file
    with pytest.raises(DriveError, match="ConnectionError"):
        drive(api).list(None)


def test_a_revoked_grant_says_how_to_re_authorise():
    api = full_api()
    api.fail = RefreshError("invalid_grant: Token has been expired or revoked.")
    with pytest.raises(DriveError, match=r"authorise_gdrive\.py matthew@mrecai\.com"):
        drive(api).list(None)


def test_fetch_downloads_files_and_exports_google_docs_as_pdf(tmp_path):
    api = full_api()
    api.blobs["A"] = b"%PDF lease"
    api.exports["D"] = b"%PDF notes"
    d = drive(api)
    files = {f.file_key: f for f in d.list(None).files}
    assert d.fetch(files["A"], tmp_path).read_bytes() == b"%PDF lease"
    out = d.fetch(files["D"], tmp_path / "x")
    assert out.name == "Notes.pdf" and out.read_bytes() == b"%PDF notes"


def test_a_failed_download_is_a_fetch_error(tmp_path):
    api = full_api()
    d = drive(api)
    files = {f.file_key: f for f in d.list(None).files}
    with pytest.raises(FetchError, match="lease.pdf"):
        d.fetch(files["A"], tmp_path)


def test_the_keychain_item_is_apart_from_gmail():
    assert gdrive.keychain_service("matthew@mleca.com") == "lms/gdrive-oauth/matthew@mleca.com"
    assert gdrive.SCOPES == ("https://www.googleapis.com/auth/drive.readonly",)
