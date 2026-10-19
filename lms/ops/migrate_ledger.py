#!/usr/bin/env python3
"""Migrate the task ledger to v2 (R13, D-066), and say what happened.

    ./ops/migrate_ledger.py --check      # is it needed? changes nothing
    ./ops/migrate_ledger.py              # snapshot, migrate, report

Any job that opens the database after `git pull` migrates it anyway
(core/db/migrate.py runs on every connect). Running this first, with the jobs
stopped, is the same migration with a report printed and a check after it,
instead of a silent one inside whichever job woke up first.

The snapshot is `lms.db.pre-ledger-v2`, next to the database. To go back:
stop the jobs, `git checkout` the commit before D-066, and copy the snapshot
over lms.db (removing lms.db-wal and lms.db-shm). See ops/LEDGER_V2_APPLY.md.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _env import load_lms_env                              # noqa: E402
from _reexec import ensure_venv                            # noqa: E402
from core.db import database as db                         # noqa: E402
from core.db import migrate                                # noqa: E402


def db_path() -> Path:
    if os.environ.get("LMS_DB"):
        return Path(os.environ["LMS_DB"])
    archive = os.environ.get("LMS_ARCHIVE_ROOT")
    if not archive:
        raise SystemExit("LMS_ARCHIVE_ROOT is not set — ops/lms.env not found")
    return Path(archive).parent / "lms.db"


def counts(conn: sqlite3.Connection) -> list[str]:
    return [f"  {r[0]:<20}{r[1]}" for r in conn.execute(
        "SELECT status, COUNT(*) FROM tasks GROUP BY status ORDER BY status")]


def check(path: Path) -> int:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        needed = migrate.needs_ledger_v2(conn)
        print(f"{path}: ledger {'v1 — migration needed' if needed else 'v2 — nothing to do'}")
        print("\n".join(counts(conn)) or "  (no tasks)")
    finally:
        conn.close()
    return 0


def run(path: Path) -> int:
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        before = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        report = migrate.upgrade(conn, path)
        print("\n".join(report.lines()))
    finally:
        conn.close()

    # Through the normal door now, as every job will.
    conn = db.connect(path)
    try:
        after = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
        dupes = conn.execute(
            "SELECT task_id, detail FROM task_events "
            "WHERE event = 'POSSIBLE_DUPLICATE' ORDER BY task_id").fetchall()
        print("\nNow:")
        print("\n".join(counts(conn)) or "  (no tasks)")
        if dupes and not report.migrated:
            print("\nPossible duplicates found when it was migrated:")
            for d in dupes:
                print(f"  #{d['task_id']}: {d['detail']}")
    finally:
        conn.close()

    if report.migrated and after != before:
        print(f"\nFAIL: {before} tasks before, {after} after", file=sys.stderr)
        return 1
    if ok != "ok":
        print(f"\nFAIL: integrity_check says {ok}", file=sys.stderr)
        return 1
    print(f"\nPASS: {after} tasks, integrity ok")
    return 0


def main() -> int:
    ensure_venv("LMS_MIGRATE_LEDGER_REEXEC", script=__file__)
    load_lms_env()
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--check", action="store_true", help="report only; change nothing")
    args = p.parse_args()

    path = db_path()
    if not path.exists():
        raise SystemExit(f"no database at {path}")
    return check(path) if args.check else run(path)


if __name__ == "__main__":
    raise SystemExit(main())
