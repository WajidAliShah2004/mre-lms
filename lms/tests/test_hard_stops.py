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

# ---------------------------------------------------------------------------
# What actually enforces each hard stop
#
# `rules.yaml` declares eight of them. `grep -rn hard_stops core/ ops/` returns
# ZERO — no code reads that block. The test here used to load the file and
# assert it said `true` eight times, which is a mirror: a config asserting
# itself. Passing meant only that someone had typed the word.
#
# The stops do hold today, but by ABSENCE — there is no smtplib, no imaplib,
# no payment client, no e-signature client anywhere in core/. That is the
# strongest form of guarantee available ("there is no code path") and the most
# fragile, because it lasts exactly until someone builds the feature. On Day 3
# an email adapter arrives, and `never_delete_email: true` becomes a sentence
# in a file with a green test beside it and nothing behind it.
#
# So each stop names its enforcement. STRUCTURAL entries carry the import that
# would make the stop violable; when one of those appears in core/, the test
# below FAILS and says the stop must now be enforced properly. That converts
# "we will remember" into "the suite stops you".
# ---------------------------------------------------------------------------

TESTED, STRUCTURAL = "TESTED", "STRUCTURAL"

ENFORCEMENT = {
    "never_delete_file": (
        TESTED, "test_core_contains_no_send_or_delete_primitives greps core/ "
                "for os.remove, unlink, rmtree and rename"),
    "never_send_without_approval": (
        TESTED, "smtplib is in FORBIDDEN_CALLS, so any SMTP client in core/ "
                "fails the grep guard above"),
    "never_click_links_in_message_bodies": (
        TESTED, "sanitise strips remote images (test_html_is_stripped_and_"
                "remote_images_dropped) and every model call goes through "
                "assert_loopback (test_loopback_is_enforced)"),

    # These three were STRUCTURAL — held only by there being no mail client in
    # core/ — until Sept 10, when adapters/gmail.py arrived and this test
    # failed, naming all three. That is what it was built to do.
    #
    # They are now enforced by the CREDENTIAL rather than by code: the adapter
    # requests gmail.readonly and nothing else, so the token Google issues
    # cannot delete a message, cannot set a flag, and cannot write a label. A
    # bug in adapters/gmail.py cannot reach past that, which is a stronger
    # guarantee than any assertion about our own behaviour.
    #
    # Widening SCOPES to add labels or drafts moves them back into our hands.
    # test_gmail.py::test_widening_the_scope_is_not_a_quiet_change is the
    # thing that makes that a decision rather than an edit.
    "never_delete_email": (
        TESTED, "gmail.SCOPES is read-only; test_the_scope_cannot_delete_or_"
                "write asserts no write scope is requested, so Google refuses "
                "the operation rather than the LMS declining to attempt it"),
    "never_mark_read": (
        TESTED, "gmail.readonly cannot set flags, and unlike IMAP a Gmail API "
                "fetch does not mark a message seen as a side effect — his "
                "unread count is untouched by the LMS reading his mail"),
    "never_touch_non_lms_labels": (
        TESTED, "gmail.readonly cannot write any label, LMS-prefixed or not; "
                "test_no_write_endpoint_is_called pins that no modify, trash "
                "or send endpoint is reachable from core/"),
    "never_move_money": (
        STRUCTURAL, ("stripe", "plaid", "braintree", "paypal")),
    "never_sign_or_bind": (
        STRUCTURAL, ("docusign", "hellosign", "adobesign", "esignature")),
}


def test_every_hard_stop_is_still_declared(rules):
    """The flags themselves. Necessary, and nowhere near sufficient."""
    stops = rules["hard_stops"]
    for key in ENFORCEMENT:
        assert stops.get(key) is True, f"hard stop {key} is not enabled"


def test_every_declared_stop_names_how_it_is_enforced(rules):
    """A stop in rules.yaml with no entry above is a stop nothing implements.

    Adding one to the config therefore forces the question here, rather than
    letting it be answered implicitly by a test that reads the config back.
    """
    undeclared = sorted(set(rules["hard_stops"]) - set(ENFORCEMENT))
    assert not undeclared, (
        f"these hard stops are declared in rules.yaml and nothing here says "
        f"what enforces them: {undeclared}")

    stale = sorted(set(ENFORCEMENT) - set(rules["hard_stops"]))
    assert not stale, f"enforcement claimed for stops that no longer exist: {stale}"


def test_a_stop_held_only_by_absence_fails_when_the_feature_arrives():
    """The test that matters on Day 3.

    `never_delete_email` is currently true because nothing in core/ can touch
    a mailbox. The moment an email adapter lands, that stops being a guarantee
    and becomes a claim — and this fails, naming the stop that now needs real
    enforcement rather than letting a green suite imply one exists.
    """
    sources = {p: p.read_text(encoding="utf-8") for p in CORE.rglob("*.py")}

    now_violable = {}
    for stop, (kind, detail) in ENFORCEMENT.items():
        if kind != STRUCTURAL:
            continue
        found = sorted({p.name for p, text in sources.items()
                        for marker in detail if marker in text})
        if found:
            now_violable[stop] = found

    assert not now_violable, (
        "these hard stops were held only by the absence of the code that could "
        "break them, and that code now exists. Enforce them properly and move "
        f"them to TESTED: {now_violable}")


def test_no_enforcement_claim_is_empty():
    """A justification of three words is not a justification."""
    for stop, (kind, detail) in ENFORCEMENT.items():
        if kind == TESTED:
            assert len(detail) > 40, f"{stop}: enforcement claim is too thin"
        else:
            assert detail, f"{stop}: no markers, so nothing would ever fire"


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
    "shutil.move",       # we copy; the source is usually not ours to move
    "shutil.rmtree",
    "os.remove",
    "os.unlink",
    "Path.unlink",
    ".rename(",          # a rename is a move wearing a different name
]

# Named exemptions. Each one is a decision, and the reason lives here rather
# than in a comment at the call site, so that adding another requires editing
# this test on purpose.
#
# The rule this protects is "never delete the user's data", not "never touch a
# file". A move that provably destroys nothing is allowed; a move that MIGHT
# is not.
EXEMPT = {
    ("corrections.py", "shutil.move"): (
        "Re-files a document Matthew has said is in the wrong place. The copy "
        "in the archive tree is DERIVED — file_artifact keeps the incoming "
        "bytes under _originals/<sha256> and never writes there again — so "
        "moving it destroys nothing; the original is intact throughout and "
        "the move is within one filesystem. Refusing would leave a document "
        "filed under the wrong business permanently, which is the failure the "
        "system exists to prevent. It refuses rather than overwrites, and "
        "nothing is deleted."
    ),
    ("filing.py", "shutil.move"): (
        "retire_quarantine_copy moves a document out of the review queue once "
        "it has actually FILED, into quarantine/_resolved. By the time this "
        "runs the bytes exist in TWO other places — the archive copy written "
        "moments earlier and _originals/<sha256>, which is never rewritten — "
        "so this copy is redundant, not precious, and it stays inside the "
        "quarantine tree rather than being removed. The alternative is a "
        "review queue that describes Tuesday: a GEICO insurance card sitting "
        "there labelled 'most likely a blank scan' while the same document is "
        "correctly filed in FINANCE, with nothing to tell a reviewer which is "
        "true. A queue that lies about resolved work is a queue nobody trusts."
    ),
    ("watchfolder.py", "shutil.move"): (
        "Retires a handled file from the iCloud inbox to inbox/_done. By the "
        "time this runs the bytes exist in TWO other places — the archive and "
        "_originals — so the inbox copy is redundant, not precious. It stays "
        "inside the inbox tree and is never removed. The alternative is "
        "leaving it to be rescanned forever, which grows the scan cost without "
        "bound and re-OCRs the same document nightly."
    ),
}


def test_core_contains_no_send_or_delete_primitives():
    """Grep-level guard. Crude, and that is the point.

    If someone adds a delete to core/ this fails before it reaches the Mac.
    Exemptions are possible but must be argued in EXEMPT above — which is the
    whole mechanism: the cost of an exception is writing down why.
    """
    offenders = []
    for path in CORE.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in FORBIDDEN_CALLS:
            if needle in text and (path.name, needle) not in EXEMPT:
                offenders.append(f"{path.name}: {needle}")
    assert not offenders, f"forbidden primitives found: {offenders}"


def test_every_exemption_is_still_used():
    """A stale exemption is a hole nobody is watching.

    If the code that needed an exception is gone, the exception should go too
    — otherwise it silently permits a future call that was never argued for.
    """
    for (filename, needle), reason in EXEMPT.items():
        matches = [p for p in CORE.rglob(filename)]
        assert matches, f"exemption for {filename} but the file is gone"
        assert needle in matches[0].read_text(encoding="utf-8"), (
            f"exemption ({filename}, {needle}) is no longer used — remove it")
        assert len(reason) > 80, "an exemption needs a real justification"
