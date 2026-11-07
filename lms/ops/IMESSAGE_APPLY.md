# Installing the iMessage reader on the Mac (R2, D-081)

Installs `com.lms.imessage`. Every hour it reads a copy of Messages' database
(`~/Library/Messages/chat.db`) and of Contacts, checks today's and yesterday's
new texts for anything urgent, and files each chat's day as one transcript
once the day is over. It never writes to Messages and never sends anything.
iMessage, SMS and RCS all come from the same database.

**Before you start:** Messages on the Mac has been signed out since Oct 5
(D-068). Until it is signed in, the reader would file only old history. Steps
1 and 2 are Matthew's own; do not sign Messages in or out for him.

## 1. Matthew signs Messages in on the Mac

1. On his iPhone: **Settings → Apps → Messages → Send & Receive**. Note the
   Apple Account shown at the bottom (expected `mattyeps@gmail.com`).
2. On the Mac: open **Messages** and sign in with that same account.
3. **Messages → Settings → iMessage → Enable Messages in iCloud.** This should
   also bring back Oct 5 to now. Leave the Mac on and Messages open until the
   newest conversations appear.

## 2. Matthew turns on Text Message Forwarding

On his iPhone: **Settings → Apps → Messages → Text Message Forwarding** →
turn on **Mac Studio**. Without it, SMS and RCS texts never reach the Mac, and
the register's SMS line says so.

## 3. Pull and probe

```bash
cd ~/lms-repo && git pull && cd lms
```

```bash
./ops/imessage.py --probe
```

The probe prints counts and dates only, never a message, number or name.
Check:
- no `MISSING:` line;
- `newest:` shows iMessage today or yesterday. If not, Messages is still not
  syncing: stop and go back to step 1;
- `text only in attributedBody: N, decoded M`: M close to N;
- `contacts read:` more than 0. If it is 0, names fall back to numbers; tell
  the developer.

## 4. Full Disk Access

Nothing new to grant: the job runs the same Homebrew `python3.12` and
`Python.app` that were given Full Disk Access for WhatsApp
(`WHATSAPP_APPLY.md` step 3). Step 6 confirms the job can read `chat.db`.

## 5. Dry run, then the 90-day history

```bash
./ops/imessage.py --dry-run
```

It lists unfiled chat days and writes nothing. Then:

```bash
./ops/backfill_imessage.py --days 90
```

It files every chat day before yesterday in batches of 20 and prints a line
per batch. Old days raise no alerts. A halt stops it between batches.

## 6. Install the hourly job

```bash
cp ops/com.lms.imessage.plist ~/Library/LaunchAgents/
```

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.lms.imessage.plist
```

```bash
launchctl kickstart -k gui/$(id -u)/com.lms.imessage
```

```bash
tail -3 ~/LMS/logs/imessage.log
```

The last line is a summary such as `41 message(s) seen, 3 chat day(s)
filed, 0 alert(s)`. A last line of `~/LMS/logs/imessage.log` starting
`PermissionError:` means Full Disk Access is not effective for the job: redo `WHATSAPP_APPLY.md`
step 3 (a Homebrew Python update needs it again), then kickstart again.

## 7. Mark it active

Tell the developer. They set `messages:imessage` to `active` in
`config/sources.yaml`, and `messages:sms` too once a text has arrived.

## Troubleshooting

- `Messages on the Mac has not synced since … (is it signed in?)` in the log
  or the brief: Messages is signed out or closed. Step 1.
- The brief's coverage section says `no SMS or RCS since … while iMessage
  arrives`: Text Message Forwarding is off. Step 2.
- `FAILED … schema changed, missing: …`: a macOS update changed Messages'
  database. The pass fails loudly rather than reading nothing; run
  `--probe` and send the developer its output.
- `/lms imessage` in WhatsApp or Telegram runs one pass now.

## Removing it

```bash
launchctl bootout gui/$(id -u)/com.lms.imessage
```

```bash
rm ~/Library/LaunchAgents/com.lms.imessage.plist
```

What was filed stays filed.
