"""Backup and restore.

The load-bearing test here is `test_a_file_copy_of_a_live_database_loses_rows`.
It is the reason this module uses SQLite's online backup API instead of `cp`,
and it fails in the worst available way: the copy opens cleanly, passes
integrity_check, and is empty.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))

from backup import (BackupError, row_counts, same_volume,       # noqa: E402
                    snapshot_database, warn_if_same_volume)
from restore_test import verify_restore                          # noqa: E402
import restore_test                                              # noqa: E402


def live_db(path: Path, rows: int = 500) -> sqlite3.Connection:
    """A database in the state the daemon leaves it in: WAL, committed, OPEN.

    Returns the connection, and the caller must hold it. Closing the database
    — or merely letting the connection be garbage-collected, which is how the
    first version of this fixture failed — checkpoints the WAL into the main
    file and erases the exact condition under test. The whole scenario is "a
    process is using this database right now", so the handle has to stay.
    """
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE artifacts (id INTEGER PRIMARY KEY, sha TEXT)")
    conn.executemany("INSERT INTO artifacts (sha) VALUES (?)",
                     [(f"{i:064d}",) for i in range(rows)])
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Why the online backup API
# ---------------------------------------------------------------------------

def test_a_file_copy_of_a_live_database_loses_rows(tmp_path):
    """The measurement behind the design.

    In WAL mode, committed rows live in lms.db-wal until a checkpoint. Copying
    lms.db alone gets the main file and none of them. The result opens, passes
    integrity_check, and contains NOTHING — a backup that fails silently and
    only reveals itself on the day it is needed.
    """
    import shutil

    src = tmp_path / "lms.db"
    conn = live_db(src)                      # held open on purpose
    assert (tmp_path / "lms.db-wal").exists(), "no WAL — the test proves nothing"

    naive = tmp_path / "naive.db"
    shutil.copy2(src, naive)

    assert sqlite3.connect(naive).execute(
        "PRAGMA integrity_check").fetchone()[0] == "ok", \
        "the bad copy is not even detectably corrupt"
    assert sum(row_counts(naive).values()) == 0, \
        "if a plain copy works here, this module's whole design is unnecessary"


def test_the_online_backup_keeps_every_row(tmp_path):
    src = tmp_path / "lms.db"
    conn = live_db(src, rows=500)            # held open on purpose
    snap = snapshot_database(src, tmp_path / "out" / "lms.db")
    assert row_counts(snap) == {"artifacts": 500}


def test_the_snapshot_checks_itself_before_being_trusted(tmp_path):
    """Verified while a good original still exists to compare against.

    A backup nobody checked is a hope.
    """
    src = tmp_path / "lms.db"
    conn = live_db(src, rows=10)             # held open on purpose
    snap = snapshot_database(src, tmp_path / "out" / "lms.db")
    assert sqlite3.connect(snap).execute(
        "PRAGMA integrity_check").fetchone()[0] == "ok"


def test_a_missing_database_is_an_error_not_an_empty_backup(tmp_path):
    """Backing up nothing must fail loudly. A zero-byte snapshot uploaded
    nightly is worse than no backup, because it looks like one."""
    with pytest.raises(BackupError) as exc:
        snapshot_database(tmp_path / "absent.db", tmp_path / "out.db")
    assert "no database" in str(exc.value)


# ---------------------------------------------------------------------------
# A backup beside the thing it backs up
# ---------------------------------------------------------------------------

def test_a_repo_on_the_archive_volume_is_flagged(tmp_path, capsys):
    """The default put the repo on the same disk as the data.

    Found on the Mac: archive at /Volumes/MacStudioHD/LMS/archive, repo at
    /Volumes/MacStudioHD/LMS/LMS_backup. That survives a bad delete and dies
    with the disk, which is the case the word "backup" is usually reaching for.
    """
    archive = tmp_path / "LMS" / "archive"
    repo = tmp_path / "LMS" / "LMS_backup"
    archive.mkdir(parents=True)
    repo.mkdir(parents=True)

    assert warn_if_same_volume(archive, repo) is True
    out = capsys.readouterr().out
    assert "SAME VOLUME" in out
    assert "D-011" in out, "the warning must say which decision unblocks it"


def test_same_volume_uses_the_device_not_the_path(tmp_path):
    """A string comparison would call these different disks.

    /Volumes/MacStudioHD/LMS and /Volumes/MacStudioHD_backup look unrelated
    and can be one device; st_dev is the only thing that actually knows.
    """
    a = tmp_path / "MacStudioHD" / "LMS"
    b = tmp_path / "MacStudioHD_backup"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    assert same_volume(a, b) is True


def test_a_path_that_does_not_exist_yet_still_resolves_a_volume(tmp_path):
    """The repo directory is created by restic on first run, so the check has
    to work before it exists — otherwise the warning never fires on the one
    run where it matters most."""
    assert same_volume(tmp_path, tmp_path / "not" / "created" / "yet") is True


# ---------------------------------------------------------------------------
# Verifying a restore
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_failures():
    restore_test.failures.clear()
    yield
    restore_test.failures.clear()


def restored_tree(tmp_path: Path, *, rows: int = 5, sidecars: int = 2,
                  entities: bool = True) -> Path:
    """What restic leaves behind: the original paths, nested under a temp dir."""
    root = tmp_path / "restored" / "Volumes" / "MacStudioHD" / "LMS"
    root.mkdir(parents=True)

    conn = sqlite3.connect(root / "lms.db")
    conn.execute("CREATE TABLE artifacts (id INTEGER PRIMARY KEY, sha TEXT)")
    conn.executemany("INSERT INTO artifacts (sha) VALUES (?)",
                     [(str(i),) for i in range(rows)])
    conn.commit()
    conn.close()

    for i in range(sidecars):
        p = root / "sidecars" / f"doc{i}.meta.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}", encoding="utf-8")

    if entities:
        cfg = root / "config"
        cfg.mkdir(exist_ok=True)
        (cfg / "entities.yaml").write_text(
            "businesses:\n  B_MRE:\n    tree: MRECAI\npeople:\n  P_MRE:\n"
            "    tree: PERSONAL/Matthew\n", encoding="utf-8")

    return tmp_path / "restored"


def with_manifest(tree: Path, **claims) -> Path:
    """Give a restored tree the manifest a real snapshot carries."""
    import json
    root = next(tree.rglob("lms.db")).parent
    (root / "manifest.json").write_text(json.dumps(claims), encoding="utf-8")
    return tree


def test_the_manifest_makes_the_sidecar_check_exact(tmp_path):
    """Checking against the LIVE archive is checking against a moving target.

    The first version reported "4 sidecar(s) restored (archive has 2)" as a
    PASS — arithmetic rather than evidence, because --documents put every
    sidecar in twice and `>=` swallowed it.
    """
    tree = with_manifest(restored_tree(tmp_path, sidecars=3), sidecars=3)
    verify_restore(tree, {"artifacts": 5}, live_sidecars=99)
    assert restore_test.failures == [], restore_test.failures


def test_a_snapshot_missing_sidecars_it_claims_is_a_failure(tmp_path):
    tree = with_manifest(restored_tree(tmp_path, sidecars=1), sidecars=8)
    verify_restore(tree, {"artifacts": 5}, live_sidecars=1)
    assert any("claims 8 sidecars" in f for f in restore_test.failures), \
        restore_test.failures


def test_growth_since_the_snapshot_does_not_fail_the_sidecar_check(tmp_path):
    """A document filed between the backup and this check must not make a
    perfectly good backup look short."""
    tree = with_manifest(restored_tree(tmp_path, sidecars=2), sidecars=2)
    verify_restore(tree, {"artifacts": 5}, live_sidecars=40)
    assert restore_test.failures == [], restore_test.failures


def test_a_catalogue_only_snapshot_says_so(tmp_path, capsys):
    """`restore latest` takes the newest snapshot, and the nightly job runs
    without --documents — so the newest is normally the catalogue alone.

    "Restore verified, the backup is usable" would otherwise read as "the
    documents are safe", which for that snapshot is false.
    """
    tree = with_manifest(restored_tree(tmp_path, sidecars=2),
                         sidecars=2, documents_included=False)
    verify_restore(tree, {"artifacts": 5}, 2)
    out = capsys.readouterr().out
    assert "CATALOGUE ONLY" in out
    assert restore_test.failures == [], "it is a caveat, not a failure"


def test_a_full_snapshot_says_the_documents_are_in_it(tmp_path, capsys):
    tree = with_manifest(restored_tree(tmp_path, sidecars=2),
                         sidecars=2, documents_included=True)
    verify_restore(tree, {"artifacts": 5}, 2)
    assert "includes the filed documents" in capsys.readouterr().out


def test_a_snapshot_with_no_manifest_still_verifies(tmp_path):
    """Repositories written before manifests existed must still be checkable.

    A restore test that refuses to run on an old snapshot is useless exactly
    when an old snapshot is all there is.
    """
    verify_restore(restored_tree(tmp_path, sidecars=2), {"artifacts": 5}, 2)
    assert restore_test.failures == []


def test_a_good_restore_verifies(tmp_path):
    verify_restore(restored_tree(tmp_path, rows=5, sidecars=2),
                   {"artifacts": 5}, 2)
    assert restore_test.failures == []


def test_an_empty_restored_database_is_caught(tmp_path):
    """The naive-copy failure, caught at the point it matters.

    integrity_check passes on an empty database. Counting is the only check
    that sees it.
    """
    verify_restore(restored_tree(tmp_path, rows=0), {"artifacts": 500}, 0)
    assert any("EMPTY" in f for f in restore_test.failures), restore_test.failures


def test_a_missing_database_fails_the_restore(tmp_path):
    tree = restored_tree(tmp_path)
    next(tree.rglob("lms.db")).unlink()
    verify_restore(tree, {"artifacts": 5}, 2)
    assert any("no catalogue" in f for f in restore_test.failures)


def test_a_corrupt_database_fails_the_restore(tmp_path):
    tree = restored_tree(tmp_path)
    next(tree.rglob("lms.db")).write_bytes(b"this is not a database at all")
    verify_restore(tree, {"artifacts": 5}, 2)
    assert restore_test.failures


def test_without_a_manifest_a_short_count_is_ambiguous_not_a_failure(tmp_path):
    """This used to assert a hard FAIL, and that was the defect.

    Restored-fewer-than-live has two causes that look identical: sidecars lost
    from the backup, or documents filed since the snapshot. Calling it loss
    produces a false alarm every time a document arrives between the backup
    and the check — and the alarm that cries wolf is the one nobody reads on
    the morning it is real.

    With a manifest the question is answerable exactly, which is why manifests
    exist. Without one, saying "cannot tell, take a fresh backup" is the only
    honest answer available.
    """
    verify_restore(restored_tree(tmp_path, sidecars=1), {"artifacts": 5}, 9)
    assert restore_test.failures == [], restore_test.failures


def test_a_restore_without_entities_yaml_fails(tmp_path):
    """It can hold documents but cannot correctly file another one.
    entities.yaml IS the classifier."""
    verify_restore(restored_tree(tmp_path, entities=False), {"artifacts": 5}, 2)
    assert any("entities.yaml" in f for f in restore_test.failures)


def test_a_backup_ahead_of_the_live_database_is_a_failure_not_drift(tmp_path):
    """Fewer rows than live is expected — the database grew since the snapshot.

    MORE rows in the backup means rows have vanished from the live database,
    which is a different and far more interesting problem, and must not be
    filed under 'drift'.
    """
    verify_restore(restored_tree(tmp_path, rows=5), {"artifacts": 2}, 2)
    assert any("rows the live database does not" in f
               for f in restore_test.failures), restore_test.failures


def test_normal_growth_since_the_snapshot_is_only_a_warning(tmp_path):
    verify_restore(restored_tree(tmp_path, rows=5), {"artifacts": 40}, 2)
    assert restore_test.failures == []
