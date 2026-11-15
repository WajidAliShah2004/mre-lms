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
