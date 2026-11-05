#!/bin/bash
# Task 1, second pass, of docs/superpowers/plans/2026-11-05-chat-control.md (D-080).
#
# ops/chat_control_facts.sh settled the WhatsApp keys, the injected workspace
# files and the login command. This settles the rest: what a plugin command
# handler is given and returns, whether /lms and /halt are free names, how a
# bundled plugin is laid out, the current plugins/commands/bindings config, and
# the WhatsApp number format for bindings.
#
# Read-only. Changes nothing; also saves the output to
# ~/LMS/logs/plugin_api_facts.txt.
#
#     ./ops/plugin_api_facts.sh

cd "$(dirname "$0")/.." || exit 1
OUT="$HOME/LMS/logs/plugin_api_facts.txt"
mkdir -p "$(dirname "$OUT")"
PKG="$(npm root -g 2>/dev/null)/openclaw"
DOCS="$PKG/docs"

{
echo "== A command types (from the shipped .d.ts files)"
grep -rlE "OpenClawPluginCommandDefinition" "$PKG/dist" --include='*.d.ts' 2>/dev/null | head -3 | while read -r f; do
  echo "-- $f"
  awk '/(type|interface) [A-Za-z]*(PluginCommand|CommandDefinition|CommandContext|CommandResult|CommandHandler)[A-Za-z]* *[={<]/{p=1;n=0}
       p{print; n++; if (n>40 || /^}/) {p=0; print "   ..."}}' "$f"
done | head -160

echo; echo "== B a bundled plugin that registers a command"
F=$(grep -rlE "registerCommand\(" "$PKG/extensions" --include='*.js' --include='*.ts' 2>/dev/null | grep -v node_modules | head -1)
if [ -n "$F" ]; then
  D=$(dirname "$F")
  while [ "$D" != "$PKG/extensions" ] && [ ! -f "$D/openclaw.plugin.json" ]; do D=$(dirname "$D"); done
  echo "-- plugin folder: $D"
  ls "$D"
  echo "-- openclaw.plugin.json"
  head -30 "$D/openclaw.plugin.json" 2>/dev/null
  echo "-- package.json (main / type / openclaw fields)"
  grep -E '"(main|type|module|exports|openclaw|extensions)"' "$D/package.json" 2>/dev/null | head -8
  echo "-- $F"
  grep -nE -B3 -A30 "registerCommand\(" "$F" | head -60
else
  echo "(no bundled plugin calls registerCommand)"
fi

echo; echo "== C plugin-command docs"
sed -n '150,185p' "$DOCS/tools/slash-commands.md" 2>/dev/null
grep -rlE "registerCommand" "$DOCS" 2>/dev/null | grep -v slash-commands | head -3 | while read -r f; do
  echo "-- $f"
  grep -nE -B2 -A30 "registerCommand" "$f" | head -50
done

echo; echo "== D built-in command names, and are lms / halt taken?"
grep -ohE '`/[a-z][a-z_-]*' "$DOCS/tools/slash-commands.md" 2>/dev/null | tr -d '`' | sort -u | tr '\n' ' '; echo
for n in lms halt; do
  if grep -qE "\`/$n([^a-z_-]|\$)" "$DOCS/tools/slash-commands.md" 2>/dev/null; then echo "/$n: listed as built-in"; else echo "/$n: not a documented built-in"; fi
done
grep -rhoiE ".{0,160}(reserved|built-?in) (command|name).{0,160}" "$PKG/dist" 2>/dev/null | sort -u | head -5
grep -rhoiE ".{0,120}command.{0,40}(already registered|conflicts? with|is reserved).{0,120}" "$PKG/dist" 2>/dev/null | sort -u | head -5

echo; echo "== E current config (plugins.allow, plugins.load, enabled plugins, commands, bindings)"
echo "-- plugins.allow"; openclaw config get plugins.allow --json 2>&1 | head -10
echo "-- plugins.load";  openclaw config get plugins.load --json 2>&1 | head -10
echo "-- plugins.entries that are enabled"
openclaw config get plugins.entries --json 2>/dev/null | python3 -c '
import json, sys
try:
    e = json.load(sys.stdin)
except Exception as x:
    sys.exit(f"(could not read: {x})")
print(" ".join(k for k, v in e.items() if isinstance(v, dict) and v.get("enabled")) or "(none)")'
echo "-- commands"; openclaw config get commands --json 2>&1 | head -30
echo "-- bindings"; openclaw config get bindings --json 2>&1 | head -30

echo; echo "== F schema: plugins.load / entries / slots"
S=$(mktemp /tmp/oc-schema.XXXXXX)
openclaw config schema > "$S" 2>/dev/null
.venv/bin/python ops/schema_dump.py "$S" plugins 2>&1 \
  | grep -E '^plugins\.(load|entries|allow|deny|slots)' | grep -vE '^plugins\.entries\.\*\.config\.' | head -30
rm -f "$S"

echo; echo "== G WhatsApp: number format, self-chat, bindings"
W=$(ls "$DOCS"/channels/whatsapp*.md 2>/dev/null | head -1)
echo "-- ${W:-(no whatsapp doc found)}"
[ -n "$W" ] && grep -nE -A3 "selfChatMode|E\.164|\+1555|allowFrom|peer|own number|linked number" "$W" | head -70
grep -rnE -A6 'channel: "whatsapp"' "$DOCS" 2>/dev/null | grep -E "peer|id:" | head -8
} 2>&1 | tee "$OUT"

echo
echo "Saved to $OUT"
