#!/usr/bin/env python3
"""Dump what the last few ingests actually recorded.

    .venv/bin/python ops/why.py

The watcher prints one word — FILED or QUARANTINED. Everything that explains
it is in the database and in the quarantine sidecars. This reads both, so
diagnosing a bad filing does not require remembering the schema or typing
sqlite3 quoting over a terminal.

Read-only. Opens the DB, prints, closes.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.pipeline import filing  # noqa: E402


def rule(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def table(conn, sql: str, args=()) -> list[sqlite3.Row]:
    try:
        return list(conn.execute(sql, args))
    except sqlite3.Error as exc:
        print(f"  ({exc})")
        return []


def main() -> int:
    roots = filing.StorageRoots.from_env()
    db_path = os.environ.get("LMS_DB", str(roots.archive.parent / "lms.db"))
    print(f"db:         {db_path}")
    print(f"archive:    {roots.archive}")
    print(f"quarantine: {roots.quarantine}")

    if not Path(db_path).exists():
        print("\nNo database yet — nothing has been ingested on this machine.")
        return 1

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    rule("Last 10 actions")
    for r in table(conn, "SELECT ts, action, detail FROM actions_log "
                         "ORDER BY id DESC LIMIT 10"):
        detail = (r["detail"] or "")[:180]
        print(f"  {r['ts']}  {r['action']:<18} {detail}")

    rule("Artifacts")
    for r in table(conn, "SELECT id, substr(sha256,1,8) AS h, source, status, "
                         "original_name FROM artifacts ORDER BY id DESC LIMIT 10"):
        print(f"  #{r['id']:<4} {r['h']}  {r['source']:<8} {r['status']:<12} "
              f"{r['original_name']}")

    rule("Classifications — the rationale column is why")
    for r in table(conn, "SELECT entity_id, category, subcategory, confidence, "
                         "decided_by, model, rationale FROM classifications "
                         "ORDER BY rowid DESC LIMIT 10"):
        flag = "  <-- QUARANTINED" if r["entity_id"] == "UNASSIGNED" else ""
        print(f"  {r['entity_id']:<12} {r['category']}/{r['subcategory']}  "
              f"conf={r['confidence']:.2f}  by={r['decided_by']}  "
              f"model={r['model']}{flag}")
        print(f"      {r['rationale']}")

    conn.close()

    rule("Quarantine folder")
    q = Path(roots.quarantine)
    if not q.exists():
        print("  (does not exist)")
        return 0
    files = sorted(p for p in q.iterdir() if p.is_file())
    if not files:
        print("  empty — nothing is waiting for review")
    for p in files:
        print(f"  {p.name}  ({p.stat().st_size} bytes)")
        if p.suffix == ".json":
            try:
                print("      " + json.dumps(json.loads(p.read_text()),
                                            indent=2)[:800].replace("\n", "\n      "))
            except Exception as exc:
                print(f"      (unreadable: {exc})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
