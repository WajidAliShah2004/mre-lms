# Mac command sequence — Day 1–2 bring-up and D-017 rotation

Run in this order, as Matthew's user (D-009) unless a step says otherwise.
Every step is safe to re-run.

**Before you start:** turn off shell history for this session. Credentials
have already leaked twice on this engagement (Aug 6, and D-017 on Sept 7),
and both times the mechanism was a value ending up somewhere it was not
meant to persist.

```bash
unset HISTFILE
set +o history
```

---

## 0. Recon — know the machine before changing it

```bash
# macOS version and hardware
sw_vers && sysctl -n machdep.cpu.brand_string && sysctl -n hw.memsize

# Every human account on the box
dscl . -list /Users | grep -v '^_'

# Which of them are admins — this is how you resolve C21 (rescueadmin)
dscl . -read /Groups/admin GroupMembership

# What rescueadmin actually is: created when, home dir, last login
dscl . -read /Users/rescueadmin RealName UniqueID NFSHomeDirectory 2>/dev/null
ls -ld /Users/rescueadmin 2>/dev/null
last rescueadmin | head -5

# Who you are right now, and whether the gateway matches
id -u && id -un
launchctl list | grep -i openclaw
```

**Do not proceed past Phase 8 hardening until `rescueadmin` is explained.**
If it turns out to be an unused account, remove it rather than hardening
around it:

```bash
# Only after Matthew confirms it is not his and holds nothing
sudo sysadminctl -deleteUser rescueadmin -secure
```

---

## 1. Encryption state (C5 / D-011)

```bash
# Boot volume
fdesetup status

# Every APFS volume and its encryption state
diskutil apfs list | grep -E "APFS Volume|FileVault|Encryption"

# The three named volumes specifically
for v in Bulk MacStudioHome Vault; do
  echo "--- $v"
  diskutil info "/Volumes/$v" 2>/dev/null | grep -Ei "FileVault|Encrypted|Mount Point"
done
```

To encrypt a volume in place — **only with written approval from Matthew**,
and start with one of the two empty ones so a failure costs nothing:

```bash
diskutil apfs encryptVolume /Volumes/Vault -user disk
# Prompts for a passphrase. It will NOT be echoed. Store it in a password
# manager immediately — losing it loses the volume.
```

Encryption runs in the background. Watch it finish before putting data on it:

```bash
diskutil apfs list | grep -A3 Vault    # look for "Conversion Progress"
```

After a reboot, confirm it mounts without a prompt. If it prompts, the
passphrase is not in the system keychain and every launchd job that writes
there will fail silently at boot.

---

## 2. Rotate the exposed credentials (D-017)

### 2a. The macOS account password — `123456`

This is the one that matters most. Under D-009 that account is the container
for the whole system.

**Log in as that account and run `passwd` from inside it:**

```bash
# As the OpenClaw user, in its own session
passwd
```

**Do not use `sysadminctl -resetPasswordFor` from another admin account.**
It changes the login password without re-wrapping the login keychain, and the
keychain then cannot be unlocked — which would break the `gateway.auth.token`
exec SecretRef from D-016 and stop the gateway starting, unattended, with no
obvious cause. `passwd` from within the account does it correctly.

If the account is FileVault-enabled, re-verify afterwards:

```bash
sudo fdesetup list          # account still listed = still able to unlock at boot
```

### 2b. The seven leaked account passwords

GitHub, five mailboxes, Backblaze. These are web logins — change them in each
provider's UI, not from a shell. Then store what the system needs in the
Keychain, never in a file:

```bash
# -w with NO value prompts interactively and keeps the secret out of both
# the command line and the shell history. This is the D-016 lesson applied.
security add-generic-password -a lms -s lms/telegram-bot-token -w
security add-generic-password -a lms -s lms/backblaze-key-id -w
security add-generic-password -a lms -s lms/backblaze-app-key -w
```

Read back to verify — note the redirect. Never print a secret to a terminal:

```bash
security find-generic-password -a lms -s lms/backblaze-key-id -w >/dev/null && echo OK
```

List what is stored, without values:

```bash
security dump-keychain 2>/dev/null | grep -o 'lms/[a-z-]*' | sort -u
```

### 2c. Remove the source document

Once every value is in a password manager and the Keychain:

```bash
# Wherever the PDF landed on the Mac
srm -v ~/Downloads/"Completed System Setup and Access Requirements copy.pdf" 2>/dev/null \
  || rm -P ~/Downloads/"Completed System Setup and Access Requirements copy.pdf"
```

Then confirm it never reached git history, on the workstation:

```bash
git log --all --oneline --name-only | grep -i "System Setup and Access" || echo "clean"
```

---

## 3. Toolchain (Phase 2)

```bash
# Homebrew, if it is not there
command -v brew || /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

brew install node python@3.12 git rclone restic ffmpeg sqlite
brew install --cask lm-studio tailscale

# Everything answers
for t in node python3 git rclone restic ffmpeg sqlite3; do
  printf '%-10s ' "$t"; $t --version 2>&1 | head -1
done
```

---

## 4. Power baseline (Phase 1)

```bash
sudo pmset -a sleep 0 disksleep 0 displaysleep 10 autorestart 1 powernap 0
pmset -g | grep -E "sleep|autorestart|powernap"
```

Planned reboots come back unattended with a one-shot unlock:

```bash
sudo fdesetup authrestart
```

Unplanned power loss still needs a human at the machine — that is C8, and it
is a name in the RUNBOOK, not a command.

---

## 5. Clone and bring up the code

```bash
cd ~
git clone <client-owned private repo URL> lms-repo
cd lms-repo/lms

chmod +x ops/bringup_mac.sh
./ops/bringup_mac.sh /Volumes/Vault        # the encrypted RAID volume
```

The script creates the storage trees, installs `pyyaml` and `pytest`, writes
`ops/lms.env` (paths only), runs the full suite on this machine, and prints
what is still blocked.

Expected: **41 passed, 1 xfailed**. The xfail is the C16 gate and is correct
until the legal names and EINs arrive. Anything else failing means stop —
do not continue to Phase 6.

---

## 6. GPU wired limit (Phase 4)

```bash
sudo sysctl iogpu.wired_limit_mb=245760
sysctl iogpu.wired_limit_mb
```

This does **not** survive a reboot on its own. Persist it with the
`com.lms.wiredlimit` LaunchDaemon from MAC_SETUP_GUIDE.md §3, then reboot and
re-check — without the daemon, the first power-triggered restart silently
shrinks GPU memory below the resident model stack and inference degrades with
no error message (Rec 21).

```bash
sudo reboot
# after login:
sysctl iogpu.wired_limit_mb        # must still print 245760
```

---

## 7. LM Studio — verify it is loopback-only

```bash
curl -s http://localhost:1234/v1/models | python3 -m json.tool | head -20

# MUST show 127.0.0.1:1234, not *:1234
lsof -nP -iTCP:1234 -sTCP:LISTEN
```

If it shows `*:1234`, "Serve on Local Network" is on. Turn it off before any
mailbox connects — that setting exposes the model endpoint to the LAN.

---

## 8. OpenClaw state check (Phases 5–6)

```bash
chmod 700 ~/.openclaw && find ~/.openclaw -type f -exec chmod 600 {} \;

openclaw --version
openclaw doctor
openclaw security audit --deep

# D-015 said 3 plugins, 0 ready skills. Every update can reopen this.
openclaw plugins list | grep -c enabled
openclaw skills list | grep -c ready
```

If the skill or plugin counts have moved since D-015, an update shipped new
capability that defaulted to on. Diff and disable before doing anything else
— there is no default-deny for these, only per-name control.

---

## 9. Restore history when you are done

```bash
set -o history
```

---

## Order that matters

Two sequences will cost you real time if reversed:

1. **Tailscale before egress lockdown.** Verify Screen Sharing over Tailscale
   from an outside network — phone on cellular, not office wifi — *before*
   default-deny goes on. RustDesk is the build channel; if you cut it before
   the replacement is proven, nobody reaches the machine until someone walks
   over to it.
2. **Rotation before mailboxes.** Do not connect a mailbox using a password
   that travelled through a PDF. It is the same account either way — the
   rotation is what makes the connection legitimate.
