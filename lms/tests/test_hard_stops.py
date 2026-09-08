"""The rules that must hold even when the AI is wrong or manipulated.

These are the tests that matter at 2am. Each one asserts something that is
enforced by code or schema, not by a prompt — spec §8.4, "enforce below the
agent". A prompt-level rule is a suggestion; these are not.
"""

from pathlib import Path

import pytest
import yaml

from core.db import database as db

CONFIG = Path(__file__).resolve().parents[1] / "config"
CORE = Path(__file__).resolve().parents[1] / "core"


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "lms.db")
    yield c
    c.close()


@pytest.fixture
def rules():
    return yaml.safe_load((CONFIG / "rules.yaml").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# The append-only audit log
# ---------------------------------------------------------------------------

def test_actions_log_cannot_be_updated(conn):
    db.log_action(conn, "FILED", detail="original")
    with pytest.raises(Exception, match="append-only"):
        conn.execute("UPDATE actions_log SET detail = 'tampered' WHERE id = 1")


def test_actions_log_cannot_be_deleted(conn):
    db.log_action(conn, "FILED", detail="keep me")
    with pytest.raises(Exception, match="append-only"):
        conn.execute("DELETE FROM actions_log WHERE id = 1")


def test_declining_to_act_is_still_logged(conn):
    """A phishing email that produced no action must leave a trace.

    Otherwise 'nothing happened' and 'the system never saw it' look identical
    in the record.
    """
    db.log_action(conn, "PHISHING_BLOCKED", detail="lookalike domain, no action taken")
    row = conn.execute("SELECT action FROM actions_log").fetchone()
    assert row["action"] == "PHISHING_BLOCKED"


# ---------------------------------------------------------------------------
# Config-level guarantees
# ---------------------------------------------------------------------------

def test_all_hard_stops_are_on(rules):
    stops = rules["hard_stops"]
    expected = [
        "never_delete_email", "never_mark_read", "never_delete_file",
        "never_send_without_approval", "never_move_money", "never_sign_or_bind",
        "never_touch_non_lms_labels", "never_click_links_in_message_bodies",
    ]
    for key in expected:
        assert stops.get(key) is True, f"hard stop {key} is not enabled"


def test_unsubscribe_is_header_only(rules):
    """Body links in marketing mail are attacker-controlled."""
    unsub = rules["unsubscribe"]
    assert unsub["method"] == "list_unsubscribe_header_only"
    assert unsub["require_authenticated_sender"] is True
    assert unsub["never_click_body_links"] is True


def test_classifier_has_no_tools(rules):
    """The component that reads hostile text must have nothing to act with."""
    assert rules["sanitisation"]["classifier_tools"] == []


def test_untrusted_content_is_wrapped(rules):
    s = rules["sanitisation"]
    assert s["wrapper_open"] and s["wrapper_close"]
    assert s["strip_remote_images"] is True, "tracking pixels would confirm the address"


def test_schedules_are_named_timezone_not_offset(rules):
    """DST safety, Rec 21. A UTC offset silently drifts an hour twice a year."""
    tz = rules["notifications"]["timezone"]
    assert tz == "America/New_York"
    assert not tz.startswith(("+", "-", "UTC"))


def test_critical_and_phishing_bypass_the_interrupt_cap(rules):
    bypass = rules["notifications"]["bypass_cap_for"]
    assert "CRITICAL" in bypass
    assert "SUSPECTED_PHISHING" in bypass


# ---------------------------------------------------------------------------
# No code path that sends or deletes
# ---------------------------------------------------------------------------

FORBIDDEN_CALLS = [
    "smtplib",           # no SMTP client anywhere in core/
    "shutil.move",       # we copy; the source is never ours to move
    "shutil.rmtree",
    "os.remove",
    "os.unlink",
    "Path.unlink",
]


def test_core_contains_no_send_or_delete_primitives():
    """Grep-level guard. Crude, and that is the point.

    If someone adds a delete to core/ this fails in CI before it ever reaches
    the Mac. Adapters that legitimately need a mail client will live in
    core/adapters/ with their own reviewed exception — and adding one here
    should require deleting a line from this test, deliberately.
    """
    offenders = []
    for path in CORE.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in FORBIDDEN_CALLS:
            if needle in text:
                offenders.append(f"{path.name}: {needle}")
    assert not offenders, f"forbidden primitives found: {offenders}"
