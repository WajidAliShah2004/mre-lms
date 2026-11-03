#!/usr/bin/env python3
"""What does not add up across documents (R26, addendum §4).

    ./ops/reconcile.py                   # run every check, record, print open findings
    ./ops/reconcile.py --dry-run         # the same, writing nothing and creating no task
    ./ops/reconcile.py --list            # open findings, without running
    ./ops/reconcile.py --links           # candidate bill / payment / statement links
    ./ops/reconcile.py --ack 12 --note "resend, paid once"
    ./ops/reconcile.py --confirm-link 4  # a human says the link is right
    ./ops/reconcile.py --reject-link 4

FINDINGS, NEVER EDITS. Nothing here moves a document, corrects a
classification, or closes, merges or reopens a task. A finding names the
command that would act on it (`./ops/done.py 15 --duplicate-of 12`) and a human
runs it. The one exception is a renewal date read from a document, which
becomes a DETECTED task through the ledger, once.

WHAT IT CHECKS
  RECEIVED_AGAIN     a closed or apparently paid bill arrived again (scenario 4)
  DUPLICATE_INVOICE  one invoice on two open tasks
  AMOUNT_MISMATCH    copies of one invoice, or its payment, disagree on the amount
  MERGED_INVOICES    two invoice numbers on one task
  DOUBLE_BILLING     two invoices, same counterparty, amount and month
  MISSING_STATEMENT  a monthly statement series skipped a month
  UNANSWERED_MAIL    not checked: there is no sent-mail source yet, and the
                     output says how many messages are waiting on one

Amounts and numbers come from a model or OCR. Links are candidates (confidence
below 1.0) until confirmed here, and disagreeing amounts are reported, not
resolved.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _env import archive_volume_problem, load_lms_env      # noqa: E402
from _reexec import ensure_venv                            # noqa: E402
from core.db import database as db                         # noqa: E402
from core.pipeline import reconcile                        # noqa: E402


def connect():
    archive = os.environ.get("LMS_ARCHIVE_ROOT")
    if not archive:
        raise SystemExit("LMS_ARCHIVE_ROOT is not set — ops/lms.env not found")
    path = Path(os.environ.get("LMS_DB", str(Path(archive).parent / "lms.db")))
    problem = archive_volume_problem(str(path.parent))
    if problem:
        raise SystemExit(problem)
    return db.connect(path)


def _label(kind: str) -> str:
    return kind.replace("_", " ")


def print_findings(rows, *, heading: str) -> None:
    if not rows:
        print(f"{heading}: nothing to look at.")
        return
    print(f"{heading}: {len(rows)}\n")
    for r in rows:
        conf = f"{r['confidence']:.2f}" if r["confidence"] is not None else "—"
        status = "" if r["status"] == "OPEN" else f"  [{r['status'].lower()}]"
        print(f"  #{r['id']:<4} {_label(r['kind'])} · {r['entity_id'] or '—'} · "
              f"{conf}{status}")
        print(f"        {r['summary']}")
        if r["detail"]:
            print(f"        {r['detail']}")
        ids = json.loads(r["artifact_ids"] or "[]")
        bits = []
        if ids:
            bits.append("artifacts " + ", ".join(str(i) for i in ids))
        if r["task_id"]:
            bits.append(f"task #{r['task_id']} (./ops/done.py {r['task_id']} --show)")
        if bits:
            print(f"        evidence: {' · '.join(bits)}")
        print()
    print("Seen it and nothing to do: ./ops/reconcile.py --ack <#> --note \"why\"")


def print_links(conn) -> int:
    rows = conn.execute(
        "SELECT * FROM recon_links ORDER BY status = 'REJECTED', id").fetchall()
    if not rows:
        print("No links. Bills, payment receipts and statement lines that "
              "match will appear here after a run.")
        return 0
    for r in rows:
        amount = (f"${r['amount_cents'] / 100:,.2f}"
                  if r["amount_cents"] is not None else "—")
        print(f"  #{r['id']:<4} {r['kind']:<17} {r['from_artifact']} -> "
              f"{r['to_artifact']}  {amount}  {r['confidence']:.2f}  "
              f"{r['status'].lower()}")
        print(f"        {r['basis']}")
    print("\nCandidates are inferred from what a model or OCR read. "
          "--confirm-link / --reject-link <#> once you have looked.")
    return 0


def print_report(report: reconcile.Report, *, dry_run: bool) -> None:
    head = f"Reconciliation, {report.today.isoformat()}"
    if dry_run:
        print(f"{head} — DRY RUN, nothing recorded\n")
        for f in report.findings:
            conf = f"{f.confidence:.2f}" if f.confidence is not None else "—"
            print(f"  {_label(f.kind)} · {f.entity_id or '—'} · {conf}")
            print(f"        {f.summary}")
            if f.detail:
                print(f"        {f.detail}")
        if not report.findings:
            print("  no findings")
    else:
        r = report.recorded
        print(f"{head}: {len(report.findings)} found — {r.new} new, "
              f"{r.reopened} back again, {r.resolved} resolved since the last run")

    links = report.links
    if links:
        extra = ("" if dry_run
                 else f" ({report.new_links} new) — ./ops/reconcile.py --links")
        print(f"\nLinks: {len(links)} candidate{'s' if len(links) != 1 else ''}{extra}")

    if report.renewals:
        print(f"\nRenewal tasks ({'would create' if dry_run else 'recorded'}):")
        # create_renewal_tasks records them in order, one result per renewal.
        results = report.created or [None] * len(report.renewals)
        for rn, rec in zip(report.renewals, results):
            tid = f"#{rec.task_id} " if rec else ""
            how = f" [{rec.outcome}]" if rec and rec.outcome != "created" else ""
            print(f"  {tid}{rn.title} — renews {rn.renewal_date.isoformat()}{how} "
                  f"(\"{rn.phrase[:60]}\")")

    if report.replies_checked:
        print(f"\nUnanswered mail: checked {report.awaiting_reply} message(s).")
    else:
        print(f"\nUnanswered mail: NOT CHECKED — there is no sent-mail source yet "
              f"(thread ids are not recorded). {report.awaiting_reply} message(s) "
              f"marked as needing a reply are waiting on it.")


def main() -> int:
    ensure_venv("LMS_RECONCILE_REEXEC", script=__file__)
    load_lms_env()

    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true",
                   help="run every check; write nothing, create no task")
    p.add_argument("--list", action="store_true", help="open findings, no run")
    p.add_argument("--all", action="store_true",
                   help="with --list: include acknowledged findings")
    p.add_argument("--links", action="store_true", help="candidate links")
    p.add_argument("--ack", type=int, metavar="ID", help="seen, nothing to do")
    p.add_argument("--note", default=None)
    p.add_argument("--confirm-link", type=int, metavar="ID")
    p.add_argument("--reject-link", type=int, metavar="ID")
    p.add_argument("--today", type=date.fromisoformat, default=None,
                   help="pretend it is this date (YYYY-MM-DD)")
    p.add_argument("--grace-days", type=int, default=reconcile.GRACE_DAYS,
                   help="days after its usual date before a statement is missing")
    p.add_argument("--renewal-lead-days", type=int,
                   default=reconcile.RENEWAL_LEAD_DAYS,
                   help="days before a renewal date that it becomes a task")
    args = p.parse_args()

    conn = connect()
    try:
        if args.ack is not None:
            row = reconcile.acknowledge(conn, args.ack, note=args.note)
            print(f"Acknowledged #{row['id']}: {row['summary']}")
            return 0
        if args.confirm_link is not None or args.reject_link is not None:
            confirm = args.confirm_link is not None
            link_id = args.confirm_link if confirm else args.reject_link
            status = "CONFIRMED" if confirm else "REJECTED"
            row = reconcile.decide_link(conn, link_id, status)
            print(f"Link #{row['id']} {status.lower()}: {row['kind']} "
                  f"{row['from_artifact']} -> {row['to_artifact']}")
            return 0
        if args.links:
            return print_links(conn)
        if args.list:
            print_findings(reconcile.open_findings(conn, include_acknowledged=args.all),
                           heading="Open findings")
            return 0

        report = reconcile.run(conn, today=args.today, grace_days=args.grace_days,
                               lead_days=args.renewal_lead_days,
                               record=not args.dry_run)
        print_report(report, dry_run=args.dry_run)
        if not args.dry_run:
            print()
            print_findings(reconcile.open_findings(conn), heading="Open findings")
        return 0
    except (LookupError, ValueError, PermissionError) as exc:
        print(exc, file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
