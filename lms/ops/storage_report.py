#!/usr/bin/env python3
"""Storage and dedup reports (R25, R28; addendum §4, Phase 5).

    ./ops/storage_report.py                  # both reports
    ./ops/storage_report.py --storage        # original / copied / saved / reclaimable
    ./ops/storage_report.py --dedup          # exact duplicates + near-duplicate candidates
    ./ops/storage_report.py --verify 20      # also re-hash 20 random filed artifacts
    ./ops/storage_report.py --no-hash        # skip hashing source folders (faster)

READ-ONLY. The database is opened with mode=ro, files are opened to read and
hash, and nothing is moved, merged, marked or removed. "Reclaimable" is a
number for a person to look at: any cleanup is a separately approved action,
and deletion is policy level D, a hard stop (D-067).

A number that cannot be measured is printed as NOT MEASURED with the reason
— Gmail's storage of a message, for one, is invisible to a read-only grant.
It is never estimated (core/reports/storage.py says why).

Near-duplicate candidates are TEXT candidates: a pair is a NEAR_DUPLICATE only
when the texts match and nothing in them disagrees — no different statement
period, amount, account, invoice number, signature or version marker. A pair
that disagrees on any of those is a RELATED_VERSION and both are kept
(core/pipeline/neardup.py). Visual comparison is a hook with no backend yet;
documents with no extracted text are listed as not compared.

--verify is the Phase 5 acceptance check "sample hash matches": it re-hashes
the archive copy and the _originals copy of N random filed artifacts and
compares sha256 and size with the database.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _env import archive_volume_problem, load_lms_env     # noqa: E402
from _reexec import ensure_venv                             # noqa: E402
from core.pipeline import filing, neardup                   # noqa: E402
from core.reports import storage                            # noqa: E402


def default_spool(roots: filing.StorageRoots) -> Path:
    """The same default poll_mail.py uses, so the two agree without lms.env."""
    return Path(os.environ.get(
        "LMS_MAIL_SPOOL", str(roots.archive.parent / "spool" / "mail")))


def default_inbox() -> Path | None:
    raw = os.environ.get("LMS_INBOX")
    return Path(raw).expanduser() if raw else None


def run(conn, roots: filing.StorageRoots, *, inbox: Path | None, spool: Path | None,
        storage_section: bool = True, dedup_section: bool = True,
        verify: int = 0, hash_sources: bool = True, limit: int = 25,
        seed: int | None = None) -> str:
    """Every requested section, as one plain-text report."""
    parts = []
    if storage_section:
        report = storage.build_report(conn, roots, inbox=inbox, spool=spool,
                                      hash_sources=hash_sources)
        parts.append(storage.render_storage(report))
    if dedup_section:
        near = neardup.find_candidates(neardup.load_docs(conn))
        parts.append(storage.render_dedup(storage.exact_dedup(conn), near,
                                          limit=limit))
    if verify:
        parts.append(storage.render_samples(
            storage.verify_sample(conn, roots, verify, seed=seed)))
    return "\n".join(parts)


def main() -> int:
    ensure_venv("LMS_STORAGE_REPORT_REEXEC", script=__file__)
    load_lms_env()

    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--storage", action="store_true", help="storage report only")
    p.add_argument("--dedup", action="store_true", help="dedup report only")
    p.add_argument("--verify", type=int, default=0, metavar="N",
                   help="re-hash N random filed artifacts (archive + _originals)")
    p.add_argument("--no-hash", action="store_true",
                   help="do not hash source folders; reclaimable bytes are then "
                        "reported as not measured")
    p.add_argument("--limit", type=int, default=25,
                   help="rows shown per list (default 25)")
    args = p.parse_args()
    both = not args.storage and not args.dedup

    problem = archive_volume_problem()
    if problem:
        print(f"NO REPORT: {problem}", file=sys.stderr)
        return 1

    roots = filing.StorageRoots.from_env()
    db_path = Path(os.environ.get("LMS_DB", str(roots.archive.parent / "lms.db")))
    if not db_path.exists():
        print(f"No database at {db_path} — nothing has been ingested here.",
              file=sys.stderr)
        return 1

    conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        print(run(conn, roots, inbox=default_inbox(), spool=default_spool(roots),
                  storage_section=both or args.storage,
                  dedup_section=both or args.dedup,
                  verify=args.verify, hash_sources=not args.no_hash,
                  limit=args.limit), end="")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
