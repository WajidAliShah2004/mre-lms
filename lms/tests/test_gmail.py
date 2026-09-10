"""The Gmail adapter.

The first four tests are the ones that matter. Three of the eight hard stops
in rules.yaml are enforced by the SCOPE this module asks for, not by anything
it does — so the scope is the security boundary, and these assert it.

Everything after that is parsing, tested against fixtures shaped like real
Gmail API responses so the module can be exercised on a machine with no Google
libraries and no credentials.
"""

import base64

import pytest

from core.adapters import gmail


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


# ---------------------------------------------------------------------------
# The scope IS the security boundary
# ---------------------------------------------------------------------------

def test_the_scope_cannot_delete_or_write():
    """never_delete_email, never_mark_read, never_touch_non_lms_labels.

    Until Sept 10 these held because no mail client existed in core/. Now they
    hold because the token Google issues cannot perform the operation — a bug
    in gmail.py cannot reach past a scope it was never granted.
    """
    assert gmail.SCOPES == (
        "https://www.googleapis.com/auth/gmail.readonly",)

    for scope in gmail.WRITE_SCOPES:
        assert scope not in gmail.SCOPES, (
            f"{scope} would let the LMS change or destroy mail")


def test_widening_the_scope_is_not_a_quiet_change():
    """A read-only token makes three hard stops Google's problem rather than
    ours. Adding `gmail.modify` for labels, or `gmail.compose` for drafts,
    hands them back — and this test is what makes that a decision someone
    argued for rather than a line someone edited.

    If you are here because you added a scope: the three stops in
    test_hard_stops.py's ENFORCEMENT table must move back from TESTED, with
    real code-level enforcement, before this assertion is relaxed.
    """
    assert len(gmail.SCOPES) == 1, (
        "SCOPES grew. See test_hard_stops.py — never_delete_email, "
        "never_mark_read and never_touch_non_lms_labels are currently "
        "enforced by this being read-only")


def test_no_write_endpoint_is_reachable_from_the_adapter():
    """Grep-level, deliberately crude. The scope already forbids these; this
    catches the case where someone widens the scope and the calls are already
    written and waiting."""
    from pathlib import Path
    source = (Path(gmail.__file__)).read_text(encoding="utf-8")
    for forbidden in (".trash(", ".delete(", ".send(", ".modify(",
                      ".batchDelete(", ".batchModify(", ".insert("):
        assert forbidden not in source, f"{forbidden} in the Gmail adapter"


def test_credentials_never_inherit_a_wider_grant(monkeypatch):
    """If the stored token was granted more than SCOPES — an older
    authorisation, a hand-edited Keychain item — the client asks for less
    rather than quietly using more."""
    import inspect
    source = inspect.getsource(gmail.credentials_for)
    assert "scopes=list(SCOPES)" in source


# ---------------------------------------------------------------------------
# Reading a message
# ---------------------------------------------------------------------------

PLAIN = {
    "id": "18f", "threadId": "t1", "labelIds": ["INBOX", "UNREAD"],
    "payload": {
        "mimeType": "text/plain",
        "headers": [
            {"name": "Subject", "value": "Invoice 7010"},
            {"name": "From", "value": "billing@acme-supply.com"},
            {"name": "To", "value": "matthew@mrecai.com"},
            {"name": "Date", "value": "Sat, 06 Sep 2026 09:14:00 -0400"},
        ],
        "body": {"data": b64("TOTAL DUE: $3,240.00")},
    },
}


def test_a_plain_message_is_flattened():
    m = gmail.parse_message(PLAIN)
    assert m.subject == "Invoice 7010"
    assert m.sender == "billing@acme-supply.com"
    assert m.recipient == "matthew@mrecai.com"
    assert "3,240.00" in m.body
    assert m.date.startswith("2026-09-06")
    assert m.sender_domain == "acme-supply.com"


def test_plain_text_is_preferred_over_html():
    """A multipart/alternative carries the same content twice, and the plain
    part has already had the markup, the tracking pixels and the mismatched
    link text removed by the sender's own client."""
    msg = {
        "id": "1", "threadId": "t", "payload": {
            "mimeType": "multipart/alternative",
            "headers": [],
            "parts": [
                {"mimeType": "text/plain", "body": {"data": b64("the plain one")}},
                {"mimeType": "text/html",
                 "body": {"data": b64("<p>the html one</p>")}},
            ]}}
    body, from_html = gmail.extract_body(msg["payload"])
    assert body == "the plain one"
    assert not from_html


def test_html_is_used_when_there_is_no_plain_part():
    msg = {"payload": {"mimeType": "text/html", "headers": [],
                       "body": {"data": b64("<p>only html</p>")}}}
    body, from_html = gmail.extract_body(msg["payload"])
    assert "only html" in body
    assert from_html


def test_attachments_are_found_at_any_depth():
    """Real mail nests. An invoice PDF three parts down is still an invoice."""
    msg = {"payload": {"mimeType": "multipart/mixed", "headers": [], "parts": [
        {"mimeType": "multipart/related", "parts": [
            {"mimeType": "text/plain", "body": {"data": b64("see attached")}},
            {"mimeType": "application/pdf", "filename": "invoice.pdf",
             "body": {"attachmentId": "att1", "size": 90210}},
        ]},
    ]}}
    atts = gmail.extract_attachments(msg["payload"])
    assert [a.filename for a in atts] == ["invoice.pdf"]
    assert atts[0].size == 90210


def test_an_attachment_is_not_mistaken_for_the_body():
    msg = {"payload": {"mimeType": "multipart/mixed", "headers": [], "parts": [
        {"mimeType": "text/plain", "body": {"data": b64("real body")}},
        {"mimeType": "text/plain", "filename": "notes.txt",
         "body": {"attachmentId": "a1", "size": 12, "data": b64("attached text")}},
    ]}}
    body, _ = gmail.extract_body(msg["payload"])
    assert body == "real body"
    assert "attached" not in body


def test_an_unparseable_date_becomes_empty_not_today():
    """A fabricated timestamp flows into the filename (D-007) and files the
    document under a day it has nothing to do with. Empty lets the existing
    fallback decide."""
    assert gmail._iso("not a date") == ""
    assert gmail._iso("") == ""


def test_a_naive_date_is_not_silently_local():
    iso = gmail._iso("Sat, 06 Sep 2026 09:14:00")
    assert iso.endswith("+00:00"), iso


def test_undecodable_body_does_not_raise():
    """A malformed message must reach the review queue, not crash the poll and
    stop every message behind it."""
    msg = {"payload": {"mimeType": "text/plain", "headers": [],
                       "body": {"data": "!!!not base64!!!"}}}
    body, _ = gmail.extract_body(msg["payload"])
    assert body == ""


# ---------------------------------------------------------------------------
# The client, against a fake transport
# ---------------------------------------------------------------------------

class FakeTransport:
    def __init__(self, messages: dict):
        self._m = messages
        self.queries: list[str] = []

    def list_ids(self, query, limit):
        self.queries.append(query)
        return list(self._m)[:limit]

    def get(self, message_id):
        return self._m[message_id]


def test_recent_asks_gmail_for_a_window():
    t = FakeTransport({"18f": PLAIN})
    msgs = gmail.GmailClient(t).recent(newer_than_days=2)
    assert t.queries == ["newer_than:2d"]
    assert [m.subject for m in msgs] == ["Invoice 7010"]


def test_an_extra_query_is_combined_not_replaced():
    t = FakeTransport({"18f": PLAIN})
    gmail.GmailClient(t).recent(newer_than_days=1, query="has:attachment")
    assert t.queries == ["newer_than:1d has:attachment"]


def test_the_limit_is_honoured():
    t = FakeTransport({str(i): PLAIN for i in range(50)})
    assert len(gmail.GmailClient(t).recent(limit=5)) == 5


def test_a_missing_token_says_how_to_fix_it(monkeypatch):
    monkeypatch.setattr(gmail.subprocess, "run",
                        lambda *a, **k: type("R", (), {"returncode": 44,
                                                       "stdout": "",
                                                       "stderr": ""})())
    with pytest.raises(gmail.GmailError) as exc:
        gmail.stored_token("matthew@mrecai.com")
    assert "authorise_gmail.py" in str(exc.value)
