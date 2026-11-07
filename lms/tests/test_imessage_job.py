"""ops/imessage.py and ops/backfill_imessage.py (R2, D-081)."""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from core.adapters import asr
from core.adapters import imessage as im
from core.adapters.lmstudio import Completion
from core.db import database as db
from core.pipeline import filing, notify
from core.pipeline import imessage as i
from core.pipeline.classify import Classifier
from core.pipeline.registry import load_registry
from tests.im_fixture import IM, contacts

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))

import backfill_imessage as bf      # noqa: E402
import imessage as job              # noqa: E402

ET = ZoneInfo("America/New_York")
CONFIG = Path(__file__).resolve().parents[1] / "config"


class FakeModel:
    def complete(self, **kw):
        return Completion(text=json.dumps({
            "domain": "BUSINESS", "entity_id": "B_MRE", "category": "VENDORS",
            "subcategory": "invoices-received", "urgency": "NORMAL",
            "confidence": 0.93, "requires_reply": False, "due_date": None,
            "counterparty": "acme-supply", "descriptor": "invoice chat",
            "amount_cents": None, "rationale": "vendor"}),
            model="fake", elapsed_s=0.01, finish_reason="stop")


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "lms.db")
    yield c
    c.close()


@pytest.fixture
def pipe(tmp_path):
    roots = filing.StorageRoots(archive=tmp_path / "archive",
                                originals=tmp_path / "_originals",
                                quarantine=tmp_path / "quarantine")
    roots.ensure()
    registry = load_registry(CONFIG)
    return roots, registry, Classifier(registry, client=FakeModel())


@pytest.fixture(autouse=True)
def no_contacts(tmp_path, monkeypatch):
    monkeypatch.setattr(im, "CONTACTS_ROOT", tmp_path / "no-contacts")


def no_speech(path, tmp):
    raise asr.ASRUnavailable("not in tests")


def history(tmp_path, days_back):
    box = IM(tmp_path / "Messages")
    h = box.handle("+15550100100")
    c = box.chat("+15550100100")
    now = datetime.now(ET)
    for back in days_back:
        box.message(c, (now - timedelta(days=back)).replace(hour=9, minute=0),
                    f"day -{back}", handle=h)
    box.close()
    return box.root, now


def test_the_job_stops_when_halted(monkeypatch, capsys):
    monkeypatch.setattr(job._halt, "read_flag", lambda: {"ts": "now", "by": "telegram"})
    monkeypatch.setattr(job, "ensure_venv", lambda *a, **k: None)
    monkeypatch.setattr(job, "load_lms_env", lambda: {})
    monkeypatch.setattr(sys, "argv", ["imessage.py"])
    assert job.main() == 0
    assert "HALTED" in capsys.readouterr().out


def test_the_probe_prints_counts_and_no_content(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(im, "CONTACTS_ROOT", contacts(
        tmp_path / "ab", {"first": "Rhonda", "phones": ["555-010-0100"]}))
    src, _ = history(tmp_path, (1, 2))
    assert job.do_probe(src) == 0
    out = capsys.readouterr().out
    assert "messages by service: iMessage 2" in out
    assert "contacts read: 1" in out
    for secret in ("+15550100100", "Rhonda", "day -1"):
        assert secret not in out


def test_the_probe_fails_on_a_changed_schema(tmp_path, capsys):
    import sqlite3
    src, _ = history(tmp_path, (1,))
    raw = sqlite3.connect(src / "chat.db")
    raw.execute("ALTER TABLE message RENAME COLUMN text TO body")
    raw.commit()
    raw.close()
    assert job.do_probe(src) == 1
    assert "MISSING: message.text" in capsys.readouterr().out


def test_the_dry_run_writes_nothing(conn, tmp_path, capsys):
    src, now = history(tmp_path, (1, 2))
    assert job.do_dry_run(conn, now, src_dir=src) == 0
    assert "Nothing was written." in capsys.readouterr().out
    assert conn.execute("SELECT COUNT(*) FROM imessage_messages").fetchone()[0] == 0


def test_backfill_files_old_days_in_batches_and_leaves_yesterday(conn, pipe, tmp_path):
    roots, registry, clf = pipe
    src, now = history(tmp_path, (5, 4, 3, 1))
    filed = bf.backfill(conn, roots, registry, clf, src_dir=src,
                        tmp_root=tmp_path / "tmp", now=now, days=10, batch=2,
                        transcriber=no_speech)
    assert filed == 3
    days = {r[0]: r[1] for r in conn.execute(
        "SELECT day, artifact_id FROM imessage_messages")}
    assert days[i.day_of(now - timedelta(days=1))] is None, "yesterday is the hourly job's"
    assert notify.pending(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM source_checks").fetchone()[0] == 0
    assert list((tmp_path / "tmp").iterdir()) == []


def test_the_backfill_stops_between_batches_when_halted(conn, pipe, tmp_path, monkeypatch):
    roots, registry, clf = pipe
    src, now = history(tmp_path, (5, 4, 3))
    monkeypatch.setattr(bf._halt, "read_flag", lambda: {"ts": "now", "by": "telegram"})
    assert bf.backfill(conn, roots, registry, clf, src_dir=src,
                       tmp_root=tmp_path / "tmp", now=now, days=10, batch=1,
                       transcriber=no_speech) == 1
