# Chat control over WhatsApp and Telegram — design

Date: 2026-11-05 · Status: approved by the developer, Nov 5 · Decision: D-080 (changes D-056) · Requirements: R10, R11

## Why

Matthew asked to control OpenClaw and the Mac from WhatsApp and Telegram. Today
the only phone control is `/halt` and `/status` on @MREOC18bot
(`ops/halt_bot.py`, D-059). @MREOCbot reaches OpenClaw's `lms-orchestrator`,
which has no tools (D-056), so it can only chat. Approvals can be answered only
at the Mac (`ops/approve.py`); R10/R11 call for a channel on his phone.

## Decisions (Nov 5)

| Question | Answer |
|---|---|
| How much can a chat message do? | A **fixed command list** only. Code runs each command; the model never chooses an action. |
| Which commands? | All four groups: look, run now, change records, halt (table below). |
| Plain text (not a command)? | The local model answers, **read-only**, from an hourly snapshot. It still has no tools. |
| Approach | An **OpenClaw plugin** registers the commands; Python does the work. Not our own bot or WhatsApp client; not an agent with tools. |
| WhatsApp number | **Matthew's own number**, OpenClaw's self-chat mode. His choice, made after the risks below were stated. |
| Who asked | Matthew. |

Rejected: giving `lms-orchestrator` tools (a forwarded message reading
"approve 7" could make the model call `approve`); a dedicated bot number
(recommended, declined); a remote `resume` (a stolen phone can stop the Mac,
never restart it, as D-059).

## Pieces

### 1. `lms/openclaw-plugin/lms-control/` (OpenClaw plugin)

- `openclaw.plugin.json` and `index.js` (or `.ts`, whichever the installed
  OpenClaw loads; confirmed in plan task 1).
- Registers `/lms` with `acceptsArgs: true, requireAuth: true`. Registers
  `/halt` too if that name is not reserved by a built-in (OpenClaw has `/stop`,
  not `/halt`, per `halt_bot.py`'s docstring; confirmed on the Mac).
- The handler holds no logic. It calls
  `/Users/mleca/lms-repo/lms/.venv/bin/python ops/remote.py <args...> --channel <telegram|whatsapp> --sender <id>`
  with `execFile` (no shell), working directory `lms/`, a 60 s timeout, and
  returns stdout as the reply. On timeout: "Still running. See
  ~/LMS/logs/remote.log." On a non-zero exit: stdout if any, else "Failed. See
  ~/LMS/logs/remote.log."
- `/lms` is one name because OpenClaw reserves built-in names (`/status`, and
  possibly `/approve`) and refuses a plugin that registers one.

### 2. `lms/ops/remote.py` (all the logic)

The single entry point, tested with pytest like every other `ops/` script.

- **Sender check, twice.** OpenClaw's `requireAuth` plus `commands.allowFrom`
  are the first check. `remote.py` checks again: `--sender` must equal
  Matthew's Telegram id (Keychain `lms/telegram-owner-id`) or his WhatsApp
  number (Keychain `lms/whatsapp-owner`, stored at apply time). Anyone else:
  exit 0, print nothing, so nothing is sent back.
- **Arguments.** The subcommand must be in the table. Numbers must match
  `^[0-9]{1,6}$`. The only other word accepted is the trailing `yes`. Anything
  else prints the help list.
- **Audit.** Every call, accepted or refused, appends a row to `actions_log`
  (append-only): `action = "remote:<subcommand>"`, `detail` = channel, sender
  kind (owner or refused), arguments and result in one line,
  `approved_by = "matthew"` for change commands. Also one line to
  `~/LMS/logs/remote.log`.
- **Halt.** "Run now" commands read the halt flag (`ops/_halt.read_flag`) and
  refuse while halted: "Halted since <time>. Resume at the Mac:
  ./ops/halt.py --resume".
- **Reply length.** At most 3,500 characters (under Telegram's 4,096 limit and
  short enough to read on a phone). Longer output is cut with "… more on the
  Dashboard."

### 3. The commands (`/lms <word>`)

| Command | Does |
|---|---|
| `help` (or nothing) | The list |
| `status` | Halted or not (`_halt.status`), each `com.lms.*` job, last good mail check, last good WhatsApp check (from `source_checks`; the backup job's state is in the job list) |
| `brief` | Today's brief text, shortened |
| `tasks` · `tasks overdue` | Open tasks by ledger number: number, title, due date, entity |
| `coverage` | Registered sources that are behind or failing |
| `mail` · `whatsapp` · `backup` | `launchctl kickstart gui/<uid>/com.lms.<job>`: the existing job runs now with its own logs and locks. Refused while halted. `whatsapp` says "not installed" until R40 is |
| `done 12` | Replies with task #12's title and "Send `/lms done 12 yes` to close it." With `yes`: `tasks.close_task(..., by="matthew")` with a note naming the channel |
| `approve 7` · `deny 7` | Same two steps for approval `AP-7`: shows the action, amount and expiry; with `yes`, `approvals.decide(conn, "AP-7", answer, by="matthew", note="via <channel>")` |
| `halt` (and `/halt`) | `_halt.halt(by="<channel>")`. It stops the OpenClaw gateway too, so the reply is sent first and these channels then go quiet. @MREOC18bot keeps answering `/status` |

The `yes` step: the first message only shows. A task number typed wrong costs
nothing, and an approval is never decided by one message. The confirmation is
stateless: `done 12 yes` acts whether or not `done 12` was sent first, because
the number is in the message.

There is no `resume`, `revise`, `dismiss`, `reopen`, or anything that runs a
shell command, edits config, or sends a message to anyone else.

### 4. `lms/ops/agent_context.py` (read-only snapshot for plain text)

- Runs hourly from its own launchd job, `com.lms.agentcontext`
  (`StartInterval` 3600), and also after each `/lms` change command.
- Writes `~/.openclaw/workspace-lms-orchestrator/LMS_NOW.md`: generated time,
  halted or not, open and overdue tasks (number, title, due, entity),
  approvals waiting (ref, action, expiry), sources behind. No document text,
  no account numbers. At most about 6,000 tokens (the model's context is 33k).
- Written to a temporary file and renamed, so the agent never reads half a file.
- The workspace's `AGENTS.md` gets one paragraph: answer questions about
  Matthew's tasks from LMS_NOW.md, say the time it was generated, and point to
  `/lms` commands for any action. Which file OpenClaw injects into the prompt
  is confirmed in plan task 1; if it injects only fixed names (`AGENTS.md`,
  `USER.md`), the snapshot goes into one of those between marker lines.
- Halted: the job writes nothing (it is a halt target like the others).

### 5. `lms/ops/remote.patch.json5` (OpenClaw config)

Every key is read from `openclaw config schema` on the Mac first (D-021, D-056).

- `channels.whatsapp`: enabled, `dmPolicy: "allowlist"`,
  `allowFrom: ["<his number>"]`, `selfChatMode: true`,
  `groupPolicy: "disabled"`, `configWrites: false`, exec approvals off.
- `bindings`: one more narrow route, WhatsApp, his number, direct → 
  `lms-orchestrator`.
- `commands.ownerAllowFrom` and `commands.allowFrom` gain `whatsapp:<number>`.
  `bash`, `config`, `mcp`, `plugins`, `debug`, `restart` stay `false`.
- `plugins`: load `lms-control` from the repo path and enable it.

His number is not in the repo. It comes from `openclaw channels status` after
the QR link and is written into the patch at apply time from the Keychain, as
the Telegram id was.

## WhatsApp on his own number

- He sends commands in WhatsApp's "Message yourself" chat.
- Messages from anyone else are dropped and never answered. Plan task 1 settles
  the docs' disagreement (one copy says the linked number skips the allowlist;
  the official page ties self-chat protection to `allowFrom`). The apply check
  messages his number from another phone and confirms no reply and a
  "dropped" log line.
- OpenClaw's link and the WhatsApp Mac app (R40's archive reader) are two
  linked devices; WhatsApp allows four.
- **Accepted risk, his choice:** OpenClaw's WhatsApp client is unofficial.
  WhatsApp can ban numbers it detects using one, and this is his main number.
  OpenClaw's docs recommend a separate number.

## Errors

- Gateway down or halted: nothing answers on WhatsApp or @MREOCbot. @MREOC18bot
  is the fallback and is unchanged.
- `remote.py` crashes: the plugin replies "Failed. See ~/LMS/logs/remote.log";
  the traceback is in the log.
- Database locked by a running job: `remote.py` retries for 10 s, then replies
  "Busy, try again in a minute."
- Unknown command or bad number: the help list.

## Testing

- `tests/test_remote.py`: sender check (owner, stranger, missing Keychain
  entry), argument parsing, the `yes` step for done, approve and deny, halt
  refusal of run-now, audit rows for accepted and refused calls, reply cut at
  3,500, kickstart called with the right label (injected runner, as
  `_halt.py`'s tests do).
- `tests/test_agent_context.py`: snapshot content and size cap, atomic write,
  nothing written while halted.
- `tests/test_remote_patch.py`: pins the patch's safety keys (allowlist, self
  chat, groups off, config writes off, narrow binding, dangerous commands
  false), as `test_phase7b.py` does.
- `verify_setup.py`: plugin loaded; WhatsApp `allowFrom` is exactly one number
  and equals the Keychain owner; `com.lms.agentcontext` installed.
- The plugin itself is tested on the Mac by the apply checklist (each command
  once on each channel, a stranger's message, `/lms halt` then @MREOC18bot
  `/status`).

## Order of work

1. **Mac check, before code:** `openclaw config schema` dump (WhatsApp keys,
   `plugins` keys), the plugin-command API and its reserved names, which
   workspace files are injected. The plan is adjusted to what the machine says.
2. `remote.py` and its tests.
3. `agent_context.py`, its launchd job and tests.
4. The plugin.
5. The patch, its test, `verify_setup`.
6. `CHAT_CONTROL_APPLY.md`, D-080, R10/R11, PHASES.
7. Apply on the Mac: link WhatsApp in OpenClaw, store his number, patch,
   restart, the checklist above.

## Not in scope

Voice notes as commands, sending messages to other people, scheduling,
anything in `ops/` beyond the table, a web dashboard login. The WhatsApp
archive reader (R40, D-079) is separate and keeps its own plan.
