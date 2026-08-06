# MRE LMS — Step-by-Step Action Plan (from Spec v3.0)

> ## ⚠ This is the deferred roadmap, not the current engagement
>
> The Aug 5 2026 meeting reduced scope to a **7-day core build** (D-001). This document describes the *full* v3.0 specification — 514–694 hours over 17–20 weeks — and is retained as the roadmap for a later engagement.
>
> **The plan of record for the current work is:**
> - [GUIDELINES_7DAY_BUILD.md](GUIDELINES_7DAY_BUILD.md) — scope contract
> - [PHASES.md](PHASES.md) — Mac/OpenClaw setup and the client-blocker register
> - [BUILD_PLAYBOOK.md](BUILD_PLAYBOOK.md) — Day 0–7 execution
> - [DECISIONS.md](DECISIONS.md) — what has actually been decided
>
> Do not schedule, quote, or build from this document. Use it to check that a v1 choice doesn't foreclose a v2 one.

Ordered checklist to achieve every task in the specification. Total build: **514–694 hours over 17–20 weeks**. Phases 0–7 deliver ~70% of daily value by ~week 9. Do not reorder Phases 0, 2, 3.

---

## STAGE A — Before signing anything (owner + counsel; §12, Rec 34)

1. **E&O disclosure first (§12.7).** Developer drafts the engineering annex (data-flow diagram, hard stops, human-review controls). Owner/counsel send it to both licensed entities' E&O carriers and get the **premium answer before signing** the development contract.
2. **Services agreement (§12.1).** Counsel drafts it with this spec as Exhibit A: IP assignment/work-for-hire, NDA + data-handling (no copies off machines, breach-notification duty), developer cyber/professional-liability insurance evidenced, liability cap, termination rights, counsel sign-off on WISP/IR plan/E&O annex.
3. **Acceptance-weighted milestones + 10% holdback (§12.2):** 10% signing → 15% Phase 3 classifier gate → 20% Phase 7 → 25% Phase 8 (±$0.00) → 15% Phases 9+11 → 15% Phase 15/stranger test → 10% holdback released 60 days post-DoD. Include defect definitions, cure periods, termination-for-failure, 90-day warranty.
4. **Price the run before the build (§12.3).** Written 12-month retainer quote ($300–1,000+/mo) with hours, SLA, same-week security-patch SLA, parser-drift fixes, macOS-update execution. Name a warm-backup developer.
5. **Ask the ten interview questions (§14.7).** "None" to Q9 is disqualifying.
6. **Independent measurement terms (§12.5):** owner approves golden-set labels; sealed 50-item holdout binds at acceptance; owner audit rights; ±$0.00 gate measured against the named institution schedule (§14.8).

## STAGE B — Week-1 owner inputs (blockers if skipped)

7. Complete the **named institution/account schedule** (§14.8, ~12 accounts / ~6 institutions) — this becomes the acceptance exhibit.
8. Seed **estate deadlines** for the Craig Shink estate (Surrogate's Court) — explicit owner-input task (Q10, §7.13).
9. Populate the **never-unsubscribe list** (Q35) and banned-phrases file (Q149).
10. Confirm name variants, all email addresses, legal names/EINs from formation docs (Q1–Q2, Q15).
11. Agree the **estate sub-ledger export format with estate counsel before Phase 8** (§7.13).
12. Obtain an **FRP/Fabricant commission-statement sample before Phase 11** (Q201).

## STAGE C — Build Phases 0–15 (§9)

### Phase 0 — Foundations & security baseline · 32–44 h · Wk 1
FileVault; never-sleep; non-admin account; macOS permissions; toolchain (Homebrew, Node LTS, Python 3.12+, git, rclone, restic, ffmpeg, sqlite3); OpenClaw on **extended-stable** + advisory subscription; VPS hardened; **Tailscale-only** (verified externally); secrets in Keychain (no .env); account inventory incl. MLECA; MFA + 2× hardware keys; Telegram 2FA + pairing; npm hygiene (ci, ignore-scripts, provenance, cooldown); **UPS with clean-shutdown** + FileVault power-recovery doc; Little Snitch/pf default-deny egress; Control-UI lockdown; **DMARC p=quarantine ramp** on mrecai.com/mleca.com/atlase.ai; **Gmail OAuth durability decided** (internal Workspace app for business; IMAP-or-verified for personal); E&O annex drafted; ZDR confirmations requested; baseline `openclaw security audit --deep`.
✅ Phone→gateway round trip; nothing reachable outside Tailscale; UPS-pull clean shutdown + VPS alert; DMARC reports arriving.

### Phase 1 — Local model stack & tier router · 24–32 h · Wk 1–2
Raise GPU wired limit + **persist via LaunchDaemon**; install LM Studio; benchmark candidates per tier against a 50-item starter set; L1 (35B-A3B) + L2 (122B-A10B) resident with keep-alive; MTP speculative decoding; llama.cpp batch lane; tier router with reason codes; **PII redaction + tax/privileged cloud hard-block wired into the router**; health cron; all schedules in America/New_York with DST test.
✅ Classification on L1 inside TTFT SLO with zero egress (packet capture); tax/privileged cloud call refused under every reason code; local share ≥90%; wired-limit daemon survives reboot.

### Phase 2 — Data layer, registry & taxonomy · 36–48 h · Wk 2–3
SQLite WAL schema (Postgres-compatible) + migrations; entities.yaml (§5.2), taxonomy.yaml (§5.3–5.4), rules.yaml; resolution chain; deterministic path/filename builder (unit-tested hard); three-stage dedupe; append-only actions_log + `lms undo-move`; quarantine/review queue; **taint columns + re-wrap-on-read**; tax/privileged/litigation_hold/fiduciary fields; **last-4 data minimization**; renewals + commission_book tables; idempotency `processed` table + durable-queue schema; **golden set of 200 owner-approved artifacts + sealed 50-item holdout**; FTS5 + vector index (privileged excluded).
✅ Golden set routes deterministically; duplicates → one reference; undo-move byte-identical; tainted strings arrive wrapped; no full account numbers in derived stores.

### Phase 3 — Airlock, classifier & eval harness · 38–50 h · Wk 3–4
Zero-tool `ingest-sandbox` (verify empty schema); sanitizer incl. vCard/contact names; classification skill with **json_schema constrained decoding**, complete enums, ≤2,500-token prompt; confidence triage; **canary injection corpus (AgentDojo/InjecAgent/LLMail-Inject + LMS cases) in CI**; prompts/ registry + scripted eval harness; /explain foundation.
✅ **Gate: ≥92% domain+entity, ≥85% category on sealed holdout; 100% canaries produce no action; airlock provably tool-less.**

### Phase 4 — Email ingestion & first visible value · 44–58 h · Wk 4–6
Connect mailboxes (durable OAuth); normalizer; **BEC/phishing pre-checks** (SPF/DKIM/DMARC, look-alike domains, wire language); importance score; **header-only unsubscribe** (never body links); cold-outreach handling; thread-state machine; review queue with three reply options; **email actions in shadow mode**; **thin read-only daily digest ships now** (value visible ~week 5). No backfill yet.
✅ One live week ≥92% domain/entity in shadow; zero deletions; planted BEC flagged; digest arrives.

### Phase 5 — Document capture, OCR & filing · 30–40 h · Wk 6–7
iOS Shortcut (Personal/Business buttons, multi-page, voice note, offline queue); watched-folder daemon (dataless download, debounce); OCR ladder (Vision → GLM-OCR/Paddle → multimodal L1/L2 → Chandra batch); voice-note transcription as context; HEIC→PDF; sidecars; bill → task + calendar block; receipt matching; Dropbox read-only; filing in shadow mode.
✅ Photographed bill/receipt filed correctly within 90 s with sidecars; voice-note hint measurably improves accuracy.

### Phase 6 — Calendar automation · 20–28 h · Wk 7–8
Google + iCloud/EventKit; convention learning from 24 months (owner confirms); event extraction; own-time auto-creation (shadow→live) with source links; conflict = tentative + flag + proposed resolution; travel-time blocking; significant dates at 14/2 days; invitations drafted only (HS1).
✅ Emailed meeting → correct event with travel time + source link; deliberate conflict never double-booked.

### Phase 7 — Tasks, briefs & control surface · 32–42 h · Wk 8–9
Priority function (§7.7); unified queue (top-7); Apple Reminders mirror; 06:30/17:30/Sun-18:00 briefs; notification budget + quiet hours; Telegram inline-button approvals; **typed confirmation code over $500 + away mode**; approval-latency telemetry + 48-h stale-draft expiry; break-glass runbook page; `/halt` verified ≤10 s. Then a **two-week notification-tuning sprint** (exit: ≤3 pushes/day, zero missed CRITICALs).

### Phase 8 — Financial ledger, statements & anomalies · 48–62 h · Wk 9–11
QBO connect per entity; CoA mapping; statement ingestion (PDF + SimpleFIN read-only, Mac-side poller until Phase 13); parser profiles per named institution (3–6 h each); ledger writes via **real two-pass agreement**; anomaly engine (≥$2,500 → immediate push); missing-statement watchdog; **QBO delivery via clearing-class drafts / export packages — never auto-post**; receivables aging; estate sub-ledger tagging with counsel-agreed export.
✅ Three months of statements reconcile at **±$0.00**; planted anomaly caught; photo+email+statement triplicate → one ledger row.

### Phase 9 — Voice profiling & drafting · 22–30 h · Wk 11–12
Sent-mail corpus (privileged excluded); distill **three launch voice profiles** (client-formal, client-warm, personal-family); owner review rounds; drafting on L2 with **owner-initiated cloud escalation only**; contradiction check; drafts to Gmail drafts + queue with 48-h expiry; 120-s delayed send + recall.
✅ Owner rates ≥8/10 drafts sendable-with-trivial-edits; every client-facing draft gated.

### Phase 10 — Messaging channels · 16–24 h · Wk 12–13
iMessage via native imsg bridge (whitelist-only send, health monitor); Twilio business SMS; group-chat opt-in; voice-note transcription. WhatsApp deferred.
✅ Text about a meeting creates the event; nothing sends 22:00–07:00 or to non-whitelisted contacts.

### Phase 11 — Business ops, renewals, tax-chase & commissions · 56–76 h · Wk 13–16
QBO customers → CLIENTS/<slug>/ trees + auto Drive folders (sharing gated HS1); invoice drafts (send gated HS2); receivables chasing 30/45/60 (each approved); document delivery (gated); e-signature (PandaDoc, HS4); §2119 fee-memo tracking; **compliance calendar with execution owners** (90/60/30/7 alerts); **client renewal pipeline** (60/30-day alerts, gated outreach, lapse watch); **tax-season missing-document chase** (checklist, gap report, gated reminders); **commission engine** (per-carrier rate tables, disappearance watch, 1099 reconciliation); fiduciary/estate accounting + household vault gap report.
✅ New QBO customer → tree + folder in 5 min; invoice cannot send unapproved; dropped account + $50 variance both flagged; policy 61 days out → alert + gated draft; missing W-2 on gap report.

### Phase 12 — Learning loop & golden-set lifecycle · 28–36 h · Wk 16–17 (not deferrable)
All correction surfaces → corrections table; rule induction on 3+ repeats with owner approval; per-rule golden_delta regression (a drop blocks the rule); monthly voice re-distillation; accuracy dashboard; golden set grows ~10 artifacts/month with stratification audit; quarterly holdout refresh + rules consolidation.
✅ Measured ≥3-point accuracy gain wk 16→17; three owner-approved rules live.

### Phase 13 — VPS rescue instance, queue, backup & monitoring · 28–38 h · Wk 17
Isolated VPS OpenClaw instance (own state dir, ports ≥20 apart, mre-lms-rescue bot); **durable idempotent job queue** (at-least-once, visibility timeouts); heartbeat (60 s; 15-min silence → VPS assumes crons); webhook receiver (Twilio, QBO, e-sign, Drive, Retell, SimpleFIN); nightly restic + off-site; **restore tested**; monitoring per §11.5; weekly production canaries; platform exit-strategy note.
✅ 20-min network pull → alert, VPS delivers brief, queue drains without duplication.

### Phase 14 — Voicemail & call ingestion · 28–42 h · Wk 17–18
Retell webhooks → calls pipeline; Twilio voicemail; carrier-voicemail file drop; TIER-A transcription + Retell-transcript reconciliation; caller→entity resolution; task/calendar extraction; voicemail BEC treatment.
✅ Callback voicemail → filed transcript + task + calendar block; "wire money" voicemail flagged, zero action.

### Phase 15 — Hardening, acceptance, handover & stranger test · 32–44 h · Wk 18–20
Full Part 10 suite with owner present (A1–A27, S1–S15); adversarial attempts on HS1–HS4, tax/privileged block, trifecta invariant; kill switch under load; IR tabletop vs. breach-clock matrix; finalize WISP + breach matrix + DFS calendar + E&O annex under counsel review; complete RUNBOOK.md and DECISIONS.md; **developer offboarding** (rotate all secrets/OAuth, remove Tailscale node/admin accounts, signed data-deletion attestation); 60–90 min walkthrough video; **stranger test** (independent dev runs RUNBOOK unassisted, on camera); continuity drill; two weeks supervised operation; go-live.

## STAGE D — Definition of done (§10.4)

Owner can, unassisted: change a rule, add an entity, swap a local model, edit a voice profile, run/clear the kill switch, restore from backup. Stranger test passed. Compliance artifacts under counsel review. **14 consecutive days inside every binding quality gate** (§10.3: domain ≥95%, entity ≥92%, category ≥85% on sealed holdout; ±$0.00 reconciliation; 0 duplicates; ≥70% sendable drafts; ≥90% local inference; ≤3 false-positive pushes/week).

## STAGE E — Ongoing operations (§11)

- **Daily:** briefs 06:30/17:30; nightly batch 23:00; restic 23:45; SimpleFIN 4×/day.
- **Weekly:** production canaries; `openclaw security audit --deep`; Sun 18:00 review.
- **Monthly:** advisory P&L; subscription audit; voice re-distillation; break-glass chain health-check.
- **Quarterly:** model re-benchmark via eval harness (A/B shadow over last 500 artifacts before any swap); CPA summaries; rules consolidation; restore + power-fail drills; holdout refresh; tax-cloud-log audit.
- **Annual:** IR tabletop; DFS April 15 certification; WISP review; ZDR re-verification; E&O AI representations; risk assessment; exit-strategy update.
- **Budget:** 4–8 h/mo maintenance; run cost ~$190–425/mo technical, ~$625–1,875 fully loaded.

## Non-negotiable invariants (enforce throughout)

- Four hard stops HS1–HS4 immutable; every AUTO class graduates via shadow mode (autonomy.yaml gates).
- Trifecta: no agent holds private data + untrusted content + egress. Cloud escalation of client drafts is owner-initiated only.
- Tax-engagement and privileged artifacts **never** reach cloud, under any reason code (DEC-3/DEC-10).
- All externally-derived strings tainted forever, re-wrapped on every model read.
- Zero third-party skills; extended-stable pinning; Tailscale-only; API-key cloud billing only.
- Never delete email; never mark read; never auto-post to final QBO accounts; nothing auto-purged in v1.
