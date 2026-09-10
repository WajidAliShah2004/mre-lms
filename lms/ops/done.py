#!/usr/bin/env python3
"""Cross something off.

    ./ops/done.py --list                 # open tasks, with their ids
    ./ops/done.py 7                      # id 7 is done
    ./ops/done.py 7 --note "served 10 Sep"
    ./ops/done.py 9 --dismiss            # this should never have been a task
    ./ops/done.py 9 --reopen             # undo either
    ./ops/done.py --dismissed            # what the system got wrong

BY ID, NOT BY POSITION. `--list` and the brief both number by score, and the
score moves — a task that is second this morning is first tomorrow because
something ahead of it was completed. So the id is what closes a task, and it is
printed in the left column.

DONE VERSUS DISMISS. Both close it. `--dismiss` additionally says the task
should never have existed, which is the only signal that tells us whether the
classifier invents work. Use it when the answer to "did you do this?" is "there
was nothing to do".

UNTIL TELEGRAM (C2) THIS IS THE ONLY WAY TO CLOSE A TASK, and it is a terminal
on the Mac — so in practice it is the contractor's, not Matthew's. That is a
gap and not a hidden one: the brief he reads on his phone is a file, and a file
cannot be replied to. When C2 lands, /done <id> calls straight into
core.pipeline.tasks and nothing here changes.
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
from core.pipeline import tasks as task_ops                # noqa: E402
from core.pipeline.registry import load_registry           # noqa: E402
from core.reports import brief as reports                  # noqa: E402


def connect():
    archive = os.environ.get("LMS_ARCHIVE_ROOT")
    if not archive:
        raise SystemExit("LMS_ARCHIVE_ROOT is not set — ops/lms.env not found")
    return db.connect(os.environ.get(
        "LMS_DB", str(Path(archive).parent / "lms.db")))


def show_open(conn) -> int:
    scored = reports.todo_list(conn)
    if not scored:
        print("Nothing open.")
        return 0

    registry = load_registry()
    print(f"{'id':>4}  {'':2} {'due':>12}  task")
    for t in scored:
        row = t.row                       # already fetched; do not re-query
        mark = "!!" if t.overdue else "  "
        due = row["due_date"] or "—"
        try:
            who = registry.get(row["entity_id"]).short_or_name()
        except Exception:
            who = row["entity_id"] or ""
        # `t.reason` already ends with the urgency or the amount — printing
        # row["urgency"] as well produced "HIGH · due TODAY · HIGH". That is
        # D-034's defect in a new place: two layers each solving the same
        # problem alone, in the two most valuable lines of a short read.
        print(f"{t.task_id:>4}  {mark} {due:>12}  {t.title}")
        print(f"{'':>4}     {'':>12}  {who + ' · ' if who else ''}{t.reason}")
    print(f"\n{len(scored)} open. Close one with: ./ops/done.py <id>")
    return 0


def show_dismissed(conn) -> int:
    rows = task_ops.dismissals(conn)
    if not rows:
        print("Nothing has been dismissed. Either the classifier is not "
              "inventing work, or nobody is telling it when it does.")
        return 0
    print("Tasks a human said should never have existed:\n")
    for r in rows:
        print(f"  {r['completed_at'][:10]}  {r['title']}")
    print(f"\n{len(rows)} dismissed. A run of these against one counterparty "
          f"or category is a taxonomy problem; a trickle across everything is "
          f"an urgency-threshold problem.")
    return 0


def main() -> int:
    ensure_venv("LMS_DONE_REEXEC", script=__file__)
    load_lms_env()

    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("task_id", nargs="?", type=int)
    p.add_argument("--list", action="store_true", help="open tasks and their ids")
    p.add_argument("--dismissed", action="store_true",
                   help="tasks that should never have existed")
    p.add_argument("--dismiss", action="store_true",
                   help="close it AND record that it was never a real task")
    p.add_argument("--reopen", action="store_true", help="undo a close")
    p.add_argument("--note", default=None)
    args = p.parse_args()

    conn = connect()
    try:
        if args.dismissed:
            return show_dismissed(conn)
        if args.list or args.task_id is None:
            return show_open(conn)

        if args.reopen:
            r = task_ops.reopen_task(conn, args.task_id)
            if r.was_already:
                print(f"#{r.task_id} was already open: {r.title}")
            else:
                print(f"Reopened #{r.task_id}: {r.title}")
            return 0

        status = "DISMISSED" if args.dismiss else "DONE"
        r = task_ops.close_task(conn, args.task_id, status=status,
                                note=args.note)
        if r.was_already:
            print(f"#{r.task_id} was already {r.was_already.lower()}: {r.title}")
            print("Nothing changed. `--reopen` if that was not intended.")
            return 0

        verb = "Dismissed" if args.dismiss else "Done"
        print(f"{verb} #{r.task_id}: {r.title}")
        if args.dismiss:
            print("Recorded as a task that should not have existed. "
                  "`./ops/done.py --dismissed` shows the pattern.")
        return 0
    except task_ops.TaskError as exc:
        print(exc, file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
