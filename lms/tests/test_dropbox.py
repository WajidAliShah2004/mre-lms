"""Dropbox, read-only through its HTTP API (R5, D-083)."""

import json
from dataclasses import replace
from urllib.parse import parse_qs

import pytest

from core.adapters import dropbox as dbx
from core.adapters.drive import DriveError, FetchError

LEASE = {".tag": "file", "id": "id:1", "name": "Lease.PDF", "path_lower": "/clients/lease.pdf",
         "path_display": "/Clients/Lease.PDF", "server_modified": "2026-11-18T14:00:00Z",
         "rev": "015a", "size": 1200}
CLIP = {".tag": "file", "id": "id:2", "name": "clip.mp4", "path_lower": "/clip.mp4",
        "path_display": "/clip.mp4", "server_modified": "2026-11-18T15:00:00Z",
        "rev": "015b", "size": 5000}
FOLDER = {".tag": "folder", "id": "id:3", "name": "Clients", "path_lower": "/clients",
          "path_display": "/Clients"}


class FakeAPI:
    def __init__(self):
        self.pages = {}
        self.blobs = {}
        self.calls = []

    def rpc(self, endpoint, args):
        self.calls.append((endpoint, args))
        key = endpoint if endpoint == "files/list_folder" else args["cursor"]
        result = self.pages[key]
        if isinstance(result, Exception):
            raise result
        return result

    def download(self, path):
        if path not in self.blobs:
            raise dbx.HTTPError(409, '{"error_summary": "path/not_found/"}')
        return self.blobs[path]


def drive(api):
    return dbx.Dropbox("storage:dropbox:matthew@mrecai.com", "matthew@mrecai.com", api)


def test_the_first_listing_is_the_whole_dropbox_page_by_page():
    api = FakeAPI()
    api.pages["files/list_folder"] = {"entries": [LEASE, FOLDER], "cursor": "c1", "has_more": True}
    api.pages["c1"] = {"entries": [CLIP], "cursor": "c2", "has_more": False}
    listing = drive(api).list(None)
    assert [f.file_key for f in listing.files] == ["/clients/lease.pdf", "/clip.mp4"]
    assert (listing.full, listing.cursor) == (True, "c2")
    lease = listing.files[0]
    assert (lease.path, lease.name, lease.size, lease.revision, lease.area) == (
        "Dropbox/Clients/Lease.PDF", "Lease.PDF", 1200, "015a", "own")
    assert lease.modified == "2026-11-18T14:00:00+00:00" and lease.is_document
    assert api.calls[0] == ("files/list_folder",
                            {"path": "", "recursive": True, "include_deleted": False,
                             "limit": 2000})


def test_later_listings_continue_from_the_cursor_with_deletions():
    api = FakeAPI()
    api.pages["c2"] = {"entries": [dict(LEASE, rev="015c"),
                                   {".tag": "deleted", "name": "Old", "path_lower": "/old"}],
                       "cursor": "c3", "has_more": False}
    listing = drive(api).list("c2")
    assert [(f.file_key, f.revision) for f in listing.files] == [("/clients/lease.pdf", "015c")]
    assert listing.deleted == ("/old",)
    assert (listing.full, listing.cursor) == (False, "c3")


def test_a_reset_cursor_means_a_full_listing():
    api = FakeAPI()
    api.pages["stale"] = dbx.HTTPError(409, '{"error_summary": "reset/..", "error": {".tag": "reset"}}')
    api.pages["files/list_folder"] = {"entries": [LEASE], "cursor": "c9", "has_more": False}
    listing = drive(api).list("stale")
    assert listing.full and listing.cursor == "c9"


def test_a_revoked_grant_says_how_to_re_authorise():
    api = FakeAPI()
    api.pages["files/list_folder"] = dbx.HTTPError(400, '{"error": "invalid_grant"}')
    with pytest.raises(DriveError, match=r"authorise_dropbox\.py matthew@mrecai\.com"):
        drive(api).list(None)


def test_fetch_downloads_by_path(tmp_path):
    api = FakeAPI()
    api.pages["files/list_folder"] = {"entries": [LEASE], "cursor": "c1", "has_more": False}
    api.blobs["/clients/lease.pdf"] = b"%PDF lease"
    d = drive(api)
    f = d.list(None).files[0]
    out = d.fetch(f, tmp_path)
    assert out.name == "Lease.PDF" and out.read_bytes() == b"%PDF lease"
    with pytest.raises(FetchError, match="not_found"):
        d.fetch(replace(f, file_key="/gone"), tmp_path)


def fake_post(*responses):
    sent = []
    queue = list(responses)

    def post(url, data, headers):
        sent.append((url, data, headers))
        r = queue.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
    return post, sent


def token(value):
    return json.dumps({"access_token": value, "expires_in": 14400}).encode()


def test_the_api_refreshes_a_short_lived_token_and_sends_it():
    post, sent = fake_post(token("AT1"), b'{"email": "matthew@mrecai.com"}')
    api = dbx.DropboxAPI("KEY", "RT", post=post)
    assert api.rpc("users/get_current_account", None)["email"] == "matthew@mrecai.com"
    url, body, headers = sent[0]
    assert url == dbx.TOKEN_URL
    form = parse_qs(body.decode())
    assert form == {"grant_type": ["refresh_token"], "refresh_token": ["RT"], "client_id": ["KEY"]}
    url, body, headers = sent[1]
    assert url == "https://api.dropboxapi.com/2/users/get_current_account"
    assert body == b"null" and headers["Authorization"] == "Bearer AT1"
    assert headers["Content-Type"] == "application/json"


def test_an_expired_token_is_refreshed_once():
    post, sent = fake_post(token("AT1"), dbx.HTTPError(401, "expired_access_token"),
                           token("AT2"), b"{}")
    api = dbx.DropboxAPI("KEY", "RT", post=post)
    assert api.rpc("files/list_folder", {"path": ""}) == {}
    assert sent[-1][2]["Authorization"] == "Bearer AT2"


def test_a_rate_limit_is_waited_out():
    waits = []
    post, sent = fake_post(token("AT1"), dbx.HTTPError(429, "too_many_requests"), b'{"ok": 1}')
    api = dbx.DropboxAPI("KEY", "RT", post=post, sleep=waits.append)
    assert api.rpc("files/list_folder", {"path": ""}) == {"ok": 1}
    assert waits == [2]


def test_download_sends_the_path_in_the_header():
    post, sent = fake_post(token("AT1"), b"%PDF")
    api = dbx.DropboxAPI("KEY", "RT", post=post)
    assert api.download("/clients/lease.pdf") == b"%PDF"
    url, body, headers = sent[1]
    assert url == "https://content.dropboxapi.com/2/files/download"
    assert json.loads(headers["Dropbox-API-Arg"]) == {"path": "/clients/lease.pdf"}
    assert headers["Content-Type"] == "application/octet-stream" and body == b""


def test_the_scopes_and_keychain_item():
    assert dbx.SCOPES == ("files.metadata.read", "files.content.read", "account_info.read")
    assert dbx.keychain_service("matthew@mrecai.com") == "lms/dropbox-oauth/matthew@mrecai.com"


def _file(path):
    return dict(LEASE, path_lower=path, path_display=path, name=path.rsplit("/", 1)[-1])


def test_entries_apply_in_order_a_later_file_survives_an_earlier_folder_deletion():
    api = FakeAPI()
    api.pages["c2"] = {"entries": [_file("/old/a.txt"),
                                   {".tag": "deleted", "name": "old", "path_lower": "/old"},
                                   _file("/old/b.txt")],
                       "cursor": "c3", "has_more": False}
    listing = drive(api).list("c2")
    assert [f.file_key for f in listing.files] == ["/old/b.txt"]
    assert listing.deleted == ("/old",)


def test_a_file_after_its_own_deletion_is_not_deleted():
    api = FakeAPI()
    api.pages["c2"] = {"entries": [{".tag": "deleted", "name": "x.pdf", "path_lower": "/x.pdf"},
                                   _file("/x.pdf")],
                       "cursor": "c3", "has_more": False}
    listing = drive(api).list("c2")
    assert [f.file_key for f in listing.files] == ["/x.pdf"]
    assert listing.deleted == ()
