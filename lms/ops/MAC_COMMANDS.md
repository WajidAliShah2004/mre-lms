# Mac commands — what is still outstanding

**Superseded in part.** The bring-up this file used to describe is now
`ops/bringup_mac.sh`, and the phase procedures are `ops/PHASE6_APPLY.md` and
`ops/PHASE7_APPLY.md`. Day-to-day operation is `RUNBOOK.md`.

What remains here is the work that is genuinely still open: the **D-017
credential rotation**, and the verification commands that prove the machine is
in the state the decision log claims.

**Before you start:**

```bash
unset HISTFILE
```

Credentials have leaked twice on this engagement — the gateway token on Aug 7
(D-016) and seven client passwords on Sept 7 (D-017). Both times the mechanism
was a value persisting somewhere it was not meant to.

---

## 1. Rotate the exposed credentials — D-017, still open

Seven passwords arrived in a PDF: GitHub, five mailboxes, Backblaze.

**None of this can be done from a shell.** They are web logins, changed in each
provider's own UI. What the shell does is store what the *system* needs, in the
Keychain, where nothing else can read it.

```bash
# -w with NO value prompts. The secret never reaches the command line,
# the process table, or the history file. This is D-016 applied.
security add-generic-password -a lms -s lms/backblaze-key-id -w
security add-generic-password -a lms -s lms/backblaze-app-key -w
```

Verify without printing anything:

```bash
security find-generic-password -a lms -s lms/backblaze-key-id -w >/dev/null && echo OK
security dump-keychain 2>/dev/null | grep -o 'lms/[a-z-]*' | sort -u
```

Then remove the source document:

```bash
rm -P ~/Downloads/"Completed System Setup and Access Requirements copy.pdf" 2>/dev/null || true
```

And confirm it never reached git, on the workstation:

```bash
git log --all --oneline --name-only | grep -i "System Setup and Access" || echo "clean"
```

**No longer applicable:** the macOS account rotation this file used to describe.
That account (`OpenClaw`, uid 502) was deleted on Sept 8 — see D-018. There is
one human account on the machine now.

---

## 2. Verify the machine matches the decision log

Everything below is a read. Run it after any change, and after every reboot.

```bash
cd ~/lms-repo/lms && ./ops/verify_setup.py
```

Six checks: agents have no tools, SKILL.md front matter agrees, no cloud
provider, scheduling survives the November DST change, the D-015 plugin/skill
baseline has not drifted, LM Studio is loopback-only.

```bash
python3 -m pytest
```

Expected: **105 passed, 1 xfailed**. The xfail is the C16 gate — legal names
and EINs — and is correct until Matthew supplies them. Anything else red means
stop.

```bash
openclaw doctor --allow-exec
openclaw security audit --deep
```

`--allow-exec` matters. Without it, doctor skips the gateway probe entirely
because the token is an exec SecretRef, and reports nothing wrong regardless.

Expected audit: **0 critical, 2 warn**. Both warnings are known and justified —
`gateway.trusted_proxies_missing` (loopback only, no reverse proxy) and
`gateway.probe_failed / missing scope: operator.read`, which clears when C2
lands in Phase 7.

---

## 3. After a reboot

```bash
launchctl list | grep -i openclaw           # gateway back?
lsof -nP -iTCP:1234 -sTCP:LISTEN             # must be 127.0.0.1, not *
sysctl iogpu.wired_limit_mb                  # must still print 245760
curl -s http://localhost:1234/v1/models | python3 -m json.tool | head
cd ~/lms-repo/lms && ./ops/verify_setup.py
```

If `iogpu.wired_limit_mb` is not 245760 the `com.lms.wiredlimit` LaunchDaemon
did not load. Inference still works and silently degrades — no error, just
worse (Rec 21).

**Still never verified: the cold boot.** D-016 asks whether the Keychain exec
SecretRef resolves at startup, before anyone has logged in. A warm restart with
the login Keychain already unlocked does not answer that, and it is the exact
scenario an overnight power cut produces. Phase 9.

---

## 4. State as of Sept 8

| | |
|---|---|
| Accounts | One: `mleca`. Three abandoned ones removed (D-018) |
| Storage | `/Volumes/MacStudioHD` — 12 TB, already FileVault-encrypted (D-019) |
| Boot volume | 231 GB free — put model weights on the array, not here |
| Python | `~/lms-repo/lms/.venv/bin/python` (3.12.13). Never bare `python3` |
| OpenClaw | 2026.7.1-2, above the CVE floor. 3 plugins, 0 skills |
| Tools | `profile: minimal`, `allow: []`, deny list (D-021) |
| Models | All five tiers loaded and confirmed |

**Volume names in older documents are wrong.** `Bulk`, `MacStudioHome` and
`Vault` do not exist and never did on this machine — the client was describing
an older layout. There is one volume, `MacStudioHD`.
