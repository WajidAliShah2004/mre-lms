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


# --- Sept 29: 403 rateLimitExceeded on a 14-day catch-up ---------------------

class _Resp:
    def __init__(self, status): self.status = status


class _FakeHttpError(Exception):
    def __init__(self, status, text):
        super().__init__(text); self.resp = _Resp(status)


def test_rate_limit_is_retried_then_succeeds():
    from core.adapters.gmail import _with_backoff
    calls, waits = [], []
    def call():
        calls.append(1)
        if len(calls) < 3:
            raise _FakeHttpError(403, "Quota exceeded for quota metric ... rateLimitExceeded")
        return {"ok": True}
    assert _with_backoff(call, sleep=waits.append) == {"ok": True}
    assert waits == [2.0, 4.0]


def test_permission_403_is_not_retried():
    import pytest
    from core.adapters.gmail import _with_backoff
    waits = []
    def call():
        raise _FakeHttpError(403, "Request had insufficient authentication scopes.")
    with pytest.raises(_FakeHttpError):
        _with_backoff(call, sleep=waits.append)
    assert waits == []


def test_gives_up_after_tries():
    import pytest
    from core.adapters.gmail import _with_backoff
    waits = []
    def call():
        raise _FakeHttpError(429, "Too many requests")
    with pytest.raises(_FakeHttpError):
        _with_backoff(call, tries=3, sleep=waits.append)
    assert waits == [2.0, 4.0]


# --- Oct 3: the backoff was silent, so it looked hung and was Ctrl-C'd -------

def test_backoff_says_it_is_waiting():
    from core.adapters.gmail import _with_backoff
    calls, notes = [], []
    def call():
        calls.append(1)
        if len(calls) < 2:
            raise _FakeHttpError(429, "Too many requests")
        return 1
    _with_backoff(call, sleep=lambda s: None, note=notes.append)
    assert len(notes) == 1
    assert "rate limit" in notes[0] and "2s" in notes[0]


def test_backoff_outlasts_a_per_minute_quota_by_default():
    # The quota is "units per minute per user". Giving up before a full
    # minute has passed gives up before the quota can possibly have reset.
    import pytest
    from core.adapters.gmail import _with_backoff
    waits = []
    def call():
        raise _FakeHttpError(429, "Too many requests")
    with pytest.raises(_FakeHttpError):
        _with_backoff(call, sleep=waits.append, note=lambda m: None)
    assert sum(waits) > 120


def test_pacer_spaces_calls_out():
    from core.adapters.gmail import _Pacer
    now = [0.0]
    slept = []
    def sleep(s):
        slept.append(round(s, 3)); now[0] += s
    p = _Pacer(min_interval=0.1, clock=lambda: now[0], sleep=sleep)
    p.wait(); p.wait(); p.wait()
    assert slept == [0.1, 0.1]
    now[0] += 5                     # a long gap needs no wait
    p.wait()
    assert slept == [0.1, 0.1]


def test_a_network_timeout_is_retried():
    import socket
    from core.adapters.gmail import _with_backoff
    calls = []
    def call():
        calls.append(1)
        if len(calls) < 2:
            raise socket.timeout("timed out")
        return "ok"
    assert _with_backoff(call, sleep=lambda s: None, note=lambda m: None) == "ok"


# --- Oct 3: is mail arriving direct, and landing in the inbox? ---------------

def _raw(received, labels=("INBOX",)):
    return {"labelIds": list(labels), "payload": {"headers": [
        {"name": "Received", "value": v} for v in received]}}


def test_direct_delivery_is_recognised():
    from core.adapters.gmail import delivery_route
    raw = _raw(["by 2002:a05:6a10 with SMTP id x; Fri, 3 Oct 2026",
                "from mail-sor-f41.google.com (mail-sor-f41.google.com.) "
                "by mx.google.com with SMTPS id y"])
    assert delivery_route(raw) == "direct"


def test_mail_forwarded_by_icloud_is_recognised():
    from core.adapters.gmail import delivery_route
    raw = _raw(["by 2002:a05 with SMTP id x",
                "from p00-icloudmta-asmtp-us-west-1a-100-percent-7.p00-icloudmta"
                "-asmtp-vip.icloud-mail-production.svc.kube.us-west-1a.k8s.cloud"
                ".apple.com by mx.google.com",
                "from mx01.mail.icloud.com by ms01.mail.icloud.com"])
    assert delivery_route(raw) == "via iCloud"


def test_inbox_state():
    from core.adapters.gmail import inbox_state
    assert inbox_state(_raw([], ["INBOX", "UNREAD"])) == "inbox"
    assert inbox_state(_raw([], ["SPAM"])) == "SPAM"
    assert inbox_state(_raw([], ["CATEGORY_UPDATES"])) == "not in inbox"
