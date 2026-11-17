#!/usr/bin/env python3
"""File older drive documents in batches (R5, R28; D-083).

    ./ops/backfill_drives.py --source storage:icloud-drive --since 2025-01-01
    ./ops/backfill_drives.py --source storage:gdrive:matthew@mrecai.com \
        --since 2024-01-01 --until 2024-12-31 --folder "My Drive/Clients"

The 3-hourly pass's rules with an earlier start date: documents only,
unchanged for 24 hours, at most 100 MB, from the areas that are filed.
It works from the index the pass keeps, so the drive needs one pass first.
Oldest first, in batches of 20; a filed revision is never filed again, so an
interrupted run picks up where it stopped. Stops between batches on a halt.
Not a check of the drive: nothing goes to source_checks.

--folder matches the path as the drive reports it, starting with its root:
"iCloud Drive/...", "My Drive/...", a shared drive's name, "Dropbox/...".
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _halt                                                # noqa: E402
from _env import archive_volume_problem, load_lms_env      # noqa: E402
from _reexec import ensure_venv                            # noqa: E402
from core.adapters.drive import DriveError                 # noqa: E402


def _never() -> bool:
    return False


def index_problem(conn, source_id: str) -> str | None:
    from core.pipeline import drives
    if drives.start_date(conn, source_id) is None:
        return (f"{source_id} has no index yet. Run one pass first: "
                f"./ops/drives.py --source {source_id}")
    return None


def backfill(src, conn, roots, registry, classifier, *, now: datetime, tmp_root: Path,
             since: str, until: str | None = None, folder: str | None = None,
             batch: int = 20, halted=_never) -> int:
    from core.pipeline import drives
    Path(tmp_root).mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="backfill-", dir=tmp_root))
    total = 0
    try:
        while True:
            files, _big = drives.choose(src, conn, now, start=since, until=until,
                                        folder=folder, limit=batch)
            if not files:
                return total
            result = drives.PassResult(src.source_id)
            drives.file_rows(src, conn, roots, registry, classifier, files, tmp, result)
            total += result.filed + result.duplicates + result.review
            print(f"batch: {result.filed} filed, {result.duplicates} already in the "
                  f"archive, {result.review} to review, {result.failed} failed", flush=True)
            if halted():
                print("HALTED: stopped between batches.", flush=True)
                return total
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _day(value: str) -> str:
    return date.fromisoformat(value).isoformat()


def main() -> int:
    ensure_venv("LMS_BACKFILL_DRIVES_REEXEC", script=__file__)
    socket.setdefaulttimeout(120)     # as drives.py: httplib2 has none otherwise
    load_lms_env()
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", required=True)
    p.add_argument("--since", required=True, type=_day, help="YYYY-MM-DD")
    p.add_argument("--until", type=_day, help="YYYY-MM-DD, inclusive")
    p.add_argument("--folder")
    p.add_argument("--batch", type=int, default=20)
    args = p.parse_args()

    if _halt.read_flag() is not None:
        print("HALTED: nothing read.")
        return 0
    problem = archive_volume_problem()
    if problem:
        print(f"archive unavailable: {problem}", file=sys.stderr)
        return 1

    from core.db import database as db
    from core.pipeline import filing
    from core.pipeline.classify import Classifier
    from core.pipeline.registry import load_registry
    from drives import build_source

    roots = filing.StorageRoots.from_env()
    roots.ensure()
    conn = db.connect(os.environ.get("LMS_DB", str(roots.archive.parent / "lms.db")))
    try:
        problem = index_problem(conn, args.source)
        if problem:
            print(problem, file=sys.stderr)
            return 1
        try:
            src = build_source(args.source)
        except DriveError as exc:
            print(f"FAIL  {exc}", file=sys.stderr)
            return 1
        registry = load_registry()
        n = backfill(src, conn, roots, registry, Classifier(registry),
                     now=datetime.now().astimezone(),
                     tmp_root=roots.archive.parent / "tmp" / "drives-backfill",
                     since=args.since, until=args.until, folder=args.folder,
                     batch=args.batch, halted=lambda: _halt.read_flag() is not None)
    finally:
        conn.close()
    print(f"{n} document(s) filed or found already in the archive.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
