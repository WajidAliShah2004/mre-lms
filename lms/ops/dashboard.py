#!/usr/bin/env python3
"""Write the task dashboard beside the brief (R22, addendum §6).

    ./ops/dashboard.py                      # write "Dashboard.html" next to the brief
    ./ops/dashboard.py --dry-run            # say where, print the counts, write nothing
    ./ops/dashboard.py --out ~/Desktop/d.html

WHERE IT GOES
-------------
The same iCloud Drive folder the brief is delivered to, for the brief's reason:
there is no push channel yet (C2), and iCloud is already on his phone. The
folder comes from deliver_brief.brief_dir(), imported rather than retyped, so
LMS_BRIEF_DIR moves both or neither. The iOS Files app opens an .html file in
its own viewer with one tap; the page loads nothing from anywhere
(core/reports/dashboard.py says why).

ONE FILE, REPLACED
------------------
The brief keeps a dated file per morning because it is the record of what he
was told. The dashboard is not a record — task_events is — it is the state
now, so it has one stable name and each run replaces it. A Home Screen
bookmark to it always opens the current one.

Replaced atomically: written beside itself, then renamed over the old one, so
iCloud never syncs half a page and the phone never shows one. Read back before
it counts as written, as deliver_brief does, because a sync volume can accept
a write and hand back less.

READS ONLY
----------
It does not expire approvals (the brief job does, deliver_brief.py); one past
its time is left off the page instead of shown as live. The one write is an
actions_log row saying where the page went.

If the archive volume is missing, the database is too. The page is then
replaced by one that says so: leaving yesterday's backlog up, looking current,
is the worse failure.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _env import archive_volume_problem, load_lms_env    # noqa: E402
from _reexec import ensure_venv                          # noqa: E402
import deliver_brief                                     # noqa: E402
from done import connect                                 # noqa: E402
from core.db import database as db                       # noqa: E402
from core.pipeline.registry import load_registry         # noqa: E402
from core.reports import dashboard as dash               # noqa: E402

FILENAME = "Dashboard.html"


def default_target() -> Path:
    return deliver_brief.brief_dir() / FILENAME


def write_atomic(path: Path, text: str) -> None:
    """Replace `path` with `text` in one rename, then prove it reads back."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        # newline="\n": the same bytes on every platform, and a read back
        # that compares equal without newline translation in between.
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()

    try:
        with open(path, encoding="utf-8", newline="") as f:
            back = f.read()
    except OSError as exc:
        raise deliver_brief.DeliveryError(
            f"wrote {path} but could not read it back: {exc}")
    if back != text:
        raise deliver_brief.DeliveryError(
            f"{path} read back as {len(back)} chars, not {len(text)} — the "
            f"write did not complete")


def main(argv: list[str] | None = None) -> int:
    ensure_venv("LMS_DASHBOARD_REEXEC", script=__file__)
    load_lms_env()

    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", metavar="PATH",
                   help=f"write here instead of <brief folder>/{FILENAME}")
    p.add_argument("--dry-run", action="store_true",
                   help="say where it would go and print the counts; write nothing")
    args = p.parse_args(argv)

    target = Path(args.out).expanduser() if args.out else default_target()

    archive = os.environ.get("LMS_ARCHIVE_ROOT")
    if not archive:
        print("LMS_ARCHIVE_ROOT is not set — ops/lms.env was not found",
              file=sys.stderr)
        return 2

    # Before anything opens the database, which lives on the archive volume.
    problem = archive_volume_problem(archive)
    if problem:
        if args.dry_run:
            print(f"would replace {target} with an alarm page:\n{problem}")
            return 1
        try:
            write_atomic(target, dash.render_alarm(problem, datetime.now(db.TZ)))
        except (deliver_brief.DeliveryError, OSError) as exc:
            print(f"alarm page could not be written either: {exc}", file=sys.stderr)
        print(problem, file=sys.stderr)
        return 1        # still a failure: launchctl should show it

    conn = connect()
    try:
        d = dash.build_dashboard(conn, registry=load_registry())
        page = dash.render_html(d)

        if args.dry_run:
            print(f"would write {target} ({len(page.encode('utf-8')):,} bytes)")
            print(dash.render_summary(d))
            print("\n(dry run — nothing written)")
            return 0

        try:
            write_atomic(target, page)
        except (deliver_brief.DeliveryError, OSError) as exc:
            db.log_action(conn, "DASHBOARD_FAILED", detail=str(exc)[:400])
            print(f"dashboard not written: {exc}", file=sys.stderr)
            return 1

        db.log_action(conn, "DASHBOARD_WRITTEN", detail=str(target))
        print(f"dashboard written to {target}")
        print(dash.render_summary(d))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
