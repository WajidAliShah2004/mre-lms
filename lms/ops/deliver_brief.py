#!/usr/bin/env python3
"""Write the brief where Matthew will actually see it.

    ./ops/deliver_brief.py                # morning
    ./ops/deliver_brief.py --evening
    ./ops/deliver_brief.py --dry-run      # print it, mark nothing

WHY A FILE IN iCLOUD AND NOT A NOTIFICATION
-------------------------------------------
Telegram is the intended channel and is blocked on C2 — the app is not
installed on the Mac and there is no authorised sender, so there is nothing to
send to and no way for Matthew to reply /halt.

Meanwhile a brief that exists only in `ops/brief.py`'s stdout is not a brief.
It is a function that could produce one, running on a machine in his office
that he does not sit at.

iCloud Drive is already on his phone, already syncing, and needs no credential,
no new app, and nothing from him. The same folder the photo Shortcut writes
into. It is not as good as a push notification — he has to go and look — but it
is the difference between "delivered late" and "not delivered", and it works
today rather than after C2.

WHAT "DELIVERED" MEANS HERE
---------------------------
`mark_brief_sent` sets the boundary for the NEXT brief's "filed since last
brief" section. Marking a brief sent that never arrived does not merely lose
that brief — it permanently removes those documents from every future brief,
because the window moves past them and nothing looks back.

So the file is written, read back, and compared before anything is marked. On a
sync volume that is not paranoia: iCloud can accept a write into a directory it
has not materialised and produce a file that reads back short.
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

# Where the briefs land. iCloud Drive, beside the inbox the Shortcut writes to,
# so both halves of the system live in one folder on his phone.
DEFAULT_DIR = ("~/Library/Mobile Documents/com~apple~CloudDocs/LMS/briefs")

# `.txt`, not `.md`. The iOS Files app previews plain text inline and offers a
# Markdown file as a download. The brief is already rendered as text and the
# point is that he can read it with one tap.
SUFFIX = ".txt"

# Kept, not rotated. A brief is a few hundred bytes and the history is the only
# record of what he was told on a given morning — which matters the first time
# someone asks why something was missed.
LATEST = "Latest brief.txt"


class DeliveryError(RuntimeError):
    pass


def brief_dir() -> Path:
    return Path(os.environ.get("LMS_BRIEF_DIR", DEFAULT_DIR)).expanduser()


def write_verified(path: Path, text: str) -> None:
    """Write it, read it back, and prove they match.

    iCloud will accept a write into a directory it has not fully materialised
    and hand back a file that reads short. A brief marked sent on the strength
    of a write that half-happened takes its documents out of every future
    brief with it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

    try:
        back = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DeliveryError(f"wrote {path} but could not read it back: {exc}")

    if back != text:
        raise DeliveryError(
            f"{path} read back as {len(back)} chars, not {len(text)} — the "
            f"write did not complete, so nothing has been marked sent")


def main() -> int:
    ensure_venv("LMS_DELIVERBRIEF_REEXEC", script=__file__)
    load_lms_env()

    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--evening", action="store_true")
    p.add_argument("--dry-run", action="store_true",
                   help="print it and mark nothing")
    args = p.parse_args()

    kind = "evening" if args.evening else "morning"

    archive = os.environ.get("LMS_ARCHIVE_ROOT")
    if not archive:
        print("LMS_ARCHIVE_ROOT is not set — ops/lms.env was not found",
              file=sys.stderr)
        return 2

    conn = db.connect(os.environ.get(
        "LMS_DB", str(Path(archive).parent / "lms.db")))
    registry = load_registry()

    now = datetime.now()
    b = reports.build_brief(conn, kind=kind, now=now)
    text = reports.render_text(b, registry)

    if args.dry_run:
        print(text)
        print("\n(dry run — nothing written, nothing marked sent)")
        return 0

    target = brief_dir() / f"{now:%Y-%m-%d} {kind}{SUFFIX}"
    try:
        write_verified(target, text)
        # The stable name is what a phone shortcut or a Home Screen bookmark
        # can point at. Written second: if this one fails, the dated file is
        # still on disk and the failure is visible rather than silent.
        write_verified(brief_dir() / LATEST, text)
    except (DeliveryError, OSError) as exc:
        db.log_action(conn, "BRIEF_DELIVERY_FAILED", detail=str(exc)[:400])
        print(f"delivery failed: {exc}", file=sys.stderr)
        print("Nothing was marked sent, so the next run will include the same "
              "items rather than skipping them.", file=sys.stderr)
        conn.close()
        return 1

    # Only now. This moves the window for the next brief, and anything it
    # covered will never appear in one again.
    reports.mark_brief_sent(conn, kind)
    db.log_action(conn, "BRIEF_DELIVERED", detail=str(target))
    conn.close()

    print(f"{kind} brief delivered to {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
