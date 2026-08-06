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

### D-009 — Service account vs. user-session integrations
**Status:** OPEN · must be settled **before Day 4** · [PHASES.md:67](PHASES.md:67)
LMS services run under a non-admin `lms` user, but iMessage (`~/Library/Messages/chat.db`, C13) and iCloud Drive (C12, C14) are bound to Matthew's own macOS session and are invisible from a separate account.

- (a) Run LMS under Matthew's user — everything works, privilege separation lost, an injected agent inherits his session's reach. Contradicts spec §4.1 principle 8.
- (b) **Split it (recommended)** — gateway and agents under `lms`; a minimal, tool-less reader daemon in Matthew's session hands artifacts to the queue read-only. Keeps the boundary; costs one extra component.
- (c) Drop both channels from scope — cheapest, but photographed mail is core to the meeting's ask.

Changes nothing in Phases 2–9; surfaced in Phase 1 only because the user account is created there.

> **Chosen:**
> **Date:**

### D-010 — Remote-access path
**Status:** OPEN · must be settled **before default-deny egress goes live** · client item **C4** · [PHASES.md:170](PHASES.md:170)

- (a) Keep RustDesk, allowlist its relay hosts, and accept a third-party relay sitting outside the Tailscale-only posture — removed at handover (§12.4, Q212).
- (b) **Tailscale + macOS Screen Sharing (recommended)** — what Q213 actually implies.

**Ordering hazard:** confirm the machine is still reachable from an outside network *before* ending the session that turns on default-deny. Getting this wrong locks you out of a machine you may not be standing next to.

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
