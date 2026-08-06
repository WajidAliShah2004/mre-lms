# Scope confirmation — LMS v1, 7-day build

**Date:** Aug 6, 2026
**Purpose:** put the Aug 5 scope change in writing before the build starts.
**Tracked as:** D-001 / C18

---

## What changed on Aug 5

The v3.0 developer specification describes a 514–694 hour build across 16 phases, 17–20 weeks. On Aug 5 you said (00:27:47):

> "I don't need it to do business operations… I just need it to determine if it's business, which business, save it, and then let me know what I need to do."

I committed to **7 working days** for that reduced system. This page confirms both halves of that trade — what you get, and what you don't — so neither of us is working from a different memory of it in three weeks.

---

## In scope — the 7 days

1. **Ingest** — email (all accounts), iMessage and texts (read-only), call transcripts, and photographed mail dropped from your phone into a watched iCloud folder.
2. **Classify** — Business or Personal → which business (MRECAI, C.H. Shink, Atlase, MLECA) or which person (you, your mother, your father, Jesse) → category and urgency.
3. **File** — into a deterministic folder tree with a consistent naming convention and a metadata sidecar per document. Duplicates are detected and never filed twice.
4. **Surface** — a running to-do list per person and per entity ("jury duty — call by Sept 12"), with a morning and evening summary to Telegram.
5. **Email hygiene** — flag important mail, unsubscribe from spam safely, and draft replies **for your approval**. Nothing sends without you tapping approve.

Everything runs locally on the Mac Studio. No client or tax data leaves the machine.

## Not in scope — deferred to a later engagement

QuickBooks push · ledger and reconciliation · commission engine · renewal pipeline · tax-season document chase · voice profiles beyond a basic drafting tone · VPS failover · SimpleFIN bank feeds · WhatsApp · calendar automation · e-signature · receivables chasing

These aren't cancelled. They're the roadmap, and the v1 system is built so they can be added without tearing anything up. But they are not in these 7 days and are not part of acceptance.

## Also removed

**Hostinger VPS.** You're cancelling it; the architecture is now Mac-only. One consequence to flag: it was going to be the off-site backup destination, so we need a replacement (item 11 on the requests list). A RAID array in your office is not an off-site backup.

---

## What the system will never do

These are enforced in code, not in instructions to the AI — meaning they hold even if the AI is manipulated by something in an email:

- **Never send anything externally** without an explicit approval tap from you
- **Never delete** an email or a file. Never mark an email as read.
- **Never move money**, sign, or bind you to anything
- **Never send your client or tax data to a cloud AI service** — all processing is local

---

## Schedule

| | |
|---|---|
| Day 0 | Thu Aug 6 (today) — requests sent, decisions pending |
| Days 1–7 | Aug 7, 10, 11, 12, 13, 14, 17 |
| Acceptance with you | Aug 17, or Aug 18–19 if we use the buffer |
| You're away | Aug 20 – Sept 1 |

The seven days are consecutive working days with no slack. Each one depends on the day before it. **If the items on the requests list are late, the schedule moves — it doesn't compress.** The single largest risk to Aug 17 is a decision waiting on a reply, not the engineering.

If we do run short, the first thing cut is call-transcript ingestion. Email, photographed mail, and the to-do list are where you'll see the value, and they stay.

---

## Acceptance

On the final day, with you watching:

| # | Test |
|---|---|
| 1 | Business email with an invoice → filed to the right business tree, task created |
| 2 | Personal email from family with a date → right person, list entry |
| 3 | Photograph of a paper bill → filed within 90 seconds, due-date task created |
| 4 | Marketing email → unsubscribed safely, logged, undoable |
| 5 | A planted fake wire-transfer email → flagged and pushed to you, zero action taken |
| 6 | Client email needing a reply → draft prepared, cannot send without your approval |
| 7 | `/halt` → everything stops within 10 seconds |
| 8 | Restore from backup → database opens, files intact |

Then a recorded walkthrough, handover of the repository, rotation of every credential I hold, and a one-page list of what's deferred so the next engagement is already scoped.

---

## Sign-off

By signing, you confirm the reduced scope above replaces the v3.0 specification for this engagement.

**Matthew R. Epstein** ______________________________  Date ____________

**Developer** ______________________________  Date ____________
