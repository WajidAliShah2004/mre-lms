# RUNBOOK

Operating procedures for the LMS on the Mac Studio.

Page 1 is the kill switch, because the moment you need it is not the moment to
go looking for it.

---

# 1. STOP EVERYTHING

## From your phone

Send **`/halt`** to the bot on Telegram.

That stops the gateway, cancels anything pending, and takes effect in under
ten seconds. It is the only remote kill switch (D-005) — the menu-bar app in
the original specification was descoped.

Nothing is lost. `/halt` stops work in progress; it does not delete, send, or
change anything. Documents already filed stay filed. Mail already read stays
where it was. Restarting picks up where it left off.

**Use it whenever you are unsure.** The cost of halting unnecessarily is a few
minutes; the cost of not halting when something is wrong is unbounded.

## At the machine

```bash
openclaw gateway stop
```

## If that will not stop it

```bash
launchctl bootout gui/501/ai.openclaw.gateway
```

## Restart afterwards

```bash
openclaw gateway restart
openclaw doctor --allow-exec
cd ~/lms-repo/lms && ./ops/verify_setup.py
```

Do not skip `verify_setup.py`. A gateway that starts is not the same as a
gateway that is configured correctly, and the difference is precisely what
that script exists to catch.

---

# 2. After a power cut

The machine restarts by itself (`pmset autorestart 1`). It then stops at the
**FileVault unlock screen and nothing runs until a person types the password.**

There is no way around this that does not defeat the encryption. It is a
consequence of the disk being encrypted, not a misconfiguration.

- **Planned restarts** — `sudo fdesetup authrestart` unlocks once on next boot,
  so a remote restart comes back unattended.
- **Unplanned power loss** — someone has to be at the machine, or reach it over
  Screen Sharing after another person unlocks it.

**Who does this: `TODO(C8)` — unassigned.**

That is an open gap, not a formality. Until a name is written here, an
overnight power cut means the system is down until somebody happens to notice.
A UPS is deferred (C19), so this procedure *is* the mitigation rather than a
stopgap around one.

After unlocking, confirm everything came back:

```bash
launchctl list | grep -i openclaw          # gateway
lsof -nP -iTCP:1234 -sTCP:LISTEN           # LM Studio, must be 127.0.0.1
sysctl iogpu.wired_limit_mb                # must still print 245760
cd ~/lms-repo/lms && ./ops/verify_setup.py
```

If `iogpu.wired_limit_mb` is not 245760, the `com.lms.wiredlimit` LaunchDaemon
did not load. Inference will still work but will quietly degrade as the model
stack no longer fits in wired memory — no error, just worse (Rec 21).

---

# 3. Updating OpenClaw

**Never blind.** D-015 established that OpenClaw has no default-deny for
skills or plugins — control is per-name only — so **every update can ship new
capability that defaults to enabled**, and nothing will tell you.

```bash
# 1. Record the current state
openclaw plugins list > /tmp/plugins.before
openclaw skills list  > /tmp/skills.before
cp ~/.openclaw/openclaw.json ~/.openclaw/openclaw.json.pre-update

# 2. Drain and stop
openclaw gateway stop

# 3. Update — extended-stable only, never the fast channel
openclaw update

# 4. Diff. This is the step that matters.
openclaw plugins list > /tmp/plugins.after
openclaw skills list  > /tmp/skills.after
diff /tmp/plugins.before /tmp/plugins.after
diff /tmp/skills.before  /tmp/skills.after

# 5. Disable anything new BEFORE starting the gateway
#    openclaw plugins disable <name>

# 6. Verify, then resume
openclaw doctor --allow-exec
openclaw security audit --deep
cd ~/lms-repo/lms && ./ops/verify_setup.py
openclaw gateway restart
```

Expected baseline: **3 plugins enabled of 67, 0 skills ready of 51.**
`verify_setup.py` check 5 asserts exactly this and fails if it moves.

**macOS updates too:** a major macOS update revokes Full Disk Access and
Automation permissions. Re-grant them, or the iMessage bridge and the watched
folder stop working — silently, because a permission denial looks like an
empty folder.

---

# 4. Credential rotation

Every secret lives in the login Keychain. None are in the repo, and none are
in a file (spec §12.4, D-016).

```bash
# The -w with NO value prompts. The secret never reaches the command line,
# the process table, or your shell history.
security add-generic-password -U -a lms -s lms/telegram-bot-token -w

# Read back WITHOUT printing it
security find-generic-password -a lms -s lms/telegram-bot-token -w >/dev/null && echo OK

# What is stored, without values
security dump-keychain 2>/dev/null | grep -o 'lms/[a-z-]*' | sort -u
```

After rotating anything the gateway reads:

```bash
openclaw gateway restart
openclaw doctor --allow-exec     # --allow-exec or the probe is skipped entirely
```

**The habit, not the rotation.** D-016 records the gateway token being printed
to a terminal and having to be treated as exposed. D-017 records seven client
credentials arriving in a PDF. Both were survivable. The lesson from both is
the same: a secret that appears anywhere it did not need to appear is spent.

Read-backs end `>/dev/null && echo OK`. Always.

---

# 5. Backup and restore

Nightly at 02:30 via `com.lms.backup`. Encrypted with restic; the repository
is ciphertext at rest.

## Check a backup is good — do this monthly, not after a disaster

```bash
cd ~/lms-repo/lms && source ops/lms.env
./ops/restore_test.py
```

Restores the latest snapshot into a temp directory and checks the database
opens, passes `integrity_check`, has the row counts the snapshot claims, that
the sidecars all came back, and that `entities.yaml` parses. Nothing is
written outside the temp directory and the live archive is opened read-only,
so this is safe to run at any time — including a moment when things are
already going wrong.

Anything other than `Restore verified — the backup is usable.` means the
backup would not have saved you. The one to fear reads:

> `the restored database is EMPTY — it opens and contains nothing.`

That is what a file copy of a live WAL database looks like. Measured: 500
committed rows in, 0 out, passing `integrity_check` throughout.

## Take one by hand

```bash
./ops/backup.py                   # database, config, sidecars AND documents
./ops/backup.py --catalogue-only  # everything except the documents
```

**Documents are included by default** since Sept 10 (D-050). They were
optional before, matching the spec's "DB, configs, sidecars" — but that
default rested on the spec's own caveat, *"only sufficient while the documents
survive elsewhere"*, and here "elsewhere" is one RAID volume. Single-parity
RAID survives a disk failing; it does not survive the enclosure, the
controller, the filesystem, a deletion, or theft. The nightly job was
producing a flawless index of files that would not exist.

`restore_test.py` prints which kind it checked. A `CATALOGUE ONLY` warning
from the scheduled job now means someone has added `--catalogue-only` to the
plist, and that is worth noticing rather than shrugging at.

## Actually restoring, when it is not a drill

```bash
./ops/restore_test.py --keep    # leaves the tree, prints where
```

Then copy what you need out of it. There is no "restore in place" command on
purpose: overwriting a live archive from a snapshot is a decision a person
should make file by file, while awake, not something a script does in one
step.

## The passphrase

In the Keychain as `lms/restic-repo`, and **in the password manager**.

```bash
./ops/set_backup_password.py --show
```

> **restic has no recovery path.** If this passphrase is lost, every snapshot
> is permanently unreadable — still there, still encrypted, useless to
> everyone including Matthew. Losing it is indistinguishable from never having
> taken a backup. It is a credential-custody item alongside the FileVault key
> (C7), not a detail of the backup script.

To replace it — note this makes existing snapshots unreadable, so take a fresh
backup immediately afterwards:

```bash
./ops/set_backup_password.py     # generates, stores, verifies, prints once
```

## Where it lives, and what that covers

`$HOME/LMS/backup` — the **internal SSD**, deliberately not the array.

`diskutil list` (Sept 10): the 12 TB array is `disk10`, four 4 TB disks
presented as one device carrying one volume, `MacStudioHD`. There is no second
partition and no useful one to make — anything inside that container shares the
physical store. Single-parity RAID survives one disk failing; it is not a
backup, and does nothing about the enclosure, the controller, filesystem
corruption, a bad delete, or theft.

So: **this survives the array failing.** It does not survive the machine.
Theft, fire, or the Mac dying takes both copies, because both are inside it.

There is still **no off-machine copy** — D-012, open. The blocker is not
technical: an off-machine target means client tax and NPI data leaving the
premises, and that needs Matthew's decision in writing.

If a dedicated external disk is ever added, change one line in `ops/lms.env`
and re-run `./ops/backup.py`. The same-volume check will confirm it.

`./ops/verify_setup.py` check [8] reports both every time it runs.

---

# 6. Where things are

| | |
|---|---|
| Repo | `~/lms-repo/lms` |
| Filed documents | `/Volumes/MacStudioHD/LMS/archive/<ENTITY>/<CATEGORY>/` |
| Untouched originals | `/Volumes/MacStudioHD/LMS/_originals/` |
| Quarantine (needs review) | `~/LMS/quarantine/` |
| Watched inbox | `~/LMS/inbox/` |
| OpenClaw config | `~/.openclaw/openclaw.json` |
| Config snapshots | `~/.openclaw/openclaw.json.pre-*` |
| Python interpreter | `~/lms-repo/lms/.venv/bin/python` — never bare `python3` |
| Paths for launchd | `~/lms-repo/lms/ops/lms.env` |

`/Volumes/MacStudioHD` is the 12 TB array, already FileVault-encrypted.
Four × 4 TB presenting as 12 TB means **one-disk parity — redundancy, not
backup.** One flood, one theft, or one bad write still takes everything, which
is why the off-site target (D-012) is not optional.

---

# 7. What the system will never do

Enforced in code and asserted in `tests/test_hard_stops.py`, not written as
instructions to a model — so they hold even if the AI is wrong or manipulated:

- Never send anything externally without an explicit approval tap
- Never delete an email or a file. Never mark an email as read
- Never move money, sign, or bind you to anything
- Never send client or tax data to a cloud service — all inference is local
- Never click a link in a message body

If you ever see the system appear to do one of these, `/halt` first and
investigate second.
