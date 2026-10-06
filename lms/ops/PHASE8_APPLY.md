# Phase 8 — Network and security hardening

Decided Oct 6 (D-063): **RustDesk stays the remote path** (D-010 option (a));
Tailscale stays installed but is not used for access. So nothing here may break
RustDesk, and the outbound allowlist, if it is ever turned on, must allow it.

Recon Oct 6 (`ops/phase8_recon.sh`): Tailscale up (100.69.58.49); gateway and
LM Studio loopback-only; **SSH (Remote Login) listening on all interfaces**;
RustDesk listening on all interfaces (21118); **macOS firewall off**; no egress
filter; ports 38365/51322 are Tailscale's own peer service. Ollama runs on
loopback (not part of the plan; harmless).

Work from `~/lms-repo/lms`. Type commands one line at a time; turn RustDesk
clipboard sharing off while doing so.

---

## Step 2 — Turn SSH (Remote Login) off

Nothing uses it, and it accepts password logins from the whole LAN.

**System Settings → General → Sharing → Remote Login → off.**

Check:

```bash
netstat -an -p tcp | grep LISTEN | grep '\.22 '
```

No output = off. RustDesk is unaffected.

## Step 3 — macOS firewall on (inbound only)

The application firewall filters **incoming** connections only; RustDesk's
connection to its relay is outgoing, so the session survives. Still, a safety
net first: if the session drops and does not come back within 5 minutes, the
firewall turns itself off again.

```bash
sudo nohup sh -c 'sleep 300; /usr/libexec/ApplicationFirewall/socketfilterfw --setglobalstate off' >/dev/null 2>&1 &
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --add /Applications/RustDesk.app
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --unblockapp /Applications/RustDesk.app
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --setglobalstate on
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --setstealthmode on
```

Disconnect RustDesk and reconnect. If it reconnects, cancel the safety net and
confirm the state:

```bash
sudo pkill -f 'sleep 300'
/usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate
/usr/libexec/ApplicationFirewall/socketfilterfw --getstealthmode
```

`Firewall is enabled` and `stealth mode is on`. If RustDesk did *not*
reconnect, wait 5 minutes: the firewall switches off and you are back in.

## Step 4 — Weekly audit

```bash
git pull
cp ops/com.lms.audit.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.lms.audit.plist
launchctl kickstart -p gui/$(id -u)/com.lms.audit
sleep 60; tail -3 ~/LMS/logs/audit.log
```

Expect `critical=0 warn=2 info=2; verify_setup PASS; report=…`.

## Step 5 — Off-site backup (Backblaze B2) — needs inputs first

`ops/backup.py` writes one local restic repository. Off-site needs:

1. In Backblaze (Matthew's account, C6): a **private** bucket, e.g.
   `mre-lms-backup`, and an **application key restricted to that bucket**
   (never the account password, which was exposed, D-017).
2. The key stored in the Keychain on the Mac, at the prompt.
3. Code: a second restic repository on B2 and a nightly `restic copy` from
   the local one (same snapshots, one encryption), plus its own restore test.

Built when the bucket and key exist.

## Step 6 — Outbound allowlist (optional; last)

Not started. Default-deny outbound needs a filter (LuLu free, Little Snitch
paid, or `pf`) and an allowlist that includes **RustDesk's relay servers**,
Telegram, Google (Gmail), Backblaze, Homebrew/npm during updates. A wrong list
locks the remote session out. Recommended: defer to handover, when RustDesk is
removed and the list gets simpler.

## Rollback

- SSH: Sharing → Remote Login on.
- Firewall: `sudo /usr/libexec/ApplicationFirewall/socketfilterfw --setglobalstate off`
- Audit: `launchctl bootout gui/$(id -u)/com.lms.audit`
