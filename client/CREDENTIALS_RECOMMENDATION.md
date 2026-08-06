# Recommendation: email account access for the LMS build

**To:** Matthew
**Re:** How I get mailbox access — recommending against disabling 2FA
**Decision needed by:** end of day Fri Aug 7, 2026
**Tracked as:** D-008 / C9

---

## Short version

On Aug 5 we agreed I'd get into the mailboxes by you sharing passwords and turning 2FA off. I'd like to do it differently. **Google app passwords** (or an internal Workspace OAuth app) give me exactly the same access, with 2FA left on and no naked credentials moving between us.

There's no cost difference and no schedule difference. If you'd rather stick with the original plan, that's your call — I'd just want it in writing, for reasons below.

## Why I'm raising it

Your practice handles client NPI and tax data. Three things follow from that:

1. **Disabling 2FA on an account holding client NPI is the single change most likely to matter later.** Not because I expect a breach — because if one ever happens, "MFA was turned off on the mailbox" is the first fact anyone establishes, and it's the one that's hard to explain.
2. **Your E&O carriers.** The engineering annex we'll need for them documents the access controls on this system. "2FA on, scoped app passwords" is a paragraph. "2FA disabled, credentials shared by message" is a conversation, and possibly a premium question.
3. **Shared passwords are permanent until rotated.** An app password is scoped to one client, shows up as its own line in your account, and you revoke it with one click without touching anything else — including at handover, when you'll want to cut my access cleanly.

## What I'm proposing

**Option A — Google app passwords (simplest, works today).**
2FA stays on. You generate one app password per mailbox, send it to me, and I use it for IMAP. Each one is independently revocable and visible in your security settings. Nothing about your day-to-day login changes.

**Option B — internal Workspace OAuth app (better, slightly more setup).**
An OAuth app inside your own Workspace, scoped to just the Gmail access the LMS needs. No password exists to share at all, access is scoped rather than total, and it survives password changes — which matters, because a password reset silently kills an app password and the LMS just stops ingesting mail until someone notices.

Option B is the better end state. Option A is fine to start with and can be migrated later. Either works for the 7-day build; I'd take whichever you can turn around fastest, because **no mailbox connects until this is settled**, and mailbox connection is Day 3.

Personal (non-Workspace) accounts: app passwords, or IMAP on a verified account.

## If you'd rather keep the original plan

Then I'll do it that way — I work for you. I'd ask for three things:

1. A one-line written acknowledgement that 2FA was disabled at your direction, on accounts holding client NPI.
2. Credentials stored only in the Mac's Keychain, never in a file, never in the repo, never in a message thread that stays around.
3. Full rotation and 2FA re-enabled at handover. That's the offboarding protocol in the spec (§12.4) and I'll run it either way.

To be straightforward about it: this protects me as well as you, and we're about to sign IP and data agreements that assume a certain standard of care. I'd rather the access method match what those documents say.

## What I need back

Just a reply naming **A**, **B**, or **the original plan**. I'll record it in the decision log and move.

---

*Prepared Aug 6, 2026. Reference: 7-day build plan §5; developer specification v3.0 §12.4, Q214, Rec 22.*
