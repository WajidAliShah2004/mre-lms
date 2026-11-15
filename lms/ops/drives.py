#!/usr/bin/env python3
"""Read the drives, every 3 hours (R5, D-083).

    ./ops/drives.py                                  # a pass of every active drive (com.lms.drives)
    ./ops/drives.py --source storage:icloud-drive    # a pass of one drive, active or not
    ./ops/drives.py --source storage:icloud-drive --probe    # counts only; writes nothing
    ./ops/drives.py --source storage:icloud-drive --dry-run  # what would be filed; writes nothing

Lists every file in each drive and files the documents created or changed
since the drive's start date (the day of its first pass) once they have
been unchanged for 24 hours. Never writes, moves, renames or deletes
anything in a drive. Older documents: ops/backfill_drives.py. Setup:
ops/DRIVES_APPLY.md. Engine: core/pipeline/drives.py.

The probe and the dry run print counts only, never a file or folder name.
Halted (D-059), it reads nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _halt                                                # noqa: E402
from _env import archive_volume_problem, load_lms_env      # noqa: E402
from _reexec import ensure_venv                            # noqa: E402
from core.adapters.drive import DriveError                 # noqa: E402

ICLOUD = "storage:icloud-drive"
ARCHIVE = "storage:icloud-archive"
GDRIVE = "storage:gdrive:"
DROPBOX = "storage:dropbox:"


def keychain_json(service: str) -> dict | None:
    r = subprocess.run(["security", "find-generic-password", "-s", service, "-w"],
                       capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        return json.loads(r.stdout.rstrip("\n"))
    except ValueError:
        return None


def has_reader(source_id: str) -> bool:
    return (source_id in (ICLOUD, ARCHIVE) or source_id.startswith(GDRIVE)
            or source_id.startswith(DROPBOX))


def build_source(source_id: str):
    from core.adapters import dropbox, gdrive, icloud_drive
    from core.pipeline.drives import DriveSource

    if source_id == ICLOUD:
        d = icloud_drive.ICloudDrive(source_id, icloud_drive.icloud_roots())
        return DriveSource(source_id, "iCloud Drive", d, d)
    if source_id == ARCHIVE:
        d = icloud_drive.ICloudDrive(source_id, icloud_drive.archive_roots())
        return DriveSource(source_id, "iCloud Drive (saved copy)", d, d,
                           files_documents=False)
    if source_id.startswith(GDRIVE):
        address = source_id[len(GDRIVE):]
        g = keychain_json(gdrive.keychain_service(address))
        if not g:
            raise DriveError(f"no Drive grant for {address}; authorise it once: "
                             f"./ops/authorise_gdrive.py {address}")
        from google.oauth2.credentials import Credentials
        creds = Credentials(token=None, refresh_token=g["refresh_token"],
                            client_id=g["client_id"], client_secret=g["client_secret"],
                            token_uri="https://oauth2.googleapis.com/token",
                            scopes=list(gdrive.SCOPES))
        d = gdrive.GDrive(source_id, address, gdrive.GoogleDriveAPI(creds))
        return DriveSource(source_id, f"Google Drive {address}", d, d)
    if source_id.startswith(DROPBOX):
        address = source_id[len(DROPBOX):]
        g = keychain_json(dropbox.keychain_service(address))
        if not g:
            raise DriveError(f"no Dropbox grant for {address}; authorise it once: "
                             f"./ops/authorise_dropbox.py {address} --app-key KEY")
        d = dropbox.Dropbox(source_id, address,
                            dropbox.DropboxAPI(g["app_key"], g["refresh_token"]))
        return DriveSource(source_id, "Dropbox", d, d)
    raise DriveError(f"no drive reader for {source_id}")


def scheduled_sources(register) -> list[str]:
    """The drives the 3-hourly job reads: active, with a cadence."""
    return [s.id for s in register
            if s.id.startswith("storage:") and s.status == "active"
            and s.cadence_hours is not None]


def _kind(f) -> str:
    return "google file" if f.native else (Path(f.name).suffix.lower().lstrip(".") or "none")


def do_probe(src) -> int:
    from core.pipeline import drives
    from core.reports.storage import fmt_bytes
    try:
        files = src.lister.list(None).files
    except DriveError as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 1
    sized = [f.size for f in files if f.size is not None]
    docs = Counter(_kind(f) for f in files if f.is_document)
    newest = max((drives.when(f.modified) for f in files), default=None)
    print(f"{src.source_id}: {len(files)} file(s), {fmt_bytes(sum(sized))} "
          f"in the {len(sized)} with a known size")
    print("by area: " + (", ".join(f"{a} {n}" for a, n in
                                   sorted(Counter(f.area for f in files).items())) or "none"))
    print("documents: " + (", ".join(f"{k} {n}" for k, n in docs.most_common()) or "none"))
    print(f"other files (indexed, not filed): {sum(1 for f in files if not f.is_document)}")
    print(f"over 100 MB: {sum(1 for f in files if f.size and f.size > drives.MAX_BYTES)}")
    print(f"newest change: {newest:%Y-%m-%d %H:%M}" if newest else "newest change: none")
    return 0


def do_dry_run(src, conn, now: datetime) -> int:
    from core.pipeline import drives
    try:
        would, big, listed = drives.plan(src, conn, now)
    except DriveError as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 1
    kinds = Counter(_kind(f) for f in would)
    detail = f" ({', '.join(f'{k} {n}' for k, n in kinds.most_common())})" if would else ""
    print(f"{src.source_id}: {listed} file(s) listed; {len(would)} document(s) would be "
          f"filed now{detail}; {big} over 100 MB would not be.")
    print("Nothing was written.")
    return 0


def _halt_requested() -> bool:
    return _halt.read_flag() is not None


def run(conn, roots, registry, classifier, sids: list[str], now: datetime, *,
        build=build_source, halted=_halt_requested) -> int:
    """A pass of each drive in turn. One broken drive does not stop the rest."""
    from core.db import database as db
    from core.pipeline import coverage, drives
    bad = 0
    failed: list[str] = []
    for sid in sids:
        if halted():
            print("HALTED: stopped between drives.")
            break
        try:
            src = build(sid)
        except (DriveError, ImportError, KeyError) as exc:
            started = db.now_iso()
            coverage.record_check(conn, sid, "FAILED", started_at=started, detail=str(exc))
            db.log_action(conn, drives.FAILED, detail=f"{sid}: {exc}"[:400])
            conn.commit()
            print(f"{sid}: FAIL  {exc}")
            bad += 1
            failed.append(sid)
            continue
        r = drives.run_pass(src, conn, roots, registry, classifier, now=now,
                            tmp_root=roots.archive.parent / "tmp" / "drives", halted=halted)
        print(f"{sid}: {r.summary()}")
        if r.error or r.failed:
            bad += 1
        if r.error:
            failed.append(sid)
    if failed:
        # The brief reads only the newest row of the pair, so a failure must be
        # the last one written, whatever drive ran after it.
        db.log_action(conn, drives.FAILED,
                      detail=f"{len(failed)} drive(s) failed this run: {', '.join(failed)}"[:400])
        conn.commit()
    return 1 if bad else 0


def main() -> int:
    ensure_venv("LMS_DRIVES_REEXEC", script=__file__)
    load_lms_env()
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", action="append", default=[],
                   help="a storage:* id from config/sources.yaml (repeatable)")
    p.add_argument("--probe", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    if (args.probe or args.dry_run) and not args.source:
        p.error("--probe and --dry-run need --source")

    flag = _halt.read_flag()
    if flag is not None:
        print(f"HALTED since {flag.get('ts')} (by {flag.get('by')}): nothing read.")
        return 0

    if args.probe:
        rc = 0
        for sid in args.source:
            try:
                rc |= do_probe(build_source(sid))
            except DriveError as exc:
                print(f"FAIL  {exc}", file=sys.stderr)
                rc = 1
        return rc

    problem = archive_volume_problem()
    if problem:
        print(f"archive unavailable: {problem}", file=sys.stderr)
        return 1

    from core.db import database as db
    from core.pipeline import coverage, filing
    from core.pipeline.classify import Classifier
    from core.pipeline.registry import load_registry

    roots = filing.StorageRoots.from_env()
    roots.ensure()
    conn = db.connect(os.environ.get("LMS_DB", str(roots.archive.parent / "lms.db")))
    now = datetime.now().astimezone()
    try:
        if args.dry_run:
            rc = 0
            for sid in args.source:
                try:
                    rc |= do_dry_run(build_source(sid), conn, now)
                except DriveError as exc:
                    print(f"FAIL  {exc}", file=sys.stderr)
                    rc = 1
            return rc
        sids = args.source or scheduled_sources(coverage.load_register())
        if not sids:
            print("No drive is switched on (status: active in config/sources.yaml).")
            return 0
        registry = load_registry()
        return run(conn, roots, registry, Classifier(registry), sids, now)
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
