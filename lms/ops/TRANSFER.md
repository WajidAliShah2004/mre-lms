# Getting this onto the Mac

Written from a Windows workstation; runs on the Mac Studio. This is the
hand-off procedure.

## Use git, not RustDesk file transfer

The client-owned private repo (C1) exists now. Push to it from here, pull on
the Mac. Dragging files over a RustDesk session works exactly once and leaves
no record of what version is on the machine — which is the problem the repo
was created to solve (§12.4).

**From the Windows workstation:**

```bash
cd "openclaw mathew"
git add lms/ DECISIONS.md PHASES.md .gitignore
git commit -m "Day 1-2: data layer, registry, D-007 filing, classifier skill"
git remote add origin <the client-owned private repo URL>   # first time only
git push -u origin main
```

Two things to check before that push:

1. **`git status` must not list the client PDF.** It is ignored now, but
   confirm — a password in git history is not removable in any way the client
   would consider removed.
2. **Signed commits** (§12.4, D-006). `git config commit.gpgsign true` with a
   key the client can verify, or agree in writing that signing is deferred.

**On the Mac:**

```bash
cd ~/                      # or wherever the checkout belongs
git clone <repo URL> lms-repo
cd lms-repo/lms
./ops/bringup_mac.sh /Volumes/MacStudioHD    # the encrypted 12 TB array
```

`bringup_mac.sh` is idempotent. It creates the storage trees, installs
`pyyaml` and `pytest`, writes `ops/lms.env` (paths only — no secrets), runs
the full test suite on the machine, and prints what is still blocked.

## Why run the tests again on the Mac

They pass on Windows. That is not evidence they pass on the target, and
three of the differences bite here specifically:

- **APFS is case-insensitive by default.** The taxonomy has `FINANCE` under
  both trees and subcategories in lowercase. If a volume was formatted
  case-sensitive, paths that collided silently on one machine stop colliding
  on the other — better to find that on day one.
- **Path length and normalisation.** macOS stores filenames NFD-normalised.
  The 200-char cap and the slug allowlist are both exercised by the suite.
- **`/Volumes` mount state.** The RAID has to be mounted *and unlocked*
  before anything writes to it. The script checks and refuses to guess.

## What the script deliberately does not do

- Touch any credential, mailbox, or network setting
- Connect to LM Studio or OpenClaw
- Encrypt a volume — it checks and warns. In practice this never fires:
  `MacStudioHD` was already FileVault-encrypted when we found it, so C5 and
  D-011 closed by observation (D-019). The check stays for the case where
  someone points the script at a different volume.

## Order of operations on the Mac

1. `bringup_mac.sh` — this document
2. **Rotate the exposed credentials (D-017)** before anything reaches a
   mailbox — seven web logins, changed in each provider's UI. The macOS
   account that carried a trivial password no longer exists; it was deleted
   on Sept 8 (D-018)
3. Phase 6 — OpenClaw config and tool policy. **Applied Sept 8 (D-021)**
4. Phase 7 — Telegram bot, `/halt`, and capture the numeric user id from his
   first message (this closes C2)
5. Phase 8 — Tailscale verified from an outside network **before** default-deny
   egress goes on. Verified, not configured. Under D-009 this is the
   containment boundary, not a hardening nicety
6. Day 3 — email, once C11 lands

## The RustDesk ordering hazard, restated

RustDesk is the build channel right now. Do not enable default-deny egress
until Tailscale + Screen Sharing has been confirmed working from an outside
network — phone on cellular, not the office wifi. If that ordering is
reversed, nobody reaches the machine until someone walks over to it.
