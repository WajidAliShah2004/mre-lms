"""File a chat source's history in batches (R28; D-079, D-081).

Shared by backfill_whatsapp.py and backfill_imessage.py. Files every chat
day before yesterday; yesterday and today are the hourly job's, so its
urgency check still sees them. Days older than 2 days raise no alerts:
notify's 2-day rule drops them. The day before yesterday is still fresh, so
an urgent one alerts once as it is filed. Not a check of the source: nothing
goes to source_checks.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _halt                                                # noqa: E402
from _env import archive_volume_problem, load_lms_env      # noqa: E402
from _reexec import ensure_venv                            # noqa: E402
from core.adapters import asr                              # noqa: E402


def backfill(pipe, conn, roots, registry, classifier, *, src_dir: Path,
             tmp_root: Path, now: datetime, days: int, batch: int,
             transcriber) -> int:
    """`pipe` is core.pipeline.whatsapp or core.pipeline.imessage."""
    Path(tmp_root).mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="backfill-", dir=tmp_root))
    before = pipe.yesterday(now)
    total = 0
    try:
        chats = pipe.read_chats(src_dir, tmp, now - timedelta(days=days))
        pipe.record(conn, chats)
        while True:
            result = pipe.PassResult()
            n = pipe.close(conn, roots, registry, classifier, chats, now, tmp,
                           transcriber, result, before=before, limit=batch)
            total += n
            print(f"batch: {result.summary()}", flush=True)
            if n < batch:
                return total
            if _halt.read_flag() is not None:
                print("HALTED: stopped between batches.", flush=True)
                return total
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(pipe, *, doc: str, reexec: str, script: str, container: Path,
         tmp_name: str, error: type[Exception]) -> int:
    ensure_venv(reexec, script=script)
    load_lms_env()
    p = argparse.ArgumentParser(
        description=doc, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--days", type=int, required=True)
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

    roots = filing.StorageRoots.from_env()
    roots.ensure()
    conn = db.connect(os.environ.get("LMS_DB", str(roots.archive.parent / "lms.db")))
    registry = load_registry()
    try:
        n = backfill(pipe, conn, roots, registry, Classifier(registry),
                     src_dir=container,
                     tmp_root=roots.archive.parent / "tmp" / tmp_name,
                     now=datetime.now().astimezone(), days=args.days,
                     batch=args.batch, transcriber=asr.transcribe)
    except error as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    print(f"{n} chat day(s) filed.")
    return 0
