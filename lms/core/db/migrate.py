"""Bring an existing database up to the current schema, in place.

schema.sql is all CREATE ... IF NOT EXISTS, which adds tables but cannot change
one that exists. Ledger v2 (D-066) changes `tasks`: twenty-odd new columns and
a new status vocabulary in a CHECK constraint, and SQLite cannot alter a CHECK.
So the table is rebuilt: renamed aside, created fresh from schema.sql, every
row copied across under the same id, the old one dropped. One transaction; if
anything fails, nothing changed.

`connect()` calls `upgrade()` before applying schema.sql, so whichever job
opens the database first after `git pull` migrates it, and the others find it
done. `ops/migrate_ledger.py` runs the same thing by hand and prints a report;
that is the step in ops/LEDGER_V2_APPLY.md.

Before touching a database file it takes a snapshot next to it,
`lms.db.pre-ledger-v2`, using the online backup API (see ops/backup.py for why
not a byte copy). Migrating a live ledger without a copy to go back to would be
a bet.

WHAT THE OLD ROWS BECOME
------------------------
    OPEN       -> TRIAGED   classified and ranked by ingest, nothing started
    DONE       -> CLOSED    final_outcome DONE; the human's close is the evidence
    DISMISSED  -> CANCELED  final_outcome DISMISSED (still the defect report)

Ids are kept: done.py closes by id, and anyone holding one from an old brief
must still close the task they meant.

Each row with a source document gets its event key and a task_sources row, so
evidence that arrives after the migration merges into it. When two old tasks
produce the same key, the older keeps it and the newer gets none; neither is
marked duplicate. The report lists the pairs for a human to look at
(`done.py <new> --duplicate-of <old>`), because a migration that quietly closes
someone's tasks is worse than one that leaves two of them open.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

LEDGER_V2 = 2

STATUS_V1_TO_V2 = {"OPEN": "TRIAGED", "DONE": "CLOSED", "DISMISSED": "CANCELED"}
OUTCOME_V1 = {"DONE": "DONE", "DISMISSED": "DISMISSED"}
V1_CLOSE_EVIDENCE = ("closed by a human with ops/done.py before ledger v2; "
                     "no further evidence was recorded then")


class MigrationError(RuntimeError):
    pass


@dataclass
class MigrationReport:
    migrated: bool
    rows: int = 0
    by_status: dict[str, int] = field(default_factory=dict)   # "OPEN -> TRIAGED": n
    keyed: int = 0
    collisions: list[tuple[int, int, str]] = field(default_factory=list)
    backup: Path | None = None

    def lines(self) -> list[str]:
        if not self.migrated:
            return ["Task ledger is already v2. Nothing to do."]
        out = [f"Task ledger migrated to v2: {self.rows} tasks, ids unchanged."]
        out += [f"  {k}: {n}" for k, n in sorted(self.by_status.items())]
        out.append(f"  {self.keyed} given an event key")
        if self.backup:
            out.append(f"  snapshot before migrating: {self.backup}")
        if self.collisions:
            out.append(f"  {len(self.collisions)} possible duplicate(s), left open for a human:")
            out += [f"    #{new} looks like #{old} ({key}) -> "
                    f"./ops/done.py {new} --duplicate-of {old}"
                    for old, new, key in self.collisions]
        return out


def schema_statements(sql: str) -> list[str]:
    """schema.sql as single statements, PRAGMAs left out.

    Split with sqlite3.complete_statement rather than on ';', which would cut
    a trigger body in half.
    """
    out, buf = [], ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            stmt = buf.strip()
            buf = ""
            body = "\n".join(l for l in stmt.splitlines()
                             if not l.strip().startswith("--")).strip()
            if body and not body.upper().startswith("PRAGMA"):
                out.append(stmt)
    return out


def needs_ledger_v2(conn: sqlite3.Connection) -> bool:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
    return bool(cols) and "event_key" not in cols


def snapshot(conn: sqlite3.Connection, db_path: Path) -> Path:
    dest = db_path.with_name(db_path.name + ".pre-ledger-v2")
    if dest.exists():
        return dest                      # the first snapshot is the one that matters
    out = sqlite3.connect(dest)
    try:
        conn.backup(out)
        ok = out.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        out.close()
    if ok != "ok":
        raise MigrationError(f"pre-migration snapshot failed its integrity check: {ok}")
    return dest


def upgrade(conn: sqlite3.Connection, db_path: str | Path | None = None, *,
            schema_sql: str | None = None) -> MigrationReport:
    """Migrate if needed. Safe to call on every connect, and from two processes."""
    if not needs_ledger_v2(conn):
        return MigrationReport(migrated=False)

    from .database import SCHEMA_PATH
    schema_sql = schema_sql or SCHEMA_PATH.read_text(encoding="utf-8")

    backup = None
    if db_path is not None and str(db_path) != ":memory:":
        backup = snapshot(conn, Path(db_path))

    conn.execute("PRAGMA foreign_keys = OFF")     # only takes effect outside a transaction
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if not needs_ledger_v2(conn):         # another process got there first
                conn.execute("ROLLBACK")
                return MigrationReport(migrated=False)
            report = _ledger_v2(conn, schema_sql)
            bad = (conn.execute("PRAGMA foreign_key_check(tasks)").fetchall()
                   + conn.execute("PRAGMA foreign_key_check(task_sources)").fetchall())
            if bad:
                raise MigrationError(f"foreign keys broken by the migration: "
                                     f"{[tuple(r) for r in bad[:5]]}")
            conn.execute(f"PRAGMA user_version = {LEDGER_V2}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    report.backup = backup
    return report


def _ledger_v2(conn: sqlite3.Connection, schema_sql: str) -> MigrationReport:
    from ..pipeline import ledger         # deferred: ledger imports database
    from . import database as db

    # Indexes on the old table would go with the rename and keep their names,
    # and schema.sql would then skip creating them on the new one. Drop them
    # all, not just v1's two: old code still running after `git pull` (the
    # haltbot stays up through a halt) reads the NEW schema.sql and can have
    # put some of v2's indexes on the v1 table.
    for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' "
            "AND tbl_name = 'tasks' AND sql IS NOT NULL").fetchall():
        conn.execute(f'DROP INDEX "{name}"')
    # The same old code can have created task_sources and friends, with foreign
    # keys to `tasks`. A modern rename rewrites those to follow the table to
    # `tasks_v1`, which is about to be dropped. The legacy rename leaves them
    # naming `tasks`: the table this creates.
    conn.execute("PRAGMA legacy_alter_table = ON")
    try:
        conn.execute("ALTER TABLE tasks RENAME TO tasks_v1")
    finally:
        conn.execute("PRAGMA legacy_alter_table = OFF")
    for stmt in schema_statements(schema_sql):
        conn.execute(stmt)

    has_cls = bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='classifications'"
    ).fetchone())
    cls_cols = ("c.domain AS c_domain, c.confidence AS c_conf, "
                "c.counterparty AS c_cp, c.descriptor AS c_desc, "
                "c.amount_cents AS c_amt") if has_cls else (
                "NULL AS c_domain, NULL AS c_conf, NULL AS c_cp, "
                "NULL AS c_desc, NULL AS c_amt")
    cls_join = ("LEFT JOIN classifications c ON c.id = (SELECT MAX(id) FROM "
                "classifications WHERE artifact_id = t.source_artifact)"
                if has_cls else "")
    rows = conn.execute(f"""
        SELECT t.*, a.source AS a_source, a.source_ref AS a_ref,
               a.sha256 AS a_sha, a.ingested_at AS a_ingested, {cls_cols}
          FROM tasks_v1 t
          LEFT JOIN artifacts a ON a.id = t.source_artifact
          {cls_join}
         ORDER BY t.id""").fetchall()

    report = MigrationReport(migrated=True, rows=len(rows))
    keys: dict[str, int] = {}
    now = db.now_iso()

    for r in rows:
        old = r["status"]
        new = STATUS_V1_TO_V2.get(old)
        if new is None:
            raise MigrationError(f"task {r['id']} has status {old!r}, which v1 "
                                 f"never allowed; stopping rather than guessing")
        label = f"{old} -> {new}"
        report.by_status[label] = report.by_status.get(label, 0) + 1

        key = ledger.event_key(entity_id=r["entity_id"], counterparty=r["c_cp"],
                               due_date=r["due_date"], amount_cents=r["c_amt"],
                               descriptor=r["c_desc"])
        if key and key in keys:
            report.collisions.append((keys[key], int(r["id"]), key))
            key = None
        elif key:
            keys[key] = int(r["id"])
            report.keyed += 1

        conn.execute(
            "INSERT INTO tasks (id, event_key, source, source_link, source_artifact, "
            "received_at, owner, entity_id, domain, person, title, detail, "
            "extraction_confidence, urgency, due_date, status, status_changed_at, "
            "verification_evidence, final_outcome, mirrored_reminder, created_at, "
            "completed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (r["id"], key, r["a_source"] or "manual", r["a_ref"], r["source_artifact"],
             r["a_ingested"] or r["created_at"], ledger.OWNER_MATTHEW, r["entity_id"],
             r["c_domain"], r["person"], r["title"], r["detail"], r["c_conf"],
             r["urgency"] or "NORMAL", r["due_date"], new,
             r["completed_at"] or r["created_at"],
             V1_CLOSE_EVIDENCE if old == "DONE" else None, OUTCOME_V1.get(old),
             r["mirrored_reminder"], r["created_at"], r["completed_at"]))

        if r["a_sha"]:
            conn.execute(
                "INSERT OR IGNORE INTO task_sources (task_id, source_key, source, "
                "source_link, artifact_id, received_at, confidence, seen_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (r["id"], f"artifact:{r['a_sha']}", r["a_source"] or "manual",
                 r["a_ref"], r["source_artifact"], r["a_ingested"], r["c_conf"],
                 r["created_at"]))

        conn.execute(
            "INSERT INTO task_events (task_id, ts, actor, event, from_status, "
            "to_status, detail) VALUES (?,?,?,?,?,?,?)",
            (r["id"], now, "migrate", "MIGRATED", old, new,
             "ledger v2 (D-066)" + (f" · key {key}" if key else "")))

    # In the trail as well as the report: a job that opened the database first
    # migrates it without printing anything, and the pairs must not be lost.
    for old_id, new_id, key in report.collisions:
        conn.execute(
            "INSERT INTO task_events (task_id, ts, actor, event, detail) "
            "VALUES (?,?,?,?,?)",
            (new_id, now, "migrate", "POSSIBLE_DUPLICATE",
             f"same event key as #{old_id} ({key}); left open for a human"))

    conn.execute("DROP TABLE tasks_v1")
    conn.execute(
        "INSERT INTO actions_log (ts, action, detail) VALUES (?, ?, ?)",
        (now, "TASK_LEDGER_V2",
         f"{report.rows} tasks; " + "; ".join(f"{k}: {n}" for k, n in
                                              sorted(report.by_status.items()))
         + f"; {len(report.collisions)} possible duplicates"))
    return report
