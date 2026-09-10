"""Read Matthew's mailbox. Read, and nothing else.

WHY THE GMAIL API AND NOT IMAP
------------------------------
Google stopped accepting legacy passwords for IMAP on 14 March 2025, so the
Aug 5 plan — share the password, turn 2FA off — cannot work at all now. The
remaining routes are an app password (which requires 2-Step Verification) or
OAuth. This is OAuth, via an **internal** Workspace app: no verification, no
CASA assessment, no service-account key with domain-wide reach, and no 2SV.

The API is also simply better suited than IMAP for this job:

  * labels are native, where IMAP maps them onto folders awkwardly
  * fetching a message does NOT set \\Seen — over IMAP it does unless every
    call remembers to say otherwise, and forgetting once is visible in his
    unread count
  * attachments come back addressable, so each becomes an artifact in its own
    right (schema: artifacts.parent_id)

THE SCOPE IS THE SECURITY BOUNDARY
----------------------------------
This module requests exactly one scope: gmail.readonly.

Three of the eight hard stops in rules.yaml stop being promises and become
refusals from Google:

    never_delete_email          the token cannot delete
    never_mark_read             reads do not mark, and the token cannot write
    never_touch_non_lms_labels  the token cannot write labels at all

That is stronger than code can be. D-036 recorded those three as held only by
the absence of a mail client in core/; adding one would have made them claims
again. Instead the guarantee moves to the credential, where a bug in this file
cannot reach it.

When labels and draft replies arrive they need a wider scope, and at that
point the code-level stops matter again. Widening SCOPES is therefore a
deliberate act with a test in the way — see tests/test_gmail.py.

NOTHING HERE TOUCHES THE NETWORK IN A TEST
------------------------------------------
The Google client libraries are imported lazily and the transport is
injectable, so the whole module is exercised against a fake. The pattern is
ocr.py's: the machine that builds this is not the machine that runs it, and a
module that can only be tested with live credentials is a module that stops
being tested.
"""

from __future__ import annotations

import base64
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Iterable, Protocol

# The whole security posture, in one tuple. Read-only. Nothing else.
#
# `gmail.modify` would permit trashing a message; `gmail.compose` permits
# sending. Neither is needed to read, classify and file, which is the entire
# Day-3 job. Adding to this list widens what a stolen token can do, so the
# test suite treats it as a change worth arguing for rather than a detail.
SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/gmail.readonly",
)

# Scopes that would let the LMS change or destroy mail. Named so the test can
# assert their absence by intent rather than by matching strings.
WRITE_SCOPES = (
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.settings.basic",
    "https://www.googleapis.com/auth/gmail.settings.sharing",
    "https://mail.google.com/",
)

KEYCHAIN_PREFIX = "lms/gmail-oauth/"


class GmailError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# What a message looks like once we are done with it
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Attachment:
    attachment_id: str
    filename: str
    mime_type: str
    size: int
    # True when the part is embedded in the HTML body rather than attached to
    # the message — a signature logo, a social icon, a spacer. Gmail reports
    # these identically to real attachments, right down to a filename, and the
    # only thing separating them is that something in the body says
    # `<img src="cid:...">`.
    inline: bool = False


@dataclass(frozen=True)
class Message:
    """A mail message, flattened into what the pipeline actually uses.

    Deliberately not the raw Gmail payload. The classifier receives untrusted
    text and the less shape that text arrives in, the fewer places for
    something to hide — a nested multipart structure is a good place to hide.
    """
    message_id: str
    thread_id: str
    subject: str
    sender: str
    recipient: str
    date: str                          # ISO8601, or "" when unparseable
    body: str
    headers: dict[str, str] = field(default_factory=dict)
    attachments: list[Attachment] = field(default_factory=list)
    label_ids: tuple[str, ...] = ()

    @property
    def sender_domain(self) -> str:
        return self.sender.rpartition("@")[2].strip(">").lower()

    @property
    def real_attachments(self) -> list[Attachment]:
        """What a person would call the attachments.

        `attachments` is everything Gmail reports, inline signature images
        included, because the raw parse should not throw information away. This
        is the list every caller actually wants: what was attached TO the
        message, as opposed to what is drawn INSIDE it.
        """
        return [a for a in self.attachments if not a.inline]


# ---------------------------------------------------------------------------
# Transport — injectable, so tests never reach the network
# ---------------------------------------------------------------------------

class Transport(Protocol):
    def list_ids(self, query: str, limit: int) -> list[str]: ...
    def get(self, message_id: str) -> dict: ...
    def get_attachment(self, message_id: str, attachment_id: str) -> bytes: ...


class GoogleTransport:
    """The real one. Imports the Google libraries lazily.

    Lazily because this file must import on a machine with no Google client
    libraries installed — the development machine is not the Mac, and the test
    suite has to run in both places.
    """

    def __init__(self, credentials: Any, user_id: str = "me") -> None:
        self._creds = credentials
        self._user = user_id
        self._service = None

    def _svc(self):
        if self._service is None:
            try:
                from googleapiclient.discovery import build
            except ImportError as exc:      # pragma: no cover - mac only
                raise GmailError(
                    "google-api-python-client is not installed. "
                    "`.venv/bin/pip install google-api-python-client "
                    "google-auth-oauthlib`") from exc
            self._service = build("gmail", "v1", credentials=self._creds,
                                  cache_discovery=False)
        return self._service

    def list_ids(self, query: str, limit: int) -> list[str]:
        out: list[str] = []
        req = self._svc().users().messages().list(
            userId=self._user, q=query, maxResults=min(limit, 500))
        while req is not None and len(out) < limit:
            resp = req.execute()
            out.extend(m["id"] for m in resp.get("messages", []))
            req = self._svc().users().messages().list_next(req, resp)
        return out[:limit]

    def get(self, message_id: str) -> dict:
        return self._svc().users().messages().get(
            userId=self._user, id=message_id, format="full").execute()

    def get_attachment(self, message_id: str, attachment_id: str) -> bytes:
        """The attachment bytes.

        `.get()` on the attachments resource — a read, permitted by
        gmail.readonly. Note this is `attachments().get`, not `messages().get`;
        Gmail returns attachment payloads separately rather than inline, which
        is why the message body can be fetched cheaply and the 40MB PDF only
        when we have decided we want it.
        """
        resp = self._svc().users().messages().attachments().get(
            userId=self._user, messageId=message_id, id=attachment_id).execute()
        return base64.urlsafe_b64decode(resp.get("data", "").encode("ascii"))


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _headers(payload: dict) -> dict[str, str]:
    return {h.get("name", ""): h.get("value", "")
            for h in payload.get("headers", []) or []}


def _decode(data: str | None) -> str:
    if not data:
        return ""
    try:
        return base64.urlsafe_b64decode(data.encode("ascii")).decode(
            "utf-8", errors="replace")
    except Exception:
        return ""


def _walk(payload: dict) -> Iterable[dict]:
    yield payload
    for part in payload.get("parts", []) or []:
        yield from _walk(part)


def extract_body(payload: dict) -> tuple[str, bool]:
    """Best text for the classifier, and whether it came from HTML.

    text/plain wins whenever there is one. A multipart/alternative message
    carries the same content twice, and the plain part has already had the
    markup — and the tracking pixels, and the mismatched link text — removed
    by the sender's own mail client. Falling back to HTML is for messages that
    only ship HTML, which sanitise.py then strips.
    """
    plain, html = [], []
    for part in _walk(payload):
        mime = part.get("mimeType", "")
        body = part.get("body", {}) or {}
        if body.get("attachmentId"):
            continue
        if mime == "text/plain":
            plain.append(_decode(body.get("data")))
        elif mime == "text/html":
            html.append(_decode(body.get("data")))

    text = "\n".join(p for p in plain if p).strip()
    if text:
        return text, False
    return "\n".join(h for h in html if h).strip(), True


def is_inline(part: dict) -> bool:
    """True when this part is part of the BODY, not attached to the message.

    Found on the first dry run against Matthew's real mailbox. His signature
    block carries nine images — `image001.gif` through `image009.png` — and
    Gmail reports every one of them exactly the way it reports an invoice PDF:
    an attachmentId, a filename, a size. Ingesting them would have put nine
    junk documents in the archive for every email he sends, each one sent to
    Vision to have a logo OCR'd.

    Two signals, either sufficient:

      * `Content-ID` — the body references it as `<img src="cid:...">`. This is
        what Outlook and Gmail both emit for signature images.
      * `Content-Disposition: inline` — the sender saying so directly.

    A real attachment carries `Content-Disposition: attachment`, or no
    disposition at all. When neither signal is present we treat it as a real
    attachment: the cost of filing one stray image is a document in the review
    queue, and the cost of the opposite mistake is silently dropping an invoice.
    """
    h = {k.lower(): v for k, v in _headers(part).items()}
    if h.get("content-id") or h.get("x-attachment-id"):
        return True
    return h.get("content-disposition", "").strip().lower().startswith("inline")


def extract_attachments(payload: dict) -> list[Attachment]:
    out = []
    for part in _walk(payload):
        body = part.get("body", {}) or {}
        att_id = body.get("attachmentId")
        if not att_id:
            continue
        out.append(Attachment(
            attachment_id=att_id,
            filename=part.get("filename") or "(unnamed)",
            mime_type=part.get("mimeType", "application/octet-stream"),
            size=int(body.get("size") or 0),
            inline=is_inline(part),
        ))
    return out


def _iso(raw: str) -> str:
    """RFC 2822 date to ISO 8601, or "" if the sender's date is nonsense.

    An empty string rather than today's date: a fabricated timestamp on a
    document would flow into the filename (D-007) and put it under a day it
    has nothing to do with. Downstream already knows how to fall back.
    """
    if not raw:
        return ""
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return ""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


# Only a HARD fail counts. `softfail`, `neutral`, `none`, `temperror` and
# `permerror` do not.
#
# This mailbox has automatic forwarding switched on, and forwarding breaks SPF
# by design — the forwarding server is not in the original domain's SPF record,
# so a perfectly legitimate forwarded invoice arrives spf=softfail or spf=fail
# depending on the hop. Treating every non-pass as fraud would quarantine a
# large share of real mail, and a review queue that is mostly false positives
# is a review queue nobody reads. That is a worse security outcome than the
# narrower check, not a more cautious one.
#
# DMARC exists precisely to resolve this: it passes when EITHER SPF or DKIM
# aligns with the From domain, and DKIM survives forwarding. So dmarc=fail is
# the signal that actually means something, and it is in the list.
HARD_FAIL = "fail"

_AUTH_METHOD = re.compile(r"\b(spf|dkim|dmarc)\s*=\s*([a-z]+)", re.I)


def auth_results(headers: dict[str, str]) -> dict[str, bool]:
    """SPF/DKIM/DMARC verdicts, as the flags rules.yaml names.

    Google verifies these at delivery and writes the answer into
    `Authentication-Results`. We read its verdict rather than re-checking:
    re-running SPF now would query today's DNS about a message that arrived
    days ago, and a domain that has since changed its record would produce a
    verdict about the wrong moment.

    The header is only trustworthy because it was written by the receiving
    server, not the sender — anything above the topmost Authentication-Results
    line was added by Google, everything below it came in over the wire. A
    sender can forge their own `Authentication-Results:` header, which is why
    only the FIRST one is read.
    """
    raw = headers.get("Authentication-Results") or headers.get(
        "authentication-results") or ""
    # Multiple headers arrive folded into one value by _headers(); the first
    # line is the receiving server's own, and the rest may be attacker text.
    raw = raw.split("\n")[0]

    seen = {}
    for method, verdict in _AUTH_METHOD.findall(raw):
        seen.setdefault(method.lower(), verdict.lower())

    return {
        "spf_fail": seen.get("spf") == HARD_FAIL,
        "dkim_fail": seen.get("dkim") == HARD_FAIL,
        "dmarc_fail": seen.get("dmarc") == HARD_FAIL,
        # Not a failure — the ABSENCE of a policy. Only meaningful when the
        # From domain is one we know, so ingest decides; this just reports it.
        "dmarc_none": seen.get("dmarc") in {"none", None},
    }


def parse_message(raw: dict) -> Message:
    payload = raw.get("payload", {}) or {}
    h = _headers(payload)
    body, _ = extract_body(payload)
    return Message(
        message_id=raw.get("id", ""),
        thread_id=raw.get("threadId", ""),
        subject=h.get("Subject", ""),
        sender=h.get("From", ""),
        recipient=h.get("To", ""),
        date=_iso(h.get("Date", "")),
        body=body,
        headers=h,
        attachments=extract_attachments(payload),
        label_ids=tuple(raw.get("labelIds", []) or []),
    )


# ---------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------

class GmailClient:
    def __init__(self, transport: Transport) -> None:
        self._t = transport

    def recent(self, *, newer_than_days: int = 1, limit: int = 100,
               query: str = "") -> list[Message]:
        """Messages from the last N days, newest first.

        `newer_than_days` rather than a stored cursor for now: a missed poll
        must not lose mail, and Gmail's own dedupe is the message id, which
        `db.find_artifact_by_hash` already backs onto via content hashing.
        """
        q = f"newer_than:{int(newer_than_days)}d"
        if query:
            q = f"{q} {query}"
        return [parse_message(self._t.get(mid))
                for mid in self._t.list_ids(q, limit)]

    def fetch(self, message_id: str) -> Message:
        return parse_message(self._t.get(message_id))

    def attachment_bytes(self, message: Message, att: Attachment) -> bytes:
        """Fetched only when something has decided it wants this attachment.

        Separate from `recent()` on purpose: listing a week of mail must not
        pull every PDF in it across the network and into memory.
        """
        return self._t.get_attachment(message.message_id, att.attachment_id)


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

def keychain_service(address: str) -> str:
    return KEYCHAIN_PREFIX + address


def stored_token(address: str) -> str:
    """The refresh token, from the Keychain. Never a file, never lms.env."""
    service = keychain_service(address)
    r = subprocess.run(
        ["security", "find-generic-password", "-s", service, "-w"],
        capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        raise GmailError(
            f"no OAuth token for {address}. Authorise it once with:\n"
            f"    ./ops/authorise_gmail.py {address}")
    return r.stdout.rstrip("\n")


def credentials_for(address: str, client_id: str, client_secret: str):
    """Build google credentials from the stored refresh token.

    Scopes are passed explicitly and come from SCOPES — never from whatever
    the token happens to carry. If the stored grant is wider than SCOPES for
    any reason, this asks for less rather than inheriting more.
    """
    try:
        from google.oauth2.credentials import Credentials
    except ImportError as exc:              # pragma: no cover - mac only
        raise GmailError(
            "google-auth is not installed. `.venv/bin/pip install "
            "google-api-python-client google-auth-oauthlib`") from exc

    return Credentials(
        token=None,
        refresh_token=stored_token(address),
        client_id=client_id,
        client_secret=client_secret,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=list(SCOPES),
    )
