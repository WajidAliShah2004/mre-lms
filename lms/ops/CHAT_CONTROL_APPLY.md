# Chat control: apply on the Mac (D-080)

Matthew controls the LMS from WhatsApp ("Message yourself") and Telegram
(@MREOCbot) with `/lms` commands. Code carries out every command
(`ops/remote.py`); the AI only answers plain questions, read-only.
@MREOC18bot (the kill switch) is not touched.

Run from `~/lms-repo/lms`. No `#` comments after commands: zsh runs them.

## 1. Pull and test

```bash
cd ~/lms-repo/lms && git pull
```

```bash
.venv/bin/python -m pytest -q tests/test_remote.py tests/test_agent_context.py tests/test_remote_patch.py
```

## 2. Back up the OpenClaw config

```bash
cp ~/.openclaw/openclaw.json ~/.openclaw/openclaw.json.pre-chatcontrol
```

## 3. Link WhatsApp to OpenClaw

Matthew's phone is needed for one QR scan: WhatsApp → Settings → Linked Devices → Link a Device.

```bash
openclaw channels login --channel whatsapp
```

Then:

```bash
openclaw channels status
```

It shows the linked number. Store it (digits only, with the country code, for example 15551234567):

```bash
security add-generic-password -U -a lms -s lms/whatsapp-owner -w
```

(It asks for the value twice; paste the number. Nothing lands in shell history.)

## 4. Patch the config, with his number filled in

```bash
N=$(security find-generic-password -a lms -s lms/whatsapp-owner -w); sed "s/__WHATSAPP_OWNER__/$N/g" ops/remote.patch.json5 > /tmp/remote.patch.json5; unset N
```

```bash
openclaw config patch --file /tmp/remote.patch.json5 --dry-run
```

```bash
openclaw config patch --file /tmp/remote.patch.json5 && rm /tmp/remote.patch.json5
```

```bash
openclaw gateway restart
```

Confirm the plugin loaded and registered its commands (a wrong entry file shows here, not as silence on the phone):

```bash
openclaw plugins inspect lms-control
```

```bash
openclaw plugins doctor
```

`inspect` must show it enabled with the `lms` and `halt` commands; `doctor` must report no load issue for `lms-control`.

## 5. Install the snapshot job

```bash
cp ops/com.lms.agentcontext.plist ~/Library/LaunchAgents/
```

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.lms.agentcontext.plist
```

```bash
./ops/agent_context.py --print | head -20
```

## 6. Check

```bash
./ops/verify_setup.py | tail -15
```

```bash
./ops/agent_tools.py
```

`[12] Chat control` must pass (it fails, not warns, if OpenClaw's WhatsApp config is unreadable or not an object, if the plugin is not enabled, or if `com.lms.agentcontext` is not loaded), and `agent_tools.py` must still show no tools for the lms-* agents.

On Matthew's phone, in WhatsApp's "Message yourself" chat and in @MREOCbot:

1. `/lms help`: the list.
2. `/lms status`: "Running (not halted)" and the jobs.
3. `/lms tasks`: open tasks.
4. `/lms done <a real number>`: shows the task and asks for `yes`. Do **not** send `yes` unless he means it.
5. A plain question, "what's overdue?": an answer that names the snapshot time.
6. From **another phone**, a WhatsApp to his number saying `/lms status`: **no reply**, and `tail -3 ~/LMS/logs/remote.log` shows nothing new or `remote:refused`. OpenClaw's log shows the message dropped.

Leave `/lms halt` for a planned test: it stops everything, and resume is `./ops/halt.py --resume` at the Mac.

## Undo

```bash
cp ~/.openclaw/openclaw.json.pre-chatcontrol ~/.openclaw/openclaw.json && openclaw gateway restart
```

```bash
launchctl bootout gui/$(id -u)/com.lms.agentcontext
```
