#!/usr/bin/env python3
"""Poll a mailbox and file what is in it.

    ./ops/poll_mail.py matthew@mrecai.com --once --days 1
    ./ops/poll_mail.py matthew@mrecai.com --once --days 7 --dry-run

Read-only, always: the credential this uses is `gmail.readonly` and cannot be
anything else (core/adapters/gmail.py, D-039). Nothing here marks a message
read, moves it, labels it or replies to it — and could not if it tried.

--dry-run prints what WOULD be ingested and touches neither the database nor
the archive. Run it first against a wide window; it is the cheapest way to see
what a mailbox is actually full of before a week of it lands in the tree.

WHY A WINDOW AND NOT A CURSOR
-----------------------------
`--days 1` re-reads yesterday every time, and that is the point. A stored
"last message id" cursor loses mail permanently the first time a run dies
between reading and committing. Re-reading is free: dedupe is the sha256 of
the rendered message, which does not change between polls, so the second pass
files nothing. A missed night is fixed by `--days 3`, not by a repair script.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _env import archive_volume_problem, load_lms_env    # noqa: E402
from _reexec import ensure_venv                            # noqa: E402
from core.adapters import gmail                            # noqa: E402
from core.db import database as db                         # noqa: E402
from core.pipeline import filing, mail                     # noqa: E402
from core.pipeline.classify import Classifier              # noqa: E402
from core.pipeline.registry import load_registry           # noqa: E402


def stored_grant(address: str) -> dict:
    """The whole OAuth grant, as one Keychain blob (see authorise_gmail.py)."""
    service = gmail.keychain_service(address)
    r = subprocess.run(
        ["security", "find-generic-password", "-s", service, "-w"],
        capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        raise SystemExit(
            f"no OAuth grant for {address}. Authorise it once:\n"
            f"    ./ops/authorise_gmail.py {address} "
            f"--client-json '~/Downloads/client_secret_*.json'")
    try:
        return json.loads(r.stdout.rstrip("\n"))
    except ValueError:
        raise SystemExit(
            f"the Keychain item {service} is not the JSON blob this expects. "
            f"It may be left over from the old app-password attempt — delete "
            f"it and re-run authorise_gmail.py.")


def build_client(address: str) -> gmail.GmailClient:
    from google.oauth2.credentials import Credentials

    g = stored_grant(address)
    creds = Credentials(
        token=None, refresh_token=g["refresh_token"],
        client_id=g["client_id"], client_secret=g["client_secret"],
        token_uri="https://oauth2.googleapis.com/token",
        # From SCOPES, never from the stored grant. If the stored grant is
        # wider for any reason, this asks for less rather than inheriting more.
        scopes=list(gmail.SCOPES))
    return gmail.GmailClient(gmail.GoogleTransport(creds))


def main() -> int:
    ensure_venv("LMS_POLLMAIL_REEXEC", script=__file__)
    # Before anything reads a path. Without this the archive root came from the
    # code default (~/LMS/archive) unless the operator happened to have sourced
    # lms.env in that shell — so the same command filed to a different place
    # depending on who typed it, and the scheduled job would have filed
    # everything off the array entirely.
    load_lms_env()

    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("address")
    p.add_argument("--days", type=int, default=1)
    # 500, not 100. A 14-day catch-up on Sept 29 returned exactly 100 and
    # stopped at Sept 22 — a week of mail silently absent from a listing that
    # looked complete. The limit is a safety valve, not a page size.
    p.add_argument("--limit", type=int, default=500)
    p.add_argument("--query", default="",
                   help="extra Gmail search terms, ANDed with the window")
    p.add_argument("--once", action="store_true",
                   help="accepted for symmetry with the watchfolder; this "
                        "command is always a single pass")
    p.add_argument("--dry-run", action="store_true",
                   help="list what would be ingested; write nothing")
    args = p.parse_args()

    client = build_client(args.address)
    messages = client.recent(newer_than_days=args.days, limit=args.limit,
                             query=args.query)

    truncated = len(messages) >= args.limit
    if truncated:
        oldest = min((m.date or "")[:10] for m in messages) if messages else "?"
        print(f"WARNING: hit --limit {args.limit}. Only mail back to {oldest} "
              f"was fetched, not the full {args.days}d. Re-run with a larger "
              f"--limit; nothing older than {oldest} is in this pass.",
              flush=True)

    if args.dry_run:
        print(f"{len(messages)} message(s) in the last {args.days}d "
              f"for {args.address}\n")
        for m in messages:
            # real_attachments: signature logos are body content, and listing
            # them here is what hid the fact that they were about to be filed.
            atts = ", ".join(a.filename for a in m.real_attachments) or "—"
            print(f"  {(m.date or '')[:10]}  {m.sender[:38]:38}  "
                  f"{(m.subject or '(no subject)')[:44]:44}  {atts}")
        print("\nNothing was written. Drop --dry-run to file these.")
        return 0

    registry = load_registry()
    outstanding = registry.placeholders()
    if outstanding:
        print(f"WARNING: C16 unanswered for {', '.join(outstanding)} — mail "
              f"for these entities will file against placeholder legal names.",
              flush=True)

    problem = archive_volume_problem()
    if problem:
        # Before roots.ensure(): mkdir under a missing mount point fails with
        # a 30-line EACCES traceback that hides the one fact that matters.
        print(f"NOT FILING: {problem}", file=sys.stderr)
        return 1

    roots = filing.StorageRoots.from_env()
    roots.ensure()
    conn = db.connect(os.environ.get(
        "LMS_DB", str(roots.archive.parent / "lms.db")))
    classifier = Classifier(registry)

    # The spool holds the rendered message and any fetched attachment. It is
    # NOT the archive and NOT _originals — both of those are written by
    # filing.file_artifact from these bytes. Keeping it under the LMS root
    # rather than /tmp means a crash leaves the evidence where it can be found.
    spool = Path(os.environ.get(
        "LMS_MAIL_SPOOL", str(roots.archive.parent / "spool" / "mail")))

    db.log_action(conn, "MAIL_POLL_START",
                  detail=f"{args.address} newer_than:{args.days}d")
    if truncated:
        # Logged so the record shows this pass was partial. A scheduled run
        # that truncates is a missed-mail event, not a quiet success.
        db.log_action(conn, "MAIL_POLL_TRUNCATED",
                      detail=f"{args.address} limit={args.limit} "
                             f"newer_than:{args.days}d")

    counts = {}
    try:
        counts = file_messages(
            conn, messages,
            lambda msg: mail.ingest_message(conn, roots, registry, classifier,
                                            client, msg, spool=spool))
    finally:
        db.log_action(conn, "MAIL_POLL_STOP",
                      detail=_summary(len(messages), counts))
        conn.close()

    print(f"\n{_summary(len(messages), counts)}")
    # Non-zero so `launchctl list` shows it; the rest of the pass still filed.
    return 1 if counts.get("failed") else 0


def _summary(n: int, c: dict) -> str:
    s = (f"{n} message(s): {c.get('filed', 0)} filed, "
         f"{c.get('quarantined', 0)} to review, "
         f"{c.get('skipped', 0)} attachment(s) skipped")
    if c.get("failed"):
        s += f", {c['failed']} FAILED (see MAIL_MESSAGE_FAILED)"
    return s


def file_messages(conn, messages, ingest) -> dict:
    """Ingest each message; one that raises is logged and the pass goes on.

    Oct 2 2026: an encrypted PDF raised AttributeError, which ended the pass.
    The window re-reads the same mail every run, so the same message ended
    EVERY run, and nothing listed after it was ever filed. The failure is
    recorded, not swallowed: it lands in actions_log with the message id, and
    the exit status is non-zero.
    """
    c = {"filed": 0, "quarantined": 0, "skipped": 0, "failed": 0}
    for msg in messages:
        try:
            res = ingest(msg)
        except Exception as exc:
            c["failed"] += 1
            detail = (f"{msg.message_id} {msg.date[:10]} {msg.subject[:80]!r}: "
                      f"{type(exc).__name__}: {exc}")
            print(f"[FAILED] {detail}", file=sys.stderr, flush=True)
            db.log_action(conn, "MAIL_MESSAGE_FAILED", detail=detail[:400])
            continue
        for r in [res.email, *res.attachments]:
            if r is None:
                continue
            print(f"[{r.status}] {r.path}", flush=True)
            if r.status == "FILED":
                c["filed"] += 1
            elif r.status in {"QUARANTINED", "SUSPECTED_PHISHING"}:
                c["quarantined"] += 1
        for note in res.skipped:
            print(f"[SKIPPED] {note}", flush=True)
            c["skipped"] += 1
    return c


if __name__ == "__main__":
    raise SystemExit(main())
