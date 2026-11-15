"""Google Drive, read-only through its API (R5, D-083).

THE SCOPE IS THE SECURITY BOUNDARY. The grant is drive.readonly and nothing
else: the token can list and download files, and Google refuses anything
that would create, change, move, share or delete one. It is stored apart
from the account's Gmail grant (lms/gdrive-oauth/<address>), so each grant
stays as narrow as its job (ops/authorise_gdrive.py).

The first listing reads every file in My Drive, the shared drives and
"Shared with me", and takes a changes token first so nothing changed during
the listing is missed. Later listings read only the changes since that
token. Google Docs, Sheets and Slides have no file of their own: fetching
one exports it as PDF (Google allows up to 10 MB per export).

The Google libraries are imported lazily, as in gmail.py: this file must
import on the development machine, where the tests use a fake DriveAPI.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Protocol

from .drive import DriveError, DriveFile, FetchError, Listing, safe_name
from .gmail import _with_backoff

SCOPES: tuple[str, ...] = ("https://www.googleapis.com/auth/drive.readonly",)
KEYCHAIN_PREFIX = "lms/gdrive-oauth/"
FOLDER = "application/vnd.google-apps.folder"
NATIVE = frozenset({"application/vnd.google-apps.document",
                    "application/vnd.google-apps.spreadsheet",
                    "application/vnd.google-apps.presentation"})
FIELDS = "id,name,mimeType,size,modifiedTime,version,parents,ownedByMe,driveId,trashed"


def keychain_service(address: str) -> str:
    return KEYCHAIN_PREFIX + address


class CursorExpired(Exception):
    """The stored changes token is no longer valid: list everything again."""


def cursor_gone(exc: Exception) -> bool:
    status = getattr(getattr(exc, "resp", None), "status", None)
    try:
        return int(status) == 410
    except (TypeError, ValueError):
        return False


def explain(exc: Exception, address: str) -> str:
    if type(exc).__name__ == "RefreshError" or "invalid_grant" in str(exc):
        return (f"the Drive grant for {address} no longer works ({exc}); "
                f"re-authorise: ./ops/authorise_gdrive.py {address}")
    return f"{type(exc).__name__}: {exc}"


def _note(message: str) -> None:
    print(message.replace("Gmail", "Google Drive"), file=sys.stderr, flush=True)


def _iso(value: str) -> str:
    from datetime import datetime
    return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat(timespec="seconds")


class DriveAPI(Protocol):
    def list_files(self, page_token: str | None) -> dict: ...
    def start_page_token(self) -> str: ...
    def list_changes(self, page_token: str) -> dict: ...
    def get_file(self, file_id: str) -> dict: ...
    def download(self, file_id: str) -> bytes: ...
    def export_pdf(self, file_id: str) -> bytes: ...
    def user_email(self) -> str: ...


class GoogleDriveAPI:
    """The real one, on google-api-python-client."""

    def __init__(self, credentials: Any) -> None:
        self._creds = credentials
        self._service = None

    def _svc(self):
        if self._service is None:
            try:
                from googleapiclient.discovery import build
            except ImportError as exc:      # pragma: no cover - mac only
                raise DriveError("google-api-python-client is not installed") from exc
            self._service = build("drive", "v3", credentials=self._creds,
                                  cache_discovery=False)
        return self._service

    def list_files(self, page_token: str | None) -> dict:
        req = self._svc().files().list(
            corpora="allDrives", includeItemsFromAllDrives=True, supportsAllDrives=True,
            q="trashed = false", pageSize=1000, pageToken=page_token,
            fields=f"nextPageToken, files({FIELDS})")
        return _with_backoff(req.execute, note=_note)

    def start_page_token(self) -> str:
        req = self._svc().changes().getStartPageToken(supportsAllDrives=True)
        return _with_backoff(req.execute, note=_note)["startPageToken"]

    def list_changes(self, page_token: str) -> dict:
        req = self._svc().changes().list(
            pageToken=page_token, includeItemsFromAllDrives=True, supportsAllDrives=True,
            pageSize=1000,
            fields=f"nextPageToken, newStartPageToken, changes(fileId, removed, file({FIELDS}))")
        try:
            return _with_backoff(req.execute, note=_note)
        except Exception as exc:
            if cursor_gone(exc):
                raise CursorExpired() from exc
            raise

    def get_file(self, file_id: str) -> dict:
        req = self._svc().files().get(fileId=file_id, supportsAllDrives=True,
                                      fields="id,name,parents")
        return _with_backoff(req.execute, note=_note)

    def download(self, file_id: str) -> bytes:
        import io

        from googleapiclient.http import MediaIoBaseDownload
        buf = io.BytesIO()
        req = self._svc().files().get_media(fileId=file_id, supportsAllDrives=True)
        dl = MediaIoBaseDownload(buf, req, chunksize=8 * 1024 * 1024)
        done = False
        while not done:
            _progress, done = _with_backoff(dl.next_chunk, note=_note)
        return buf.getvalue()

    def export_pdf(self, file_id: str) -> bytes:
        req = self._svc().files().export(fileId=file_id, mimeType="application/pdf")
        return _with_backoff(req.execute, note=_note)

    def user_email(self) -> str:
        req = self._svc().about().get(fields="user")
        return _with_backoff(req.execute, note=_note)["user"]["emailAddress"]


class GDrive:
    """One account's Drive, as a Lister and a Fetcher."""

    def __init__(self, source_id: str, address: str, api: DriveAPI) -> None:
        self.source_id = source_id
        self.address = address
        self.api = api
        self._folders: dict[str, tuple[str, str | None] | None] = {}

    def list(self, cursor: str | None) -> Listing:
        try:
            if cursor:
                try:
                    return self._changes(cursor)
                except CursorExpired:
                    pass
            return self._full()
        except DriveError:
            raise
        except Exception as exc:                              # noqa: BLE001
            raise DriveError(explain(exc, self.address)) from exc

    def _full(self) -> Listing:
        start = self.api.start_page_token()
        items: list[dict] = []
        self._folders = {}
        token = None
        while True:
            page = self.api.list_files(token)
            for item in page.get("files", []):
                if item.get("mimeType") == FOLDER:
                    self._remember(item)
                else:
                    items.append(item)
            token = page.get("nextPageToken")
            if not token:
                break
        return Listing(tuple(self._file(i) for i in items), (), full=True, cursor=start)

    def _changes(self, cursor: str) -> Listing:
        items: list[dict] = []
        deleted: list[str] = []
        token: str | None = cursor
        new_start = None
        while token:
            page = self.api.list_changes(token)
            for ch in page.get("changes", []):
                if "fileId" not in ch:
                    continue                      # a shared drive itself changed
                item = ch.get("file")
                if ch.get("removed") or item is None or item.get("trashed"):
                    deleted.append(ch["fileId"])
                elif item.get("mimeType") == FOLDER:
                    self._remember(item)
                else:
                    items.append(item)
            new_start = page.get("newStartPageToken") or new_start
            token = page.get("nextPageToken")
        return Listing(tuple(self._file(i) for i in items), tuple(deleted), full=False,
                       cursor=new_start or cursor)

    def _remember(self, item: dict) -> None:
        self._folders[item["id"]] = (item.get("name") or item["id"],
                                     (item.get("parents") or [None])[0])

    def _folder(self, folder_id: str) -> tuple[str, str | None] | None:
        if folder_id not in self._folders:
            try:
                self._remember(self.api.get_file(folder_id))
            except Exception:                                 # noqa: BLE001
                self._folders[folder_id] = None
        return self._folders[folder_id]

    def _path(self, item: dict, area: str) -> str:
        if area == "shared_with_me":
            return f"Shared with me/{item['name']}"
        parts = [item["name"]]
        parent = (item.get("parents") or [None])[0]
        seen: set[str] = set()
        while parent and parent not in seen and len(parts) < 64:
            seen.add(parent)
            folder = self._folder(parent)
            if folder is None:
                break
            name, parent = folder
            parts.append(name)
        return "/".join(reversed(parts))

    def _file(self, item: dict) -> DriveFile:
        mime = item.get("mimeType")
        if item.get("driveId"):
            area = "shared_drive"
        elif item.get("ownedByMe"):
            area = "own"
        else:
            area = "shared_with_me"
        size = item.get("size")
        return DriveFile(
            source_id=self.source_id, file_key=item["id"], path=self._path(item, area),
            name=item["name"], size=int(size) if size is not None else None,
            modified=_iso(item["modifiedTime"]),
            revision=str(item.get("version") or item["modifiedTime"]),
            area=area, mime=mime, native=mime in NATIVE)

    def fetch(self, f: DriveFile, dest_dir: Path) -> Path:
        try:
            if f.native:
                data, name = self.api.export_pdf(f.file_key), f.name + ".pdf"
            else:
                data, name = self.api.download(f.file_key), f.name
        except Exception as exc:                              # noqa: BLE001
            raise FetchError(f"{f.path}: {explain(exc, self.address)}") from exc
        Path(dest_dir).mkdir(parents=True, exist_ok=True)
        dest = Path(dest_dir) / safe_name(name)
        dest.write_bytes(data)
        return dest
