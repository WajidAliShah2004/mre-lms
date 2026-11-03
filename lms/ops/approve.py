#!/usr/bin/env python3
"""Answer an approval (R19, D-067).

    ./ops/approve.py                         # what is waiting, soonest expiry first
    ./ops/approve.py AP-3                    # the exact action, in full
    ./ops/approve.py AP-3 --approve --note "Matthew, phone call 3 Nov"
    ./ops/approve.py AP-3 --revise  --note "use the new account number"
    ./ops/approve.py AP-3 --deny    --note "not this month"
    ./ops/approve.py --task 7                # every approval asked for task 7

An approval is for the exact details shown: the action, its recipient,
destination, amount and document version. Change any of them and it no longer
applies; the agent asks again. It is used once, and it expires
(config/mandates.yaml, approval_ttl_hours). Nothing is approved by waiting.

THE PERSON AT THIS TERMINAL IS USUALLY THE DEVELOPER, not Matthew. Approving
here records `--by developer` unless told otherwise, and an approve or deny by
the developer needs `--note` saying when and how Matthew gave the answer. When
an approval channel on his phone exists (R10/R11), it calls the same
core.pipeline.approvals.decide and nothing here changes.

A payment approved while rules.yaml's never_move_money stands is his
agreement, recorded; nothing pays it. The output says so.
"""

from __future__ import annotations

import argparse
import json
import sys

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _env import load_lms_env                              # noqa: E402
from _reexec import ensure_venv                            # noqa: E402
from core.pipeline import approvals, ledger                # noqa: E402
from done import connect                                   # noqa: E402


def _money(cents) -> str:
    return "—" if cents is None else f"${cents / 100:,.2f}"


def show_pending(conn) -> int:
    expired = approvals.expire_due(conn)
    for ref in expired:
        print(f"{ref} expired; its task went back for revision.")
    rows = approvals.pending(conn)
    if not rows:
        print("Nothing is waiting for approval.")
        return 0
    print(f"{'ref':<7} {'task':>5}  {'expires':<16}  action")
    for a in rows:
        print(f"{a['ref']:<7} {a['task_id']:>5}  {a['expires_at'][:16]}  "
              f"{a['kind']} · {a['recipient'] or ''} · {_money(a['amount_cents'])}")
    print(f"\n{len(rows)} waiting. Show one with: ./ops/approve.py <ref>")
    return 0


def show_one(conn, ref: str) -> int:
    a = approvals.get(conn, ref)
    task = ledger.get(conn, int(a["task_id"]))
    print(f"{a['ref']}  {a['status']}  task #{task['id']} {task['title']}")
    print(f"  asked by {a['requested_by']} at {a['requested_at'][:16]}, "
          f"expires {a['expires_at'][:16]}")
    print("\n  the exact action:")
    for k, v in json.loads(a["action_json"]).items():
        if v not in (None, "", []):
            print(f"    {k:<15}{_money(v) if k == 'amount_cents' else v}")
    for label, key in (("before/after", "before_after"), ("anomaly", "anomaly"),
                       ("evidence", "evidence_links"), ("draft", "draft")):
        if a[key] not in (None, "", "[]"):
            print(f"\n  {label}:\n    {a[key]}")
    print(f"\n  policy: {a['policy_verdict']}"
          + (f" — {a['policy_reasons']}" if a["policy_reasons"] else ""))
    if a["would_execute"]:
        print(f"  mandate {a['mandate_id']} would have executed this on its own.")
    if a["decided_by"]:
        print(f"  answered {a['status'].lower()} by {a['decided_by']} at "
              f"{(a['decided_at'] or '')[:16]}"
              + (f": {a['decision_note']}" if a["decision_note"] else ""))
    print(f"\n  fingerprint {a['fingerprint'][:16]}…")
    return 0


def main() -> int:
    ensure_venv("LMS_APPROVE_REEXEC", script=__file__)
    load_lms_env()

    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("ref", nargs="?", help="AP-<n>")
    answer = p.add_mutually_exclusive_group()
    answer.add_argument("--approve", action="store_true")
    answer.add_argument("--revise", action="store_true")
    answer.add_argument("--deny", action="store_true")
    p.add_argument("--note", default=None)
    p.add_argument("--by", default="developer",
                   help="who answered: matthew, or developer (the default)")
    p.add_argument("--task", type=int, metavar="ID",
                   help="every approval asked for this task")
    args = p.parse_args()

    conn = connect()
    try:
        if args.task is not None:
            for a in approvals.for_task(conn, args.task):
                print(f"{a['ref']:<7} {a['status']:<10} {a['requested_at'][:16]}  "
                      f"{a['kind']} · {_money(a['amount_cents'])}")
            return 0
        if not args.ref:
            return show_pending(conn)
        verb = ("approve" if args.approve else "revise" if args.revise
                else "deny" if args.deny else None)
        if verb is None:
            return show_one(conn, args.ref)
        if verb != "revise" and args.by.lower() != "matthew" and not args.note:
            print("An answer given for Matthew needs --note saying when and how "
                  "he gave it.", file=sys.stderr)
            return 2

        approvals.expire_due(conn)
        r = approvals.decide(conn, args.ref, verb, by=args.by, note=args.note)
        print(f"{r.ref} {r.status.lower()}: task #{r.task_id}")
        if r.status == approvals.APPROVED and not r.authorizes:
            print("A hard stop forbids this action: the approval is recorded as his "
                  "agreement and nothing will execute it. Do it by hand, then "
                  f"./ops/done.py {r.task_id}")
        return 0
    except (approvals.ApprovalError, ledger.LedgerError) as exc:
        print(exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
