#!/usr/bin/env python3
"""Restore the latest backup and prove it is usable — spec §Day-6.4.

    "Test a restore now, not later."

An untested backup is not a backup, it is a belief about a directory. This
restores the most recent snapshot into a temporary directory and then checks
the things that actually decide whether a bad morning is survivable:

  * the database opens at all
  * it passes PRAGMA integrity_check
  * its row counts match the live database, table by table
  * the sidecars came back, and the count matches the archive
  * config/entities.yaml is present and parses — it IS the classifier, and a
    restore without it can hold documents but cannot file another one

Nothing is written outside the temp directory, and the live archive is opened
read-only throughout. Running this must never be able to make things worse,
because it will be run when things are already going badly.

    ./ops/restore_test.py             # restore latest, verify, report
    ./ops/restore_test.py --keep      # leave the restored tree for inspection
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env import load_lms_env
from _reexec import ensure_venv                            # noqa: E402
from backup import restic_password, row_counts            # noqa: E402

GREEN, RED, YELLOW, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[0m"

failures: list[str] = []


def ok(msg: str) -> None:
    print(f"  {GREEN}PASS{RESET}  {msg}")


def fail(msg: str) -> None:
    failures.append(msg)
    print(f"  {RED}FAIL{RESET}  {msg}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}WARN{RESET}  {msg}")


# ---------------------------------------------------------------------------
# The verification, separated from restic so it can be tested without it
# ---------------------------------------------------------------------------

def find_restored(root: Path, name: str) -> Path | None:
    """restic restores absolute paths, so the tree is nested under the temp
    dir by its original location. Search rather than guess at the depth."""
    matches = sorted(root.rglob(name))
    return matches[0] if matches else None


def read_manifest(restored: Path) -> dict | None:
    """What the snapshot says about itself, if it said anything.

    Absent for repositories written before manifests existed, so every caller
    falls back to the live comparison rather than failing on its absence — a
    restore test that refuses to run on an old snapshot is useless precisely
    when an old snapshot is all there is.
    """
    path = find_restored(restored, "manifest.json")
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        warn(f"manifest.json is present but unreadable: {exc}")
        return None


def verify_restore(restored: Path, live_counts: dict[str, int] | None,
                   live_sidecars: int | None) -> None:
    """Check a restored tree. Every failure appends to `failures`."""
    db = find_restored(restored, "lms.db")
    if db is None:
        fail("no lms.db in the restored snapshot — there is no catalogue")
        return
    ok(f"database restored: {db.name}")

    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        fail(f"the restored database will not open: {exc}")
        return

    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
    except sqlite3.DatabaseError as exc:
        fail(f"the restored file is not a database: {exc}")
        conn.close()
        return
    finally:
        conn.close()

    if result == "ok":
        ok("integrity_check: ok")
    else:
        fail(f"integrity_check: {result}")
        return

    restored_counts = row_counts(db)

    # An empty database passes integrity_check perfectly. That is exactly what
    # a naive `cp` of a WAL-mode database produces — the rows are all in the
    # -wal file, the copy has none of them, and it opens without complaint.
    # Measured: 500 committed rows in, 0 rows out. Counting is the only check
    # that catches it.
    if sum(restored_counts.values()) == 0:
        fail("the restored database is EMPTY — it opens and contains nothing. "
             "This is what a file copy of a live WAL database looks like.")
        return

    if live_counts is None:
        warn("no live database to compare against; counts not verified")
        print("        " + ", ".join(f"{t}={n}" for t, n in restored_counts.items() if n))
    else:
        drift = {t: (live_counts.get(t, 0), n)
                 for t, n in restored_counts.items()
                 if live_counts.get(t, 0) != n}
        # The live database may legitimately have grown since the snapshot.
        # Fewer rows is expected; MORE rows in the backup than in the live
        # database means something was lost from the original, which is a
        # different and much more interesting problem.
        gained = {t: v for t, v in drift.items() if v[1] > v[0]}
        if gained:
            fail(f"the backup has rows the live database does not: {gained}")
        elif drift:
            warn(f"live database has grown since the snapshot: {drift}")
        else:
            ok(f"row counts match the live database ({sum(restored_counts.values())} rows)")

    # Sidecars, checked against the snapshot's own manifest where possible.
    #
    # Comparing to the LIVE archive is comparing to a moving target: a document
    # arriving between the backup and this check makes a perfectly good backup
    # look short, and the only way to tolerate that is a >= comparison loose
    # enough to miss real loss. The first version did exactly that and reported
    # "4 sidecar(s) restored (archive has 2)" as a PASS — arithmetic, not
    # evidence.
    #
    # A snapshot that states its own contents can be checked exactly.
    sidecars = {p.resolve() for p in restored.rglob("*.meta.json")}
    manifest = read_manifest(restored)

    if manifest is not None and "sidecars" in manifest:
        claimed = int(manifest["sidecars"])
        if len(sidecars) == claimed:
            ok(f"{len(sidecars)} sidecar(s), exactly what the snapshot claims")
        elif len(sidecars) > claimed:
            # Only expected with --documents, where each sidecar is inside the
            # archive tree; a duplicate is harmless but should be explained.
            warn(f"{len(sidecars)} sidecars restored, manifest claims {claimed} "
                 f"— duplicated between the archive tree and the staged copy")
        else:
            fail(f"the snapshot claims {claimed} sidecars and only "
                 f"{len(sidecars)} came back")
    elif live_sidecars is None:
        print(f"        {len(sidecars)} sidecar(s) restored")
    elif len(sidecars) >= live_sidecars:
        ok(f"{len(sidecars)} sidecar(s) restored (archive has {live_sidecars})")
    else:
        # No manifest, so this snapshot predates them. Fewer than live is
        # ambiguous — growth or loss — and saying so is more useful than
        # picking one.
        warn(f"{len(sidecars)} restored, archive has {live_sidecars}. Without a "
             f"manifest this cannot distinguish loss from documents filed "
             f"since the snapshot. Take a fresh backup and re-run.")

    # Say what KIND of snapshot this was.
    #
    # `restore latest` takes whatever ran most recently. Until Sept 10 the
    # nightly job ran WITHOUT documents, so the newest snapshot was normally
    # the catalogue alone — and "Restore verified, the backup is usable" would
    # read as "the documents are safe", which for that snapshot was false.
    #
    # Documents are now the default, so this line should say so every night. A
    # CATALOGUE ONLY warning from the scheduled job means someone has added
    # --catalogue-only to the plist, and that is worth noticing.
    if manifest is not None and "documents_included" in manifest:
        if manifest["documents_included"]:
            ok("this snapshot includes the filed documents")
        else:
            warn("this snapshot is the CATALOGUE ONLY — no filed documents. "
                 "Restoring it gives a perfect index of files it cannot "
                 "produce. Documents are included by default; something "
                 "passed --catalogue-only.")

    entities = find_restored(restored, "entities.yaml")
    if entities is None:
        fail("config/entities.yaml is missing — a restore without it can hold "
             "documents but cannot correctly file another one")
    else:
        try:
            import yaml
            data = yaml.safe_load(entities.read_text(encoding="utf-8"))
            n = len(data.get("businesses", {})) + len(data.get("people", {}))
            ok(f"entities.yaml parses, {n} entities")
        except Exception as exc:
            fail(f"entities.yaml did not parse: {exc}")


# ---------------------------------------------------------------------------

def main() -> int:
    ensure_venv("LMS_RESTORE_REEXEC", script=__file__)
    load_lms_env()

    p = argparse.ArgumentParser()
    p.add_argument("--repo", default=None)
    p.add_argument("--keep", action="store_true",
                   help="do not delete the restored tree")
    args = p.parse_args()

    archive = os.environ.get("LMS_ARCHIVE_ROOT")
    if not archive:
        print("LMS_ARCHIVE_ROOT is not set — run: source ops/lms.env", file=sys.stderr)
        return 2
    archive = Path(archive)
    repo = Path(args.repo or os.environ.get(
        "LMS_BACKUP_REPO", archive.parent / "LMS_backup"))

    print("LMS restore verification")
    print("=" * 60)
    print(f"repo: {repo}")

    if shutil.which("restic") is None:
        print("\nrestic is not installed. `brew install restic`", file=sys.stderr)
        return 2
    if not (repo / "config").exists():
        print(f"\nno restic repository at {repo} — run ./ops/backup.py first",
              file=sys.stderr)
        return 2

    try:
        password = restic_password()
    except Exception as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 2

    live_db = Path(os.environ.get("LMS_DB", archive.parent / "lms.db"))
    live_counts = row_counts(live_db) if live_db.exists() else None
    live_sidecars = len(list(archive.rglob("*.meta.json"))) if archive.exists() else None

    dest = Path(tempfile.mkdtemp(prefix="lms-restore-"))
    print(f"restoring into {dest}\n")
    try:
        r = subprocess.run(
            ["restic", "-r", str(repo), "restore", "latest", "--target", str(dest)],
            env={**os.environ, "RESTIC_PASSWORD": password},
            capture_output=True, text=True)
        if r.returncode != 0:
            print(f"restic restore failed: {r.stderr.strip()}", file=sys.stderr)
            return 1

        verify_restore(dest, live_counts, live_sidecars)

        print("\n" + "=" * 60)
        if failures:
            print(f"{RED}{len(failures)} FAILED{RESET} — this backup would not save you:")
            for f in failures:
                print(f"  - {f}")
            return 1
        print(f"{GREEN}Restore verified{RESET} — the backup is usable.")
        return 0
    finally:
        if args.keep:
            print(f"\nrestored tree kept at {dest}")
        else:
            shutil.rmtree(dest, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
