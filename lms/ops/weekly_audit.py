#!/usr/bin/env python3
"""Weekly security audit (Phase 8 item 6, spec Q216).

    ./ops/weekly_audit.py            # what com.lms.audit runs, Sundays 05:15 ET

Runs `openclaw security audit --deep` and `ops/verify_setup.py`, writes both
outputs to ~/LMS/logs/audit/audit-YYYY-MM-DD.txt, and records AUDIT_COMPLETED
in the audit log with the counts. The brief reports the job as stalled if it
goes quiet for 16 days (brief.WATCHED_JOBS), so an audit that silently stops
running is noticed, which is the failure a weekly job actually has.

It records what it FOUND, not a pass/fail verdict it cannot make: the audit's
two standing warnings (trusted_proxies_missing, operator.read) are accepted,
and deciding what is "clean" is a person's job. A change in the counts from one
week to the next is what is worth looking at, and the file makes that a diff.

Read-only: neither command changes configuration.
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

OUT_DIR = Path.home() / "LMS" / "logs" / "audit"
SUMMARY = re.compile(r"Summary:\s*(\d+)\s*critical\D+(\d+)\s*warn\D+(\d+)\s*info", re.I)


def parse_summary(text: str) -> tuple[int, int, int] | None:
    """'Summary: 0 critical · 2 warn · 2 info' -> (0, 2, 2)."""
    m = SUMMARY.search(text)
    return tuple(int(g) for g in m.groups()) if m else None


def run(argv: list[str], timeout: int = 900) -> tuple[int, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, f"could not run {argv[0]}: {exc}"


def record(detail: str) -> None:
    try:
        from _env import archive_volume_problem, load_lms_env
        load_lms_env()
        if archive_volume_problem():
            print("archive not mounted: audit not recorded in the database")
            return
        import os
        from core.db import database as db
        archive = os.environ["LMS_ARCHIVE_ROOT"]
        conn = db.connect(os.environ.get("LMS_DB",
                                         str(Path(archive).parent / "lms.db")))
        db.log_action(conn, "AUDIT_COMPLETED", detail=detail[:400])
        conn.close()
    except Exception as exc:                                  # noqa: BLE001
        print(f"could not record the audit: {exc}", file=sys.stderr)


def main() -> int:
    from _reexec import ensure_venv
    ensure_venv("LMS_AUDIT_REEXEC", script=__file__)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    report = OUT_DIR / f"audit-{stamp}.txt"

    rc_a, audit = run(["openclaw", "security", "audit", "--deep"])
    rc_v, verify = run([sys.executable, str(Path(__file__).with_name("verify_setup.py"))])
    report.write_text(
        f"# openclaw security audit --deep (exit {rc_a})\n{audit}\n\n"
        f"# verify_setup.py (exit {rc_v})\n{verify}\n", encoding="utf-8")

    counts = parse_summary(audit)
    verdict = "verify_setup PASS" if rc_v == 0 else "verify_setup FAIL"
    if counts is None:
        print(f"audit summary not found (exit {rc_a}); see {report}")
        return 1                      # no row: the brief will say it stalled
    detail = (f"critical={counts[0]} warn={counts[1]} info={counts[2]}; "
              f"{verdict}; report={report}")
    print(detail)
    record(detail)
    return 0 if counts[0] == 0 and rc_v == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
