#!/usr/bin/env python3
"""File the WhatsApp history the Mac app holds (R40, R28, D-079).

    ./ops/backfill_whatsapp.py --days 30              # last 30 days, 20 chat days a batch
    ./ops/backfill_whatsapp.py --days 365 --batch 50

Files every chat day before yesterday; yesterday and today are the hourly
job's, so its urgency check still sees them. Days older than 2 days raise no
alerts: notify's 2-day rule drops them. The day before yesterday is still
fresh, so an urgent one alerts once as it is filed.
Not a check of the source: nothing goes to source_checks. The loop is
ops/_backfill.py, shared with backfill_imessage.py.
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
from core.adapters import whatsapp as wa                   # noqa: E402
from core.pipeline import whatsapp as w                    # noqa: E402


def backfill(conn, roots, registry, classifier, *, src_dir: Path, tmp_root: Path,
             now: datetime, days: int, batch: int, transcriber=asr.transcribe) -> int:
    return _backfill.backfill(w, conn, roots, registry, classifier,
                              src_dir=src_dir, tmp_root=tmp_root, now=now,
                              days=days, batch=batch, transcriber=transcriber)


def main() -> int:
    return _backfill.main(w, doc=__doc__, reexec="LMS_WHATSAPP_REEXEC",
                          script=__file__, container=wa.CONTAINER,
                          tmp_name="whatsapp", error=wa.WhatsAppError)


if __name__ == "__main__":
    raise SystemExit(main())
