"""iCloud Drive, read from its folder on the Mac (R5, D-083).

Listing uses stat only, so a file iCloud keeps online is listed from its
placeholder and not downloaded. Fetching one asks iCloud for the bytes (the
watch folder's materialise step), then copies them out with
shutil.copyfile. Nothing in iCloud Drive is ever written, moved, renamed or
deleted.

The LMS folder at the top of iCloud Drive is skipped: it holds the watch
folder's inbox and the briefs, and reading it would feed the LMS its own
output. The same reader, given archive_roots(), lists the copies macOS kept
in ~/Library/CloudStorage when iCloud was signed out.
"""

from __future__ import annotations

import os
import shutil
import stat
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from . import watchfolder
from .drive import DriveError, DriveFile, FetchError, Listing, safe_name

MOBILE_DOCUMENTS = Path.home() / "Library" / "Mobile Documents"
CLOUD_STORAGE = Path.home() / "Library" / "CloudStorage"
CLOUD_DOCS = "com~apple~CloudDocs"
# Apple's document apps keep their files here; Finder shows them as folders
# of iCloud Drive.
APP_CONTAINERS = (("com~apple~Pages", "Pages"), ("com~apple~Numbers", "Numbers"),
                  ("com~apple~Keynote", "Keynote"), ("com~apple~Preview", "Preview"),
                  ("com~apple~TextEdit", "TextEdit"))
ARCHIVE_PREFIX = "iCloudDrive-iCloudDrive ("
STUB_SUFFIX = ".icloud"
# Folders that are really one document: listed as one entry, never walked into.
PACKAGE_SUFFIXES = frozenset({".pages", ".numbers", ".key", ".rtfd", ".photoslibrary", ".app",
                              ".bundle", ".pkg", ".framework", ".band", ".logicx",
                              ".fcpbundle", ".imovielibrary"})


class Root(NamedTuple):
    label: str
    path: Path
    skip: tuple[str, ...] = ()      # top-level folder names not read
    required: bool = False          # must exist, or the listing is an error


def icloud_roots(mobile: Path = MOBILE_DOCUMENTS) -> list[Root]:
    roots = [Root("iCloud Drive", mobile / CLOUD_DOCS, ("LMS",), required=True)]
    roots += [Root(label, mobile / folder / "Documents") for folder, label in APP_CONTAINERS]
    return roots


def archive_roots(storage: Path = CLOUD_STORAGE) -> list[Root]:
    if not storage.is_dir():
        return []
    return [Root(p.name, p) for p in sorted(storage.iterdir())
            if p.is_dir() and p.name.startswith(ARCHIVE_PREFIX)]


def real_name(name: str) -> str | None:
    """The file a directory entry stands for: itself, or what a `.x.icloud`
    placeholder stands for. None for hidden files."""
    if name.startswith(".") and name.endswith(STUB_SUFFIX) and len(name) > len(STUB_SUFFIX) + 1:
        return name[1:-len(STUB_SUFFIX)]
    if name.startswith("."):
        return None
    return name


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


class ICloudDrive:
    def __init__(self, source_id: str, roots: list[Root]) -> None:
        self.source_id = source_id
        self.roots = list(roots)
        self._by_label = {r.label: r for r in self.roots}

    def list(self, cursor: str | None = None) -> Listing:
        present = [r for r in self.roots if r.path.is_dir()]
        if not present:
            where = ", ".join(str(r.path) for r in self.roots) or "no folder given"
            raise DriveError(f"none of {where} is on this Mac (is iCloud Drive on?)")
        for root in self.roots:
            if root.required and not root.path.is_dir():
                raise DriveError(f"{root.path} is not on this Mac (is iCloud Drive on?)")
        files: dict[str, DriveFile] = {}
        errors: list[OSError] = []
        for root in present:
            for f in self._walk(root, errors):
                old = files.get(f.file_key)
                # A file and its own placeholder side by side: keep the file.
                if old is None or old.size is None:
                    files[f.file_key] = f
        # A partial walk must not look like a full listing: the engine would
        # count every file it could not read as deleted.
        if errors:
            first = errors[0]
            raise DriveError(
                f"{len(errors)} folder(s) or file(s) could not be read, first: "
                f"{getattr(first, 'filename', '?')}: {getattr(first, 'strerror', first)} "
                "(is Full Disk Access granted to the LMS's Python?)")
        return Listing(tuple(files.values()), (), full=True, cursor=None)

    def _walk(self, root: Root, errors: list[OSError]):
        def onerror(exc: OSError) -> None:
            if not isinstance(exc, FileNotFoundError):     # removed mid-walk: not an error
                errors.append(exc)

        for dirpath, dirnames, filenames in os.walk(root.path, onerror=onerror):
            here = Path(dirpath)
            top = here == root.path
            dirnames[:] = sorted(d for d in dirnames
                                 if not d.startswith(".") and not (top and d in root.skip))
            for d in list(dirnames):
                if Path(d).suffix.lower() not in PACKAGE_SUFFIXES:
                    continue
                dirnames.remove(d)
                try:
                    st = os.stat(here / d, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    errors.append(exc)
                    continue
                key = f"{root.label}/{(here / d).relative_to(root.path).as_posix()}"
                yield DriveFile(
                    source_id=self.source_id, file_key=key, path=key, name=d, size=None,
                    modified=_iso(st.st_mtime), revision=f"{st.st_mtime_ns}:pkg")
            for name in sorted(filenames):
                real = real_name(name)
                if real is None:
                    continue
                try:
                    st = os.stat(here / name, follow_symlinks=False)
                except FileNotFoundError:
                    continue            # removed between the walk and the stat
                except OSError as exc:
                    errors.append(exc)
                    continue
                if not stat.S_ISREG(st.st_mode):
                    continue
                key = f"{root.label}/{(here / real).relative_to(root.path).as_posix()}"
                yield DriveFile(
                    source_id=self.source_id, file_key=key, path=key, name=real,
                    size=None if real != name else st.st_size,
                    modified=_iso(st.st_mtime),
                    revision=f"{st.st_mtime_ns}:{st.st_size}")

    def fetch(self, f: DriveFile, dest_dir: Path) -> Path:
        label, _, rel = f.file_key.partition("/")
        root = self._by_label.get(label)
        if root is None:
            raise FetchError(f"{f.path}: no folder called {label!r} is read")
        path = root.path / Path(rel)
        if not path.exists():
            stub = path.with_name(f".{path.name}{STUB_SUFFIX}")
            if not stub.exists():
                raise FetchError(f"{f.path}: no longer there")
            path = stub
        try:
            if watchfolder.is_dataless(path):
                path = watchfolder.materialise(path)
            Path(dest_dir).mkdir(parents=True, exist_ok=True)
            dest = Path(dest_dir) / safe_name(f.name)
            shutil.copyfile(path, dest)
        except (OSError, TimeoutError) as exc:
            raise FetchError(f"{f.path}: {exc}") from exc
        return dest
