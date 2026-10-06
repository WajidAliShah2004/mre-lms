#!/usr/bin/env python3
"""The kill switch, at the Mac. Same code path as /halt from the phone.

    ./ops/halt.py              # halt now
    ./ops/halt.py --status     # halted or not; every job's state
    ./ops/halt.py --resume     # bring back exactly what the last halt stopped

--resume exists only here (D-059). The phone can stop the system and can never
restart it.

Resume re-enables and reloads the jobs; it does not run them. Scheduled jobs
fire at their next slot; the gateway and the watcher come up immediately
(RunAtLoad). If a halt was for a reason, fix the reason first.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _halt                                                # noqa: E402
from halt_bot import record                                 # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group()
    g.add_argument("--status", action="store_true")
    g.add_argument("--resume", action="store_true")
    args = p.parse_args()

    if args.status:
        s = _halt.status()
        h = s["halted"]
        print(f"HALTED since {h.get('ts')} by {h.get('by')}" if h
              else "Running (not halted)")
        print(f"haltbot: {s['haltbot']}")
        for label, state in s["jobs"]:
            print(f"  {label:32} {state}")
        return 0

    if args.resume:
        started, problems = _halt.resume()
        for label in started:
            print(f"  started  {label}")
        for msg in problems:
            print(f"  PROBLEM  {msg}")
        if problems:
            print("\nStill marked HALTED. Fix the above and run --resume again.")
            return 1
        record("SYSTEM_RESUMED", f"at the Mac; {started}")
        print("\nResumed. Run ./ops/verify_setup.py before trusting it.")
        return 0

    result = _halt.halt(by="local")
    print(_halt.describe(result))
    record("SYSTEM_HALTED",
           f"at the Mac in {result.elapsed:.2f}s; targets={result.targets}; "
           f"still_running={result.still_running}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
