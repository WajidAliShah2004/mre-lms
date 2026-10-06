#!/usr/bin/env bash
#
# Phase 8 step 1: what is the network posture of this Mac right now?
# READ-ONLY. Changes nothing, needs no sudo.
#
#     ./ops/phase8_recon.sh 2>&1 | tee /tmp/phase8_recon.txt
#
# Phase 8 must not turn on default-deny egress until the replacement remote
# path is verified from an outside network (D-010). This collects the facts
# that plan depends on: is Tailscale up, what is listening and on which
# address, which firewall exists, and is RustDesk still the only way in.
# Secrets are never printed: openclaw redacts its own, and nothing here
# reads the Keychain.

set -u
hr() { printf '\n==== %s\n' "$1"; }

hr "machine"
sw_vers 2>/dev/null | tr '\n' ' '; echo
scutil --get ComputerName 2>/dev/null
scutil --get LocalHostName 2>/dev/null

hr "tailscale"
TS=""
for c in tailscale /Applications/Tailscale.app/Contents/MacOS/Tailscale /usr/local/bin/tailscale /opt/homebrew/bin/tailscale; do
  if command -v "$c" >/dev/null 2>&1 || [ -x "$c" ]; then TS="$c"; break; fi
done
if [ -z "$TS" ]; then
  echo "tailscale CLI: NOT FOUND"
  ls -d /Applications/Tailscale* 2>/dev/null || echo "no Tailscale app in /Applications"
else
  echo "cli: $TS"
  "$TS" version 2>&1 | head -3
  echo "-- status"
  "$TS" status 2>&1 | head -20
  echo "-- this node's tailnet IPv4"
  "$TS" ip -4 2>&1 | head -2
fi

hr "listening TCP sockets (address:port, process)"
# netstat needs no sudo; lsof without sudo only sees this user's processes.
netstat -anv -p tcp 2>/dev/null | awk 'NR<=2 || /LISTEN/' | awk '{print $4, $NF, $(NF-1)}' | sort -u | head -60
echo "-- this user's listeners with process names"
lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null | awk 'NR==1 || !seen[$1$9]++ {print $1, $2, $9}' | head -40

hr "remote access services"
for p in 22 5900 3283 21115 21116 21117 21118 21119; do
  if netstat -an -p tcp 2>/dev/null | grep LISTEN | grep -qE "[.:]$p[[:space:]]"; then
    echo "port $p LISTENING on: $(netstat -an -p tcp | grep LISTEN | grep -E "[.:]$p[[:space:]]" | awk '{print $4}' | tr '\n' ' ')"
  else
    echo "port $p not listening"
  fi
done
echo "(22 = Remote Login/SSH, 5900 = Screen Sharing/VNC, 3283 = Apple Remote Desktop, 2111x = RustDesk direct)"
echo "-- RustDesk"
ls -d /Applications/RustDesk* 2>/dev/null || echo "no RustDesk app in /Applications"
pgrep -fl -i rustdesk 2>/dev/null | head -5 || true

hr "macOS application firewall"
FW=/usr/libexec/ApplicationFirewall/socketfilterfw
"$FW" --getglobalstate 2>&1
"$FW" --getstealthmode 2>&1
"$FW" --getblockall 2>&1

hr "egress filters installed?"
ls -d /Applications/Little\ Snitch* /Applications/LuLu* 2>/dev/null || echo "no Little Snitch / LuLu in /Applications"
systemextensionsctl list 2>/dev/null | grep -i -E "snitch|lulu|network" | head -5 || true

hr "openclaw gateway exposure"
openclaw config get gateway.bind 2>&1 | tail -3
openclaw config get gateway.tailscale 2>&1 | tail -6
lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null | grep -i -E "node|openclaw" | awk '{print $1, $9}' | sort -u

hr "LM Studio exposure"
lsof -nP -iTCP:1234 -sTCP:LISTEN 2>/dev/null | awk 'NR>1{print $1, $9}' | sort -u || true

hr "launch jobs (this user)"
launchctl list | awk 'NR==1 || /com\.lms\.|ai\.openclaw\.|tailscale|rustdesk/'

hr "config/egress-allowlist.txt in repo"
ls -l "$(dirname "$0")/../config/egress-allowlist.txt" 2>/dev/null || echo "not present yet"

echo
echo "Done. Nothing was changed."
