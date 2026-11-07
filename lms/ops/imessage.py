#!/usr/bin/env python3
"""Read iMessage and texts, hourly (R2, D-081).

    ./ops/imessage.py              # one pass: what com.lms.imessage runs hourly
    ./ops/imessage.py --probe      # the database's shape, no content; writes nothing
    ./ops/imessage.py --dry-run    # unfiled chat days, open or closed; writes nothing

Reads a copy of Messages' chat.db and of Contacts; never writes to Messages,
never sends. Each pass: hourly urgency check on today's and yesterday's new
messages (alerts through D-077), then every closed chat day filed as one
transcript, then the SMS line of the coverage register. See
core/pipeline/imessage.py and ops/IMESSAGE_APPLY.md.

Halted (D-059), it reads nothing: com.lms.imessage is disabled with every
other job, and run by hand it stops at the flag.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _halt                                                # noqa: E402
from _env import archive_volume_problem, load_lms_env      # noqa: E402
from _reexec import ensure_venv                            # noqa: E402
from core.adapters import imessage as im                   # noqa: E402


def _tally(d: dict) -> str:
    return ", ".join(f"{k} {n}" for k, n in d.items())


def do_probe(src_dir: Path) -> int:
    tmp = Path(tempfile.mkdtemp(prefix="im-probe-"))
    try:
        copy = im.snapshot(src_dir, tmp / "db")
        conn = sqlite3.connect(copy)
        try:
            p = im.probe(conn)
        finally:
            conn.close()
        print(f"database: {Path(src_dir) / im.DB_NAME} (copied, checked)")
        print("tables: " + ", ".join(p["tables"]))
        for table, n in p["counts"].items():
            print(f"{table}: {n}")
        for m in p["missing"]:
            print(f"MISSING: {m}")
        if p["missing"]:
            return 1
        print("messages by service: " + _tally(p["services"]))
        print("chats by style: " + ", ".join(f"{s}={n}" for s, n in p["styles"].items())
              + f"; the reader takes {im.GROUP_STYLE} as group")
        print("attachments by type: " + _tally(p["attachment_types"])
              + f"; link previews (skipped): {p['plugin_payloads']}")
        print(f"skipped: {p['reactions']} reactions, {p['system']} system rows")
        need, decoded = p["attributed"]
        print(f"text only in attributedBody: {need}, decoded {decoded}")
        print(f"chat identifiers with more than one chat row: {p['shared_identifiers']}")
        found, filled = p["files"]
        print(f"attachment files on the Mac: {found} of {filled}")
        print("newest: " + _tally(p["newest"]))
        names = im.contact_names(im.CONTACTS_ROOT, tmp / "contacts")
        print(f"contacts read: {len(names)}"
              + ("" if names else " (names fall back to numbers)"))
        return 0
    except (im.IMessageError, OSError, sqlite3.Error) as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def do_dry_run(conn, now: datetime, src_dir: Path = im.CONTAINER) -> int:
    from core.pipeline import imessage as i
    tmp = Path(tempfile.mkdtemp(prefix="im-dry-"))
    try:
        chats = i.read_chats(src_dir, tmp, now - timedelta(days=3))
    except (im.IMessageError, OSError) as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    rows = i.plan_days(conn, chats, now)
    for label, day, n, is_closed in rows:
        print(f"  {day}  {'closed' if is_closed else 'open  '}  {n:4d}  {label}")
    print(f"{len(rows)} chat day(s) unfiled. Nothing was written.")
    return 0


def main() -> int:
    ensure_venv("LMS_IMESSAGE_REEXEC", script=__file__)
    load_lms_env()
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--probe", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    if args.probe:
        return do_probe(im.CONTAINER)

    flag = _halt.read_flag()
    if flag is not None:
        print(f"HALTED since {flag.get('ts')} (by {flag.get('by')}): nothing read.")
        return 0

    problem = archive_volume_problem()
    if problem:
        print(f"archive unavailable: {problem}", file=sys.stderr)
        return 1

    from core.db import database as db
    from core.pipeline import filing
    from core.pipeline import imessage as i
    from core.pipeline.classify import Classifier
    from core.pipeline.registry import load_registry

    roots = filing.StorageRoots.from_env()
    roots.ensure()
    conn = db.connect(os.environ.get("LMS_DB", str(roots.archive.parent / "lms.db")))
    now = datetime.now().astimezone()
    try:
        if args.dry_run:
            return do_dry_run(conn, now)
        registry = load_registry()
        result = i.run_pass(conn, roots, registry, Classifier(registry),
                            src_dir=im.CONTAINER,
                            tmp_root=roots.archive.parent / "tmp" / "imessage",
                            now=now)
    finally:
        conn.close()
    print(result.summary())
    return 1 if result.result == "FAILED" or result.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
