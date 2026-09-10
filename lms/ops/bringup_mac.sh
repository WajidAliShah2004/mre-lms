#!/usr/bin/env bash
#
# Day 1-2 bring-up on the Mac Studio.
#
# Run this ON THE MAC, from the repo checkout, as Matthew's user (D-009).
# It is idempotent: safe to re-run after a failure or a reboot.
#
# It does NOT touch credentials, mailboxes, or the network. It creates the
# storage trees, installs the two Python dependencies, and runs the test
# suite so you know the code works on the machine it will actually live on
# rather than on the workstation it was written on.
#
# Usage:
#   ./ops/bringup_mac.sh /Volumes/MacStudioHD
#
# The argument is the RAID volume that will hold the filed trees (C5).
# Pick an ENCRYPTED volume. If you are unsure which are encrypted, run:
#   diskutil apfs list | grep -A2 -i "FileVault"

set -euo pipefail

RAID_ROOT="${1:-}"
if [[ -z "$RAID_ROOT" ]]; then
  echo "usage: $0 /Volumes/<volume>   # the RAID volume for LMS data (C5)" >&2
  exit 2
fi
if [[ ! -d "$RAID_ROOT" ]]; then
  echo "error: $RAID_ROOT is not mounted" >&2
  exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

echo "==> repo:  $REPO_DIR"
echo "==> RAID:  $RAID_ROOT"

# ---------------------------------------------------------------------------
# 1. Encryption check. Warn loudly, do not silently proceed.
# ---------------------------------------------------------------------------
echo
echo "==> Checking encryption on $RAID_ROOT"
if diskutil info "$RAID_ROOT" 2>/dev/null | grep -qi "FileVault:.*Yes"; then
  echo "    encrypted: yes"
else
  echo "    *** WARNING: $RAID_ROOT does not report FileVault encryption. ***"
  echo "    C5/D-011 requires every volume holding LMS documents to be encrypted."
  echo "    Encrypt in place with:  diskutil apfs encryptVolume $RAID_ROOT -user disk"
  echo "    Save the passphrase to the SYSTEM keychain so it mounts after a"
  echo "    FileVault unlock without a second prompt."
  read -r -p "    Continue anyway? [y/N] " reply
  [[ "$reply" == "y" || "$reply" == "Y" ]] || exit 1
fi

# ---------------------------------------------------------------------------
# 2. Storage roots (BUILD_PLAYBOOK Day 1 step 6)
# ---------------------------------------------------------------------------
echo
echo "==> Creating storage roots"
ARCHIVE="$RAID_ROOT/LMS/archive"
ORIGINALS="$RAID_ROOT/LMS/_originals"
QUARANTINE="$HOME/LMS/quarantine"
INBOX="$HOME/LMS/inbox"

# Tree names must match entities.yaml `tree:` values exactly.
for tree in PERSONAL/Matthew PERSONAL/Jesse PERSONAL/Mother PERSONAL/Father \
            PERSONAL/Household MRECAI CHSHINK ATLASE MLECA _UNASSIGNED _JUNK; do
  mkdir -p "$ARCHIVE/$tree"
done
mkdir -p "$ORIGINALS" "$QUARANTINE" "$INBOX"

# The originals store is the only copy that is never rewritten. Lock it down.
chmod 700 "$ORIGINALS"

echo "    archive:    $ARCHIVE"
echo "    originals:  $ORIGINALS"
echo "    quarantine: $QUARANTINE"

# ---------------------------------------------------------------------------
# 3. Python dependencies
# ---------------------------------------------------------------------------
echo
echo "==> Python"
# Pin to Homebrew 3.12 explicitly. Bare `python3` on macOS resolves to Apple's
# 3.9 in /usr/bin, and the launchd jobs added on Day 6 will not inherit this
# shell's PATH — so a script that works when typed can fail silently at 06:30
# against a different interpreter. Name the interpreter, do not inherit it.
BOOTSTRAP_PY="/opt/homebrew/bin/python3.12"
if [[ ! -x "$BOOTSTRAP_PY" ]]; then
  echo "    $BOOTSTRAP_PY not found; install with: brew install python@3.12" >&2
  exit 1
fi
echo "    bootstrap: $BOOTSTRAP_PY ($("$BOOTSTRAP_PY" --version))"

# Homebrew's Python is externally managed (PEP 668) and refuses system-wide
# installs. The documented escape hatch is --break-system-packages; we do not
# use it. A venv is correct here for reasons that outlast the error message:
#
#   * `brew upgrade python@3.12` will not move dependencies under a running
#     service. A --user install shares its fate with Homebrew's.
#   * The launchd jobs can name .venv/bin/python absolutely, so the scheduled
#     run and the interactive run are provably the same interpreter.
#   * Wiping and rebuilding is `rm -rf .venv` and re-running this script.
VENV="$REPO_DIR/.venv"
if [[ ! -x "$VENV/bin/python" ]]; then
  echo "    creating venv at $VENV"
  "$BOOTSTRAP_PY" -m venv "$VENV"
fi
PY="$VENV/bin/python"
echo "    interpreter: $PY"
"$PY" -m pip install --quiet --upgrade pip
"$PY" -m pip install --quiet pyyaml pytest

# Apple's on-device readers, reached through PyObjC.
#
#   Vision  — OCR for photographed mail and scanned PDF pages
#   Quartz  — PDFKit for a PDF's text layer, and page rasterisation
#
# Both run entirely on this machine (D-004). Without them Vision is
# unavailable, PDFKit is unavailable, and EVERY PDF quarantines regardless of
# what it contains — which is how this was found: PDF reading was built and
# could not run here, because bringup installed pyyaml and pytest and nothing
# else. The pipeline degrades honestly without them, but it degrades.
#
# Not fatal if the install fails: a machine with no PyObjC still files text and
# still reaches the model for images. verify_setup.py names what is missing.
if ! "$PY" -m pip install --quiet pyobjc-framework-Vision pyobjc-framework-Quartz; then
  echo "    WARNING: PyObjC did not install — OCR and PDF reading will be" >&2
  echo "             unavailable. Run ops/verify_setup.py for the detail." >&2
fi

echo "    deps: $("$PY" -m pip list 2>/dev/null | grep -Ei 'pyyaml|pytest|pyobjc-framework-(vision|quartz)' | tr '\n' ' ')"

# restic — the backup engine (spec §Day-6.4). Encrypts client-side, so the
# repository is ciphertext at rest and an off-machine copy never hands a
# provider readable client data.
#
# Not fatal if it is missing: everything else works, and ops/backup.py says
# exactly what to install. But a machine with no backup is the only remaining
# failure in this build whose downside is unrecoverable, so it is loud.
if command -v restic >/dev/null 2>&1; then
  echo "    backup: restic $(restic version 2>/dev/null | awk '{print $2}')"
else
  echo "    backup: restic NOT INSTALLED — there is no backup on this machine." >&2
  echo "            brew install restic" >&2
fi

# ---------------------------------------------------------------------------
# 4. Environment. Written to a file the LaunchAgent sources.
#    NOT a .env with secrets in it — these are paths only. Credentials live
#    in the Keychain (D-016) and never in a file.
# ---------------------------------------------------------------------------
echo
echo "==> Writing ops/lms.env (paths only, no secrets)"
cat > "$REPO_DIR/ops/lms.env" <<EOF
# Generated by bringup_mac.sh. Paths only — never put a credential here.
export LMS_ARCHIVE_ROOT="$ARCHIVE"
export LMS_ORIGINALS_ROOT="$ORIGINALS"
export LMS_QUARANTINE_ROOT="$QUARANTINE"
export LMS_INBOX="$INBOX"
# Written from $RAID_ROOT/LMS, not as "$ARCHIVE/../". Both resolve to the same
# directory, but a path containing ".." cannot be eyeballed against another
# path, and telling two repository locations apart by eye is exactly what was
# needed to notice they had diverged.
export LMS_DB="$RAID_ROOT/LMS/lms.db"
# The restic repository. Declared HERE and nowhere else — the plists used to
# carry their own copy, this variable was missing from this file entirely, and
# the interactive runs and the nightly job ended up writing to two different
# repositories while both reported success (D-035).
#
# ON THE INTERNAL DISK, NOT THE ARRAY. `diskutil list` on Sept 10 showed the
# 12 TB array is four 4 TB disks presented as ONE device (disk10) carrying ONE
# volume (MacStudioHD). There is no second partition and no room to make a
# meaningful one: anything carved out of that container shares the physical
# store, so it dies with the enclosure, the controller, or the filesystem.
# Single-parity RAID survives one disk failing; it is not a backup.
#
# The internal SSD is different hardware, so this genuinely survives the array
# failing. It does NOT survive the machine — theft, fire, or the Mac dying take
# both copies. That is D-012's job and it is still open.
export LMS_BACKUP_REPO="$HOME/LMS/backup"
export TZ="America/New_York"
# The interpreter every launchd job must name explicitly. Never "python3".
export LMS_PYTHON="$PY"
EOF
chmod 600 "$REPO_DIR/ops/lms.env"

# ---------------------------------------------------------------------------
# 5. Verify on the real machine
# ---------------------------------------------------------------------------
echo
echo "==> Running the test suite on this Mac"
# shellcheck disable=SC1090
source "$REPO_DIR/ops/lms.env"
"$PY" -m pytest || { echo "TESTS FAILED — stop here, do not proceed to Day 3." >&2; exit 1; }

# ---------------------------------------------------------------------------
# 6. Report what is still blocked
# ---------------------------------------------------------------------------
echo
echo "==> Outstanding client items"
"$PY" - <<'PY'
import sys
sys.path.insert(0, ".")
from core.pipeline.registry import load_registry
r = load_registry()
missing = r.placeholders()
if missing:
    print(f"    C16 UNANSWERED for: {', '.join(missing)}")
    print("    Documents for these entities will file, but with placeholder")
    print("    legal names. Fill config/entities.yaml before real mail flows.")
else:
    print("    C16 complete — no placeholders remain.")
PY

cat <<'EOF'

==> Done. Day 1-2 is live on this machine.

Still blocked, in priority order:
  C16   legal names, EINs, name variants       -> the classifier is built from
                                                  this file; Day 2 accuracy
                                                  depends on it entirely
  D-017 rotate the seven exposed credentials   -> before ANY mailbox connects
  C11   Workspace admin + 4 Shared Drives      -> Day 3
  C2    Telegram numeric user id + 2FA         -> /halt has no authorised
                                                  sender until this lands
  C7    FileVault key custody                  -> Phase 1
  C8    who unlocks after a power cut          -> Phase 1 / RUNBOOK

Closed since the Sept 8 recon:
  C5    RAID encryption   -> MacStudioHD already encrypted (D-019)
  C21   dead accounts     -> OpenClaw/rescueadmin/lms removed (D-018)
  C1    git remote        -> INTERIM: contractor-owned, transfer before
                             handover (D-020)

Next: Phase 6 (OpenClaw config) and Phase 7 (Telegram), then Day 3 email.
EOF
