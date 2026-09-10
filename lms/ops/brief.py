#!/usr/bin/env python3
"""Print the brief, or the whole to-do list, from the live database.

Delivery to Telegram is blocked on C2 (the app is not installed on the Mac and
there is no authorised sender), so this is how the brief gets read and checked
until that lands. It is also what the scheduled 06:30 / 17:30 jobs will call,
so what is verified here is what will be sent.

    .venv/bin/python ops/brief.py                 # morning brief
    .venv/bin/python ops/brief.py --evening
    .venv/bin/python ops/brief.py --todo          # the full ranked list
    .venv/bin/python ops/brief.py --todo --entity B_MRE
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _env import load_lms_env                            # noqa: E402
from _reexec import ensure_venv                          # noqa: E402
from core.db import database as db                       # noqa: E402
from core.pipeline.registry import load_registry         # noqa: E402
from core.reports import brief as reports                # noqa: E402


def main() -> int:
    ensure_venv("LMS_BRIEF_REEXEC", script=__file__)
    load_lms_env()

    p = argparse.ArgumentParser()
    p.add_argument("--evening", action="store_true")
    p.add_argument("--todo", action="store_true", help="the full ranked list")
    p.add_argument("--entity", default=None)
    p.add_argument("--person", default=None)
    args = p.parse_args()

    archive = os.environ.get("LMS_ARCHIVE_ROOT")
    if not archive:
        print("LMS_ARCHIVE_ROOT is not set — run: source ops/lms.env", file=sys.stderr)
        return 2

    db_path = os.environ.get("LMS_DB", str(Path(archive).parent / "lms.db"))
    conn = db.connect(db_path)
    registry = load_registry()

    if args.todo:
        scored = reports.todo_list(conn, entity_id=args.entity, person=args.person)
        if not scored:
            print("No open tasks.")
            return 0
        width = len(str(len(scored)))
        for i, t in enumerate(scored, 1):
            flag = "!!" if t.overdue else "  "
            print(f"{flag} {i:>{width}}. [{t.score:6.1f}] {t.title}")
            print(f"{'':>{width + 6}}{t.reason}")
        return 0

    b = reports.build_brief(conn, kind="evening" if args.evening else "morning",
                            now=datetime.now())
    print(reports.render_text(b, registry))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
