# Drive readers — design

**Date:** Nov 15 2026 · **Requirement:** R5 (Google Drive, Dropbox, whole iCloud Drive, read-only; scope everything, D-065), R7 (coverage register), R28 (storage report), addendum §2.3 (read-only source mode) · **Decision to record at build:** D-083 · **Approved by:** the developer, Nov 15 2026 · **Builds on:** the chat-day engine (D-081), the mail backfill (D-074), the Gmail grants (D-039, D-061)

## Goal

Every file in Matthew's drives is known to the LMS, and every new document in them is filed to the right business, without the LMS ever changing a drive. The drives are iCloud Drive (`mattyeps@gmail.com`, signed in on the Mac since Nov 15, D-082), Google Drive `matthew@mrecai.com` and `matthew@mleca.com`, and Dropbox `matthew@mrecai.com` (Matthew, Nov 15, D-082).

## Decisions taken while designing

| Question | Answer |
|---|---|
| What "read everything" means | **Index everything, file new.** Every file is catalogued (path, size, dates, revision; sha256 once fetched). Documents created or changed from each source's start date are filed. Older documents are filed only by a backfill started per folder or date range. Filing everything was rejected: iCloud Drive alone is 1.6 TB, weeks of OCR and model time and a second copy of terabytes. Index-only was rejected: drive documents would never reach tasks or the brief. |
| How to reach Google Drive and Dropbox | **Their read-only APIs.** Neither desktop app is installed on the Mac (checked Nov 15). A sync app would hold read-write access and download terabytes or leave placeholders; the API grant cannot write and lists without downloading. |
| How to reach iCloud Drive | Its folder on the Mac, `~/Library/Mobile Documents`, walked without opening files. |
| The two saved iCloud copies | `~/Library/CloudStorage/iCloudDrive-iCloudDrive (7-30-26 5:55 PM)` and `(10-5-26 11:47 AM)`, left by macOS when iCloud was signed out. **Indexed once, nothing filed** (developer, Nov 15). A backfill can file from them later. |
| Google Drive areas | **File from My Drive and shared drives.** "Shared with me" is indexed only: other people own and edit those files, so every save of theirs would be filed again. |
| Shape of the code | **One engine, three thin adapters** (approach A), as the chat-day engine serves WhatsApp and iMessage. Three separate readers (B) would write every rule three times. `rclone` (C) would add a program to pin and keep its tokens in its own config file instead of the Keychain (D-016, D-039). |
| Changed documents | Filed **once settled**: unchanged for 24 hours. Each settled revision is a new artifact; the near-duplicate report (D-074) marks them related versions. |
| Google Docs, Sheets, Slides | Exported as PDF when filed. |

## Components

### 1. Shared types — `core/adapters/drive.py` (new)

- `DriveFile`, frozen: `source_id`, `file_key` (the stable id: a path relative to the root for iCloud, the file id for Google, the `id:` for Dropbox), `path` (for display and folder filters), `name`, `size` (bytes, `None` for Google-native files), `modified` (ISO8601 with offset), `revision` (Google `version` or `headRevisionId`, Dropbox `rev`, iCloud `f"{st_mtime_ns}:{st_size}"`), `area` (`"own"`, `"shared_drive"`, `"shared_with_me"`), `mime` (as reported, may be `None`), `native` (`True` for Google Docs, Sheets and Slides).
- `Listing`: `files` (list of `DriveFile`), `deleted` (list of `file_key`), `full` (bool: a complete listing, so anything not in it is gone), `cursor` (opaque string to store for the next incremental listing, or `None`).
- `class Lister(Protocol)`: `list(cursor: str | None) -> Listing`.
- `class Fetcher(Protocol)`: `fetch(f: DriveFile, dest_dir: Path) -> Path`, which writes the bytes to a new file in `dest_dir` and returns its path.
- `DriveError` (listing failed: the pass is FAILED) and `FetchError` (one file failed: the pass is PARTIAL).

### 2. iCloud Drive — `core/adapters/icloud_drive.py` (new)

- **Roots:** `~/Library/Mobile Documents/com~apple~CloudDocs` and the `Documents` folder of each Apple document app container in `~/Library/Mobile Documents` (`com~apple~Pages`, `com~apple~Numbers`, `com~apple~Keynote`, `com~apple~Preview`, `com~apple~TextEdit`). For the archive copies, the root is the copy folder itself.
- **Excluded:** the `LMS` folder at the top of iCloud Drive (the system's inbox and briefs; reading it would feed the LMS its own output), hidden files and folders, and `.icloud` stub names, which are mapped to the real file name they stand for.
- **Listing:** `os.scandir` recursively, `stat` only. A file not yet downloaded is listed from its stub with the size macOS reports. Every listing is full (`full=True`, `cursor=None`).
- **Fetching:** if the file is not downloaded, ask iCloud for it with the watch folder's existing materialise step (`core/adapters/watchfolder.py`) and wait up to its timeout; then copy the bytes to `dest_dir`. The original is opened read-only and never moved.

### 3. Google Drive — `core/adapters/gdrive.py` (new)

- The same Google API library and backoff as Gmail (`core/adapters/gmail.py`). Credentials from the Keychain item `lms/gdrive-oauth/<address>`. Scope `https://www.googleapis.com/auth/drive.readonly` and nothing else.
- **First listing:** `files.list` with `corpora=allDrives`, `includeItemsFromAllDrives=true`, `supportsAllDrives=true`, `q="trashed = false"`, 1,000 per page, fields limited to what `DriveFile` needs plus `parents`, `ownedByMe`, `driveId`. Then `changes.getStartPageToken` is stored as the cursor.
- **Later listings:** `changes.list` from the cursor. Removed or trashed files go to `deleted`. A `410`/expired token means a full listing and `full=True`.
- **Area:** `driveId` set → `shared_drive`; `ownedByMe` → `own`; otherwise `shared_with_me`.
- **Path:** built from `parents` with a per-pass cache of folder names.
- **Fetching:** `files.get_media` for ordinary files; `files.export` as `application/pdf` for Docs, Sheets and Slides (Google limits an export to 10 MB; a larger one is a `FetchError` with that reason).

### 4. Dropbox — `core/adapters/dropbox.py` (new)

- Plain HTTPS with `urllib`, as `core/adapters/lmstudio.py` does; no new dependency. Token from the Keychain item `lms/dropbox-oauth/<address>`: a refresh token from an offline PKCE grant, exchanged for a short-lived access token each pass.
- **First listing:** `files/list_folder` on `""` with `recursive=true`, then `list_folder/continue` until `has_more` is false. The final cursor is stored.
- **Later listings:** `list_folder/continue` from the cursor. `deleted` entries go to `deleted`. `reset` or an expired cursor means a full listing.
- **Area:** always `own`.
- **Fetching:** `files/download` by id.

### 5. The engine — `core/pipeline/drives.py` (new)

`DriveSource`, frozen settings: `source_id`, `label`, `lister`, `fetcher`, `files_documents` (bool; `False` for the archive copies), `filed_areas` (set of areas whose documents are filed), `start` (ISO date), `action` (log name).

`run_pass(src, conn, roots, registry, classifier, now, tmp) -> PassResult`:

1. **Index.** `lister.list(stored cursor)`. Upsert every `DriveFile` into `drive_files` (`last_seen = now`); set `deleted_at` for `deleted` keys, and on a full listing for every row of the source not seen in it. Store the new cursor. Never delete a row.
2. **Choose.** Rows where all hold:
   - `src.files_documents` and the row's `area` is in `src.filed_areas`;
   - it is a document: by extension `pdf jpg jpeg png heic tif tiff doc docx xls xlsx ppt pptx txt rtf csv md eml`, or `native`;
   - `modified >= src.start` (a backfill passes an earlier start, a folder prefix and an end date);
   - `modified <= now - 24 hours`;
   - `filed_revision` is not the current `revision`;
   - `size` is `None` or ≤ 100 MB;
   - `deleted_at` is null;
   - fewer than 3 failed attempts on this revision.
3. **File.** For each chosen row, oldest first: `fetcher.fetch` into a fresh temp directory, then `ingest_file(conn, roots, registry, classifier, path, source=src.source_id, source_ref=f"{file_key}@{revision}", artifact=Artifact(...))` with the file's name, path and modified date. Record `filed_revision`, `artifact_id`, `sha256`, or on failure `attempts += 1` and the reason. Ingest's own sha256 check makes a document already filed from elsewhere a recorded duplicate, not a second copy. The temp copy is always removed.
4. **Halt** is checked between files.

`PassResult`: counts of files listed, new, changed, deleted, filed, duplicates, failed, too large, and whether the listing was full. `result` is FAILED on a `DriveError`, PARTIAL when any file failed, otherwise OK. Too-large files are counted in the summary and do not make the pass PARTIAL.

### 6. Database — `core/db/schema.sql`

```sql
CREATE TABLE IF NOT EXISTS drive_files (
    source_id       TEXT    NOT NULL,
    file_key        TEXT    NOT NULL,
    path            TEXT    NOT NULL,
    name            TEXT    NOT NULL,
    area            TEXT    NOT NULL,
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

CREATE TABLE IF NOT EXISTS drive_cursors (
    source_id       TEXT    PRIMARY KEY,
    cursor          TEXT,
    updated_at      TEXT    NOT NULL
);
```

### 7. Jobs — `ops/drives.py`, `ops/backfill_drives.py`, `ops/com.lms.drives.plist`

- `ops/drives.py` runs one pass for each configured source in turn (`--source` for one). `--probe` prints counts only: files by source, area and document type, total size, newest change, and how many would be filed. **No file or folder names.** `--dry-run` lists what would be filed and writes nothing.
- `ops/backfill_drives.py --source X [--folder PREFIX] [--since DATE] [--until DATE]` files with an earlier start, in batches of 20, resumable (a filed revision is skipped), stopping between batches on a halt. A backfill is not recorded as a check of the source (as D-074).
- `com.lms.drives`: launchd, every 3 hours (`StartInterval` 10800, `RunAtLoad`), same environment and Full Disk Access as the other jobs.
- Sources are configured in `config/sources.yaml` (status, start date) and switched on one at a time.

### 8. Access — `ops/authorise_gdrive.py`, `ops/authorise_dropbox.py`

- Google: same flow and code as `ops/authorise_gmail.py`, with scope `drive.readonly`, through the existing internal app of each Workspace (D-061), stored under `lms/gdrive-oauth/<address>`. Before saving it calls `about.get(fields=user)` and refuses if the token is not for the address named.
- Dropbox: PKCE authorisation-code flow with `token_access_type=offline`; the app key is passed in, there is no secret. Before saving it calls `users/get_current_account` and refuses if the email is not the one named. Stored under `lms/dropbox-oauth/<address>`.
- Both write through `security -i`: no token on argv or in shell history.
- Hosts for the future egress allowlist (Phase 8): `www.googleapis.com`, `oauth2.googleapis.com`, `api.dropboxapi.com`, `content.dropboxapi.com`.

### 9. Coverage, brief and storage report

- `config/sources.yaml`: `storage:icloud-drive` (cadence 3), new `storage:icloud-archive` (index only, read once, no cadence), `storage:gdrive:matthew@mrecai.com` and `storage:gdrive:matthew@mleca.com` (cadence 3) replacing `storage:google-drive`, and `storage:dropbox` (cadence 3, account `matthew@mrecai.com`). Each pass writes a `source_checks` row with the summary.
- `core/reports/brief.py`: `DRIVES_CHECKED` with a 7-hour stall window and `DRIVES_FAILED`, so the stalled-job test (D-027) sees the new literals.
- `core/reports/storage.py`: original bytes at each drive measured from `drive_files` (sum of `size` for rows not deleted); reclaimable counts only rows with a `sha256` matching a filed artifact, and says the rest is not measured.

## Errors

| Case | Result |
|---|---|
| Token revoked or expired (`invalid_grant`, `401`) | FAILED: "re-authorise: ./ops/authorise_gdrive.py <address>" (or the Dropbox script) |
| Google change token or Dropbox cursor expired | A full listing; noted in the summary, not an error |
| Drive unreachable, rate limited after backoff | FAILED; next pass retries |
| iCloud file not downloaded within the timeout | That file failed (PARTIAL); retried next pass, up to 3 times per revision |
| Export over Google's 10 MB limit | That file failed with the reason; indexed, not filed |
| Unreadable format (an `.xls` the pipeline cannot read) | Filed to the review queue with the original kept (D-055), as for mail |
| Halt | The pass stops between files; the backfill between batches |

## Setup, in order

1. **iCloud Drive and the archive copies:** nothing from Matthew. `git pull`, `./ops/drives.py --source storage:icloud-drive --probe`, `--dry-run`, then switch the source on and install `com.lms.drives`. The archive copies are indexed by one `--source storage:icloud-archive` run.
2. **Google Drive:** the developer enables the Drive API in the MRECAI and MLECA Cloud projects and adds `drive.readonly` to each consent screen. Matthew runs one Allow per account in a private window. Then probe, dry run, switch on.
3. **Dropbox:** the developer creates a Dropbox app (scoped access, full Dropbox, `files.metadata.read`, `files.content.read`, `account_info.read`; development status is enough). Matthew runs one Allow. Then probe, dry run, switch on.

Each source's start date is the day it is switched on.

## Testing

All on Windows with fakes; nothing needs a real account.

- **Engine rules:** start date, 24-hour settle, revision already filed, size limit, index-only sources and areas, document types, three attempts per revision, oldest first.
- **Index:** new, changed, deleted (incremental and by absence from a full listing), cursor stored, a new revision of a filed file filed again, a duplicate of a filed artifact recorded as a duplicate.
- **iCloud adapter:** a temp tree with stub files, the `LMS` folder skipped, hidden files skipped, app containers' `Documents` included, archive copy root.
- **Google adapter:** recorded response shapes for paging, all-drives listing, area mapping, path building, the change feed, `410` → full listing, native export as PDF, `invalid_grant`.
- **Dropbox adapter:** recorded shapes for `list_folder`, `continue`, deleted entries, `reset`, token refresh, download.
- **No writes:** adapters and engine run with `os.rename`, `os.replace`, `os.remove`, `os.unlink`, `shutil.move`, `shutil.rmtree` on the source root patched to raise; a source scan fails if those calls appear in the adapters.
- **Jobs:** probe prints no names, dry run writes nothing, backfill resumes and halts between batches, the brief knows `DRIVES_CHECKED` and `DRIVES_FAILED` (D-027), job tests load `_halt` behind the Windows `killpg` shim.

## Not in scope

- Mac folders outside iCloud Drive (R8): a later source on the same engine.
- The managed `iCloud Drive/OpenClaw` root (R24): the archive stays on the array.
- Near-duplicate and visual comparison beyond what D-074 already does.
- Any write, move, rename, delete or permission change in a drive.
