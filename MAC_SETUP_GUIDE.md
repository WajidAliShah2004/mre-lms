# LMS Mac Setup Guide — M-Ultra · 256 GB · macOS Tahoe 26.x

Your hardware matches the spec target exactly. This is the hands-on execution of **Phase 0 + Phase 1** on your Mac, with copy-paste commands. Run everything in Terminal unless noted. Follow the order.

---

## 1. Security & power baseline (Phase 0)

### 1.1 FileVault
System Settings → Privacy & Security → FileVault → Turn On. Store the recovery key offline (sealed envelope per §8.7).

```bash
# verify
fdesetup status
```

### 1.2 Never-sleep
```bash
sudo pmset -a sleep 0 disksleep 0 displaysleep 10
sudo pmset -a autorestart 1        # auto-restart after power failure
sudo pmset -a powernap 0
```

### 1.3 Dedicated non-admin user
System Settings → Users & Groups → Add User → Standard. Name it e.g. `lms`. All LMS services run under this account; keep your admin account separate.

### 1.4 macOS permissions (grant later to the LMS user/apps as prompted)
Full Disk Access, Automation, Accessibility, Microphone — System Settings → Privacy & Security.

## 2. Toolchain

```bash
# Homebrew
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

brew install node python@3.12 git rclone restic ffmpeg sqlite
brew install --cask tailscale lm-studio
```

Create a `Brewfile` in the repo so rebuild-as-code works (§11.5):
```bash
brew bundle dump --file=./ops/Brewfile
```

## 3. GPU wired-memory limit + LaunchDaemon (Phase 1 prerequisite — Rec 21)

```bash
# immediate (lost on reboot)
sudo sysctl iogpu.wired_limit_mb=245760
```

Persist across reboots — create `/Library/LaunchDaemons/com.lms.wiredlimit.plist`:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.lms.wiredlimit</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/sbin/sysctl</string>
    <string>iogpu.wired_limit_mb=245760</string>
  </array>
  <key>RunAtLoad</key><true/>
</dict></plist>
```
```bash
sudo chown root:wheel /Library/LaunchDaemons/com.lms.wiredlimit.plist
sudo chmod 644 /Library/LaunchDaemons/com.lms.wiredlimit.plist
sudo launchctl load -w /Library/LaunchDaemons/com.lms.wiredlimit.plist

# acceptance test: reboot, then verify
sysctl iogpu.wired_limit_mb   # must print 245760
```

## 4. LM Studio model stack (Phase 1)

Install via brew cask above, open once, enable the local server (OpenAI-compatible API, default `http://localhost:1234/v1`). Enable **json_schema structured output** and keep-alive.

Download per the spec's sizing classes (substitute the nearest current MLX build if a named model is unavailable — the *tier*, not the name, is normative §4.6):

| Tier | Model (spec Aug 2026) | ~GB | Resident |
|---|---|---|---|
| TIER-L1 | Qwen3.6-35B-A3B, MLX 4-bit (MTP speculative) | 21 | yes |
| TIER-L2 | Qwen3.5-122B-A10B, MLX 4-bit | 70 | yes |
| TIER-OCR | GLM-OCR 0.9B (+ PaddleOCR-VL, Chandra 2 batch) | 1–9 | OCR resident |
| TIER-A | Qwen3-ASR-1.7B MLX 5-bit (+ Whisper large-v3 fallback) | 2 | yes |
| TIER-E | Qwen3-Embedding-4B Q8 | 5 | yes |

Memory budget: ~150 GB committed, ~105 GB free — both large tiers stay resident, never swapped.

Health check:
```bash
curl -s http://localhost:1234/v1/models | python3 -m json.tool
```

## 5. Tailscale (exposure = Tailscale only, §Q213)

```bash
# after installing the cask, log in via the menu bar app, then:
tailscale status
```
Never open a public port. OpenClaw Control UI binds to loopback only. Verify from an outside network (phone on cellular) that nothing is reachable except over Tailscale.

## 6. Egress control (default-deny outbound)

Option A (spec-preferred): buy **Little Snitch**, set default-deny outbound. Option B: `pf` rules (harder to maintain; Little Snitch recommended).

Keep the allowlist as `config/egress-allowlist.txt` in the repo. Allowlist **only what this build actually calls**:

- LM Studio on localhost
- Tailscale
- Gmail / Google APIs
- the restic off-site target (D-012)
- the remote-access path chosen in D-010 — RustDesk relays only if that option wins

**Do not pre-open** `api.anthropic.com`, Twilio, SimpleFIN, QBO, or Retell. None are in the 7-day scope; `api.anthropic.com` in particular must stay closed because the spec's cloud guards are not built (D-003). Open a host when something actually calls it, not before.

⚠ **Ordering:** settle D-010 and confirm you can still reach the machine from an outside network *before* ending the session that turns default-deny on.

## 7. OpenClaw install (extended-stable pin)

```bash
npm ci --ignore-scripts             # inside the repo, lockfile committed
# install OpenClaw per its docs, pinned to the extended-stable channel ≥ 2026.7.1
# then:
chmod 700 ~/.openclaw && find ~/.openclaw -type f -exec chmod 600 {} \;
openclaw security audit --deep      # baseline — zero unjustified findings
```
Rules: **zero third-party skills** (no ClawHub installs, ever); all SKILL.md files hand-authored in-repo; subscribe to the security advisory list; same-week patch SLA.

## 8. Secrets

Everything in macOS Keychain — no plaintext `.env`:
```bash
security add-generic-password -a lms -s lms/telegram-bot-token -w '<TOKEN>'
security find-generic-password -a lms -s lms/telegram-bot-token -w   # read back
```
The database stores pointers only (`secrets_ref`).

## 9. Repository scaffold (day one, owner-owned remote)

```bash
mkdir -p lms/{openclaw/{agents,skills},core/{db,adapters/calls,pipeline,models,reports},config/institutions,prompts,shortcuts,tests/{fixtures,golden,holdout,injection},compliance,ops}
cd lms && git init
touch README.md RUNBOOK.md DECISIONS.md \
  config/{entities.yaml,taxonomy.yaml,rules.yaml,autonomy.yaml,models.yaml,egress-allowlist.txt}
git commit --allow-empty -m "scaffold" 
# push to a private remote YOU own (not a contractor's account) — §12.4
```
Enable signed commits. Never commit secrets.

## 10. What cannot live on the Mac (external dependencies)

**In scope for this build:**

| Item | Why | Action |
|---|---|---|
| Telegram bot | Sole control/approval surface | Create via @BotFather, enable 2FA on your account, pair in Phase 0 (C2) |
| Tailscale | Only exposure path; no public ports | Account/tailnet + login on the Mac (C3) |
| Google Workspace | Business mail/Drive; app password vs. **internal OAuth app** decision (Rec 22) | Decide D-008 first, then configure (C9, C11) |
| Off-site restic target (B2/S3) | The only off-machine copy — the VPS that would have held it is gone (D-000) | Account + credentials (C6, D-012) |

**Removed or out of scope:**

| Item | Status |
|---|---|
| ~~Hostinger VPS~~ | **Removed** at the Aug 5 meeting (D-000). No rescue bot, no webhook receiver, no external watchdog in v1. |
| DMARC ramp on mrecai.com / mleca.com / atlase.ai | Deferred — pure DNS work with no Mac footprint; belongs with the email phase, not setup. |
| Twilio, SimpleFIN, QuickBooks Online, Retell | Spec Phases 8–14. Out of the 7-day scope; keep them off the egress allowlist (§6). |
| UPS (1000–1500 VA), 2× hardware security keys | Purchases deferred (C19, C20). Phase 1's power-recovery procedure is the interim mitigation; the MFA *decision* (D-008) is not deferred. |

## 11. Phase 0/1 acceptance — run before proceeding

- [ ] Phone → gateway round trip over Tailscale works
- [ ] External port scan: nothing reachable outside Tailscale
- [ ] Remote access still reaches the machine from an outside network *after* default-deny (D-010)
- [ ] `openclaw security audit --deep`: zero unjustified findings
- [ ] **Warm reboot** (`sudo fdesetup authrestart`) → wired-limit daemon, LM Studio, health ping and gateway all return unattended
- [ ] **Cold power-loss boot** (pull the plug) → machine reaches the FileVault unlock screen and the documented recovery procedure brings every service back; record how long and who acts (C8)
- [ ] Reboot → `sysctl iogpu.wired_limit_mb` still 245760 → model stack reloads
- [ ] L1 classification completes within TTFT SLO with **zero network egress** (verify with packet capture)

Dropped from the spec's version of this list: the UPS-pull test (hardware deferred, C19 — the cold-boot test above is the substitute) and DMARC aggregate reports (out of setup scope). The cloud-refusal tests are moot because no cloud tier exists — see D-003 before ever adding one.

The authoritative version of this checklist is [PHASES.md](PHASES.md) Phase 9. Then continue with [BUILD_PLAYBOOK.md](BUILD_PLAYBOOK.md) — **not** `LMS_BUILD_STEPS.md`, which is the deferred full-spec roadmap.

---

**Notes for macOS Tahoe 26:** re-grant Full Disk Access/Automation permissions after major updates (the imsg bridge health monitor exists precisely because updates break it); the `brctl download` dataless-file behavior for the iCloud watched folder is unchanged in 26.x; keep the machine on extended-stable OpenClaw and apply macOS point updates only after the drain→snapshot→update→re-run-golden-set procedure (§11.2).
