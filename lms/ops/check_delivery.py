#!/usr/bin/env python3
"""Is mail arriving straight into the Gmail inbox, or still via iCloud?

    ./ops/check_delivery.py matthew@mrecai.com --days 2

Read-only (gmail.readonly). For each message in the window, from everywhere
including spam: when it arrived, whether any hop went through iCloud, and
whether it is in the inbox. After the Oct 3 2026 MX cutover (D-054) every new
message should read `direct` and `inbox`; `via iCloud` on anything newer than
the cutover means something is still forwarding.
"""

from __future__ import annotations

import argparse
import socket
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _env import load_lms_env                              # noqa: E402
from _reexec import ensure_venv                            # noqa: E402
from core.adapters import gmail                            # noqa: E402


def _header(raw: dict, name: str) -> str:
    for h in (raw.get("payload") or {}).get("headers", []):
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def main() -> int:
    ensure_venv("LMS_CHECKDELIVERY_REEXEC", script=__file__)
    load_lms_env()
    socket.setdefaulttimeout(120)          # as poll_mail: httplib2 has none
    from poll_mail import build_client

    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("address")
    p.add_argument("--days", type=int, default=2)
    args = p.parse_args()

    raws = build_client(args.address).recent_raw(newer_than_days=args.days)
    tally: Counter = Counter()
    for raw in raws:
        route, state = gmail.delivery_route(raw), gmail.inbox_state(raw)
        tally[route] += 1
        tally[state] += 1
        print(f"{_header(raw, 'Date')[:31]:31}  {route:10}  {state:12}  "
              f"{_header(raw, 'From')[:40]:40}  {_header(raw, 'Subject')[:50]}")

    print(f"\n{len(raws)} message(s) in {args.days}d: "
          f"{tally['direct']} direct, {tally['via iCloud']} via iCloud; "
          f"{tally['inbox']} in inbox, {tally['SPAM']} spam, "
          f"{tally['sent']} sent by him, {tally['not in inbox']} not in inbox")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
