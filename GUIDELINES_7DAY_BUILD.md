# Guidelines — LMS Build After the Aug 5 Meeting

The meeting **overrides the v3.0 spec's scope**. Matthew said it directly (00:27:47): *"I don't need it to do business operations… I just need it to determine if it's business, which business, save it, and then let me know what I need to do."* You committed to **7 working days**. Build the simple version well; treat the v3.0 spec as the roadmap for later, not the current deliverable.

---

## 1. What the meeting changed vs. the spec

| Spec v3.0 said | Meeting decided | Action |
|---|---|---|
| 514–694 h, 16 phases, 17–20 weeks | Simple LMS in **7 working days** | Build the descoped core only |
| Hostinger VPS rescue instance (Phase 13) | **Remove Hostinger** — Matthew cancels it | No VPS; Mac-only architecture |
| QBO integration, ledger, commission engine, renewals, tax-chase | **Not now** — "I don't need it to put it into QuickBooks" | Defer all of Part 7's business workstreams |
| Ten agents, two-gateway topology | Single Mac Studio instance | Simplify agent topology (see §3) |
| Internal storage roots | Thunderbolt 5 RAID (12 TB) attached | Put LMS trees + models on the agreed volumes |
| OAuth, least-privilege, MFA everywhere | Matthew disabling 2FA + sharing passwords | ⚠ See §5 — push back on this |

## 2. The actual 7-day scope (from Matthew's own words, 00:29–00:30)

Build exactly this, nothing more:

1. **Ingest** — email (all accounts), iMessage/texts, call transcripts, photographed mail dropped via phone photo → watched iCloud folder.
2. **Classify** — Business or Personal → which business (MRECAI, C.H. Shink, Atlase, MLECA) or which person (Matthew, mom, dad, fiancée) → category.
3. **File** — save the artifact into the deterministic folder tree (keep the spec's §6.2 naming convention — it costs nothing and prevents a re-file later).
4. **Surface** — updating to-do list per person/entity ("jury duty — call by Sept 12"); tell him what it is and the next action.
5. **Email hygiene** — flag important email, unsubscribe spam (keep it header-only per Q33 — never click body links), draft replies **for his approval** — never auto-send.

Explicitly out (for now): QBO push, ledger/reconciliation, commission engine, renewal pipeline, tax-chase, voice profiles beyond a basic drafting tone, VPS failover, SimpleFIN, WhatsApp, shadow-mode graduation machinery.

## 3. Suggested 7-day plan (Mac Studio, on-site via RustDesk)

- **Day 1 — Foundations.** Toolchain, OpenClaw extended-stable, LM Studio + wired-limit LaunchDaemon (MAC_SETUP_GUIDE.md §1–4), storage roots on the RAID volumes, Telegram bot as control channel, repo scaffold.
- **Day 2 — Data + classify.** SQLite schema (artifacts, classifications, tasks, corrections), entities.yaml (4 businesses + 5 people + household), taxonomy, zero-tool classifier agent with json_schema output, deterministic filing code.
- **Day 3 — Email.** Connect mailboxes, normalize, classify, file, label `LMS/` namespace, flag important, header-only unsubscribe. **Never delete, never mark read.**
- **Day 4 — Messages + calls + mail photos.** iMessage ingestion (imsg bridge, read-only), call-transcript ingestion, watched iCloud folder + Apple Vision OCR for photographed mail.
- **Day 5 — To-do + briefs.** Task extraction (due dates → tasks), unified per-person/entity to-do list, morning digest to Telegram, draft-reply generation into Gmail drafts + approval buttons.
- **Day 6 — Hardening minimums.** The four hard stops (no autonomous sends, no money, no deletes, no binding acts), egress basics, `/halt`, backup (restic to the RAID + one off-machine copy since there's no VPS now).
- **Day 7 — Test with Matthew + handover.** Run the relevant acceptance tests (A1–A3, A6–A9 from the spec), walkthrough, document what's deferred.

If something slips, cut Day 4's call transcripts before anything else — email + mail photos + to-do list is the visible value.

## 4. Keep these spec invariants even in the simple build (cheap now, painful later)

- Naming convention + sidecar metadata (§6.2–6.3) and sha256 dedupe — prevents a re-file when the full system comes.
- Zero-tool classifier reading untrusted content; drafts always gated; no autonomous outbound to anyone. These are 90% of the spec's security value at 10% of the cost.
- Never delete / never mark read / append-only action log.
- Local models only for content touching client/tax data (the Mac Studio makes this free).
- Owner-owned git repo, secrets in Keychain, no plaintext credentials in code.

## 5. ⚠ Credentials — protect Matthew and yourself

The meeting agreed to **shared email passwords with 2FA disabled**. Given his practice handles client NPI and tax data (and the spec you'll eventually build to treats this as regulated), recommend instead, in writing:

- Keep 2FA **on**; use **app passwords** (Google supports them with 2FA enabled) or an internal Workspace OAuth app — same access for you, no naked credentials.
- If he insists, get it acknowledged in writing, store credentials only in the Mac's Keychain, and rotate + re-enable 2FA at handover (the spec's own offboarding protocol, §12.4).
- This also protects you contractually — you're about to sign IP/data agreements with him.

## 6. Your other action items from the meeting (not LMS)

| Item | Due |
|---|---|
| Send Twilio update guide to Matthew | Tomorrow (committed) |
| Update MCAI website wording (father-in-law's notes) | Tomorrow (committed) |
| Sign + return IP agreements (Atlas C-Corp / Nova Edge) | When received — read §12.1 of the spec first so terms are consistent |
| Complete OpenClaw/LMS | 7 working days |
| AI phone receptionist beta (on MRE knowledge base) | ~Sept 1; full build ~2 months, HIPAA-compliant |
| Atlas + ALES ready, atlas.te landing page with product subdomains, Atlas-brand naming | Sept 1 launch (Matthew unavailable Aug 20–Sept 1, wedding) |
| Father-in-law website + Atlas onboarding pilot | After Sept 1, paid separately |

Confirm Matthew removes the Hostinger instance only **after** anything on it (configs, data) is copied off.

## 7. Follow-up meeting

One is planned for "tomorrow" — bring: the Twilio guide, this 7-day plan for sign-off (so the descope is in writing), and the credentials recommendation from §5.
