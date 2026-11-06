#!/usr/bin/env python3
"""Read WhatsApp, hourly (R40, D-079).

    ./ops/whatsapp.py --probe      # the database's shape, no content; writes nothing

Reads the WhatsApp Mac app's database from a copy; never writes to WhatsApp,
never sends. See core/adapters/whatsapp.py and ops/WHATSAPP_APPLY.md.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _env import load_lms_env                              # noqa: E402
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


def main() -> int:
    ensure_venv("LMS_WHATSAPP_REEXEC", script=__file__)
    load_lms_env()
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--probe", action="store_true")
    args = p.parse_args()
    if args.probe:
        return do_probe(wa.CONTAINER)
    p.error("only --probe exists so far")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
