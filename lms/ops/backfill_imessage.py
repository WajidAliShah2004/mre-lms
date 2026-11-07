#!/usr/bin/env python3
"""File the iMessage and text history Messages on the Mac holds (R2, R28, D-081).

    ./ops/backfill_imessage.py --days 90              # the first load: 90 days
    ./ops/backfill_imessage.py --days 365 --batch 50  # older years, later

Files every chat day before yesterday; yesterday and today are the hourly
job's. Days older than 2 days raise no alerts (notify's 2-day rule), so 90
days of history does not flood his phone. Not a check of the source:
nothing goes to source_checks. The loop is ops/_backfill.py.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _backfill                                            # noqa: E402
import _halt                                                # noqa: E402,F401
from core.adapters import asr                              # noqa: E402
from core.adapters import imessage as im                   # noqa: E402
from core.pipeline import imessage as i                    # noqa: E402


def backfill(conn, roots, registry, classifier, *, src_dir: Path, tmp_root: Path,
             now: datetime, days: int, batch: int, transcriber=asr.transcribe) -> int:
    return _backfill.backfill(i, conn, roots, registry, classifier,
                              src_dir=src_dir, tmp_root=tmp_root, now=now,
                              days=days, batch=batch, transcriber=transcriber)


def main() -> int:
    return _backfill.main(i, doc=__doc__, reexec="LMS_IMESSAGE_REEXEC",
                          script=__file__, container=im.CONTAINER,
                          tmp_name="imessage", error=im.IMessageError)


if __name__ == "__main__":
    raise SystemExit(main())
