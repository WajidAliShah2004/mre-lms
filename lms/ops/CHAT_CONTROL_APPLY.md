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

`halt.py` changed, and its tests are skipped on Windows, so run them here too:

```bash
.venv/bin/python -m pytest -q tests/test_halt.py
```

## 2. Back up the OpenClaw config

```bash
cp ~/.openclaw/openclaw.json ~/.openclaw/openclaw.json.pre-chatcontrol
```

## 3. Enable the WhatsApp plugin, then link WhatsApp to OpenClaw

D-015 turned OpenClaw's channel plugins off, so WhatsApp cannot be linked until its plugin is on. The patch in step 4 also enables it, but the patch needs his number, which only exists after the link. So enable the plugin by hand first:

```bash
openclaw plugins enable whatsapp
```

```bash
openclaw gateway restart
```

Check it shows as enabled:

```bash
openclaw plugins list | grep -i whatsapp
```

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
N=$(security find-generic-password -a lms -s lms/whatsapp-owner -w)
```

Refuse a number that is empty or not all digits (this must print nothing):

```bash
case "$N" in ''|*[!0-9]*) echo "lms/whatsapp-owner must be digits only"; false;; esac
```

If it printed that message, stop and store the number again (step 3). Otherwise:

```bash
sed "s/__WHATSAPP_OWNER__/$N/g" ops/remote.patch.json5 > /tmp/remote.patch.json5; unset N
```

```bash
openclaw config patch --file /tmp/remote.patch.json5 --dry-run
```

Read the dry-run output: his number must appear with a leading `+` (for example `+15551234567`), once in `allowFrom`, in the WhatsApp binding and in the two command lists. If it does not, stop.

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

Confirm the plugin loaded and registered its commands (a wrong entry file shows here, not as silence on the phone):

```bash
openclaw plugins inspect lms-control
```

```bash
openclaw plugins doctor
```

`inspect` must show it enabled with the `lms` and `halt` commands; `doctor` must report no load issue for `lms-control`.

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

Replies in WhatsApp's "Message yourself" chat start with `[openclaw]`. Leave that alone: it stops a reply that begins with `/lms ...` from ever being read back as a command. Do not set `messages.responsePrefix` to empty.

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

That puts the config back. If you instead switch chat control off in place, disable both plugin entries:

```bash
openclaw plugins disable whatsapp
```

```bash
openclaw plugins disable lms-control
```

If chat control is removed for good, set `D015_PLUGINS` in `ops/verify_setup.py` back to 4, or check [5] will fail.

```bash
launchctl bootout gui/$(id -u)/com.lms.agentcontext
```
