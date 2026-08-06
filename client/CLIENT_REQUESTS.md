# What I need from you — LMS build

**Date:** Aug 6, 2026
**Why this is one list:** so nothing stalls quietly. Each item below has a day it blocks. If an item is late, that day's work stops — it doesn't shift to the next day, because the days are already back-to-back.

**Timeline reality:** Day 0 is today (Thu Aug 6). The seven build days are Aug 7, 10, 11, 12, 13, 14, and 17. You're away Aug 20 – Sept 1, and the final acceptance run needs you present. There is no slack in that. The items in the first two sections are the ones that decide whether Aug 17 holds.

---

## Today or tomorrow — everything downstream waits on these

### 1. Email access method — *blocks Day 3 (Aug 11)*
See the separate note, [CREDENTIALS_RECOMMENDATION.md](CREDENTIALS_RECOMMENDATION.md). Reply with **A**, **B**, or **keep the original plan**. No mailbox connects until you do. *(C9)*

### 2. A private git repository you own — *blocks Day 1 (Aug 7)*
A private repo under **your** account, not mine, with push access for me. This is where the whole system lives; you keeping ownership is deliberate, so nothing is stranded in a contractor's account. Everything I build after Day 1 goes into it, so this one blocks the most. *(C1)*

### 3. Which RAID volumes hold LMS data — *blocks Day 1 (Aug 7)*
Which volumes on the 12 TB Thunderbolt array should hold the file trees, the models, and the repo. You mentioned some of the array is encrypted and some isn't — I'd like to encrypt every volume that will hold your documents, in place. **Need your OK on that**, and confirmation of what's currently encrypted. *(C5)*

### 4. Hostinger — safe to delete? — *blocks nothing, but do it before you forget*
Confirm nothing on that instance is still needed, then cancel it. We removed it from the architecture on Aug 5. *(C17)*

### 5. Scope sign-off — *no deadline, but please don't skip it*
[SCOPE_SIGNOFF.md](SCOPE_SIGNOFF.md) — one page, confirms in writing what we cut on Aug 5. *(C18)*

---

## Before Day 1 ends (Fri Aug 7)

### 6. Telegram
Account paired with 2FA enabled, and your Telegram **user id** (not the @handle). Telegram is the only way you'll approve drafts and the only remote kill switch, so it gets locked down properly. *(C2)*

### 7. Tailscale
An account/tailnet, and login on the Mac. This is how the machine is reachable without opening a single public port. *(C3)*

### 8. How I reach the Mac remotely — **answer before I turn on the network lockdown**
Right now it's RustDesk. Two choices:
- **Keep RustDesk** — works today, but it's a third-party relay sitting outside the otherwise-locked-down setup, and it gets removed at handover.
- **Tailscale + macOS Screen Sharing** — my recommendation. No third party in the path.

This one is time-sensitive in a specific way: once outbound network filtering is on, an un-allowlisted remote-access tool stops working, and if I get that wrong nobody can reach the machine remotely until someone walks over to it. *(C4)*

### 9. Who unlocks the Mac after a power cut
FileVault means that after a power failure the machine reboots to a locked screen and **nothing starts until a human unlocks it**. There's no way around that which doesn't defeat the encryption. I need a name, and a way that person finds out it happened. Planned restarts I can handle remotely; unplanned ones need a person. *(C8)*

### 10. FileVault recovery key
Held by you, offline, in a sealed envelope. Also: please confirm the keys that were exposed on Aug 6 have been rotated. *(C7)*

### 11. Off-site backup destination
A Backblaze B2 or AWS S3 account and credentials. With the VPS gone there's no off-machine copy of anything, and a RAID array is not a backup — it's one flood, one theft, one bad write away from being zero copies. Cheap (a few dollars a month at this size). *(C6)*

---

## Before Day 3 (Mon Aug 11) — email

### 12. Every email address
The complete list across personal and all four entities. Not just the main ones — anything that receives real mail. *(C10)*

### 13. Google Workspace
Admin access, plus four Shared Drives created: **MRECAI**, **C.H. Shink**, **Atlase**, **MLECA**. If you pick Option B on the credentials note, the OAuth grant happens here too. *(C11)*

---

## Before Day 2 (Mon Aug 10) — so the system files things correctly

### 14. Entities and people
- Legal names and EINs for all four businesses, from the formation documents
- Every name variant that appears in real mail (e.g. "Matthew Epstein", "Matt Epstein", "M. Epstein")
- Exact spellings for the five people

Boring, and it's the thing that determines whether a document lands in the right folder or the wrong one. Name variants especially — mail doesn't arrive addressed the way the registry expects. *(C16)*

---

## Before Day 4 (Wed Aug 12) — messages and photographed mail

### 15. iCloud Drive
Signed in on both the Mac and your iPhone, and confirm the personal folder root. *(C12)*

### 16. iMessage
Messages signed in on the Mac, Full Disk Access granted, and tell me which group chats you want included — default is none. Read-only; the system will never send a text. *(C13)*

### 17. The photo shortcut
I'll send you an iOS Shortcut to install. It needs to actually get used — snap a photo of a bill, tap Personal or Business, and it files itself. This is the feature you'll notice most, and it's the one that depends on you. *(C14)*

### 18. Call transcripts
Where they come from — Retell, Twilio, or carrier voicemail — and read access.
**Fair warning:** if the week runs tight, this is the first thing I cut. It's the least visible of the four inputs. *(C15)*

---

## Purchases — deferred, not forgotten

| Item | Cost | Status |
|---|---|---|
| UPS, 1000–1500 VA | ~$150–250 | Deferred. Until then, the documented power-recovery procedure (item 9) is the mitigation. |
| 2× hardware security keys | ~$60 | Deferred. The *decision* on item 1 is not deferred. |

---

## Please don't set these up

QuickBooks Online, SimpleFIN, Twilio, WhatsApp, Retell, PandaDoc/DocuSign, Dropbox, Slack/Teams/Signal, LinkedIn.

All outside the 7-day scope. They stay blocked at the network level until something actually needs them — which is the point of the lockdown.

---

*Reply inline on this document or however's easiest. I'll log each answer in the decision record and mark it off.*
