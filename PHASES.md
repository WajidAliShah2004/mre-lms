# PHASES — OpenClaw Setup on the Mac Studio

Scope: **only** getting OpenClaw installed, configured, hardened, and verified on the Mac Studio (M-Ultra · 256 GB · macOS Tahoe 26.x). Nothing here covers email pipelines, classification, filing, or any LMS feature work — those live in [BUILD_PLAYBOOK.md](BUILD_PLAYBOOK.md) and [GUIDELINES_7DAY_BUILD.md](GUIDELINES_7DAY_BUILD.md).

Command-level detail for most steps is in [MAC_SETUP_GUIDE.md](MAC_SETUP_GUIDE.md) (referenced per phase below). Spec references (§) are to the v3.0 developer specification PDF. Where that document's own phase numbers appear ("spec Phase 0", "spec Phase 1"), they refer to its sixteen-phase plan — not to the nine phases below.

Per the Aug 5 meeting: **no Hostinger VPS** — this is a Mac-only setup.

---

## What's needed from Matthew

One list, so nothing stalls silently. Each phase below references this table rather than restating it. Status column is maintained here — update it as answers land, and record decisions in `DECISIONS.md`.

**Status values:** `Closed` · `Asked — awaiting` · `Partial` · `Open` · `Dropped` · `Deferred`.
`Asked` is not `Closed`. The point of this table is that a question in flight still blocks the day it blocks.

**Last reconciled:** Sept 7 2026, against the client's *System Setup and Access Requirements* document. The original Aug 17 acceptance date is void — Days 1–7 were never run, and the schedule needs re-dating with Matthew present.

### Blocks setup phases

| # | Item | What exactly | Blocks | Status |
|---|---|---|---|---|
| C1 | **Private git remote** | A private repo he owns (not a contractor account), with push access for the developer (§12.4) | Phase 3 | **Reopened — Partial (D-020)** — client *account* exists (`Mleca18`) but no repo. Interim remote is `WajidAliShah2004/mre-lms`, a **contractor** account. Unblocks the build; must be transferred to `Mleca18` before handover, and confirmed Private now |
| C2 | **Telegram** | Account paired, 2FA enabled, and his **user id** for `commands.ownerAllowFrom` | Phase 7 — also clears the open `operator.read` audit finding | **Partial** — @handle supplied. The config field takes the *numeric* id, which we capture from his first message to the bot during Phase 7. 2FA still unconfirmed |
| C3 | **Tailscale** | Account/tailnet, login on the Mac | Phase 8 | **Closed** — account created, installed on the Mac |
| C4 | **Remote-access decision** | Keep RustDesk (allowlist its relays, remove at handover) **or** switch to Tailscale + macOS Screen Sharing (recommended) | Phase 8 — must be settled *before* default-deny egress, or the machine locks you out | **Closed** — Tailscale. RustDesk stays as the build channel until Tailscale + Screen Sharing is *verified from an outside network*, then removed. Never the reverse order (D-010) |
| C5 | **RAID volumes** | Which volumes hold LMS data/models, and approval to encrypt in place | Phase 1 item 4 | **Closed by observation (D-019)** — Bulk/MacStudioHome/Vault do not exist. The array is one already-encrypted 12 TB volume, `MacStudioHD`, empty. Nothing to encrypt, no approval needed. Archive root = `/Volumes/MacStudioHD/LMS` |
| C6 | **Off-site backup target** | B2/S3 account + credentials — there is no VPS to back up to (D-000) | Phase 8 item 5 | **Closed** — Backblaze account created. Credentials to Keychain, then rotate (D-017) |
| C7 | **FileVault recovery key custody** | Sealed envelope held by him; confirm the keys exposed on 2026-08-06 were rotated | Phase 1 | **Open** — listed on his own to-do, not yet done. Rotation of the Aug 6 exposure still unconfirmed |
| C8 | **Named unlock owner** | Who physically unlocks the Mac after a power cut, and how they find out | Phase 1 item 3 / RUNBOOK | **Open** |
| C21 | **Abandoned accounts** | Four accounts existed, not two. Identify and remove the dead ones | Phase 1 / Phase 8 — hardening around unknown admins is not hardening | **Closed (D-018)** — `OpenClaw` (502, admin + FileVault + trivial password), `rescueadmin` (503), `lms` (504) all removed after archiving. Only `mleca` remains; only `mleca` is admin; only `mleca` can unlock the disk |

### Blocks the feature build (BUILD_PLAYBOOK.md)

| # | Item | What exactly | Blocks | Status |
|---|---|---|---|---|
| C9 | **Credential / MFA decision** | The written recommendation (keep 2FA on; app passwords or internal Workspace OAuth app) sent, and his answer recorded as D-008 | Day 3 — no mailbox connects until this lands | **Answered by action, not by choice** — raw passwords supplied for all five mailboxes; neither option selected. See D-017. Rotate and move to app passwords at minimum |
| C10 | **Email accounts** | Complete list of addresses across personal and all four entities | Day 3 | **Closed** — five addresses supplied and loaded into `entities.yaml`. Note: **no C.H. Shink mailbox**. Confirm the entity is document-only, or supply the address |
| C11 | **Google Workspace** | Admin access; four Shared Drives created (MRECAI, C.H. Shink, Atlase, MLECA); OAuth grant | Day 3 | **Asked — awaiting** |
| C12 | **iCloud Drive** | Signed in on both Mac and iPhone; personal folder root confirmed | Day 4 | **Partial** — developer authorised to perform the Mac side. The **iPhone** side and the Apple ID 2FA prompt still need Matthew in the room |
| C13 | **iMessage** | Messages signed in on the Mac; Full Disk Access granted; group-chat opt-in list | Day 4 — see D-009 | **Partial** — developer authorised for sign-in and FDA. The **group-chat opt-in list stays his decision**: it scopes whose messages get ingested, which is not a setting a contractor should pick. Default remains none |
| C14 | **Photographed mail** | iOS Shortcut installed on his phone, and he actually uses it | Day 4 | **Open** — developer builds and sends the Shortcut; installation and first-run Allow are on his phone. The only deliverable that depends on a habit rather than a setting |
| C15 | **Call transcripts** | Where they come from (Retell / Twilio / carrier voicemail) and read access | Day 4 — first thing to cut if the week slips | **Open** |
| C16 | **Entities & people** | Legal names, EINs, name variants, and exact spellings for the five people | Day 2 | **Open — highest-value blocker.** This *is* `config/entities.yaml`. Placeholders are in place and marked `TODO(C16)`; `tests/test_registry.py::test_no_placeholders_remain` is the acceptance gate and currently xfails by design |

### Confirmations and purchases

| # | Item | Note | Status |
|---|---|---|---|
| C17 | **Hostinger decommission** | Confirm nothing needed remains on it *before* deletion | **Dropped** — developer judgment, Sept 7 |
| C18 | **7-day descope sign-off** | The scope change from the Aug 5 meeting, in writing | **Dropped** — developer accepted the risk of an unsigned descope, Sept 7 |
| C19 | **UPS** (~$150–250) | Deferred purchase; Phase 1's power-recovery procedure is the interim mitigation | Deferred |
| C20 | **2× hardware security keys** (~$60) | Deferred purchase; the *decision* on MFA posture (C9) is not deferred | Deferred |

**Not needed yet — do not request accounts:** QuickBooks Online, SimpleFIN, Twilio, WhatsApp, Retell outbound feed, PandaDoc/DocuSign, Dropbox, Slack/Teams/Signal, LinkedIn. All are outside the 7-day scope; keep them off the egress allowlist until something actually calls them.

---

## Phase 1 — Mac security & power baseline

*Guide: MAC_SETUP_GUIDE.md §1. Spec: Phase 0; Rec 27; Q214.*

1. FileVault on; recovery key stored offline in a sealed envelope (`fdesetup status` to verify).
2. Never-sleep: `sudo pmset -a sleep 0 disksleep 0 displaysleep 10 autorestart 1 powernap 0`.
3. **Power-recovery procedure (Rec 27).** `autorestart 1` brings the machine back after a power cut, but FileVault leaves it at the unlock screen — nothing starts until someone unlocks it, and there is no auto-unlock that doesn't defeat FileVault. Write the procedure into RUNBOOK.md (Phase 3):
   - Planned reboots: `sudo fdesetup authrestart` — a one-shot unlock on next boot, so remote restarts come back unattended.
   - Unplanned power loss: the machine needs a physical or Screen-Sharing unlock. Name who does it and how they find out (**C8**).
   - UPS is deferred (see the closing table), so this procedure *is* the mitigation, not a stopgap around one.
4. **External RAID encryption (C5).** The 12 TB Thunderbolt array holds the LMS trees and the model cache — and per the meeting (00:26:35) "some of it's encrypted, some of it isn't." FileVault covers the boot volume only. Every volume that will hold LMS data, models, or the repo gets encrypted (`diskutil apfs encryptVolume`), with the passphrase saved to the **system** keychain so it mounts after a FileVault unlock without a second prompt. Record which volumes are encrypted and which are deliberately not.
5. Dedicated **non-admin** `lms` user — all LMS services run under it; admin account stays separate.
6. macOS permissions granted to the LMS user/apps as prompted: Full Disk Access, Automation, Accessibility, Microphone.
7. **Service account vs. user-session integrations — resolve before Day 4.** Items 5–6 put LMS services under the non-admin `lms` user, which is right for privilege separation. But two planned integrations are bound to *Matthew's own* macOS session and cannot see anything from a separate account:
   - **iMessage** — the `imsg` bridge reads `~/Library/Messages/chat.db` of the logged-in user (C13).
   - **iCloud Drive** — syncs into that user's home; the watched folder for photographed mail lives there (C12, C14).

   Three ways out, none free:
   - (a) **Run LMS under Matthew's user.** Everything works; privilege separation is lost, and an injected agent runs with his session's reach. Contradicts the spec's "enforce below the agent" posture (§4.1 principle 8).
   - (b) **Split it** — the gateway and agents stay under `lms`; a minimal, tool-less reader daemon runs in Matthew's session and hands artifacts to the queue read-only. Preserves the boundary at the cost of one extra component. **Recommended.**
   - (c) **Drop both channels** from the 7-day scope. Cheapest, but photographed mail is core to the meeting's ask.

   Whichever is chosen goes in DECISIONS.md before Day 4 work starts. Note this changes nothing in Phases 2–9 — it is a feature-build dependency surfaced here because the user account is created in this phase.
8. **Account MFA posture (Q214, N8, C9).** The spec requires MFA on every account and hardware security keys on the Google/Apple admin accounts, verified at this stage. The Aug 5 meeting went the other way — 2FA disabled, shared passwords in transit. Send the written recommendation from [GUIDELINES_7DAY_BUILD.md](GUIDELINES_7DAY_BUILD.md) §5 **before connecting any mailbox**, and record whichever way Matthew decides in DECISIONS.md once the repo exists (Phase 3). The hardware keys are a purchase and are deferred; the decision is not.

**Done when:** FileVault reports On; `pmset -g` shows the settings; the `lms` user exists and is Standard, not Admin; every LMS-bearing volume reports encrypted; the credential recommendation has gone out in writing.

## Phase 2 — Toolchain

*Guide: MAC_SETUP_GUIDE.md §2. Spec: Phase 0.*

1. Homebrew, then: `brew install node python@3.12 git rclone restic ffmpeg sqlite` and `brew install --cask tailscale lm-studio`.
2. npm hygiene for everything that follows: `npm ci --ignore-scripts`, lockfile committed, provenance checks, and a cooldown period before adopting new package versions.

**Done when:** every tool answers `--version`.

*(The Brewfile dump moved to Phase 3 — it writes into the repo, which doesn't exist yet.)*

## Phase 3 — Repo & source control

*Guide: MAC_SETUP_GUIDE.md §9. Spec: §9 repository layout (day one), §12.4.*

Every phase after this one writes into the repo — the Brewfile, `openclaw.json`, the egress allowlist, every SKILL.md — so it lands here, not at the end.

1. Scaffold per the guide: `openclaw/{agents,skills}/`, `core/`, `config/`, `prompts/`, `tests/`, `ops/`, plus `README.md`, `RUNBOOK.md`, `DECISIONS.md`.
2. Push to a **private remote Matthew owns** — not a contractor account (§12.4, **C1**). Enable signed commits. No secrets, ever: the repo holds pointers only.
3. `brew bundle dump --file=./ops/Brewfile` so the machine can be rebuilt as code (§11.5).
4. Seed RUNBOOK.md with the power-recovery procedure from Phase 1. `/halt` becomes page 1 in Phase 7.
5. Seed DECISIONS.md with what's already decided: no VPS, no third-party skills, no cloud tier, and the credential posture from Phase 1.

**Done when:** the repo is on the owner's remote, commits are signed, `ops/Brewfile` is committed, and a fresh clone contains no secret material.

## Phase 4 — Local model stack (OpenClaw's inference backend)

*Guide: MAC_SETUP_GUIDE.md §3–4. Spec: Phase 1, §4.6.*

1. Raise the GPU wired limit (`sudo sysctl iogpu.wired_limit_mb=245760`) and **persist it** with the `com.lms.wiredlimit` LaunchDaemon. Without the daemon, the first power-triggered restart silently shrinks GPU memory below the resident stack (Rec 21).
2. Open LM Studio once; enable the local OpenAI-compatible server at `http://localhost:1234/v1`. Verify it binds to **loopback only** (`lsof -nP -iTCP:1234 -sTCP:LISTEN` must show `127.0.0.1:1234`, not `*:1234`) and leave "Serve on Local Network" off.
   - Structured output needs no toggle — json_schema is requested per-call via the API's `response_format` parameter.
   - Residency: load models manually with **Load Model** (manually-loaded models stay resident; the auto-unload settings only apply to JIT-loaded ones).
3. Download the model tiers (substitute the nearest current MLX build — the tier, not the model name, is normative):
   - TIER-L1 ~35B MLX 4-bit (classification, resident)
   - TIER-L2 ~122B MLX 4-bit (drafting, resident)
   - TIER-OCR GLM-OCR 0.9B
   - TIER-A ASR (Qwen3-ASR 1.7B or Whisper large-v3)
   - TIER-E embedding 4B
4. Where the chosen MLX builds ship MTP heads, enable speculative decoding on the L1/L2 interactive lanes (~2.2× per §4.6.5); keep llama.cpp available as the long-context/batch lane. Neither is load-bearing for acceptance below — configure and move on.
5. **Health ping (§4.6.5, §11.1).** A launchd job every 5 minutes curls `/v1/models`; on failure it reloads the tier and alerts. Keep-alive is indefinite for all resident tiers.
6. Health check by hand: `curl -s http://localhost:1234/v1/models | python3 -m json.tool`.

**Done when:** both large tiers load and stay resident (~150 GB committed, no swapping); the models endpoint lists all tiers; the health ping recovers a killed server; after a reboot `sysctl iogpu.wired_limit_mb` still prints 245760.

## Phase 5 — OpenClaw install & pinning

*Guide: MAC_SETUP_GUIDE.md §7. Spec: §1.2, §11.2, §11.7, §11.9.*

1. Install OpenClaw pinned to the **extended-stable** channel (≥ 2026.7.1). Never the fast channel.
2. Lock down state: `chmod 700 ~/.openclaw && find ~/.openclaw -type f -exec chmod 600 {} \;`.
3. Control UI bound to **loopback only** — this is the CVE-2026-25253 one-click RCE surface, not a nicety.
4. Policy, non-negotiable: **zero third-party skills** — no ClawHub installs, ever; any SKILL.md is hand-authored in-repo and version-pinned.
5. Subscribe to the OpenClaw security advisory list (same-week patch SLA).
6. Run `openclaw doctor --fix` to migrate `HEARTBEAT.md` to database-backed scratch (Rec 2). Confirm no HEARTBEAT.md remains in use.
7. **Write the update procedure into RUNBOOK.md** (§11.2): drain the queue → snapshot → update on extended-stable → `openclaw doctor` → re-verify → resume, plus the same-week security-patch variant. There's no golden set yet, so "re-verify" means re-running Phase 9's checklist until the feature build produces one. Tahoe note: re-grant Full Disk Access and Automation after major macOS updates — the imsg bridge health monitor exists precisely because updates break them.
8. Baseline `openclaw security audit --deep`.

**Done when:** the audit reports zero unjustified findings; version is pinned; no skill directory contains anything not in the repo; the update procedure is in RUNBOOK.md.

## Phase 6 — OpenClaw configuration

*Spec: §4.2–4.3, §4.6.4, Phase 1 (scheduling).*

1. `openclaw.json` in the repo (no secrets in it — pointers only).
2. Register LM Studio (`http://localhost:1234/v1`) as the model provider; map the tiers.
3. **No cloud tier.** TIER-C (Cloud Claude via API key) is not configured: no API key on the machine, and `api.anthropic.com` stays off the egress allowlist. The spec's cloud guards — `block_cloud_if_tax_engagement`, `block_cloud_if_unredacted_pii`, reason-code gating — are therefore not built, and **must be built before any cloud tier is ever added**. Record this in DECISIONS.md so nobody adds a key later and inherits an unguarded path.
4. Define the agents (simplified single-Mac topology per the meeting): an orchestrator, a **zero-tool ingest-sandbox** (verify its tool schema is empty), and a quarantined drafting agent. Per-entity agents can be added later — the topology must allow it without re-architecture.
5. Cron/heartbeat scheduling declared in **America/New_York** (never UTC offsets — DST safety, Rec 21).

**Done when:** the gateway starts clean under the `lms` user; the ingest-sandbox provably has no tools; a scheduled heartbeat fires at the declared local time, **and** the scheduler's computed next-fire time for a date past the November DST boundary still lands on the declared local hour.

## Phase 7 — Control channel (Telegram)

*Guide: MAC_SETUP_GUIDE.md §8, §10. Spec: Q157, §8.9, §0.3.*

1. Create the bot via @BotFather; enable 2FA on the Telegram account (**C2**).
2. Store the token in the macOS Keychain (`security add-generic-password -a lms -s lms/telegram-bot-token -w '<TOKEN>'`) — never in a file, never in the repo.
3. Pair Matthew's Telegram account with the gateway.
4. Wire `/halt` — the sole **remote** kill switch now that the menu-bar app is descoped (§0.3); the maintenance agent's local command is the on-machine equivalent (§8.9). Verify it stops the gateway in ≤ 10 s.
5. `/halt` becomes page 1 of RUNBOOK.md.

**Done when:** a message from the phone reaches the gateway and gets a reply; `/halt` works within 10 seconds and is documented on page 1.

## Phase 8 — Network & security hardening

*Guide: MAC_SETUP_GUIDE.md §5–6, §8. Spec: Q212–Q216, §8.4 (enforce below the agent), §11.4.*

1. Tailscale up (**C3**); **no public ports, ever**. Verify from an outside network (phone on cellular) that nothing is reachable except over Tailscale.
2. **Remote-access decision — settle this before default-deny goes live (C4).** RustDesk is the working path today (meeting, 00:26:35). Either:
   - (a) keep it, allowlist its relay hosts, and record in DECISIONS.md that it's a third-party relay sitting outside the Tailscale-only posture and must be removed at handover (§12.4, Q212); or
   - (b) drop it for Tailscale + macOS Screen Sharing, which is what Q213 actually implies. **Recommended.**

   Either way, confirm you can still reach the machine from outside *before* you end the session that turns on default-deny.
3. Egress default-deny outbound (Little Snitch preferred; `pf` as fallback). Allowlist only what this build actually calls — Tailscale, the restic off-site target, Homebrew/npm during builds, and the remote-access path from step 2. The guide's §6 list names services that aren't in the 7-day scope (Twilio, SimpleFIN, QBO, Retell, `api.anthropic.com`); do **not** pre-open them. Keep the list as `config/egress-allowlist.txt` in the repo.
4. Secrets sweep: everything in Keychain, no plaintext `.env` anywhere, repo contains pointers only.
   - **`gateway.auth.token` migration (carried from Phase 5).** `openclaw doctor` flags it as a plaintext secret in `openclaw.json`. `openclaw secrets configure` only offers env refs, so the fix is three-part: store the token via `security add-generic-password`, have `~/Library/LaunchAgents/ai.openclaw.gateway.plist` export it at launch, then set `gateway.auth.token` to the env ref. Must land before agents gain filesystem tools.
5. **State backup (§11.4, C6).** Nightly restic snapshot of `~/.openclaw/`, `ops/`, the LaunchDaemon/LaunchAgent plists, and the repo checkout → the RAID mirror volume **and** one off-machine target (B2/S3 — there's no VPS). launchd, `America/New_York`. Test a restore to a scratch directory now, not later. The LMS database and filed artifacts are Day-6 feature work; this covers the machine's own state so a rebuild doesn't start from zero.
6. **Audit cadence (Q216).** A launchd job runs `openclaw security audit --deep` weekly; run it by hand on every config change.
7. Re-run `openclaw security audit --deep` after all of the above.

**Done when:** external port scan finds nothing; a non-allowlisted outbound connection is blocked and logged; remote access still works; a restic restore opens; the audit is clean.

## Phase 9 — Verification & acceptance

*Guide: MAC_SETUP_GUIDE.md §11 (Mac-only subset). Spec: §10.3.*

- [ ] Phone → gateway round trip over Tailscale
- [ ] External scan: nothing reachable outside Tailscale
- [ ] Remote access still reaches the machine from an outside network *after* default-deny
- [ ] `openclaw security audit --deep`: zero unjustified findings
- [ ] **Warm reboot** (`sudo fdesetup authrestart`): wired-limit daemon, LM Studio server, health ping, and the OpenClaw gateway all come back with no manual steps
- [ ] **Cold power-loss boot** (pull the plug): machine reaches the FileVault unlock screen, and the documented recovery procedure brings every service back — record how long it takes and who has to act
- [ ] A model call on TIER-L1 completes locally at **p95 TTFT < 3.5 s** (§10.3) with **zero network egress** (verify with packet capture)
- [ ] Kill LM Studio by hand: the 5-minute health ping detects it, reloads, and alerts
- [ ] A scheduled job's next-fire time across the DST boundary still lands on the declared local hour
- [ ] `/halt` stops the gateway in ≤ 10 s
- [ ] `restic restore` to a scratch directory yields a working `~/.openclaw`

**Done when:** every box is checked in one sitting, cold boot included.

---

## Deliberately not here

These are decisions, not oversights. Each says why.

| Item | Why it's out |
|---|---|
| UPS + clean-shutdown integration (spec Phase 0, Rec 27) | Hardware purchase, not yet made. Phase 1's power-recovery procedure is the interim mitigation — and it stays useful once the UPS arrives. |
| 2× hardware security keys on admin accounts (Q214, N8) | Purchase; and the meeting moved the opposite way on 2FA entirely. Phase 1 sends the written recommendation and records whichever way it lands. |
| Model benchmarking — 2–3 candidates per tier against a 50-item labeled set (spec Phase 1) | No labeled set exists until the feature build's data layer. The *tier* is normative, the model name isn't, and re-benchmarking is a quarterly ops item (§11.8). |
| DMARC ramp on mrecai.com / mleca.com / atlase.ai (spec Phase 0, Rec 16) | Pure DNS work with no Mac footprint. Belongs with the email phase. |
| Gmail OAuth durability (spec Phase 0, Rec 22, Q24) | The *decision* — app password vs. internal Workspace OAuth app — is a Phase 1 blocker (item 8, D-008) and gates Day 3. The *configuration* is email-phase work. |
| VPS / rescue instance (spec Phase 13) | Removed at the Aug 5 meeting. |
| Storage-root layout, database schema, pipeline and agent skill content | Feature work — [BUILD_PLAYBOOK.md](BUILD_PLAYBOOK.md). |
| LMS data backup (database, sidecars, filed artifacts) | Day 6. Phase 8 backs up machine state only. |

## Channels and integrations — none configured here

Telegram (Phase 7) is the only channel this document sets up. Everything else the requirements mention, and where it stands — client prerequisites are tracked as C-items in [What's needed from Matthew](#whats-needed-from-matthew):

| Channel / service | Status |
|---|---|
| Email — Gmail API preferred, IMAP fallback (Q23–Q24) | Day 3 (C9, C10, C11). Only its OAuth-durability decision touches setup. |
| iCloud Drive (personal) + Google Drive Shared Drives per entity (DEC-8) | Day 3–4 (C11, C12). Systems of record; no setup-phase configuration. |
| iMessage (read-only), photographed mail, call transcripts | Day 4 (C13, C14, C15) — BUILD_PLAYBOOK.md. iMessage and iCloud are blocked on the service-account decision in Phase 1 item 7. |
| **WhatsApp** | Roadmap in the spec itself (§0.3, Rec 36 descope) and out of the 7-day scope. If ever enabled: dedicated secondary number for personal, Twilio Business API for business (Q160–Q161). |
| Slack / Teams / Discord / Signal | "No in v1" (Q167). |
| LinkedIn | No compliant path (Q168). |
| Twilio, SimpleFIN, QuickBooks Online, Retell, PandaDoc/DocuSign, Dropbox, web search | Spec Phases 8–14; all out of the 7-day scope. Keep them off the egress allowlist until something actually calls them. |
