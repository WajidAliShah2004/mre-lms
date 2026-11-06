#!/usr/bin/env python3
"""File the WhatsApp history the Mac app holds (R40, R28, D-079).

    ./ops/backfill_whatsapp.py --days 30              # last 30 days, 20 chat days a batch
    ./ops/backfill_whatsapp.py --days 365 --batch 50

Files every chat day before yesterday; yesterday and today are the hourly
job's, so its urgency check still sees them. Old days raise no alerts: they
are marked checked when filed, and notify's 2-day rule drops them anyway.
Not a check of the source: nothing goes to source_checks.
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
from core.adapters import whatsapp as wa                   # noqa: E402
from core.pipeline import whatsapp as w                    # noqa: E402


def backfill(conn, roots, registry, classifier, *, src_dir: Path, tmp_root: Path,
             now: datetime, days: int, batch: int, transcriber=asr.transcribe) -> int:
    Path(tmp_root).mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="backfill-", dir=tmp_root))
    before = w.day_of(now - timedelta(days=1))
    total = 0
    try:
        chats = w.read_chats(src_dir, tmp, now - timedelta(days=days))
        w.record(conn, chats)
        while True:
            result = w.PassResult()
            n = w.close(conn, roots, registry, classifier, chats, now, tmp,
                        transcriber, result, before=before, limit=batch)
            total += n
            print(f"batch: {result.summary()}", flush=True)
            if n < batch:
                return total
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    ensure_venv("LMS_WHATSAPP_REEXEC", script=__file__)
    load_lms_env()
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
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
        n = backfill(conn, roots, registry, Classifier(registry),
                     src_dir=wa.CONTAINER,
                     tmp_root=roots.archive.parent / "tmp" / "whatsapp",
                     now=datetime.now().astimezone(), days=args.days,
                     batch=args.batch)
    except wa.WhatsAppError as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    print(f"{n} chat day(s) filed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
