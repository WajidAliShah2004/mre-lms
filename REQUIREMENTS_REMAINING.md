# What remains — only what Matthew asked for

Reconciled Oct 7 2026 against the repo at `171dab9`. Replaces the "remaining" lists in PHASES.md, BUILD_PLAYBOOK.md and the Sept 10 summary as the list to work from. Decision: D-064.

## The rule

Every item below traces to a document of Matthew's:

| Code | Source |
|---|---|
| **MTG** | Aug 5 meeting, his own words (00:29–00:30) |
| **SPEC** | v3.0 developer specification — still the blueprint (the addendum's first line integrates into it) |
| **ADD** | Task Delegation and Document Management Addendum, Sept 28 |
| **SETUP** | His answers on *System Setup and Access Requirements* |

Where the addendum and the spec disagree, the **addendum wins** (it is later). Items that exist only in our own planning documents, or that a later document of his supersedes, are removed (§3). Items the spec requires but that need his decision because the addendum changes them are in §2, not resolved either way.

---

## 1. Remaining

### Intake
| # | Item | Source | Blocked on |
|---|---|---|---|
| R1 | 5th mailbox `matthew@atlase.ai` (separate mailbox or alias?) | MTG "all my emails", SETUP | Matthew: which it is |
| R2 | iMessage / texts, read-only | MTG, SPEC Phase 10, ADD §6 | Full Disk Access; the Mac's Messages is on `mattyeps@gmail.com`, which is OpenClaw's Apple ID (D-078), so likely right; his iPhone's Send & Receive confirms it (D-068); Text Message Forwarding for SMS. Scope: **all chats, hourly** (D-065) |
| R3 | Call transcripts, voicemail, Google Voice, phone logs | MTG, SPEC DEC-5, ADD §6 | Matthew: where transcripts come from (C15) |
| R4 | Photo Shortcut installed and used on his phone | MTG, SPEC Phase 5, ADD §0 | Matthew: install + first run. Apple ID is `mattyeps@gmail.com` (D-078); the Mac's iCloud must be moved to it first |
| R5 | Google Drive, Dropbox, whole iCloud Drive, approved Mac folders — **read-only** | ADD §4, SPEC Q68 (Dropbox) | Scope = everything (D-065). Matthew: which Dropbox and Google accounts; one read-only grant each |
| R6 | Calendar invitations and notes as a source | ADD §1, §6, SPEC §7.6 | — |
| R7 | ~~Coverage register: every source, its method, permissions, last checkpoint, gaps (WhatsApp listed as "no supported path")~~ **Built Nov 3 (D-069)**: `config/sources.yaml` (18 sources), `source_checks`, `ops/coverage.py`; mail records every pass. Shown in the brief since the merge (D-072) | ADD §4, §6 | — |
| R8 | Strict read-only mode for every new source (no move, no rename) | ADD §0 | — |
| R39 | Poll `mreconsultinginsurance@gmail.com` (business mail goes there) | Matthew, Oct 19 (D-065) | **Done Nov 5.** Routed to B_MRE (MRE Consulting & Insurance LLC); signed in through the External app, `--check` works, 21-day backfill filed 130 of 131 listed messages. The twice-daily mail job now polls it |
| R40 | WhatsApp, hourly, read-only | Matthew, Oct 19 (D-065) | No supported path; Matthew: OK to try the unofficial Mac-app route |

### Email hygiene
| # | Item | Source |
|---|---|---|
| R9 | Unsubscribe spam — `List-Unsubscribe` header only, logged, 30-day resubscribe log | MTG, SPEC Q33–Q35 |
| R10 | ~~Tell him when an important email arrives (push; 3/day baseline, 1/hour max outside briefs)~~ **Built Nov 4 (D-077)**: Telegram via @MREOCbot for CRITICAL, legal/tax, phishing and stalled jobs; **installed on the Mac Nov 4** ([lms/ops/NOTIFY_APPLY.md](lms/ops/NOTIFY_APPLY.md)). Not yet: ACT NOW escalations, $2,500 anomalies **Nov 5 (D-080):** two-way: /lms commands on WhatsApp and @MREOCbot. | MTG, SPEC §8.8 |
| R11 | Draft replies for his approve / change / send; nothing sends without approval **Nov 5 (D-080):** approvals answerable from his phone (/lms approve N yes); drafting replies still to build. | MTG, SPEC, ADD level B |
| R12 | Drafting in his voice (style from sent mail; attorney mail excluded) | SPEC (voice corpus) |

### Tasks, briefs, delegation
| # | Item | Source |
|---|---|---|
| R13 | ~~Task ledger v2: the addendum's ~20 fields, 10 states + 5 exception states, unique event key, one task per obligation across sources~~ **Built and applied on the Mac Oct 19 (D-066)**: 33 tasks migrated, 3 possible duplicates for review. Approval objects (R19) and the dashboard (R22) attach to its hooks | ADD §2 |
| R14 | Apple Reminders mirror (one-way) | SPEC Q127 |
| R15 | ~~Daily digest; escalate imminent obligations at once; weekly backlog review; TINs, health details and credentials redacted from digests~~ **Built Nov 3 (D-072)**: redaction at render time, Monday backlog, ACT NOW escalation once per task. Push delivery of escalations waits on R10 | ADD §2, §5, SPEC Q152 |
| R16 | Hourly refresh of active sources (WhatsApp, iMessage per D-065; email to confirm), every 3 h for the rest, evening communications sweep, daily reconciliation. **Missed checks are now counted and disclosed (D-069)**; schedules, sweep and reconciliation remain | ADD §6 |
| R17 | Focused questions to Matthew, answers stored as scoped rules with provenance | ADD §2 |
| R18 | Coordinator + specialists (Communications, Documents, Finance, Calendar, Business ops, Reviewer), least-privilege, read-only enforced by filesystem permissions and separate credentials | ADD §1, §5 |
| R19 | ~~Approval matrix A–D, routine-action mandate, policy engine outside the model, approval objects that expire~~ **Built Nov 3 (D-067)**: `policy.py`, `approvals.py`, `ops/approve.py`; trial mode under the hard stops. `config/mandates.yaml` is empty until Matthew's call (X1). No executor exists yet | ADD §3 |
| R20 | Execution-ready proposals: ≤3 options, code only for the recommended one | ADD §3 |
| R21 | Capability estimate per task (OpenClaw / ChatGPT / Claude %, human residual) | ADD §6 |
| R22 | ~~Dashboard (business vs personal, entities, overdue, blocked, approvals, hours)~~ **Built Nov 3 (D-073)**: static `Dashboard.html` beside the brief. Not scheduled yet; hours wait on R21 | ADD §6 |
| R23 | ChatGPT master to-do: canonical tracker file + change summary; import the Sept 24 seed files (Matthew resending; account not named, D-065) | ADD §6 |

### Documents
| # | Item | Source |
|---|---|---|
| R24 | Managed root `iCloud Drive/OpenClaw` with the addendum's folders | ADD §0, §4 — blocked on §2.2 |
| R25 | ~~Near-duplicate candidates (text/visual); exact dedup already built~~ **Text built Nov 3 (D-074)**; visual comparison is a hook, to calibrate on the Mac | ADD §4 |
| R26 | ~~Reconcile bill ↔ statement ↔ proof of payment; missing statements; duplicate invoices; tasks for renewals~~ **Built Nov 3 (D-075)**; shown in the brief as CHECK BEFORE PAYING. Not scheduled yet. Unanswered mail waits on a sent-mail source | ADD §4 |
| R27 | Undo for reversible managed-folder changes; a retention policy | ADD §5 |
| R28 | ~~Backfill in batches, dedup report, storage report (original bytes, copied, saved, reclaimable)~~ **Built Nov 3 (D-074)** for mail; other sources' backfill waits on R5 | ADD §4, §7 |

### Security, backup, handover
| # | Item | Source |
|---|---|---|
| R29 | Off-site backup to Backblaze (private bucket, bucket-only key). **Built inert Nov 3 (D-071)**; Matthew: create the private bucket and a bucket-restricted key ([lms/ops/OFFSITE_APPLY.md](lms/ops/OFFSITE_APPLY.md)) and give D-012's written sign-off. Follow-up: the local repository is never pruned | SETUP, SPEC §11.4 |
| R30 | ~~Rotate the @MREOC18bot token (shared as a screenshot)~~ **Done Nov 4**: rotated, re-stored, `verify_setup` [11] asks Telegram to accept it | ADD §5 (credential revocation) |
| R31 | ~~`gateway.auth.token` into the Keychain~~ **Already done Aug 7 (D-016)**: exec SecretRef to `/usr/bin/security`, token rotated. Confirmed on the Mac Oct 3: `secrets audit` plaintext=1, and that one is the LM Studio `apiKey` placeholder, not the gateway token. Listed here in error on Oct 7 | ADD §5, SPEC §8.4 |
| R32 | Weekly `security audit --deep` job (built, not installed) | SPEC Q216 |
| R33 | Outbound egress allowlist — deferred to handover (D-063) | SPEC §4.1 principle 8 |
| R34 | ~~Model health ping (5 min) with reload and alert~~ **Built Nov 3 (D-070)**; install on the Mac per [lms/ops/MODEL_HEALTH_APPLY.md](lms/ops/MODEL_HEALTH_APPLY.md). A failure is pushed to Telegram by R10 (D-077, installed Nov 4) | SPEC §4.6.5, ADD §5 |
| R35 | Repo transferred to `Mleca18`; RustDesk removed at handover | SETUP item 1, SPEC §12.4 |
| R36 | DMARC on mrecai.com, mleca.com, atlase.ai, atlaseai.com. DNS is at IONOS and the developer has the logins (D-078) | SPEC Rec 16 |

### Acceptance
| # | Item | Source |
|---|---|---|
| R37 | The addendum's per-phase acceptance tests and its 16 demonstration scenarios | ADD §7 |
| R38 | Spec Part 10 tests: external port scan, TTFT p95 < 3.5 s, `/halt` ≤ 10 s, restore, warm and cold reboot | SPEC §10.3, §11.2 |

---

## 2. Conflicts — Matthew decides, not us

| # | Spec says | Addendum says | Recommendation |
|---|---|---|---|
| X1 | Hard stops: never move money, never send without approval | Level C: pay verified recurring bills, send routine replies, inside a mandate | Shadow mode per action class first; lift a hard stop only by a recorded decision per class. No payment rail exists yet. **Oct 19: accepted in principle; bills and bank on a call (D-065)** |
| X2 | DEC-8: business documents to Google Shared Drives per entity | Managed copies **only** in `iCloud Drive/OpenClaw` | Follow the addendum. **Oct 19: iCloud+ and ADP done (D-065); Nov 4: the Apple ID is `mattyeps@gmail.com` (D-078)**; the Mac's iCloud must be moved to it |
| X3 | Entities: no Atlase products | Atlase products are entities | Ask: the product names |
| X4 | Tailscale-only remote access (spec S3) | — | D-063 chose RustDesk; R38's port scan cannot pass "outside Tailscale" until that is reversed |

Open questions for him are ADDENDUM_2026-09-28_GAP_ANALYSIS.md §4. Answered Oct 19 (D-065): payments in principle, iCloud, Drive/Dropbox scope, hourly sources, `mreconsultinginsurance@gmail.com` (yes, add it as a mailbox: **R39**).

---

## 3. Removed — not required by any document of Matthew's

| Removed | Why |
|---|---|
| Four Google Shared Drives + admin access as filing destinations (C11) | Superseded: the addendum puts managed copies only in iCloud `OpenClaw`. Google Drive stays as a **read-only source** (R5) |
| Gmail `LMS/` labels, auto-archive, `LMS/Cold-Outreach` (spec Q28, Q29, Q36) | Superseded: the addendum requires read-only source access; `gmail.readonly` (D-039) already enforces it |
| "Flag important" as a Gmail flag | The ask is to *tell him* (R10), not to mark the mailbox |
| Separate 17:30 evening brief | Our own plan (BUILD_PLAYBOOK Day 5); superseded by the addendum's evening sweep and daily reconciliation (R16) |
| Day-7 eight-test acceptance table | Our adaptation of spec Part 10; replaced by the tests themselves (R37, R38) |
| C17 — where vehicle documents go | Already answered by the spec: Q13, vehicles are a category under HOUSEHOLD (`VEHICLES/`) |
| C18 — what to do with unsolicited sales mail | Already answered by the spec: Q36, file as cold outreach, weekly digest, never delete (without the label, per the row above) |
| "Review the unused `openclaw` Google Cloud project" | Our housekeeping note, not a requirement; 0 users ever |
| Packet capture as the zero-egress proof | Our method, not his requirement; R38 keeps the TTFT test |
| C.H. Shink — entity data, and whether it is still an entity | Ignored for now (Oct 7): the client has provided nothing for it, and the addendum does not list it. `B_CHS` stays a placeholder; mail mentioning it goes to review |

## 4. Dropped earlier that his documents do require

For the record, so these are not taken as optional. D-062 dropped them by developer judgment; each has a source of Matthew's:

| Dropped (D-062) | Source that requires it |
|---|---|
| FileVault recovery key custody | SETUP item 11 |
| Parents' full names | SETUP item 10, SPEC Q15 |
| Password rotation for the five mailboxes | SPEC §12.4 (rotate at handover); passwords appear in plain text in SETUP |
