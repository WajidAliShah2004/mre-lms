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

(Done Oct 6. Now `./ops/schema_dump.py /tmp/oc-schema.json channels.telegram bindings commands secrets.providers`, because the heredoc above does not survive RustDesk.) From it, `ops/phase7b.patch.json5` was written to:

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

### Schema read Oct 6 (OpenClaw 2026.7.1-2) — what it settled

- The key is `botToken`, which takes an exec SecretRef `{source, provider, id}`.
- `dmPolicy` defaults to **`pairing`**; `groupPolicy` is **required**.
- `bindings[]` = `{type: "route", agentId, match: {channel, peer: {kind, id}}}`.
- `commands.restart` defaults to **true**; bash/config/mcp/plugins/debug default false.

All of it is in `ops/phase7b.patch.json5`, and `tests/test_phase7b.py` asserts
the security properties on the parsed patch.

### 2. Create the second bot and store its token

BotFather → `/newbot` → a name and a `…bot` username → `/setjoingroups` →
**Disable**. Copy BotFather's message on the Mac, click into Terminal and run
this **as one line** (over RustDesk the clipboard can change between two
commands; D-059):

```bash
T=$(pbpaste | grep -Eo '[0-9]{8,12}:[A-Za-z0-9_-]{30,}' | head -1); if [ -n "$T" ]; then security add-generic-password -U -a lms -s lms/telegram-bot-token -w "$T" && echo STORED; else echo "NO TOKEN ON CLIPBOARD"; fi; unset T
pbcopy < /dev/null
security find-generic-password -a lms -s lms/telegram-bot-token -w | wc -c
```

About `47`. From Matthew's Telegram: open the new bot, **Start**, `hello`. Then
(before the gateway is polling it):

```bash
./ops/capture_telegram_id.py --bot chat
```

It must show **8783061626** and nobody else. If it shows a different id, stop:
the patch hard-codes that one.

### 3. Apply

```bash
openclaw config get secrets.providers.default
openclaw config patch --file ops/phase7b.patch.json5 --dry-run
```

The provider in the patch mirrors `default` with only the Keychain service
changed; compare the two. Then:

```bash
openclaw config patch --file ops/phase7b.patch.json5
openclaw plugins enable telegram
openclaw gateway restart
openclaw config get channels.telegram
```

That last output must show `botToken` as a `{source: "exec", …}` reference.
**If a token is readable there, stop and roll back**: it is in openclaw.json in
plaintext.

### 4. Verify

From Matthew's Telegram, send the new bot `Reply with exactly READY`. It should
answer. Then:

```bash
./ops/agent_tools.py
./ops/verify_setup.py 2>&1 | tail -25
openclaw doctor --allow-exec 2>&1 | grep -i -A3 "owner"
openclaw security audit --deep 2>&1 | tail -15
./ops/halt.py --status
```

- `agent_tools.py`: all three `lms-*` PASS, tools=none.
- `verify_setup`: [5] now expects 4 enabled plugins; all pass.
- doctor: no "No command owner is configured".
- audit: 0 critical; the `operator.read` warning gone.
- `halt.py --status`: the gateway is still a halt target. A `/halt` to
  **@MREOC18bot** stops this chat too, and that is correct.

Also try it from a **different** Telegram account if one is at hand: the bot
must not answer, and must not offer pairing.

### 5. Retire the onboarding script

A new agent workspace carries `BOOTSTRAP.md`, OpenClaw's "what should I call
you?" first-run script, which the agent is expected to delete itself. A
tool-less agent never can. Move it aside (done Oct 6), then `/new` in the chat:

```bash
mkdir -p ~/LMS/openclaw-bootstrap-retired
for a in orchestrator classifier drafter; do f=~/.openclaw/workspace-lms-$a/BOOTSTRAP.md; [ -f "$f" ] && mv "$f" ~/LMS/openclaw-bootstrap-retired/lms-$a-BOOTSTRAP.md; done
```

### Rollback (7b)

```bash
cp ~/.openclaw/openclaw.json.pre-phase7 ~/.openclaw/openclaw.json
openclaw plugins disable telegram
openclaw gateway restart
```

**Not** `.pre-phase6`: that would undo Phase 6. Revert `D015_PLUGINS` to 3 if
7b is abandoned.

---

## What Phase 7 does NOT do

- **No approvals yet.** Approve/reject buttons are Day 5, through 7b.
- **No delayed-send window to cancel.** Nothing sends yet. When something
  does, `/halt` must cancel its queue: that is a requirement on that feature,
  recorded in D-059.
- **No Tailscale exposure.** Both bots reach Telegram outbound.
