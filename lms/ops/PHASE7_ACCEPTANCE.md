# Phase 7 acceptance — at the Mac, after Matthew's /halt

Matthew reported sending `/halt` to @MREOC18bot from his phone on Oct 19
(D-065). If it worked, **everything except the haltbot is stopped** — mail, the
watcher, the brief, the backup, the gateway — and stays stopped until resumed
here. This closes Phase 7's two acceptance items: /halt from his own phone, and
the token rotation (R30).

Order matters: verify first (resume overwrites the evidence), rotate while
halted, resume last.

```bash
unset HISTFILE
cd ~/lms-repo/lms
```

## 1. Was it him, and how fast

```bash
./ops/halt.py --status                                 # HALTED since <ts> by telegram
grep SYSTEM_HALTED ~/LMS/logs/haltbot.log | tail -1    # elapsed=…s telegram_lag=…s
grep IGNORED ~/LMS/logs/haltbot.log | tail -3          # anything after Oct 6?
```

- **`by telegram`** is the proof it was his account: the bot obeys only the
  owner id in the Keychain, in a private chat, and ignores everyone else.
- **`elapsed`** must be ≤ 10 s (spec §8.9, R38). `telegram_lag` is delivery time
  on top; over 30 s, his reply said so.
- **Not halted, and an `IGNORED sender=…` line from today:** he sent it from a
  different Telegram account. Stop. Do not change the owner id on the
  strength of that; ask him which account he used.
- **Not halted, no IGNORED line:** the message never arrived. Check
  `haltbot.err` and `POLL_FAILED` lines before anything else.

Record `ts`, `elapsed` and `telegram_lag` in DECISIONS.md under D-065.

## 2. Rotate the @MREOC18bot token (R30)

The current token was shared as a screenshot on Oct 6. In Telegram,
**@BotFather** → `/revoke` → `@MREOC18bot`. Do not screenshot the new token or
paste it anywhere but the prompt below.

```bash
security add-generic-password -U -a lms -s lms/telegram-halt-token -w
security find-generic-password -a lms -s lms/telegram-halt-token -w | wc -c   # > 40
launchctl kickstart -k gui/$(id -u)/com.lms.haltbot
./ops/halt_bot.py --check                     # OK  @MREOC18bot reachable; owner id set
tail -2 ~/LMS/logs/haltbot.log                # "haltbot up; owner configured"
```

`-U` updates the existing item (without it, `add` fails because the item
exists). No value after `-w`: it prompts, so the token never reaches shell
history (D-016). The bot reads the token at start, hence the `kickstart -k`.

While in BotFather: `/setprivacy` → **Enable** for @MREOC18bot, if not
already done.

## 3. Resume

```bash
./ops/halt.py --resume
./ops/halt.py --status                        # Running (not halted); every job loaded
./ops/verify_setup.py                         # [11] Kill switch armed
```

Resume brings back exactly what the halt stopped. Scheduled jobs (mail, brief,
backup) fire at their next slot; the watcher and the gateway start at once.

## 4. Weekly audit job (R32)

```bash
launchctl list | grep com.lms.audit           # present?
```

Missing: install it per `PHASE8_APPLY.md` Step 4.

## 5. One message from Matthew

Ask him to send **`/status`** (not `/halt`) to @MREOC18bot. The reply
"Running (not halted)" proves the new token receives and replies from his
phone. Then Phase 7 is accepted: mark it in PHASES.md and close R30.
