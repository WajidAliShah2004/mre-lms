#!/bin/bash
# Task 1 of docs/superpowers/plans/2026-11-05-chat-control.md (D-080).
#
# Read-only. Prints what the installed OpenClaw actually supports, so the
# plugin and the config patch are written from the machine, not the docs
# (D-021, D-056). Changes nothing; also saves the output to
# ~/LMS/logs/chat_control_facts.txt.
#
#     ./ops/chat_control_facts.sh

cd "$(dirname "$0")/.." || exit 1
OUT="$HOME/LMS/logs/chat_control_facts.txt"
mkdir -p "$(dirname "$OUT")"

{
echo "== 1 version"
openclaw --version 2>&1 | tail -1

echo; echo "== 2 schema: channels.whatsapp, plugins, commands, agents.defaults (bootstrap)"
S=$(mktemp /tmp/oc-schema.XXXXXX)
openclaw config schema > "$S" 2>/dev/null
.venv/bin/python ops/schema_dump.py "$S" channels.whatsapp plugins commands agents.defaults 2>&1 \
  | grep -Ev '^channels\.whatsapp\.accounts\.' \
  | grep -E '^(channels\.whatsapp\.[A-Za-z]+(\.|  |$)|plugins|commands|agents\.defaults\.bootstrap)' \
  | head -120
rm -f "$S"

echo; echo "== 3 where OpenClaw is installed"
PKG="$(npm root -g 2>/dev/null)/openclaw"
if [ ! -d "$PKG" ]; then
  BIN=$(readlink -f "$(command -v openclaw)")
  PKG=$(cd "$(dirname "$BIN")/.." && pwd)
fi
echo "$PKG"
[ -d "$PKG/dist" ] || echo "(no dist folder under it)"

echo; echo "== 4 registerCommand: the options it takes"
grep -rhoE "registerCommand.{0,240}" "$PKG/dist" 2>/dev/null | sort -u | head -6

echo; echo "== 5 reserved command names"
grep -rhoE "[A-Za-z_]*(RESERVED|Reserved|reserved)[A-Za-z_]*[ =:(]+.{0,400}" "$PKG/dist" 2>/dev/null \
  | grep -iE "command" | sort -u | head -4

echo; echo "== 6 what a plugin command handler is given"
grep -rhoE ".{0,260}isAuthorizedSender.{0,260}" "$PKG/dist" 2>/dev/null | sort -u | head -3

echo; echo "== 7 workspace files injected into the prompt, and their limit"
grep -rhoE "\"(AGENTS|SOUL|TOOLS|IDENTITY|USER|HEARTBEAT|BOOTSTRAP|MEMORY)\.md\"" "$PKG/dist" 2>/dev/null \
  | sort | uniq -c
grep -rhoE "bootstrapMaxChars.{0,60}" "$PKG/dist" 2>/dev/null | sort -u | head -4

echo; echo "== 8 plugin docs shipped with this version"
for f in $(grep -rlE "registerCommand|openclaw\.plugin\.json" "$PKG/docs" 2>/dev/null | head -4); do
  echo "-- $f"
  grep -nE -A3 "registerCommand|openclaw\.plugin\.json|requireAuth|acceptsArgs|\"id\"|load\.paths|paths" "$f" | head -40
done

echo; echo "== 9 CLI help"
openclaw plugins --help 2>&1 | grep -vE '^\s*$' | head -25
openclaw channels --help 2>&1 | grep -vE '^\s*$' | head -25
openclaw channels login --help 2>&1 | grep -vE '^\s*$' | head -15

echo; echo "== 10 current plugins config"
openclaw config get plugins --json 2>&1 | head -30

echo; echo "== 11 lms-orchestrator workspace"
ls -la "$HOME/.openclaw/workspace-lms-orchestrator/" 2>&1
} 2>&1 | tee "$OUT"

echo
echo "Saved to $OUT"
