# WhatsApp reader — design

**Date:** Nov 5 2026 · **Requirement:** R40 (WhatsApp, hourly, read-only; Matthew, Oct 19, D-065), R16 (hourly refresh of active sources), R7 (coverage register) · **Decision to record at build:** D-079 · **Approved by:** the developer, Nov 5 2026

## Goal

Every WhatsApp conversation Matthew has is read, filed to the right business, and checked hourly for anything that should not wait for the brief, without ever writing to WhatsApp or sending anything.

## Decisions taken while designing

| Question | Answer |
|---|---|
| How to read WhatsApp | The WhatsApp Mac app's local database, from a copy (below). Not an unofficial WhatsApp Web client (ban risk, a Node or Go runtime, his keys on the machine). Manual "Export chat" into the watch folder stays as the fallback if the database turns out unreadable. |
| What a document is | **One chat, one day (Eastern time), one transcript.** Files and photos sent in the chat are documents of their own under it, as mail attachments are under their email (D-040). |
| Which chats | All, groups included (D-065: "ALL WHATSAPP"). WhatsApp Status broadcasts are not chats and are skipped. |
| Voice notes | Transcribed now, on the Mac, with a local speech model. |
| Mac app | Not installed yet. Installing it is part of setup; linking needs one QR scan from Matthew's phone. |

## What is assumed and must be confirmed on the Mac first

Two facts come from outside reports, not from this machine. A forensic guide and coverage of the May 2026 Mysk research describe `ChatStorage.sqlite` as unencrypted SQLite (Core Data, `ZWA*` tables); the same coverage disagrees on whether another process can read the app's group container. So:

1. The file is at `~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite`, with `-wal` and `-shm` beside it, and media under `…/Message/`.
2. With Full Disk Access granted to the job's interpreter, a process running as Matthew's user can copy those files.

`ops/whatsapp.py --probe` answers both on day one and prints only table names, column names and row counts, never a message. **Nothing below is built past the reader until the probe passes on the Mac.** If it fails, the build stops and falls back to chat export.

The table and column names below are the expected ones (the iOS schema the Mac app shares) and are confirmed by the probe, not trusted.

## Components

### 1. Reader — `core/adapters/whatsapp.py`

- `snapshot(src_dir, tmp_dir) -> Path`: copies `ChatStorage.sqlite`, `-wal` and `-shm` into a fresh private directory (mode 0700) under `<LMS root>/tmp/whatsapp/` (beside the mail spool), and returns the copy. **SQLite never opens the originals;** they are only read as bytes by the copy. The copy is ours, so it is opened normally and SQLite applies the copied WAL, which holds recent messages not yet merged into the main file. WhatsApp may be writing while the three files are copied, so the copy is checked with `PRAGMA quick_check`. If the check fails, the copy is retried up to 3 times; after that the pass is `FAILED`. The snapshot directory is deleted at the end of every pass, including failed ones.
- `check_schema(conn) -> list[str]`: the required tables and columns that are missing. Required:
  - `ZWACHATSESSION`: `Z_PK`, `ZCONTACTJID`, `ZPARTNERNAME`, `ZSESSIONTYPE`
  - `ZWAMESSAGE`: `Z_PK`, `ZCHATSESSION`, `ZSTANZAID`, `ZMESSAGEDATE`, `ZISFROMME`, `ZTEXT`, `ZMESSAGETYPE`, `ZGROUPMEMBER`, `ZMEDIAITEM`
  - `ZWAMEDIAITEM`: `Z_PK`, `ZMEDIALOCALPATH`, `ZTITLE`, `ZFILESIZE`, `ZMOVIEDURATION`
  - `ZWAGROUPMEMBER`: `Z_PK`, `ZCONTACTNAME`, `ZMEMBERJID`

  Anything missing fails the pass loudly (a WhatsApp update renamed it). It never reads zero rows and calls that a quiet hour.
- `read(conn, since: datetime) -> list[Chat]`: plain dataclasses, no SQL outside this module.
  - `Chat(jid, name, is_group, messages)`
  - `Message(stanza_id, sent_at, from_me, sender_name, text, kind, media)`. `sent_at` is converted from Apple time (seconds since 2001-01-01 UTC) to an aware datetime. `kind` is one of `text`, `image`, `video`, `voice`, `document`, `sticker`, `other`, mapped from `ZMESSAGETYPE`, with the mapping confirmed by the probe.
  - `Media(local_path | None, title, size, duration_s)`. `local_path` is `None` when the Mac app has not downloaded the file.
  - Chats whose jid is `status@broadcast` are skipped. Group senders come from `ZWAGROUPMEMBER.ZCONTACTNAME`, then the member jid's number. `from_me` messages are "Matthew".

### 2. Speech — `core/adapters/asr.py`

- `transcribe(path) -> str`: decodes the `.opus` voice note with `ffmpeg` to 16 kHz mono WAV in the pass's temp directory, then runs `mlx_whisper.transcribe` against Whisper large-v3-turbo from a local model directory, with `HF_HUB_OFFLINE=1` so nothing is fetched at run time. Language is auto-detected.
- LM Studio serves chat and embeddings, not speech, so speech runs in-process in the LMS venv rather than through `localhost:1234`. D-004's rule (no model call leaves the machine) still holds. The `TIER-A` name stays; its model becomes `whisper-large-v3-turbo` until a Qwen3-ASR MLX runtime is confirmed on the Mac.
- Notes over 10 minutes are not transcribed. If `ffmpeg` or the model is missing, `transcribe` raises `ASRUnavailable`. Neither is a failure of the pass (see Results).

### 3. Chat days — `core/pipeline/whatsapp.py`

State is one new table:

```sql
CREATE TABLE IF NOT EXISTS whatsapp_messages (
    stanza_id     TEXT PRIMARY KEY,          -- WhatsApp's own message id; survives a re-link, Z_PK does not
    chat_jid      TEXT NOT NULL,
    day           TEXT NOT NULL,             -- YYYY-MM-DD, America/New_York
    scanned_at    TEXT,                      -- hourly urgency check done
    artifact_id   INTEGER REFERENCES artifacts(id),   -- the transcript it was filed in
    voice_text    TEXT                       -- cached transcription; NULL if none or not yet
);
CREATE INDEX IF NOT EXISTS idx_whatsapp_unfiled ON whatsapp_messages(chat_jid, day) WHERE artifact_id IS NULL;
```

No message text is stored here except the voice transcription cache. Message text lives in the filed transcript.

**Transcript format** (UTF-8 `.txt`):

```
WhatsApp chat: Acme Supply (direct, +1 555 0100)
Date: 2026-11-05 (America/New_York)
Part: 1

09:14 Matthew: Can you resend the invoice?
09:20 Acme Supply: [document: INV-2291.pdf]
09:21 Acme Supply: [voice 0:42] Hi Matthew, the invoice is due Friday…
09:30 Acme Supply: [photo]
09:31 Acme Supply: [video, not filed]
09:32 Acme Supply: [document: scan.pdf, not downloaded on the Mac]
09:40 Acme Supply: [voice 12:10, not transcribed: over 10 minutes]
```

Groups put the group name on the first line and `(group)`.

**Hourly urgency check** (`scan`):

For each chat with messages from today or yesterday not yet `scanned_at`:
1. Voice notes among them are transcribed first, and the text is cached in `voice_text`.
2. The new lines, formatted as above, go to the existing `Classifier.classify` as an `Artifact(source="whatsapp", subject=<chat name>, sender=<chat>, recipient="Matthew (WhatsApp)", body=<lines>, received_date=<day>)`.
3. If `notify.reason_for(urgency, category)` gives a reason, `notify.enqueue(key=f"whatsapp:{jid}:{day}", …)` queues an alert. The key makes it one alert per chat per day, however many hours it stays urgent. Nothing is filed in this step.
4. The messages are marked `scanned_at`.

**Closing a day** (`close`):

A chat day is closed at the first pass at or after 00:30 Eastern on the next day.
1. All of a closed day's messages with `artifact_id IS NULL` are formatted into one transcript.
2. It is `Part 1` if the chat day has no filed part yet, else the next part. Late-synced messages become a part 2; nothing filed is ever rewritten.
3. The transcript is written to the pass's temp directory and passed to `ingest_file(source="whatsapp", source_ref=f"whatsapp:{jid}:{day}:{part}", artifact=…, alert_key=f"whatsapp:{jid}:{day}")`.
4. Each downloaded **document or photo** in those messages is copied to temp and ingested with `source="attachment"`, `parent_id=<transcript artifact>`, `source_ref=f"whatsapp:{stanza_id}"`. Videos and stickers are listed in the transcript and not filed.
5. The messages get `artifact_id`.

**Always a copy.** `filing.file_artifact` moves the file it is given (`filing.py:326`), so every transcript and every media file handed to ingest is a copy in the pass's temp directory, never a path inside WhatsApp's container.

### 4. Changes to existing code

- `ingest_file` gains `alert_key: str | None = None`, passed to `notify.for_document(key=…)`. With it, the closed day's filing reuses the hourly check's key, so a chat day already alerted is not alerted twice (`INSERT OR IGNORE`). Without it, behaviour is unchanged.
- `notify.for_document` gains the same optional `key`, defaulting to today's `artifact:{parent_id or artifact_id}`.
- `config/sources.yaml` `chat:whatsapp`: method, permissions and gaps rewritten; `status: active` once installed on the Mac.
- `core/reports/brief.py`: `WATCHED_JOBS["WHATSAPP_CHECKED"] = ("whatsapp", 3)` and `JOB_FAILURES["WHATSAPP_FAILED"] = "WHATSAPP_CHECKED"`. The stalled-job alert (D-077) then covers this job with no new code.
- `core/db/schema.sql`: the table above.

### 5. The job — `ops/whatsapp.py`, `com.lms.whatsapp` (hourly)

One pass:
1. Stop if the halt flag is set (D-059).
2. Stop if the archive volume is missing.
3. Snapshot, then check the schema.
4. `scan`, then `close`.
5. `coverage.record_check(conn, "chat:whatsapp", …)` and the `WHATSAPP_CHECKED` row.
6. Delete the snapshot.

Flags:
- `--probe`: steps 1–3, prints tables, columns, row counts, the `ZMESSAGETYPE` values present with counts, and whether any media file is readable. Writes nothing.
- `--dry-run`: does everything except ingest and the database writes, and prints chat-day counts.

launchd runs the job every hour (`StartInterval 3600`), logging to `~/LMS/logs/whatsapp.log` and `.err`.

**Backfill** — `ops/backfill_whatsapp.py --days N [--batch 20]`:
- Closes every day before yesterday that the Mac app holds, in batches of `--batch` chat days (default 20), with a summary after each. `--days N` limits it to the last N days.
- No urgency check runs on backfilled days: their messages are marked scanned. Yesterday and today are left to the hourly job, so its urgency check still sees them. `notify`'s 2-day freshness rule stops alerts for days older than 2 days; the day before yesterday is still fresh, so an urgent one alerts once as it is filed.

### 6. Results and failures

| Situation | `source_checks` result | Action row | Brief / Telegram |
|---|---|---|---|
| Pass completed | `OK` | `WHATSAPP_CHECKED` | — |
| Some voice notes not transcribed, or some media not downloaded on the Mac | `PARTIAL`, detail counts them | `WHATSAPP_CHECKED` | Coverage section discloses the gap |
| Database missing, unreadable, or schema changed | `FAILED` | `WHATSAPP_FAILED` with the reason | "whatsapp: FAILED …", pushed once a day (D-077) |
| Job not running | (none) | (none) | "whatsapp: last ran …" after 3 h, pushed |

A failure in one chat's ingest is logged, and that chat day stays unfiled for the next pass. It does not stop the other chats.

### 7. Safety

- **Read-only by construction:** only copies are opened, and nothing calls WhatsApp. Nothing is ever sent (`never_send_without_approval` stands).
- **Untrusted text:** chat text reaches only the classifier, which has zero tools (D-009 control 1).
- **No sender authentication:** WhatsApp has nothing like SPF, DKIM or DMARC, so the phishing rules that read mail authentication never fire. The wording rules (changed bank details, urgency) do.
- **Gaps disclosed in the register:**
  - A message deleted in WhatsApp after filing stays filed.
  - The Mac app only holds the history it synced when linked.
  - Videos are not filed.
  - Media the Mac app never downloaded is listed, not filed.

## Setup on the Mac — `lms/ops/WHATSAPP_APPLY.md`

1. Install WhatsApp from the Mac App Store. In WhatsApp → Settings → Storage, set auto-download to documents, photos and audio.
2. **Matthew scans the QR code once from his phone** (WhatsApp → Linked devices).
3. Full Disk Access for the LMS venv's Python (System Settings → Privacy & Security).
4. `brew install ffmpeg`. Install `mlx-whisper` into the venv and download Whisper large-v3-turbo once to a local directory.
5. `./ops/whatsapp.py --probe`. **Stop here if it fails.**
6. `./ops/backfill_whatsapp.py --days 30`, then install `com.lms.whatsapp`.

## Tests

All tests are on Windows except where marked. They use a fixture database built with the expected tables, and fake speech.
- `snapshot` never changes the original's bytes or mtime, and the temp directory is gone after a failing pass.
- A copy that fails `quick_check` is retried, and fails the pass after 3 attempts.
- A missing column fails the pass with `FAILED`, not zero messages.
- Apple-time conversion; days split in Eastern time, including across the Nov 1 DST change.
- Group sender names, `from_me` as Matthew, and `status@broadcast` skipped.
- The transcript format, including every bracketed marker.
- Closing at 00:30, not before. A late message files a part 2, and part 1 is untouched.
- Media ingested from a copy with the right `parent_id`; videos and stickers not filed; an undownloaded file is listed and counted as `PARTIAL`.
- The voice transcription is cached and not repeated. `ASRUnavailable` gives `PARTIAL` and a marker.
- The hourly check queues one alert per chat day, and the closing filing does not queue a second.
- The halt flag stops the pass. Backfill queues no alerts for days older than 2 days.
- The brief's `WATCHED_JOBS` and `JOB_FAILURES` strings are ones this job writes (the D-027 trap, as in `test_notify.py`).
- **On the Mac only:** the probe against the real database, and one real voice note through `ffmpeg` and `mlx-whisper`.

## Out of scope

- Sending or replying on WhatsApp.
- Filing videos.
- Muting chats.
- WhatsApp Business API.
- Reading a phone backup.

## Effort

About 6–8 days, including speech.
