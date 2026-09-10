"""Mail into the pipeline.

Two things are being tested and they are not the same thing:

  1. The normaliser's own decisions — what gets rendered, what gets fetched,
     what a hostile filename is allowed to do. Pure functions, no database.
  2. That a message comes out the far end as filed documents with the right
     shape in `artifacts`. That needs the real pipeline, with a stub model.

The Google libraries are never imported here.
"""

import base64
import json
from pathlib import Path

import pytest

from core.adapters import gmail
from core.adapters.gmail import Attachment, GmailClient
from core.db import database as db
from core.pipeline import filing, mail
from core.pipeline.classify import Classifier
from core.pipeline.registry import load_registry

CONFIG = Path(__file__).resolve().parents[1] / "config"


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


# ---------------------------------------------------------------------------
# A filename from a stranger
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("hostile", [
    "../../.ssh/authorized_keys",
    "/etc/passwd",
    "..\\..\\windows\\system32\\evil.dll",
    "....//....//escape.pdf",
])
def test_a_filename_cannot_escape_the_spool(hostile):
    """The sender chooses this string and we are about to create a file with
    it. Rebuilt from an allowlist, not filtered for known-bad."""
    safe = mail.safe_filename(hostile, fallback="x.bin")
    assert "/" not in safe and "\\" not in safe
    assert not safe.startswith(".")
    assert ".." not in safe


def test_a_filename_of_only_punctuation_still_produces_a_name():
    assert mail.safe_filename("...", fallback="fallback.bin") == "fallback.bin"
    assert mail.safe_filename("", fallback="fallback.bin") == "fallback.bin"


def test_an_absurdly_long_filename_is_bounded():
    safe = mail.safe_filename("A" * 5000 + ".pdf", fallback="x.bin")
    assert len(safe) <= 121
    assert safe.endswith(".pdf")


def test_an_ordinary_filename_survives_intact():
    """Mangling every name would make the archive unreadable."""
    assert mail.safe_filename("Invoice_7010-Acme.pdf", "x") == "Invoice_7010-Acme.pdf"


# ---------------------------------------------------------------------------
# Which attachments we go and fetch
# ---------------------------------------------------------------------------

def att(name, size=1000):
    return Attachment(attachment_id="a1", filename=name,
                      mime_type="application/octet-stream", size=size)


def test_a_video_is_not_downloaded_to_be_ocred():
    assert mail._unfetchable(att("holiday.mov")) is not None


def test_an_invoice_pdf_is_fetched():
    assert mail._unfetchable(att("invoice.pdf")) is None


def test_an_oversized_attachment_is_refused_without_reading_it():
    """A malformed size field must not become an unbounded read."""
    reason = mail._unfetchable(att("huge.pdf", size=99 * 1024 * 1024))
    assert "exceeds" in reason


def test_a_zero_byte_attachment_is_reported_not_silently_skipped():
    assert "zero" in mail._unfetchable(att("empty.pdf", size=0))


# ---------------------------------------------------------------------------
# A signature block is not nine documents
#
# Found on the first dry run against the real mailbox, before anything was
# written. Every message Matthew sends carries image001.gif .. image009.png —
# the logo, the social icons, a divider — and Gmail reports each one exactly
# the way it reports an invoice PDF: attachmentId, filename, size. Ingesting
# them would have put nine junk artifacts in the archive per email, each one
# sent to Vision to have a logo OCR'd.
# ---------------------------------------------------------------------------

def part(filename, *, cid=None, disposition=None, mime="image/gif", size=1200):
    headers = [{"name": "Content-Type", "value": f"{mime}; name={filename}"}]
    if cid:
        headers.append({"name": "Content-ID", "value": cid})
    if disposition:
        headers.append({"name": "Content-Disposition",
                        "value": f"{disposition}; filename={filename}"})
    return {"mimeType": mime, "filename": filename, "headers": headers,
            "body": {"attachmentId": f"id-{filename}", "size": size}}


SIGNATURE_MAIL = {
    "id": "sig1", "threadId": "t", "payload": {
        "mimeType": "multipart/related",
        "headers": [
            {"name": "From", "value": '"Matthew R. Epstein" <matthew@mrecai.com>'},
            {"name": "To", "value": "adjuster@statefarm.com"},
            {"name": "Subject", "value": "Re: Policy Notice-State Farm"},
        ],
        "parts": [
            {"mimeType": "text/plain", "headers": [],
             "body": {"data": b64("Attached, as requested.")}},
            part("Requested Document(s) #1.pdf", mime="application/pdf",
                 disposition="attachment", size=88000),
            part("image001.gif", cid="<image001.gif@01DC1234.5678>"),
            part("image002.gif", cid="<image002.gif@01DC1234.5678>"),
            part("image007.png", cid="<image007.png@01DC1234.5678>", mime="image/png"),
        ],
    },
}


def test_a_signature_logo_is_not_an_attachment():
    msg = gmail.parse_message(SIGNATURE_MAIL)
    assert len(msg.attachments) == 4, "the raw parse keeps everything"
    assert [a.filename for a in msg.real_attachments] == [
        "Requested Document(s) #1.pdf"], (
        "nine signature images per email is the difference between an archive "
        "and a junk drawer")


def test_an_inline_image_is_body_content():
    assert gmail.is_inline(part("banner.png", disposition="inline", mime="image/png"))
    assert gmail.is_inline(part("logo.gif", cid="<logo@x>"))


@pytest.mark.parametrize("headers", [
    {"cid": "<doc@01DC1234>"},
    {"disposition": "inline"},
    {"cid": "<doc@01DC1234>", "disposition": "inline"},
])
def test_a_pdf_is_never_body_content_whatever_headers_it_carries(headers):
    """The regression that cost two real documents.

    The first version of is_inline checked only for Content-ID or an inline
    disposition. Outlook sends genuine attachments with both — `inline` there
    means "show this in the reading pane", not "this is decoration" — so
    `Requested Document(s) #1.pdf` and `SIGNATURE PAGE 2022 TOYOTA.pdf`
    vanished from the very next run. A PDF is never a signature logo.
    """
    p = part("Requested Document(s) #1.pdf", mime="application/pdf", **headers)
    assert not gmail.is_inline(p)


@pytest.mark.parametrize("mime,filename", [
    ("application/pdf", "invoice.pdf"),
    ("text/plain", "notes.txt"),
    ("application/vnd.ms-excel", "ledger.xls"),
    ("message/rfc822", "forwarded.eml"),
])
def test_nothing_but_an_image_is_ever_dropped_as_inline(mime, filename):
    assert not gmail.is_inline(
        part(filename, mime=mime, cid="<x@y>", disposition="inline"))


def test_a_part_with_no_disposition_is_treated_as_a_real_attachment():
    """Some senders emit neither header. Filing one stray image costs a
    document in the review queue; the opposite mistake silently drops an
    invoice, and only one of those is recoverable."""
    assert not gmail.is_inline(part("scan.pdf", mime="application/pdf"))
    assert not gmail.is_inline(part("photo.jpg", mime="image/jpeg"))


def test_the_rendered_email_does_not_list_signature_images():
    text = mail.render_message(gmail.parse_message(SIGNATURE_MAIL))
    assert "Attachments: Requested Document(s) #1.pdf" in text
    assert "image001" not in text


def test_inline_images_are_not_reported_as_skipped(tmp_path):
    """They are body content. Nine [SKIPPED] lines per email would bury the
    one that matters."""
    conn = db.connect(tmp_path / "lms.db")
    roots = filing.StorageRoots(archive=tmp_path / "a", originals=tmp_path / "o",
                                quarantine=tmp_path / "q")
    roots.ensure()
    reg = load_registry(CONFIG)
    clf = Classifier(reg, client=StubModel(ANSWER))
    client = GmailClient(FakeTransport(
        {"sig1": SIGNATURE_MAIL},
        {"id-Requested Document(s) #1.pdf": b"%PDF-1.4 not really\n"}))

    res = mail.ingest_message(conn, roots, reg, clf, client,
                              client.fetch("sig1"), spool=tmp_path / "spool")
    assert res.skipped == []
    conn.close()


# ---------------------------------------------------------------------------
# Authentication-Results
# ---------------------------------------------------------------------------

def test_a_hard_spf_fail_is_a_failure():
    h = {"Authentication-Results": "mx.google.com; spf=fail smtp.mailfrom=x.com"}
    assert gmail.auth_results(h)["spf_fail"]


def test_a_softfail_is_not_treated_as_fraud():
    """This mailbox forwards automatically, and forwarding breaks SPF by
    design. Quarantining every softfail would fill the review queue with
    legitimate mail, and a queue nobody reads is worse than a narrower check.
    """
    h = {"Authentication-Results": "mx.google.com; spf=softfail smtp.mailfrom=x.com"}
    assert not gmail.auth_results(h)["spf_fail"]


@pytest.mark.parametrize("verdict", ["neutral", "none", "temperror", "permerror"])
def test_only_a_hard_fail_counts(verdict):
    h = {"Authentication-Results": f"mx.google.com; dkim={verdict}"}
    assert not gmail.auth_results(h)["dkim_fail"]


def test_dmarc_fail_is_read():
    h = {"Authentication-Results":
         "mx.google.com; dkim=pass; spf=pass; dmarc=fail (p=REJECT) header.from=x.com"}
    r = gmail.auth_results(h)
    assert r["dmarc_fail"] and not r["spf_fail"] and not r["dkim_fail"]


def test_only_the_first_authentication_results_header_is_trusted():
    """Anything below the receiving server's own line came in over the wire.
    A sender can write `Authentication-Results: spf=pass` into their message
    and it arrives looking exactly like Google's verdict."""
    h = {"Authentication-Results":
         "mx.google.com; spf=fail\nattacker.example; spf=pass dkim=pass"}
    assert gmail.auth_results(h)["spf_fail"]


def test_a_missing_header_is_not_a_failure():
    """Absence of evidence. Photographed mail and older messages have none."""
    r = gmail.auth_results({})
    assert not any(r[k] for k in ("spf_fail", "dkim_fail", "dmarc_fail"))


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

RAW = {
    "id": "18f2c", "threadId": "t1", "labelIds": ["INBOX"],
    "payload": {
        "mimeType": "multipart/mixed",
        "headers": [
            {"name": "Subject", "value": "Invoice 7010 for August"},
            {"name": "From", "value": "billing@acme-supply.com"},
            {"name": "To", "value": "matthew@mrecai.com"},
            {"name": "Date", "value": "Sat, 06 Sep 2026 09:14:00 -0400"},
            {"name": "Message-ID", "value": "<abc123@acme-supply.com>"},
            {"name": "Authentication-Results",
             "value": "mx.google.com; spf=pass; dkim=pass; dmarc=pass"},
        ],
        "parts": [
            {"mimeType": "text/plain",
             "body": {"data": b64("Please find invoice 7010 attached.\nTOTAL DUE: $3,240.00")}},
            {"mimeType": "application/pdf", "filename": "invoice-7010.pdf",
             "body": {"attachmentId": "att1", "size": 4096}},
        ],
    },
}


def test_the_rendered_email_is_what_a_human_would_print():
    msg = gmail.parse_message(RAW)
    text = mail.render_message(msg)
    assert text.startswith("From: billing@acme-supply.com")
    assert "Subject: Invoice 7010 for August" in text
    assert "Attachments: invoice-7010.pdf" in text
    assert "TOTAL DUE: $3,240.00" in text


def test_the_message_id_is_in_the_rendered_bytes():
    """Identity is the sha256 of this file. Without the Message-ID, two
    different emails that both say "thanks" collapse into one artifact and the
    second is lost permanently; with it, re-polling the same week still
    dedupes because the bytes do not change between polls.
    """
    a = mail.render_message(gmail.parse_message(RAW))
    b = mail.render_message(gmail.parse_message(RAW))
    assert a == b                                    # stable across polls
    assert "<abc123@acme-supply.com>" in a

    other = json.loads(json.dumps(RAW))
    other["payload"]["headers"] = [
        h for h in other["payload"]["headers"] if h["name"] != "Message-ID"
    ] + [{"name": "Message-ID", "value": "<different@acme-supply.com>"}]
    assert mail.render_message(gmail.parse_message(other)) != a


def test_received_chains_are_not_dumped_into_the_document():
    """40 lines of Received: above two lines of text makes the archived
    document unreadable, and for text mail the archived file IS the record."""
    noisy = json.loads(json.dumps(RAW))
    noisy["payload"]["headers"].append(
        {"name": "Received", "value": "from mail-1.acme by mx.google.com; " + "x" * 400})
    text = mail.render_message(gmail.parse_message(noisy))
    assert "Received" not in text


def test_the_classifier_still_sees_every_header():
    """The body is trimmed for the archive; the headers are NOT trimmed for
    the classifier, which needs List-Unsubscribe for the BULK override."""
    reg = load_registry(CONFIG)
    msg = gmail.parse_message(RAW)
    art, _ = mail._artifact_for(msg, reg)
    assert "Authentication-Results" in art.headers
    assert art.recipient == "matthew@mrecai.com"
    assert art.source == "email"
    assert art.received_date == "2026-09-06"


def test_dmarc_none_only_matters_for_a_domain_we_know():
    reg = load_registry(CONFIG)
    known = next(iter({d for e in reg.entities.values() for d in e.domains}))

    stranger = json.loads(json.dumps(RAW))
    stranger["payload"]["headers"] = [
        {"name": "From", "value": "someone@a-domain-we-never-heard-of.example"},
        {"name": "Authentication-Results", "value": "mx.google.com; dmarc=none"},
    ]
    _, auth = mail._artifact_for(gmail.parse_message(stranger), reg)
    assert not auth["dmarc_none_from_known_domain"], (
        "most of the internet has no DMARC policy; flagging all of it is noise")

    impostor = json.loads(json.dumps(stranger))
    impostor["payload"]["headers"][0]["value"] = f"someone@{known}"
    _, auth = mail._artifact_for(gmail.parse_message(impostor), reg)
    assert auth["dmarc_none_from_known_domain"], (
        f"mail claiming to be from {known} with no DMARC policy is forgeable "
        f"by anyone")


# ---------------------------------------------------------------------------
# End to end, with a stub model
# ---------------------------------------------------------------------------

class FakeTransport:
    def __init__(self, messages, attachments=None):
        self._m = messages
        self._a = attachments or {}
        self.attachment_calls = []

    def list_ids(self, query, limit):
        return list(self._m)[:limit]

    def get(self, message_id):
        return self._m[message_id]

    def get_attachment(self, message_id, attachment_id):
        self.attachment_calls.append((message_id, attachment_id))
        return self._a[attachment_id]


class StubModel:
    """Answers whatever the test told it to. The model is not what is under
    test here — the wiring around it is."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = 0

    def complete(self, **kw):
        self.calls += 1
        payload = json.dumps(self.answer)
        return type("C", (), {"json": lambda self=None: json.loads(payload),
                              "model": "stub"})()


@pytest.fixture
def pipeline(tmp_path):
    conn = db.connect(tmp_path / "lms.db")
    roots = filing.StorageRoots(
        archive=tmp_path / "archive",
        originals=tmp_path / "_originals",
        quarantine=tmp_path / "quarantine")
    roots.ensure()
    reg = load_registry(CONFIG)
    yield conn, roots, reg, tmp_path
    conn.close()


ANSWER = {
    "domain": "BUSINESS", "entity_id": "B_MRE", "category": "VENDORS",
    "subcategory": "invoices-received", "urgency": "NORMAL", "confidence": 0.9,
    "requires_reply": False, "due_date": None, "counterparty": "Acme Supply",
    "descriptor": "invoice 7010", "amount_cents": 324000, "currency": "USD",
    "rationale": "an invoice addressed to us",
}


def test_an_email_and_its_attachment_are_two_artifacts(pipeline, monkeypatch):
    conn, roots, reg, tmp = pipeline
    answer = dict(ANSWER)
    answer["entity_id"] = sorted(
        e for e in reg.entities if e not in {"UNASSIGNED"})[0]
    # Whatever entity the registry actually has — this test is about SHAPE,
    # not about the taxonomy, and hard-coding an id here would break the next
    # time entities.yaml is edited.
    answer["category"] = sorted(reg.categories_for(answer["entity_id"]))[0]
    answer["subcategory"] = None

    clf = Classifier(reg, client=StubModel(answer))
    transport = FakeTransport({"18f2c": RAW}, {"att1": b"INVOICE 7010\nTOTAL: $3,240.00\n"})
    client = GmailClient(transport)

    # The PDF suffix would send it to Vision, which does not exist here.
    raw = json.loads(json.dumps(RAW))
    raw["payload"]["parts"][1]["filename"] = "invoice-7010.txt"
    transport._m["18f2c"] = raw

    msg = client.fetch("18f2c")
    res = mail.ingest_message(conn, roots, reg, clf, client, msg,
                              spool=tmp / "spool")

    assert res.email.status == "FILED", res.email.reason
    assert len(res.attachments) == 1
    assert res.attachments[0].status == "FILED", res.attachments[0].reason

    rows = conn.execute(
        "SELECT source, parent_id FROM artifacts ORDER BY id").fetchall()
    assert [r["source"] for r in rows] == ["email", "attachment"]
    assert rows[0]["parent_id"] is None
    assert rows[1]["parent_id"] == 1, "the invoice must remember its email"


def test_the_attachment_does_not_inherit_the_covering_note(pipeline):
    """"Please see the attached ATLASE invoice" must not classify a document
    that is nothing of the sort — and passing the mail body down would also
    defeat D-024's emptiness check by handing ingest a body it never read from
    that file.

    Asserted on what reaches the ARCHIVE, not on an internal variable: the
    filed attachment must contain the attachment's bytes and only those.
    """
    conn, roots, reg, tmp = pipeline
    clf = Classifier(reg, client=StubModel(ANSWER))
    raw = json.loads(json.dumps(RAW))
    raw["payload"]["parts"][1]["filename"] = "receipt.txt"
    transport = FakeTransport(
        {"18f2c": raw}, {"att1": b"RECEIPT 88 paid in full thank you\n"})
    client = GmailClient(transport)

    res = mail.ingest_message(conn, roots, reg, clf, client,
                              client.fetch("18f2c"), spool=tmp / "spool")

    filed = res.attachments[0].path.read_text(encoding="utf-8")
    assert "RECEIPT 88" in filed
    assert "invoice 7010 attached" not in filed, (
        "the covering note leaked into the attachment's document")
    assert "Message-ID" not in filed, "mail headers leaked into the attachment"


def test_an_attachment_gmail_returns_empty_is_visible_not_dropped(pipeline):
    """The declared size said 4096 and the fetch returned nothing. That is an
    anomaly worth a human's two seconds — D-030's lesson was that a zero-byte
    file skipped quietly looks exactly like a file that never arrived.
    """
    conn, roots, reg, tmp = pipeline
    clf = Classifier(reg, client=StubModel(ANSWER))
    raw = json.loads(json.dumps(RAW))
    raw["payload"]["parts"][1]["filename"] = "receipt.txt"
    client = GmailClient(FakeTransport({"m": raw}, {"att1": b""}))

    res = mail.ingest_message(conn, roots, reg, clf, client,
                              client.fetch("m"), spool=tmp / "spool")

    assert res.attachments[0].status == "QUARANTINED"
    assert "empty" in res.attachments[0].reason


def test_a_zero_byte_attachment_is_never_fetched_at_all(pipeline):
    """When Gmail itself says the size is 0, there is nothing to ask for."""
    conn, roots, reg, tmp = pipeline
    clf = Classifier(reg, client=StubModel(ANSWER))
    raw = json.loads(json.dumps(RAW))
    raw["payload"]["parts"][1].update({"filename": "receipt.txt"})
    raw["payload"]["parts"][1]["body"]["size"] = 0
    transport = FakeTransport({"m": raw}, {})
    client = GmailClient(transport)

    res = mail.ingest_message(conn, roots, reg, clf, client,
                              client.fetch("m"), spool=tmp / "spool")

    assert res.attachments == []
    assert any("zero" in s for s in res.skipped)
    assert transport.attachment_calls == []


def test_one_bad_attachment_does_not_lose_the_others(pipeline):
    conn, roots, reg, tmp = pipeline
    clf = Classifier(reg, client=StubModel(ANSWER))
    raw = json.loads(json.dumps(RAW))
    raw["payload"]["parts"] = [
        raw["payload"]["parts"][0],
        {"mimeType": "video/quicktime", "filename": "site-visit.mov",
         "body": {"attachmentId": "big", "size": 90_000_000}},
        {"mimeType": "text/plain", "filename": "notes.txt",
         "body": {"attachmentId": "ok", "size": 30}},
    ]
    transport = FakeTransport({"m": raw}, {"ok": b"a short note about the job\n"})
    client = GmailClient(transport)

    res = mail.ingest_message(conn, roots, reg, clf, client,
                              client.fetch("m"), spool=tmp / "spool")

    assert len(res.skipped) == 1 and "site-visit.mov" in res.skipped[0]
    assert len(res.attachments) == 1
    assert transport.attachment_calls == [("18f2c", "ok")]


def test_polling_twice_files_nothing_the_second_time(pipeline):
    """What makes a missed run harmless: widen the window and run again."""
    conn, roots, reg, tmp = pipeline
    clf = Classifier(reg, client=StubModel(ANSWER))
    raw = json.loads(json.dumps(RAW))
    raw["payload"]["parts"] = [raw["payload"]["parts"][0]]
    client = GmailClient(FakeTransport({"m": raw}))

    first = mail.poll(conn, roots, reg, clf, client, spool=tmp / "spool")
    second = mail.poll(conn, roots, reg, clf, client, spool=tmp / "spool")

    assert first[0].email.status in {"FILED", "QUARANTINED"}
    assert second[0].email.status in {"DUPLICATE", "QUARANTINED"}
    assert conn.execute(
        "SELECT COUNT(*) c FROM artifacts").fetchone()["c"] == 1
