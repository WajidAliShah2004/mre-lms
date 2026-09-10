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

Added **[8] Backup readiness** to `verify_setup.py`: restic present, the password readable and long enough, and whether the repo shares a volume with the archive. Every one of those is something the 02:30 job would otherwise discover alone, in a log nobody reads, on the night it mattered. Platform-guarded like [7] — off a Mac it reports the platform rather than failing forever.

**206 passed.**

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
