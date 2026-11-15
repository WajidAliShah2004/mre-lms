"""Shared shapes for the drive readers (R5, D-083).

Every drive reader is two things: a Lister, which says what files a drive
holds, and a Fetcher, which copies one file's bytes into a directory the LMS
owns. Neither ever changes the drive. The engine that uses them is
core/pipeline/drives.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

# own: the account's own files. shared_drive: a Google shared drive it
# belongs to. shared_with_me: files other people own and shared with it.
AREAS = ("own", "shared_drive", "shared_with_me")

DOCUMENT_SUFFIXES = frozenset({
    ".pdf", ".jpg", ".jpeg", ".png", ".heic", ".tif", ".tiff",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".txt", ".rtf", ".csv", ".md", ".eml",
})


class DriveError(RuntimeError):
    """The drive could not be listed: the whole pass fails."""


class FetchError(RuntimeError):
    """One file could not be fetched: that file fails, the pass goes on."""


@dataclass(frozen=True)
class DriveFile:
    source_id: str
    file_key: str            # stable id: a relative path, a Google file id, a Dropbox path_lower
    path: str                # for display and --folder: starts with a root label
    name: str
    size: int | None         # None when the drive does not say (Google Docs, iCloud stubs)
    modified: str            # ISO8601 with offset
    revision: str            # changes whenever the content does
    area: str = "own"
    mime: str | None = None
    native: bool = False     # a Google Doc, Sheet or Slides file: exported as PDF

    @property
    def is_document(self) -> bool:
        return self.native or Path(self.name).suffix.lower() in DOCUMENT_SUFFIXES


@dataclass(frozen=True)
class Listing:
    files: tuple[DriveFile, ...] = ()
    deleted: tuple[str, ...] = ()     # file_keys gone since the cursor
    full: bool = False                # every file is here: anything missing is gone
    cursor: str | None = None         # for the next incremental listing


class Lister(Protocol):
    def list(self, cursor: str | None) -> Listing: ...


class Fetcher(Protocol):
    def fetch(self, f: DriveFile, dest_dir: Path) -> Path: ...


def safe_name(name: str) -> str:
    """A drive's file name as a file name on the Mac: no separators, not empty,
    at most 200 characters with the extension kept."""
    clean = "".join("_" if c in "/\\:\0" else c for c in name).strip() or "file"
    if len(clean) > 200:
        stem, dot, ext = clean.rpartition(".")
        clean = (stem[:190] + dot + ext[:9]) if dot else clean[:200]
    return clean


def wrong_account(wanted: str, actual: str | None, where: str) -> str | None:
    """Why a grant is for the wrong account, or None if it is the right one."""
    if (actual or "").strip().lower() == wanted.strip().lower():
        return None
    return (f"{where} signed in as {actual or '(unknown)'}, not {wanted}. A token "
            f"for the wrong account would file its files as {wanted}'s.")
