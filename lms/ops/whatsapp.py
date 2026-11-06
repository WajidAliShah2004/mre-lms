#!/usr/bin/env python3
"""Read WhatsApp, hourly (R40, D-079).

    ./ops/whatsapp.py              # one pass: what com.lms.whatsapp runs hourly
    ./ops/whatsapp.py --probe      # the database's shape, no content; writes nothing
    ./ops/whatsapp.py --dry-run    # unfiled chat days, open or closed; writes nothing

Reads the WhatsApp Mac app's database from a copy; never writes to WhatsApp,
never sends. Each pass: hourly urgency check on today's and yesterday's new
messages (alerts through D-077), then every closed chat day filed as one
transcript. See core/pipeline/whatsapp.py and ops/WHATSAPP_APPLY.md.

Halted (D-059), it reads nothing: com.lms.whatsapp is disabled with every
other job, and run by hand it stops at the flag.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _halt                                                # noqa: E402
from _env import archive_volume_problem, load_lms_env      # noqa: E402
from _reexec import ensure_venv                            # noqa: E402
from core.adapters import whatsapp as wa                   # noqa: E402


def do_probe(src_dir: Path) -> int:
    tmp = Path(tempfile.mkdtemp(prefix="wa-probe-"))
    try:
        copy = wa.snapshot(src_dir, tmp)
        conn = sqlite3.connect(copy)
        try:
            p = wa.probe(conn)
        finally:
            conn.close()
        media = Path(src_dir) / wa.MEDIA_DIR
        print(f"database: {src_dir / wa.DB_NAME} (copied, checked)")
        print("tables: " + ", ".join(p["tables"]))
        for table, n in p["counts"].items():
            print(f"{table}: {n}")
        print("message types: " + ", ".join(
            f"{t}={n} ({wa.KINDS.get(t, 'other')})"
            for t, n in p["message_types"].items()))
        def tally(d: dict) -> str:
            return ", ".join(f"{k} {n}" for k, n in
                             sorted(d.items(), key=lambda kv: -kv[1])[:5])
        print("session types: " + ", ".join(
            f"{t}={n} ({g} groups; {tally(p['session_domains'].get(t, {}))})"
            for t, (n, g) in p["session_types"].items())
            + f"; the reader takes {wa.GROUP_SESSION} as group")
        for t, (x, m) in p["type_shape"].items():
            files = p["media_ext"].get(t)
            print(f"  type {t}={p['message_types'][t]} ({wa.KINDS.get(t, 'other')}), "
                  f"with text {x}, with media {m}"
                  + (f"; files: {tally(files)}" if files else ""))
        print("media item columns filled: " + ", ".join(
            f"{c} {n}" for c, n in p["media_columns"].items() if n))
        print(f"media folder readable: {media.is_dir()}")
        if media.is_dir():
            files = wa.media_files(media)
            print(f"media folder files: {sum(files.values())}"
                  + (f" ({tally(files)})" if files else ""))
            conn = sqlite3.connect(copy)
            try:
                found, filled = wa.local_paths_found(conn, media)
            finally:
                conn.close()
            print(f"local paths the reader can open: {found} of {filled}")
        for m in p["missing"]:
            print(f"MISSING: {m}")
        return 1 if p["missing"] else 0
    except (wa.WhatsAppError, OSError) as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def do_dry_run(conn, now: datetime) -> int:
    from core.pipeline import whatsapp as w
    tmp = Path(tempfile.mkdtemp(prefix="wa-dry-"))
    try:
        chats = w.read_chats(wa.CONTAINER, tmp, now - timedelta(days=3))
    except wa.WhatsAppError as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    rows = w.plan_days(conn, chats, now)
    for label, day, n, is_closed in rows:
        print(f"  {day}  {'closed' if is_closed else 'open  '}  {n:4d}  {label}")
    print(f"{len(rows)} chat day(s) unfiled. Nothing was written.")
    return 0


def main() -> int:
    ensure_venv("LMS_WHATSAPP_REEXEC", script=__file__)
    load_lms_env()
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--probe", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    if args.probe:
        return do_probe(wa.CONTAINER)

    flag = _halt.read_flag()
    if flag is not None:
        print(f"HALTED since {flag.get('ts')} (by {flag.get('by')}): nothing read.")
        return 0

    problem = archive_volume_problem()
    if problem:
        print(f"archive unavailable: {problem}", file=sys.stderr)
        return 1

    from core.db import database as db
    from core.pipeline import filing
    from core.pipeline import whatsapp as w
    from core.pipeline.classify import Classifier
    from core.pipeline.registry import load_registry

    roots = filing.StorageRoots.from_env()
    roots.ensure()
    conn = db.connect(os.environ.get("LMS_DB", str(roots.archive.parent / "lms.db")))
    now = datetime.now().astimezone()
    try:
        if args.dry_run:
            return do_dry_run(conn, now)
        registry = load_registry()
        result = w.run_pass(conn, roots, registry, Classifier(registry),
                            src_dir=wa.CONTAINER,
                            tmp_root=roots.archive.parent / "tmp" / "whatsapp",
                            now=now)
    finally:
        conn.close()
    print(result.summary())
    return 1 if result.result == "FAILED" or result.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
