# Telegram alerts — apply on the Mac (R10, D-077)

Installs `com.lms.notify`: every 15 minutes it sends Matthew one Telegram
message from **@MREOCbot** (the chat bot, not the kill switch) listing what
arrived that should not wait for the brief:

- **URGENT**: mail classified CRITICAL (a lapse notice, a final notice).
- **Legal/tax**: LEGAL or TAX mail at HIGH (a summons, a tax notice).
- **Phishing blocked**: a quarantined fraud attempt, so he does not act on
  the real-looking copy elsewhere.
- **System**: a job in the brief's "THE SYSTEM NEEDS ATTENTION" list.

At most 3 messages a day, 1 an hour, none 22:00–07:00 ET. URGENT ignores all
three. Only mail received in the last 2 days alerts, so a backfill does not
flood his phone. Nothing is ever dropped: what the budget holds back goes in
the next message.

Ten minutes. No new permission, no new secret: it uses the chat bot token
stored in 7b and the owner id the kill switch uses. **The test message in
step 2 arrives on Matthew's phone**; he does not need to do anything with it.

Type commands one line at a time over RustDesk. Expected output is written
under each block, never after a `#` on the command line: zsh on this Mac does
not treat `#` as a comment, so `wc -c   # 47` hands `#` and `47` to `wc` as
file names (Nov 4).

## 1. Pull, and check the two Keychain items exist

```bash
cd ~/lms-repo/lms && git pull
security find-generic-password -a lms -s lms/telegram-bot-token -w | wc -c
security find-generic-password -a lms -s lms/telegram-owner-id -w | wc -c
```

About `47`, then `11`. Neither value is printed, only its length. If the
first is `0` or an error, stop: 7b's token is missing (ops/PHASE7_APPLY.md, 7b step 2).

## 2. One test message, then a dry run

```bash
./ops/notify.py --test
./ops/notify.py --dry-run
```

`--test` prints `OK    test message sent`. `--dry-run` prints what would go
out now and sends nothing.

`--test` failing with `HTTP 401` or `404` means the stored chat bot token is
wrong: re-store it with the one-line clipboard command in PHASE7_APPLY.md 7b
step 2, then `launchctl kickstart -k gui/$(id -u)/ai.openclaw.gateway` so the
gateway reads it too. `HTTP 403 ... bot can't initiate conversation` means
Matthew has never pressed Start on @MREOCbot; he did on Oct 6, so this should
not happen.

The dry run will usually say `0 pending; not sent: nothing pending`. The
alert queue starts empty: mail filed before today never alerts.

## 3. Install

```bash
cp ops/com.lms.notify.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.lms.notify.plist
launchctl list | grep com.lms.notify
tail -3 ~/LMS/logs/notify.log
```

`launchctl list` shows a pid or `-`, and status `0`. The log line is
`0 pending; not sent: nothing pending` on a quiet run.

## 4. Check it is armed

```bash
./ops/verify_setup.py | tail -5
./ops/brief.py | grep -A3 "NEEDS ATTENTION"
```

Within the hour, actions_log has a `NOTIFY_CHECKED` row. From then on, if the
job stops, the brief says `alerts: last ran …`; if a send fails, it says
`alerts: FAILED … ` with Telegram's reason.

## Undo

```bash
launchctl bootout gui/$(id -u)/com.lms.notify
rm ~/Library/LaunchAgents/com.lms.notify.plist
```

Pending alerts stay in the `notifications` table and nothing else changes.
A halt (`/halt` or `./ops/halt.py`) stops this job with every other one.
