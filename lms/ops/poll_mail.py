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

    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("address")
    p.add_argument("--days", type=int, default=1)
    p.add_argument("--limit", type=int, default=100)
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

    if args.dry_run:
        print(f"{len(messages)} message(s) in the last {args.days}d "
              f"for {args.address}\n")
        for m in messages:
            atts = ", ".join(a.filename for a in m.attachments) or "—"
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

    filed = quarantined = skipped = 0
    try:
        for msg in messages:
            res = mail.ingest_message(conn, roots, registry, classifier,
                                      client, msg, spool=spool)
            for r in [res.email, *res.attachments]:
                if r is None:
                    continue
                print(f"[{r.status}] {r.path}", flush=True)
                if r.status == "FILED":
                    filed += 1
                elif r.status in {"QUARANTINED", "SUSPECTED_PHISHING"}:
                    quarantined += 1
            for note in res.skipped:
                print(f"[SKIPPED] {note}", flush=True)
                skipped += 1
    finally:
        db.log_action(
            conn, "MAIL_POLL_STOP",
            detail=f"{len(messages)} message(s): {filed} filed, "
                   f"{quarantined} to review, {skipped} attachment(s) skipped")
        conn.close()

    print(f"\n{len(messages)} message(s): {filed} filed, "
          f"{quarantined} to review, {skipped} attachment(s) skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
