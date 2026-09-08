# DECISIONS — MRE LMS

The decision log for this engagement. One row per decision, never deleted — superseded entries are marked, not removed.

**Numbering:** `D-nnn` are decisions made in *this* engagement. `DEC-n` and `Rec n` / `Q n` references point at the v3.0 developer specification and are cited, not re-decided here.

**Where this lives:** in this folder until the private remote exists (C1 → Phase 3), then it moves into the repo root and this copy is deleted. Do not fork it.

**Status values:** `Decided` · `Decided — pending written confirmation` · `Open` · `Superseded by D-nnn`

---

## Decided

### D-000 — No VPS; Mac-only architecture
**Status:** Decided · Aug 5 2026 meeting · Matthew
Hostinger is cancelled and the spec's Phase 13 rescue instance is removed. Everything runs on the Mac Studio.
**Consequences:** no off-site backup target comes free — one must be purchased (D-012 / C6). No external watchdog, no webhook receiver, no rescue path if the Mac is down. Accepted for v1.
**Blocked on:** C17 — confirm nothing needed remains on the Hostinger box *before* it is deleted.

### D-001 — Scope reduced to the 7-day core
**Status:** Decided — pending written confirmation (C18)
Per Matthew, 00:27:47: *"I don't need it to do business operations… I just need it to determine if it's business, which business, save it, and then let me know what I need to do."* Build ingest → classify → file → surface → email hygiene. Everything else in spec v3.0 becomes roadmap.
**Consequences:** [LMS_BUILD_STEPS.md](LMS_BUILD_STEPS.md) is no longer the plan of record. Deferred: QBO, ledger, commissions, renewals, tax-chase, voice profiles, WhatsApp, SimpleFIN, shadow-mode graduation.
**Why pending:** a verbal descope of a 514–694 h spec down to 7 days protects nobody until it is in writing. Sign-off requested in [client/SCOPE_SIGNOFF.md](client/SCOPE_SIGNOFF.md).

### D-002 — Zero third-party skills
**Status:** Decided · spec §11.7, carried forward
No ClawHub installs, ever. Every SKILL.md is hand-authored in-repo and version-pinned.

### D-003 — No cloud model tier
**Status:** Decided
TIER-C (Cloud Claude) is not configured: no API key on the machine, `api.anthropic.com` stays off the egress allowlist.
**Consequences — read before reversing this.** The spec's cloud guards (`block_cloud_if_tax_engagement`, `block_cloud_if_unredacted_pii`, reason-code gating, DEC-3/DEC-10 tax-and-privileged block) are *not built*. Adding a key later without building them first creates an unguarded path from client NPI and tax data to a third party. **Any future cloud tier requires those guards first.**

### D-004 — Local inference only
**Status:** Decided
All model calls go to LM Studio at `http://localhost:1234/v1`, loopback-bound. Verified by packet capture at acceptance (Phase 9).

### D-005 — Telegram is the sole control and approval surface
**Status:** Decided · spec §0.3 descopes the menu-bar app
`/halt` is the only remote kill switch; the maintenance agent's local command is the on-machine equivalent. `/halt` is page 1 of RUNBOOK.md.
**Blocked on:** C2 — Telegram account paired, 2FA on, owner user id for `commands.ownerAllowFrom`.

### D-006 — Repository on a private remote Matthew owns
**Status:** Decided · spec §12.4
Not a contractor account. Signed commits. Secrets never committed — the repo holds pointers only.
**Blocked on:** C1. This gates Phase 3, and Phase 3 gates every phase after it.

### D-007 — Filing naming convention follows spec §6.2 verbatim, including AMOUNT
**Status:** Decided · Aug 6 2026 · resolves a conflict between the planning docs

```
YYYY-MM-DD__ENTITY__CATEGORY__COUNTERPARTY__DESCRIPTOR__AMOUNT__hash8.ext
```

- `YYYY-MM-DD` — the **document's** date, not the ingestion date (ingestion date lives in metadata)
- `ENTITY` — registry ID **without the prefix** (`B_CHS` → `CHS`)
- `CATEGORY` — top-level category, uppercase
- `COUNTERPARTY` — slug, strict `[a-z0-9-]`, ≤32 chars
- `DESCRIPTOR` — 2–5 hyphenated words, same allowlist, ≤40 chars
- `AMOUNT` — `USD1234-56` or `NOAMT`
- `hash8` — first 8 chars of the file's sha256

200-char cap, descriptor truncated first; full untruncated values always in the database.
Example: `2026-07-31__CHS__COMMISSIONS__foundation-risk-partners__july-renewal-statement__USD14208-33__a3f91b2c.pdf`

Sidecars (§6.3): every filed artifact gets `<name>.meta.json`; where OCR ran, also `<name>.txt`.

**Why it matters:** [BUILD_PLAYBOOK.md](BUILD_PLAYBOOK.md) omitted `AMOUNT`, [LMS_AGENT_PROMPTS.md](LMS_AGENT_PROMPTS.md) included it. Getting this wrong means a re-file of the whole tree when the full system arrives — which is the one thing the convention exists to prevent. BUILD_PLAYBOOK.md corrected Aug 6.

### D-015 — OpenClaw attack surface minimised to 3 plugins / 0 bundled skills
**Status:** Decided · Aug 6 2026 · applied on the Mac

OpenClaw was onboarded with general-purpose assistant defaults. Measured baseline was **50 of 67 plugins enabled and 16 of 53 skills ready**. That is the correct default for a personal assistant and the wrong one for a system whose job is ingesting hostile email.

**End state: 3 plugins enabled** — `lmstudio` (the model backend), `document-extract` (Day 4 OCR/attachment text), `memory-core` (backs the enabled `session-memory` hook). **0 bundled skills ready** — correct, not an overshoot: D-002 says every SKILL.md is hand-authored in-repo, so the right number of bundled skills is zero and ours are added deliberately.

**Disabled and why:**
- **31 cloud model providers** (`anthropic`, `openai`, `google`, `mistral`, `cohere`, `meta`, `microsoft`, `xai`, `together`, `openrouter`, `litellm`, `huggingface`, `deepgram`, `elevenlabs`, `azure-speech`, `voyage`, and others). D-003/D-004 were previously enforced only by the *absence of an API key*. They are now enforced by the absence of a code path.
- **`clawrouter`** — a second, independent `text-inference` capability. `lmstudio` provides its own `text-inference: lmstudio`, so clawrouter was a parallel inference path with nothing left to route to.
- **`bonjour`** — advertised the gateway over mDNS, contradicting the Tailscale-only posture.
- **`browser`, `web-readability`, `canvas`** — page fetching and HTML eval in a system that reads untrusted email is the trifecta.
- **`device-pair`** — Telegram is the sole control channel (D-005).
- **`file-transfer`, `phone-control`, `opencode`, `opencode-go`, `ollama`, `sglang`, `vllm`, `talk-voice`, `tts-local-cli`, `vydra`** — unused surface.
- **7 escalation skills**: `clawhub` (**installs third-party skills — a live D-002 violation**), `skill-creator`, `node-connect`, `node-inspect-debugger`, `python-debugpy`, `browser-automation`, `canvas`.
- **9 remaining ready skills**: `diagram-maker`, `healthcheck`, `meme-maker`, `notion`, `spike`, `taskflow`, `taskflow-inbox-triage`, `video-frames`, `weather`.

**The failure mode this exposed — read this before the next update.** `openclaw.json` disabled ~38 skills *by name*. There are 53. The 16 that were ready were exactly the ones nobody had enumerated. **OpenClaw offers no default-deny for skills or plugins** — control is per-name only (`openclaw plugins enable/disable`, `skills.entries.<name>.enabled`); `skills curator` is only pin/restore/status/unpin. So this is a denylist, and **every OpenClaw update can ship new skills that default to ready**. RUNBOOK.md's update procedure must diff `openclaw skills list` and `openclaw plugins list` before and after every update and explicitly disable anything new. Without that check this hole reopens silently.

**Also note:** `openclaw skills install` and `openclaw skills search` remain available at the CLI regardless of the `clawhub` skill being disabled. Disabling the skill closes the *agent-reachable* path; the *operator-reachable* path stays open by design. D-002 therefore requires operator discipline in RUNBOOK.md, not just a config flag.

**Re-enable deliberately when needed:** `telegram` (Phase 7) and `imessage` (Day 4) plugins are currently disabled.

### D-016 — `gateway.auth.token` moved to Keychain via an exec SecretRef
**Status:** Decided · Aug 7 2026 · applied

The token was plaintext in `openclaw.json` (flagged by both `openclaw doctor` and `secrets audit`). `openclaw secrets configure` offers **env / file / exec** providers — no native Keychain provider — so the fix is an **exec** provider that shells out to `security` at resolve time. The token now lives only in the login Keychain (`-a lms -s lms/gateway-auth-token`); the config holds a reference. This is better than the env-ref workaround [PHASES.md](PHASES.md) anticipated: the value never enters an environment variable or the LaunchAgent plist, so `ai.openclaw.gateway.plist` needed no edit at all.

Provider: command `/usr/bin/security`, args `["find-generic-password","-a","lms","-s","lms/gateway-auth-token","-w"]`, trusted dirs `/usr/bin`, JSON-only `No` (the command returns a raw string), symlinks `No`.

**`allowInsecure` command-path checks is set to `true` — deliberately.** OpenClaw requires the exec command to be owned by the current user (uid 501); `/usr/bin/security` is owned by root, so the strict check rejects it. The alternative was a user-owned wrapper script. That would be *worse*: a script under `~/` is writable by anything running as `mleca` — exactly the injected-agent scenario this build defends against — whereas `/usr/bin/security` requires root to modify. Combined with `trusted dirs = /usr/bin`, the provider can only execute a root-owned, Apple-signed binary from a pinned directory. The flag's name describes the skipped assertion, not the resulting posture. **Do not "fix" this by pointing it at a wrapper script.**

**Token was rotated on Aug 7** — the original was printed to a terminal during migration and had to be treated as exposed. Rotation is trivial; the lesson is the habit. Read-backs must always end `>/dev/null && echo OK`. The Telegram bot token (Phase 7) and mailbox credentials (Day 3) are far more sensitive than a loopback-bound gateway token, and the same slip there would be materially worse.

**Verify after every reboot.** Resolution happens at gateway start, from the *login* Keychain, and the gateway is a user LaunchAgent — so both come up at login and that ordering is consistent. But a Keychain prompt at boot would mean the gateway silently fails to start unattended. Phase 9's warm-reboot and cold-boot tests must confirm the gateway resolves the token with no interaction.

**Audit delta:** `browser control` went `enabled` → `disabled`. Remaining: `tools.elevated: enabled` (open), plus two warnings — `gateway.trusted_proxies_missing` (**justified**: loopback-only, no reverse proxy, accepted not fixed) and `gateway.probe_failed / missing scope: operator.read` (blocked on C2).

### D-009 — LMS runs under Matthew's macOS user (option a)
**Status:** Decided · Sept 7 2026 · developer, with client authorisation to operate his accounts
**Supersedes the recommendation in [PHASES.md](PHASES.md) Phase 1 item 7, which was option (b).**

iMessage (`~/Library/Messages/chat.db`, C13) and iCloud Drive (C12, C14) are bound to the logged-in macOS session and are invisible from a separate account. Rather than run a split reader daemon, everything runs under Matthew's user.

Note this was already the observed state — the Aug 7 commit recorded the gateway running as uid 501 (`mleca`), not `lms`. This decision makes that deliberate instead of accidental.

**What this costs — state it plainly.** Privilege separation is gone. The spec's §4.1 principle 8 ("enforce below the agent") loses its outermost layer: an agent that is successfully injected inherits Matthew's full session reach — his Messages, his iCloud, his Keychain items, his Drive. Option (b) existed to contain exactly that.

**Compensating controls, now load-bearing rather than defence-in-depth.** Each of these was a second line under option (b). Under option (a) there is no first line, so none of them may be relaxed without revisiting this decision:

1. The classifier — the only component that reads hostile text — has **zero tools** (`lms-classifier/SKILL.md`, asserted at startup and in `tests/test_hard_stops.py`).
2. Filing, naming, dedupe, and notification are deterministic code. No model decides where a document goes or whether it is kept.
3. The hard stops in `config/rules.yaml` are enforced in code and asserted in tests — no delete, no mark-read, no send without an explicit Telegram tap, no money, no signing.
4. `actions_log` is append-only at the schema level (triggers, not convention).
5. Default-deny egress (Phase 8) becomes the containment boundary that the user account no longer provides. **It is now mandatory, not a hardening nicety.**
6. Untrusted content is wrapped, HTML-stripped, and remote images blocked before any model sees it.

**Revisit this if** the drafting agent ever gains filesystem tools, a cloud tier is added, or the system starts handling attorney-privileged material. Any of those three changes the risk enough that option (b) should be rebuilt.

### D-017 — Client credentials were transmitted in plaintext; full rotation required
**Status:** OPEN — rotation not yet performed · Sept 7 2026 · **treat as an incident, not paperwork**

The client supplied *System Setup and Access Requirements* (PDF, authored in ChatGPT Canvas) containing plaintext passwords for **seven accounts**: GitHub, five email accounts, and Backblaze — plus the password for the newly created OpenClaw macOS user.

**Exposure path:** typed into a third-party AI product → exported to PDF → placed in a git working tree that did **not** ignore it → read into an AI assistant session. Any one of those is disqualifying on its own.

**This is the second occurrence.** C7 already carries "confirm the keys exposed on 2026-08-06 have been rotated" — still unconfirmed. D-016 recorded the same lesson after the gateway token was printed to a terminal: *"Rotation is trivial; the lesson is the habit."* The habit has not formed.

**Required actions, none optional:**

| # | Action | Status |
|---|---|---|
| 1 | Rotate all 7 account passwords | Pending |
| 2 | Replace the OpenClaw macOS user password — it was set to `123456` | Pending |
| 3 | Confirm the Aug 6 exposure (C7) was rotated | Pending |
| 4 | Delete the PDF from disk once values are in Keychain / a password manager | Pending |
| 5 | Verify the PDF never entered git history | Ignore rule added Sept 7; history clean at `a41ebc9` |
| 6 | Identify the `rescueadmin` macOS account | Pending |

**On item 2 specifically.** D-009 above makes the macOS user account the container for the entire system. A six-digit sequential password on that account means the container is decorative. This must be fixed before that account runs anything.

**Going forward:** credentials are transmitted through a password manager share or entered by Matthew directly. Not a document, not a chat message, not a PDF. This supersedes nothing in D-008 — it is the delivery mechanism, which D-008 never addressed because nobody imagined it needed saying.

### D-018 — Account cleanup: three abandoned accounts removed (closes C21)
**Status:** Decided and applied · Sept 8 2026 · verified on the machine

Recon found **four** accounts where the docs assumed two. Three were abandoned setup attempts, created one per day and never used again:

| uid | account | created | what it actually was |
|---|---|---|---|
| 501 | `mleca` | — | Matthew's. The only real account |
| 502 | `OpenClaw` | Aug 4 14:06 | Admin, FileVault-enabled, password `123456`. Its entire shell history was one line: `curl -fsSL https://openclaw.ai/install.sh \| bash`. Used for six minutes to run the installer |
| 503 | `rescueadmin` | Aug 5 11:37 | Temporary admin for a home-folder relocation. User record already deleted; home left behind. Only real file was a Perplexity export, `home-folder-relocation-adjudication-and-guide-v2.pplx.docx` |
| 504 | `lms` | Aug 6 01:45 | The non-admin service account from Phase 1 item 5. Stub home, 924 KB, one 3-byte file, never logged into |

**This is the archaeology behind D-009.** The privilege-separation design was attempted three times in three days and abandoned each time, which is why the gateway ended up running as `mleca`. D-009 records that as a deliberate choice; this records that it was also the de-facto outcome of three failed attempts.

**The finding that mattered:** `OpenClaw` was an **administrator** *and* **FileVault-enabled** with the password `123456`. That combination means anyone with the password reaches full disk and full admin — and it had been sitting there since Aug 4. It was not the account running anything: the gateway runs as `mleca` (uid 501) with config in `/Users/mleca/.openclaw`.

**Applied:** both home directories archived to `/Volumes/MacStudioHD/_pre-lms-archive/` with a 3,757-line manifest before deletion (`Library` excluded — the iCloud placeholder tree hangs `tar`). Account deleted via `sysadminctl -deleteUser`; the other two homes removed.

**Verified after:** `fdesetup list` → `mleca` only. Admin group → `root mleca _mbsetupuser`. Four accounts became one.

**Residue:** `/Users/.pending_delete_*` cannot be fully removed while the `bird` daemon holds the iCloud placeholder directories. It holds no data, but it is owned by **uid 502** — a uid macOS may reuse for the next account created, which would silently inherit ownership. Clear it after the next reboot.

**Superseded assumption:** C21 asked "does `rescueadmin` belong to the client?" The answer is that it was never a client account at all — all three were developer setup debris.

### D-019 — C5 closed: the RAID is one already-encrypted 12 TB volume
**Status:** Decided · Sept 8 2026 · observed, no client approval needed

The client's document named three volumes to encrypt — **Bulk**, **MacStudioHome**, **Vault**. None of them exist. What is actually attached:

```
disk6-disk9   4 x 4 TB physical members
disk10        Apple_APFS 12.0 TB  (the array as one device)
disk11s1      MacStudioHD  FileVault: Yes (Unlocked)  11 TB free, empty
```

So there is nothing to encrypt and no approval to obtain — C5 and D-011 are closed by observation. The client was describing an older layout, or misremembering.

**Archive root is `/Volumes/MacStudioHD/LMS`.**

Two consequences worth recording:

1. **4 × 4 TB presenting as 12 TB is single-disk parity — redundancy, not backup.** D-012 is unchanged: one flood, one theft, or one bad write still takes everything. The off-site target remains required.
2. **The model cache belongs on MacStudioHD, not the boot volume.** The internal disk has 231 GB free against a ~150 GB resident model stack. It fits, barely, with no headroom for growth or a second tier.

### D-020 — Interim remote is a contractor-owned repo; must be transferred before handover
**Status:** Decided — **temporary, with a close condition** · Sept 8 2026

The repository is at `https://github.com/WajidAliShah2004/mre-lms.git` — the developer's account, not Matthew's. This **deviates from D-006 and C1**, which require a private remote the client owns "not a contractor account (§12.4)."

**Why it was accepted:** Day 1–2 code had no home and every phase after Phase 3 writes into the repo. Waiting on the client to create a repo would have blocked the build for the sake of a step that is reversible in thirty seconds.

**Why the deviation is tolerable but not fine:**
- The repo holds **pointers only** — no credentials, no client documents, no filed artifacts. The exposure if the account were compromised is the design, not the data.
- GitHub's **Settings → Transfer ownership** moves the repo in place, preserving full history, issues, and commit attribution. This is not a "re-create it later" debt.

**What is genuinely at risk while this stands:** the exact failure C1 existed to prevent — the client's system living in a contractor's account. If this engagement ended today, Matthew would own a Mac he cannot rebuild from source.

**Close condition — one of these, before handover, not at handover:**
1. Transfer the repo to Matthew's `Mleca18` account (preferred: preserves everything), **or**
2. Matthew creates `Mleca18/mre-lms` and this becomes a mirror push target, with the contractor copy deleted at handover.

**Also required regardless:** confirm the repository is **Private**. A public repo here would put the entity registry, taxonomy, routing rules, and the full security design — including which defences exist and which do not — on the open internet.

**Do not close D-020 by deleting it.** It closes when the transfer is done and verified by Matthew being able to clone it himself.

### D-021 — Phase 6 applied; the zero-tool guarantee is global, not per-agent
**Status:** Decided and applied · Sept 8 2026 · verified on the machine

**What was wrong first.** The Phase 6 config was written from the v3.0 specification's vocabulary rather than from OpenClaw's actual schema. It was rejected by the validator (`Unrecognized key: "type"`), which is the good outcome — nothing was written. Reading `openclaw config schema` showed the differences were structural, not cosmetic:

| Assumed | Actual |
|---|---|
| `agents.<name>.tools.allow` | **Does not exist.** Tool policy is global only |
| `models.providers.<id>.type` | `api`; and the lmstudio plugin already advertises `text-inference` |
| `scheduling.*` | `cron`, with jobs in `cron.store`, managed by CLI |
| per-agent topology in config | `agents.list` (array) + `agents.defaults.skills` |

**The correction improved the design.** Per-agent tool lists protect the agents someone remembered to configure. What OpenClaw actually offers is stronger:

```
tools.profile = minimal     baseline (already set, D-015)
tools.allow   = []          absolute — "replaces profile-derived defaults"
tools.deny    = [bash, exec, shell, browser, canvas, file-transfer]
                            blocks "even when profile or provider rules would allow"
```

Three independent mechanisms covering **every** agent, including ones a future update introduces. Under D-009 there is no privilege boundary underneath this, so it is the containment boundary rather than a defence-in-depth layer.

**Also applied — four chat commands pinned off.** All four already defaulted to `false`; they are now explicit, because D-015 established that OpenClaw has no default-deny and an update can ship changed defaults silently. `commands.bash` "runs host shell commands"; under D-009 a chat message reaching it would have Matthew's entire session.

**Deliberately withheld: `models.mode: "replace"`.** It dry-run-validated. It would be a stronger form of D-003 — removing the built-in cloud catalog rather than disabling 31 plugins individually. It was **not** applied because `models.providers` is empty: the lmstudio plugin advertises a capability, but whether that populates the model *catalog* is not something the schema answers. If it does not, "replace" yields an empty catalog that fails at first inference rather than at restart — the gateway would look healthy and the failure would surface hours later. Revisit once a provider entry exists and a test inference has run.

**Verified after restart:** gateway came back clean with an absolute empty allowlist; plugins 3 loaded / 64 disabled / 0 errors; `security audit --deep` 0 critical, 2 warn (both known and justified); `verify_setup.py` all six checks pass.

**On D-016 — partial confirmation, and worth stating precisely.** Without `--allow-exec`, doctor prints "health probes skipped because gateway credentials use an exec SecretRef". With the flag that message is gone, so the exec provider was invoked and the Keychain reference resolves. But the audit still reports `probe_failed / missing scope: operator.read`, and doctor separately reports no command owner — **those are the same finding**, and both clear in Phase 7 with C2.

This is *not* the verification D-016 asked for. Resolving on a warm restart, with the login Keychain already unlocked by an interactive session, is not the same as resolving at boot before anyone has logged in. **The cold-boot test in Phase 9 remains the only real proof**, and it has still never been run.

**Repo correction.** `openclaw/agents/agents.fragment.json` previously described a config shape that does not exist — worse than no file, because a future maintainer would have believed it. It is now marked as documentation of *intent*, with the real mechanism recorded alongside. The config actually applied is `ops/phase6a.patch.json5`, committed verbatim so the machine and the repo cannot drift.

### D-014 — Call transcripts are the first thing cut if the week slips
**Status:** Decided (contingency) · [GUIDELINES_7DAY_BUILD.md:40](GUIDELINES_7DAY_BUILD.md:40)
Email + photographed mail + the to-do list are the visible value. Day 4's call-transcript ingestion (C15) goes first, before anything else is touched.

---

## Open — these block work

### D-008 — Credential and MFA posture
**Status:** OPEN · blocks Day 3 · client item **C9**
The Aug 5 meeting agreed to shared email passwords with 2FA disabled. Written recommendation to the contrary sent as [client/CREDENTIALS_RECOMMENDATION.md](client/CREDENTIALS_RECOMMENDATION.md): keep 2FA on, use Google app passwords or an internal Workspace OAuth app.
**No mailbox connects until this is answered.** If Matthew insists on the original approach, record his acknowledgement here verbatim, store credentials only in the macOS Keychain, and rotate + re-enable 2FA at handover (spec §12.4).

> **Answer (record here):**
> **Date:**

### D-010 — Remote-access path
**Status:** OPEN · must be settled **before default-deny egress goes live** · client item **C4** · [PHASES.md:170](PHASES.md:170)

- (a) Keep RustDesk, allowlist its relay hosts, and accept a third-party relay sitting outside the Tailscale-only posture — removed at handover (§12.4, Q212).
- (b) **Tailscale + macOS Screen Sharing (recommended)** — what Q213 actually implies.

**Ordering hazard:** confirm the machine is still reachable from an outside network *before* ending the session that turns on default-deny. Getting this wrong locks you out of a machine you may not be standing next to.

**Raised stakes as of Aug 7.** RustDesk is no longer just occasional access — it is the **build channel**. All development is being relayed through it (commands and files hand-carried between the Windows workstation and the Mac). Losing it mid-build doesn't cost a support session, it costs the remaining build days.

Two consequences:
- Nothing touches egress filtering (Phase 8) until this decision is made and the replacement path is *verified working from an outside network*. Not "configured" — verified.
- If option (b) wins, stand up Tailscale + Screen Sharing and confirm it end-to-end **while RustDesk still works**, then cut over. Never the reverse order.

> **Chosen:**
> **Date:**

### D-011 — RAID volumes and encryption
**Status:** OPEN · blocks Phase 1 item 4 · client item **C5**
Which volumes hold LMS data, models, and the repo — and approval to encrypt in place. Per the meeting (00:26:35), "some of it's encrypted, some of it isn't." FileVault covers the boot volume only. Record which volumes are encrypted and which are deliberately not, and why.

> **Volumes:**
> **Approved to encrypt in place:**
> **Date:**

### D-012 — Off-site backup target
**Status:** OPEN · blocks Phase 8 item 5 · client item **C6**
D-000 removed the VPS, so there is no off-machine backup destination. B2 or S3 account + credentials needed. Nightly restic → RAID mirror **and** this target. Restore tested at Phase 8, not later.

> **Target:**
> **Date:**

### D-013 — Day-7 acceptance date
**Status:** OPEN · decide before Day 3
Day 0 is Thu Aug 6 2026; Days 1–7 land on Aug 7, 10, 11, 12, 13, 14, **17**. Matthew is offline **Aug 20 – Sept 1** and Day-7 acceptance needs him present. That is zero slack against his departure.
Decide now whether Aug 18–19 is the designated buffer, or whether Aug 17 is hard and D-014's cut fires at the first sign of slip.

> **Decided:**
> **Date:**

---

## Deferred purchases (decisions not deferred, hardware is)

| Item | Note |
|---|---|
| UPS, ~$150–250 (C19) | Phase 1's power-recovery procedure is the interim mitigation, and stays useful once the UPS arrives. |
| 2× hardware security keys, ~$60 (C20) | The *purchase* is deferred. The MFA posture decision (D-008) is not. |
