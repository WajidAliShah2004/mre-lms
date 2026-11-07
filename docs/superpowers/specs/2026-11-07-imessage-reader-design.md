# iMessage reader — design

**Date:** Nov 7 2026 · **Requirement:** R2 (iMessage and texts, read-only; all chats, hourly, D-065), R16 (hourly refresh of active sources), R7 (coverage register) · **Decision to record at build:** D-081 · **Approved by:** the developer, Nov 7 2026 · **Builds on:** the WhatsApp reader (D-079, [2026-11-05-whatsapp-reader-design.md](2026-11-05-whatsapp-reader-design.md))

## Goal

Every text Matthew sends or receives (iMessage, SMS and RCS) is read, filed to the right business, and checked hourly for anything that should not wait for the brief. Nothing is ever sent and Messages is never written to. It works the same way as the WhatsApp reader and shares its code.

## Decisions taken while designing

| Question | Answer |
|---|---|
| How to read texts | The Mac's own Messages database, `~/Library/Messages/chat.db`, from a checked copy (D-009). |
| Share or copy the WhatsApp pipeline | **Share** (approach A). The WhatsApp pipeline becomes a generic chat-day engine with a `ChatSource` settings object; WhatsApp and iMessage are two settings. Copying it (B) would mean fixing every bug twice; one table for all sources (C) would mean migrating a live table on the Mac. |
| What a document is | As WhatsApp: one chat, one Eastern-time day, one transcript; photos and documents filed under it. |
| Which chats | All, groups included (D-065). |
| Names in transcripts | Read the Mac's **Contacts** (read-only, same Full Disk Access) so a transcript says the contact's name; the number or address when there is no card. |
| First history load | **90 days** (developer, Nov 7). Older years can be loaded later with `--days`. |
| Verification-code texts | Treated like any other text. They expire within minutes, and the digest already hides credentials (D-072). |

## What the Mac probe showed (Nov 7)

Read with `sqlite3 -readonly` from Terminal, counts and dates only:

- 238,307 messages from Oct 2014: iMessage 209,641, SMS 25,089, RCS 3,577; about half are his. This is Matthew's real history, so the Mac's Messages is on his account (answers most of D-068).
- 1,513 one-to-one chats (style 45) and 251 group chats (style 43).
- 5,208 messages (about 2%) have no `text` and keep it only in `attributedBody`.
- Attachments: 11,808 images, 721 `application/*` (documents), 459 video, 245 text, 49 audio, 3,912 with no MIME type.
- **Nothing has arrived since Oct 5** (except 2 messages typed on the Mac on Nov 4). Messages on the Mac shows "Sign in to iMessage": **it has been signed out since Oct 5.** Until Matthew signs in again, the reader would file only history. Install waits on that (see Setup).

The reader's own `--probe` confirms the schema below on the Mac before the job is installed, as WhatsApp's did.

## Components

### 1. Shared types — `core/adapters/chat.py` (new)

`Media`, `Message` and `Chat` move here unchanged from `core/adapters/whatsapp.py`, which re-exports them so every existing import keeps working. `Message.stanza_id` keeps its name and now means "the source's own stable message id" (WhatsApp `ZSTANZAID`, iMessage `guid`). `Chat.jid` likewise means the source's chat id.

`snapshot(src_dir, tmp_dir, db_name, attempts=3)` moves here from the WhatsApp adapter with the database file name as a parameter (WhatsApp keeps a wrapper with its old signature). Behaviour is unchanged: copy the file, `-wal` and `-shm` into a private 0700 directory, `PRAGMA quick_check`, retry up to 3 times, never open the original.

### 2. The chat-day engine — `core/pipeline/chatdays.py` (new)

The body of today's `core/pipeline/whatsapp.py` moves here. Everything WhatsApp-specific becomes a field of a frozen dataclass:

```python
@dataclass(frozen=True)
class ChatSource:
    name: str            # "whatsapp" | "imessage": Artifact.source, alert-key and source_ref prefix
    label: str           # "WhatsApp" | "iMessage": transcript header and file name
    source_id: str       # "chat:whatsapp" | "messages:imessage"
    table: str           # "whatsapp_messages" | "imessage_messages"
    action: str          # "WHATSAPP" | "IMESSAGE": _CHECKED, _FAILED, _DAY_FAILED
    recipient: str       # "Matthew (WhatsApp)" | "Matthew (iMessage)"
    read: Callable[[Path, Path, datetime], tuple[list[Chat], datetime | None]]
    ident: Callable[[str], str]          # how a chat or sender id is shown (number, address)
    not_synced: str      # the stale-pass wording, e.g. "WhatsApp on the Mac has not synced since"
```

Every engine function takes the source as its first argument (`record(src, conn, chats)`, `scan(src, …)`, `close(src, …)`, `run_pass(src, …)`, `plan_days(src, …)`). Table names come only from `src.table`, which is one of two constants, never input.

One behaviour change, for both sources: a voice note with no known duration is shown as `[voice]` rather than `[voice 0:00]` (iMessage stores no duration). If any WhatsApp test pins the old text, that test is the spec and the change is limited to `duration_s is None`.

### 3. WhatsApp keeps its face — `core/pipeline/whatsapp.py`

Becomes a thin module: `CHAT_SOURCE = ChatSource(name="whatsapp", …)` with today's exact values (`SOURCE` stays the string `"whatsapp"` it is today), and module-level functions with today's names and signatures that call the engine with it (`record`, `scan`, `close`, `run_pass`, `plan_days`, `read_chats`, `line`, `transcript`, `header`, `alert_key`, `day_of`, `yesterday`, `closed`, `PassResult`, and the constants `SOURCE`, `SOURCE_ID`, `CHECKED`, `FAILED`, …). `ops/whatsapp.py`, `ops/backfill_whatsapp.py` and `tests/test_whatsapp.py` do not change. **The WhatsApp tests passing unchanged is the acceptance test for the refactor.**

### 4. The iMessage reader — `core/adapters/imessage.py` (new)

- `CONTAINER = ~/Library/Messages`, `DB_NAME = "chat.db"`, attachments under `~/Library/Messages/Attachments/`.
- `check_schema(conn) -> list[str]`, required:
  - `message`: `ROWID`, `guid`, `text`, `attributedBody`, `date`, `is_from_me`, `handle_id`, `service`, `associated_message_type`, `item_type`, `is_audio_message`
  - `chat`: `ROWID`, `guid`, `chat_identifier`, `style`, `display_name`
  - `handle`: `ROWID`, `id`
  - `chat_message_join`: `chat_id`, `message_id`
  - `message_attachment_join`: `message_id`, `attachment_id`
  - `attachment`: `ROWID`, `filename`, `mime_type`, `transfer_name`, `total_bytes`

  Anything missing fails the pass loudly, as WhatsApp's does.
- **Time:** `date` is nanoseconds since 2001-01-01 UTC; a value under 10^11 is seconds (very old rows) and is read as such.
- **Text:** `text` when present; otherwise decoded from `attributedBody` by `decode_attributed_body(blob) -> str | None`, a small parser of the NSKeyedArchiver "typedstream": find the `NSString` class marker, then read the length-prefixed UTF-8 bytes that follow (one byte, or `0x81` + 2 bytes little-endian, or `0x82` + 4 bytes). Anything it cannot parse returns `None` and the line shows `[message not readable]`; it never raises.
- **Skipped rows:** `associated_message_type != 0` (tapbacks and other reactions) and `item_type != 0` (renames, people added or left). They are not conversation.
- **Chats:** the chat key is `chat.chat_identifier` (one person's iMessage and SMS threads share it on current macOS; the probe counts how many identifiers have more than one `chat` row). `style = 43` is a group, named from `display_name`, else "Group with <first three names>". A direct chat is named from Contacts, else its identifier.
- **Senders:** `is_from_me` is "Matthew". In groups, the sender is the message's `handle.id`, named from Contacts.
- **Kinds**, from the attachment:
  - `image/*` gives `image` (filed); `video/*` gives `video` (listed); `is_audio_message = 1` gives `voice` (transcribed); `application/pdf`, `application/*`, `text/*` give `document` (filed); anything else, and a NULL MIME type, `other` with its `transfer_name`.
  - A message with text and several attachments becomes one line per attachment, the text on the first.
  - `Media.local_path` is the expanded `filename` when that file exists, else `None` (listed as not on the Mac). `duration_s` is `None`.
- **Contacts:** `contact_names(path) -> dict[str, str]` reads a snapshot of each `AddressBook-v22.abcddb` under `~/Library/Application Support/AddressBook/` (`ZABCDRECORD`, `ZABCDPHONENUMBER`, `ZABCDEMAILADDRESS`). Phone numbers are matched on their last 10 digits, emails case-folded. If Contacts cannot be read, names fall back to numbers and the probe says so; it is not a failure of the pass.
- `newest(conn)`: the latest message time in the copy, for the stale check.
- `newest_by_service(conn) -> dict[str, datetime]`: the latest iMessage, SMS and RCS times, for the SMS check.
- `probe(conn) -> dict`: table and column names, counts by service, chat style and attachment type, how many messages need `attributedBody`, how many decode, newest date per service, how many chat identifiers have more than one chat row, and how many contacts were read. **Never a message, number or name.**

State, a new table with the same columns as WhatsApp's, so the engine's SQL is the same:

```sql
CREATE TABLE IF NOT EXISTS imessage_messages (
    stanza_id     TEXT PRIMARY KEY,          -- message.guid
    chat_jid      TEXT NOT NULL,             -- chat.chat_identifier
    day           TEXT NOT NULL,             -- YYYY-MM-DD, America/New_York
    scanned_at    TEXT,
    artifact_id   INTEGER REFERENCES artifacts(id),
    voice_text    TEXT
);
CREATE INDEX IF NOT EXISTS idx_imessage_unfiled ON imessage_messages(chat_jid, day) WHERE artifact_id IS NULL;
```

### 5. iMessage's settings — `core/pipeline/imessage.py` (new)

`CHAT_SOURCE = ChatSource(name="imessage", label="iMessage", source_id="messages:imessage", table="imessage_messages", action="IMESSAGE", recipient="Matthew (iMessage)", …, not_synced="Messages on the Mac has not synced since")`, plus the same thin functions as the WhatsApp module.

Transcript header: `iMessage chat: Aunt Rhonda (direct, +1 555 0100)` or `(group)`; file name `iMessage <chat> <day> part <n>.txt`. Alert key `imessage:<chat_identifier>:<day>`.

**The SMS line in the register.** After each pass, `messages:sms` is recorded too, from the same read:
- `OK` when an SMS or RCS message is newer than 7 days, or when the whole database is stale (that is already the iMessage line's PARTIAL);
- `PARTIAL` "no SMS or RCS since <date> while iMessage arrives: is Text Message Forwarding on for this Mac?" when iMessage has a message in the last 7 days and SMS and RCS have none;
- `FAILED` when the pass failed.

**Stale:** no message newer than 48 hours makes the pass `PARTIAL` with "Messages on the Mac has not synced since <time> (is it signed in?)". This would have caught the Oct 5 sign-out within two days.

### 6. Changes to existing code

- `core/db/schema.sql`: the table above.
- `core/reports/brief.py`: `WATCHED_JOBS["IMESSAGE_CHECKED"] = ("imessage", 3)`, `JOB_FAILURES["IMESSAGE_FAILED"] = "IMESSAGE_CHECKED"`. The stalled-job alert (D-077) then covers it.
- `ops/remote.py`: `/lms imessage` runs one pass now and replies with its summary, like `/lms whatsapp`; added to `HELP` and `NO_ARGS`.
- `config/sources.yaml`: `messages:imessage` and `messages:sms` get the method, permissions and gaps below; they stay `blocked` until installed, then `active`. `tests/test_coverage.py` changes in the same commit.
- `tests/test_hard_stops.py`: the new modules are scanned like the rest; no new exemption is expected.

### 7. The job — `ops/imessage.py`, `com.lms.imessage` (hourly)

The same as `ops/whatsapp.py`: halt flag first, archive volume check, then the engine's `run_pass` with iMessage's settings, then the SMS line. Flags `--probe` (prints `probe()`, writes nothing) and `--dry-run` (unfiled chat days of the last 3 days, writes nothing). launchd every hour (`StartInterval 3600`), logs `~/LMS/logs/imessage.log` and `.err`. A halt stops it with the other `com.lms.*` jobs (D-059); nothing new is needed for that.

**Backfill** — `ops/backfill_imessage.py --days 90 [--batch 20]`: as WhatsApp's: closes every day before yesterday in batches, re-checks the halt flag between batches, marks backfilled messages scanned, and alerts only through `notify`'s 2-day freshness rule, so 90 days of history does not flood his phone. The backfill is not a check of the source: nothing goes to `source_checks`.

### 8. Results and failures

| Situation | `source_checks` | Action row | Brief / Telegram |
|---|---|---|---|
| Pass completed | `OK` | `IMESSAGE_CHECKED` | — |
| Voice not transcribed, files not on the Mac, a day failed, or nothing new for 48 h | `PARTIAL` with the counts or the reason | `IMESSAGE_CHECKED` | Coverage section |
| Database missing, unreadable, or schema changed | `FAILED` | `IMESSAGE_FAILED` | Pushed once a day (D-077) |
| Job not running | — | — | "imessage: last ran …" after 3 h, pushed |

A failed chat day is logged `IMESSAGE_DAY_FAILED`, left unfiled for the next pass, and does not stop the other chats (as WhatsApp, final review Nov 6). The model down means retry next hour, not "not urgent".

### 9. Safety

- **Read-only by construction:** only copies of `chat.db` and the Contacts databases are opened; nothing calls Messages; nothing is sent (`never_send_without_approval` stands).
- **Untrusted text** reaches only the classifier, which has zero tools (D-009 control 1). Attachment file names come from the sender: they are sanitised as WhatsApp's are (`_media_name`), and message ids are hashed for temp paths (`_slot`).
- **No sender authentication** for texts, as for WhatsApp: the wording rules for phishing apply, the mail-authentication rules do not.
- **Gaps disclosed in the register:** only what this Mac's Messages holds; nothing while it is signed out; SMS and RCS only while Text Message Forwarding is on; videos not filed; attachments not on the Mac are listed; a message deleted after filing stays filed; the hourly job reads the last 3 days, older late arrivals need a backfill.

## Setup on the Mac — `lms/ops/IMESSAGE_APPLY.md`

1. **Matthew signs Messages in on the Mac** with the Apple Account his iPhone uses for iMessage (iPhone: Settings → Apps → Messages → Send & Receive; expected `mattyeps@gmail.com`). Then Messages → Settings → iMessage → **Enable Messages in iCloud**, which should also bring back Oct 5 to now. Do not sign Messages in or out for him (D-068).
2. On his iPhone: Settings → Apps → Messages → **Text Message Forwarding** → turn on the Mac Studio (SMS and RCS).
3. `git pull`, then `./ops/imessage.py --probe`. The newest iMessage date must be today or yesterday; stop otherwise.
4. Full Disk Access: already granted to `python3.12` and `Python.app` for WhatsApp; the probe's ability to read `chat.db` from the job is checked at step 6.
5. `./ops/imessage.py --dry-run`, then `./ops/backfill_imessage.py --days 90`.
6. Install `com.lms.imessage`, then `launchctl kickstart`, then `tail -3 ~/LMS/logs/imessage.log`.
7. The developer sets `messages:imessage` (and `messages:sms` once SMS arrives) to `active`.

## Tests

On Windows, with a fixture `chat.db` built with the tables above, a fixture Contacts database, and fake speech:

- **Refactor:** `tests/test_whatsapp.py` and `tests/test_whatsapp_adapter.py` pass with no edits.
- **Engine with two sources:** each source writes only its own table, action rows, alert keys and `source_ref` prefix; a WhatsApp and an iMessage chat with the same id do not collide.
- `decode_attributed_body`: real-shaped blobs with one-byte, `0x81` and `0x82` lengths, non-ASCII text, and garbage (returns `None`, never raises).
- Nanosecond and old seconds dates; days split in Eastern time across DST.
- Reactions and system rows skipped; a message with several attachments; image, document, video, voice and unknown kinds; a missing file listed and counted.
- Chats keyed on `chat_identifier`; group naming with and without `display_name`; senders named from Contacts by phone (last 10 digits) and email (case-folded); no Contacts falls back to numbers.
- The SMS line: OK, PARTIAL when only iMessage is recent, OK when the whole database is stale; the 48-hour stale rule.
- `/lms imessage` runs one pass; it is in `HELP`.
- The brief's `WATCHED_JOBS` and `JOB_FAILURES` strings are ones this job writes (the D-027 trap).
- The halt flag stops the pass and the backfill between batches; the backfill queues no alerts for days older than 2 days.
- **On the Mac only:** the probe against the real `chat.db` and Contacts, and one real voice message.

## Out of scope

- Sending or replying.
- Reading Messages while it is signed out, or another Apple Account's messages.
- Filing videos.
- Reading an iPhone backup.
- Message effects, stickers, Memoji and app messages beyond a listed marker.

## Effort

About 4–5 days: the engine refactor and its proof, the reader with the text decoder and Contacts, the job, backfill and setup.
