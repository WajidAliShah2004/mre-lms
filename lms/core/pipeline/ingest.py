"""One file in, one filed document out.

    hash -> dedupe -> phishing pre-check -> OCR -> classify -> file | quarantine
                                                            -> task
                                                            -> log

Every stage is recorded in `processed(sha256, stage)`, so a crash halfway
through is safe to re-run: completed stages are skipped and nothing is done
twice. That is why the whole thing can be driven by a dumb watched-folder loop
that simply calls `ingest_file()` on anything it sees.

The order is not arbitrary. Dedupe comes before OCR because OCR is the
expensive step and a resent invoice is common. The phishing pre-check comes
before classification because a wire-fraud email should never reach a model at
all — there is nothing useful for the model to decide, and the answer is
already known.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from ..db import database as db
from . import filing, ocr
from .classify import Artifact, Classification, Classifier
from .registry import Registry

CONFIG = Path(__file__).resolve().parents[2] / "config"

_DATE_PATTERNS = [
    (re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b"), lambda m: f"{m[1]}-{m[2]}-{m[3]}"),
    (re.compile(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b"),
     lambda m: f"{m[3]}-{int(m[1]):02d}-{int(m[2]):02d}"),   # US order
]


@dataclass
class IngestResult:
    sha256: str
    status: str                      # FILED | DUPLICATE | QUARANTINED | SUSPECTED_PHISHING
    path: Path | None = None
    classification: Classification | None = None
    ocr_engine: str | None = None
    task_id: int | None = None
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in {"FILED", "DUPLICATE"}


def _rules() -> dict[str, Any]:
    return yaml.safe_load((CONFIG / "rules.yaml").read_text(encoding="utf-8"))


def _unreadable_reason(path: Path, engine: str | None) -> str:
    """Say why nothing was read, in the words a human needs to act on it.

    "low confidence" sends someone to look at the prompt. "PDF text extraction
    is not implemented" sends them to the right place immediately.
    """
    suffix = path.suffix.lower() or "(no extension)"
    if suffix == ".pdf":
        return ("no text extracted: the PDF has no text layer and OCR of its "
                "pages produced nothing — most likely a blank scan")
    if suffix not in ocr.READABLE_SUFFIXES:
        return (f"no text extracted: {suffix} is not a format this pipeline "
                f"can read (handles images and {', '.join(sorted(ocr.TEXT_SUFFIXES))})")
    if engine == "failed":
        return f"no text extracted: every reader failed on {suffix}"
    return f"no text extracted: {suffix} read as empty"


def _sidecar_text(text: str | None, engine: str | None) -> str | None:
    """The extracted-text sidecar, or None when it would only be a duplicate.

    filing writes this beside the document as `<filed name>.txt`. For a
    photograph that is the whole point: the image is not searchable and the
    transcription is. For a document that arrived AS text, it produces
    `...__hash.txt.txt` — byte-identical to the document sitting next to it.

    That is not merely untidy. It doubles the archive for every text document,
    and it puts a second file in the folder that looks like a separate record,
    which is exactly the confusion the D-007 naming convention exists to
    prevent. The sidecar's `has_ocr_text` and `ocr_engine` still say what was
    read and by what, so nothing is lost by not writing the copy.
    """
    if engine == "plain-text":
        return None
    return text


# ---------------------------------------------------------------------------
# Phishing pre-check — runs before the model, not after
# ---------------------------------------------------------------------------

def phishing_check(art: Artifact, registry: Registry,
                   auth_results: dict[str, bool] | None = None) -> str | None:
    """Return a reason string if this should be blocked, else None.

    Deliberately crude and deliberately deterministic. It is not trying to
    catch every fraud — it is trying to make the specific catastrophic case
    (a payment-redirect email that looks like a known counterparty) impossible
    to file quietly.
    """
    rules = _rules()["phishing"]
    auth_results = auth_results or {}

    for failure in rules["auth_failures"]:
        if auth_results.get(failure):
            return f"SUSPECTED_PHISHING: {failure}"

    sender_domain = (art.sender or "").rpartition("@")[2].lower()
    if sender_domain:
        known = {d.lower() for e in registry.entities.values() for d in e.domains}
        for domain in known:
            if sender_domain != domain and _confusable(sender_domain, domain,
                                                       rules["lookalike"]["max_edit_distance"]):
                return (f"SUSPECTED_PHISHING: sender {sender_domain!r} is a "
                        f"look-alike of {domain!r}")

    haystack = f"{art.subject}\n{art.body}".lower()
    hits = [p for p in rules["money_language"] if p.lower() in haystack]
    if len(hits) >= int(rules["score_threshold"]):
        return f"SUSPECTED_PHISHING: payment-redirect language ({', '.join(hits[:3])})"

    return None


def _confusable(a: str, b: str, max_distance: int) -> bool:
    """Cheap edit distance. Domains are short; this is not a hot path."""
    if abs(len(a) - len(b)) > max_distance:
        return False
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return 0 < prev[-1] <= max_distance


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

def guess_doc_date(text: str, fallback: str | None = None) -> str:
    """The document's own date, which is what the filename uses (D-007).

    An invoice photographed three weeks late still belongs under the day it
    was issued. Falls back to the received date, then to today — a document
    always files somewhere.
    """
    for pattern, fmt in _DATE_PATTERNS:
        m = pattern.search(text or "")
        if m:
            candidate = fmt(m)
            try:
                y, mo, d = (int(x) for x in candidate.split("-"))
                date(y, mo, d)                # reject 2026-13-45
                return candidate
            except ValueError:
                continue
    return fallback or date.today().isoformat()


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------

def ingest_file(conn, roots: filing.StorageRoots, registry: Registry,
                classifier: Classifier, source_path: Path, *,
                source: str = "photo", source_ref: str | None = None,
                artifact: Artifact | None = None,
                auth_results: dict[str, bool] | None = None,
                run_ocr: bool = True) -> IngestResult:
    source_path = Path(source_path)
    if not source_path.is_file():
        raise FileNotFoundError(source_path)

    sha = db.sha256_file(source_path)

    # --- dedupe, before anything expensive --------------------------------
    existing = db.find_artifact_by_hash(conn, sha)
    if existing is not None and existing["filed_path"]:
        db.record_duplicate(conn, sha, source, source_ref, source_path.name)
        db.log_action(conn, "INGEST_SKIPPED_DUPLICATE", artifact_id=existing["id"],
                      detail=f"{source}:{source_ref or source_path.name}")
        return IngestResult(sha256=sha, status="DUPLICATE",
                            path=Path(existing["filed_path"]),
                            reason="already filed")

    art = artifact or Artifact(source=source, source_ref=source_ref)

    # --- read the file -----------------------------------------------------
    ocr_text = None
    engine = None
    suffix = source_path.suffix.lower()

    if run_ocr and suffix in ocr.READABLE_SUFFIXES:
        if not db.already_processed(conn, sha, "ocr"):
            try:
                result = ocr.read_any(source_path)
                ocr_text, engine = result.text, result.engine
                db.mark_processed(conn, sha, "ocr")
            except ocr.OCRError as exc:
                # Not fatal here. An unreadable photograph still needs to reach
                # the review queue, where a human can look at it in two seconds.
                # The emptiness check below is what stops it reaching the model.
                db.log_action(conn, "OCR_FAILED", detail=str(exc)[:400])
                ocr_text, engine = "", "failed"
        if ocr_text and not art.body:
            art.body = ocr_text

    # --- did we actually read anything? (D-024) ----------------------------
    #
    # Never ask the model about a document we failed to read. Before this
    # check, a .txt file matched no extraction path at all, so `art.body`
    # stayed empty, the classifier was handed nothing, and the model answered
    # with low confidence — correctly, since there was nothing there. That
    # surfaced as "confidence 0.15 below 0.60", which reads like a model
    # problem and is in fact a reading problem two stages earlier.
    #
    # The distinction matters because the two have opposite fixes. An empty
    # body is also the case where a confidently WRONG answer costs most: the
    # model has nothing to be constrained by, so whatever it invents is
    # unfalsifiable.
    if not (art.body or "").strip():
        reason = _unreadable_reason(source_path, engine)
        target = filing.quarantine_artifact(
            conn, roots, source_path=source_path, sha256=sha,
            source=source, reason=reason, source_ref=source_ref)
        db.log_action(conn, "UNREADABLE", detail=reason[:400])
        return IngestResult(sha256=sha, status="QUARANTINED", path=target,
                            ocr_engine=engine, reason=reason)

    # --- phishing, before the model ---------------------------------------
    reason = phishing_check(art, registry, auth_results)
    if reason:
        target = filing.quarantine_artifact(
            conn, roots, source_path=source_path, sha256=sha,
            source=source, reason=reason, source_ref=source_ref)
        db.set_artifact_status(conn, int(db.find_artifact_by_hash(conn, sha)["id"]),
                               "SUSPECTED_PHISHING")
        db.log_action(conn, "PHISHING_BLOCKED", detail=reason)
        return IngestResult(sha256=sha, status="SUSPECTED_PHISHING",
                            path=target, reason=reason, ocr_engine=engine)

    # --- classify ----------------------------------------------------------
    classification = classifier.classify(art)

    if classification.needs_review:
        target = filing.quarantine_artifact(
            conn, roots, source_path=source_path, sha256=sha, source=source,
            reason=classification.review_reason or "low confidence",
            source_ref=source_ref)
        return IngestResult(sha256=sha, status="QUARANTINED", path=target,
                            classification=classification, ocr_engine=engine,
                            reason=classification.review_reason)

    # --- file --------------------------------------------------------------
    doc_date = classification.doc_date or guess_doc_date(
        ocr_text or art.body, art.received_date)

    filed = filing.file_artifact(
        conn, roots, registry,
        source_path=source_path, sha256=sha, source=source,
        entity_id=classification.entity_id, category=classification.category,
        subcategory=classification.subcategory, doc_date=doc_date,
        counterparty=classification.counterparty,
        descriptor=classification.descriptor,
        amount_cents=classification.amount_cents,
        currency=classification.currency,
        source_ref=source_ref, ocr_text=_sidecar_text(ocr_text, engine),
        extra_metadata={
            "ocr_engine": engine,
            "confidence": classification.confidence,
            "urgency": classification.urgency,
            "decided_by": classification.decided_by,
            "model": classification.model,
            "prompt_hash": classification.prompt_hash,
            "rationale": classification.rationale,
        },
    )

    db.insert_classification(conn, artifact_id=filed.artifact_id,
                             **classification.as_row())

    # --- task --------------------------------------------------------------
    task_id = None
    if classification.due_date or classification.urgency in {"CRITICAL", "HIGH"}:
        title = _task_title(classification)
        task_id = db.insert_task(
            conn, title=title, due_date=classification.due_date,
            entity_id=classification.entity_id,
            urgency=classification.urgency,
            source_artifact=filed.artifact_id,
            detail=classification.rationale,
        )
        db.log_action(conn, "TASK_CREATED", artifact_id=filed.artifact_id,
                      detail=title)

    return IngestResult(sha256=sha, status="FILED", path=filed.filed_path,
                        classification=classification, ocr_engine=engine,
                        task_id=task_id)


def _task_title(c: Classification) -> str:
    """Written the way it will read on a phone at 06:30.

    "Jury duty — call by Sept 12", not "LEGAL/court artifact 4471 requires
    action". The morning brief is the product; a task nobody can parse at a
    glance is a task that gets ignored.
    """
    who = c.counterparty.replace("-", " ").title() if c.counterparty else ""
    what = c.descriptor.replace("-", " ") if c.descriptor else c.category.lower()
    title = f"{what}".strip().capitalize()
    if who:
        title = f"{title} — {who}"
    if c.due_date:
        title = f"{title} (by {c.due_date})"
    return title[:200]
