#!/usr/bin/env bash
#
# Measure how long the kill switch actually takes.
#
# Phase 7 requires /halt to stop the gateway in <= 10 seconds. This measures
# the LOCAL equivalent (`openclaw gateway stop`) rather than asserting the
# requirement is met, because the number is what matters and nobody has ever
# written it down.
#
# What this does NOT test: the Telegram path. /halt from the phone adds
# network latency, Telegram's own delivery, and the owner-allowlist check.
# That has to be measured with the phone in hand, at acceptance, with Matthew
# watching — which is the point of testing it in front of him.
#
# This measures the floor. If the local stop is already slow, the remote one
# cannot be fast.
#
# Usage:  ./ops/verify_halt.sh
# Leaves the gateway RUNNING when it finishes.

set -uo pipefail

LIMIT=10
LABEL="gui/$(id -u)/ai.openclaw.gateway"

running() {
  launchctl list 2>/dev/null | grep -q "ai.openclaw.gateway"
}

pid_of() {
  launchctl list 2>/dev/null | awk '/ai.openclaw.gateway/{print $1}'
}

echo "==> Pre-flight"
if ! running; then
  echo "    gateway is not running; starting it so there is something to stop"
  openclaw gateway restart >/dev/null 2>&1
  sleep 5
fi
before=$(pid_of)
echo "    gateway pid: ${before:-none}"

echo
echo "==> Stopping, and timing it"
start=$(python3 -c 'import time; print(time.time())')

openclaw gateway stop >/dev/null 2>&1

# Poll rather than trusting the command's own return. The question is when the
# process is actually gone, not when the CLI decided to return.
elapsed=0
for _ in $(seq 1 200); do
  if ! running; then break; fi
  sleep 0.1
done
end=$(python3 -c 'import time; print(time.time())')
elapsed=$(python3 -c "print(f'{$end - $start:.2f}')")

echo "    stopped in ${elapsed}s"

echo
if running; then
  echo "    RESULT: FAIL — still running after the poll window"
  echo "    Escalation path: launchctl bootout $LABEL"
  result=1
elif (( $(python3 -c "print(1 if $elapsed <= $LIMIT else 0)") )); then
  echo "    RESULT: PASS — ${elapsed}s, inside the ${LIMIT}s requirement"
  result=0
else
  echo "    RESULT: FAIL — ${elapsed}s exceeds the ${LIMIT}s requirement"
  result=1
fi

echo
echo "==> Restarting (this script leaves the system up)"
openclaw gateway restart >/dev/null 2>&1
sleep 5
if running; then
  echo "    gateway pid: $(pid_of)"
else
  echo "    *** GATEWAY DID NOT COME BACK ***"
  echo "    openclaw gateway restart"
  echo "    openclaw doctor --allow-exec"
  result=1
fi

echo
echo "Record the measured time in DECISIONS.md. A requirement with no measured"
echo "number attached is an aspiration."
exit $result
