#!/bin/bash
# LMS Mac recon - READ ONLY, no sudo, changes nothing.
# Answers by inspection: C5/D-011 (volumes + encryption), C12/C13 (iCloud,
# Messages, Mail sign-in), and current Phase 1-8 baseline state.
# Prints the iCloud account id and mail account names. Nothing else personal.
S(){ echo; echo "----- $1 -----"; }
echo "== LMS recon $(date '+%F %T %Z') =="

S "OS & hardware"
sw_vers
echo "Model: $(sysctl -n hw.model)  Cores: $(sysctl -n hw.ncpu)  RAM: $(( $(sysctl -n hw.memsize)/1073741824 ))GB"
echo "Chip: $(sysctl -n machdep.cpu.brand_string 2>/dev/null)"
echo "User: $(whoami)  Host: $(hostname)"
echo "TZ: $(readlink /etc/localtime | sed 's|.*zoneinfo/||')"

S "FileVault (want: On)"
fdesetup status

S "Power (want sleep 0, autorestart 1, powernap 0)"
pmset -g | grep -Ei 'sleep|autorestart|powernap|hibernatemode'

S "User accounts + admins"
dscl . -list /Users | grep -v '^_' | grep -vE '^(daemon|nobody|root)$' | tr '\n' ' '; echo
dscl . -read /Groups/admin GroupMembership 2>/dev/null

S "VOLUMES + ENCRYPTION (C5/D-011) - key section"
df -h | grep -E '^/dev|^Filesystem'
echo "--- APFS ---"
diskutil apfs list 2>/dev/null | grep -E 'Container |Volume |Name:|FileVault|Encrypted|Capacity|Mount Point'
echo "--- non-APFS (cannot use APFS encryption) ---"
diskutil list | grep -E 'Apple_HFS|Microsoft Basic Data|ExFAT|Windows_NTFS' || echo none
echo "--- external disks ---"
diskutil list external 2>/dev/null || echo none

S "Toolchain"
for t in brew node npm python3 git rclone restic ffmpeg sqlite3 tailscale openclaw; do
  if command -v $t >/dev/null 2>&1; then printf '%-9s %s\n' "$t" "$($t --version 2>&1|head -1)"; else printf '%-9s MISSING\n' "$t"; fi
done
for a in "LM Studio" Tailscale RustDesk "Little Snitch"; do
  [ -d "/Applications/$a.app" ] && echo "app: $a present" || echo "app: $a absent"
done

S "GPU wired limit (want 245760)"
sysctl iogpu.wired_limit_mb 2>/dev/null || echo "sysctl key absent"
ls -l /Library/LaunchDaemons/com.lms.wiredlimit.plist 2>/dev/null || echo "wiredlimit daemon not installed"

S "LM Studio :1234 (must bind 127.0.0.1, not *)"
lsof -nP -iTCP:1234 -sTCP:LISTEN 2>/dev/null || echo "nothing listening on 1234"
curl -s --max-time 4 http://localhost:1234/v1/models | head -c 800; echo
du -sh ~/.lmstudio/models/* 2>/dev/null | head -20 || echo "no ~/.lmstudio/models"

S "OpenClaw state"
[ -d ~/.openclaw ] && { echo "perms: $(stat -f '%Sp %Su' ~/.openclaw)"; ls -la ~/.openclaw | head -15; } || echo "~/.openclaw absent"

S "Network exposure"
launchctl list 2>/dev/null | grep -Ei 'screensharing|RemoteDesktop' || echo "screen sharing not running"
(/Applications/Tailscale.app/Contents/MacOS/Tailscale status 2>/dev/null || tailscale status 2>/dev/null) | head -10 || echo "tailscale not up"
echo "--- listening on all interfaces ---"
lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null | grep -E '\*:|0\.0\.0\.0:' | head -15 || echo none
/usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate 2>/dev/null

S "iCloud / Messages / Mail (C12, C13 - Day 4 depends on these)"
defaults read MobileMeAccounts AccountID 2>/dev/null || echo "iCloud: not signed in for $(whoami)"
[ -d ~/Library/Mobile\ Documents/com~apple~CloudDocs ] && echo "iCloud Drive folder: present" || echo "iCloud Drive folder: absent"
if [ -f ~/Library/Messages/chat.db ]; then
  echo "chat.db: $(du -h ~/Library/Messages/chat.db|cut -f1)"
  (head -c 16 ~/Library/Messages/chat.db >/dev/null 2>&1) && echo "chat.db readable (Full Disk Access OK)" || echo "chat.db NOT readable (no Full Disk Access)"
else echo "chat.db absent - Messages not signed in for this user"; fi
defaults read com.apple.mail MailAccounts 2>/dev/null | grep -E 'AccountName|EmailAddresses|Hostname|AccountType' | head -30 || echo "Mail.app not configured"

S "Existing LMS artifacts (expect none)"
ls -la ~/LMS 2>/dev/null || echo "~/LMS absent"
ls ~/Library/LaunchAgents/ 2>/dev/null | grep -iE 'lms|openclaw' || echo "no LMS LaunchAgents"

echo; echo "== RECON COMPLETE =="
