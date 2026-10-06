#!/usr/bin/env bash
#
# Measure the kill switch, the real one: every LMS and OpenClaw job stopped,
# disabled so it survives a restart, inside 10 seconds (spec §8.9, A16).
#
# The first version of this timed `openclaw gateway stop`. That passes while
# mail, the watcher, the brief and the backup keep running, because none of
# them go through the gateway (D-056, D-059). This runs ./ops/halt.py, the
# same code /halt runs from the phone, then checks launchd's own view.
#
# What this does NOT test: the phone. /halt from Telegram adds Telegram's
# delivery time on top. That is measured at acceptance, with Matthew holding
# the phone, so he has done it once himself.
#
# Usage:  ./ops/verify_halt.sh
# Leaves the system RUNNING: it resumes at the end.

set -uo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
[ -x "$PY" ] || PY=python3
DOMAIN="gui/$(id -u)"
result=0

if [ -f "$HOME/LMS/HALTED.json" ]; then
  echo "Already halted ($HOME/LMS/HALTED.json). Resume first if you mean it:"
  echo "    ./ops/halt.py --resume"
  exit 1
fi

echo "==> Before"
"$PY" ops/halt.py --status

echo
echo "==> Halting (same code path as /halt)"
"$PY" ops/halt.py || result=1

echo
echo "==> launchd's view"
loaded=$(launchctl list | awk 'NR>1 && ($3 ~ /^com\.lms\./ || $3 ~ /^ai\.openclaw\./) && $3 != "com.lms.haltbot" {print $3}')
if [ -n "$loaded" ]; then
  echo "    FAIL: still loaded:"; echo "$loaded" | sed 's/^/      /'; result=1
else
  echo "    PASS: no LMS/OpenClaw job loaded (haltbot excepted)"
fi
not_disabled=0
for plist in "$HOME"/Library/LaunchAgents/com.lms.*.plist "$HOME"/Library/LaunchAgents/ai.openclaw.*.plist; do
  [ -e "$plist" ] || continue
  label=$(basename "$plist" .plist)
  [ "$label" = "com.lms.haltbot" ] && continue
  if ! launchctl print-disabled "$DOMAIN" | grep -Eq "\"$label\" => (true|disabled)"; then
    echo "    FAIL: $label is not disabled; it would start again at the next login"
    not_disabled=1; result=1
  fi
done
[ $not_disabled -eq 0 ] && echo "    PASS: every installed job is disabled (survives a restart)"
if launchctl list | grep -q com.lms.haltbot; then
  echo "    PASS: haltbot still running (it is never halted)"
else
  echo "    WARN: haltbot not running; /halt from the phone would not work"
fi

echo
echo "==> Resuming"
"$PY" ops/halt.py --resume || result=1
sleep 5
"$PY" ops/halt.py --status

echo
[ $result -eq 0 ] && echo "RESULT: PASS" || echo "RESULT: FAIL"
echo "Record the HALTED time above in DECISIONS.md (D-059)."
exit $result
