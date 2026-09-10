#!/usr/bin/env python3
"""Tell the system it got one wrong — spec §Day-5.5.

Eventually this happens in Telegram: Matthew taps a filed document in the
morning brief and picks the right business. That needs C2, which is blocked.
Until then it happens here, and the underlying operation is the same one
either way — `core.pipeline.corrections.apply_correction` — so when the
Telegram path arrives it is a new front door onto tested behaviour rather than
a second implementation of it.

    ./ops/correct.py --find acme                     # what could I mean?
    ./ops/correct.py 7 --entity B_ATL                # it is Atlase, not MRECAI
    ./ops/correct.py 7 --category CLIENTS --subcategory policies
    ./ops/correct.py 7 --due 2026-11-01              # the deadline was wrong
    ./ops/correct.py 7 --subcategory NONE            # file it at category level
    ./ops/correct.py --history                       # where it has been wrong

Every correction moves the document, its sidecar and its transcription to
match, and records the old value — which is the only surviving record of what
the model said, since the classification row is overwritten.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _env import load_lms_env                              # noqa: E402
from _reexec import ensure_venv                            # noqa: E402
from core.db import database as db                         # noqa: E402
from core.pipeline import corrections, filing              # noqa: E402
from core.pipeline.registry import load_registry           # noqa: E402


def find(conn, needle: str) -> list:
    like = f"%{needle}%"
    return conn.execute(
        "SELECT a.id, a.original_name, a.status, a.filed_path, "
        "       c.entity_id, c.category, c.subcategory, c.decided_by "
        "  FROM artifacts a "
        "  LEFT JOIN classifications c ON c.artifact_id = a.id "
        " WHERE a.original_name LIKE ? OR a.filed_path LIKE ? "
        " ORDER BY a.id DESC LIMIT 20", (like, like)).fetchall()


def main() -> int:
    ensure_venv("LMS_CORRECT_REEXEC", script=__file__)
    load_lms_env()

    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("artifact_id", nargs="?", type=int)
    p.add_argument("--entity", help="the correct entity_id, e.g. B_ATL")
    p.add_argument("--category")
    p.add_argument("--subcategory",
                   help="a subcategory, or NONE to file at the category level")
    p.add_argument("--date", help="the document's own date, YYYY-MM-DD")
    p.add_argument("--due", help="the task's due date, YYYY-MM-DD")
    p.add_argument("--find", help="search filed and original names")
    p.add_argument("--history", action="store_true",
                   help="every correction so far, newest first")
    args = p.parse_args()

    archive = os.environ.get("LMS_ARCHIVE_ROOT")
    if not archive:
        print("LMS_ARCHIVE_ROOT is not set — run: source ops/lms.env",
              file=sys.stderr)
        return 2
    conn = db.connect(os.environ.get(
        "LMS_DB", str(Path(archive).parent / "lms.db")))

    if args.find:
        rows = find(conn, args.find)
        if not rows:
            print(f"nothing matching {args.find!r}")
            return 1
        for r in rows:
            where = f"{r['entity_id']}/{r['category']}" if r["entity_id"] else r["status"]
            by = f" (by {r['decided_by']})" if r["decided_by"] else ""
            print(f"  #{r['id']:<4} {where}{by}  {r['original_name']}")
        return 0

    if args.history:
        rows = corrections.history(conn, args.artifact_id)
        if not rows:
            print("No corrections recorded. Either it has been right so far, "
                  "or nobody has told it otherwise.")
            return 0
        for r in rows:
            print(f"  {r['ts'][:16].replace('T', ' ')}  #{r['artifact_id']}  "
                  f"{r['field']}: {r['old_value']} -> {r['new_value']}")
        return 0

    if args.artifact_id is None:
        p.error("give an artifact id, or use --find / --history")

    kw: dict = {}
    if args.entity:
        kw["entity_id"] = args.entity
    if args.category:
        kw["category"] = args.category
    if args.subcategory is not None:
        # "NONE" is how you say "no subcategory" on a command line, where the
        # absence of a flag already means "leave it alone".
        kw["subcategory"] = None if args.subcategory.upper() == "NONE" else args.subcategory
    if args.date:
        kw["doc_date"] = args.date
    if args.due:
        kw["due_date"] = args.due
    if not kw:
        p.error("nothing to correct — pass at least one of "
                "--entity / --category / --subcategory / --date / --due")

    registry = load_registry()
    roots = filing.StorageRoots.from_env()

    try:
        out = corrections.apply_correction(conn, roots, registry,
                                           args.artifact_id, **kw)
    except corrections.CorrectionError as exc:
        print(f"\nRefused: {exc}", file=sys.stderr)
        return 1

    if not out.changed:
        print("No change — the system already had it that way.")
        return 0

    for c in out.changes:
        print(f"  {c}")
    if out.refiled:
        print(f"\nMoved to {out.filed_path}")
    else:
        print("\nRecorded. Nothing needed moving.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
