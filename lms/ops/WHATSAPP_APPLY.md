# WhatsApp reader — apply on the Mac (R40, D-079)

Installs `com.lms.whatsapp`. Every hour it reads a copy of the WhatsApp Mac
app's database, checks today's new messages for anything urgent (Telegram
alert, D-077), and files each finished day of each chat as one transcript.
It never writes to WhatsApp and never sends.

Steps 1–4 were done on the Mac on Nov 6 (installed, linked, Full Disk Access,
probe passed, auto-download on), so start at step 5. They stay here for a
rebuild.

About 40 minutes. **Step 2 needs Matthew's phone, once.**

Type commands one line at a time over RustDesk, one command per block.
Expected output is written under each block, never after a `#` on the command
line (zsh on this Mac passes `#` to the command).

## 1. Install WhatsApp and set downloads

Mac App Store, WhatsApp. Then WhatsApp → Settings → Storage → Automatic
downloads: tick Photos, Audio, Video and Documents. This applies only to
media received after it is on. A file the Mac app never downloads is listed in
the transcript but cannot be filed.

## 2. Matthew links it

On his phone: WhatsApp → Settings → Linked devices → Link a device, and scan
the QR code on the Mac. Let the Mac app finish syncing (the chat list fills).

## 3. Full Disk Access

System Settings → Privacy & Security → Full Disk Access → `+`, press
Cmd-Shift-G, paste `/Users/mleca/lms-repo/lms/.venv/bin/python`, Open, turn
it on.

## 4. Probe: stop here if it fails

```bash
cd ~/lms-repo/lms && git pull
```
```bash
./ops/whatsapp.py --probe
```

`ZWAMESSAGE: <n>` with n above 0, no `MISSING:` line, and `media folder
readable: True`. Anything else: copy the output to the developer and stop.

## 5. Speech

```bash
cd ~/lms-repo/lms && git pull
```
```bash
./ops/whatsapp.py --probe | tail -2
```

It must say `own chat (Message yourself): found under his number; the
reader skips it`. That chat holds the `/lms` commands and the LMS's replies
(D-080), so the reader never reads it. Any other wording: copy the output to
the developer and stop.
```bash
brew install ffmpeg
```
```bash
.venv/bin/python -m pip install mlx-whisper
```
```bash
.venv/bin/python -c "from huggingface_hub import snapshot_download; snapshot_download('mlx-community/whisper-large-v3-turbo', local_dir='/Users/mleca/LMS/models/whisper-large-v3-turbo')"
```

The last command downloads about 1.6 GB once; after it, speech runs offline.

## 6. Keep WhatsApp running

System Settings → General → Login Items (on newer macOS, Login Items &
Extensions) → Open at Login → `+`, choose WhatsApp in Applications, Open.

The reader reads only what the Mac app has synced, and the app syncs only
while it is running. If it is closed, nothing new arrives; after 48 hours
without a new message the pass reports PARTIAL, "WhatsApp on the Mac has not
synced since ...".

## 7. Dry run, backfill, install

```bash
./ops/whatsapp.py --dry-run
```
```bash
./ops/backfill_whatsapp.py --days 30
```
```bash
cp ops/com.lms.whatsapp.plist ~/Library/LaunchAgents/
```
```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.lms.whatsapp.plist
```
```bash
tail -3 ~/LMS/logs/whatsapp.log
```

The dry run lists unfiled chat days and writes nothing. The backfill prints
one line a batch and ends with `N chat day(s) filed.` The log line is the
pass summary, for example `412 message(s) seen, 0 chat day(s) filed, 0
alert(s)`.

- **The first pass takes a while.** It starts as soon as the job is loaded
  and checks two days of chats with the model. Wait about 15 minutes before
  the `tail`; until the pass ends the log can be empty.
- **`PermissionError` (or `no ChatStorage.sqlite`) in the log** means the
  job's Python lacks Full Disk Access: redo step 3 for exactly
  `/Users/mleca/lms-repo/lms/.venv/bin/python`. The probe in step 4 can pass
  anyway, because Terminal's own Full Disk Access lets it through.
- **A few alerts are expected now.** Urgent chat days from the last 2 days
  alert once, from the first pass and from the backfill (the day before
  yesterday is still fresh for alerts). Days older than 2 days raise none.

Then in `config/sources.yaml` set `chat:whatsapp` to `status: active` and
remove its `blocked_on` (the developer commits that).

## Older files

Auto-download covers only media received after it was turned on, so the
history's files (about 15,800 items, 643 of them documents) were never
downloaded to the Mac. The backfill files those days' text, and lists those
files as "not downloaded on the Mac". Bringing them in is Matthew's choice,
either way:

1. **Before the backfill (step 7):** open the chats that matter in the Mac
   app and let their files download; the backfill then files them with
   their day. A day already filed does not pick up files downloaded later:
   once a chat day is filed, only newly arrived messages make a later part.
2. **At any time:** export those chats with media from his phone, and drop
   the export in the watch folder.

## Undo

```bash
launchctl bootout gui/$(id -u)/com.lms.whatsapp
```
```bash
rm ~/Library/LaunchAgents/com.lms.whatsapp.plist
```

Filed transcripts stay filed. A halt (`/halt`) stops this job with the rest.
