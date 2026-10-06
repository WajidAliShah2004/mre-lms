# Phase 7 — Telegram control channel

Rewritten Oct 6 (D-059). The Sept 8 version assumed OpenClaw has a `/halt`
command. It does not: its commands include `/stop` (abort the current reply)
and `/restart`, and nothing that stops the gateway. And stopping the gateway
would not have stopped the LMS — mail, the watcher, the brief and the backup are
launchd jobs that never go through it (D-056).

So Phase 7 is two parts that do not depend on each other:

| Part | What | Bot | Needs |
|---|---|---|---|
| **7a** | The kill switch: `com.lms.haltbot`, a stdlib daemon that obeys `/halt` from Matthew's numeric id and stops **every** LMS and OpenClaw job, persistently | **@MREOC18bot** | Token, Matthew sends one message |
| **7b** | Chat with the gateway (later: approvals on Day 5) | a **second** bot | `openclaw config schema` read on the Mac first |

Two bots because Telegram allows one reader per bot, and because a kill switch
that lives inside the gateway cannot act when the gateway is the thing that is
stuck.

Work from `~/lms-repo/lms` throughout.

---

## 7a — The kill switch

### 1. Revoke the exposed token, store the new one

The @MREOC18bot token was shared as a screenshot on Oct 6, so treat it as public.
In Telegram, **@BotFather** → `/revoke` → `@MREOC18bot`. It replies with a new
token. Do not screenshot it, paste it into chat, or type it on a command line:

```bash
security add-generic-password -a lms -s lms/telegram-halt-token -w
```

**No value after `-w`.** It prompts twice; paste at the prompt. The token never
reaches shell history or the process table (D-016, D-017).

Check that it is non-empty without printing it (an empty `-w` is accepted
silently; this exact thing happened with the backup key, D-049):

```bash
security find-generic-password -a lms -s lms/telegram-halt-token -w | wc -c   # > 40
```

Also in BotFather, for @MREOC18bot: `/setjoingroups` → **Disable** (done Oct 6),
and `/setprivacy` → **Enable**. The bot ignores everything outside a private chat
with Matthew anyway; this is the first line of that.

### 2. Two-step verification on Matthew's Telegram account

Settings → Privacy and Security → Two-Step Verification. Whoever holds this
account can stop the system now and, from Day 5, approve mail as Matthew. The
password should be long and unique, **not** a company name, and kept with the
FileVault recovery key. Do not record it in this repo.

### 3. Capture Matthew's numeric id

Ask Matthew to open `t.me/MREOC18bot`, tap **Start**, and send `hello`. Then:

```bash
./ops/capture_telegram_id.py            # shows who has messaged the bot
./ops/capture_telegram_id.py --store    # saves the id as the owner
```

It must show **exactly one** sender, and it must be him (check the name). If it
shows more, stop: this id decides who can stop the system. The id is stored in
the Keychain as `lms/telegram-owner-id`, not in git (same reasoning as the EINs,
D-057).

Run this **before** step 4. Once the daemon is running it consumes the messages
first.

### 4. Install the daemon

```bash
./ops/halt_bot.py --check        # OK  @MREOC18bot reachable; owner id set
cp ops/com.lms.haltbot.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.lms.haltbot.plist
launchctl list | grep haltbot    # a pid in the first column
tail ~/LMS/logs/haltbot.log      # "haltbot up; owner configured"
```

`--check` fails on a missing token, a missing or non-numeric owner id, or an
unreachable API. If egress filtering is ever on (Phase 8), `api.telegram.org`
must be on the allowlist, or the kill switch dies silently. Add it to
`config/egress-allowlist.txt` now.

### 5. Measure the local halt

```bash
./ops/verify_halt.sh
```

This runs `./ops/halt.py`, which is the same code `/halt` runs. It checks
launchd's own view: no LMS/OpenClaw job loaded, every installed one
**disabled** (so a reboot does not bring it back), the haltbot still up. Then it
resumes and shows the state. **Record the HALTED time in DECISIONS.md, D-059.**

### 6. From the phone

Matthew sends `/status`, then `/halt`. The reply states the time taken. Then, at
the Mac:

```bash
./ops/halt.py --status
./ops/halt.py --resume
./ops/verify_setup.py            # [11] Kill switch armed
```

There is **no `/resume` on the phone** (D-059). A hijacked phone can stop the
system and can never restart it.

Do the phone test with Matthew holding the phone: partly to measure it, mostly
so he has done it once himself.

### Rollback (7a)

```bash
launchctl bootout gui/$(id -u)/com.lms.haltbot
rm ~/Library/LaunchAgents/com.lms.haltbot.plist
```

If a halt is in force, `./ops/halt.py --resume` first. Nothing in OpenClaw's
config changed in 7a.

---

## 7b — Chat with the gateway

**Not to be applied from guesses.** D-021 and D-056 both came from writing
OpenClaw config from the spec and finding the keys did not exist. The Sept 8
draft used `channels.telegram.token`; the docs say `botToken`. Its default DM
policy is `pairing`, which lets *anyone* who messages the bot ask to pair. Read
the schema first, then the patch gets written from what it says.

### 1. Read the schema (read-only, changes nothing)

```bash
cp ~/.openclaw/openclaw.json ~/.openclaw/openclaw.json.pre-phase7
openclaw --version
openclaw config schema > /tmp/oc-schema.json
python3 - <<'EOF'
import json
s = json.load(open("/tmp/oc-schema.json"))
def find(node, want, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            p = f"{path}.{k}" if path else k
            if k in want:
                print(f"==== {p}\n{json.dumps(v, indent=1)[:6000]}\n")
            find(v, want, p)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            find(v, want, f"{path}[{i}]")
find(s, {"telegram", "bindings", "ownerAllowFrom", "allowFrom"})
EOF
openclaw plugins list | grep -i telegram
```

Send the whole output back. From it, `ops/phase7b.patch.json5` is written to:

- read `botToken` through its **own** exec secrets provider. The `default`
  provider has the gateway token's Keychain service hard-coded in its argument
  list; pointing a Telegram SecretRef at it would hand Telegram the gateway's
  token, silently, with no error.
- set the DM policy to an allowlist of Matthew's numeric id only, and disable
  groups.
- **bind** Telegram to an `lms-*` agent (no tools), not `main`, which still
  has `session_status` (D-056).
- set `commands.ownerAllowFrom` to `["telegram:<id>"]`, which also clears the
  `operator.read` audit warning.

### 2. Create the second bot

Same as 7a step 1: BotFather `/newbot`, `/setjoingroups` Disable, `/setprivacy`
Enable, token at the prompt into `lms/telegram-bot-token`. Matthew messages it
once; `./ops/capture_telegram_id.py --bot chat` should print the **same** id as
the halt bot.

### 3. Then (once the patch exists)

Enable the plugin and raise `D015_PLUGINS` in `ops/verify_setup.py` **in the
same commit**, dry-run, apply, restart, `./ops/agent_tools.py`, audit, doctor.
Rollback is `openclaw.json.pre-phase7`. **Not** `.pre-phase6`: that would undo
Phase 6.

---

## What Phase 7 does NOT do

- **No approvals yet.** Approve/reject buttons are Day 5, through 7b.
- **No delayed-send window to cancel.** Nothing sends yet. When something
  does, `/halt` must cancel its queue: that is a requirement on that feature,
  recorded in D-059.
- **No Tailscale exposure.** Both bots reach Telegram outbound.
