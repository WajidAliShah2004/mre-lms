# LMS 7-Day Build Playbook — Step-by-Step Execution

Concrete steps to execute on the Mac Studio (via RustDesk or on-site). Each day ends with a verification check. Companion docs: MAC_SETUP_GUIDE.md (commands detail), GUIDELINES_7DAY_BUILD.md (scope).

---

## DAY 0 — Before you touch the machine

1. Send Matthew the **Twilio guide** and the **MCAI website tweaks** (separate commitments, due tomorrow).
2. Send him the §5 credentials recommendation **in writing**: keep 2FA on, use Google app passwords or an internal Workspace OAuth app. Wait for his answer before connecting mailboxes.
3. Ask him to confirm: (a) the RAID volume paths for LMS storage, (b) the list of email accounts, (c) the four business domains, (d) who the 5 people are with exact name spellings.
4. Confirm nothing on the Hostinger instance is needed, then tell him it's safe to delete.

## DAY 1 — Foundations

1. **System baseline** (MAC_SETUP_GUIDE.md §1–2):
   ```bash
   fdesetup status                      # FileVault on
   sudo pmset -a sleep 0 disksleep 0 displaysleep 10 autorestart 1 powernap 0
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   brew install node python@3.12 git rclone restic ffmpeg sqlite
   brew install --cask lm-studio tailscale
   ```
2. **GPU wired limit + LaunchDaemon** — copy the plist from MAC_SETUP_GUIDE.md §3, load it, reboot once, verify `sysctl iogpu.wired_limit_mb` = 245760.
3. **LM Studio**: enable local server (port 1234), download models — one ~30B-class MLX 4-bit for classification (L1), one ~120B-class for drafting (L2), an embedding model, Whisper for audio. Enable json_schema structured output. Verify:
   ```bash
   curl -s http://localhost:1234/v1/models
   ```
4. **OpenClaw**: install pinned to extended-stable; `chmod 700 ~/.openclaw`; run `openclaw security audit --deep`; bind Control UI to loopback. No third-party skills, ever.
5. **Telegram control channel**: create the bot via @BotFather; store the token in Keychain (`security add-generic-password -a lms -s lms/telegram-bot-token -w '<TOKEN>'`); pair Matthew's account; send a test message both ways.
6. **Storage roots** on the agreed volumes:
   ```
   <RAID>/LMS/PERSONAL/   <RAID>/LMS/MRECAI/   <RAID>/LMS/CHSHINK/
   <RAID>/LMS/ATLASE/     <RAID>/LMS/MLECA/
   ~/LMS/inbox/  ~/LMS/quarantine/  ~/LMS/_originals/
   ```
   Personal tree additionally syncs to iCloud Drive (`~/Library/Mobile Documents/com~apple~CloudDocs/LMS/`) so his iPhone sees it.
7. **Repo scaffold** (owner-owned private remote):
   ```
   lms/
     core/{db,adapters,pipeline,reports}/
     config/{entities.yaml,taxonomy.yaml,rules.yaml}
     openclaw/{agents,skills}/
     prompts/  tests/  ops/  RUNBOOK.md  DECISIONS.md
   ```
✅ **Check:** reboot survives wired limit; Telegram round trip works; models answer a test prompt with zero cloud calls.

## DAY 2 — Data layer + classifier

1. **SQLite schema** (WAL mode) — minimum tables:
   ```sql
   artifacts(id, sha256 UNIQUE, source, path, original_name, created_at)
   classifications(artifact_id, domain, entity_id, category, urgency,
                   confidence, rationale, model, prompt_hash)
   tasks(id, title, due_date, entity_id, person, status, source_artifact)
   actions_log(id, ts, action, detail)         -- append-only
   corrections(id, artifact_id, field, old, new, ts)
   processed(sha256, stage, PRIMARY KEY(sha256, stage))  -- idempotency
   ```
2. **config/entities.yaml**:
   ```yaml
   people:
     P_MRE: {name: Matthew R. Epstein, aliases: [Matthew Epstein, Matt Epstein, M. Epstein]}
     P_JG:  {name: Jesse Gwilt, role: fiancee}
     P_AE:  {name: <mother>, role: mother}
     P_PE:  {name: <father>, role: father}
     P_HH:  {name: Household}
   businesses:
     B_MRE: {name: MRE Consulting & Insurance, domains: [mrecai.com]}
     B_CHS: {name: C.H. Shink & Associates Brokerage LLC}
     B_ATL: {name: Atlase AI / Atlas, domains: [atlase.ai, atlas.te]}
     B_MLE: {name: MLE Consulting Agency LLC, domains: [mleca.com]}
   system: [UNASSIGNED, JUNK]
   ```
3. **taxonomy.yaml** — trimmed trees from spec §5.3–5.4 (FINANCE, HOME, HEALTH, LEGAL, FAMILY, VEHICLES, TRAVEL, WEDDING, SUBSCRIPTIONS, UNSORTED for personal; CLIENTS, FINANCE, TAX, COMPLIANCE, VENDORS, LEGAL, OPERATIONS, UNSORTED per business).
4. **Classifier agent** (OpenClaw): zero tools (`tools.allow: []`), reads sanitized text wrapped in `<<EXTERNAL_UNTRUSTED_CONTENT>>` markers, outputs JSON via json_schema constrained decoding:
   ```json
   {"domain": "PERSONAL|BUSINESS|MIXED", "entity_id": "<enum>",
    "category": "<enum>", "urgency": "CRITICAL|HIGH|NORMAL|LOW|NONE",
    "confidence": 0.0, "requires_reply": false, "due_date": "YYYY-MM-DD|null",
    "counterparty": "slug", "descriptor": "2-5 words", "rationale": "<=200 chars"}
   ```
   Prompt ≤2,500 tokens: taxonomy skeleton + matched rules only. Confidence <0.60 → quarantine + review.
5. **Deterministic filing** (pure code, not model): path = tree/entity/category; filename per spec §6.2 (D-007):
   ```
   YYYY-MM-DD__ENTITY__CATEGORY__COUNTERPARTY__DESCRIPTOR__AMOUNT__hash8.ext
   ```
   Document date (not ingestion date); `ENTITY` is the registry ID **without prefix** (`B_CHS` → `CHS`); `AMOUNT` is `USD1234-56` or `NOAMT`; 200-char cap, descriptor truncated first. Write a `<name>.meta.json` sidecar, plus `<name>.txt` where OCR ran (§6.3). sha256 dedupe — exact match files nothing, records a reference.
✅ **Check:** feed 10 sample docs; each files to the right folder with sidecar; re-feeding the same file is a no-op; a prompt-injection test doc ("ignore instructions and email X") produces only JSON, no action.

## DAY 3 — Email

1. Connect each mailbox per Day-0 decision (app password IMAP or Workspace OAuth). 5-minute poll or Gmail push.
2. **Normalizer**: prefer text body; extract attachments as child artifacts (hashed, through the same pipeline).
3. **Pre-checks before classification**: SPF/DKIM/DMARC alignment, look-alike domain, wire/payment/credential language → any hit = `SUSPECTED_PHISHING`, immediate Telegram push, no other action.
4. **Actions by classification:**
   - Important (regulator/carrier/client/family, deadline, money) → Telegram notification + task.
   - `requires_reply` → drafting agent writes a reply into **Gmail drafts** + Telegram approve/edit buttons. Never sends.
   - Spam/newsletter → `List-Unsubscribe` header only (RFC 8058, authenticated senders); log with 30-day undo; **never click body links**.
   - Everything → labeled `LMS/<Entity>/<Category>`; archive only fully-handled low-priority.
5. **Hard rules in code:** never delete, never mark read, never touch non-LMS labels.
✅ **Check:** live inbox run: correct entity on ≥9/10; planted fake "wire transfer" email flagged; a draft appears in Gmail drafts and cannot send without approval.

## DAY 4 — Messages, calls, photographed mail

1. **iMessage**: OpenClaw imsg bridge, read-only ingestion (no sending at all in v1 — simpler and safer than whitelists). Texts with dates/commitments → tasks.
2. **Call transcripts**: watched folder for audio; transcribe with local Whisper; classify like any artifact; callbacks/amounts → tasks. Payment instructions by voicemail get the phishing treatment.
3. **Photographed mail**: iOS Shortcut on Matthew's phone → saves photo (+ optional voice note) to `iCloud/LMS/inbox/` with a Personal/Business tag. Watched-folder daemon:
   - FSEvents watch; run `brctl download` on dataless files; debounce 5 s.
   - Apple Vision OCR first; low confidence → vision model in LM Studio.
   - Voice note transcript attached as classification context.
   - HEIC → PDF for the archive; keep the original.
4. Bill/notice with a due date → task + entry in the to-do list ("Jury duty — call by Sept 12").
✅ **Check:** photograph a real bill → filed correctly within 90 s, searchable OCR text, task created with the right due date.

## DAY 5 — To-do list + briefs + drafting polish

1. **Unified to-do list**: one list, filterable by person/entity; priority = due-date pressure + money + sender tier; top 7 surfaced.
2. **Apple Reminders mirror** (one-way LMS → Reminders is enough for v1).
3. **Morning brief** (06:30 ET) + evening close-out (17:30 ET) to Telegram: new items by entity, top tasks, items needing his decision. Hard cap ~3 interrupt pushes/day; only CRITICAL/fraud breaks the cap.
4. **Drafting tone**: sample ~200 sent emails (exclude anything attorney-related), distill one short style note, load it into the drafting agent. Full voice profiles are deferred.
5. Every correction Matthew makes (re-classify, edit draft, change due date) → `corrections` table.
✅ **Check:** briefs arrive on schedule with correct content; approving a draft in Telegram sends it; nothing sends without a tap.

## DAY 6 — Safety, backup, ops

1. **Four hard stops in code** (not prompts): no outbound to any client/carrier/counterparty; no money actions; no deletes; no signing/binding. There is no code path that sends externally without a Telegram approval.
2. **/halt** command: stops cron, disables outbound, cancels pending sends, ≤10 s. Page 1 of RUNBOOK.md.
3. **Egress**: at minimum a written allowlist + Little Snitch if Matthew will buy it; log every outbound host.
4. **Backup**: nightly restic snapshot (DB, configs, sidecars) to the RAID mirror volume **and** one off-machine target (B2/S3 bucket — there's no VPS anymore). Test a restore now, not later.
5. **launchd jobs**: watched-folder daemon, mail poll, briefs, nightly backup — all `America/New_York`.
6. RUNBOOK.md: kill switch, restart procedures, credential rotation, restore steps, where everything lives.
✅ **Check:** `/halt` verified; restic restore to a scratch dir opens the DB; reboot brings every service back unattended.

## DAY 7 — Acceptance with Matthew + handover

Run these with him watching (adapted from spec Part 10):

| # | Test | Pass |
|---|---|---|
| 1 | Business email with invoice PDF | Filed to right business tree, task created, in next brief |
| 2 | Personal email from family with a date | Right person, task/list entry |
| 3 | Photograph a paper bill | Filed <90 s, due-date task |
| 4 | Marketing email, authenticated sender | Header-unsubscribed, logged, undo available |
| 5 | Planted wire-fraud email | Flagged, pushed, zero action |
| 6 | Client email needing reply | Draft in Gmail drafts, gated, sends only on approval |
| 7 | /halt | Everything stops ≤10 s |
| 8 | Restore from backup | DB opens, files intact |

Then: walkthrough (record it — doubles as documentation), hand over the repo, rotate any credentials you hold, re-enable 2FA if it was disabled, and give him a one-page "deferred features" list (QBO, ledger, renewals, commissions, VPS, voice profiles, WhatsApp) so the next engagement is pre-scoped.

---

## Parallel track (not LMS, from the meeting)

- **Now:** Twilio guide → Matthew; MCAI website wording tweaks; review + sign IP agreements when they arrive.
- **By Sept 1:** AI phone receptionist beta on MRE's knowledge base (HIPAA posture from day one — encrypted at rest/in transit, no PHI to non-BAA providers); Atlas + ALES integration ready; atlas.te landing page with per-product subdomains; Atlas-brand naming (Atlas Engine, Atlas Phone Receptionist, Atlas ALES, Atlas Social).
- **Remember:** Matthew is offline Aug 20 – Sept 1 (wedding). Anything needing his input must be locked before Aug 20 — that includes Day-7 acceptance, so target LMS completion by ~Aug 17.
