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
| 2 | Replace the OpenClaw macOS user password — it was set to a six-digit sequential | Pending |
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
| 502 | `OpenClaw` | Aug 4 14:06 | Admin, FileVault-enabled, a six-digit sequential password. Its entire shell history was one line: `curl -fsSL https://openclaw.ai/install.sh \| bash`. Used for six minutes to run the installer |
| 503 | `rescueadmin` | Aug 5 11:37 | Temporary admin for a home-folder relocation. User record already deleted; home left behind. Only real file was a Perplexity export, `home-folder-relocation-adjudication-and-guide-v2.pplx.docx` |
| 504 | `lms` | Aug 6 01:45 | The non-admin service account from Phase 1 item 5. Stub home, 924 KB, one 3-byte file, never logged into |

**This is the archaeology behind D-009.** The privilege-separation design was attempted three times in three days and abandoned each time, which is why the gateway ended up running as `mleca`. D-009 records that as a deliberate choice; this records that it was also the de-facto outcome of three failed attempts.

**The finding that mattered:** `OpenClaw` was an **administrator** *and* **FileVault-enabled** with the a six-digit sequential password. That combination means anyone with the password reaches full disk and full admin — and it had been sitting there since Aug 4. It was not the account running anything: the gateway runs as `mleca` (uid 501) with config in `/Users/mleca/.openclaw`.

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

### D-022 — Under constrained decoding this model returns the answer in `reasoning_content`, not `content`
**Status:** Decided and fixed · Sept 8 2026 · **measured, not assumed**

Running one real document through the pipeline on the Mac exposed a defect that 105 passing unit tests could not see, and that would have made the classifier fail on **every single document**.

Measured against `qwen3.6-35b-a3b-mlx` via LM Studio:

| Request | `message.content` | `message.reasoning_content` |
|---|---|---|
| no `response_format` | the answer | the thinking |
| **with `json_schema`** | **empty string** | **the constrained schema JSON** |

The runtime routes the constrained decode into the reasoning channel and leaves `content` blank. The client read only `content` — the documented, obvious field — so every classification returned `""`, failed to parse, and quarantined. The pipeline behaved correctly: `_quarantine()` caught it, nothing was filed, `UNASSIGNED` every time. It simply never worked.

**Why no test caught it.** Every test mocks the model, and a mock returns whatever shape the person writing it believes is correct. The wrong belief was in both the code and the tests, so they agreed with each other. This class of defect is only visible against the real runtime.

**Fix:** prefer `content`, fall back to `reasoning_content` when it is empty, and record which channel supplied the text on every `Completion`. Preferring content is right in both directions — if a future LM Studio release fixes this, the fallback silently stops firing.

Also hardened while in there, because reasoning models do all of these in practice:
- `<think>...</think>` blocks are stripped
- the first balanced `{...}` is extracted when prose surrounds it, brace-counted with string and escape awareness rather than by regex
- **truncation still fails loudly.** A model that hits `max_tokens` returns *nearly* valid JSON, and half-parsing it would file a document against whichever fields survived — worse than not filing, because it looks fine.

**Two things this run confirmed were already right.** Constrained decoding genuinely works: the model's `rationale` was cut off mid-word at exactly the schema's 200-character limit. And validation caught an invented entity — the model returned `entity_id: "B_XXX"`, which satisfies the schema's regex but is not in `entities.yaml`, and the registry check quarantined it rather than filing against a business that does not exist.

**The general lesson, and the reason to keep doing this.** The units were fine. What was broken was the seam between the code and the runtime, and seams are invisible to unit tests by construction. Two bugs found in one afternoon on the Mac — this one, and the `--once` stability counter — both at seams, neither reachable from the workstation.

### D-023 — A rule that decided the entity outranks the model's confidence, whether or not the model agreed
**Status:** Decided and fixed · Sept 8 2026 · **measured, not assumed**

The document immediately after the D-022 fix still quarantined. Reason recorded by `why.py`:

```
QUARANTINED   confidence 0.15 below 0.60
```

Not a parse failure this time. The document was 87 bytes — `ACME SUPPLY COMPANY / INVOICE 5588 / Date: 2026-09-02 / Bill to: MRECAI / TOTAL DUE: $2,310.00`. `alias_in_body` matched `MRECAI` and routed it to `B_MRE` deterministically, before the model was consulted at all. The model then also said `B_MRE` — but at confidence **0.15**, which is a reasonable thing to say about five lines of text.

The override in `_validate` read:

```python
if routed is not None and entity_id != routed.entity_id:   # only on DISAGREEMENT
```

So the rule's certainty was applied **only when the model contradicted it**. On agreement — the case where the evidence is strongest — the branch never fired, the model's 0.15 survived, and `ingest.py` quarantined a document whose entity was never in doubt.

**The asymmetry is the whole bug.** Agreement between an address match and a model is stronger evidence than disagreement, and it was the case being handled worse. Deterministic routing exists precisely so that thin, ambiguous, or hostile documents still file correctly; letting a diffident model veto it gives back the guarantee that routing was built to provide.

**Fix:** whenever routing decided the entity, `decided_by = "rule"` and `confidence = max(confidence, ROUTED_CONFIDENCE)` — unconditionally. The floor is 0.95 rather than 1.0 because the rule is certain about the *entity* while the rest of the row still came from a model, and that distinction should stay visible in the archive sidecar.

**Scope kept narrow.** The floor applies only on the routed path. With no rule matched the model *is* the decision, and a low confidence there is a real signal that a human should look — `test_an_unrouted_document_still_respects_the_model` pins that boundary.

**Tests added** (5, and one corrected). The corrected one is the interesting record: `test_sidecar_and_task_carry_what_the_filename_cannot` asserted the sidecar's confidence was the model's `0.93`, but its fixture is addressed to `matthew@mrecai.com` and was therefore routed all along. The old assertion was passing while describing behaviour that was wrong. It now asserts `0.95` **and** `decided_by == "rule"`, so the sidecar explains the number rather than just carrying it.

**Why no test caught it.** Every routing test used a *disagreeing* model, because disagreement is the dramatic case and the one the injection story is about. Nothing exercised agreement, so the branch that only fires on disagreement looked complete. The Mac supplied the boring case that the test suite never thought to.

**Third bug at a seam, third one found by running a real document.** Not a seam between code and runtime this time, but between two components that were each correct alone — routing knew the entity, the model reported its own uncertainty honestly, and the join discarded the better of the two.

### D-024 — A document nobody could read is quarantined, never classified
**Status:** Decided and fixed · Sept 8 2026 · **measured, not assumed**

Third live run, third quarantine — and the D-023 fix was correctly applied. Same reason: `confidence 0.15 below 0.60`. But the probe filed the *same invoice text* cleanly at 0.95.

The difference is what the probe does that the watcher does not: it sets `body=` explicitly.

`ingest_file` extracted text only when the suffix was in `ocr.IMAGE_SUFFIXES` — `.jpg .jpeg .png .heic .heif .tiff .tif .gif .bmp`. A `.txt` file matched nothing. **No reader ran, `art.body` stayed empty, and the classifier was handed a document with no content.** The model returned 0.15, which is the right answer to an empty document, and it quarantined against a floor.

So the symptom pointed at the model and the cause was two stages earlier, in a branch that simply had no case for the file it was given.

**The same hole swallows PDFs** — the single most common form of real scanned mail. Nothing in the pipeline read them either. That would have surfaced on Day 3 against live attachments rather than on a test invoice.

**Fix, in two parts.**

*Read text files.* `ocr.TEXT_SUFFIXES` and `read_text_file()` — not OCR, but the same `OCRResult` contract so callers don't branch. The sidecar records `engine: "plain-text"`, so the archive says which path read it.

*And the part that matters more:* **never ask the model about a document we failed to read.** `ingest_file` now checks for an empty body after extraction and quarantines with a reason that names the cause — `"no text extracted: PDF reading is not implemented yet"` — instead of passing nothing to a model and reporting whatever it says about nothing.

That check is the real fix. Reading `.txt` closes one gap; refusing to classify emptiness closes the whole class, PDF included. An empty body is also precisely where a confidently wrong answer costs most: with no content to be constrained by, whatever the model invents is unfalsifiable — no rule contradicts it, no validation catches it, and it files somewhere plausible.

**Eight existing tests failed on the change**, and every one deserved to. They handed the pipeline `.pdf` files containing plain-text bytes — a fiction nothing checked, because nothing ever tried to read them. The watched-folder fixtures are now `.txt`, which makes them honest and exercises the real extraction path; the three `run_ocr=False` cases now supply a `body`, which is what an email adapter actually does. **Second time in one day that correcting behaviour exposed tests that were passing while describing the wrong thing** (D-023 was the first).

One assertion also had to change meaning rather than value: `test_retired_files_are_not_rescanned` counted `*.txt` in the archive, and filing writes the extracted text beside the document — so with `.txt` fixtures the glob counted both and read as a double-file. It now counts `.meta.json`, one per filed artifact, which is what the test was always trying to say.

**Five new tests**, the load-bearing one being `test_an_unreadable_format_is_quarantined_before_the_model`: it wires in a model that *would* answer confidently and asserts it is never called.

**Why no test caught it.** Every ingest test either set `body=` directly or used `run_ocr=False`. Not one handed the pipeline a real file and let it do the reading — so the reading step, the only part that was broken, was the one part never exercised. The watched-folder tests came closest and still used a fake `.pdf` that no reader was ever going to open.

**Fourth bug from running one real document, and the pattern is now unmistakable.** Every one lived at a boundary the tests spanned by assumption: code-to-runtime (D-022), process-to-filesystem (`--once`), rule-to-model (D-023), and now file-to-pipeline. The units were never the problem. The tests asserted what each stage does when handed correct input, and the bugs were all in what one stage actually hands the next.

### D-025 — The extracted-text sidecar is not written for documents that arrived as text
**Status:** Decided and fixed · Sept 8 2026 · **found in the first live `[FILED]`**

The pipeline produced its first real filed document on the Mac:

```
MRECAI/FINANCE/2026-09-03__MRE__FINANCE__acme-supply-company__invoice-6023-total__USD2100-00__f0c2109e.txt
```

Correct entity, correct category, correct date, correct amount. But `find` returned **three** files, and one of them was `…__f0c2109e.txt.txt`.

`filing.file_artifact` writes the extracted text beside the document as `<filed name>.txt`. For a photograph that is the entire point — the image is not searchable and the transcription is. For a document that arrived *as* text, D-024 now reads it successfully and hands back the same bytes, so filing writes a byte-identical copy of the document next to the document.

Not merely untidy. It doubles the archive for every text document, and it puts a second file in the folder that reads as a separate record — the exact confusion the D-007 naming convention exists to prevent. Anyone browsing the archive in a year sees two files where one document was filed.

`ingest_file` now passes the sidecar text through `_sidecar_text()`, which returns `None` when the engine was `plain-text`. The `.meta.json` still carries `has_ocr_text` and `ocr_engine`, so the archive still states what was read and by what — not writing the copy must not decay into "we never read it", and a test pins that.

**Three new tests**, including the boundary: suppressing the copy for text must not suppress it for images, where the sidecar is the only searchable form of the document. **138 passed, 1 xfailed.**

Worth noting how this was found. It is the fifth defect this build, and the first that no test could have caught, because it was never wrong in the code's own terms — filing did exactly what it was told, and D-024 changed what it was being told without anyone re-reading the result. It surfaced only because a real document went all the way through and the output was *looked at* rather than checked for a status string. The other four came from running one document end to end; this one came from reading what that produced.

### D-026 — Two categories cannot both accept the same document type
**Status:** Decided and fixed · Sept 8 2026 · **found by filing two documents instead of one**

The second live document filed cleanly, and to the wrong place:

```
inv6023  →  MRECAI/FINANCE/…__MRE__FINANCE__acme-supply-company__…
inv6024  →  MRECAI/VENDORS/…__MRE__VENDORS__acme-supply-company__…
```

Same counterparty, same layout, same document type, consecutive runs, different folders. **Neither answer was wrong.** `taxonomy.yaml` offered a subcategory literally named `invoices` under *both* `FINANCE` and `VENDORS`, and `render_prompt` handed the model a bare comma-separated list of category names with no descriptions at all. The distinction that decides it — **which direction the money moves** — existed only in the head of whoever wrote the taxonomy. The model was asked to guess a convention nobody had written down, and guessed differently twice.

This is the defect that most directly breaks what the client asked for. "Determine if it's business, which business, save it" is not satisfied by an archive where you must check two folders to find a vendor's invoices.

**Fix.** Each category now carries a one-line description of what belongs in it, rendered into the prompt beside the name — `VENDORS — Money going OUT…`, `FINANCE — Money coming IN…`. And the colliding subcategories are renamed to state the direction themselves: `invoices-issued` under FINANCE, `invoices-received` under VENDORS.

**Then the property test found two more.** Rather than fix `invoices` and move on, `test_no_two_categories_share_a_subcategory_name` asserts the class: no subcategory name may appear under two categories in the same tree. It immediately caught `maintenance` under HOME and VEHICLES, `contracts` under LEGAL and WEDDING, and — after the first pass — `contracts` under LEGAL and VENDORS. The wedding one is a genuine coin flip: a caterer's contract is honestly both. Now `WEDDING` outranks, its description says so, and the subcategory is `vendor-contracts` so the names cannot collide either.

Writing the assertion as a property rather than as three examples is the point. The next collision will be `receipts`, added by someone who did not read this file.

### D-027 — The taxonomy overrides block was read by no code at all
**Status:** Decided and fixed · Sept 8 2026 · **the worst defect this build, and entirely silent**

`taxonomy.yaml` has carried this since the taxonomy was written:

> *"Signals that force a category regardless of model output. Deterministic, checked in code before classification. A hit here is not a suggestion."*

```
grep -rn "overrides" --include=*.py .
./tests/test_classify.py:94:def test_routing_overrides_a_disagreeing_model
```

One hit, and it is a test function name about entity routing. `Registry` had no field for it. `load_registry` parsed `personal`, `business`, and stopped. **Nothing had ever read the block.** Which means:

- **Jury duty → `LEGAL/court`, urgency HIGH was not implemented.** This is the client's own worked example, the one he used in the Aug 5 meeting to describe what he wanted the system to do. A summons was being categorised by whatever the model happened to say that run.
- **"notice of cancellation" / "policy will lapse" / "final notice" → CRITICAL was not implemented.** For an insurance business, a lapse notice is the single most expensive thing in the mailbox to miss.
- **`List-Unsubscribe` → urgency NONE was not implemented**, so bulk mail competed with real mail for space in the morning brief.

Worse than D-024, and for one reason: **it was silent.** No error, no quarantine, no wrong-looking output. It simply did not happen, and would have been discovered weeks from now by a summons sitting in `OPERATIONS` — or not discovered at all, since nobody knew to look.

**Fix.** `Override` is parsed into the registry and applied in `Classifier._validate`, after the model and before category validation. After, deliberately: we still want the counterparty, amount, dates and descriptor the model extracted — we simply do not let it choose the category when a hard signal is present. The rationale is prefixed `[override: text jury duty]` so a reviewer seeing `LEGAL` beside reasoning about invoices knows the model's answer was discarded rather than that it reasoned badly.

**What an override deliberately does not touch is `confidence`.** Confidence means confidence in the *entity* (D-023). Matching the word "subpoena" tells you a great deal about the category and nothing whatsoever about whose subpoena it is. An unroutable summons still goes to a human, which is right, and `test_an_override_does_not_raise_entity_confidence` pins it.

Validation moved to load time, because an override is by definition the case where we decided not to trust the model — a typo in it replaces a merely uncertain answer with an impossible one, and quarantines pointing at the model instead of at the config. A forced category must exist in **both** trees, since an override fires on text and text does not know whether the document routed to a person or a business. Enforcing that immediately exposed that `court` existed only under personal `LEGAL`, so a subpoena served on one of the businesses would have quarantined every time.

**The test that should have existed:** `test_every_declared_override_is_reachable` — every rule in the config must fire on some sample. That is a config-to-code coverage assertion, and its absence is exactly what let the block sit dead in plain sight while reading as though it were load-bearing.

**Fifth and sixth defects, same boundary as all the others.** Code-to-runtime (D-022), process-to-filesystem (`--once`), rule-to-model (D-023), file-to-pipeline (D-024), stage-to-stage (D-025), and now **config-to-code, twice**: a taxonomy that under-specified what the code had to decide, and a config block the code never read. Every unit was correct in isolation. Every defect lived in what one layer assumed about the next.

**151 passed, 1 xfailed** (up from 138).

### D-028 — The model was never told subcategories exist
**Status:** Decided and fixed · Sept 8 2026 · **found in the `why.py` output of a successful run**

Three documents filed cleanly. The classifications table read:

```
P_MRE   LEGAL/court      conf=0.95  by=rule
B_MRE   VENDORS/None     conf=0.95  by=rule
B_MRE   FINANCE/None     conf=0.95  by=rule
```

`None`, twice. And the filed paths confirmed it — the summons went to `PERSONAL/Matthew/LEGAL/court/`, the invoices went to `MRECAI/VENDORS/` and `MRECAI/FINANCE/` with no folder beneath.

`taxonomy.yaml` defines subcategories for every category. `validate_category` enforces them. `filing` uses them as folder depth — demonstrably, since `court/` exists. And `grep -i subcategor prompts/classifier.md` returned **nothing**. The field is in the JSON schema, so the model dutifully emitted it; it had simply never been told what the field was for or what values were legal, so it returned `null` every single time.

The one subcategory ever set on that machine was `court`, and only because a D-027 override forced it.

Left alone, `MRECAI/VENDORS/` accumulates contracts, invoices and subscriptions in one flat folder forever, and the entire subcategory half of the taxonomy is decorative. It also means D-026's renaming of `invoices` → `invoices-issued` / `invoices-received` fixed a collision in a field nothing populated — correct, and until now inert.

**Fix.** `_category_block` renders each category's subcategories on an indented line beneath its description, and the prompt gains a rule for the field: pick from the list under your chosen category verbatim, or `null`. Null is explicitly endorsed — a wrong subcategory buries a document one level deeper than a wrong category does, and filing flat is findable.

System prompt is now ~1,600 tokens against the ~2,500 budget the file allows for.

**`test_every_subcategory_reaches_the_model`** asserts every value defined in `taxonomy.yaml` appears in the rendered prompt. That is the same config-to-code coverage assertion as D-027's, and its absence is why this survived: the taxonomy, the validator and the filer all agreed subcategories existed, and the one component that had to *ask* for one was never checked against them.

**Seventh defect, and the third in a row on the config-to-code boundary** — a taxonomy that under-specified what the code must decide (D-026), a config block no code read (D-027), and now a config field the code enforced but never offered. All three passed every unit test, because every unit was correct about the part of the contract it could see.

**155 passed, 1 xfailed.**

### D-029 — PDFs are read text-layer first, rasterised only if there isn't one
**Status:** Built · Sept 8 2026 · the gap D-024 named out loud

Since D-024 every PDF quarantined with *"PDF reading is not implemented yet"* — correct, loud, and useless, because a PDF is the format most real mail actually arrives in. Nothing about it was blocked on Matthew.

**Two different formats wear the extension.** A generated PDF — an emailed invoice, a policy document — carries its text as text. A scan is pixels in a PDF wrapper and needs the same OCR as a phone photo. `extract_pdf` tries the text layer first via PDFKit, and falls through to page rasterisation into Vision only when the text layer comes back under `MIN_USEFUL_CHARS`.

Order matters for a reason beyond speed. **OCR of a generated PDF introduces errors into a document that had none** — a transposed digit in an account number that was perfectly legible in the source. If the text is already there, reading it is exact.

The `usable` threshold also catches the awkward middle case: a scan behind a generated fax cover sheet. Reading only the text layer would file the document on the strength of its letterhead and never look at the pages carrying the content.

**Decisions inside it:**

- **Pages render to in-memory `CGImage`s and never touch disk.** A rendered page of somebody's tax return has no business existing as a temp file, and `_vision_read_cgimage` — split out of `vision_ocr` — means a PDF page goes through the identical recognition path as a photograph.
- **A white ground is filled before drawing.** A PDF page is transparent by default, and Vision reads dark-on-transparent as dark-on-black, which recognises very badly.
- **`MAX_PDF_PAGES = 20`.** One inference call per page: twenty pages is a long insurance policy, two hundred is either a mistake or a way to occupy the machine for an hour. Pages past the cap are not read and the engine string says `+truncated-20of200`, so a truncated document never looks complete.
- **A password-protected PDF raises rather than falling through to rasterisation.** Rendering it produces blank pages, and blank pages OCR to a *successful empty read* — a locked document misreported as an unreadable one. It is filed unread with a reason that says which it is.

**And one entry point.** `read_any()` now dispatches every readable suffix, and `ingest` calls nothing else. D-024 was `READABLE_SUFFIXES` and the code acting on it disagreeing; `test_every_readable_suffix_has_a_reader` walks the set and demands a real reader for every member, so the set and the dispatch can no longer drift apart. That test is the fix for the *class*, not the instance.

**A test was passing while describing the wrong thing again — the third time this build.** `test_an_unreadable_format_is_quarantined_before_the_model` used a `.pdf` to mean "a format with no reader". It kept passing after PDFs became readable, for entirely the wrong reason. It now uses `.docx`, which is genuinely unsupported.

One more, smaller: the new suffix-walking test dialled LM Studio nine times and took the suite from 9s to 37s. It takes a stub client now. **A test that reaches the network is a test people start skipping.**

**161 passed, 1 xfailed.**

Still not implemented, and now the honest list: `.docx`, `.xlsx`, and any other office format quarantine by name. That is the right behaviour until someone decides they are worth reading.

### D-030 — A file that arrives empty is reported, not skipped
**Status:** Decided and fixed · Sept 8 2026

`cupsfilter` wrote a 0-byte PDF into the inbox. The watcher printed **neither `[FILED]` nor `[QUARANTINED]`**, wrote no log line, and created no row. It did nothing at all, and would have gone on doing nothing on every scan for as long as the file sat there.

```python
if path.stat().st_size == 0:
    continue
```

Bare, unlogged, and ahead of the settle check. And there was a test asserting it: **`test_zero_byte_files_are_ignored`** — a test whose name states the defect as though it were the design. That is the **fourth** time this build that correcting behaviour exposed a test passing while describing the wrong thing.

**Silence is the worst outcome available here.** A document that is loudly refused gets dealt with; one that vanishes does not. Under D-024 the pipeline already refuses to guess about a document it could not read — but that only helps if the document reaches the pipeline, and this one never did.

**Fix.** The settle check runs first, so a file that is momentarily zero bytes because it is still being written is still left alone — the original guard was right about that case and wrong about every other one. A file that has *settled* at zero bytes is a failed delivery: a truncated AirDrop, a Shortcut that errored, a converter that wrote nothing. It is logged `EMPTY_FILE`, retired to `_failed` so it is not re-skipped forever, and returned as an `IngestResult` with status `EMPTY` so `--once` prints it. A log line nobody is watching is only marginally louder than silence.

### D-031 — The readers were built and could not run on the target machine
**Status:** Decided and fixed · Sept 8 2026 · **the gap between "shipped" and "works here"**

`ops/bringup_mac.sh` installed `pyyaml pytest`. That is the whole dependency list, and it has been since Day 1.

So on the Mac: `vision_available()` is False, `pdfkit_available()` is False. Photographed mail has been falling back to `glm-ocr` — slower, but it works. **PDFs, as of D-029, would have quarantined unread every single time, whatever they contained**, because both readers PDF support depends on were absent. I built the feature, tested it, shipped it, and it could not execute on the one machine it exists for.

Nothing announced this. Each PDF would simply have failed on its own, one at a time, with a reason describing the symptom.

**Fix, two parts.** `bringup_mac.sh` installs `pyobjc-framework-Vision` and `pyobjc-framework-Quartz`, and does not treat failure as fatal — a machine without them still files text and still reaches the model for images. It degrades honestly, but it degrades.

And `verify_setup.py` gains **[7] Document readers available on this machine**, which is a different question from everything else in that script. The other six checks ask whether the *configuration* is right. This one asks whether the *code can run here* — and reports missing PDFKit as a hard FAIL, since PDF is the format most real mail arrives in.

**A capability that is missing should be announced once, at setup, not rediscovered per document.** That is the same lesson as D-027's config-to-code coverage test, arriving from the opposite direction: there, config declared behaviour no code implemented; here, code implemented behaviour the environment could not support.

**163 passed, 1 xfailed.**

### D-032 — A pre-flight check must run in the environment it is checking
**Status:** Decided and fixed · Sept 8 2026 · **the check was confidently wrong**

`bringup_mac.sh` installed the PyObjC frameworks and reported them:

```
deps: pyobjc-framework-Quartz 12.2.2 pyobjc-framework-Vision 12.2.2 pytest 9.1.1 PyYAML 6.0.3
```

`./ops/verify_setup.py` then said:

```
FAIL  PDFKit UNAVAILABLE — EVERY PDF will quarantine unread, whatever it contains
```

And in the same session, the watcher filed a PDF:

```
[FILED] …/MRECAI/VENDORS/invoices-received/2026-09-06__MRE__VENDORS__acme-supply-company__…__USD3240-00__9223b73b.pdf
```

Both statements were produced honestly. `verify_setup.py` carries `#!/usr/bin/env python3`, so `./ops/verify_setup.py` runs under **Homebrew's python3** — which has no PyObjC. The LaunchAgent runs `.venv/bin/python`, which does. D-031's new check was inspecting a different interpreter from the one that does the work.

**It was wrong in the harmless direction this time.** The same mismatch reversed — PyObjC present system-wide, absent from the venv — reports PASS while every PDF quarantines, and that is the direction that costs a day of looking at the wrong thing. A check that inspects a different environment than the thing it checks is worse than no check, because it converts an unknown into a confident falsehood.

**Fix.** The script re-execs itself under `.venv/bin/python` when that exists and is not already the running interpreter, guarded by an env var against a loop. Re-exec rather than warn: a warning about the interpreter is one more thing to read past. Check [7] then states which interpreter produced its answers, and says so loudly when it is not the venv.

Second, smaller correction in the same pass: PDFKit and Vision are macOS frameworks and can never exist on a Linux CI box, where `--skip-openclaw` is meant to be usable. A permanent FAIL there trains people to scroll past the section that also carries the real failures, so off-Darwin it is a WARN that says why.

**Note what found this.** Not a test — the suite passes on both interpreters, because it never asserts *which* interpreter. It was found by two outputs in the same terminal contradicting each other, and by not letting that go. The eighth defect this build, and the second (after D-031) on the boundary between the code and the machine it runs on rather than between two pieces of code.

### D-033 — An environment is identified by `sys.prefix`, not by the binary it symlinks to
**Status:** Decided and fixed · Sept 8 2026 · **the D-032 fix was itself wrong**

D-032's guard read:

```python
Path(sys.executable).resolve() != _VENV_PY.resolve()
```

Everything then passed, and the output said:

```
PASS  interpreter: /opt/homebrew/Cellar/python@3.12/…/bin/python3.12 (the one the daemon uses)
```

That path is not `.venv/bin/python`. It is the Homebrew interpreter, and the check called it a match — because a venv's `python` is a **symlink to the base interpreter**, so `.resolve()` collapses every venv onto the same file:

```
/tmp/va/bin/python -> /usr/bin/python3.10    sys.prefix = /tmp/va
/tmp/vb/bin/python -> /usr/bin/python3.10    sys.prefix = /tmp/vb
/usr/bin/python3   -> /usr/bin/python3.10    sys.prefix = /usr
```

All three compare equal. The guard could not distinguish `.venv` from a different venv or from the bare Homebrew install, so it re-exec'd or didn't for reasons unconnected to which environment was active, and then printed a path that contradicted its own claim.

That is not hypothetical on this machine: a stray `path/to/venv` has been active in the shell since a mis-pasted `python3 -m venv path/to/venv` earlier in the build, which is why the prompt reads `(venv)`. Two venvs, one base interpreter, and a check that cannot tell them apart.

**`sys.prefix` is the environment** — the one value of the three that differs. The guard and the report now use it, and the report says `environment:` rather than `interpreter:`, because the interpreter was never the question.

**The pattern worth naming.** D-031 was "the code cannot run here". D-032 was "the check ran somewhere else". D-033 is "the check could not tell where it ran". Three failures in a row about the boundary between the program and its environment, each one found by the fix for the previous one being visibly inconsistent with its own output — not by a test. The suite passes under every interpreter, because no test asserts which environment it is running in, and it is not obvious one usefully could.

**The general lesson:** when a comparison is meant to establish identity, check what actually carries the identity. `resolve()` answers "which file", and the question was "which environment".

### D-034 — The brief: ranked, capped, and never silent
**Status:** Built · Sept 8 2026 · Day-5 §1 and §3

Matthew's sentence has four clauses — *"determine if it's business, which business, save it, **and then let me know what I need to do**"* — and until now the system did three of them. `core/reports/` was an empty `__init__.py`.

**The order is the message.** A brief is read on a phone, before coffee, in about twenty seconds. A list he has to read in full to find the urgent thing has failed at the only job it has. So tasks are ranked, not listed chronologically:

- **Overdue outranks everything and keeps climbing**, capped at 30 days. A deadline passing unseen is the failure this system exists to prevent, and an item that has already slipped is evidence the earlier briefs did not work.
- **Money is a tiebreaker, never a driver** — capped at 10 points, roughly $10k. A $40 renewal that lapses a licence outranks a $50,000 invoice due in March. Any scoring where the amount leads gets that backwards, which is exactly the mistake a busy person makes unaided and the reason to rank at all.
- **Undated is not zero-risk, it is unknown-risk.** It scores nothing from the calendar and leans on urgency, which is the honest answer rather than a guessed deadline.
- **Ties break by id**, so the order is stable between runs. A list that reshuffles overnight teaches people not to trust the order.

**The cap is seven (spec §Day-5.1), but it never hides a CRITICAL or overdue item.** If nine things lapse this week, showing seven is not concision — it is choosing which two he finds out about the hard way. Withheld items are counted in the text, not dropped.

**And it always says something.** An empty brief states that nothing needs him, because an empty message and a broken one are indistinguishable on a phone. That is D-030's lesson arriving somewhere new: silence is the one outcome that cannot be acted on.

**"Since the last brief" is read from the log, not assumed from the clock.** The first draft used a fixed 14-hour window, which is correct exactly as long as every scheduled run happens. The Mac sleeps, loses power (C8 is *still* unanswered), gets shut for a weekend — and the moment one run is missed, a fixed window silently skips every document that arrived in the gap. Filed, findable, and never mentioned: the failure this module exists to prevent, reintroduced at the last step. It now reads the last `BRIEF_SENT_*` row from the append-only log; the window survives only as the first-run fallback.

`mark_brief_sent()` is deliberately **not** called by `build_brief()`. Recording a delivery at build time would mark documents as reported by a brief that was rendered to a terminal and read by nobody.

**Delivery is not here.** The brief goes to Telegram (§Day-5.3) and Telegram is blocked on C2 — not installed, no authorised sender. So this module builds and renders; `ops/brief.py` prints it. The split is worth keeping regardless: the brief can be read, diffed and tested without a network, and the 06:30/17:30 jobs stay thin wrappers rather than the place the logic lives. The ~3-interrupt/day cap is also delivery-side.

**One test tried to backdate a log row and the database refused it** — `actions_log is append-only`, the trigger doing precisely its job against the first thing that tried to rewrite history. The fixture appends a row with an older timestamp instead, which is the honest way to say "this happened earlier".

**188 passed, 1 xfailed.**

**Follow-on, from reading the first real brief.** Every task line printed its due date twice:

```
1. Jury duty summons — Superior Court Of Nassau County (by 2026-10-03)
       due 2026-10-03 · HIGH
```

`_task_title` appended `(by …)` — written months ago, when nothing else would ever show the date. The brief shows it now, and better: `3d OVERDUE` or `due in 5d`, which is what a date is actually for. Two layers had each solved the same problem alone, neither knowing the other existed, and the cost landed on the two most valuable lines of a twenty-second read.

The title now says only what the task *is*. The date lives in `tasks.due_date`, where it can be sorted, filtered and re-rendered — a date baked into a title string can only be read. **189 passed.**

### D-035 — Backup, and a restore that is actually run
**Status:** Built · Sept 9 2026 · spec §Day-6.4

Until this there was no backup of any kind. The archive, the classifications and the append-only audit log sat on one volume. It was the last remaining risk in the build whose downside is unrecoverable, and it needed nothing from Matthew.

**The measurement that decided the design.** SQLite runs in WAL mode, so committed rows live in `lms.db-wal` until a checkpoint. Copying `lms.db` with `cp` while anything holds the database open gets the main file and none of them:

```
WAL present : True
snapshot    : {'artifacts': 500}      # Connection.backup()
naive cp    : {}                      # shutil.copy2()
```

**Five hundred committed rows in, zero rows out** — and the bad copy passes `PRAGMA integrity_check` cleanly. It restores, it opens, it is empty. That is the worst failure mode available to a backup: it announces nothing, and reveals itself only on the day it is needed. `Connection.backup()` takes a consistent snapshot of a live database, and that is the whole difference between a backup and a file that resembles one.

`test_a_file_copy_of_a_live_database_loses_rows` pins it. The first version of that test quietly proved nothing — the fixture returned a path and let the connection be garbage-collected, which checkpoints the WAL and erases the condition under test. It now returns the connection and the caller holds it, because the scenario *is* "a process is using this database right now".

**`restore_test.py` is the other half, and the reason the spec says "test a restore now, not later."** An untested backup is a belief about a directory. It restores the latest snapshot to a temp directory and checks what actually decides whether a bad morning is survivable: the database opens, passes integrity_check, its row counts match the live database table by table, the sidecars came back, and `entities.yaml` is present and parses.

Two of those checks exist because of specific failure shapes:

- **Counting, not just integrity_check.** An empty database is perfectly valid. Only the count catches the naive-copy failure.
- **A backup with MORE rows than the live database is a FAILURE, not drift.** Fewer is expected — the database grew since the snapshot. More means rows have vanished from the original, which is a different and far more interesting problem, and must not be filed under "drift".

Nothing is written outside the temp directory and the live archive is opened read-only throughout: running the restore test must never be able to make things worse, because it will be run when things are already going badly.

**Secrets.** restic encrypts client-side; the repository password lives in the Keychain and is read at run time. It is not in the script, not in `lms.env`, and not in the plist — plists are world-readable and land in git.

**Scheduling.** `StartCalendarInterval` at 02:30, deliberately not `StartInterval`: if the Mac is asleep or off, launchd runs the job at the next opportunity, where an interval timer would simply skip the night. C8 — who unlocks the machine after a power cut — is still unanswered, so missed windows are a live scenario rather than a hypothetical. It is a LaunchAgent for a second reason beyond the watcher's: a system daemon cannot read the user's Keychain.

**A side fix.** The re-exec block from D-032/D-033 had been copied into three ops scripts at module level, which made them re-exec *on import* — a test that imported `backup.py` to exercise its snapshot logic instead relaunched the script, which exited complaining `LMS_ARCHIVE_ROOT` was unset. Code that cannot be imported cannot be unit-tested, and the backup path is the last place to accept "we think it works". It is now `ops/_reexec.py`, called from `main()`.

**Still outstanding, and it is Matthew's decision (D-012).** A local repository does not survive theft, fire, or the volume failing. An off-machine copy means client tax and NPI data leaving the premises — restic encrypts before upload so the provider only ever holds ciphertext, and that is the mitigation, not an argument that the question does not arise. It needs his sign-off in writing, plus an account. `backup.py` prints this every run rather than letting it become invisible.

**201 passed, 1 xfailed.**

**Two corrections from the first real run (Sept 10).**

*The error message was a guess.* The Keychain read failed and the script said "no Keychain item `lms/restic-repo`" — to someone who had just created that item successfully. It collapsed every non-zero exit from `security` into one diagnosis and threw away `stderr` and the exit code, so the message sent the reader to check the one thing that was fine. It now reports the command, the exit code and the actual stderr, and only claims "not found" on exit 44, naming 51 (access denied — macOS prompts the first time a new process reads an item) and 36 (locked keychain) as the other likely answers. **An error that names a cause it has not established is worse than one that admits it does not know**, and this build has spent two days on exactly that failure in other people's code.

*The default put the backup on the same disk as the data.*

```
archive  /Volumes/MacStudioHD/LMS/archive
repo     /Volumes/MacStudioHD/LMS/LMS_backup
```

Both on `MacStudioHD`. That survives a bad delete, a corrupted database, or a classification run gone wrong — all worth having — and it does nothing whatsoever about the disk failing, which is the case the word "backup" is usually reaching for. Both copies go at once. The plist default had the same fault.

Detected with `st_dev`, not by comparing path strings: `/Volumes/MacStudioHD/LMS` and `/Volumes/MacStudioHD_backup` look unrelated and can be one device. The check also has to work before the repo directory exists, since restic creates it on first run — otherwise the warning would never fire on the one run where it matters most.

Not fatal, deliberately. A same-volume repository is better than none, and refusing to run would leave the machine with no backup at all while **D-011 is open** — nobody has yet said which volume is the mirror. So it warns, loudly, every run, and names D-011 as the thing that unblocks it.

**Third correction, and the improved error message caught it on its first run.** With the diagnostics fixed, the script said:

> *`security` reported success for 'lms/restic-repo' but returned an empty password.*

`security add-generic-password -w` had **accepted an empty password in silence**. Exit 0, nothing stored. Confirmed directly: `security find-generic-password -s lms/restic-repo -w; echo "exit=$?"` printed a blank line and `exit=0`.

Left alone, restic would have initialised the repository with an empty passphrase and reported success. **An encrypted-at-rest backup whose key is `""` is a plaintext backup with extra steps** — and the encryption is the entire mitigation for D-012's off-machine copy, the one that lets client tax and NPI data leave the premises at all. The mitigation would have been nominally in place and worth nothing.

So `restic_password()` now enforces a 12-character floor, and the creation hint generates a 40-character passphrase to the clipboard rather than asking someone to type one — paste it at both prompts, and the secret never touches shell history or the process table. The hint ends with the thing nobody says out loud until it is too late: **restic has no recovery path.** Lose the passphrase and every snapshot is permanently unreadable — still there, still encrypted, useless. It belongs in Matthew's password manager before the first backup runs, which makes it a C7-adjacent custody question rather than a detail of this script.

**Fourth correction — the terminal was mangling the paste.** Three attempts to create the Keychain item by hand produced, in order: an item with an empty password stored in silence, a "passwords don't match" mismatch, and another empty one. Then this appeared on the command line:

```
5~5~5~5~5~5~5~5~5~5~5~5~5~...
```

That is the tail of `ESC[200~` — **bracketed-paste markers arriving as literal text.** Paste was broken in that session, so what reached `security`'s no-echo prompt was neither the clipboard contents nor anything the operator could see. Every "just paste it at the prompt" instruction was doomed before it was given, and the no-echo prompt guaranteed nobody would notice.

Two silent failures compounding: a terminal that mangles paste, and a `security` command that accepts an empty password without complaint.

`ops/set_backup_password.py` removes the terminal from the loop entirely. The passphrase is generated with `secrets`, stored, read back, and compared — and the script refuses to claim success unless what came back is byte-identical to what it generated. It is never typed, never pasted, and never round-trips through a prompt.

It is written via **`security -i`**, which reads commands from stdin, so the secret goes down a pipe rather than into `argv`. It therefore never appears in `ps`, never reaches shell history, and is never a temp file — the one thing a documented one-liner cannot manage, since `-w <value>` puts the secret in the process table. `-U` updates in place, so there is no window where the machine has no passphrase at all.

The alphabet is letters and digits only. Punctuation survives a pipe perfectly well, but this value gets copied into a password manager and may be read aloud or retyped during a restore that is already going badly; ambiguity is a worse trade than four characters of entropy.

It prints the passphrase once, deliberately. That is not a leak, it is the requirement: restic has no recovery path, so a human must capture it exactly once.

**The first successful run, and what it showed.**

```
==> consistent database snapshot (online backup API, not cp)
    actions_log=6, artifacts=2, classifications=2, processed=4, tasks=2
    integrity_check: ok
snapshot e9fc569a saved
...
Restore verified — the backup is usable.
```

Then, in the same output:

```
PASS  4 sidecar(s) restored (archive has 2)
```

Four from two. With `--documents` the sidecars go in twice — once inside the archive tree, once from the staged copy that exists *because* documents are normally excluded. The check passed on arithmetic rather than evidence: `4 >= 2` is true for reasons that have nothing to do with whether the sidecars survived.

And the comparison was against a **moving target**. A document filed between the backup and the restore test makes `restored < live`, which the check treated as failure — while the row-count check, three lines above, correctly treats growth as expected. The same situation, opposite verdicts, in one function.

Both fixed by making the snapshot **state its own contents**. `backup.py` writes a `manifest.json` — created_at, whether documents were included, sidecar count, row counts — and `restore_test.py` verifies against that claim exactly, falling back to the live comparison only for repositories written before manifests existed. A restore test that refuses to run on an old snapshot is useless precisely when an old snapshot is all there is.

The staged sidecar copy is now skipped when `--documents` is set, since they are already in the tree.

One existing test had to invert: `test_missing_sidecars_fail_the_restore` asserted a hard failure for restored-fewer-than-live, and that assertion *was* the defect. Without a manifest the two causes — sidecars lost, or documents filed since — are indistinguishable, and calling it loss produces a false alarm every time the archive grows. The alarm that cries wolf is the one nobody reads on the morning it is real. It now pins the honest answer: cannot tell, take a fresh backup. **Fifth time this build a test was asserting the wrong behaviour.**

**Then the nightly job ran, and there were two repositories.**

The interactive run and `launchctl kickstart` both reported success. They were writing to different places:

```
/Volumes/MacStudioHD/LMS/LMS_backup      ./ops/backup.py, ./ops/restore_test.py
/Volumes/MacStudioHD/LMS_backup          com.lms.backup.plist (the nightly job)
```

`lms.env` never declared `LMS_BACKUP_REPO`, so interactive runs fell through to the code default while the plist supplied its own. **The scheduled backup wrote to one repository and the restore test verified the other** — both green, indefinitely, and the discovery would have come on the morning someone needed a snapshot that was never in the repository they were looking at.

Every plist carried its own copy of the LMS_* paths, under a comment that admitted the hazard outright: *"Mirrors ops/lms.env. Change both together; they are not linked."* A comment naming a risk is not a control.

**`lms.env` is now the only description of where things live.** It is generated by `bringup_mac.sh` from the volume layout, so it is derived rather than retyped, and it now declares `LMS_BACKUP_REPO`. `ops/_env.py` loads it — parsed, never sourced, because this runs unattended at 02:30 and sourcing executes whatever the file contains. Existing environment variables always win: `LMS_BACKUP_REPO=/Volumes/Other ./ops/backup.py` means it.

The backup plist now carries only what launchd cannot supply any other way — `PATH` (launchd inherits none, so restic would not be found), `TZ`, `PYTHONUNBUFFERED`. `test_no_plist_declares_an_lms_path` enforces that, and deliberately names `com.lms.watchfolder.plist` as the remaining unconverted one rather than quietly passing over it.

This is the same shape as D-032 — the check and the thing checked in different places, with nothing to notice — arriving through configuration rather than through an interpreter.

**And one more thing the fix surfaced.** With a single repository, `restore latest` now picks whatever ran most recently — and the nightly job runs *without* `--documents`. So the newest snapshot is normally the catalogue alone, while the restore test signs off with:

> *Restore verified — the backup is usable.*

Which reads as "the documents are safe", and for that snapshot is false. The manifest already records `documents_included`; there was no reason to leave the reader inferring it. The restore test now says which kind of snapshot it checked, as a caveat rather than a failure — the catalogue-only snapshot is exactly what the spec asks for nightly, it just must not be mistaken for something else.

Also: `lms.env` is written from `$RAID_ROOT/LMS` rather than `$ARCHIVE/../`. Both resolve identically, but a path containing `..` cannot be compared against another path by eye — and telling two repository locations apart by eye is precisely what was needed to notice they had diverged.

Added **[8] Backup readiness** to `verify_setup.py`: restic present, the password readable and long enough, and whether the repo shares a volume with the archive. Every one of those is something the 02:30 job would otherwise discover alone, in a log nobody reads, on the night it mattered. Platform-guarded like [7] — off a Mac it reports the platform rather than failing forever.

**206 passed.**

### D-036 — The hard stops were asserted against themselves
**Status:** Decided and fixed · Sept 10 2026 · **the compensating controls for D-009**

Under D-009 the LMS runs as Matthew's own user. There is no privilege boundary; the containment is entirely the compensating controls — zero-tool agents, no code path that sends, no deletes, an append-only log. `rules.yaml` declares eight hard stops and `test_all_hard_stops_are_on` asserted every one was `true`.

```
grep -rn hard_stops core/ ops/   →   0 references
```

**No code reads that block.** The test loaded a YAML file and asserted the YAML file said `true` eight times — a config asserting itself. Passing meant somebody had typed the word. Same shape as D-027, and in the one place it matters most.

The same grep: `unsubscribe` 0 references, `classifier_tools` 0, `bypass_cap_for` 0. Only `sanitisation` (2) and `phishing` (1) are actually read.

**The stops do hold — but by absence.** There is no `smtplib`, no `imaplib`, no payment client, no e-signature client anywhere in `core/`. "There is no code path" is the strongest guarantee available and the most fragile, because it lasts exactly as long as the feature stays unbuilt. On **Day 3 an email adapter arrives**, and `never_delete_email: true` becomes a sentence in a file with a green test beside it and nothing behind it.

**Each stop now names what enforces it.** Three are `TESTED` against a real mechanism — the grep guard on delete primitives, `smtplib` in `FORBIDDEN_CALLS`, remote-image stripping plus `assert_loopback`. Five are `STRUCTURAL`, and each carries the imports that would make it violable: `imaplib`, `googleapiclient`, `stripe`, `docusign`, and so on.

`test_a_stop_held_only_by_absence_fails_when_the_feature_arrives` walks `core/` for those markers. **Verified by injecting a module containing `import imaplib` — the suite failed and named the three stops that had just become claims.** That is the point of it: it converts "we will remember to enforce this" into "the suite stops you", on the day the code lands rather than after it ships.

Two more guards: a stop declared in `rules.yaml` with no enforcement entry fails, so adding one forces the question; and an enforcement entry for a stop that no longer exists fails, so the mapping cannot rot.

**Sixth test this build that was passing while describing the wrong thing** — and the first one where the wrong thing being described was a security guarantee.

### D-037 — A comment in a generated file executed, and cost the only backup
**Status:** Fixed · Sept 10 2026 · **my defect, and the near-miss was worse than the bug**

The D-011 change added a comment to the block in `bringup_mac.sh` that generates `ops/lms.env`. The comment contained backticks around a command name — written as prose, meant as prose. The heredoc is **unquoted**, because `$ARCHIVE` and friends have to expand. Command substitution expands too.

So bringup ran `diskutil list` and pasted its output into `lms.env`. On the Mac:

```
/Users/mleca/lms-repo/lms/ops/lms.env: line 18: 0:: command not found
```

The first line of substituted output stays inside the `#`. Every line after it does not. Single-line output would have hidden completely — this failed loudly only because `diskutil` is chatty.

**What followed is the part worth recording.** `source ops/lms.env` returned non-zero, the `&&` chain short-circuited, and the `echo "repo: …"` that would have shown the new path never ran. `LMS_BACKUP_REPO` never took effect. The backup went to the **old** location, on the array, and reported:

> *Restore verified — the backup is usable.*

True, and about the wrong repository. On the strength of that line the old repository was then deleted, per an instruction I had written. **For a few minutes there was no backup at all.** Nothing was lost — the archive is intact and two documents small — but the sequence is exact: a config generator that executed, a verdict that was true of the wrong thing, and a destructive step gated on reading rather than on checking.

**Three fixes, in order of how much they matter.**

*The generator now verifies what it wrote.* `bringup_mac.sh` sources the file in a clean subshell and fails if it produces **any output at all**, then confirms all eight variables are set. Silence is the whole test: a config file that prints anything is a config file executing something. Reproduced and confirmed — the guard catches the multi-line case with the same `command not found` the Mac saw.

*The heredoc is now free of substitution, comments included* — and the warning saying so is spelled out in words, because writing the construct in a warning about the construct would be an instance of it.

*And the instruction was wrong, not just unlucky.* "Delete the old repository once you see `Restore verified`" asked someone to check a verdict when the thing in doubt was a **path** — which was printed three lines above the verdict and contradicted it. A destructive step must be gated on the specific fact in question, mechanically. Nothing about that failure required the operator to be careless; it required them to read the line I told them to read.

**Eleventh defect found by running the thing rather than testing it, and the only one I introduced myself.**

### D-038 — A correction moves the document, or it is only a note
**Status:** Built · Sept 10 2026 · spec §Day-5.5

The `corrections` table and `db.record_correction()` have existed since Day 1 and nothing called either — the same shape as D-027 and D-036, found by looking rather than by anything failing.

**The design question was what a correction IS.** The spec says corrections go to a table. Doing only that produces a database that knows the truth and an archive that does not, and **Matthew looks in folders, not in SQLite.** So `apply_correction` does three things, all or none:

1. records old → new in `corrections`, never pruned
2. re-files the document, its sidecar and its transcription
3. sets `decided_by = 'human'`, so nothing downstream mistakes his judgement for the model's

The classification row is overwritten rather than duplicated, which makes `corrections` **the only surviving record of what the model actually said** — its stated purpose, and the training signal for the next engagement.

Measured, on a filed invoice, telling it the document is Atlase rather than MRECAI:

```
BEFORE  archive/MRECAI/VENDORS/invoices-received/2026-09-06__MRE__VENDORS__…pdf
AFTER   archive/ATLASE/VENDORS/invoices-received/2026-09-06__ATL__VENDORS__…pdf
        corrections:  entity_id: B_MRE -> B_ATL
        originals:    untouched
```

Note the filename, not just the folder. The entity code is *in* the name (D-007), so moving without renaming would leave `__MRE__` on a document filed under Atlase — a file that lies about itself in the one place people read.

**This had to argue its way past the hard stops.** `test_core_contains_no_send_or_delete_primitives` forbids `shutil.move` in `core/`, and D-036 had just made that guard real. The exemption is written out in `EXEMPT`: the archive copy is *derived* — `file_artifact` keeps every incoming byte under `_originals/<sha256>` and never writes there again — so moving it destroys nothing, the original is intact throughout, and refusing would leave a document filed under the wrong business permanently.

Worth stating plainly: `os.replace` and `Path.rename` are not in `FORBIDDEN_CALLS`, and using one of them would have slipped past the guard silently. **A guard's value depends entirely on not routing around it**, so the move is written the obvious way and argued for in the open.

**Refusals, and why each one refuses rather than does its best:**

- An invalid entity or category is rejected **before anything is recorded or moved**. A half-applied correction is worse than a refused one, because the whole point is that the database and the archive agree afterwards.
- A name collision refuses rather than overwrites, exactly as first-time filing does. Losing a document silently is the one failure this system must not have.
- A document the database says is filed, that is not where it says, refuses — correcting it would mean guessing what happened to it.
- A quarantined document is not corrected. It was never classified, so there is nothing to override; that is a first classification, and a different operation.
- Agreeing with the system is not a correction. Recording one would put noise into the only table that says where the classifier was wrong.

`subcategory=None` means "file it at the category level" and is a legitimate instruction, so a sentinel distinguishes it from "leave it alone" — on the command line that is `--subcategory NONE`, since an absent flag already means the latter.

`ops/correct.py` is the front door for now. When C2 lands and this happens in Telegram, that becomes a second entrance onto the same tested operation rather than a second implementation of it.

**240 passed, 1 xfailed.**

### D-040 — A message becomes a document, and its attachments become documents of their own
**Status:** Built · Sept 10 2026

`ingest_file` takes a path, and every guarantee in the system is anchored to the bytes at that path — dedupe is the sha256 of the file, `_originals/<sha256>` is the copy that is never rewritten, and the archive holds something openable in five years without this codebase. An email that exists only as rows in SQLite has none of that. **So the message is rendered to a text file first**, and after that it is an ordinary document going through the pipeline that already works.

**Identity is the sha256 of the rendered file, and the `Message-ID` is inside it.** That single detail carries the whole polling model. The rendered bytes do not change between polls, so re-reading yesterday costs one hash and files nothing; and two different emails that both say "thanks" stay two documents instead of collapsing into one and losing the second permanently. Which is why there is **no cursor**: a stored "last message id" loses mail the first time a run dies between reading and committing, whereas a missed night here is fixed by `--days 3`.

**The invoice is almost never in the body; it is the PDF.** Each attachment is ingested as its own artifact with `parent_id` pointing at the email — the schema has had that column since day one and nothing had ever populated it. They classify **independently**: a covering note routes on `recipient_email` like anything else, and a PDF that OCRs to an ATLASE invoice files under ATLASE even when the note arrived at the MRECAI address. Passing the mail body down to the attachment would let *"please see the attached ATLASE invoice"* classify a document that is nothing of the sort, and would also defeat D-024's emptiness check by handing ingest a body it never read from that file.

An attachment whose parent was quarantined is still ingested. The covering email being unclassifiable says nothing about the invoice attached to it.

**Only a hard `fail` counts.** `Authentication-Results` is finally read — rules.yaml has named `spf_fail`/`dkim_fail`/`dmarc_fail` since the spec and `ingest_file` has taken an `auth_results` argument that nothing populated. But `softfail`, `neutral`, `none`, `temperror` and `permerror` do not count, and that is not laxity:

> **this mailbox has automatic forwarding switched on, and forwarding breaks SPF by design.** The forwarding server is not in the original domain's SPF record. Treating every non-pass as fraud would quarantine a large share of legitimate mail, and a review queue that is mostly false positives is a review queue nobody reads — a worse security outcome than the narrower check, not a more cautious one.

DMARC exists precisely to resolve this: it passes when *either* SPF or DKIM aligns, and DKIM survives forwarding. So `dmarc=fail` is the signal that means something. `dmarc=none` is not a failure at all — most of the internet has no policy — and only becomes one when the sender claims a domain **we know**, because for those we would expect a policy and its absence means anyone can forge the address.

Only the **first** `Authentication-Results` header is read. Everything below the receiving server's own line came in over the wire, and a sender can write `Authentication-Results: spf=pass` into their own message.

**Two things arrive from strangers and are treated accordingly.** Attachment filenames are rebuilt from an allowlist rather than filtered for known-bad — `../../.ssh/authorized_keys` is a legal MIME filename, and a blocklist here is a bet that we thought of every encoding. And attachments are fetched only by suffix and under 25MB, which is not a security control (the read-only scope is) but a "do not download 60MB of video in order to OCR it" control.

**A signature block is not nine documents.** The first `--dry-run` against the real mailbox — before anything was written — showed `image001.gif` through `image009.png` on every message Matthew sends. Gmail reports each of them **exactly** the way it reports an invoice PDF: an attachmentId, a filename, a size. The first real run would have downloaded all nine, sent each to Vision to have a logo OCR'd, and filed nine junk artifacts per email.

What separates them is `Content-ID`: the body references the part as `<img src="cid:…">`, so it is drawn *inside* the message rather than attached *to* it. `Content-Disposition: inline` is accepted as the same signal. A part with **neither** header is treated as a real attachment — filing one stray image costs a document in the review queue, and the opposite mistake silently drops an invoice; only one of those is recoverable.

`Message.attachments` still keeps everything, because the raw parse should not throw information away. `Message.real_attachments` is what every caller uses. Inline parts are not reported as skipped either — nine `[SKIPPED]` lines per email would bury the one that matters.

**This is why `--dry-run` exists**, and it is the first time on this project a defect was caught before it reached the archive rather than after. Twelve of the previous thirteen were found by reading output that had already been written.

**Then the fix ate two real documents.** The next run's listing showed `—` where `Requested Document(s) #1.pdf` and `SIGNATURE PAGE 2022 TOYOTA.pdf` had been. Both had arrived from Outlook, which marks genuine attachments `Content-Disposition: inline` — there it means "show this in the reading pane", not "this is decoration". So the disposition is not sufficient on its own, and the rule is now **mime type first**: only an `image/*` part can ever be body content. A PDF is never a signature logo, whatever headers it carries.

I had written the sentence *"the opposite mistake silently drops an invoice, and only one of those is recoverable"* into a comment, and then written code that did exactly that. The asymmetry has to be enforced by the mime check, not by remembering.

**Then everything was quarantined as phishing**, and that was a third defect in the same feature:

```python
auth["dmarc_none"] = seen.get("dmarc") in {"none", None}     # wrong
auth["dmarc_none"] = seen.get("dmarc") == "none"             # right
```

`None` in that set meant a message with **no `Authentication-Results` header at all** counted as "a known domain that published no DMARC policy". Mail from `matthew@mrecai.com` to himself never leaves Google — there is nothing to authenticate, so Gmail writes no header — and every internal message he sends was flagged as suspected phishing. **Absence of a verdict is not a verdict.**

`test_a_missing_header_is_not_a_failure` was supposed to cover this. It asserted `spf_fail`, `dkim_fail` and `dmarc_fail` were all False on a missing header — true, and not the flag that fires. It now asserts on **every** flag, which is the only version of that test worth having.

**302 passed, 1 xfailed** — 46 new tests. Three defects in this feature, all found by running it against the real mailbox, none by the suite.

### D-047 — For mail, the hash identifies a rendering, not a document
**Status:** Built · Sept 10 2026

The first delivered brief said:

```
WAITING ON YOU (1)
   - 1a0879e46550679e.eml.txt (suspected_phishing)
```

about a document sitting correctly filed in `MRECAI/CLIENTS`.

```
id 3  sha 1726ed8a  SUSPECTED_PHISHING  source_ref 1a0879e46550679e  filed_path None
id 7  sha c1191639  FILED               source_ref 1a0879e46550679e  ← the same message
```

**Identity is the sha256 of the bytes, and for mail those bytes are a rendering rather than a fact.** Dropping the signature images from the `Attachments:` line changed the rendered text, so the message became a second artifact — filed correctly — while the first stayed at `SUSPECTED_PHISHING` with no `filed_path` and nothing that would ever resolve it. Every improvement to the renderer leaves one of these behind.

`supersede_earlier_versions` marks them DUPLICATE when a newer rendering of the **same `source_ref`** files. That key is exact: for mail it is the Gmail message id, for an attachment `<message id>/<attachment id>`, so two rows sharing one are necessarily two renderings of one thing. Rows with no `source_ref` — a photograph named by the phone — are left alone, because there the filename is not an identity and `IMG_0001.HEIC` comes round again.

**One phantom entry in the section Matthew is supposed to act on, and he learns to skim that section** — which is the same as not having it. That is the whole reason this is worth fixing rather than tolerating.

**And the clean-up was on the wrong side of an early return.** `_resolved/` was empty on the Mac while four *filed* documents still sat in the review queue: they had quarantined, then filed, and every run since took the duplicate short-circuit, which returned before `retire_quarantine_copy` was ever reached. Anything that filed before that clean-up existed would have stayed in the queue forever. Both clean-ups now run on the duplicate path too.

**408 passed, 1 xfailed.**

### D-046 — The brief is delivered to iCloud, because Telegram is blocked and stdout is not delivery
**Status:** Built · Sept 10 2026

The brief has existed since D-034 and **nothing has ever run it.** `ops/brief.py` prints to stdout, on a machine in Matthew's office that he does not sit at. That is a function capable of producing a brief, not a brief.

Telegram is the intended channel and is blocked on C2 — the app is not installed and there is no authorised sender, so there is nothing to send to and no way for him to reply `/halt`. Waiting for it means the daily rhythm he asked for on Aug 5 does not exist at all.

**iCloud Drive is already on his phone**, already syncing, needs no credential and nothing from him — the same folder the photo Shortcut writes into, so both halves of the system are one folder he can see. It is worse than a push notification: he has to go and look. It is the difference between *delivered late* and *not delivered*, and it works today. When C2 lands, the job changes its command and nothing else.

Delivered as `.txt`, not `.md`: the iOS Files app previews plain text inline and offers Markdown as a download, and one tap versus a download is the difference between read and unread. A dated file plus a stable `Latest brief.txt` a Home Screen bookmark can point at.

**The thing that must not go wrong is marking a brief sent that never arrived.** `mark_brief_sent` moves the boundary for the next brief's "filed since last brief" section — so a false mark does not lose one brief, it removes those documents from **every future brief**, permanently, because nothing looks back. iCloud will accept a write into a directory it has not materialised and hand back a file that reads short, which is exactly how that would happen. So the file is written, read back, and compared, and only then is anything marked. A failed delivery logs `BRIEF_DELIVERY_FAILED` and leaves the window where it was, so the next run repeats the same items rather than skipping them.

**Morning only.** The evening brief needs a second Label and a second plist, and it is deliberately not scheduled: two unread files a day is how a daily brief becomes something he stops opening, and until there is evidence he reads the morning one, adding a second is guessing.

**404 passed, 1 xfailed.**

### D-045 — Mail runs on a schedule, and a plist may not name a path
**Status:** Built · Sept 10 2026

**D-035 happened a second time, in the file that carried the warning.**

`com.lms.watchfolder.plist` declared its own `LMS_*` block under the comment *"Mirrors ops/lms.env … they are not linked."* They were not linked, and they had already diverged:

```
plist:    LMS_INBOX = ~/Library/Mobile Documents/…/CloudDocs/LMS/inbox   ← where the Shortcut writes
lms.env:  LMS_INBOX = ~/LMS/inbox                                        ← empty, always
```

The scheduled job looked in the right place. Anything run from a shell that had sourced `lms.env` looked at an empty directory, found nothing, and reported no error at all — **the exact failure that plist's own header comment warns about**, one variable further down.

`poll_mail.py` had the same hole: it never called `load_lms_env()`, so it only filed to the array because the operator happened to have sourced the file in that shell. Scheduled, it would have filed everything to `~/LMS/archive` instead.

Writing the lesson down twice did not stop it happening twice, so it is a test now. **`tests/test_plists.py` fails on any plist declaring an `LMS_*` variable**, and also on: an interpreter that is not the venv's absolute path, a script that is not in `ops/`, a secret in the environment, a Label that does not match the filename, `KeepAlive` on a scheduled job, and discarded output. Entry points moved into `ops/` (`ops/watch.py`) so `core/` never has to import from `ops/` to read its own paths.

That test caught something on its first run: **`--days 2` inside an XML comment**. A double hyphen makes a plist unparseable, launchd refuses the file, and the symptom is a job that silently never runs — with nothing in the log, because the log path is configured inside the file it could not read. Now its own named test, since these plists carry long comments and quoting a flag is exactly what such a comment wants to do.

**The mail poll is scheduled 07:00 and 19:00, with a two-day window.** The overlap is the design: identity is the sha256 of the rendered message, so re-reading yesterday files nothing and costs one hash. That is what makes a missed run harmless — a stored cursor would be cheaper and would lose mail permanently the first time a run died between reading and committing. C8 (who unlocks the Mac after a power cut) is still unanswered, so missed windows are a live scenario rather than a hypothetical.

The morning poll lands **after** the 06:30 brief, deliberately: polling at 06:00 would put a network call and twenty inference passes in front of the one thing that has to arrive on time. Mail is at most a day late reaching a brief, and the evening run means most of a day is already filed. If that is the wrong trade, move the brief, not the poll.

Scheduling adds no new risk: the credential is `gmail.readonly` and cannot delete, flag or label anything (D-039).

**385 passed, 1 xfailed.**

### D-044 — `List-Unsubscribe` identifies the senders who are already behaving well
**Status:** Built · Sept 10 2026

Measured, not assumed. Across a real week of Matthew's mailbox — 22 messages — **`List-Unsubscribe` was present on 2**, and both were SignWell and Braintrust, senders already doing the right thing. The override fired correctly on both. It was never broken.

The other twenty had no such header, and about half of them were marketing. So the header now accepts a list — `List-Unsubscribe`, `List-Unsubscribe-Post`, `List-Id`, `Feedback-ID`, all of which appear only on mail sent to a list.

**That will help a little and will not solve it.** The dominant category in this mailbox is not newsletters, it is **cold sales email** — b2bfunnelgroup, cedarbridgeadvisors, tdmsource, Manhattan Fortress Capital. Those omit `List-Unsubscribe` *on purpose*, because the entire trick is to look like a message a person wrote. No header check can catch mail engineered to have no headers that distinguish it.

**C18, for Matthew:** there is no category for *"someone is selling me something I did not ask for."* So an unsolicited financing offer files under VENDORS — a vendor is someone we buy from — and a cold consulting pitch files under CLIENTS. The model is not guessing wildly; it is picking the least-bad option from a menu with no right answer. Same shape as C17, found the same way, and it is his taxonomy to decide.

`match_overrides` now returns `(override, signal)` pairs so the rationale names the header that actually fired rather than the whole candidate list — "header List-Unsubscribe" and "header List-Id" are different facts about a message, and naming the wrong one sends a reviewer looking for something that is not there.

**That change broke nothing in 342 tests, because nothing called `match_overrides`.** D-027 was this same block existing while nothing read it. `tests/test_overrides.py` now tests it directly — matching, the reported signal, the shipped configuration, and load-time validation.

**355 passed, 1 xfailed.**

### D-043 — Only offer the model answers that can validate
**Status:** Built · Sept 10 2026

```
category 'VEHICLES' is not valid for 'B_MRE'
```

A GEICO insurance card and a Toyota signature page arrived at `matthew@mrecai.com`, so `recipient_email` routed them to a business. `render_prompt` then listed **both** category trees anyway, the model reasonably chose `VEHICLES`, and `validate_category` refused it — the BUSINESS tree has no such category.

**The model was not wrong about the documents.** It was answering a question we asked badly: here are twenty categories, nine of which are guaranteed to be rejected. Once routing has decided the entity, only that entity's tree is offered. When routing has NOT resolved an entity, both trees still appear, because choosing between them *is* the question.

This is the same principle as validating the answer, moved one step earlier — decide in code what code can decide. Validation catches the bad answer; constraining the menu means it is never available. `test_every_offered_category_would_validate` asserts the property rather than the one case: whatever the model can pick from the menu it is shown must be capable of being accepted.

**A separate question for Matthew (C17):** his vehicle paperwork genuinely has nowhere to go. `VEHICLES` exists only in the PERSONAL tree, and these documents arrive at a business address, so `recipient_email` — his own precedence rule — sends them to MRECAI. Either the BUSINESS tree gains a VEHICLES category, or vehicle documents are personal regardless of which address they arrive at. That is his call about his taxonomy, not mine to invent.

**And the review queue now describes the present.** A document that quarantined on Tuesday and filed on Wednesday left its Tuesday copy sitting there, sidecar and all, saying *"most likely a blank scan"* about a document by then correctly filed in FINANCE. I misread the output myself because of it. `retire_quarantine_copy` moves it to `quarantine/_resolved/` on successful filing — never deletes, since the record that something was once refused and why is worth keeping, and by then the bytes exist in the archive and in `_originals` anyway.

**342 passed, 1 xfailed.**

### D-042 — `processed` is an audit trail, not a cache
**Status:** Built · Sept 10 2026

A GEICO insurance card with a **10,533-character text layer** sat in the review queue described as *"no text extracted: the PDF has no text layer and OCR of its pages produced nothing — most likely a blank scan."*

The text extraction was never the problem. `pdf_text_layer` read the document perfectly, on the first run and every run since. This did it:

```python
if not db.already_processed(conn, sha, "ocr"):
    result = ocr.read_any(source_path)
    ...
    db.mark_processed(conn, sha, "ocr")
```

**`processed` records that a stage RAN. It does not keep what the stage produced.** OCR's output is only persisted when the document is filed — the sidecar `.txt`. So a document that read successfully and then quarantined for any reason had its text discarded, and the next run skipped the read, found an empty body, and reported it as blank. **Permanently** — every later run skipped it too, so no downstream fix could recover it. The PDFs only became visible once the phishing false positives were fixed and they stopped being quarantined for a different reason first.

The guard could never have helped. A document that HAS been filed never reaches that line — the dedupe check at the top of `ingest_file` returns `DUPLICATE` first. So the only artifacts arriving there are ones not yet filed, which are exactly the ones that must be read again. **The cache was pure cost.**

The module docstring claimed *"a crash halfway through is safe to re-run: completed stages are skipped and nothing is done twice."* The first half is true and the second half was the bug — re-running is safe because filing is **idempotent on the sha256**, not because stages are skipped. Corrected, along with a standing rule: `processed` is an audit trail and must never gate work.

The regression test was run against the old code before being kept, and produced the exact production symptom — `no text extracted: read as empty` on the second ingest of a document the first ingest read fine.

**334 passed, 1 xfailed.**

### D-041 — Nothing computes a domain from a header by hand
**Status:** Built · Sept 10 2026 · the worst defect of this build

Real mail says:

```
From: "Matthew R. Epstein" <matthew@mrecai.com>
```

Three places computed the domain as `address.rpartition("@")[2]`, which on that header returns **`mrecai.com>`** — with the bracket. One missing function produced four failures at once, and they only became visible when a live mailbox arrived:

| what | why it failed |
|---|---|
| `resolve_by_email` | compared the whole display-name string against `entities.yaml` |
| `resolve_by_domain` | `mrecai.com>` is not `mrecai.com` |
| **the entire precedence chain** | so every message fell through to the model |
| `phishing_check` | `sender 'mrecai.com>' is a look-alike of 'mrecai.com'` — edit distance 1 |

The third row is the serious one. **"Deterministic routing beats the model" is the load-bearing claim of the whole classification design** (D-005), and it had never once fired on a real message — code that cannot be argued with by an email was silently not running, and the model was deciding everything. The fourth row is the one that stopped the run: every message from a domain we know was quarantined as an impersonation of itself.

`email.utils.parseaddr` has been in the standard library since 1999. **The rule from here: no module computes a domain from a header itself** — `core/addressing.py` is the only place that parses an address, and `registry`, `ingest` and `gmail` all call it.

`addresses()` returns **every** address in a `To:` header, not the first. A message addressed to a client with Matthew in copy is still his, and `getaddresses` is also the only thing that parses `"Epstein, Matthew" <…>` correctly — a naive `split(",")` makes that two recipients, one of them with no `@` at all.

Writing the tests found a fifth: `parseaddr("not an address")` returns `("", "not")` — the first word, confidently, as though it were an address. `address()` now requires an `@`, because a domain we invented is exactly the kind of value the look-alike check will then have an opinion about.

**None of this was caught by 302 tests**, because every fixture ever written used a bare `matthew@mrecai.com` — the one form real mail does not use. That is the pattern of this entire build restated in a single defect: the tests agreed with the code, and both were wrong about the world.

**331 passed, 1 xfailed.**

### D-039 — Mail arrives over OAuth, and the scope is the security boundary
**Status:** Built · Sept 10 2026 · supersedes the Aug 5 access plan

**The Aug 5 agreement cannot be implemented.** It was: share the mailbox passwords, disable 2FA. Since **14 March 2025** Google Workspace has refused legacy passwords for IMAP, SMTP, POP, CalDAV and CardDAV. That plan is not unwise any more, it is non-functional — the mailbox will not authenticate. D-008 was framed as a choice and no longer is.

What remained was an app password (which **requires** 2-Step Verification — the plan's first step would have made its own second step impossible) or OAuth. The client's constraint was firm: no 2SV.

**So: an internal Workspace OAuth app.** Google confirms internal apps using restricted Gmail scopes need no verification and no CASA assessment. No 2SV, no app password, no service-account key. It is also what the spec asked for originally — Rec 22, *"internal Workspace OAuth app for business."*

Domain-wide delegation was the other no-2SV route and was rejected: since August 2024 it can require **multi-party approval from a second super admin** (this Workspace may not have one), it cannot touch `mattyeps@gmail.com` at all, and the credential becomes a key that can impersonate **any of the eleven accounts in the domain**. On a project where credentials have already reached a PDF and a chat window, that is the wrong secret to create.

**The scope is the whole security posture.**

```python
SCOPES = ("https://www.googleapis.com/auth/gmail.readonly",)
```

Three of the eight hard stops stop being promises:

| stop | enforced by |
|---|---|
| `never_delete_email` | the token cannot delete |
| `never_mark_read` | a Gmail API fetch does not set `\Seen` — unlike IMAP, where forgetting once shows up in his unread count |
| `never_touch_non_lms_labels` | the token cannot write any label |

**That is stronger than code.** A bug in `adapters/gmail.py` cannot reach past a scope it was never granted. D-036 recorded those three as held only by the absence of a mail client; rather than hand them back when one arrived, they moved to the credential — the one place our own mistakes cannot reach.

**D-036's tripwire fired on its first real occasion**, which is the whole reason it exists:

```
these hard stops were held only by the absence of the code that could
break them, and that code now exists:
  never_delete_email, never_mark_read, never_touch_non_lms_labels
```

`test_widening_the_scope_is_not_a_quiet_change` now stands in front of the next step: labels and draft replies need `gmail.modify` and `gmail.compose`, and adding either hands those three stops back to code. The test says so, in the failure message, to whoever is about to do it.

**Everything is testable without credentials.** The transport is injectable and the Google libraries import lazily — the same pattern as `ocr.py`, for the same reason: the machine this is written on is not the machine it runs on, and a module that can only be tested against a live mailbox stops being tested. Sixteen parsing and client tests run against fixtures shaped like real API responses.

Two parsing decisions worth recording. **`text/plain` beats `text/html`** whenever both exist: a multipart/alternative carries the same content twice, and the plain part has already had the markup, the tracking pixels and the mismatched link text removed by the sender's own client. And **an unparseable `Date:` becomes empty, not today** — a fabricated timestamp flows into the filename under D-007 and files the document under a day it has nothing to do with.

**256 passed, 1 xfailed.**

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

**Sept 10 — measured, and the plan did not survive contact with the hardware.**

The instruction was "backups go in a backup partition". `diskutil list` says there is no such partition, and no room to make a useful one:

```
disk6 disk7 disk8 disk9   4 × 4 TB external, IDENTICAL partition GUIDs → RAID members
disk10                    12 TB Apple_APFS — the array as ONE device
disk11 (synthesized)      Physical Store disk10
  └─ APFS Volume MacStudioHD     the only volume on it
```

16 TB of hardware presenting 12 TB is single-parity RAID. **That survives one disk failing. It is not a backup.** It does nothing about the enclosure, the controller, filesystem corruption, an accidental delete, ransomware, or theft — and from the LMS's point of view all four disks are one failure domain.

A second APFS volume in container `disk11` would satisfy the words and none of the intent: same container, same physical store, dies with the array. It would also silence the same-volume warning while making nothing safer, which is worse than leaving the warning up.

**Decision: the repository moves to the internal SSD — `$HOME/LMS/backup`.**

`disk0` is genuinely different hardware from `disk10`, so this survives the array failing, and `st_dev` differs so the warning clears on evidence rather than by agreement. `MacStudioHD` is using 170.5 MB against 723 GB free internally, so capacity is not a near-term constraint.

What it does **not** survive: the machine. Theft, fire, or the Mac itself dying takes both copies, because both are inside it. That is **D-012**, still open, and this decision does not reduce its urgency — it removes the case where a single array fault loses everything, and leaves the case where a single *building* fault does.

Reversible in one line of `lms.env` if a dedicated external disk is added later.

> **Volumes:** 12 TB array = `disk10` (4 × 4 TB, single-parity), one volume `MacStudioHD`. Internal = `disk0`, `Macintosh HD - Data`.
> **Backup target:** `$HOME/LMS/backup` — internal SSD, different device from the array · Sept 10 2026
> **Approved to encrypt in place:** n/a — the array was already encrypted (D-019); the internal volume is under FileVault
> **Date:** Sept 10 2026

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
