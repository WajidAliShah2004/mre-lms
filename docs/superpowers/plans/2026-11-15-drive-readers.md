# Drive Readers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Index every file in iCloud Drive, Google Drive and Dropbox, read-only, and file new documents from them into the archive (R5, D-083).

**Architecture:** One engine (`core/pipeline/drives.py`) keeps a `drive_files` index and files settled new documents through the existing `ingest_file`. Three thin adapters supply `list(cursor)` and `fetch(file, dest_dir)`: iCloud Drive from its folder on the Mac, Google Drive through `google-api-python-client` (already used for Gmail), Dropbox through its HTTP API with `urllib`. A launchd job runs every active drive every 3 hours; a backfill files older documents by folder and date.

**Tech Stack:** Python 3.12 on the Mac (tests also run on Windows, Python 3.14), SQLite, `google-api-python-client` / `google-auth` (already installed), `urllib`, pytest.

**Spec:** [docs/superpowers/specs/2026-11-15-drive-readers-design.md](../specs/2026-11-15-drive-readers-design.md)

## Global Constraints

- **Read-only, always.** No adapter may write, move, rename, delete or change permissions on anything in a drive. iCloud files are opened read-only and copied with `shutil.copyfile`; Google and Dropbox only list and download.
- **Google scope:** `https://www.googleapis.com/auth/drive.readonly` and nothing else.
- **Dropbox scopes:** `files.metadata.read`, `files.content.read`, `account_info.read` and nothing else.
- **Keychain items:** `lms/gdrive-oauth/<address>` and `lms/dropbox-oauth/<address>`, written through `security -i` (reuse `ops/authorise_gmail.py`'s `keychain_write`). No token on argv or in shell history.
- **Documents** (filed): extensions `pdf jpg jpeg png heic tif tiff doc docx xls xlsx ppt pptx txt rtf csv md eml`, plus Google Docs, Sheets and Slides (exported as PDF). Everything else is indexed only.
- **Filing rules:** created or changed on or after the drive's start date (local day, America/New_York); unchanged for **24 hours**; **≤ 100 MB** (`100 * 1024 * 1024`); current revision not already filed; fewer than **3** failed attempts on this revision; not deleted.
- **Areas filed:** `own` and `shared_drive`. `shared_with_me` (Google) is indexed only. The saved iCloud copies are indexed only.
- **Skipped:** the top-level `LMS` folder of iCloud Drive, and hidden files and folders.
- **Probe and dry run print counts only:** no file or folder names.
- **Log actions:** `DRIVES_CHECKED`, `DRIVES_FAILED` (watched by the brief, 7-hour window), `DRIVE_FILE_FAILED`.
- **Job:** `com.lms.drives`, every 3 hours (`StartInterval` 10800).
- **No new dependencies.**
- **Tests** run on Windows. Test command, from `lms/`:
  `C:/Users/murta/AppData/Local/Temp/claude/2af8ed49a9d09b06/f6bbf9fe-bd98-476b-a999-c3afdc2be58b/scratchpad/venv/Scripts/python.exe -m pytest -o addopts="" -p no:cacheprovider --ignore=tests/test_halt.py -q`
- Test files that import an ops script which imports `_halt` must load it behind the Windows `os.killpg` shim (see `tests/test_imessage_job.py`).
- New ops scripts with a shebang must be committed executable: `git add --chmod=+x <file>`.
- Plist comments must not contain a double hyphen.
- Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Refinements to the spec

Decided while planning; each is the smaller change that keeps the spec's behaviour.

1. **The start date lives in `drive_cursors.started_on`**, set by a drive's first pass, not in `sources.yaml` (the coverage register's keys are fixed by `core/pipeline/coverage.py`). "Switched on" means `status: active` in the register; the first pass, by hand or by the job, sets the date.
2. **Dropbox's `file_key` is `path_lower`**, not the `id:`. A deleted entry carries only a path, so ids could not be matched to deletions. A moved file becomes a deletion plus a new file; ingest's sha256 check makes its second filing a recorded duplicate.
3. **Dropbox's register id is `storage:dropbox:matthew@mrecai.com`**, the same shape as the Google ids, so the job reads the address from the id.
4. **A deleted key also marks everything under it** (`key/…`): Dropbox deletes a folder as one entry.
5. **Paths start with a root label** (`iCloud Drive/…`, `Pages/…`, `My Drive/…`, `<shared drive>/…`, `Shared with me/…`, `Dropbox/…`), which is what `--folder` matches.
6. **The job runs register sources with `status: active` and a cadence.** The saved iCloud copies have no cadence: they run only with `--source`.
7. **The dry run prints counts, not names,** as the probe does: these outputs get pasted into chat.
8. **`started_on` is added to `drive_cursors`** (the spec's table had `cursor` and `updated_at` only).

## File structure

| File | Task | Responsibility |
|---|---|---|
| `lms/core/adapters/drive.py` | 1 | `DriveFile`, `Listing`, `Lister`, `Fetcher`, `DriveError`, `FetchError`, `DOCUMENT_SUFFIXES`, `safe_name`, `wrong_account` |
| `lms/core/db/schema.sql` | 1 | `drive_files`, `drive_cursors` |
| `lms/core/adapters/icloud_drive.py` | 2 | `Root`, `icloud_roots`, `archive_roots`, `real_name`, `ICloudDrive` |
| `lms/core/pipeline/drives.py` | 3 | `DriveSource`, `PassResult`, `verdict`, `index`, `choose`, `file_rows`, `plan`, `run_pass`, `start_date`, `started_on`, `when`, `local_day` |
| `lms/tests/drive_fixture.py` | 3 | `FakeDrive` for engine and job tests |
| `lms/core/adapters/gdrive.py` | 4 | `SCOPES`, `keychain_service`, `DriveAPI`, `GoogleDriveAPI`, `CursorExpired`, `cursor_gone`, `explain`, `GDrive` |
| `lms/core/adapters/dropbox.py` | 5 | `SCOPES`, `TOKEN_URL`, `keychain_service`, `HTTPError`, `urllib_post`, `DropboxAPI`, `explain`, `Dropbox` |
| `lms/ops/drives.py` | 6 | the job: `build_source`, `has_reader`, `scheduled_sources`, `do_probe`, `do_dry_run`, `run`, `main` |
| `lms/ops/backfill_drives.py` | 6 | `backfill`, `index_problem`, `main` |
| `lms/ops/com.lms.drives.plist` | 6 | launchd, every 3 hours |
| `lms/config/sources.yaml` | 6 | the five storage lines |
| `lms/core/reports/brief.py` | 6 | `DRIVES_CHECKED`, `DRIVES_FAILED` |
| `lms/core/reports/storage.py` | 6 | drive sizes and reclaimable bytes from the index |
| `lms/ops/authorise_gdrive.py` | 7 | one read-only Drive grant per account |
| `lms/ops/authorise_dropbox.py` | 7 | one read-only Dropbox grant (PKCE, offline) |
| `lms/ops/DRIVES_APPLY.md` | 8 | setup on the Mac |
| `DECISIONS.md`, `REQUIREMENTS_REMAINING.md` | 8 | D-083, R5 |

---

### Task 1: Shared drive types and tables

**Files:**
- Create: `lms/core/adapters/drive.py`
- Modify: `lms/core/db/schema.sql` (append at the end)
- Test: `lms/tests/test_drive_types.py`

**Interfaces:**
- Produces: `DriveFile(source_id, file_key, path, name, size, modified, revision, area="own", mime=None, native=False)` with property `is_document`; `Listing(files=(), deleted=(), full=False, cursor=None)`; `Lister.list(cursor: str | None) -> Listing`; `Fetcher.fetch(f: DriveFile, dest_dir: Path) -> Path`; `DriveError`, `FetchError`; `DOCUMENT_SUFFIXES`; `AREAS`; `safe_name(name: str) -> str`; `wrong_account(wanted: str, actual: str | None, where: str) -> str | None`. Tables `drive_files` and `drive_cursors`.

- [ ] **Step 1: Write the failing test**

`lms/tests/test_drive_types.py`:

```python
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
```

- [ ] **Step 2: Run it to see it fail**

Run: `<test command> tests/test_drive_types.py`
Expected: FAIL, `ModuleNotFoundError: No module named 'core.adapters.drive'`.

- [ ] **Step 3: Write `lms/core/adapters/drive.py`**

```python
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
```

- [ ] **Step 4: Append to `lms/core/db/schema.sql`**

```sql

-- ---------------------------------------------------------------------------
-- drive_files — every file in every drive the LMS reads (R5, D-083). One row
-- per file per drive. A row is never deleted: a file gone from its drive gets
-- deleted_at. filed_revision is the revision last filed; a new revision is
-- filed again once settled.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS drive_files (
    source_id       TEXT    NOT NULL,          -- sources.yaml id, e.g. storage:icloud-drive
    file_key        TEXT    NOT NULL,
    path            TEXT    NOT NULL,
    name            TEXT    NOT NULL,
    area            TEXT    NOT NULL,          -- own | shared_drive | shared_with_me
    mime            TEXT,
    native          INTEGER NOT NULL DEFAULT 0,
    size            INTEGER,
    modified        TEXT    NOT NULL,
    revision        TEXT    NOT NULL,
    first_seen      TEXT    NOT NULL,
    last_seen       TEXT    NOT NULL,
    deleted_at      TEXT,
    filed_revision  TEXT,
    artifact_id     INTEGER REFERENCES artifacts(id),
    sha256          TEXT,
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    PRIMARY KEY (source_id, file_key)
);
CREATE INDEX IF NOT EXISTS idx_drive_files_modified ON drive_files(source_id, modified);

-- Per drive: where its incremental listing resumes, and the day of its first
-- pass, which is its start date (D-083).
CREATE TABLE IF NOT EXISTS drive_cursors (
    source_id       TEXT    PRIMARY KEY,
    cursor          TEXT,
    started_on      TEXT    NOT NULL,          -- YYYY-MM-DD, America/New_York
    updated_at      TEXT    NOT NULL
);
```

- [ ] **Step 5: Run the test and the whole suite**

Run: `<test command> tests/test_drive_types.py` → 4 passed. Then the full test command → all pass.

- [ ] **Step 6: Commit**

```bash
git add lms/core/adapters/drive.py lms/core/db/schema.sql lms/tests/test_drive_types.py
git commit -m "D-083: shared drive types and the drive_files and drive_cursors tables

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: iCloud Drive adapter

**Files:**
- Create: `lms/core/adapters/icloud_drive.py`
- Test: `lms/tests/test_icloud_drive.py`

**Interfaces:**
- Consumes: `DriveFile`, `Listing`, `DriveError`, `FetchError`, `safe_name` (Task 1); `core.adapters.watchfolder.is_dataless(path) -> bool` and `materialise(path) -> Path` (existing).
- Produces: `Root(label: str, path: Path, skip: tuple[str, ...] = ())`; `icloud_roots(mobile: Path = MOBILE_DOCUMENTS) -> list[Root]`; `archive_roots(storage: Path = CLOUD_STORAGE) -> list[Root]`; `real_name(name: str) -> str | None`; `ICloudDrive(source_id: str, roots: list[Root])` with `list(cursor=None) -> Listing` and `fetch(f, dest_dir) -> Path`. File keys and paths are `"<root label>/<relative posix path>"`.

- [ ] **Step 1: Write the failing test**

`lms/tests/test_icloud_drive.py`:

```python
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
    # The real names hold "11:47 AM"; Windows cannot create a ":" in a name,
    # and only the prefix matters here.
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
```

- [ ] **Step 2: Run it to see it fail**

Run: `<test command> tests/test_icloud_drive.py`
Expected: FAIL, `ModuleNotFoundError: No module named 'core.adapters.icloud_drive'`.

- [ ] **Step 3: Write `lms/core/adapters/icloud_drive.py`**

```python
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


class Root(NamedTuple):
    label: str
    path: Path
    skip: tuple[str, ...] = ()      # top-level folder names not read


def icloud_roots(mobile: Path = MOBILE_DOCUMENTS) -> list[Root]:
    roots = [Root("iCloud Drive", mobile / CLOUD_DOCS, ("LMS",))]
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
        files: dict[str, DriveFile] = {}
        for root in present:
            for f in self._walk(root):
                old = files.get(f.file_key)
                # A file and its own placeholder side by side: keep the file.
                if old is None or old.size is None:
                    files[f.file_key] = f
        return Listing(tuple(files.values()), (), full=True, cursor=None)

    def _walk(self, root: Root):
        for dirpath, dirnames, filenames in os.walk(root.path):
            here = Path(dirpath)
            top = here == root.path
            dirnames[:] = sorted(d for d in dirnames
                                 if not d.startswith(".") and not (top and d in root.skip))
            for name in sorted(filenames):
                real = real_name(name)
                if real is None:
                    continue
                try:
                    st = os.stat(here / name, follow_symlinks=False)
                except OSError:
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
```

- [ ] **Step 4: Run the tests**

Run: `<test command> tests/test_icloud_drive.py` → 9 passed. Then the full test command → all pass.

- [ ] **Step 5: Commit**

```bash
git add lms/core/adapters/icloud_drive.py lms/tests/test_icloud_drive.py
git commit -m "D-083: iCloud Drive reader, stat-only listing, copy-out fetch, LMS folder skipped

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The drive engine

**Files:**
- Create: `lms/core/pipeline/drives.py`
- Create: `lms/tests/drive_fixture.py`
- Test: `lms/tests/test_drives.py`

**Interfaces:**
- Consumes: Task 1 types; `ingest_file(conn, roots, registry, classifier, source_path, *, source, source_ref, artifact, ...) -> IngestResult` (`core/pipeline/ingest.py`; it MOVES the file it is given, so only temp copies go in); `Artifact` (`core/pipeline/classify.py`); `coverage.record_check(conn, source_id, result, *, started_at, items, detail)`; `db.log_action`, `db.now_iso`, `db.find_artifact_by_hash`.
- Produces:
  - `DriveSource(source_id, label, lister, fetcher, files_documents=True, filed_areas=FILED_AREAS)`
  - `PassResult(source_id)` with counters `listed, new, changed, deleted, filed, duplicates, failed, too_large`, `full`, `error`, and `result` (`OK|PARTIAL|FAILED`), `summary()`.
  - `when(iso: str) -> datetime`, `local_day(d: datetime) -> str`
  - `start_date(conn, source_id) -> str | None`, `started_on(conn, source_id, now) -> str`
  - `verdict(src, f, *, filed_revision, attempts, start, now, until=None, folder=None) -> str` (`"file"`, `"too_large"` or `""`)
  - `index(src, conn, now, result) -> None`
  - `choose(src, conn, now, *, start, until=None, folder=None, limit=None) -> tuple[list[DriveFile], int]`
  - `file_rows(src, conn, roots, registry, classifier, files, tmp, result, *, ingest=ingest_file, halted=...) -> int`
  - `plan(src, conn, now) -> tuple[list[DriveFile], int, int]` (would file, too large, listed; writes nothing)
  - `run_pass(src, conn, roots, registry, classifier, *, now, tmp_root, ingest=ingest_file, halted=...) -> PassResult`
  - Constants `SETTLE`, `MAX_BYTES`, `MAX_ATTEMPTS`, `FILED_AREAS`, `CHECKED = "DRIVES_CHECKED"`, `FAILED = "DRIVES_FAILED"`, `FILE_FAILED = "DRIVE_FILE_FAILED"`.
  - `tests/drive_fixture.py`: `SID = "storage:test"`, `FakeDrive` (used again in Task 6).

- [ ] **Step 1: Write the fixture**

`lms/tests/drive_fixture.py`:

```python
"""An in-memory drive for engine and job tests (D-083)."""

from pathlib import Path

from core.adapters.drive import DriveError, DriveFile, FetchError, Listing

SID = "storage:test"


class FakeDrive:
    """Lists what was put in it; fetch copies the bytes and records the key."""

    def __init__(self, source_id: str = SID) -> None:
        self.source_id = source_id
        self.files: dict[str, DriveFile] = {}
        self.data: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.full = True
        self.cursor = "c1"
        self.fail_list: str | None = None
        self.fail_fetch: set[str] = set()
        self.fetched: list[str] = []

    def put(self, key: str, body: bytes = b"x", *, modified: str, size: int | None = None,
            area: str = "own", native: bool = False, revision: str | None = None) -> DriveFile:
        name = key.rsplit("/", 1)[-1]
        f = DriveFile(self.source_id, key, f"Test/{key}", name,
                      len(body) if size is None else size, modified,
                      revision or f"{modified}:{len(body)}", area, None, native)
        self.files[key] = f
        self.data[key] = body
        return f

    def list(self, cursor):
        if self.fail_list:
            raise DriveError(self.fail_list)
        return Listing(tuple(self.files.values()), tuple(self.deleted), self.full, self.cursor)

    def fetch(self, f, dest_dir):
        if f.file_key in self.fail_fetch:
            raise FetchError(f"{f.path}: no bytes")
        self.fetched.append(f.file_key)
        Path(dest_dir).mkdir(parents=True, exist_ok=True)
        out = Path(dest_dir) / f.name
        out.write_bytes(self.data[f.file_key])
        return out
```

- [ ] **Step 2: Write the failing test**

`lms/tests/test_drives.py`:

```python
"""The drive engine (R5, D-083): index everything, file settled new documents."""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from core.adapters.lmstudio import Completion
from core.db import database as db
from core.pipeline import drives, filing
from core.pipeline.classify import Classifier
from core.pipeline.registry import load_registry
from tests.drive_fixture import SID, FakeDrive

ET = ZoneInfo("America/New_York")
CONFIG = Path(__file__).resolve().parents[1] / "config"
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=ET)


class FakeModel:
    def complete(self, **kw):
        return Completion(text=json.dumps({
            "domain": "BUSINESS", "entity_id": "B_MRE", "category": "VENDORS",
            "subcategory": "invoices-received", "urgency": "NORMAL",
            "confidence": 0.93, "requires_reply": False, "due_date": None,
            "counterparty": "acme-supply", "descriptor": "drive document",
            "amount_cents": None, "rationale": "vendor"}),
            model="fake", elapsed_s=0.01, finish_reason="stop")


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "lms.db")
    yield c
    c.close()


@pytest.fixture
def pipe(tmp_path):
    roots = filing.StorageRoots(archive=tmp_path / "archive",
                                originals=tmp_path / "_originals",
                                quarantine=tmp_path / "quarantine")
    roots.ensure()
    registry = load_registry(CONFIG)
    return roots, registry, Classifier(registry, client=FakeModel())


def source(drive, **kw):
    return drives.DriveSource(SID, "Test Drive", drive, drive, **kw)


def begin(conn, day="2026-11-01"):
    conn.execute("INSERT INTO drive_cursors (source_id, cursor, started_on, updated_at) "
                 "VALUES (?, NULL, ?, ?)", (SID, day, db.now_iso()))
    conn.commit()


def run(conn, pipe, src, now=NOW, **kw):
    roots, registry, clf = pipe
    return drives.run_pass(src, conn, roots, registry, clf, now=now,
                           tmp_root=roots.archive.parent / "tmp", **kw)


def row(conn, key):
    return conn.execute("SELECT * FROM drive_files WHERE source_id = ? AND file_key = ?",
                        (SID, key)).fetchone()


def count(conn, sql, *args):
    return conn.execute(sql, args).fetchone()[0]


def test_the_first_pass_indexes_everything_and_sets_the_start_date(conn, pipe):
    d = FakeDrive()
    d.put("old.txt", b"Old invoice from Acme for 300 dollars", modified="2026-10-01T09:00:00-04:00")
    d.put("clip.mp4", b"video", modified="2026-11-18T09:00:00-05:00")
    r = run(conn, pipe, source(d))
    assert (r.result, r.listed, r.new, r.filed) == ("OK", 2, 2, 0)
    assert drives.start_date(conn, SID) == "2026-11-20"
    assert count(conn, "SELECT COUNT(*) FROM drive_files") == 2
    assert d.fetched == []


def test_settled_new_documents_are_filed_and_the_rest_only_indexed(conn, pipe):
    begin(conn)
    d = FakeDrive()
    d.put("Clients/lease.txt", b"Lease for 12 Main St, rent 2400 dollars a month",
          modified="2026-11-18T09:00:00-05:00")
    d.put("fresh.txt", b"Draft letter still being written", modified="2026-11-20T10:00:00-05:00")
    d.put("clip.mp4", b"video bytes", modified="2026-11-18T09:00:00-05:00")
    d.put("big.pdf", b"x", size=drives.MAX_BYTES + 1, modified="2026-11-18T09:00:00-05:00")
    d.put("theirs.txt", b"Quote from a broker for the renewal",
          modified="2026-11-18T09:00:00-05:00", area="shared_with_me")
    d.put("before.txt", b"An October statement", modified="2026-10-20T09:00:00-04:00")
    r = run(conn, pipe, source(d))
    assert (r.result, r.filed, r.too_large, r.duplicates) == ("OK", 1, 1, 0)
    assert d.fetched == ["Clients/lease.txt"]
    lease = row(conn, "Clients/lease.txt")
    assert lease["filed_revision"] == lease["revision"]
    assert lease["artifact_id"] and lease["sha256"]
    art = conn.execute("SELECT source, source_ref FROM artifacts WHERE id = ?",
                       (lease["artifact_id"],)).fetchone()
    assert art["source"] == SID and art["source_ref"].startswith("Clients/lease.txt@")
    check = conn.execute("SELECT result, items FROM source_checks WHERE source_id = ?",
                         (SID,)).fetchone()
    assert (check["result"], check["items"]) == ("OK", 6)
    assert count(conn, "SELECT COUNT(*) FROM actions_log WHERE action = 'DRIVES_CHECKED'") == 1
    assert "over 100 MB" in r.summary()
    assert list((pipe[0].archive.parent / "tmp").iterdir()) == []


def test_a_filed_revision_is_not_filed_again_but_a_new_one_is(conn, pipe):
    begin(conn)
    d = FakeDrive()
    d.put("a.txt", b"Invoice 100 from Acme Supply for 500 dollars",
          modified="2026-11-17T09:00:00-05:00")
    assert run(conn, pipe, source(d)).filed == 1
    assert run(conn, pipe, source(d)).filed == 0
    d.put("a.txt", b"Invoice 100 from Acme Supply for 650 dollars, corrected",
          modified="2026-11-18T09:00:00-05:00")
    r = run(conn, pipe, source(d))
    assert (r.changed, r.filed) == (1, 1)
    assert d.fetched == ["a.txt", "a.txt"]
    assert count(conn, "SELECT COUNT(*) FROM artifacts WHERE source = ?", SID) == 2


def test_bytes_already_in_the_archive_are_a_duplicate_not_a_second_copy(conn, pipe):
    begin(conn)
    d = FakeDrive()
    body = b"Insurance policy renewal notice for policy 778"
    d.put("one.txt", body, modified="2026-11-17T09:00:00-05:00")
    d.put("copy.txt", body, modified="2026-11-17T10:00:00-05:00")
    r = run(conn, pipe, source(d))
    assert (r.filed, r.duplicates) == (1, 1)
    assert row(conn, "copy.txt")["filed_revision"] is not None
    assert count(conn, "SELECT COUNT(*) FROM artifacts WHERE source = ?", SID) == 1


def test_a_file_that_cannot_be_fetched_is_tried_three_times(conn, pipe):
    begin(conn)
    d = FakeDrive()
    d.put("stuck.txt", b"Unreachable document text", modified="2026-11-17T09:00:00-05:00")
    d.fail_fetch.add("stuck.txt")
    results = [run(conn, pipe, source(d)) for _ in range(4)]
    assert [r.result for r in results] == ["PARTIAL", "PARTIAL", "PARTIAL", "OK"]
    assert row(conn, "stuck.txt")["attempts"] == 3
    assert "no bytes" in row(conn, "stuck.txt")["last_error"]
    assert count(conn, "SELECT COUNT(*) FROM actions_log WHERE action = 'DRIVE_FILE_FAILED'") == 3


def test_a_new_revision_gets_fresh_attempts(conn, pipe):
    begin(conn)
    d = FakeDrive()
    d.put("stuck.txt", b"Unreachable document text", modified="2026-11-16T09:00:00-05:00")
    d.fail_fetch.add("stuck.txt")
    for _ in range(3):
        run(conn, pipe, source(d))
    d.fail_fetch.clear()
    d.put("stuck.txt", b"Now reachable document text", modified="2026-11-17T09:00:00-05:00")
    assert run(conn, pipe, source(d)).filed == 1


def test_a_listing_that_fails_fails_the_pass(conn, pipe):
    d = FakeDrive()
    d.fail_list = "grant revoked: re-authorise"
    r = run(conn, pipe, source(d))
    assert r.result == "FAILED" and "re-authorise" in r.summary()
    assert conn.execute("SELECT result FROM source_checks").fetchone()["result"] == "FAILED"
    assert count(conn, "SELECT COUNT(*) FROM actions_log WHERE action = 'DRIVES_FAILED'") == 1


def test_deleted_files_are_marked_never_removed(conn, pipe):
    d = FakeDrive()
    for k in ("keep.txt", "Old/a.txt", "Old/b.txt", "gone.txt"):
        d.put(k, k.encode(), modified="2026-10-01T09:00:00-04:00")
    run(conn, pipe, source(d))
    d.full, d.deleted = False, ["Old"]
    assert run(conn, pipe, source(d)).deleted == 2
    d.full, d.deleted = True, []
    for k in ("gone.txt", "Old/a.txt", "Old/b.txt"):
        del d.files[k]
    assert run(conn, pipe, source(d)).deleted == 1
    assert count(conn, "SELECT COUNT(*) FROM drive_files") == 4
    assert row(conn, "keep.txt")["deleted_at"] is None
    assert row(conn, "gone.txt")["deleted_at"] is not None


def test_the_cursor_is_kept_for_the_next_listing(conn, pipe):
    d = FakeDrive()
    d.cursor = "after-pass-1"
    seen = []
    real = d.list
    d.list = lambda cursor: (seen.append(cursor), real(cursor))[1]
    run(conn, pipe, source(d))
    run(conn, pipe, source(d))
    assert seen == [None, "after-pass-1"]


def test_a_source_that_files_nothing_only_indexes(conn, pipe):
    begin(conn)
    d = FakeDrive()
    d.put("a.txt", b"Statement for October from the bank", modified="2026-11-17T09:00:00-05:00")
    r = run(conn, pipe, source(d, files_documents=False))
    assert (r.listed, r.filed) == (1, 0) and d.fetched == []


def test_a_halt_stops_before_the_next_file(conn, pipe):
    begin(conn)
    d = FakeDrive()
    d.put("a.txt", b"First document text here", modified="2026-11-17T09:00:00-05:00")
    d.put("b.txt", b"Second document text here", modified="2026-11-17T10:00:00-05:00")
    r = run(conn, pipe, source(d), halted=lambda: len(d.fetched) >= 1)
    assert d.fetched == ["a.txt"] and r.filed == 1


def test_plan_writes_nothing(conn, pipe):
    begin(conn)
    d = FakeDrive()
    d.put("a.txt", b"x doc", modified="2026-11-17T09:00:00-05:00")
    d.put("b.txt", b"y doc", modified="2026-11-01T09:00:00-04:00")
    d.put("big.pdf", b"x", size=drives.MAX_BYTES + 1, modified="2026-11-17T09:00:00-05:00")
    d.put("clip.mp4", b"v", modified="2026-11-17T09:00:00-05:00")
    would, big, listed = drives.plan(source(d), conn, NOW)
    assert sorted(f.file_key for f in would) == ["a.txt", "b.txt"]
    assert (big, listed) == (1, 4)
    assert count(conn, "SELECT COUNT(*) FROM drive_files") == 0
    assert d.fetched == []


def test_choose_narrows_by_dates_and_folder_for_the_backfill(conn, pipe):
    d = FakeDrive()
    d.put("Clients/2025/a.txt", b"a", modified="2025-03-01T09:00:00-05:00")
    d.put("Clients/2025/b.txt", b"b", modified="2025-08-01T09:00:00-04:00")
    d.put("Other/c.txt", b"c", modified="2025-03-01T09:00:00-05:00")
    run(conn, pipe, source(d))
    files, _ = drives.choose(source(d), conn, NOW, start="2025-01-01",
                             until="2025-06-30", folder="Test/Clients")
    assert [f.file_key for f in files] == ["Clients/2025/a.txt"]
    files, _ = drives.choose(source(d), conn, NOW, start="2025-01-01", limit=2)
    assert [f.file_key for f in files] == ["Clients/2025/a.txt", "Other/c.txt"]


def test_when_reads_z_and_offsets():
    assert drives.when("2026-11-18T14:00:00Z").isoformat() == "2026-11-18T14:00:00+00:00"
    assert drives.local_day(drives.when("2026-11-18T03:00:00Z")) == "2026-11-17"
```

- [ ] **Step 3: Run it to see it fail**

Run: `<test command> tests/test_drives.py`
Expected: FAIL, `ImportError: cannot import name 'drives' from 'core.pipeline'`.

- [ ] **Step 4: Write `lms/core/pipeline/drives.py`**

```python
"""The drive readers' engine (R5, D-083): index every file, file new documents.

One pass over one drive:

1. Index. The drive's lister returns its files: all of them, or the changes
   since the stored cursor. Each is upserted into drive_files; files gone
   from the drive get deleted_at. No row is ever removed.
2. Choose. A file is filed when it is a document, in an area that is filed,
   created or changed on or after the drive's start date, unchanged for 24
   hours, at most 100 MB, its current revision is not already filed, and it
   has failed fewer than 3 times.
3. File. The fetcher copies its bytes into a temp directory, and ingest_file
   files that copy (filing moves what it is given, so it is always a copy).
   The original in the drive is never touched.

The start date is the day of the drive's first pass (drive_cursors). Its
history is the backfill's (ops/backfill_drives.py).
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from ..adapters.drive import DriveFile, Fetcher, Lister
from ..db import database as db
from . import coverage
from .classify import Artifact
from .ingest import ingest_file

TZ = ZoneInfo("America/New_York")
SETTLE = timedelta(hours=24)
MAX_BYTES = 100 * 1024 * 1024
MAX_ATTEMPTS = 3
FILED_AREAS = frozenset({"own", "shared_drive"})
CHECKED = "DRIVES_CHECKED"
FAILED = "DRIVES_FAILED"
FILE_FAILED = "DRIVE_FILE_FAILED"


def _never() -> bool:
    return False


@dataclass(frozen=True)
class DriveSource:
    source_id: str
    label: str
    lister: Lister
    fetcher: Fetcher
    files_documents: bool = True
    filed_areas: frozenset[str] = FILED_AREAS


@dataclass
class PassResult:
    source_id: str
    listed: int = 0
    new: int = 0
    changed: int = 0
    deleted: int = 0
    full: bool = False
    filed: int = 0
    duplicates: int = 0
    failed: int = 0
    too_large: int = 0
    error: str | None = None
    failures: list[str] = field(default_factory=list)

    @property
    def result(self) -> str:
        if self.error:
            return "FAILED"
        return "PARTIAL" if self.failed else "OK"

    def summary(self) -> str:
        if self.error:
            return self.error
        s = (f"{self.listed} file(s) listed ({'full' if self.full else 'changes'}): "
             f"{self.new} new, {self.changed} changed, {self.deleted} deleted; "
             f"{self.filed} filed, {self.duplicates} already in the archive")
        if self.too_large:
            s += f", {self.too_large} over 100 MB not filed"
        if self.failed:
            s += f", {self.failed} FAILED (see {FILE_FAILED} in actions_log)"
        return s


def when(iso: str) -> datetime:
    d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=TZ)


def local_day(d: datetime) -> str:
    return d.astimezone(TZ).date().isoformat()


def start_date(conn: sqlite3.Connection, source_id: str) -> str | None:
    row = conn.execute("SELECT started_on FROM drive_cursors WHERE source_id = ?",
                       (source_id,)).fetchone()
    return row["started_on"] if row else None


def started_on(conn: sqlite3.Connection, source_id: str, now: datetime) -> str:
    """The drive's start date; the first pass sets it to today."""
    day = start_date(conn, source_id)
    if day is None:
        day = local_day(now)
        conn.execute("INSERT INTO drive_cursors (source_id, cursor, started_on, updated_at) "
                     "VALUES (?, NULL, ?, ?)", (source_id, day, db.now_iso()))
        conn.commit()
    return day


def verdict(src: DriveSource, f: DriveFile, *, filed_revision: str | None,
            attempts: int, start: str, now: datetime, until: str | None = None,
            folder: str | None = None) -> str:
    """'file', 'too_large' or '' (not filed): the spec's rules, in order."""
    if not src.files_documents or f.area not in src.filed_areas or not f.is_document:
        return ""
    if filed_revision == f.revision or attempts >= MAX_ATTEMPTS:
        return ""
    changed = when(f.modified)
    day = local_day(changed)
    if day < start or (until is not None and day > until):
        return ""
    if folder is not None:
        top = folder.rstrip("/")
        if f.path != top and not f.path.startswith(top + "/"):
            return ""
    if changed > now - SETTLE:
        return ""
    if f.size is not None and f.size > MAX_BYTES:
        return "too_large"
    return "file"


def _under(key: str) -> str:
    """A LIKE pattern for everything below `key`."""
    esc = key.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return esc.rstrip("/") + "/%"


def index(src: DriveSource, conn: sqlite3.Connection, now: datetime,
          result: PassResult) -> None:
    row = conn.execute("SELECT cursor FROM drive_cursors WHERE source_id = ?",
                       (src.source_id,)).fetchone()
    listing = src.lister.list(row["cursor"] if row else None)
    stamp = now.isoformat(timespec="seconds")
    result.full = listing.full
    result.listed = len(listing.files)
    known = {r["file_key"]: r for r in conn.execute(
        "SELECT file_key, revision, deleted_at FROM drive_files WHERE source_id = ?",
        (src.source_id,))}
    for f in listing.files:
        old = known.get(f.file_key)
        values = (f.path, f.name, f.area, f.mime, int(f.native), f.size, f.modified,
                  f.revision)
        if old is None:
            conn.execute(
                "INSERT INTO drive_files (source_id, file_key, path, name, area, mime, "
                "native, size, modified, revision, first_seen, last_seen) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (src.source_id, f.file_key, *values, stamp, stamp))
            result.new += 1
            continue
        changed = old["revision"] != f.revision
        if changed:
            result.changed += 1
        conn.execute(
            "UPDATE drive_files SET path = ?, name = ?, area = ?, mime = ?, native = ?, "
            "size = ?, modified = ?, revision = ?, last_seen = ?, deleted_at = NULL"
            + (", attempts = 0, last_error = NULL" if changed else "")
            + " WHERE source_id = ? AND file_key = ?",
            (*values, stamp, src.source_id, f.file_key))
    gone = set(listing.deleted)
    if listing.full:
        seen = {f.file_key for f in listing.files}
        gone |= {k for k, r in known.items() if k not in seen and r["deleted_at"] is None}
    for key in sorted(gone):
        result.deleted += conn.execute(
            "UPDATE drive_files SET deleted_at = ? WHERE source_id = ? AND deleted_at IS NULL "
            "AND (file_key = ? OR file_key LIKE ? ESCAPE '\\')",
            (stamp, src.source_id, key, _under(key))).rowcount
    conn.execute(
        "INSERT INTO drive_cursors (source_id, cursor, started_on, updated_at) "
        "VALUES (?, ?, ?, ?) ON CONFLICT(source_id) DO UPDATE SET "
        "cursor = excluded.cursor, updated_at = excluded.updated_at",
        (src.source_id, listing.cursor, local_day(now), db.now_iso()))
    conn.commit()


def _row_file(source_id: str, r: sqlite3.Row) -> DriveFile:
    return DriveFile(source_id, r["file_key"], r["path"], r["name"], r["size"],
                     r["modified"], r["revision"], r["area"], r["mime"], bool(r["native"]))


def choose(src: DriveSource, conn: sqlite3.Connection, now: datetime, *, start: str,
           until: str | None = None, folder: str | None = None,
           limit: int | None = None) -> tuple[list[DriveFile], int]:
    """(files to file, oldest first; how many documents are over 100 MB)."""
    if not src.files_documents:
        return [], 0
    chosen: list[DriveFile] = []
    big = 0
    for r in conn.execute("SELECT * FROM drive_files WHERE source_id = ? "
                          "AND deleted_at IS NULL", (src.source_id,)).fetchall():
        f = _row_file(src.source_id, r)
        v = verdict(src, f, filed_revision=r["filed_revision"], attempts=r["attempts"],
                    start=start, now=now, until=until, folder=folder)
        if v == "file":
            chosen.append(f)
        elif v == "too_large":
            big += 1
    chosen.sort(key=lambda f: (when(f.modified), f.file_key))
    return (chosen[:limit] if limit else chosen), big


def file_rows(src: DriveSource, conn: sqlite3.Connection, roots, registry, classifier,
              files: list[DriveFile], tmp: Path, result: PassResult, *,
              ingest=ingest_file, halted: Callable[[], bool] = _never) -> int:
    """File each file from a temp copy. Returns how many were handled."""
    done = 0
    for f in files:
        if halted():
            break
        ref = f"{f.file_key}@{f.revision}"
        work = Path(tempfile.mkdtemp(prefix="file-", dir=tmp))
        try:
            copy = src.fetcher.fetch(f, work)
            art = Artifact(source=src.source_id, source_ref=ref,
                           subject=f"{src.label}: {f.path}",
                           received_date=local_day(when(f.modified)))
            res = ingest(conn, roots, registry, classifier, copy, source=src.source_id,
                         source_ref=ref, artifact=art)
        except Exception as exc:                              # noqa: BLE001
            why = f"{type(exc).__name__}: {exc}"
            result.failed += 1
            result.failures.append(f"{f.file_key}: {why}")
            conn.execute("UPDATE drive_files SET attempts = attempts + 1, last_error = ? "
                         "WHERE source_id = ? AND file_key = ?",
                         (why[:400], src.source_id, f.file_key))
            db.log_action(conn, FILE_FAILED, detail=f"{src.source_id} {f.file_key}: {why}"[:400])
            conn.commit()
            continue
        finally:
            shutil.rmtree(work, ignore_errors=True)
        art_row = db.find_artifact_by_hash(conn, res.sha256)
        conn.execute("UPDATE drive_files SET filed_revision = ?, artifact_id = ?, sha256 = ?, "
                     "attempts = 0, last_error = NULL WHERE source_id = ? AND file_key = ?",
                     (f.revision, int(art_row["id"]) if art_row else None, res.sha256,
                      src.source_id, f.file_key))
        conn.commit()
        if res.status == "DUPLICATE":
            result.duplicates += 1
        else:
            result.filed += 1
        done += 1
    return done


def plan(src: DriveSource, conn: sqlite3.Connection,
         now: datetime) -> tuple[list[DriveFile], int, int]:
    """For --dry-run: (would file, over 100 MB, listed), from a full live
    listing and what is already filed. Writes nothing."""
    listing = src.lister.list(None)
    start = start_date(conn, src.source_id) or local_day(now)
    rows = {r["file_key"]: r for r in conn.execute(
        "SELECT file_key, revision, filed_revision, attempts FROM drive_files "
        "WHERE source_id = ?", (src.source_id,))}
    would: list[DriveFile] = []
    big = 0
    for f in listing.files:
        r = rows.get(f.file_key)
        attempts = r["attempts"] if r is not None and r["revision"] == f.revision else 0
        v = verdict(src, f, filed_revision=r["filed_revision"] if r is not None else None,
                    attempts=attempts, start=start, now=now)
        if v == "file":
            would.append(f)
        elif v == "too_large":
            big += 1
    return would, big, len(listing.files)


def run_pass(src: DriveSource, conn: sqlite3.Connection, roots, registry, classifier, *,
             now: datetime, tmp_root: Path, ingest=ingest_file,
             halted: Callable[[], bool] = _never) -> PassResult:
    """Index, choose, file; record the pass however it ends."""
    started = db.now_iso()
    result = PassResult(src.source_id)
    Path(tmp_root).mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="pass-", dir=tmp_root))
    try:
        start = started_on(conn, src.source_id, now)
        index(src, conn, now, result)
        files, result.too_large = choose(src, conn, now, start=start)
        file_rows(src, conn, roots, registry, classifier, files, tmp, result,
                  ingest=ingest, halted=halted)
    except Exception as exc:                                  # noqa: BLE001
        result.error = f"{type(exc).__name__}: {exc}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    coverage.record_check(conn, src.source_id, result.result, started_at=started,
                          items=result.listed, detail=result.summary())
    db.log_action(conn, FAILED if result.error else CHECKED,
                  detail=f"{src.source_id}: {result.summary()}"[:400])
    conn.commit()
    return result
```

- [ ] **Step 5: Run the tests**

Run: `<test command> tests/test_drives.py` → 14 passed. Then the full test command → all pass. (The D-027 test in `tests/test_stalled_jobs.py` only checks actions the brief watches; the brief does not watch these until Task 6.)

- [ ] **Step 6: Commit**

```bash
git add lms/core/pipeline/drives.py lms/tests/drive_fixture.py lms/tests/test_drives.py
git commit -m "D-083: drive engine: index every file, file settled new documents from temp copies

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Google Drive adapter

**Files:**
- Create: `lms/core/adapters/gdrive.py`
- Test: `lms/tests/test_gdrive.py`

**Interfaces:**
- Consumes: Task 1 types; `core.adapters.gmail._with_backoff(call, *, note=...)` (existing retry helper).
- Produces: `SCOPES = ("https://www.googleapis.com/auth/drive.readonly",)`; `KEYCHAIN_PREFIX = "lms/gdrive-oauth/"`; `keychain_service(address) -> str`; `FOLDER`, `NATIVE`; `CursorExpired`; `cursor_gone(exc) -> bool`; `explain(exc, address) -> str`; `DriveAPI` protocol (`list_files(page_token)`, `start_page_token()`, `list_changes(page_token)`, `get_file(file_id)`, `download(file_id)`, `export_pdf(file_id)`, `user_email()`); `GoogleDriveAPI(credentials)`; `GDrive(source_id, address, api)` with `list(cursor) -> Listing`, `fetch(f, dest_dir) -> Path`.

- [ ] **Step 1: Write the failing test**

`lms/tests/test_gdrive.py`:

```python
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
```

- [ ] **Step 2: Run it to see it fail**

Run: `<test command> tests/test_gdrive.py`
Expected: FAIL, `ModuleNotFoundError: No module named 'core.adapters.gdrive'`.

- [ ] **Step 3: Write `lms/core/adapters/gdrive.py`**

```python
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
```

- [ ] **Step 4: Run the tests**

Run: `<test command> tests/test_gdrive.py` → 8 passed. Then the full test command → all pass.

- [ ] **Step 5: Commit**

```bash
git add lms/core/adapters/gdrive.py lms/tests/test_gdrive.py
git commit -m "D-083: Google Drive reader, drive.readonly, full listing then changes, Docs as PDF

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Dropbox adapter

**Files:**
- Create: `lms/core/adapters/dropbox.py`
- Test: `lms/tests/test_dropbox.py`

**Interfaces:**
- Consumes: Task 1 types.
- Produces: `SCOPES = ("files.metadata.read", "files.content.read", "account_info.read")`; `API`, `CONTENT`, `TOKEN_URL = "https://api.dropboxapi.com/oauth2/token"`; `KEYCHAIN_PREFIX = "lms/dropbox-oauth/"`; `keychain_service(address)`; `HTTPError(status, body)`; `urllib_post(url, data, headers) -> bytes`; `DropboxAPI(app_key, refresh_token, *, post=urllib_post, sleep=time.sleep)` with `access_token()`, `rpc(endpoint, args) -> dict`, `download(path) -> bytes`; `explain(exc, address) -> str`; `Dropbox(source_id, address, api)` with `list(cursor)`, `fetch(f, dest_dir)`.

- [ ] **Step 1: Write the failing test**

`lms/tests/test_dropbox.py`:

```python
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
```

- [ ] **Step 2: Run it to see it fail**

Run: `<test command> tests/test_dropbox.py`
Expected: FAIL, `ModuleNotFoundError: No module named 'core.adapters.dropbox'`.

- [ ] **Step 3: Write `lms/core/adapters/dropbox.py`**

```python
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
        files: list[DriveFile] = []
        deleted: list[str] = []
        while True:
            for e in page.get("entries", []):
                tag = e.get(".tag")
                if tag == "file":
                    files.append(self._file(e))
                elif tag == "deleted":
                    deleted.append(e["path_lower"])
            if not page.get("has_more"):
                break
            page = self.api.rpc("files/list_folder/continue", {"cursor": page["cursor"]})
        return Listing(tuple(files), tuple(deleted), full=full, cursor=page.get("cursor"))

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
```

- [ ] **Step 4: Run the tests**

Run: `<test command> tests/test_dropbox.py` → 10 passed. Then the full test command → all pass.

- [ ] **Step 5: Commit**

```bash
git add lms/core/adapters/dropbox.py lms/tests/test_dropbox.py
git commit -m "D-083: Dropbox reader, read-only scopes, offline refresh token, urllib only

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The job, the backfill, the register, the brief and the storage report

**Files:**
- Create: `lms/ops/drives.py`, `lms/ops/backfill_drives.py`, `lms/ops/com.lms.drives.plist`
- Modify: `lms/config/sources.yaml` (the Storage block), `lms/core/reports/brief.py` (`WATCHED_JOBS`, `JOB_FAILURES`), `lms/core/reports/storage.py`
- Test: `lms/tests/test_drives_job.py`, `lms/tests/test_storage_drives.py`, `lms/tests/test_drive_readonly.py`

**Interfaces:**
- Consumes: everything from Tasks 1–5; `_halt.read_flag()`, `_env.archive_volume_problem()`, `_env.load_lms_env()`, `_reexec.ensure_venv(guard, script=...)`; `coverage.load_register()`, `coverage.Source`; `storage.Tree`, `storage.Reclaim`, `storage.SourceLine`, `storage.NOT_MEASURED`, `storage.fmt_bytes`.
- Produces: `ops/drives.py`: `ICLOUD`, `ARCHIVE`, `GDRIVE`, `DROPBOX`, `keychain_json(service)`, `build_source(source_id) -> DriveSource`, `has_reader(source_id) -> bool`, `scheduled_sources(register) -> list[str]`, `do_probe(src) -> int`, `do_dry_run(src, conn, now) -> int`, `run(conn, roots, registry, classifier, sids, now, *, build=build_source, halted=...) -> int`, `main()`. `ops/backfill_drives.py`: `backfill(src, conn, roots, registry, classifier, *, now, tmp_root, since, until=None, folder=None, batch=20, halted=...) -> int`, `index_problem(conn, source_id) -> str | None`, `main()`. Storage: `drive_tree(conn, source_id) -> Tree`, `drive_reclaim(conn, source_id, filed_shas) -> Reclaim`.

- [ ] **Step 1: Write the failing tests**

`lms/tests/test_drives_job.py`:

```python
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
```

`lms/tests/test_storage_drives.py`:

```python
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
    assert not any("Dropbox, the wider iCloud Drive" in n for n in r.not_measured)
    conn.close()
```

`lms/tests/test_drive_readonly.py`:

```python
"""No drive reader can change a drive (R5, D-083; addendum §2.3)."""

import re
from pathlib import Path

CORE = Path(__file__).resolve().parents[1] / "core"
READERS = [CORE / "adapters" / n for n in ("drive.py", "icloud_drive.py", "gdrive.py",
                                           "dropbox.py")]
FORBIDDEN = [
    r"\bos\.(rename|replace|remove|unlink|rmdir|chmod|chown)\b",
    r"\bshutil\.(move|rmtree|copytree)\b",
    r"\.(unlink|rename|rmdir|chmod|touch)\(",
    r"\bfiles\(\)\.(delete|update|create|copy|emptyTrash)\b",
    r"\bpermissions\(\)",
    r"files/(delete|move|upload|copy|create_folder|save_url)",
    r"\bsharing/",
]


def code(path: Path) -> str:
    return "\n".join(line for line in path.read_text(encoding="utf-8").splitlines()
                     if not line.lstrip().startswith("#"))


def test_no_reader_calls_anything_that_changes_a_drive():
    for path in READERS:
        text = code(path)
        for pattern in FORBIDDEN:
            assert not re.search(pattern, text), f"{path.name} matches {pattern}"


def test_the_engine_only_removes_its_own_temp_directories():
    text = code(CORE / "pipeline" / "drives.py")
    for pattern in (r"\bos\.(rename|replace|remove|unlink)\b", r"\bshutil\.move\b"):
        assert not re.search(pattern, text)
    assert text.count("shutil.rmtree(") == 2, "only the pass and per-file temp directories"


def test_the_grants_are_read_only():
    from core.adapters import dropbox, gdrive
    assert gdrive.SCOPES == ("https://www.googleapis.com/auth/drive.readonly",)
    assert dropbox.SCOPES == ("files.metadata.read", "files.content.read", "account_info.read")
```

- [ ] **Step 2: Run them to see them fail**

Run: `<test command> tests/test_drives_job.py tests/test_storage_drives.py tests/test_drive_readonly.py`
Expected: FAIL: `ModuleNotFoundError: No module named 'backfill_drives'`, and the storage test fails on `storage:test` missing from the sources (`KeyError`). `test_drive_readonly.py` passes already (Tasks 1–5 wrote no forbidden calls); keep it.

- [ ] **Step 3: Write `lms/ops/drives.py`**

```python
#!/usr/bin/env python3
"""Read the drives, every 3 hours (R5, D-083).

    ./ops/drives.py                                  # a pass of every active drive (com.lms.drives)
    ./ops/drives.py --source storage:icloud-drive    # a pass of one drive, active or not
    ./ops/drives.py --source storage:icloud-drive --probe    # counts only; writes nothing
    ./ops/drives.py --source storage:icloud-drive --dry-run  # what would be filed; writes nothing

Lists every file in each drive and files the documents created or changed
since the drive's start date (the day of its first pass) once they have
been unchanged for 24 hours. Never writes, moves, renames or deletes
anything in a drive. Older documents: ops/backfill_drives.py. Setup:
ops/DRIVES_APPLY.md. Engine: core/pipeline/drives.py.

The probe and the dry run print counts only, never a file or folder name.
Halted (D-059), it reads nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _halt                                                # noqa: E402
from _env import archive_volume_problem, load_lms_env      # noqa: E402
from _reexec import ensure_venv                            # noqa: E402
from core.adapters.drive import DriveError                 # noqa: E402

ICLOUD = "storage:icloud-drive"
ARCHIVE = "storage:icloud-archive"
GDRIVE = "storage:gdrive:"
DROPBOX = "storage:dropbox:"


def keychain_json(service: str) -> dict | None:
    r = subprocess.run(["security", "find-generic-password", "-s", service, "-w"],
                       capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        return json.loads(r.stdout.rstrip("\n"))
    except ValueError:
        return None


def has_reader(source_id: str) -> bool:
    return (source_id in (ICLOUD, ARCHIVE) or source_id.startswith(GDRIVE)
            or source_id.startswith(DROPBOX))


def build_source(source_id: str):
    from core.adapters import dropbox, gdrive, icloud_drive
    from core.pipeline.drives import DriveSource

    if source_id == ICLOUD:
        d = icloud_drive.ICloudDrive(source_id, icloud_drive.icloud_roots())
        return DriveSource(source_id, "iCloud Drive", d, d)
    if source_id == ARCHIVE:
        d = icloud_drive.ICloudDrive(source_id, icloud_drive.archive_roots())
        return DriveSource(source_id, "iCloud Drive (saved copy)", d, d,
                           files_documents=False)
    if source_id.startswith(GDRIVE):
        address = source_id[len(GDRIVE):]
        g = keychain_json(gdrive.keychain_service(address))
        if not g:
            raise DriveError(f"no Drive grant for {address}; authorise it once: "
                             f"./ops/authorise_gdrive.py {address}")
        from google.oauth2.credentials import Credentials
        creds = Credentials(token=None, refresh_token=g["refresh_token"],
                            client_id=g["client_id"], client_secret=g["client_secret"],
                            token_uri="https://oauth2.googleapis.com/token",
                            scopes=list(gdrive.SCOPES))
        d = gdrive.GDrive(source_id, address, gdrive.GoogleDriveAPI(creds))
        return DriveSource(source_id, f"Google Drive {address}", d, d)
    if source_id.startswith(DROPBOX):
        address = source_id[len(DROPBOX):]
        g = keychain_json(dropbox.keychain_service(address))
        if not g:
            raise DriveError(f"no Dropbox grant for {address}; authorise it once: "
                             f"./ops/authorise_dropbox.py {address} --app-key KEY")
        d = dropbox.Dropbox(source_id, address,
                            dropbox.DropboxAPI(g["app_key"], g["refresh_token"]))
        return DriveSource(source_id, "Dropbox", d, d)
    raise DriveError(f"no drive reader for {source_id}")


def scheduled_sources(register) -> list[str]:
    """The drives the 3-hourly job reads: active, with a cadence."""
    return [s.id for s in register
            if s.id.startswith("storage:") and s.status == "active"
            and s.cadence_hours is not None]


def _kind(f) -> str:
    return "google file" if f.native else (Path(f.name).suffix.lower().lstrip(".") or "none")


def do_probe(src) -> int:
    from core.pipeline import drives
    from core.reports.storage import fmt_bytes
    try:
        files = src.lister.list(None).files
    except DriveError as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 1
    sized = [f.size for f in files if f.size is not None]
    docs = Counter(_kind(f) for f in files if f.is_document)
    newest = max((drives.when(f.modified) for f in files), default=None)
    print(f"{src.source_id}: {len(files)} file(s), {fmt_bytes(sum(sized))} "
          f"in the {len(sized)} with a known size")
    print("by area: " + (", ".join(f"{a} {n}" for a, n in
                                   sorted(Counter(f.area for f in files).items())) or "none"))
    print("documents: " + (", ".join(f"{k} {n}" for k, n in docs.most_common()) or "none"))
    print(f"other files (indexed, not filed): {sum(1 for f in files if not f.is_document)}")
    print(f"over 100 MB: {sum(1 for f in files if f.size and f.size > drives.MAX_BYTES)}")
    print(f"newest change: {newest:%Y-%m-%d %H:%M}" if newest else "newest change: none")
    return 0


def do_dry_run(src, conn, now: datetime) -> int:
    from core.pipeline import drives
    try:
        would, big, listed = drives.plan(src, conn, now)
    except DriveError as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 1
    kinds = Counter(_kind(f) for f in would)
    detail = f" ({', '.join(f'{k} {n}' for k, n in kinds.most_common())})" if would else ""
    print(f"{src.source_id}: {listed} file(s) listed; {len(would)} document(s) would be "
          f"filed now{detail}; {big} over 100 MB would not be.")
    print("Nothing was written.")
    return 0


def _halt_requested() -> bool:
    return _halt.read_flag() is not None


def run(conn, roots, registry, classifier, sids: list[str], now: datetime, *,
        build=build_source, halted=_halt_requested) -> int:
    """A pass of each drive in turn. One broken drive does not stop the rest."""
    from core.db import database as db
    from core.pipeline import coverage, drives
    bad = 0
    for sid in sids:
        if halted():
            print("HALTED: stopped between drives.")
            break
        try:
            src = build(sid)
        except (DriveError, ImportError, KeyError) as exc:
            started = db.now_iso()
            coverage.record_check(conn, sid, "FAILED", started_at=started, detail=str(exc))
            db.log_action(conn, drives.FAILED, detail=f"{sid}: {exc}"[:400])
            conn.commit()
            print(f"{sid}: FAIL  {exc}")
            bad += 1
            continue
        r = drives.run_pass(src, conn, roots, registry, classifier, now=now,
                            tmp_root=roots.archive.parent / "tmp" / "drives", halted=halted)
        print(f"{sid}: {r.summary()}")
        if r.error or r.failed:
            bad += 1
    return 1 if bad else 0


def main() -> int:
    ensure_venv("LMS_DRIVES_REEXEC", script=__file__)
    load_lms_env()
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", action="append", default=[],
                   help="a storage:* id from config/sources.yaml (repeatable)")
    p.add_argument("--probe", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    if (args.probe or args.dry_run) and not args.source:
        p.error("--probe and --dry-run need --source")

    if args.probe:
        rc = 0
        for sid in args.source:
            try:
                rc |= do_probe(build_source(sid))
            except DriveError as exc:
                print(f"FAIL  {exc}", file=sys.stderr)
                rc = 1
        return rc

    flag = _halt.read_flag()
    if flag is not None:
        print(f"HALTED since {flag.get('ts')} (by {flag.get('by')}): nothing read.")
        return 0
    problem = archive_volume_problem()
    if problem:
        print(f"archive unavailable: {problem}", file=sys.stderr)
        return 1

    from core.db import database as db
    from core.pipeline import coverage, filing
    from core.pipeline.classify import Classifier
    from core.pipeline.registry import load_registry

    roots = filing.StorageRoots.from_env()
    roots.ensure()
    conn = db.connect(os.environ.get("LMS_DB", str(roots.archive.parent / "lms.db")))
    now = datetime.now().astimezone()
    try:
        if args.dry_run:
            rc = 0
            for sid in args.source:
                try:
                    rc |= do_dry_run(build_source(sid), conn, now)
                except DriveError as exc:
                    print(f"FAIL  {exc}", file=sys.stderr)
                    rc = 1
            return rc
        sids = args.source or scheduled_sources(coverage.load_register())
        if not sids:
            print("No drive is switched on (status: active in config/sources.yaml).")
            return 0
        registry = load_registry()
        return run(conn, roots, registry, Classifier(registry), sids, now)
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Write `lms/ops/backfill_drives.py`**

```python
#!/usr/bin/env python3
"""File older drive documents in batches (R5, R28; D-083).

    ./ops/backfill_drives.py --source storage:icloud-drive --since 2025-01-01
    ./ops/backfill_drives.py --source storage:gdrive:matthew@mrecai.com \
        --since 2024-01-01 --until 2024-12-31 --folder "My Drive/Clients"

The 3-hourly pass's rules with an earlier start date: documents only,
unchanged for 24 hours, at most 100 MB, from the areas that are filed.
It works from the index the pass keeps, so the drive needs one pass first.
Oldest first, in batches of 20; a filed revision is never filed again, so an
interrupted run picks up where it stopped. Stops between batches on a halt.
Not a check of the drive: nothing goes to source_checks.

--folder matches the path as the drive reports it, starting with its root:
"iCloud Drive/...", "My Drive/...", a shared drive's name, "Dropbox/...".
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _halt                                                # noqa: E402
from _env import archive_volume_problem, load_lms_env      # noqa: E402
from _reexec import ensure_venv                            # noqa: E402
from core.adapters.drive import DriveError                 # noqa: E402


def _never() -> bool:
    return False


def index_problem(conn, source_id: str) -> str | None:
    from core.pipeline import drives
    if drives.start_date(conn, source_id) is None:
        return (f"{source_id} has no index yet. Run one pass first: "
                f"./ops/drives.py --source {source_id}")
    return None


def backfill(src, conn, roots, registry, classifier, *, now: datetime, tmp_root: Path,
             since: str, until: str | None = None, folder: str | None = None,
             batch: int = 20, halted=_never) -> int:
    from core.pipeline import drives
    Path(tmp_root).mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="backfill-", dir=tmp_root))
    total = 0
    try:
        while True:
            files, _big = drives.choose(src, conn, now, start=since, until=until,
                                        folder=folder, limit=batch)
            if not files:
                return total
            result = drives.PassResult(src.source_id)
            drives.file_rows(src, conn, roots, registry, classifier, files, tmp, result)
            total += result.filed + result.duplicates + result.review
            print(f"batch: {result.filed} filed, {result.duplicates} already in the "
                  f"archive, {result.review} to review, {result.failed} failed", flush=True)
            if halted():
                print("HALTED: stopped between batches.", flush=True)
                return total
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _day(value: str) -> str:
    return date.fromisoformat(value).isoformat()


def main() -> int:
    ensure_venv("LMS_BACKFILL_DRIVES_REEXEC", script=__file__)
    load_lms_env()
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", required=True)
    p.add_argument("--since", required=True, type=_day, help="YYYY-MM-DD")
    p.add_argument("--until", type=_day, help="YYYY-MM-DD, inclusive")
    p.add_argument("--folder")
    p.add_argument("--batch", type=int, default=20)
    args = p.parse_args()

    if _halt.read_flag() is not None:
        print("HALTED: nothing read.")
        return 0
    problem = archive_volume_problem()
    if problem:
        print(f"archive unavailable: {problem}", file=sys.stderr)
        return 1

    from core.db import database as db
    from core.pipeline import filing
    from core.pipeline.classify import Classifier
    from core.pipeline.registry import load_registry
    from drives import build_source

    roots = filing.StorageRoots.from_env()
    roots.ensure()
    conn = db.connect(os.environ.get("LMS_DB", str(roots.archive.parent / "lms.db")))
    try:
        problem = index_problem(conn, args.source)
        if problem:
            print(problem, file=sys.stderr)
            return 1
        try:
            src = build_source(args.source)
        except DriveError as exc:
            print(f"FAIL  {exc}", file=sys.stderr)
            return 1
        registry = load_registry()
        n = backfill(src, conn, roots, registry, Classifier(registry),
                     now=datetime.now().astimezone(),
                     tmp_root=roots.archive.parent / "tmp" / "drives-backfill",
                     since=args.since, until=args.until, folder=args.folder,
                     batch=args.batch, halted=lambda: _halt.read_flag() is not None)
    finally:
        conn.close()
    print(f"{n} document(s) filed or found already in the archive.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Write `lms/ops/com.lms.drives.plist`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!--
  The drive readers, every 3 hours. R5, D-083. ops/drives.py says what one pass does.

  Install (ops/DRIVES_APPLY.md):
    cp ops/com.lms.drives.plist ~/Library/LaunchAgents/
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.lms.drives.plist

  Each pass reads, in turn, every drive whose status is active in
  config/sources.yaml. StartInterval: a pass missed while the Mac slept is
  made up by the next one. A LaunchAgent, because iCloud Drive is in
  Matthew's user Library; Full Disk Access is the grant the other readers
  already use. A halt (D-059) disables this job with the rest.
-->
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.lms.drives</string>

    <key>ProgramArguments</key>
    <array>
        <string>/Users/mleca/lms-repo/lms/.venv/bin/python</string>
        <string>/Users/mleca/lms-repo/lms/ops/drives.py</string>
    </array>

    <key>WorkingDirectory</key>
    <string>/Users/mleca/lms-repo/lms</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>TZ</key>
        <string>America/New_York</string>
        <key>PYTHONUNBUFFERED</key>
        <string>1</string>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>

    <key>StartInterval</key>
    <integer>10800</integer>

    <key>RunAtLoad</key>
    <true/>

    <key>StandardOutPath</key>
    <string>/Users/mleca/LMS/logs/drives.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/mleca/LMS/logs/drives.err</string>

    <key>ProcessType</key>
    <string>Background</string>
    <key>Nice</key>
    <integer>10</integer>
</dict>
</plist>
```

- [ ] **Step 6: Replace the Storage block of `lms/config/sources.yaml`**

Replace the three entries `storage:icloud-drive`, `storage:google-drive` and `storage:dropbox` (everything between the `# Storage — read-only sources (R5, R8)` header's closing rule line and the `# Capture` header's opening rule line) with:

```yaml
  - id: storage:icloud-drive
    label: iCloud Drive
    kind: storage
    account: the Mac's iCloud, mattyeps@gmail.com (D-082)
    method: >-
      the iCloud Drive folder on the Mac and the Documents folders of Pages,
      Numbers, Keynote, Preview and TextEdit, listed without opening files
      (ops/drives.py, D-083); the LMS folder is skipped. Every file indexed;
      documents created or changed from the start date filed once unchanged
      for 24 hours
    permissions: Full Disk Access for the venv's Python (already granted); read only
    cadence_hours: 3
    status: pending
    requirement: R5
    blocked_on: Built, not switched on (ops/DRIVES_APPLY.md step 2).
    gaps: >-
      Documents older than the start date are filed only by a backfill
      (ops/backfill_drives.py). Files over 100 MB, videos, audio and other
      non-documents are indexed, not filed.

  - id: storage:icloud-archive
    label: iCloud Drive, saved copies (Jul 30 and Oct 5)
    kind: storage
    account: the two folders macOS kept in ~/Library/CloudStorage when iCloud was signed out
    method: listed by ops/drives.py --source storage:icloud-archive, index only (D-083)
    permissions: Full Disk Access (already granted); read only
    status: pending
    requirement: R5
    blocked_on: Built, not run yet (ops/DRIVES_APPLY.md step 3).
    gaps: Indexed only; nothing in them is filed.

  - id: storage:gdrive:matthew@mrecai.com
    label: Google Drive (MRECAI)
    kind: storage
    account: matthew@mrecai.com (D-082)
    method: >-
      Drive API, read-only (ops/drives.py, D-083): every file in My Drive,
      shared drives and Shared with me indexed; documents from My Drive and
      shared drives filed once unchanged for 24 hours; Docs, Sheets and
      Slides filed as PDF
    permissions: drive.readonly via the MRECAI Workspace internal app, not yet granted
    cadence_hours: 3
    status: pending
    requirement: R5
    blocked_on: >-
      The Drive API and the drive.readonly scope on the MRECAI app, then one
      Allow by Matthew (ops/DRIVES_APPLY.md step 4).
    gaps: >-
      Shared with me is indexed, not filed. Documents older than the start
      date are filed only by a backfill. Exports over Google's 10 MB limit
      and files over 100 MB are not filed.

  - id: storage:gdrive:matthew@mleca.com
    label: Google Drive (MLECA)
    kind: storage
    account: matthew@mleca.com (D-082)
    method: >-
      Drive API, read-only (ops/drives.py, D-083): every file in My Drive,
      shared drives and Shared with me indexed; documents from My Drive and
      shared drives filed once unchanged for 24 hours; Docs, Sheets and
      Slides filed as PDF
    permissions: drive.readonly via the MLECA Workspace internal app, not yet granted
    cadence_hours: 3
    status: pending
    requirement: R5
    blocked_on: >-
      The Drive API and the drive.readonly scope on the MLECA app, then one
      Allow by Matthew (ops/DRIVES_APPLY.md step 4).
    gaps: >-
      Shared with me is indexed, not filed. Documents older than the start
      date are filed only by a backfill. Exports over Google's 10 MB limit
      and files over 100 MB are not filed.

  - id: storage:dropbox:matthew@mrecai.com
    label: Dropbox
    kind: storage
    account: matthew@mrecai.com (D-082)
    method: >-
      Dropbox API, read-only (ops/drives.py, D-083): every file indexed;
      documents filed once unchanged for 24 hours
    permissions: files.metadata.read, files.content.read, account_info.read; not yet granted
    cadence_hours: 3
    status: pending
    requirement: R5
    blocked_on: >-
      A Dropbox app, then one Allow by Matthew (ops/DRIVES_APPLY.md step 5).
    gaps: >-
      Documents older than the start date are filed only by a backfill.
      Files over 100 MB are not filed.
```

- [ ] **Step 7: Watch the drives in the brief**

In `lms/core/reports/brief.py`, add to `WATCHED_JOBS`, after the `"IMESSAGE_CHECKED"` line:

```python
    # The drive readers (R5, D-083): every 3 hours; every pass writes a row.
    "DRIVES_CHECKED":   ("drives", 7),
```

and to `JOB_FAILURES`, after `"IMESSAGE_FAILED"`:

```python
    "DRIVES_FAILED":       "DRIVES_CHECKED",
```

- [ ] **Step 8: Measure drives in `lms/core/reports/storage.py`**

1. Directly after the `StorageReport` dataclass (the last class in the `# Shapes` section, so `Tree` and `Reclaim` exist), add:

```python
# Drives are read in place by read-only readers (D-083); what they hold is
# measured from the index the readers keep, not from the archive.
DRIVE_WHERE = "a drive, read in place by a read-only reader (D-083); originals stay there"


def drive_tree(conn, source_id: str) -> Tree:
    """What a drive holds, from its index."""
    r = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(size), 0) AS b, "
        "COALESCE(SUM(size IS NULL), 0) AS u FROM drive_files "
        "WHERE source_id = ? AND deleted_at IS NULL", (source_id,)).fetchone()
    note = "from the drive index"
    if r["u"]:
        note += (f"; {r['u']} file(s) have no size (Google Docs, iCloud placeholders) "
                 "and are not in the total")
    return Tree(f"{source_id} (indexed)", None, files=int(r["n"]), bytes=int(r["b"]),
                note=note)


def drive_reclaim(conn, source_id: str, filed_shas: set[str]) -> Reclaim:
    n = size = 0
    for r in conn.execute(
            "SELECT sha256, size FROM drive_files WHERE source_id = ? AND deleted_at IS NULL "
            "AND sha256 IS NOT NULL AND filed_revision = revision", (source_id,)):
        if r["sha256"] in filed_shas:
            n += 1
            size += int(r["size"] or 0)
    return Reclaim(f"{source_id} — drive files whose current bytes are filed", n, size,
                   "sha256 recorded when the reader fetched them; files never fetched "
                   f"are {NOT_MEASURED}. Not removable from here: the drive readers "
                   "are read-only")
```

2. In `source_lines`, before `for g in dedup.groups:`, add:

```python
    for r in conn.execute("SELECT DISTINCT source_id FROM drive_files ORDER BY source_id"):
        lines.setdefault(r["source_id"], SourceLine(source=r["source_id"], where=DRIVE_WHERE))
```

and make the existing artifacts loop use `DRIVE_WHERE` for a drive source: change `where=WHERE.get(r["source"], "not described")` there to `where=WHERE.get(r["source"], DRIVE_WHERE if r["source"].startswith("storage:") else "not described")`.

3. In `build_report`'s "what is still at each source" loop, add a branch before the final `else:`:

```python
        elif s.source.startswith("storage:"):
            s.at_source = drive_tree(conn, s.source)
```

4. Replace

```python
    not_measured.append(
        "sources not connected yet (Dropbox, the wider iCloud Drive, the managed "
        "OpenClaw root — R24): " + NOT_MEASURED)
```

with

```python
    not_measured.append("the managed OpenClaw root (R24), not built: " + NOT_MEASURED)
```

5. After the `reclaim.append(Reclaim("Gmail originals ...` line and its `not_measured.append(...)`, add:

```python
    for s in sources:
        if s.source.startswith("storage:"):
            reclaim.append(drive_reclaim(conn, s.source, filed_shas))
```

- [ ] **Step 9: Mark the new scripts executable and run the tests**

```bash
git add --chmod=+x lms/ops/drives.py lms/ops/backfill_drives.py
```

Run: `<test command> tests/test_drives_job.py tests/test_storage_drives.py tests/test_drive_readonly.py` → all pass. Then the full test command → all pass (including `tests/test_stalled_jobs.py`, `tests/test_plists.py`, `tests/test_ops_env.py`, `tests/test_ops_scripts.py`, `tests/test_coverage.py`, `tests/test_storage_report.py`).

- [ ] **Step 10: Commit**

```bash
git add lms/ops/drives.py lms/ops/backfill_drives.py lms/ops/com.lms.drives.plist \
        lms/config/sources.yaml lms/core/reports/brief.py lms/core/reports/storage.py \
        lms/tests/test_drives_job.py lms/tests/test_storage_drives.py lms/tests/test_drive_readonly.py
git commit -m "D-083: drive job every 3 hours, backfill, register lines, brief watch, storage from the index

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: The authorise scripts

**Files:**
- Create: `lms/ops/authorise_gdrive.py`, `lms/ops/authorise_dropbox.py`
- Test: `lms/tests/test_authorise_drives.py`

**Interfaces:**
- Consumes: `ops/authorise_gmail.py`'s `keychain_read(service) -> dict | None`, `keychain_write(service, account, payload)`, `load_client(path_glob) -> (client_id, client_secret, path)`; `gdrive.SCOPES`, `gdrive.keychain_service`, `gdrive.GoogleDriveAPI`; `gmail.keychain_service`; `dropbox.SCOPES`, `dropbox.TOKEN_URL`, `dropbox.urllib_post`, `dropbox.DropboxAPI`, `dropbox.keychain_service`; `drive.wrong_account`.
- Produces: `authorise_gdrive.scope_problem(granted) -> str | None`, `authorise_gdrive.main()`; `authorise_dropbox.pkce_pair() -> (verifier, challenge)`, `authorise_dropbox.authorize_url(app_key, challenge) -> str`, `authorise_dropbox.scope_problem(granted: str | None) -> str | None`, `authorise_dropbox.exchange(app_key, code, verifier, post=...) -> dict`, `authorise_dropbox.main()`.

- [ ] **Step 1: Write the failing test**

`lms/tests/test_authorise_drives.py`:

```python
"""ops/authorise_gdrive.py and ops/authorise_dropbox.py (R5, D-083)."""

import base64
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))

import authorise_dropbox as adb     # noqa: E402
import authorise_gdrive as agd      # noqa: E402

READONLY = "https://www.googleapis.com/auth/drive.readonly"


def test_gdrive_accepts_exactly_drive_readonly():
    assert agd.scope_problem([READONLY]) is None
    assert agd.scope_problem([]) is None, "Google did not say: the token itself is checked"
    wider = agd.scope_problem([READONLY, "https://www.googleapis.com/auth/drive"])
    assert "more than asked" in wider and "Nothing was stored" in wider
    assert "did not grant" in agd.scope_problem(["https://www.googleapis.com/auth/gmail.readonly"])


def test_the_pkce_challenge_is_the_s256_of_the_verifier():
    verifier, challenge = adb.pkce_pair()
    assert 43 <= len(verifier) <= 128
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    assert challenge == expected.decode().rstrip("=")
    assert adb.pkce_pair()[0] != verifier


def test_the_dropbox_url_asks_for_an_offline_read_only_grant():
    url = adb.authorize_url("APPKEY", "CHALLENGE")
    q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    assert urlparse(url).netloc == "www.dropbox.com"
    assert q == {"client_id": "APPKEY", "response_type": "code", "code_challenge": "CHALLENGE",
                 "code_challenge_method": "S256", "token_access_type": "offline",
                 "scope": "files.metadata.read files.content.read account_info.read"}


def test_dropbox_accepts_exactly_the_three_scopes():
    assert adb.scope_problem("account_info.read files.content.read files.metadata.read") is None
    assert adb.scope_problem(None) is None
    assert "more than asked" in adb.scope_problem(
        "files.metadata.read files.content.read account_info.read files.content.write")
    assert "did not grant files.content.read" in adb.scope_problem(
        "files.metadata.read account_info.read")


def test_the_code_is_exchanged_with_the_verifier_and_no_secret():
    sent = []

    def post(url, data, headers):
        sent.append((url, parse_qs(data.decode())))
        return json.dumps({"refresh_token": "RT", "access_token": "AT"}).encode()

    assert adb.exchange("APPKEY", "CODE", "VERIFIER", post=post)["refresh_token"] == "RT"
    url, form = sent[0]
    assert url == "https://api.dropboxapi.com/oauth2/token"
    assert form == {"code": ["CODE"], "grant_type": ["authorization_code"],
                    "code_verifier": ["VERIFIER"], "client_id": ["APPKEY"]}
```

- [ ] **Step 2: Run it to see it fail**

Run: `<test command> tests/test_authorise_drives.py`
Expected: FAIL, `ModuleNotFoundError: No module named 'authorise_dropbox'`.

- [ ] **Step 3: Write `lms/ops/authorise_gdrive.py`**

```python
#!/usr/bin/env python3
"""Authorise Google Drive for one account, once, read-only (R5, D-083).

Matthew signs in and clicks Allow. The refresh token goes in the Keychain
under lms/gdrive-oauth/<address>, apart from the account's Gmail grant, so
each grant stays as narrow as its job.

WHAT IS ASKED FOR: drive.readonly and nothing else. The token can list and
download files. It cannot create, change, move, share or delete one: Google
refuses.

WHICH APP: the same internal Workspace app as the account's Gmail grant
(D-061), once the Drive API is enabled in its Cloud project and
drive.readonly is on its consent screen (ops/DRIVES_APPLY.md step 4). By
default the client id and secret come from the Gmail grant stored for the
same address.

    ./ops/authorise_gdrive.py matthew@mrecai.com
    ./ops/authorise_gdrive.py matthew@mrecai.com --check
    ./ops/authorise_gdrive.py matthew@mleca.com --client-json ~/Downloads/client_secret_*.json

The browser may already be signed in to another account: sign out of it, or
copy the address the script prints into a private window. Before saving,
the script asks Drive whose files the token reads, and refuses if that is
not the address given.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _reexec import ensure_venv                                         # noqa: E402
from authorise_gmail import keychain_read, keychain_write, load_client  # noqa: E402
from core.adapters import gdrive, gmail                                 # noqa: E402
from core.adapters.drive import wrong_account                           # noqa: E402

TOKEN_URI = "https://oauth2.googleapis.com/token"


def scope_problem(granted, wanted=gdrive.SCOPES) -> str | None:
    """Why the granted scopes are not exactly the one asked for, or None.
    An empty answer is not a problem: the token is checked by using it."""
    got = set(granted or ())
    if not got or got == set(wanted):
        return None
    missing = sorted(set(wanted) - got)
    if missing:
        return (f"Google did not grant {', '.join(missing)}. Is drive.readonly on the "
                "app's consent screen? Nothing was stored.")
    extra = sorted(got - set(wanted))
    return (f"Google granted more than asked ({', '.join(extra)}). Nothing was "
            "stored: the read-only guarantee is the point of this.")


def credentials(stored: dict):
    from google.oauth2.credentials import Credentials
    return Credentials(token=None, refresh_token=stored["refresh_token"],
                       client_id=stored["client_id"], client_secret=stored["client_secret"],
                       token_uri=TOKEN_URI, scopes=list(gdrive.SCOPES))


def main() -> int:
    ensure_venv("LMS_AUTHGDRIVE_REEXEC", script=__file__)
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("address")
    p.add_argument("--client-json", help="a downloaded Desktop-app client JSON")
    p.add_argument("--client-from", metavar="ADDRESS",
                   help="take the app from this address's Gmail grant (default: the same address)")
    p.add_argument("--check", action="store_true",
                   help="report what is stored, and prove it still works")
    args = p.parse_args()
    service = gdrive.keychain_service(args.address)

    if args.check:
        stored = keychain_read(service)
        if not stored:
            print(f"{service}: nothing stored")
            return 1
        try:
            email = gdrive.GoogleDriveAPI(credentials(stored)).user_email()
        except Exception as exc:                              # noqa: BLE001
            print(f"{service}: stored, but it does not work: {exc}")
            return 1
        problem = wrong_account(args.address, email, "Google Drive")
        if problem:
            print(f"{service}: {problem}")
            return 1
        print(f"{service}: works, reads {email}'s Drive.")
        return 0

    if args.client_json:
        client_id, client_secret, json_path = load_client(args.client_json)
    else:
        donor_address = args.client_from or args.address
        donor = keychain_read(gmail.keychain_service(donor_address))
        if not donor or not donor.get("client_id") or not donor.get("client_secret"):
            raise SystemExit(f"no Gmail grant stored for {donor_address} to take the app "
                             "from; use --client-json instead")
        client_id, client_secret, json_path = donor["client_id"], donor["client_secret"], None

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("Missing libraries: .venv/bin/pip install google-api-python-client "
              "google-auth-oauthlib", file=sys.stderr)
        return 2

    print(f"Authorising Google Drive for {args.address}")
    print(f"  scope: {gdrive.SCOPES[0]}")
    print("  a browser will open: sign in as this account and click Allow\n")
    flow = InstalledAppFlow.from_client_config(
        {"installed": {"client_id": client_id, "client_secret": client_secret,
                       "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                       "token_uri": TOKEN_URI, "redirect_uris": ["http://localhost"]}},
        scopes=list(gdrive.SCOPES))
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline",
                                  login_hint=args.address)
    if not creds.refresh_token:
        print("\nGoogle returned no refresh token. Revoke the old grant at "
              "myaccount.google.com/permissions and run this again.", file=sys.stderr)
        return 1
    problem = scope_problem(getattr(creds, "scopes", None))
    if problem:
        print(f"\n{problem}", file=sys.stderr)
        return 1
    email = gdrive.GoogleDriveAPI(creds).user_email()
    problem = wrong_account(args.address, email, "Google Drive")
    if problem:
        print(f"\n{problem} Nothing was stored. Sign out of that account in the "
              "browser (or use a private window) and run this again.", file=sys.stderr)
        return 1

    payload = {"refresh_token": creds.refresh_token, "client_id": client_id,
               "client_secret": client_secret, "address": args.address,
               "scopes": list(gdrive.SCOPES)}
    keychain_write(service, args.address, payload)
    back = keychain_read(service)
    if not back or back.get("refresh_token") != creds.refresh_token:
        print("\nVERIFICATION FAILED: the Keychain does not hold what was just granted.",
              file=sys.stderr)
        return 1
    print(f"\nStored {service} and read it back unchanged. It reads {email}'s Drive.")
    if json_path:
        print(f"\nYou can delete {json_path} now: the client id and secret are in the Keychain.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Write `lms/ops/authorise_dropbox.py`**

```python
#!/usr/bin/env python3
"""Authorise Dropbox for one account, once, read-only (R5, D-083).

The Dropbox app is created once in the Dropbox App Console (ops/DRIVES_APPLY.md
step 5): scoped access, full Dropbox, permissions files.metadata.read,
files.content.read and account_info.read and nothing else. Development
status is enough. Only its app key is needed: the grant uses PKCE, so there
is no app secret to keep.

Matthew opens the address this prints in a private browser window, signs in,
clicks Allow and pastes the code Dropbox shows. The offline refresh token goes
in the Keychain under lms/dropbox-oauth/<address>.

    ./ops/authorise_dropbox.py matthew@mrecai.com --app-key KEY
    ./ops/authorise_dropbox.py matthew@mrecai.com --check

Before saving, it asks Dropbox whose account the token reads, and refuses if
that is not the address given.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import secrets
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _reexec import ensure_venv                             # noqa: E402
from authorise_gmail import keychain_read, keychain_write   # noqa: E402
from core.adapters import dropbox                           # noqa: E402
from core.adapters.drive import wrong_account               # noqa: E402

AUTHORIZE = "https://www.dropbox.com/oauth2/authorize"


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode().rstrip("=")


def authorize_url(app_key: str, challenge: str) -> str:
    return AUTHORIZE + "?" + urllib.parse.urlencode({
        "client_id": app_key, "response_type": "code", "code_challenge": challenge,
        "code_challenge_method": "S256", "token_access_type": "offline",
        "scope": " ".join(dropbox.SCOPES)})


def scope_problem(granted: str | None) -> str | None:
    got = set((granted or "").split())
    if not got:
        return None
    extra = sorted(got - set(dropbox.SCOPES))
    if extra:
        return (f"Dropbox granted more than asked ({', '.join(extra)}). Nothing was "
                "stored: the read-only guarantee is the point of this.")
    missing = sorted(set(dropbox.SCOPES) - got)
    if missing:
        return (f"Dropbox did not grant {', '.join(missing)}. Check the app's "
                "Permissions tab, then run this again. Nothing was stored.")
    return None


def exchange(app_key: str, code: str, verifier: str, post=dropbox.urllib_post) -> dict:
    body = urllib.parse.urlencode({"code": code, "grant_type": "authorization_code",
                                   "code_verifier": verifier, "client_id": app_key}).encode()
    return json.loads(post(dropbox.TOKEN_URL, body,
                           {"Content-Type": "application/x-www-form-urlencoded"}))


def main() -> int:
    ensure_venv("LMS_AUTHDROPBOX_REEXEC", script=__file__)
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("address")
    p.add_argument("--app-key", help="the Dropbox app's key (not its secret)")
    p.add_argument("--check", action="store_true",
                   help="report what is stored, and prove it still works")
    args = p.parse_args()
    service = dropbox.keychain_service(args.address)

    if args.check:
        stored = keychain_read(service)
        if not stored:
            print(f"{service}: nothing stored")
            return 1
        try:
            api = dropbox.DropboxAPI(stored["app_key"], stored["refresh_token"])
            email = api.rpc("users/get_current_account", None)["email"]
        except Exception as exc:                              # noqa: BLE001
            print(f"{service}: stored, but it does not work: {exc}")
            return 1
        problem = wrong_account(args.address, email, "Dropbox")
        if problem:
            print(f"{service}: {problem}")
            return 1
        print(f"{service}: works, reads {email}'s Dropbox.")
        return 0

    if not args.app_key:
        p.error("--app-key is needed to authorise")
    verifier, challenge = pkce_pair()
    print(f"Authorising Dropbox for {args.address}")
    print(f"  scopes: {' '.join(dropbox.SCOPES)}\n")
    print("Open this in a private browser window, sign in as that account, click "
          "Allow, and copy the code Dropbox shows:\n")
    print(authorize_url(args.app_key, challenge) + "\n")
    code = input("Code: ").strip()
    if not code:
        print("No code given. Nothing was stored.", file=sys.stderr)
        return 1
    try:
        token = exchange(args.app_key, code, verifier)
    except dropbox.HTTPError as exc:
        print(f"Dropbox refused the code: {exc}. Nothing was stored.", file=sys.stderr)
        return 1
    if not token.get("refresh_token"):
        print("Dropbox returned no refresh token. Nothing was stored.", file=sys.stderr)
        return 1
    problem = scope_problem(token.get("scope"))
    if problem:
        print(problem, file=sys.stderr)
        return 1
    api = dropbox.DropboxAPI(args.app_key, token["refresh_token"])
    email = api.rpc("users/get_current_account", None)["email"]
    problem = wrong_account(args.address, email, "Dropbox")
    if problem:
        print(f"{problem} Nothing was stored.", file=sys.stderr)
        return 1

    payload = {"refresh_token": token["refresh_token"], "app_key": args.app_key,
               "address": args.address, "scopes": list(dropbox.SCOPES)}
    keychain_write(service, args.address, payload)
    back = keychain_read(service)
    if not back or back.get("refresh_token") != token["refresh_token"]:
        print("VERIFICATION FAILED: the Keychain does not hold what was just granted.",
              file=sys.stderr)
        return 1
    api.rpc("files/list_folder", {"path": "", "limit": 1})
    print(f"Stored {service} and read it back unchanged. It reads {email}'s Dropbox.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Mark them executable and run the tests**

```bash
git add --chmod=+x lms/ops/authorise_gdrive.py lms/ops/authorise_dropbox.py
```

Run: `<test command> tests/test_authorise_drives.py` → 5 passed. Then the full test command → all pass.

- [ ] **Step 6: Commit**

```bash
git add lms/ops/authorise_gdrive.py lms/ops/authorise_dropbox.py lms/tests/test_authorise_drives.py
git commit -m "D-083: one read-only grant per drive account, account checked before it is stored

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Setup guide and records

**Files:**
- Create: `lms/ops/DRIVES_APPLY.md`
- Modify: `DECISIONS.md` (new entry above `### D-082`), `REQUIREMENTS_REMAINING.md` (the R5 row)

**Interfaces:**
- Consumes: the commands, ids and Keychain names from Tasks 1–7. No code.

- [ ] **Step 1: Write `lms/ops/DRIVES_APPLY.md`**

````markdown
# Drive readers: setup on the Mac

R5, D-083. Reads iCloud Drive, Google Drive (`matthew@mrecai.com`, `matthew@mleca.com`) and Dropbox (`matthew@mrecai.com`), read-only. Every file is indexed; documents created or changed from each drive's start date are filed once unchanged for 24 hours. Nothing in a drive is ever written, moved, renamed or deleted.

Paste only the last lines of any output here: the probe and the dry run print counts, never file names, but logs of filing can name documents.

## 1. Pull

```bash
cd ~/lms-repo && git pull && cd lms
```

## 2. iCloud Drive

Needs nothing from Matthew: Python already has Full Disk Access.

```bash
./ops/drives.py --source storage:icloud-drive --probe
```

Counts by area and type, total size, newest change. A first listing of a large iCloud Drive can take a few minutes.

```bash
./ops/drives.py --source storage:icloud-drive --dry-run
```

On a drive with no start date yet, "would be filed now" is 0: the start date is the day of the first pass.

```bash
./ops/drives.py --source storage:icloud-drive
```

The first pass: it indexes everything and sets today as the start date. Then tell the developer: they set `storage:icloud-drive` to `status: active` in `config/sources.yaml`. After the next `git pull`, install the job:

```bash
cp ops/com.lms.drives.plist ~/Library/LaunchAgents/
```

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.lms.drives.plist
```

```bash
tail -3 ~/LMS/logs/drives.log
```

## 3. The saved iCloud copies (Jul 30, Oct 5)

Indexed once; nothing is filed.

```bash
./ops/drives.py --source storage:icloud-archive
```

## 4. Google Drive

**Developer, once per company** (MRECAI and MLECA are separate Workspaces, D-061):

1. In the Google Cloud console, open the project of that Workspace's internal app (the one Gmail uses). **APIs & Services → Library → Google Drive API → Enable.**
2. **Google Auth Platform → Data access → Add or remove scopes**: add `.../auth/drive.readonly`. Save. Leave the Gmail scope as it is.

**Matthew, once per account**, in Terminal on the Mac:

```bash
./ops/authorise_gdrive.py matthew@mrecai.com
```

A browser opens: sign in as that account (sign out of any other first, or copy the printed address into a private window) and click Allow. It takes the app from the account's Gmail grant. Then the same for `matthew@mleca.com`.

```bash
./ops/authorise_gdrive.py matthew@mrecai.com --check
```

Then, per account, as for iCloud: `--probe`, `--dry-run`, a first pass with `--source storage:gdrive:<address>`, and the developer sets that line `active`.

## 5. Dropbox

**Developer, once:** at dropbox.com/developers, **Create app** → **Scoped access** → **Full Dropbox** → a name such as "MRE LMS reader". In **Permissions**, tick only `files.metadata.read`, `files.content.read` and `account_info.read`, and **Submit**. Development status is enough. Note the **App key**; the secret is not used.

**Matthew, once:**

```bash
./ops/authorise_dropbox.py matthew@mrecai.com --app-key APPKEY
```

Open the printed address in a private window, sign in, Allow, paste the code. Then `--check`, `--probe`, `--dry-run`, a first pass with `--source storage:dropbox:matthew@mrecai.com`, and the developer sets the line `active`.

## 6. Older documents (backfill)

Only after a drive's first pass. Oldest first, 20 at a time; safe to stop and run again.

```bash
nohup ./ops/backfill_drives.py --source storage:icloud-drive --since 2025-01-01 > ~/LMS/logs/drives-backfill.log 2>&1 &
```

Narrow it with `--until YYYY-MM-DD` and `--folder "iCloud Drive/Clients"` (paths start with the drive's root: `iCloud Drive/`, `My Drive/`, a shared drive's name, `Dropbox/`). Check it with `tail -2 ~/LMS/logs/drives-backfill.log`. Do not start a second backfill while one runs.

## Troubleshooting

- `none of ... is on this Mac (is iCloud Drive on?)`: iCloud Drive is off or signed out in System Settings.
- `the Drive grant for X no longer works ... re-authorise`: run `./ops/authorise_gdrive.py X` again.
- `the Dropbox grant ... no longer works`: run `./ops/authorise_dropbox.py matthew@mrecai.com --app-key APPKEY` again.
- A pass is PARTIAL: some files could not be fetched (an iCloud download that timed out, an export over Google's 10 MB). Each is tried on the next 3 passes; `DRIVE_FILE_FAILED` in `actions_log` says why.

## Removing it

```bash
launchctl bootout gui/$(id -u)/com.lms.drives
```

```bash
rm ~/Library/LaunchAgents/com.lms.drives.plist
```

Revoke the grants at myaccount.google.com/permissions and dropbox.com/account/connected_apps. Filed documents stay filed.
````

- [ ] **Step 2: Add D-083 to `DECISIONS.md`**, directly above `### D-082`:

```markdown
### D-083 — Drives are read in place: every file indexed, new documents filed, nothing in a drive ever changed
**Status:** Decided Nov 15 2026 · designed and approved with the developer · R5, R7, R28, addendum §2.3 · spec [docs/superpowers/specs/2026-11-15-drive-readers-design.md](docs/superpowers/specs/2026-11-15-drive-readers-design.md) · plan [docs/superpowers/plans/2026-11-15-drive-readers.md](docs/superpowers/plans/2026-11-15-drive-readers.md)

- **Index everything, file new.** Every file in iCloud Drive, Google Drive (`matthew@mrecai.com`, `matthew@mleca.com`) and Dropbox (`matthew@mrecai.com`) is listed into `drive_files`. Documents (PDF, images, Office, text, email files, Google Docs, Sheets and Slides as PDF) created or changed from the drive's start date are filed once unchanged for 24 hours, at most 100 MB. Older ones are filed by `ops/backfill_drives.py`. Rejected: filing everything (1.6 TB in iCloud Drive alone: weeks of model time and a second copy) and index-only (drive documents would never reach tasks).
- **Read-only by construction.** Google: `drive.readonly`. Dropbox: `files.metadata.read`, `files.content.read`, `account_info.read`. iCloud: listed with stat, copied out with `copyfile`. A test fails if a reader calls anything that could change a drive. This is the read-only source mode the addendum (§2.3) requires.
- **APIs, not sync apps,** for Google and Dropbox: neither desktop app is on the Mac, a sync app holds read-write access, and the API lists without downloading.
- **One engine, three adapters** (`core/pipeline/drives.py`), as the chat-day engine serves WhatsApp and iMessage.
- **Indexed only:** Google's "Shared with me" (others own and edit those files) and the two iCloud Drive copies macOS kept on Jul 30 and Oct 5.
- **The start date is the day of a drive's first pass** (`drive_cursors.started_on`); "switched on" is `status: active` in the register. Job `com.lms.drives` every 3 hours; `DRIVES_CHECKED` watched by the brief (7 hours).
- **Each grant stands alone:** `lms/gdrive-oauth/<address>` apart from the Gmail grant; `lms/dropbox-oauth/<address>` holds an offline PKCE refresh token and the app key, no secret. Each authorise script refuses a token for the wrong account.
- **Network:** the readers need `www.googleapis.com`, `oauth2.googleapis.com`, `api.dropboxapi.com` and `content.dropboxapi.com`, for the outbound allowlist when Phase 8 builds it.
- **Setup** waits on the developer (Drive API and scope on the two Workspace apps; a Dropbox app) and one Allow per account by Matthew ([lms/ops/DRIVES_APPLY.md](lms/ops/DRIVES_APPLY.md)). iCloud Drive needs neither.
```

- [ ] **Step 3: Update the R5 row in `REQUIREMENTS_REMAINING.md`**

Replace the text of the R5 row's last cell with:

```markdown
Scope = everything (D-065), accounts named Nov 15 (D-082). **Built Nov 15 (D-083)**, not switched on: every file indexed, new documents filed, read-only. iCloud Drive needs nothing from Matthew; Google Drive needs the Drive API and scope on the two Workspace apps, then one Allow per account; Dropbox needs a Dropbox app, then one Allow ([lms/ops/DRIVES_APPLY.md](lms/ops/DRIVES_APPLY.md)) |
```

- [ ] **Step 4: Run the full suite**

Run: the full test command → all pass.

- [ ] **Step 5: Commit**

```bash
git add lms/ops/DRIVES_APPLY.md DECISIONS.md REQUIREMENTS_REMAINING.md
git commit -m "D-083: drive readers setup guide, decision and R5

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
