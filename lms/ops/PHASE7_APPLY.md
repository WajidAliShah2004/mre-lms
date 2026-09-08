# Phase 7 — Telegram control channel

Telegram is the only remote kill switch and the only approval surface (D-005).
The menu-bar app in the original specification was descoped, so if this channel
is wrong there is no other way to stop the system from outside the building.

**Closes C2**, which simultaneously clears two findings that are really one:
`openclaw doctor`'s "No command owner is configured" and `security audit
--deep`'s `gateway.probe_failed / missing scope: operator.read`.

---

## The thing that will bite you

`secrets.providers.default` — the exec provider D-016 created — has the
Keychain **service name baked into its argument list**:

```
/usr/bin/security find-generic-password -a lms -s lms/gateway-auth-token -w
```

Six literal args. There is no substitution of the SecretRef's `id`. So this:

```json
{ "source": "exec", "provider": "default", "id": "telegram-bot-token" }
```

would run that exact command and hand Telegram **the gateway's token**.

It would not error. The bot would authenticate against nothing and silently
never respond, and every obvious explanation — wrong token, bad pairing,
network — would be wrong. Telegram gets its **own provider**.

---

## 1. Create the bot

On Telegram, message **@BotFather** → `/newbot` → name it → copy the token.

**Who should own it.** BotFather ties the bot to whichever account creates it.
Creating it from Matthew's account means it survives handover cleanly and he
can revoke it himself. Creating it from the developer's account is faster
today and becomes another D-020-shaped problem later. Prefer Matthew's; if you
create it, record it as a transfer item.

**Turn on 2FA on the Telegram account itself** (Settings → Privacy and Security
→ Two-Step Verification). This is the remaining half of C2. Whoever holds that
account can stop this system, and — once approvals are wired — approve outgoing
mail as Matthew.

## 2. Token into the Keychain

```bash
security add-generic-password -a lms -s lms/telegram-bot-token -w
```

**No value after `-w`.** It prompts. The token never reaches the command line,
the process table, or your shell history — the exact exposure D-016 recorded
when the gateway token was printed to a terminal and had to be rotated.

Verify without printing it:

```bash
security find-generic-password -a lms -s lms/telegram-bot-token -w >/dev/null && echo OK
security dump-keychain 2>/dev/null | grep -o 'lms/[a-z-]*' | sort -u
```

You should now see two items: `lms/gateway-auth-token` and
`lms/telegram-bot-token`.

## 3. Its own secrets provider

```bash
cat > /tmp/phase7.patch.json5 <<'ENDOFSCRIPT'
{
  secrets: {
    providers: {
      // Separate from `default`, which is hardcoded to the gateway token.
      telegram: {
        source: "exec",
        command: "/usr/bin/security",
        args: [
          "find-generic-password",
          "-a", "lms",
          "-s", "lms/telegram-bot-token",
          "-w",
        ],
        jsonOnly: false,          // security returns a raw string
        trustedDirs: ["/usr/bin"],
        allowInsecurePath: true,  // see below — deliberate
      },
    },
  },
}
ENDOFSCRIPT
openclaw config patch --file /tmp/phase7.patch.json5 --dry-run
```

**`allowInsecurePath: true` is deliberate, and D-016 already argued this.**
OpenClaw wants the exec command owned by the current user; `/usr/bin/security`
is owned by root, so the strict check rejects it. The alternative is a
user-owned wrapper script — which would be *worse*, because a script under
`~/` is writable by anything running as `mleca`, which is exactly the injected
agent this build defends against. Combined with `trustedDirs: ["/usr/bin"]`,
the provider can only execute a root-owned, Apple-signed binary from a pinned
directory. **Do not "fix" this by pointing it at a wrapper script.**

Apply once the dry run is clean:

```bash
openclaw config patch --file /tmp/phase7.patch.json5
```

## 4. Enable the plugin

D-015 disabled it deliberately, to be re-enabled here and nowhere else.

```bash
openclaw plugins enable telegram
openclaw plugins list | grep -i telegram
```

The D-015 baseline moves from **3 enabled to 4**. That is expected, and
`ops/verify_setup.py` must be updated to match — otherwise check 5 fails and
the next person reads it as drift. Change the baseline in the same commit that
enables the plugin, never afterwards.

## 5. Point the channel at the token

```bash
openclaw config set channels.telegram.token \
  --ref-provider telegram --ref-source exec --ref-id telegram-bot-token
openclaw gateway restart
```

Confirm the config holds a **reference**, not a token:

```bash
openclaw config get channels.telegram
```

If you can read a token in that output, stop — it is in the config file in
plaintext, which is the D-016 finding all over again.

## 6. Pair, and capture the numeric id

Ask Matthew to send **any** message to the bot.

```bash
openclaw gateway stop          # getUpdates is destructive per offset;
                               # a running gateway consumes the update first
cd ~/lms-repo/lms
./ops/capture_telegram_id.py
openclaw gateway restart
```

It prints `telegram:<numeric id>`. Then:

```bash
openclaw config set commands.ownerAllowFrom '["telegram:<ID>"]'
openclaw gateway restart
```

**Numeric id, never the @handle.** The Bot API only reports the numeric id,
because handles can be changed or unset and are therefore not identity. A
handle in that field matches nothing, and `/halt` ends up with no authorised
sender — which you would not discover until you needed it.

## 7. Verify

```bash
openclaw doctor --allow-exec 2>&1 | grep -A4 -i "command owner"   # should be silent
openclaw security audit --deep 2>&1 | tail -20                    # operator.read gone
cd ~/lms-repo/lms && ./ops/verify_setup.py
```

Then measure the kill switch:

```bash
./ops/verify_halt.sh
```

That measures the **local** stop, which is the floor. The real test is `/halt`
from the phone, and it belongs in front of Matthew at acceptance — partly to
prove it works, mostly so he has done it once himself and knows what it feels
like.

**Record the measured number in DECISIONS.md.** A ≤10s requirement with no
measured value attached is an aspiration.

## 8. RUNBOOK

`/halt` is already page 1 of `RUNBOOK.md`. Once the bot exists, add its name
there so the person reaching for it at 2am does not have to remember which
chat.

---

## Rollback

```bash
cp ~/.openclaw/openclaw.json.pre-phase6 ~/.openclaw/openclaw.json
openclaw plugins disable telegram
openclaw gateway restart
```

The Keychain item can stay — an unused secret costs nothing, and rotating it is
a separate decision from rolling back the config.

## What Phase 7 does NOT do

- **No approval flows yet.** Draft approve/reject buttons are Day 5. Right now
  the channel carries `/halt` and notifications only.
- **No mailbox.** Day 3, and not before the D-017 rotation.
- **No Tailscale exposure.** `gateway.tailscale.mode` stays `off`; Telegram
  reaches the bot outbound. Phase 8 handles remote access, and the ordering
  hazard there still stands — verify Screen Sharing over Tailscale from an
  outside network *before* default-deny egress goes on.
