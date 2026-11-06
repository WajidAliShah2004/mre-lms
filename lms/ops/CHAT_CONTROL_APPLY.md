# Chat control: apply on the Mac (D-080)

Matthew controls the LMS from WhatsApp ("Message yourself") and Telegram
(@MREOCbot) with `/lms` commands. Code carries out every command
(`ops/remote.py`); the AI only answers plain questions, read-only.
@MREOC18bot (the kill switch) is not touched.

Run from `~/lms-repo/lms`. No `#` comments after commands: zsh runs them.

Applied and tested on the Mac Nov 6 2026: every check in step 6 and the halt test in step 7 passed. `/lms` commands are chat messages; typed in the Terminal, zsh only says `no such file or directory: /lms`.

## 1. Pull and test

```bash
cd ~/lms-repo/lms && git pull
```

```bash
.venv/bin/python -m pytest -q tests/test_remote.py tests/test_agent_context.py tests/test_remote_patch.py
```

`halt.py` changed, and its tests are skipped on Windows, so run them here too:

```bash
.venv/bin/python -m pytest -q tests/test_halt.py
```

## 2. Back up the OpenClaw config

```bash
cp ~/.openclaw/openclaw.json ~/.openclaw/openclaw.json.pre-chatcontrol
```

## 3. Install the WhatsApp plugin, then link WhatsApp to OpenClaw

WhatsApp is not part of OpenClaw 2026.7.1-2: it is a separate plugin, `@openclaw/whatsapp`. Its newest version (2026.11.1) needs OpenClaw 2026.11.1 or later, so install the version built for this OpenClaw, 2026.7.1 (done on the Mac Nov 6: installed and loaded). Without it, the QR login would install the newest version itself. Skip the install if `openclaw plugins inspect whatsapp` already shows `Version: 2026.7.1`.

```bash
openclaw plugins install @openclaw/whatsapp@2026.7.1
```

```bash
openclaw plugins inspect whatsapp
```

It must show `Version: 2026.7.1`. Do not run `openclaw plugins update` on it while OpenClaw is 2026.7.x.

Until step 4 writes the allowlist, WhatsApp must answer no one: by default it replies to unknown senders with a pairing code, and this is his own number. Switch its messages off before linking:

```bash
openclaw config set channels.whatsapp.dmPolicy disabled
```

```bash
openclaw config set channels.whatsapp.groupPolicy disabled
```

```bash
openclaw gateway restart
```

```bash
openclaw config get channels.whatsapp --json
```

It must show `"dmPolicy": "disabled"` and `"groupPolicy": "disabled"`. If not, stop here.

Matthew's phone is needed for one QR scan: WhatsApp → Settings → Linked Devices → Link a Device.

```bash
openclaw channels login --channel whatsapp
```

Then:

```bash
openclaw channels status
```

It shows the linked number. Copy it (Cmd-C; a `+` or spaces are fine), then store it. This keeps only the digits of what you copied, and the number does not land in shell history:

```bash
security add-generic-password -U -a lms -s lms/whatsapp-owner -w "$(pbpaste | tr -cd '0-9')"
```

Check it: this prints how many digits were stored (with the country code; 11 for a US number):

```bash
security find-generic-password -a lms -s lms/whatsapp-owner -w | tr -d '\n' | wc -c
```

(Do not use the hidden prompt, `-w` with no value: on the Mac on Nov 6 it asked twice, refused, and once stored a wrong 5-character value.)

## 4. Patch the config, with his number filled in

```bash
N=$(security find-generic-password -a lms -s lms/whatsapp-owner -w)
```

Write the patch with his number filled in. The file is written only when the number is all digits; otherwise the line prints a message, writes nothing, and the steps below fail harmlessly:

```bash
rm -f /tmp/remote.patch.json5; case "$N" in ''|*[^0-9]*) echo "lms/whatsapp-owner must be digits only: store it again (step 3) and stop here";; *) sed "s/__WHATSAPP_OWNER__/$N/g" ops/remote.patch.json5 > /tmp/remote.patch.json5;; esac; unset N
```

If it printed that message, stop and go back to step 3.

```bash
openclaw config patch --file /tmp/remote.patch.json5 --dry-run
```

Read the dry-run output: his number must appear with a leading `+` (for example `+15551234567`), once in `allowFrom`, in the WhatsApp binding and in the two command lists. If it does not, run the `rm -f` block below and stop.

```bash
openclaw config patch --file /tmp/remote.patch.json5
```

Remove the temporary file whether the patch worked or failed:

```bash
rm -f /tmp/remote.patch.json5
```

```bash
openclaw gateway restart
```

Confirm the plugin loaded (a wrong entry file shows here, not as silence on the phone):

```bash
openclaw plugins inspect lms-control
```

```bash
openclaw plugins doctor
```

`inspect` must show it loaded and enabled; `doctor` must report no load issue for `lms-control`. OpenClaw 2026.7.1-2 does not list a plugin's commands in `inspect`: the phone test in step 6 is what shows `/lms` works.

The patch enables two plugins, `whatsapp` and `lms-control`, so `verify_setup` check [5] now expects 6 enabled plugins (it was 4).

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

Check [5] must report 6 enabled plugins. `[12] Chat control` must pass (it fails, not warns, if OpenClaw's WhatsApp config is unreadable or not an object, if the plugin is not enabled, or if `com.lms.agentcontext` is not loaded), and `agent_tools.py` must still show no tools for the lms-* agents.

Each message must get exactly one reply. OpenClaw does not take its own replies in "Message yourself" as new messages; if a reply is ever answered again, run `/lms halt` and report it. (The `[openclaw]` reply prefix the OpenClaw docs describe does not appear with WhatsApp plugin 2026.7.1.)

On Matthew's phone, in WhatsApp's "Message yourself" chat and in @MREOCbot:

1. `/lms help`: the list.
2. `/lms status`: "Running (not halted)" and the jobs.
3. `/lms tasks`: open tasks.
4. `/lms done <a real number>`: shows the task and asks for `yes`. Do **not** send `yes` unless he means it.
5. A plain question, "what's overdue?": an answer that names the snapshot time.
6. From **another phone**, a WhatsApp to his number saying `/lms status`: **no reply**, and `tail -3 ~/LMS/logs/remote.log` shows nothing new or `remote:refused`. OpenClaw's log shows the message dropped.

Leave `/lms halt` for the planned test below: it stops everything.

## 7. Planned halt test

While halted nothing answers on WhatsApp or @MREOCbot and the snapshot job writes nothing. Resume is only at the Mac (no remote resume, D-059).

1. From the phone, send `/lms halt` in WhatsApp's "Message yourself" chat or in @MREOCbot. Expect the "Halting in 2 seconds" reply, then those chats go quiet.
2. From the phone, send `/status` to @MREOC18bot. Expect it to report HALTED (it is the independent kill switch and keeps answering).
3. At the Mac, show the state (halted, by whatsapp or telegram):

```bash
./ops/halt.py --status
```

4. At the Mac, resume:

```bash
./ops/halt.py --resume
```

5. At the Mac, confirm it is running again:

```bash
./ops/halt.py --status
```

```bash
./ops/verify_setup.py | tail -15
```

## Undo

```bash
cp ~/.openclaw/openclaw.json.pre-chatcontrol ~/.openclaw/openclaw.json && openclaw gateway restart
```

That puts the config back. The WhatsApp plugin stays installed but, with the old config, is not used. If you instead switch chat control off in place, disable both plugin entries:

```bash
openclaw plugins disable whatsapp
```

```bash
openclaw plugins disable lms-control
```

```bash
openclaw gateway restart
```

If chat control is removed for good, set `D015_PLUGINS` in `ops/verify_setup.py` back to 4, or check [5] will fail.

```bash
launchctl bootout gui/$(id -u)/com.lms.agentcontext
```
