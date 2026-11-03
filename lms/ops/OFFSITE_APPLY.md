# Off-site backup to Backblaze B2 — apply on the Mac (R29)

The nightly backup (`com.lms.backup`, 02:30) writes one restic repository on
the internal SSD. It survives the array failing; it does not survive the Mac
(theft, fire, the machine dying). This adds a **second restic repository in a
private B2 bucket** and copies every nightly snapshot there (D-063, spec §11.4).

The code is already on the Mac after `git pull` and does nothing until step 2
below has run. Until then the nightly log says `off-site: not configured (R29)`
and `./ops/verify_setup.py` [8] WARNs the same. Nothing fails.

Before starting: D-012 asks for Matthew's sign-off **in writing** that client
tax and NPI data may leave the premises, encrypted. restic encrypts on the Mac
before upload, so Backblaze only ever holds ciphertext — that is the
mitigation, not a reason to skip the sign-off.

---

## 1. Matthew, in Backblaze (about 5 minutes)

Do this **in a browser on the Mac**, during the setup session, so the key goes
from the Backblaze page straight to the Mac's clipboard and Keychain and never
travels anywhere else. **Turn RustDesk clipboard sharing off first** —
otherwise the copied key is also copied to the developer's computer.

Sign in to Backblaze (the account from C6). Not with a password anyone has
been sent (D-017).

**Create the bucket** — B2 Cloud Storage → Buckets → *Create a Bucket*:

| Setting | Value |
|---|---|
| Bucket Unique Name | e.g. `mre-lms-backup-` plus a few random letters (names are global across all of Backblaze) |
| Files in Bucket are | **Private** |
| Default Encryption | Enable (harmless; restic already encrypts) |
| Object Lock | **Disable** — restic's retention has to delete old data |

Then the bucket's *Lifecycle Settings*: **Keep only the last version of the
file.** restic manages its own history; old B2 versions would only be billed.

**Create the key** — Application Keys → *Add a New Application Key*:

| Setting | Value |
|---|---|
| Name of Key | `lms-restic-mac` |
| Allow access to Bucket(s) | **only the bucket above** — never "All" |
| Type of Access | **Read and Write** |
| Allow List All Bucket Names | leave unticked |
| File name prefix | leave empty |
| Duration | leave empty |

Backblaze then shows a **keyID** and an **applicationKey**. The applicationKey
is shown **once**. Leave that page open for step 2; do not copy it anywhere
else, photograph it, or send it.

Never use the *Master Application Key*. The setup script refuses it, and any
key that is not restricted to this one bucket.

## 2. Developer, at the Mac

```bash
cd ~/lms-repo/lms
git pull
./ops/verify_setup.py                  # [8]: WARN off-site not configured (R29)
./ops/set_offsite_backup.py --bucket <the bucket name>
```

It asks for the two values one at a time: **copy** the keyID in the Backblaze
page, press Return in the terminal; then the applicationKey, Return. They are
read from the clipboard — nothing is typed or pasted into the terminal (D-035)
— and the clipboard is cleared afterwards.

It then asks Backblaze what the key may do and **refuses** it, storing
nothing, unless it is restricted to this bucket, can list/read/write/delete
files, cannot manage keys, and the bucket is private. Then it:

- stores `lms/b2-key-id` and `lms/b2-app-key` in the Keychain,
- generates the **off-site repository passphrase** (`lms/restic-offsite`; a
  different one from the local backup's),
- runs `restic init` on `b2:<bucket>:lms` with the local repository's chunker
  parameters,
- stores `lms/offsite-repo` **last** — this is what turns the nightly copy on.

Expect, at the end, a box headed `OFF-SITE RESTIC PASSPHRASE — record this
NOW`. **Matthew puts it in his password manager, now**, next to the local
backup passphrase. It is printed once. restic has no recovery path, and this is
the copy that exists for the day the Mac and its Keychain are gone — a
passphrase that only lives in that Keychain goes with it. (`--show` prints it
again from the Keychain if needed.)

Then the first copy, by hand, and prove it restores:

```bash
./ops/offsite_backup.py                # copies every local snapshot; minutes
./ops/restore_test.py --offsite        # downloads the latest and checks it
./ops/verify_setup.py                  # [8]: PASS off-site repository configured
./ops/set_offsite_backup.py --check    # key still bucket-only, snapshot count
```

Expect `copied (…s)` then `done`, and `Restore verified — the OFF-SITE backup
is usable.` The first copy writes `OFFSITE_COPY_COMPLETED`, which arms the
brief's watch: from then on, if no copy succeeds for 48 hours the morning
brief shows `off-site backup: last ran …` under THE SYSTEM NEEDS ATTENTION.

The nightly job does the rest: after each successful local snapshot it copies
off-site and applies retention there (`forget --prune --group-by host
--keep-daily 90 --keep-monthly 24 --keep-tag hold`). It only runs while the
system is not halted.

### Next morning

```bash
tail -20 ~/LMS/logs/backup.log
sqlite3 /Volumes/MacStudioHD/LMS/lms.db \
  "SELECT ts, action, detail FROM actions_log WHERE action LIKE 'OFFSITE_%' ORDER BY id DESC LIMIT 5"
```

`OFFSITE_COPY_COMPLETED` at about 02:3x. If `backup.err` shows a Keychain
exit 51, macOS wanted permission for the scheduled job to read the new items:
run `launchctl kickstart -p gui/$(id -u)/com.lms.backup` while at the Mac and
click *Always Allow*.

## If it fails

`backup.py` exits **3** when the local backup succeeded and the off-site copy
did not, and logs `OFFSITE_COPY_FAILED` (or `OFFSITE_PRUNE_FAILED` if only the
retention step failed) with restic's error, secrets removed. The next night
catches up on its own — `restic copy` skips what is already there.

- `repository is already locked` — a run was interrupted. With nothing else
  running: `./ops/offsite_backup.py --unlock`, then `./ops/offsite_backup.py`.
- `401` / `bad_auth_token` — the key was deleted or changed in Backblaze.
  Create a new one (step 1) and run `./ops/set_offsite_backup.py --bucket …`
  again; the existing off-site passphrase is kept, never replaced.
- Anything `half-configured` — re-run `./ops/set_offsite_backup.py --bucket …`.

## Quarterly (spec §11.4, §11.8)

In January, April, July and October, at the Mac:

```bash
./ops/restore_test.py --offsite
```

Not scheduled on purpose: a drill nobody watches is not a drill. It downloads
one snapshot (a few hundred MB; cents).

## Turning it off

```bash
security delete-generic-password -s lms/offsite-repo
```

The nightly job is inert again and logs `OFFSITE_NOT_CONFIGURED`. The other
three Keychain items and the B2 data are left alone. Because the watch was
armed, the brief will then say `off-site backup: last ran …` — correctly: it
was running and it stopped. The B2 copy itself is deleted only in Backblaze,
by Matthew.

## Outbound allowlist (PHASES Phase 8 item 3, deferred)

If default-deny outbound is ever turned on, allow `api.backblazeb2.com` and
`*.backblazeb2.com` (B2 assigns the account an API host and a download host
under that name).
