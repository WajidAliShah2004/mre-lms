"""Dropbox, read-only through its HTTP API (R5, D-083).

THE SCOPES ARE THE SECURITY BOUNDARY: files.metadata.read,
files.content.read and account_info.read. The token can list and download
files and read the account's email; Dropbox refuses anything that would
upload, move, share or delete. ops/authorise_dropbox.py gets it once, with
PKCE and an offline refresh token, so there is no app secret; each pass
exchanges the refresh token for a short-lived access token.

Plain HTTPS with urllib, as lmstudio.py does: no new dependency.

A file's key is its path_lower. Dropbox reports a deletion by path only,
and a deleted folder as one entry; the engine marks everything below it.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from .drive import DriveError, DriveFile, FetchError, Listing, safe_name

SCOPES: tuple[str, ...] = ("files.metadata.read", "files.content.read", "account_info.read")
API = "https://api.dropboxapi.com"
CONTENT = "https://content.dropboxapi.com"
TOKEN_URL = API + "/oauth2/token"
KEYCHAIN_PREFIX = "lms/dropbox-oauth/"
TIMEOUT = 120
RETRYABLE = (429, 500, 502, 503, 504)


def keychain_service(address: str) -> str:
    return KEYCHAIN_PREFIX + address


class HTTPError(Exception):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}: {body[:300]}")
        self.status = status
        self.body = body


def urllib_post(url: str, data: bytes, headers: dict[str, str]) -> bytes:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.read()
    except urllib.error.HTTPError as exc:
        raise HTTPError(exc.code, exc.read().decode("utf-8", "replace")) from exc


class DropboxAPI:
    def __init__(self, app_key: str, refresh_token: str, *, post=urllib_post,
                 sleep=time.sleep) -> None:
        self._key = app_key
        self._refresh = refresh_token
        self._post = post
        self._sleep = sleep
        self._token: str | None = None

    def access_token(self) -> str:
        if self._token is None:
            body = urllib.parse.urlencode({"grant_type": "refresh_token",
                                           "refresh_token": self._refresh,
                                           "client_id": self._key}).encode()
            data = json.loads(self._post(TOKEN_URL, body,
                                         {"Content-Type": "application/x-www-form-urlencoded"}))
            self._token = data["access_token"]
        return self._token

    def _call(self, url: str, data: bytes, headers: dict[str, str]) -> bytes:
        refreshed = False
        wait = 2
        for attempt in range(5):
            h = dict(headers)
            h["Authorization"] = f"Bearer {self.access_token()}"
            try:
                return self._post(url, data, h)
            except HTTPError as exc:
                if exc.status == 401 and not refreshed:
                    self._token, refreshed = None, True
                    continue
                if exc.status in RETRYABLE and attempt < 4:
                    self._sleep(wait)
                    wait = min(wait * 2, 64)
                    continue
                raise
        raise HTTPError(0, "gave up after retries")

    def rpc(self, endpoint: str, args: dict | None) -> dict:
        body = json.dumps(args).encode() if args is not None else b"null"
        raw = self._call(f"{API}/2/{endpoint}", body, {"Content-Type": "application/json"})
        return json.loads(raw) if raw else {}

    def download(self, path: str) -> bytes:
        return self._call(f"{CONTENT}/2/files/download", b"",
                          {"Dropbox-API-Arg": json.dumps({"path": path}),
                           "Content-Type": "application/octet-stream"})


def explain(exc: Exception, address: str) -> str:
    body = getattr(exc, "body", "") or str(exc)
    if "invalid_grant" in body or getattr(exc, "status", None) == 401:
        return (f"the Dropbox grant for {address} no longer works; re-authorise: "
                f"./ops/authorise_dropbox.py {address} --app-key KEY")
    return f"{type(exc).__name__}: {exc}"


def _iso(value: str) -> str:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat(timespec="seconds")


class Dropbox:
    """One account's Dropbox, as a Lister and a Fetcher."""

    def __init__(self, source_id: str, address: str, api: DropboxAPI) -> None:
        self.source_id = source_id
        self.address = address
        self.api = api

    def list(self, cursor: str | None) -> Listing:
        try:
            if cursor:
                try:
                    page = self.api.rpc("files/list_folder/continue", {"cursor": cursor})
                    return self._pages(page, full=False)
                except HTTPError as exc:
                    if not (exc.status == 409 and "reset" in exc.body):
                        raise
            page = self.api.rpc("files/list_folder", {"path": "", "recursive": True,
                                                      "include_deleted": False,
                                                      "limit": 2000})
            return self._pages(page, full=True)
        except DriveError:
            raise
        except Exception as exc:                              # noqa: BLE001
            raise DriveError(explain(exc, self.address)) from exc

    def _pages(self, page: dict, *, full: bool) -> Listing:
        files: dict[str, DriveFile] = {}
        deleted: list[str] = []
        while True:
            # Entries apply in the order Dropbox gave them.
            for e in page.get("entries", []):
                tag = e.get(".tag")
                if tag == "file":
                    f = self._file(e)
                    files[f.file_key] = f
                    if f.file_key in deleted:
                        deleted.remove(f.file_key)
                elif tag == "deleted":
                    p = e["path_lower"]
                    for k in [k for k in files if k == p or k.startswith(p + "/")]:
                        del files[k]
                    if p not in deleted:
                        deleted.append(p)
            if not page.get("has_more"):
                break
            page = self.api.rpc("files/list_folder/continue", {"cursor": page["cursor"]})
        return Listing(tuple(files.values()), tuple(deleted), full=full, cursor=page.get("cursor"))

    def _file(self, e: dict) -> DriveFile:
        return DriveFile(
            source_id=self.source_id, file_key=e["path_lower"],
            path="Dropbox" + e["path_display"], name=e["name"],
            size=int(e["size"]) if e.get("size") is not None else None,
            modified=_iso(e["server_modified"]), revision=e["rev"], area="own")

    def fetch(self, f: DriveFile, dest_dir: Path) -> Path:
        try:
            data = self.api.download(f.file_key)
        except Exception as exc:                              # noqa: BLE001
            raise FetchError(f"{f.path}: {explain(exc, self.address)}") from exc
        Path(dest_dir).mkdir(parents=True, exist_ok=True)
        dest = Path(dest_dir) / safe_name(f.name)
        dest.write_bytes(data)
        return dest
