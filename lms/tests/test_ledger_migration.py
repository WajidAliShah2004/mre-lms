"""Migrating a v1 task table to ledger v2, in place (D-066).

The live database on the Mac has real tasks, some open, some closed, closed by
id from briefs Matthew has already read. So the migration is tested against a
database built from the v1 schema exactly as it shipped
(tests/fixtures/schema_v1.sql, a copy of schema.sql before v2), with rows of
every v1 status in it.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parents[1] / "ops"
sys.path.insert(0, str(OPS))

import done                                                   # noqa: E402
import migrate_ledger                                         # noqa: E402
from core.db import database as db                            # noqa: E402
from core.db import migrate                                   # noqa: E402
from core.pipeline import ledger                              # noqa: E402
from core.pipeline import tasks as task_ops                   # noqa: E402
from core.reports import brief as reports                     # noqa: E402

V1_SCHEMA = (Path(__file__).parent / "fixtures" / "schema_v1.sql").read_text(
    encoding="utf-8")
T0 = "2026-09-20T09:00:00-04:00"


def v1_db(path: Path) -> Path:
    """A database as the Mac has it: v1 schema, real-looking rows."""
    c = sqlite3.connect(path, isolation_level=None)
    c.executescript(V1_SCHEMA)

    def artifact(n, source, ref, cp, due, amt, desc="invoice", domain="BUSINESS",
                 entity="B_MRE"):
        aid = c.execute(
            "INSERT INTO artifacts (sha256, source, source_ref, ingested_at, status) "
            "VALUES (?,?,?,?, 'FILED')", (f"{n:064x}", source, ref, T0)).lastrowid
        c.execute(
            "INSERT INTO classifications (artifact_id, domain, entity_id, category, "
            "urgency, confidence, due_date, counterparty, descriptor, amount_cents, "
            "model, prompt_hash, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (aid, domain, entity, "VENDORS", "HIGH", 0.91, due, cp, desc, amt,
             "m", "h", T0))
        return aid

    def task(tid, title, status, art=None, due=None, entity="B_MRE",
             completed=None, urgency="HIGH"):
        c.execute(
            "INSERT INTO tasks (id, title, detail, due_date, entity_id, urgency, "
            "status, source_artifact, created_at, completed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (tid, title, "why", due, entity, urgency, status, art, T0, completed))

    a1 = artifact(1, "email", "<msg-1@acme>", "acme-corp", "2026-10-06", 12500)
    a2 = artifact(2, "photo", "IMG_0042.jpg", "nassau-county-court", "2026-10-03",
                  None, desc="jury summons", domain="PERSONAL", entity="P_MRE")
    a3 = artifact(3, "email", "<msg-3@acme>", "acme-corp", "2026-10-06", 12500)
    a4 = artifact(4, "email", "<msg-4@news>", "newsletter", "2026-09-30", None)

    # Ids are deliberately not 1..n: they must survive exactly.
    task(3, "Invoice — Acme Corp", "OPEN", a1, "2026-10-06")
    task(7, "Jury duty summons — Nassau County Court", "DONE", a2, "2026-10-03",
         entity="P_MRE", completed="2026-09-12T10:00:00-04:00")
    task(9, "Invoice — Acme Corp (forwarded)", "OPEN", a3, "2026-10-06")
    task(12, "Reply to newsletter", "DISMISSED", a4, "2026-09-30",
         completed="2026-09-21T08:00:00-04:00", urgency="NORMAL")
    task(15, "Call the accountant", "OPEN")                  # no source document
    c.execute("INSERT INTO actions_log (ts, action, detail) VALUES (?, 'TASK_DONE', 'x')",
              (T0,))
    c.close()
    return path


@pytest.fixture
def old(tmp_path):
    return v1_db(tmp_path / "lms.db")


def test_the_fixture_really_is_v1(old):
    c = sqlite3.connect(old)
    assert migrate.needs_ledger_v2(c)
    c.close()


def test_connect_migrates_and_keeps_every_id(old):
    conn = db.connect(old)
    rows = {r["id"]: r for r in conn.execute("SELECT * FROM tasks")}
    assert sorted(rows) == [3, 7, 9, 12, 15]
    assert not migrate.needs_ledger_v2(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == migrate.LEDGER_V2


def test_statuses_and_outcomes_map(old):
    conn = db.connect(old)
    got = {r["id"]: (r["status"], r["final_outcome"])
           for r in conn.execute("SELECT * FROM tasks")}
    assert got == {3: ("TRIAGED", None), 7: ("CLOSED", "DONE"),
                   9: ("TRIAGED", None), 12: ("CANCELED", "DISMISSED"),
                   15: ("TRIAGED", None)}
    done = ledger.get(conn, 7)
    assert done["completed_at"] == "2026-09-12T10:00:00-04:00", "close time lost"
    assert "before ledger v2" in done["verification_evidence"]


def test_the_brief_shows_the_same_tasks_after_as_before(old):
    """Nothing Matthew had open disappears, nothing closed comes back."""
    conn = db.connect(old)
    assert sorted(t.task_id for t in reports.todo_list(conn)) == [3, 9, 15]
    assert [r["title"] for r in task_ops.dismissals(conn)] == ["Reply to newsletter"]


def test_source_fields_come_from_the_document(old):
    conn = db.connect(old)
    t = ledger.get(conn, 7)
    assert (t["source"], t["source_link"], t["domain"]) == (
        "photo", "IMG_0042.jpg", "PERSONAL")
    assert t["extraction_confidence"] == pytest.approx(0.91)
    assert t["owner"] == ledger.OWNER_MATTHEW
    [src] = ledger.sources(conn, 7)
    assert src["source_key"] == f"artifact:{2:064x}"
    assert ledger.get(conn, 15)["source"] == "manual"
    assert ledger.sources(conn, 15) == []


def test_every_row_has_a_migrated_event(old):
    conn = db.connect(old)
    for tid in (3, 7, 9, 12, 15):
        [e] = [e for e in ledger.history(conn, tid) if e["event"] == "MIGRATED"]
        assert e["actor"] == "migrate"
    logged = conn.execute(
        "SELECT detail FROM actions_log WHERE action = 'TASK_LEDGER_V2'").fetchone()
    assert "5 tasks" in logged["detail"]


def test_old_duplicates_are_reported_not_closed(old):
    """Tasks 3 and 9 are the same Acme invoice. The older keeps the key; the
    newer stays open with a note, for a human to merge in one command."""
    conn = sqlite3.connect(old, isolation_level=None)
    conn.row_factory = sqlite3.Row
    report = migrate.upgrade(conn, old)
    assert report.collisions == [(3, 9, "v1|B_MRE|acme-corp|due=2026-10-06|amt=12500")]
    assert ledger.get(conn, 3)["event_key"] == "v1|B_MRE|acme-corp|due=2026-10-06|amt=12500"
    assert ledger.get(conn, 9)["event_key"] is None
    assert ledger.get(conn, 9)["status"] == "TRIAGED"
    notes = [e["detail"] for e in ledger.history(conn, 9)
             if e["event"] == "POSSIBLE_DUPLICATE"]
    assert notes and "#3" in notes[0]
    assert any("--duplicate-of 3" in line for line in report.lines())


def test_new_evidence_merges_into_a_migrated_task(old):
    """The point of giving old rows keys: a later copy of the Acme invoice from
    Drive lands on task 3, not on a new task."""
    conn = db.connect(old)
    r = ledger.record_event(
        conn, source_key="drive:file-123", source="drive", title="Invoice",
        event_key=ledger.event_key(entity_id="B_MRE", counterparty="Acme Corp",
                                   due_date="2026-10-06", amount_cents=12500))
    assert (r.task_id, r.outcome) == (3, "merged")


def test_a_rerun_of_an_old_source_event_is_seen(old):
    conn = db.connect(old)
    r = ledger.record_event(conn, source_key=f"artifact:{1:064x}", source="email",
                            title="Invoice")
    assert (r.task_id, r.outcome) == (3, "seen")


def test_migrated_tasks_still_close_and_reopen(old):
    conn = db.connect(old)
    task_ops.close_task(conn, 3, note="paid")
    task_ops.reopen_task(conn, 7)
    assert ledger.get(conn, 3)["status"] == "CLOSED"
    assert ledger.get(conn, 7)["status"] == "TRIAGED"


def test_a_snapshot_is_taken_first_and_is_the_old_database(old):
    db.connect(old).close()
    snap = old.with_name("lms.db.pre-ledger-v2")
    assert snap.exists()
    c = sqlite3.connect(snap)
    assert migrate.needs_ledger_v2(c), "the snapshot is not the pre-migration state"
    assert c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 5
    c.close()


def test_indexes_are_on_the_new_table(old):
    conn = db.connect(old)
    idx = {r["name"]: r["tbl_name"] for r in conn.execute(
        "SELECT name, tbl_name FROM sqlite_master WHERE type = 'index'")}
    assert idx["idx_tasks_status"] == "tasks"
    assert idx["idx_tasks_due"] == "tasks"
    assert "tasks_v1" not in {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}


def test_running_it_twice_is_a_no_op(old):
    db.connect(old).close()
    conn = db.connect(old)
    report = migrate.upgrade(conn, old)
    assert not report.migrated
    assert conn.execute("SELECT COUNT(*) FROM task_events WHERE event = 'MIGRATED'"
                        ).fetchone()[0] == 5


def test_a_second_process_finds_it_done(old):
    """Two jobs waking after `git pull`: the second waits on the lock, then
    finds nothing to do instead of migrating a migrated table."""
    a = sqlite3.connect(old, isolation_level=None)
    b = sqlite3.connect(old, isolation_level=None)
    a.row_factory = b.row_factory = sqlite3.Row
    assert migrate.upgrade(a, old).migrated
    assert not migrate.upgrade(b, old).migrated
    a.close(), b.close()


def test_a_failure_halfway_changes_nothing(old):
    conn = sqlite3.connect(old, isolation_level=None)
    conn.row_factory = sqlite3.Row
    broken = migrate.SCHEMA_PATH.read_text() if hasattr(migrate, "SCHEMA_PATH") \
        else db.SCHEMA_PATH.read_text(encoding="utf-8")
    broken += "\nCREATE TABLE task_sources_broken (x REFERENCES nowhere(;\n"
    with pytest.raises(sqlite3.Error):
        migrate.upgrade(conn, old, schema_sql=broken)
    assert migrate.needs_ledger_v2(conn)
    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 5
    assert {r[0] for r in conn.execute("SELECT status FROM tasks")} == {
        "OPEN", "DONE", "DISMISSED"}
    conn.close()
    # ...and the next connect migrates it properly.
    assert sorted(t.task_id for t in reports.todo_list(db.connect(old))) == [3, 9, 15]


def test_a_fresh_database_needs_no_migration(tmp_path):
    conn = db.connect(tmp_path / "new.db")
    assert not migrate.needs_ledger_v2(conn)
    assert not (tmp_path / "new.db.pre-ledger-v2").exists()


def test_the_ops_script_reports_and_passes(old, capsys):
    assert migrate_ledger.check(old) == 0
    assert "migration needed" in capsys.readouterr().out
    assert migrate_ledger.run(old) == 0
    out = capsys.readouterr().out
    assert "ids unchanged" in out
    assert "OPEN -> TRIAGED: 3" in out
    assert "--duplicate-of 3" in out
    assert "PASS: 5 tasks" in out
    assert migrate_ledger.run(old) == 0
    assert "already v2" in capsys.readouterr().out


def test_done_py_lists_shows_and_merges_a_migrated_ledger(old, capsys):
    conn = db.connect(old)
    assert done.show_open(conn) == 0
    listed = capsys.readouterr().out
    assert "3 open" in listed
    task_ops.mark_duplicate(conn, 9, 3)
    assert done.show_task(conn, 3) == 0
    shown = capsys.readouterr().out
    assert "sources (2):" in shown
    assert "#9 folded in" in shown
    assert "(as detected)" in shown


def test_old_code_half_applying_the_new_schema_does_not_break_the_migration(old):
    """The haltbot stays up through a halt, and its old db.connect() reads
    schema.sql from disk on every call. After `git pull`, before the migration,
    that runs the NEW schema against the OLD table: task_sources and
    idx_tasks_entity get created, then idx_tasks_owner fails (no such column).

    The migration must still end with the index on the new table and
    task_sources' foreign key pointing at `tasks` — not at `tasks_v1`, which is
    where SQLite's rename would otherwise redirect it, and which is then
    dropped.
    """
    c = sqlite3.connect(old, isolation_level=None)
    with pytest.raises(sqlite3.OperationalError, match="owner"):
        c.executescript(db.SCHEMA_PATH.read_text(encoding="utf-8"))
    # Worse than what executescript does today (it stops at the first error):
    # every statement that CAN apply, applied. The new task tables then exist
    # already, with foreign keys to the old table.
    for stmt in migrate.schema_statements(db.SCHEMA_PATH.read_text(encoding="utf-8")):
        try:
            c.execute(stmt)
        except sqlite3.OperationalError:
            pass
    assert "task_sources" in {r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    c.close()

    conn = db.connect(old)
    idx = {r["name"]: r["tbl_name"] for r in conn.execute(
        "SELECT name, tbl_name FROM sqlite_master WHERE type = 'index'")}
    assert idx["idx_tasks_entity"] == "tasks"
    assert idx["idx_tasks_owner"] == "tasks"
    fk_targets = {r["table"] for r in conn.execute(
        "PRAGMA foreign_key_list(task_sources)")}
    assert "tasks" in fk_targets and "tasks_v1" not in fk_targets
    r = ledger.record_event(conn, source_key="drive:x", source="drive", title="t")
    assert r.outcome == "created"
