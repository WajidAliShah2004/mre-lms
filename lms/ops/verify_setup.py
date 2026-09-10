#!/usr/bin/env python3
"""Pre-flight assertions for the LMS. Run at gateway start and after every
OpenClaw update.

Exit code 0 means every check passed. Anything else means stop — do not
process mail until it is understood.

Why this exists as a script rather than a checklist
---------------------------------------------------
Three of these properties are the ones the whole security design rests on,
and all three can be silently undone by something nobody chose:

  * An OpenClaw update can ship new skills and plugins that default to
    available. D-015 established there is NO default-deny for either — control
    is per-name only — so the hardening is a denylist, and denylists rot.
  * A config edit can add a tool to an agent without anyone noticing, and a
    zero-tool agent with one tool is not a slightly-worse sandbox, it is not a
    sandbox.
  * A scheduler declared with a UTC offset instead of a named zone keeps
    working perfectly until the November DST change, then runs an hour off
    forever, and the symptom (a brief arriving at the wrong time) does not
    look like a config bug.

None of the three raises an error on its own. That is what makes them worth
asserting rather than remembering.

Usage:
    ./ops/verify_setup.py                  # all checks
    ./ops/verify_setup.py --skip-openclaw  # config-only, for CI
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Run under the interpreter the DAEMON uses, not whatever the shebang found.
#
# The shebang is `#!/usr/bin/env python3`, so `./ops/verify_setup.py` resolves
# to Homebrew's python3 — which has no PyObjC — while the LaunchAgent runs
# .venv/bin/python, which does. Check [7] therefore reported "PDFKit
# UNAVAILABLE — EVERY PDF will quarantine" at the same moment the watcher was
# filing PDFs perfectly well.
#
# A check that inspects a different environment than the thing it is checking
# is worse than no check. It was confidently wrong in the harmless direction
# this time; the same mismatch reversed — PyObjC present system-wide, absent
# from the venv — reports PASS while every PDF quarantines, and that is the
# direction that costs a day.
#
# Re-exec rather than warn: a warning about the interpreter is one more thing
# to read past.
# ---------------------------------------------------------------------------
# Compared by sys.prefix, NOT by the resolved binary path.
#
# The first version of this compared Path(sys.executable).resolve() against
# .venv/bin/python — and a venv's `python` is a symlink to the base
# interpreter, so EVERY venv built on the same Python resolves to the same
# file:
#
#     /tmp/va/bin/python -> /usr/bin/python3.10    sys.prefix = /tmp/va
#     /tmp/vb/bin/python -> /usr/bin/python3.10    sys.prefix = /tmp/vb
#     /usr/bin/python3   -> /usr/bin/python3.10    sys.prefix = /usr
#
# All three "match". The guard could not distinguish .venv from a different
# venv or from the bare Homebrew install, so it fired or didn't for reasons
# unconnected to which environment was active — and then printed the resolved
# Homebrew path while claiming it was "the one the daemon uses".
#
# sys.prefix IS the environment. It is the only one of the three that differs.
_VENV = Path(__file__).resolve().parents[1] / ".venv"
_VENV_PY = _VENV / "bin" / "python"
if (_VENV_PY.exists()
        and Path(sys.prefix).resolve() != _VENV.resolve()
        and not os.environ.get("LMS_VERIFY_REEXEC")):
    os.environ["LMS_VERIFY_REEXEC"] = "1"
    os.execv(str(_VENV_PY), [str(_VENV_PY), str(Path(__file__).resolve()), *sys.argv[1:]])

REPO = Path(__file__).resolve().parents[1]
FRAGMENT = REPO / "openclaw" / "agents" / "agents.fragment.json"
SKILLS_DIR = REPO / "openclaw" / "skills"

# The D-015 end state, measured on the Mac 2026-08-06 and re-verified
# 2026-09-08 after the 2026.6.34 -> 2026.7.1-2 upgrade.
D015_PLUGINS = 3
D015_SKILLS_READY = 0

TZ = ZoneInfo("America/New_York")

GREEN, RED, YELLOW, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[0m"

failures: list[str] = []
warnings: list[str] = []


def ok(msg: str) -> None:
    print(f"  {GREEN}PASS{RESET}  {msg}")


def fail(msg: str) -> None:
    print(f"  {RED}FAIL{RESET}  {msg}")
    failures.append(msg)


def warn(msg: str) -> None:
    print(f"  {YELLOW}WARN{RESET}  {msg}")
    warnings.append(msg)


def load_fragment() -> dict:
    return json.loads(FRAGMENT.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1. Every model-facing agent has zero tools
# ---------------------------------------------------------------------------

def check_agents_have_no_tools() -> None:
    print("\n[1] Agent tool lists — the first line of defence under D-009")
    frag = load_fragment()
    agents = {k: v for k, v in frag.get("agents", {}).items() if not k.startswith("_")}

    if not agents:
        fail("no agents defined in the fragment")
        return

    for name, body in agents.items():
        allow = (body.get("tools") or {}).get("allow", None)
        if allow is None:
            fail(f"{name}: no tools.allow key — absent is not the same as empty")
        elif allow != []:
            fail(f"{name}: tools.allow is {allow!r}, must be []")
        else:
            ok(f"{name}: zero tools")


def check_skills_declare_no_tools() -> None:
    print("\n[2] SKILL.md front matter agrees with the config")
    found = sorted(SKILLS_DIR.glob("*/SKILL.md"))
    if not found:
        fail(f"no SKILL.md files under {SKILLS_DIR}")
        return

    for path in found:
        text = path.read_text(encoding="utf-8")
        head = text.split("---")[1] if text.startswith("---") else ""
        if "tools: []" in head:
            ok(f"{path.parent.name}: tools: []")
        else:
            fail(f"{path.parent.name}: front matter does not declare 'tools: []'")


# ---------------------------------------------------------------------------
# 3. No cloud tier
# ---------------------------------------------------------------------------

def check_no_cloud_provider() -> None:
    print("\n[3] No cloud model tier (D-003)")
    frag = load_fragment()
    providers = frag.get("models", {}).get("providers", {})

    banned = {"anthropic", "openai", "google", "mistral", "cohere", "azure",
              "bedrock", "vertex", "openrouter", "together", "groq", "xai"}
    for name, body in providers.items():
        if name.startswith("_") or not isinstance(body, dict):
            continue
        if name.lower() in banned:
            fail(f"cloud provider '{name}' is configured — see D-003 before proceeding")
        url = str(body.get("baseUrl", ""))
        if url and not (url.startswith("http://localhost") or url.startswith("http://127.0.0.1")):
            fail(f"provider '{name}' points off-machine: {url}")
        else:
            ok(f"provider '{name}': loopback only ({url})")

    for tier, body in frag.get("models", {}).get("tiers", {}).items():
        if tier.startswith("_") or not isinstance(body, dict):
            continue          # underscore keys are commentary, not tiers
        if body.get("provider") != "lmstudio":
            fail(f"tier {tier} uses provider {body.get('provider')!r}, expected lmstudio")
        elif not body.get("model"):
            warn(f"tier {tier} names no model — is it actually loaded?")


# ---------------------------------------------------------------------------
# 4. DST safety
# ---------------------------------------------------------------------------

def next_fire_local_hour(hour: int, minute: int, after: datetime) -> datetime:
    """Next occurrence of a daily local wall-clock time, in America/New_York."""
    candidate = after.astimezone(TZ).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    if candidate <= after.astimezone(TZ):
        candidate += timedelta(days=1)
    return candidate


def check_dst_boundary() -> None:
    print("\n[4] Scheduling survives the November DST change (Rec 21)")
    frag = load_fragment()
    sched = frag.get("scheduling", {})

    tz = sched.get("timezone")
    if tz != "America/New_York":
        fail(f"scheduling.timezone is {tz!r}, must be the named zone 'America/New_York'")
    elif str(tz).startswith(("+", "-", "UTC")):
        fail(f"scheduling.timezone {tz!r} looks like an offset, not a zone")
    else:
        ok("timezone is a named zone, not an offset")

    # US DST ends on the first Sunday of November — 2026-11-01 at 02:00.
    # Straddle it with a full week of clearance on each side. A `before` of
    # Oct 31 is NOT enough: the next 06:30 after it falls on Nov 1, which is
    # already past the changeover, so both samples land in EST and the check
    # silently proves nothing.
    before = datetime(2026, 10, 25, 12, 0, tzinfo=TZ)   # firmly EDT
    after = datetime(2026, 11, 8, 12, 0, tzinfo=TZ)     # firmly EST

    for label, (h, m) in {"morning-brief": (6, 30), "evening-close": (17, 30)}.items():
        a = next_fire_local_hour(h, m, before)
        b = next_fire_local_hour(h, m, after)
        if (a.hour, a.minute) != (b.hour, b.minute):
            fail(f"{label}: local time shifts across the boundary "
                 f"({a:%H:%M %Z} -> {b:%H:%M %Z})")
        elif a.utcoffset() == b.utcoffset():
            warn(f"{label}: UTC offset did not change across the boundary — "
                 "check the zone database is current")
        else:
            ok(f"{label}: {a:%H:%M %Z} -> {b:%H:%M %Z} "
               f"(offset moves, local hour holds)")


# ---------------------------------------------------------------------------
# 5. Live OpenClaw state — D-015 drift
# ---------------------------------------------------------------------------

def _count(cmd: list[str], needle: str) -> int | None:
    """Parse 'X/Y enabled' style headers, which is what OpenClaw prints.

    Counting matching lines does NOT work: the header line itself contains
    the word, and so do wrapped description lines. That mistake produced a
    false drift alarm on 2026-09-08.
    """
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    import re
    m = re.search(rf"\((\d+)\s*/\s*\d+\s+{needle}\)", out)
    return int(m.group(1)) if m else None


def check_openclaw_drift() -> None:
    print("\n[5] OpenClaw attack surface has not drifted from D-015")
    plugins = _count(["openclaw", "plugins", "list"], "enabled")
    skills = _count(["openclaw", "skills", "list"], "ready")

    if plugins is None or skills is None:
        warn("openclaw CLI not available or output unparseable — skipped. "
             "Run this on the Mac, not the workstation.")
        return

    if plugins != D015_PLUGINS:
        fail(f"enabled plugins = {plugins}, D-015 baseline is {D015_PLUGINS}. "
             "An update may have shipped new default-on capability. "
             "Diff and disable before processing mail.")
    else:
        ok(f"enabled plugins = {plugins}")

    if skills != D015_SKILLS_READY:
        fail(f"ready skills = {skills}, D-015 baseline is {D015_SKILLS_READY}. "
             "D-002 requires every skill to be hand-authored in-repo.")
    else:
        ok(f"ready skills = {skills}")


# ---------------------------------------------------------------------------
# 6. LM Studio is loopback-only
# ---------------------------------------------------------------------------

def check_lmstudio_loopback() -> None:
    print("\n[6] LM Studio bound to loopback only")
    try:
        out = subprocess.run(
            ["lsof", "-nP", "-iTCP:1234", "-sTCP:LISTEN"],
            capture_output=True, text=True, timeout=15,
        ).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        warn("lsof unavailable — skipped")
        return

    if not out.strip():
        warn("nothing listening on 1234 — LM Studio may not be running")
    elif "127.0.0.1:1234" in out:
        ok("127.0.0.1:1234 — not reachable from the LAN")
    else:
        fail("port 1234 is NOT bound to loopback. 'Serve on Local Network' is "
             "on, which puts the model endpoint on the LAN. Turn it off before "
             "any mailbox connects.")


def check_readers_available() -> None:
    """Which document readers actually work on THIS machine.

    Every other check here asks whether the configuration is right. This one
    asks whether the code can run, which is a different question and the one
    that bit: PDF support was built, shipped, and could not execute on the Mac
    because PyObjC was never installed. Nothing said so. Each PDF simply
    quarantined, one at a time, with a reason that described the symptom.

    A capability that is missing should be announced once at setup, not
    rediscovered per document.
    """
    print("\n[7] Document readers available on this machine")

    # State the interpreter. Every answer below is only true of this one, and
    # the whole point of the re-exec above is that it is the same interpreter
    # the LaunchAgent runs.
    venv = Path(__file__).resolve().parents[1] / ".venv"
    here = Path(sys.prefix).resolve()
    if venv.exists() and here == venv.resolve():
        ok(f"environment: {here} — the one the LaunchAgent runs")
    else:
        warn(f"environment: {here} — NOT {venv}, so what follows may not "
             f"describe where the daemon actually runs. If a stray venv is "
             f"active in this shell, `deactivate` and re-run.")

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        from core.pipeline import ocr
    except Exception as exc:
        fail(f"could not import the OCR module: {exc}")
        return

    ok("plain text (.txt, .md, .csv) — always available")

    if ocr.vision_available():
        ok("Apple Vision — on-device OCR for photographed mail")
    elif sys.platform != "darwin":
        warn(f"Apple Vision unavailable on {sys.platform} — expected, macOS only")
    else:
        warn("Apple Vision UNAVAILABLE — photographed mail falls back to "
             "glm-ocr in LM Studio, which is slower. Install with: "
             ".venv/bin/pip install pyobjc-framework-Vision")

    if ocr.pdfkit_available():
        ok("PDFKit — PDF text layer and page rasterisation")
    elif sys.platform != "darwin":
        # Not a defect off a Mac, and reporting it as one trains people to
        # scroll past the section that also carries the real failures.
        warn(f"PDFKit unavailable on {sys.platform} — expected, these are "
             f"macOS frameworks. Meaningless off the target machine.")
    else:
        fail("PDFKit UNAVAILABLE — EVERY PDF will quarantine unread, whatever "
             "it contains, and PDF is the format most real mail arrives in. "
             "Install with: .venv/bin/pip install pyobjc-framework-Quartz")

    if ocr.pdfkit_available() and not ocr.vision_available():
        warn("scanned PDFs cannot be read: the text layer will work, but a "
             "scan needs Vision and it is missing")


def check_backup_ready() -> None:
    """Can this machine actually take a backup tonight?

    Every part of this is something the 02:30 job would otherwise discover on
    its own, in a log nobody reads, on the night it was needed. The Keychain
    item in particular failed silently on the first real run: `security
    add-generic-password -w` accepted an EMPTY password without complaint, and
    restic would have initialised the repository with an empty passphrase and
    reported success.
    """
    print("\n[8] Backup readiness")

    # The Keychain is macOS-only, so off a Mac this section can only ever
    # report the platform back at you. Same reasoning as [7]: a check that
    # always fails somewhere it cannot pass trains people to skip the section
    # that also carries the real failures.
    if sys.platform != "darwin":
        warn(f"backup readiness is not checkable on {sys.platform} — the "
             f"Keychain and the target volume are macOS-only")
        return

    if shutil.which("restic") is None:
        fail("restic is not installed — there is no backup on this machine. "
             "`brew install restic`")
    else:
        v = subprocess.run(["restic", "version"], capture_output=True, text=True)
        ok(f"restic present ({v.stdout.split()[1] if v.stdout.split() else '?'})")

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import backup as bk
    except Exception as exc:
        fail(f"could not import ops/backup.py: {exc}")
        return

    try:
        pw = bk.restic_password()
    except Exception as exc:
        first = str(exc).splitlines()[0]
        fail(f"repository password: {first}")
    else:
        ok(f"repository password present ({len(pw)} chars) — and it is in "
           f"Matthew's password manager, yes? restic has no recovery path.")

    archive = os.environ.get("LMS_ARCHIVE_ROOT")
    if not archive:
        warn("LMS_ARCHIVE_ROOT unset — cannot check where the repo would go")
        return
    archive = Path(archive).resolve()
    repo = Path(os.environ.get("LMS_BACKUP_REPO",
                               archive.parent / "LMS_backup")).resolve()
    if bk.same_volume(archive, repo):
        warn(f"the repo ({repo}) is on the SAME VOLUME as the archive. It "
             f"survives a mistake, not the disk. Blocked on D-011 — nobody has "
             f"said which volume is the mirror.")
    else:
        ok(f"repo is on a different volume from the archive")


# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--skip-openclaw", action="store_true",
                   help="config-only checks; for CI on a machine without the CLI")
    args = p.parse_args()

    print("LMS pre-flight verification")
    print("=" * 60)

    check_agents_have_no_tools()
    check_skills_declare_no_tools()
    check_no_cloud_provider()
    check_dst_boundary()
    check_readers_available()
    check_backup_ready()
    if not args.skip_openclaw:
        check_openclaw_drift()
        check_lmstudio_loopback()

    print("\n" + "=" * 60)
    if failures:
        print(f"{RED}{len(failures)} FAILED{RESET} — do not process mail until resolved:")
        for f in failures:
            print(f"  - {f}")
        return 1
    if warnings:
        print(f"{GREEN}All checks passed{RESET} ({len(warnings)} warning(s))")
    else:
        print(f"{GREEN}All checks passed{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
