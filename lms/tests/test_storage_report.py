"""Storage and dedup report arithmetic (R28; addendum §4 "Report storage
accurately"), on a small archive built in tmp_path where every byte is known.

The fixture, in bytes:

    A  statement.pdf   1000  photo        filed   also seen at dropbox:/Dropbox/stmt.pdf
    B  m1.eml.txt       300  email        filed   re-polled once (not a saving)
    C  invoice.pdf     2000  attachment   filed   also attached to m2 (a saving), m2 re-read once
    D  scan.jpg         500  photo        QUARANTINED
    E  m1 (old render)  280  email        DUPLICATE — superseded rendering of m1

So: 3800 distinct bytes received, 3000 saved by exact dedup, 6800 original
bytes in all; 3300 bytes of filed documents.
"""

import importlib.util
from pathlib import Path

import pytest

from core.db import database as db
from core.pipeline import filing
from core.reports import storage

OPS = Path(__file__).resolve().parents[1] / "ops"

A, B, C, D, E = b"A" * 1000, b"B" * 300, b"C" * 2000, b"D" * 500, b"E" * 280


@pytest.fixture
def world(tmp_path):
    roots = filing.StorageRoots(archive=tmp_path / "archive",
                                originals=tmp_path / "_originals",
                                quarantine=tmp_path / "quarantine")
    roots.ensure()
    inbox, spool = tmp_path / "inbox", tmp_path / "spool"
    conn = db.connect(tmp_path / "lms.db")
    tree = roots.archive / "PERSONAL" / "FINANCE"
    tree.mkdir(parents=True)

    def filed(data, name, source, ref, suffix):
        sha = db.sha256_bytes(data)
        path = tree / name
        path.write_bytes(data)
        (tree / (name + ".meta.json")).write_text('{"x": 1}', encoding="utf-8")
        (roots.originals / f"{sha}{suffix}").write_bytes(data)
        db.insert_artifact(conn, sha256=sha, source=source, source_ref=ref,
                           original_name=f"orig{suffix}", byte_size=len(data),
                           filed_path=str(path), status="FILED")
        return sha

    sha_a = filed(A, "2026-03-31__stmt.pdf", "photo", "stmt.pdf", ".pdf")
    (tree / "2026-03-31__stmt.pdf.txt").write_text("statement text", encoding="utf-8")
    sha_b = filed(B, "2026-04-02__m1.eml.txt", "email", "m1", ".txt")
    sha_c = filed(C, "2026-04-02__invoice.pdf", "attachment", "m1/a1", ".pdf")

    db.insert_artifact(conn, sha256=db.sha256_bytes(D), source="photo",
                       source_ref="scan.jpg", byte_size=len(D), status="QUARANTINED")
    (roots.quarantine / "dddddddd__scan.jpg").write_bytes(D)
    db.insert_artifact(conn, sha256=db.sha256_bytes(E), source="email",
                       source_ref="m1", byte_size=len(E), status="DUPLICATE")

    db.record_duplicate(conn, sha_a, "dropbox", "/Dropbox/stmt.pdf", "stmt.pdf")
    db.record_duplicate(conn, sha_a, "photo", "stmt.pdf", "stmt.pdf")       # same place
    db.record_duplicate(conn, sha_b, "email", "m1", "m1.eml.txt")            # re-poll
    db.record_duplicate(conn, sha_c, "attachment", "m2/a9", "invoice.pdf")
    db.record_duplicate(conn, sha_c, "attachment", "m2/a9", "invoice.pdf")   # re-poll

    done = inbox / "_done"
    done.mkdir(parents=True)
    (done / "stmt.pdf").write_bytes(A)
    (done / "stmt-1.pdf").write_bytes(A)
    (done / "other.jpg").write_bytes(b"x" * 77)
    (inbox / "_failed").mkdir()
    (inbox / "_failed" / "empty.jpg").write_bytes(b"")

    spool.mkdir()
    (spool / "m1.eml.txt").write_bytes(B)
    (spool / "m1__invoice.pdf").write_bytes(C)

    yield conn, roots, inbox, spool
    conn.close()


def test_exact_dedup_counts_new_locations_not_re_reads(world):
    conn, *_ = world
    d = storage.exact_dedup(conn)
    assert d.extra_locations == 2
    assert d.bytes_saved == 1000 + 2000
    assert d.resightings == 3
    assert (d.superseded, d.superseded_bytes) == (1, 280)
    assert [g.byte_size for g in d.groups] == [2000, 1000]   # largest saving first
    assert d.groups[1].locations[0][:2] == ("dropbox", "/Dropbox/stmt.pdf")


def test_a_re_poll_with_a_new_gmail_attachment_id_is_a_re_read(world):
    """Gmail hands out a fresh attachment id on every fetch (Mac, Nov 3: one
    message seen nine times under nine ids). Only a different MESSAGE is a
    new location."""
    conn, *_ = world
    sha_c = db.sha256_bytes(C)
    db.record_duplicate(conn, sha_c, "attachment", "m1/a7", "invoice.pdf")   # its own message
    db.record_duplicate(conn, sha_c, "attachment", "m2/a10", "invoice.pdf")  # m2 again
    db.record_duplicate(conn, sha_c, "attachment", "m3/a1", "invoice.pdf")   # a new message
    d = storage.exact_dedup(conn)
    assert d.extra_locations == 3
    assert d.resightings == 3 + 2
    c = next(g for g in d.groups if g.sha256 == sha_c)
    assert c.canonical == ("attachment", "m1")
    assert [ref for _, ref, _ in c.locations] == ["m2", "m3"]
    text = storage.render_dedup(d, None)
    assert "attachment:m2  (" in text and "a10" not in text


def test_original_bytes_are_distinct_plus_saved(world):
    conn, roots, inbox, spool = world
    r = storage.build_report(conn, roots, inbox=inbox, spool=spool)
    by = {s.source: s for s in r.sources}
    assert (by["photo"].artifacts, by["photo"].bytes) == (2, 1500)
    assert (by["photo"].filed, by["photo"].filed_bytes) == (1, 1000)
    assert (by["email"].artifacts, by["email"].bytes) == (1, 300)   # E left out
    assert (by["attachment"].extra_locations, by["attachment"].extra_bytes) == (1, 2000)
    assert (by["dropbox"].artifacts, by["dropbox"].extra_bytes) == (0, 1000)
    assert r.distinct_bytes == 3800
    assert r.original_bytes == r.distinct_bytes + r.dedup.bytes_saved == 6800


def test_copied_bytes_are_measured_on_disk_and_agree_with_the_database(world):
    conn, roots, inbox, spool = world
    r = storage.build_report(conn, roots, inbox=inbox, spool=spool)
    docs, side, orig, quar, spl = r.managed
    assert (docs.files, docs.bytes) == (3, 3300)
    assert side.files == 4                  # three .meta.json and one text sidecar
    assert (orig.files, orig.bytes) == (3, 3300)
    assert (quar.files, quar.bytes) == (1, 500)
    assert (spl.files, spl.bytes) == (2, 2300)
    assert r.db_filed_bytes == 3300 and r.db_filed == 3
    assert r.managed_bytes == sum(t.bytes for t in r.managed)
    assert r.checks == []


def test_reclaimable_is_verified_by_hash_and_gmail_is_not_measured(world):
    conn, roots, inbox, spool = world
    r = storage.build_report(conn, roots, inbox=inbox, spool=spool)
    rec = {c.label: c for c in r.reclaimable}
    first = rec["iCloud inbox _done/ — originals whose bytes are filed"]
    more = rec["iCloud inbox _done/ — further copies of the same bytes"]
    other = rec["iCloud inbox _done/ — NOT reclaimable"]
    assert (first.files, first.bytes) == (1, 1000)
    assert (more.files, more.bytes) == (1, 1000)
    assert (other.files, other.bytes) == (1, 77)
    gmail = rec["Gmail originals (mail and attachments)"]
    assert gmail.bytes is None and "not measured" in gmail.note
    spool_line = rec["LMS working copies — mail spool files already filed"]
    assert (spool_line.files, spool_line.bytes) == (2, 2300)
    assert any("Gmail" in m for m in r.not_measured)

    text = storage.render_storage(r)
    assert "never_delete_file hard stop" in text and "D-067" in text
    assert "6,800 B" in text


def test_a_missing_tree_is_not_measured_rather_than_zero(world, tmp_path):
    conn, roots, inbox, _ = world
    r = storage.build_report(conn, roots, inbox=inbox, spool=tmp_path / "nope")
    spl = r.managed[4]
    assert spl.bytes is None and "not measured" in spl.note
    assert r.managed_bytes is None
    assert "total: NOT MEASURED" in storage.render_storage(r)


def test_archive_bytes_that_disagree_with_the_database_are_a_finding(world):
    conn, roots, inbox, spool = world
    path = conn.execute("SELECT filed_path FROM artifacts WHERE source = 'email' "
                        "AND status = 'FILED'").fetchone()[0]
    Path(path).write_bytes(b"")          # truncated: sizes now disagree
    r = storage.build_report(conn, roots, inbox=inbox, spool=spool)
    assert any("≠ database filed bytes" in c for c in r.checks)


def test_a_filed_document_missing_from_disk_is_a_finding(world):
    conn, roots, inbox, spool = world
    path = conn.execute("SELECT filed_path FROM artifacts WHERE source = 'email' "
                        "AND status = 'FILED'").fetchone()[0]
    Path(path).unlink()
    r = storage.build_report(conn, roots, inbox=inbox, spool=spool)
    assert any("not at their filed_path" in c for c in r.checks)


def test_sample_hash_check_catches_a_changed_copy(world):
    conn, roots, *_ = world
    checks = storage.verify_sample(conn, roots, 10, seed=1)
    assert len(checks) == 3 and all(c.ok for c in checks)

    path = Path(conn.execute("SELECT filed_path FROM artifacts WHERE source = "
                             "'attachment'").fetchone()[0])
    path.write_bytes(C + b"!")
    bad = [c for c in storage.verify_sample(conn, roots, 10, seed=1) if not c.ok]
    assert len(bad) == 1
    assert any("size" in p for p in bad[0].problems)
    assert any("sha256" in p for p in bad[0].problems)
    assert "1 do not" in storage.render_samples(storage.verify_sample(conn, roots, 3))


def test_the_dedup_report_names_both_kinds(world):
    conn, *_ = world
    from core.pipeline import neardup
    text = storage.render_dedup(storage.exact_dedup(conn),
                                neardup.find_candidates(neardup.load_docs(conn)))
    assert "2 extra location(s)" in text
    assert "dropbox:/Dropbox/stmt.pdf" in text
    assert "near-duplicate candidates: 0" in text


def _ops(name):
    spec = importlib.util.spec_from_file_location(f"{name}_r28", OPS / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_the_ops_report_opens_the_database_read_only(world, tmp_path, monkeypatch,
                                                     capsys):
    conn, roots, inbox, spool = world
    before = conn.execute("SELECT COUNT(*) FROM actions_log").fetchone()[0]
    sr = _ops("storage_report")
    monkeypatch.setattr(sr, "ensure_venv", lambda *a, **k: None)
    monkeypatch.setattr(sr, "load_lms_env", lambda *a, **k: {})
    for k, v in {"LMS_ARCHIVE_ROOT": roots.archive, "LMS_ORIGINALS_ROOT": roots.originals,
                 "LMS_QUARANTINE_ROOT": roots.quarantine, "LMS_INBOX": inbox,
                 "LMS_MAIL_SPOOL": spool, "LMS_DB": tmp_path / "lms.db"}.items():
        monkeypatch.setenv(k, str(v))
    monkeypatch.setattr("sys.argv", ["storage_report.py", "--verify", "2"])
    assert sr.main() == 0
    out = capsys.readouterr().out
    for heading in ("STORAGE REPORT", "DEDUP REPORT", "SAMPLE HASH CHECK"):
        assert heading in out
    after = conn.execute("SELECT COUNT(*) FROM actions_log").fetchone()[0]
    assert after == before


def test_the_reports_have_no_write_path():
    """'Report only' is a property of the code, not of a docstring."""
    import re
    src = (Path(__file__).resolve().parents[1] / "core" / "reports" /
           "storage.py").read_text(encoding="utf-8")
    code = re.sub(r'"""[\s\S]*?"""', "", src)
    for verb in ("INSERT", "UPDATE ", "DELETE", "write_text", "write_bytes",
                 "log_action", "set_artifact_status", "copy2"):
        assert verb not in code, f"storage.py contains {verb}"
