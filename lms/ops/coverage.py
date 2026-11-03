#!/usr/bin/env python3
"""What the system reads, and what it does not (R7, D-069).

    ./ops/coverage.py              # every source: status, last good check, gap
    ./ops/coverage.py --gaps       # only what is not fully covered right now

The register is config/sources.yaml; the checkpoints come from source_checks,
written by every reader on every pass. `--gaps` is the list the daily
reconciliation must disclose (addendum §6): while it prints anything, the day
was not fully reviewed, and nothing may say it was.
"""

from __future__ import annotations

import argparse
import sys

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _env import load_lms_env                              # noqa: E402
from _reexec import ensure_venv                            # noqa: E402
from core.pipeline import coverage                         # noqa: E402
from done import connect                                   # noqa: E402


def _when(dt) -> str:
    return dt.strftime("%b %d %H:%M") if dt else "—"


def show_all(conn) -> int:
    rows = coverage.coverage(conn)
    print(f"{'source':<34} {'status':<12} {'every':>6}  {'last good':<12} last")
    for c in rows:
        s = c.source
        every = f"{s.cadence_hours:g}h" if s.cadence_hours else "push" \
            if s.status == "active" else "—"
        last = c.last_result or ""
        if c.missed:
            last += f" ({c.missed} missed)"
        print(f"{s.label[:34]:<34} {s.status:<12} {every:>6}  "
              f"{_when(c.last_ok):<12} {last}")
        if s.status != "active" and s.blocked_on:
            print(f"{'':<34} waits on: {' '.join(s.blocked_on.split())}")
    active = sum(1 for c in rows if c.source.status == "active")
    print(f"\n{active} of {len(rows)} sources read. "
          f"./ops/coverage.py --gaps for what must be disclosed.")
    return 0


def show_gaps(conn) -> int:
    lines = coverage.disclosures(conn)
    if not lines:
        print("Every registered source completed its schedule.")
        return 0
    for line in lines:
        print(f"- {' '.join(line.split())}")
    print(f"\n{len(lines)} gap(s). The day is not fully reviewed.")
    return 1


def main() -> int:
    ensure_venv("LMS_COVERAGE_REEXEC", script=__file__)
    load_lms_env()
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--gaps", action="store_true",
                   help="only what is not fully covered right now")
    args = p.parse_args()
    conn = connect()
    try:
        return show_gaps(conn) if args.gaps else show_all(conn)
    except coverage.RegisterError as exc:
        print(f"config/sources.yaml: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
