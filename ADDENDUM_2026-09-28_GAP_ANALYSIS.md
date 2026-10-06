# Addendum of Sept 28 2026 — gap analysis against what is built

Source: *OpenClaw Task Delegation and Document Management Addendum* (Matthew R. Epstein, 28 Sept 2026), read in full on Oct 6, together with the three Business Owner Records of Sept 27 (MRECAI, MLE, Atlase AI Inc.). Measured against the repo at `3d497df` and the Mac as verified on Oct 6.

The addendum is integrated **into** the existing blueprint (its first line says so). It does not replace the spec, and where it conflicts with a rule the build enforces today, that is listed below as a decision for Matthew, not silently resolved either way.

---

## 1. Already meets the addendum

| Addendum requirement | Where it stands |
|---|---|
| Pause switch that stops scheduled actions (§5) | `/halt` on @MREOC18bot: every LMS/OpenClaw job disabled in 0.3 s, survives reboot; resume only at the Mac (D-059) |
| Secrets in the system credential store, never in notes/prompts/logs (§5) | Keychain throughout; tokens never on argv or in git (D-016, D-039, D-057, D-059) |
| Email, PDF, OCR text treated as untrusted; embedded instructions cannot act (§5) | Classifier is tool-less; schema-constrained output; phishing pre-checks before classification (D-040, D-056, `rules.yaml`) |
| Read-only source access for email (§4) | `gmail.readonly` on 4 mailboxes; Google itself refuses delete/label/send (D-039, D-061) |
| Exact duplicates by cryptographic hash of complete bytes (§4) | sha256 identity; `_originals/<sha256>`; `duplicate_refs` table |
| Preserve the original; OCR scans; low confidence goes to review (§4) | PDFKit/Vision OCR ladder; quarantine with reasons; unreadable formats kept, not skipped (D-029, D-055) |
| Log source access, classification, filing, approval (§5) | Append-only `actions_log` with triggers that refuse UPDATE/DELETE |
| Backup and tested restore (§5) | Nightly restic, restore tested (D-035, D-050); off-site target still open (D-012) |
| Alerting on failed jobs (§5) | The brief reports stalled jobs (D-051) |
| Daily digest (§2) | 06:30 brief to iCloud (D-046); Telegram delivery possible now (Phase 7) |
| Entity separation for personal / MRECAI / MLE / Atlase AI (§0) | `entities.yaml` with legal names, aliases, EINs on the Mac only (D-057) |
| Do not infer an entity from a logo or alias alone when evidence conflicts (§0) | Recipient address routes first; ambiguous → quarantine, not a guess (D-040) |
| Phone intake by supported share/capture paths only (§0) | iOS Shortcut → iCloud inbox → watcher (C14); no background app inspection promised |

---

## 2. Conflicts that need Matthew's decision

These are not build work. Each one changes a rule that is enforced in code today.

### 2.1 Autonomous payment and autonomous replies (approval level C)
The addendum allows *"pay a verified recurring bill"* and *"send a routine reply to a known party"* without per-item sign-off, inside a mandate. `config/rules.yaml` today carries **hard stops that are asserted in tests and "must never become configurable"**: `never_move_money`, `never_send_without_approval`. Those come from the v3.0 spec (HS1–HS8).

Also, **no payment rail exists**: no bank or bill-pay API is connected, and the addendum itself says *"do not claim either can … finish a transaction without a tested route."* Sending needs a new Gmail scope (`gmail.send` or `gmail.compose`) on every mailbox, which ends the "Google refuses" guarantee of D-039.

**Recommendation:** build levels A (observe) and B (prepare) fully; build level C in **shadow mode** (spec §8.5): the system logs exactly what it *would* have paid or sent, for 1–2 weeks per action class, and graduates a class only on Matthew's explicit sign-off against its accuracy. Lifting a hard stop is recorded as its own decision, per class, with the mandate (§3 of the addendum) attached.

### 2.2 Where managed copies live
The addendum: managed copies **only** inside a new iCloud Drive folder **`OpenClaw`** (00 Inbox, 01 Personal, 02 MRECAI, 03 MLECA, 04 Atlase AI Inc, 05 Atlase Products, 90 Review, 99 System Manifests). Today the archive is on the encrypted 12 TB array, `/Volumes/MacStudioHD/LMS/archive` (D-019).

Facts that bear on it:
- **The Mac's Apple ID (`mreconsultinginsurance@gmail.com`) appears to have no iCloud+.** macOS showed "Private Relay is no longer available because you aren't subscribed to iCloud+" on Oct 6. Without iCloud+, iCloud Drive is 5 GB; a document archive outgrows that.
- **Standard iCloud is not end-to-end encrypted.** Tax records and EIN-bearing documents in iCloud are readable by Apple unless **Advanced Data Protection** is on. The addendum requires stored documents to be encrypted (§5).
- **Which Apple ID is on Matthew's iPhone?** The phone Shortcut writes to the phone's iCloud. If it differs from the Mac's, the watched inbox never receives anything. Not yet confirmed.

**Recommendation:** iCloud+ (2 TB) plus Advanced Data Protection on the Apple ID the iPhone uses, signed in on the Mac; then the managed root becomes `iCloud Drive/OpenClaw` with the addendum's structure, and the array keeps the restic repository and `_originals`. Until iCloud+ exists, the array stays the managed root.

### 2.3 "Originals are never moved"
The watcher today **moves** a handled file from `iCloud Drive/LMS/inbox` into a subfolder of that inbox (`_retire`, never deletes). That folder is the system's own drop-box, which the addendum's `00 Inbox` corresponds to, so moving within it is consistent with the intent. But **every new source** (Dropbox, Google Drive, Mac folders, iCloud Drive at large) must be read in a strictly read-only mode: no move, no rename. That mode does not exist yet.

### 2.4 Entities
The addendum lists personal, MRECAI, MLECA, Atlase AI Inc, and **each Atlase product as its own entity**. The registry has no Atlase product entities (names needed). The registry **does** have C.H. Shink (`B_CHS`), which the addendum does not mention: confirm whether it remains an entity (C16 is still open on it).

MRECAI's owner record sets its perimeter as **"insurance business only"**: AI consulting and development work belong to MLE. That is a classification rule worth encoding (`rules.yaml` overrides), so MRECAI is not credited with MLE's work.

### 2.5 Polling cadence
The addendum: active sources **hourly on the hour**, others at least every 3 hours, plus an evening sweep. Today mail is polled at 07:00 and 19:00. Hourly polling of 4–5 mailboxes is within Gmail quotas but multiplies model load (OCR + L1 per new message). Decide which sources are "active".

---

## 3. Build work, by the addendum's own phases

### Phase 1 — Foundation
| Item | Status |
|---|---|
| Source inventory / coverage register | **Missing.** Needs a register file per source: method, permissions, last checkpoint, gaps |
| Managed iCloud root with the addendum's structure | **Blocked on 2.2** |
| Canonical task ledger | **Partial.** `tasks` has 13 columns and 3 states (OPEN/DONE/DISMISSED). The addendum requires ~20 fields (source link, owner, entity, sensitivity, action risk, approval policy, permitted tools, retry count, verification evidence, …) and a 10-state lifecycle with exception states, an idempotent event key, and supersession history |
| Approval engine and policy engine separate from the model | **Missing.** Today the only approval surface is the planned Telegram tap; no mandate object, no approval object with expiry |
| No original changed; unauthorized action blocked; restore works | Restore ✓. The other two need the read-only source mode (2.3) and the policy engine |

### Phase 2 — Intake
| Source | Status |
|---|---|
| Email | **4 of 5 mailboxes live** (D-061). matthew@atlase.ai deferred. Cursor: the 2-day overlap window (D-040) |
| iCloud Drive | Only the `LMS/inbox` drop folder. Read-only whole-drive scan: missing |
| Dropbox | **Missing** (spec deferred it; addendum requires it). Needs Dropbox app + read-only scope |
| Google Drive | **Missing.** Needs `drive.readonly` per Workspace app + personal app |
| Mac watcher (approved local directories) | **Missing** beyond the inbox |
| Phone share/capture | Shortcut designed (C14); install and first-run on Matthew's phone still owed |
| iMessage / texts | **Missing.** Blocked on the service-account decision (Phases item 7, D-009) and Full Disk Access |
| Google Voice, voicemail, call transcripts | **Missing** (C15; spec's first cut if time slips) |
| WhatsApp / other chats | **Missing**; spec roadmap. Goes in the register as "no supported path" until one is chosen |

### Phase 3 — Delegation
Coordinator + specialists (Communications, Documents, Finance, Calendar, Business ops, Reviewer). Today: three **tool-less** agents (D-056) and all real work runs in `core/` without agents. The addendum's specialists need *scoped* tools (read sources, write only the managed root). That is a deliberate change to D-015/D-021's zero-tools posture and must be done per agent, with read-only enforced by filesystem permissions and separate credentials (the addendum's own last line), not by tool filtering.

Also missing: hourly refreshes, the evening communications sweep with daily reconciliation, capability estimates per task (OpenClaw / ChatGPT / Claude %, human residual), and the dashboard.

### Phase 4 — Action
Routine mandates, anomaly review, payment executor, the "execution-ready proposal" format (≤3 options, code only for the recommended one). All missing; gated on 2.1.

### Phase 5 — Backfill
Historical batch scan, dedup report, near-duplicate candidates (text/visual similarity — missing; exact dedup exists), storage dashboard (original bytes by source, bytes copied, bytes saved, reclaimable after approved cleanup). Missing.

### ChatGPT master to-do project (§6)
No supported API reads or writes a ChatGPT project's chat history; the addendum says so itself. The workable route is the one it names: a **canonical structured tracker file** (the task ledger exported) attached or linked in the project, plus a generated change summary for Matthew to paste. Needs: access to his ChatGPT account/project, and the **Sept 24 seed files** (working-session master list, action tracker, exhaustive-context files in the "Master To Do Lists" collection).

### The 16 demonstration scenarios
Today the system can show: (1) photographed receipt [via Shortcut, once installed]; (9) forged instruction in an attachment [classifier is tool-less; needs a recorded test]; (11) failed connector and replay [per-message and per-mailbox guards, D-053/D-058]. The rest depend on the work above.

---

## 4. Questions for Matthew

1. **Autonomous payments and replies (2.1):** accept shadow mode first, graduating per class? Which bills and which reply types are the first candidates? Which bank/rail would pay them?
2. **iCloud (2.2):** subscribe to iCloud+ and turn on Advanced Data Protection? Which Apple ID is on your iPhone?
3. **Atlase products:** the list of product names that are separate entities.
4. **C.H. Shink:** still an entity?
5. **Dropbox and Google Drive:** which accounts and which folders are in scope?
6. **ChatGPT:** which account holds the "master to-do list" project; share the Sept 24 seed files.
7. **Communications:** which sources count as "active" (hourly); is iMessage in scope (needs the Phase 1 item 7 decision); Google Voice?
8. **mreconsultinginsurance@gmail.com:** does business mail go there?

---

## 5. Recommended order

1. Answer 2.2 (storage) and 2.4 (entities): every later step writes into that structure.
2. Task ledger v2 + coverage register + policy/approval objects (Phase 1). No new permissions needed.
3. Google Drive and Dropbox read-only, Mac folders read-only, Shortcut installed (Phase 2).
4. Hourly refresh + evening sweep + daily reconciliation into the brief/Telegram (Phase 3, communications half).
5. ChatGPT tracker file exchange.
6. Coordinator/specialists with scoped tools; reviewer agent.
7. Level C in shadow mode, after 2.1 is decided.
8. Backfill and storage dashboard.

Phase 8 (network hardening) is independent and can run in parallel; its egress allowlist must include each new source's API hosts (Dropbox, Drive, Telegram).
