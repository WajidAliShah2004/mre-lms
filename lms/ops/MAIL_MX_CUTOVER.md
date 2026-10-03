# Mail is not missing. It is being delivered to iCloud.

Diagnosis and cutover runbook — `mrecai.com`, 2026-09-17.

> **Status, Oct 3 2026 — cutover done (D-054).** `mrecai.com` was removed from
> iCloud+ Custom Email Domain. Checked from outside via 8.8.8.8 and 1.1.1.1:
>
> | Step | State |
> |---|---|
> | 2 MX | `1 smtp.google.com` — done (TTL 3600) |
> | 4 SPF | `v=spf1 include:_spf.google.com ~all` — done, and narrower than §6 planned: `bolt.im` and `_spf-us.ionos.com` were also removed |
> | DKIM | `google`, `s1`, `s2` all still published — unchanged, correct |
> | 5 DMARC | **still missing** on `mrecai.com` |
> | 6 Migration | **open** — mail delivered to iCloud before the cutover is still in iCloud |
> | 3/§3 Pre-flight | **unconfirmed** — prove with an outside test message (D-054) |
>
> `mleca.com` (`smtp.google.com`) and `atlase.ai` (`aspmx` set) already pointed at Google.

---

## 1. What is actually wrong

`mrecai.com`'s MX records point at Apple, not at Google:

```
mrecai.com.  3600  IN  MX  10 mx01.mail.icloud.com.
mrecai.com.  3600  IN  MX  10 mx02.mail.icloud.com.
```

MX is the only thing on the internet that decides where a domain's mail is
*delivered*. Every message sent to `matthew@mrecai.com` is handed to iCloud
Mail and stops there. Gmail is not in the delivery path at all.

The Gmail mailbox has only ever been fed by an **iCloud → Gmail forward**, which
is what `SUMMARY_2026-09-10.md` records as *"the mailbox forwards and forwarding
breaks SPF"*. When that forward stops — or when iCloud decides a message is junk,
because iCloud does not forward what it junks — the message never reaches Gmail,
and `ops/poll_mail.py` correctly reports an empty mailbox.

**OpenClaw is not the cause and could not be.** The Gmail grant is
`gmail.readonly` (D-039). It cannot delete, archive, mark read or relabel
anything — Google refuses at the API, not our code. If mail is absent from the
Gmail inbox, it never arrived.

### The rest of the DNS picture

| Record | Current value | Verdict |
|---|---|---|
| MX | iCloud (`mx01`/`mx02.mail.icloud.com`, pri 10) | **Wrong host — this is the bug** |
| SPF | `v=spf1 include:bolt.im include:icloud.com include:_spf.google.com include:_spf-us.ionos.com ~all` | Valid, but **9 of 10** permitted DNS lookups. One more `include:` makes it a permerror and *everything* fails SPF |
| DKIM | `google._domainkey` — 2048-bit, published | OK |
| DKIM | `s1`/`s2._domainkey` → SendGrid | OK (present; SPF does not list SendGrid, so SendGrid mail aligns on DKIM only) |
| DMARC | `_dmarc.mrecai.com` — **NXDOMAIN** | Missing. Also missing on `mleca.com` and `atlase.ai` |
| Nameservers | IONOS (`ui-dns.*`) | DNS is edited in the IONOS control panel |
| MX TTL | 3600s (1 hour) | Lower before cutover |

DKIM and Google SPF being present, with MX pointing elsewhere, is the signature
of a Workspace that was configured for **sending** and never switched over for
**receiving**.

---

## 2. Before changing anything — today

1. **Sign in to <https://icloud.com/mail> as Matthew.** The missing mail is
   sitting there. Nothing has been lost.
2. **Check Gmail for the forwarded copies that did arrive but were filed as
   spam.** Forwarded mail fails SPF, so Gmail is entitled to junk it. Search:
   ```
   in:anywhere -in:inbox newer_than:14d
   ```
   and
   ```
   in:spam newer_than:30d
   ```
3. **Check Gmail's own filters** for a rule that skips the inbox:
   Settings → Filters and Blocked Addresses.
4. **Confirm the iCloud forward still exists** in iCloud Settings → Mail →
   Forwarding. If it was ever switched off, that is when Gmail went quiet.

Do this first. It tells you how much mail is stranded on iCloud and needs
migrating in step 6.

---

## 3. Pre-flight — do not skip, or mail will bounce

Repointing MX to Google when Google is not ready to accept the address means
hard bounces (`550 5.1.1 user unknown`) and **senders get a permanent failure**.
In Google Admin (`admin.google.com`), confirm all four:

- [ ] **`mrecai.com` is listed** under Account → Domains → Manage domains, and
      shows **Verified**.
- [ ] Note whether it is the **primary domain**, a **secondary domain**, or a
      **domain alias**. A domain alias routes `matthew@mrecai.com` to the
      matching primary-domain user automatically. A secondary domain does not —
      it needs its own user account.
- [ ] **`matthew@mrecai.com` exists as a real user** (Directory → Users) with an
      assigned licence, not just as a "send mail as" alias in Gmail settings.
- [ ] **Send a test to it now, while MX still points at iCloud**, from the
      Google Admin "Email Log Search" perspective it will show nothing — instead
      confirm the user by having Matthew sign in to `mail.google.com` as
      `matthew@mrecai.com` and send himself a message from a personal account
      *using Workspace's internal routing*. If the account cannot receive its
      own internal mail, MX will not save it.

If `mrecai.com` is **not** in Workspace, stop. Adding and verifying the domain
is a prerequisite, not part of this change.

---

## 4. Cutover

### Step 1 — lower the TTL (do this ~24h before the switch)

In IONOS DNS for `mrecai.com`, set the **MX record TTL to 300 seconds**. Wait
for the old 3600s TTL to expire before step 2, so the world picks up the change
in five minutes rather than an hour.

### Step 2 — replace the MX records

Delete both iCloud MX records. Add one record:

| Type | Host | Priority | Value | TTL |
|---|---|---|---|---|
| MX | `@` (or blank / `mrecai.com`) | `1` | `smtp.google.com` | 300 |

`smtp.google.com` at priority 1 is Google's current single-record MX. The older
five-record `aspmx.l.google.com` set still works — `atlase.ai` already uses it —
but there is no reason to add five records to a domain that has none.

**There must be exactly one MX destination.** Leaving an iCloud record in place
alongside Google means mail arrives at whichever host wins, at random.

### Step 3 — verify propagation before telling anyone

```bash
dig +short MX mrecai.com @8.8.8.8
# expect:  1 smtp.google.com.
dig +short MX mrecai.com @1.1.1.1
```

Then send a real message from an outside account and confirm it lands in the
Gmail inbox. In Gmail: **⋮ → Show original**, and check the header block reads
`SPF: PASS`, `DKIM: PASS`, `DMARC: PASS` (DMARC will say `PASS` only after
step 5).

### Step 4 — tidy SPF

Once you are certain nothing sends through iCloud any more — check Apple Mail
on the Mac, and any iPhone account configured to send as `matthew@mrecai.com` —
remove the iCloud include:

```
v=spf1 include:bolt.im include:_spf.google.com include:_spf-us.ionos.com ~all
```

That takes the record from **9 lookups to 4**, which is the difference between
"one more service breaks everything" and "room to grow". Do not do this on the
same day as the MX change; change one thing at a time so a failure is
attributable.

Leave `apple-domain=` and the other verification TXT records alone — they are
inert.

### Step 5 — publish DMARC

Missing DMARC is why `matthew@mrecai.com` is forgeable to the insurers and
mortgage servicer he is corresponding with. Start in **report-only** mode; it
changes nothing about delivery and cannot cause an outage:

| Type | Host | Value |
|---|---|---|
| TXT | `_dmarc` | `v=DMARC1; p=none; rua=mailto:dmarc@mrecai.com; fo=1; adkim=r; aspf=r` |

`dmarc@mrecai.com` must be a real, deliverable mailbox (a Workspace group is
ideal). Keep the `rua` address **on `mrecai.com`** — reporting to a different
domain requires an extra authorisation record at the receiving end and silently
produces no reports if you forget it.

Do the same for `mleca.com` and `atlase.ai`; both are equally exposed.

Read the aggregate reports for **at least two weeks** before considering
`p=quarantine`, and never move to `p=reject` while any legitimate sender is
still failing both SPF and DKIM. Moving too early is how a law practice stops
receiving its own newsletters and its e-signature notifications.

### Step 6 — migrate the mail already sitting in iCloud

Everything delivered before cutover is in iCloud and stays there. Options, in
order of preference:

1. **Google Workspace Migrate / Data Migration Service** (Admin → Data →
   Data import & export → Data migration), source type IMAP, server
   `imap.mail.me.com`, using an **app-specific password** generated at
   <https://account.apple.com>. iCloud requires this; the normal password fails.
2. **Apple Mail on the Mac** — add both accounts, drag the mailboxes across.
   Slow, but it is already configured and needs no admin action.

Only **after** the mail is migrated and verified, remove `mrecai.com` from
iCloud+ Custom Email Domain (iCloud Settings → Custom Email Domain). Removing it
first does not delete the mail, but it makes it considerably harder to reach.

### Step 7 — OpenClaw

**No code change is required.** `ops/poll_mail.py` uses a rolling window rather
than a stored cursor precisely so that a gap is repaired by widening the window.
After cutover, on the Mac:

```bash
cd ~/lms-repo/lms
source ops/lms.env
./ops/poll_mail.py matthew@mrecai.com --once --days 7 --dry-run
```

Read the list. When it looks right, drop `--dry-run`. Dedupe is the sha256 of
the rendered message, so anything already filed will not file twice.

Then confirm the scheduled job is healthy:

```bash
launchctl list | grep com.lms.mail
./ops/verify_setup.py
```

One note for the classifier: `core/pipeline/mail.py` treats **only a hard SPF
fail** as a signal, which was the right call while mail arrived forwarded. Once
MX points at Google, mail arrives direct and SPF becomes trustworthy — that
leniency can be tightened later, but leave it alone until the migration is done
and the aggregate DMARC reports confirm what is actually sending.

---

## 5. What this does not fix

- **Mail already bounced** during any window when neither iCloud nor Gmail was
  accepting. Nothing recovers that; senders were told it failed.
- **Outbound reputation.** Publishing DMARC at `p=none` improves nothing on its
  own — it only starts the measurement.
- **`bolt.im` in SPF.** Nobody has established what still sends through it. Find
  out before step 4; removing a live sender from SPF is its own outage.

---

## 6. Records reference — final intended state

```
mrecai.com.            MX   1 smtp.google.com.
mrecai.com.            TXT  "v=spf1 include:bolt.im include:_spf.google.com include:_spf-us.ionos.com ~all"
_dmarc.mrecai.com.     TXT  "v=DMARC1; p=none; rua=mailto:dmarc@mrecai.com; fo=1; adkim=r; aspf=r"
google._domainkey.     TXT  (unchanged — already correct)
s1._domainkey.         CNAME s1.domainkey.u57070934.wl185.sendgrid.net.   (unchanged)
s2._domainkey.         CNAME s2.domainkey.u57070934.wl185.sendgrid.net.   (unchanged)
```

---

## 7. Open decision for Matthew

This runbook assumes **Google Workspace becomes the mailbox of record for
`mrecai.com`**. That is the clean answer: mail is delivered straight to Gmail,
SPF stays intact because nothing is forwarded, and the LMS reads the real
mailbox rather than a copy of it.

The alternative — keep iCloud and repair the forward — is cheaper today and
worse permanently: forwarded mail fails SPF by construction, iCloud silently
declines to forward anything it junks, and the LMS is then reading a mailbox
that is *usually* complete. For a system filing tax and client documents,
"usually complete" is the failure mode you cannot detect.
