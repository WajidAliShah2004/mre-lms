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
brew install ffmpeg
```
```bash
.venv/bin/python -m pip install mlx-whisper
```
```bash
.venv/bin/python -c "from huggingface_hub import snapshot_download; snapshot_download('mlx-community/whisper-large-v3-turbo', local_dir='/Users/mleca/LMS/models/whisper-large-v3-turbo')"
```

The last command downloads about 1.6 GB once; after it, speech runs offline.

## 6. Dry run, backfill, install

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

Then in `config/sources.yaml` set `chat:whatsapp` to `status: active` and
remove its `blocked_on` (the developer commits that).

## Older files

Auto-download covers only media received after it was turned on, so the
history's files (about 15,800 items, 643 of them documents) were never
downloaded to the Mac. The backfill files those days' text, and lists those
files as "not downloaded on the Mac". Bringing them in is Matthew's choice,
either way:

1. Open the chats that matter in the Mac app, so their files download; the
   next pass files them as a late part of that day.
2. Export those chats with media from his phone, and drop the export in the
   watch folder.

## Undo

```bash
launchctl bootout gui/$(id -u)/com.lms.whatsapp
```
```bash
rm ~/Library/LaunchAgents/com.lms.whatsapp.plist
```

Filed transcripts stay filed. A halt (`/halt`) stops this job with the rest.
