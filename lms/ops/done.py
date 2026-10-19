#!/usr/bin/env python3
"""Cross something off.

    ./ops/done.py --list                 # open tasks, with their ids
    ./ops/done.py 7                      # id 7 is done
    ./ops/done.py 7 --note "served 10 Sep"
    ./ops/done.py 9 --dismiss            # this should never have been a task
    ./ops/done.py 9 --reopen             # undo either
    ./ops/done.py --dismissed            # what the system got wrong
    ./ops/done.py 12 --duplicate-of 7    # the same obligation, recorded twice
    ./ops/done.py 7 --show               # sources, state and history of one task

BY ID, NOT BY POSITION. `--list` and the brief both number by score, and the
score moves — a task that is second this morning is first tomorrow because
something ahead of it was completed. So the id is what closes a task, and it is
printed in the left column.

DONE VERSUS DISMISS. Both close it. `--dismiss` additionally says the task
should never have existed, which is the only signal that tells us whether the
classifier invents work. Use it when the answer to "did you do this?" is "there
was nothing to do".

LEDGER V2 (D-066). Done is CLOSED with your word as the evidence; dismiss is
CANCELED with outcome DISMISSED; reopen puts it back at TRIAGED. A duplicate
moves its sources onto the task it duplicates, and is refused across entities.

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

from _env import archive_volume_problem, load_lms_env      # noqa: E402
from _reexec import ensure_venv                            # noqa: E402
from core.db import database as db                         # noqa: E402
from core.pipeline import ledger                           # noqa: E402
from core.pipeline import tasks as task_ops                # noqa: E402
from core.pipeline.registry import load_registry           # noqa: E402
from core.reports import brief as reports                  # noqa: E402


def connect():
    archive = os.environ.get("LMS_ARCHIVE_ROOT")
    if not archive:
        raise SystemExit("LMS_ARCHIVE_ROOT is not set — ops/lms.env not found")
    path = Path(os.environ.get("LMS_DB", str(Path(archive).parent / "lms.db")))
    # Before db.connect, which would try to mkdir under a missing mount point
    # and die with "Permission denied: '/Volumes/MacStudioHD'" (Oct 19).
    problem = archive_volume_problem(str(path.parent))
    if problem:
        raise SystemExit(problem)
    return db.connect(path)


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


def show_task(conn, task_id: int) -> int:
    row = task_ops.get_task(conn, task_id)
    print(f"#{row['id']}  {row['title']}")
    for label, key in (("status", "status"), ("outcome", "final_outcome"),
                       ("entity", "entity_id"), ("domain", "domain"),
                       ("owner", "owner"), ("agent", "assigned_agent"),
                       ("due", "due_date"), ("urgency", "urgency"),
                       ("risk", "action_risk"), ("event key", "event_key"),
                       ("duplicate of", "duplicate_of"),
                       ("evidence", "verification_evidence")):
        if row[key] not in (None, ""):
            extra = ""
            if key == "due_date":
                extra = " (verified)" if row["due_date_verified"] else " (as detected)"
            print(f"  {label:<13}{row[key]}{extra}")
    srcs = ledger.sources(conn, task_id)
    print(f"\n  sources ({len(srcs)}):")
    for s in srcs:
        print(f"    {(s['received_at'] or s['seen_at'])[:16]}  {s['source']:<10} "
              f"{s['source_key']}")
    print("\n  history:")
    for e in ledger.history(conn, task_id):
        move = (f"{e['from_status'] or ''} -> {e['to_status']}"
                if e["to_status"] else e["event"])
        print(f"    {e['ts'][:16]}  {e['actor']:<10} {move}"
              f"{'  ' + e['detail'] if e['detail'] else ''}")
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
    p.add_argument("--duplicate-of", type=int, metavar="ID",
                   help="this task is the same obligation as task ID")
    p.add_argument("--show", action="store_true",
                   help="one task: sources, state, history")
    p.add_argument("--note", default=None)
    args = p.parse_args()

    conn = connect()
    try:
        if args.dismissed:
            return show_dismissed(conn)
        if args.list or args.task_id is None:
            return show_open(conn)

        if args.show:
            return show_task(conn, args.task_id)

        if args.duplicate_of is not None:
            r = task_ops.mark_duplicate(conn, args.task_id, args.duplicate_of)
            if r.was_already:
                print(f"#{r.task_id} was already a duplicate: {r.title}")
            else:
                print(f"#{r.task_id} folded into #{args.duplicate_of}: {r.title}")
            return 0

        if args.reopen:
            r = task_ops.reopen_task(conn, args.task_id)
            if r.was_already:
                print(f"#{r.task_id} was already open: {r.title}")
            else:
                print(f"Reopened #{r.task_id}: {r.title}")
            return 0

        outcome = "DISMISSED" if args.dismiss else "DONE"
        r = task_ops.close_task(conn, args.task_id, outcome=outcome,
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
