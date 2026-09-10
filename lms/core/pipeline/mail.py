"""A mailbox becomes documents.

    Gmail Message  ->  one .eml.txt artifact (the mail itself)
                   ->  one child artifact per attachment (artifacts.parent_id)

Everything after that is the pipeline that already exists. This module's whole
job is to turn a message into things `ingest_file` understands and then get out
of the way — no classifying, no filing, no deciding.

WHY A FILE AT ALL
-----------------
`ingest_file` takes a path, and every guarantee in the system is anchored to
the bytes at that path: dedupe is sha256 of the file, `_originals/<sha256>` is
the copy that is never rewritten, and the archive holds something a human can
open in five years without this codebase. An email that exists only as rows in
SQLite has none of that. So the message is rendered to a text file first, and
from there it is an ordinary document.

The rendering is deliberately plain: a short header block, then the body. It is
what Matthew would see if he printed the email, which is the right standard for
something going into an archive he may read long after the LMS is gone.

DEDUPE, AND WHY THE MESSAGE-ID IS IN THE FILE
--------------------------------------------
Identity is the sha256 of the rendered file, as everywhere else. The rendered
file includes `Message-ID`, which makes it unique per message but IDENTICAL
across polls — so re-polling the same week costs one hash and files nothing,
while two different emails that happen to both say "thanks" stay two
documents. Content hashing alone would collapse those into one and lose the
second permanently.

ATTACHMENTS ARE THE POINT
-------------------------
The invoice is almost never in the body; it is the PDF. Each attachment is
ingested as its own artifact with `parent_id` pointing at the email, so the
archive holds the invoice as a first-class filed document AND remembers which
message carried it. They classify independently: a covering email routes on
`recipient_email` like anything else, and a PDF that OCRs to an ATLASE invoice
files under ATLASE even if the covering note came to the MRECAI address. That
is correct — the invoice is what it is regardless of where it was sent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..adapters import gmail
from ..adapters.gmail import Attachment, GmailClient, Message
from . import filing
from .classify import Artifact, Classifier
from .ingest import IngestResult, ingest_file
from .registry import Registry

# Attachments the pipeline will fetch. Anything else is recorded on the email's
# sidecar and left in Gmail.
#
# Not a security control — it is a "do not download 60MB of video to OCR it"
# control. The security boundary is the read-only scope plus the fact that
# nothing here executes what it downloads.
INGESTIBLE_SUFFIXES = {
    ".pdf", ".png", ".jpg", ".jpeg", ".heic", ".tiff", ".tif",
    ".txt", ".md", ".csv",
}

# 25MB. Gmail's own attachment ceiling is 25MB, so this rejects nothing real;
# it is here so a malformed `size` field cannot ask for an unbounded read.
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024

# Filenames arrive from the sender and are therefore hostile. This is not
# sanitising for display, it is refusing to let a message name a path.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass
class MailResult:
    """What happened to one message."""
    message_id: str
    email: IngestResult | None = None
    attachments: list[IngestResult] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def filed(self) -> int:
        return sum(1 for r in [self.email, *self.attachments]
                   if r is not None and r.status == "FILED")


def safe_filename(name: str, fallback: str) -> str:
    """A filename that cannot escape the directory it is written into.

    `../../.ssh/authorized_keys` is a legal MIME filename. So is one with a
    NUL, a newline, or 4000 characters. The sender chooses this string and we
    are about to create a file with it, so it is rebuilt from an allowlist
    rather than filtered for known-bad — a blocklist here is a bet that we
    thought of every encoding, and we have not.
    """
    name = Path(name or "").name              # strip any directory component
    cleaned = _UNSAFE.sub("_", name).strip("._") or fallback
    stem, dot, suffix = cleaned.rpartition(".")
    if not dot:
        return cleaned[:120]
    return f"{stem[:100]}.{suffix[:20]}"


def render_message(msg: Message) -> str:
    """The email as a plain document.

    Only the headers a human reading the archive would want. The full header
    set goes to the classifier (it needs List-Unsubscribe for the BULK
    override) and to the sidecar, but it does not belong in the body of an
    archived document — 40 lines of Received: chains above two lines of text
    makes the document unreadable, and OCR-free text is the one case where the
    archived file IS the readable record.
    """
    lines = [
        f"From: {msg.sender}",
        f"To: {msg.recipient}",
        f"Date: {msg.date or '(unparseable)'}",
        f"Subject: {msg.subject}",
        f"Message-ID: {msg.headers.get('Message-ID', msg.message_id)}",
    ]
    if msg.attachments:
        names = ", ".join(a.filename for a in msg.attachments)
        lines.append(f"Attachments: {names}")
    return "\n".join(lines) + "\n\n" + (msg.body or "")


def _artifact_for(msg: Message, registry: Registry) -> tuple[Artifact, dict]:
    """The classifier's view of the message, and the auth verdicts."""
    auth = gmail.auth_results(msg.headers)

    # dmarc=none is not a failure in general — most of the internet has no
    # DMARC policy. It is only a signal when the sender CLAIMS to be a domain
    # we know, because for those domains we would expect a policy and its
    # absence means anyone can forge the address.
    known = {d.lower() for e in registry.entities.values() for d in e.domains}
    auth["dmarc_none_from_known_domain"] = bool(
        auth.pop("dmarc_none", False) and msg.sender_domain in known)

    art = Artifact(
        body=render_message(msg),
        subject=msg.subject,
        sender=msg.sender,
        recipient=msg.recipient,
        source="email",
        source_ref=msg.message_id,
        received_date=(msg.date or "")[:10] or None,
        attachments=[a.filename for a in msg.attachments],
        is_html=False,                      # extract_body already un-HTMLed it
        headers=msg.headers,
    )
    return art, auth


def ingest_message(conn, roots: filing.StorageRoots, registry: Registry,
                   classifier: Classifier, client: GmailClient,
                   msg: Message, *, spool: Path,
                   with_attachments: bool = True) -> MailResult:
    """One message, all the way through. Never raises for one bad attachment.

    A single unreadable PDF must not stop the other four attachments, nor the
    email itself, nor the rest of the poll. Each piece is independently
    ingested and independently quarantined.
    """
    result = MailResult(message_id=msg.message_id)
    spool.mkdir(parents=True, exist_ok=True)

    art, auth = _artifact_for(msg, registry)

    # ---- the email itself -------------------------------------------------
    body_path = spool / f"{msg.message_id}.eml.txt"
    body_path.write_text(render_message(msg), encoding="utf-8")

    result.email = ingest_file(
        conn, roots, registry, classifier, body_path,
        source="email", source_ref=msg.message_id,
        artifact=art, auth_results=auth,
    )

    if not with_attachments or not msg.attachments:
        return result

    # An attachment whose parent was quarantined still gets ingested. The
    # covering email being unclassifiable says nothing about the invoice
    # attached to it, and the invoice is usually the document that matters.
    parent_id = _artifact_id(conn, result.email)

    for att in msg.attachments:
        reason = _unfetchable(att)
        if reason:
            result.skipped.append(f"{att.filename}: {reason}")
            continue
        try:
            data = client.attachment_bytes(msg, att)
        except Exception as exc:              # network, or Gmail said no
            result.skipped.append(f"{att.filename}: fetch failed — {exc}")
            continue

        name = safe_filename(att.filename, fallback=f"{att.attachment_id}.bin")
        path = spool / f"{msg.message_id}__{name}"
        path.write_bytes(data)

        result.attachments.append(ingest_file(
            conn, roots, registry, classifier, path,
            source="attachment",
            source_ref=f"{msg.message_id}/{att.attachment_id}",
            # A fresh Artifact, carrying the mail's envelope so routing still
            # works, but NOT the mail's body — the PDF's own OCR text fills
            # that in. Passing the covering note down would let "please see the
            # attached ATLASE invoice" classify a document that is nothing of
            # the sort, and would defeat the emptiness check in ingest_file
            # (D-024) by handing it a body it did not read from this file.
            artifact=Artifact(
                subject=att.filename or msg.subject,
                sender=msg.sender, recipient=msg.recipient,
                source="attachment",
                source_ref=f"{msg.message_id}/{att.attachment_id}",
                received_date=(msg.date or "")[:10] or None,
                headers=msg.headers,
            ),
            auth_results=auth,
            parent_id=parent_id,
        ))

    return result


def _unfetchable(att: Attachment) -> str | None:
    suffix = Path(att.filename or "").suffix.lower()
    if suffix not in INGESTIBLE_SUFFIXES:
        return f"{suffix or 'no extension'} is not a format this pipeline reads"
    if att.size > MAX_ATTACHMENT_BYTES:
        return f"{att.size} bytes exceeds the {MAX_ATTACHMENT_BYTES} limit"
    if att.size == 0:
        return "zero bytes"
    return None


def _artifact_id(conn, res: IngestResult | None) -> int | None:
    """The email's row id, for the attachments to point at.

    Looked up by hash rather than returned by ingest_file: a QUARANTINED email
    has a row but no FilingResult, and its attachments should still record
    which message they arrived on.
    """
    if res is None:
        return None
    from ..db import database as db
    row = db.find_artifact_by_hash(conn, res.sha256)
    return int(row["id"]) if row else None


def poll(conn, roots: filing.StorageRoots, registry: Registry,
         classifier: Classifier, client: GmailClient, *, spool: Path,
         newer_than_days: int = 1, limit: int = 100) -> list[MailResult]:
    """One pass over the mailbox. Safe to run again immediately.

    Re-running costs a hash per message and files nothing new, because dedupe
    is on the rendered bytes and those do not change between polls. That is
    what makes a missed run harmless: widen the window and run again.
    """
    out = []
    for msg in client.recent(newer_than_days=newer_than_days, limit=limit):
        out.append(ingest_message(conn, roots, registry, classifier, client,
                                  msg, spool=spool))
    return out
