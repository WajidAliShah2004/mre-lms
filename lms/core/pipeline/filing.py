"""Deterministic filing — spec §6.2–6.3, decision D-007.

Filing is pure code. The model decides *what* a document is; this module
decides *where it goes* and *what it is called*, and it does so without
consulting anything that can be talked into a different answer by the
contents of an email.

Hard guarantees, asserted in tests:

  * The original is copied, never moved and never deleted. `_originals/`
    keeps the byte-identical source forever.
  * The same bytes are never filed twice. A re-fed file records a sighting
    and returns the existing path.
  * Filing never overwrites. A name collision that is not a hash match is a
    hard error, not a silent clobber.
  * Every filed artifact gets a `.meta.json` sidecar holding the full,
    untruncated values that the filename had to shorten.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..db import database as db
from . import naming
from .registry import Registry


class FilingError(RuntimeError):
    pass


@dataclass(frozen=True)
class FilingResult:
    artifact_id: int
    filed_path: Path
    sidecar_path: Path
    filename: str
    duplicate_of: str | None = None      # sha256 when this was a re-feed

    @property
    def was_duplicate(self) -> bool:
        return self.duplicate_of is not None


@dataclass(frozen=True)
class StorageRoots:
    """Where the trees live.

    `archive` is the RAID path holding the filed trees; `originals` keeps an
    untouched copy of every incoming byte; `quarantine` holds anything the
    classifier was not confident about.
    """
    archive: Path
    originals: Path
    quarantine: Path

    @classmethod
    def from_env(cls) -> "StorageRoots":
        base = Path(os.environ.get("LMS_ARCHIVE_ROOT", "~/LMS/archive")).expanduser()
        return cls(
            archive=base,
            originals=Path(os.environ.get("LMS_ORIGINALS_ROOT", "~/LMS/_originals")).expanduser(),
            quarantine=Path(os.environ.get("LMS_QUARANTINE_ROOT", "~/LMS/quarantine")).expanduser(),
        )

    def ensure(self) -> None:
        for p in (self.archive, self.originals, self.quarantine):
            p.mkdir(parents=True, exist_ok=True)


def destination_dir(roots: StorageRoots, registry: Registry, entity_id: str,
                    category: str, subcategory: str | None = None) -> Path:
    """archive / <entity tree> / <CATEGORY> [/ <subcategory>]"""
    ent = registry.get(entity_id)
    registry.validate_category(entity_id, category, subcategory)
    path = roots.archive / ent.tree / category
    if subcategory:
        path = path / subcategory
    return path


def write_sidecar(target: Path, payload: dict[str, Any]) -> Path:
    """`<name>.meta.json` beside the artifact (spec §6.3).

    Holds the full untruncated values. The filename is lossy by design; this
    is not. If the database is ever lost, the tree plus its sidecars is
    enough to rebuild the index.
    """
    sidecar = target.with_name(target.name + ".meta.json")
    sidecar.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return sidecar


def file_artifact(conn, roots: StorageRoots, registry: Registry, *,
                  source_path: Path,
                  sha256: str,
                  source: str,
                  entity_id: str,
                  category: str,
                  doc_date: str,
                  counterparty: str | None = None,
                  descriptor: str | None = None,
                  amount_cents: int | None = None,
                  currency: str = "USD",
                  subcategory: str | None = None,
                  source_ref: str | None = None,
                  parent_id: int | None = None,
                  ocr_text: str | None = None,
                  extra_metadata: dict[str, Any] | None = None) -> FilingResult:
    """File one artifact. Idempotent on sha256.

    Returns a FilingResult either way — a duplicate returns the path of the
    copy already on disk, so callers never need to branch on it before
    reporting a location back to the user.
    """
    source_path = Path(source_path)
    if not source_path.is_file():
        raise FilingError(f"source is not a file: {source_path}")

    # ---- dedupe ---------------------------------------------------------
    existing = db.find_artifact_by_hash(conn, sha256)
    if existing is not None and existing["filed_path"]:
        db.record_duplicate(conn, sha256, source, source_ref, source_path.name)
        db.log_action(conn, "FILE_SKIPPED_DUPLICATE", artifact_id=existing["id"],
                      detail=f"re-fed from {source}:{source_ref or source_path.name}")

        reconcile(conn, roots, sha256, source_ref, artifact_id=int(existing["id"]))

        filed = Path(existing["filed_path"])
        return FilingResult(
            artifact_id=int(existing["id"]),
            filed_path=filed,
            sidecar_path=filed.with_name(filed.name + ".meta.json"),
            filename=filed.name,
            duplicate_of=sha256,
        )

    roots.ensure()

    # ---- name and destination -------------------------------------------
    filename = naming.build_filename(
        doc_date=doc_date, entity_id=entity_id, category=category,
        counterparty=counterparty, descriptor=descriptor,
        amount_cents=amount_cents, sha256=sha256,
        extension=source_path.suffix, currency=currency,
    )
    dest_dir = destination_dir(roots, registry, entity_id, category, subcategory)
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / filename

    # A collision here means two different files produced the same name,
    # including the same hash prefix. Refuse rather than overwrite: losing a
    # document silently is the one failure this system must not have.
    if target.exists():
        raise FilingError(
            f"refusing to overwrite an existing file: {target}. "
            "Same name from different bytes — investigate before re-running."
        )

    # ---- keep the original, untouched ------------------------------------
    original_copy = roots.originals / f"{sha256}{source_path.suffix.lower()}"
    if not original_copy.exists():
        shutil.copy2(source_path, original_copy)

    # copy2, not move: the watched folder's file may still be syncing, and
    # the source is somebody else's (iCloud's, the mail store's) to manage.
    shutil.copy2(source_path, target)

    # ---- record ----------------------------------------------------------
    if existing is None:
        artifact_id = db.insert_artifact(
            conn, sha256=sha256, source=source, source_ref=source_ref,
            parent_id=parent_id, original_name=source_path.name,
            byte_size=source_path.stat().st_size, doc_date=doc_date,
            filed_path=str(target), status="FILED",
        )
    else:
        artifact_id = int(existing["id"])
        db.set_artifact_status(conn, artifact_id, "FILED", str(target))

    sidecar_payload: dict[str, Any] = {
        "sha256": sha256,
        "filed_path": str(target),
        "original_name": source_path.name,
        "source": source,
        "source_ref": source_ref,
        "doc_date": doc_date,
        "ingested_at": db.now_iso(),
        "entity_id": entity_id,
        "entity_tree": registry.get(entity_id).tree,
        "category": category,
        "subcategory": subcategory,
        # Full values — the filename versions are slugged and truncated.
        "counterparty_full": counterparty,
        "descriptor_full": descriptor,
        "amount_cents": amount_cents,
        "currency": currency,
        "has_ocr_text": bool(ocr_text),
    }
    if extra_metadata:
        sidecar_payload.update(extra_metadata)

    sidecar = write_sidecar(target, sidecar_payload)

    if ocr_text:
        target.with_name(target.name + ".txt").write_text(ocr_text, encoding="utf-8")

    reconcile(conn, roots, sha256, source_ref, artifact_id=artifact_id)

    db.mark_processed(conn, sha256, "file")
    db.log_action(conn, "FILED", artifact_id=artifact_id, detail=str(target))

    return FilingResult(
        artifact_id=artifact_id, filed_path=target,
        sidecar_path=sidecar, filename=filename,
    )


def reconcile(conn, roots: StorageRoots, sha256: str,
              source_ref: str | None, *, artifact_id: int) -> None:
    """Make the review queue agree with reality, for one filed document.

    Two clean-ups that must happen together and on EVERY path that ends with a
    document filed — including the paths that end early because it was filed
    already.

    ONE FUNCTION, CALLED FROM EVERY SUCH PATH. There are two duplicate
    short-circuits in this pipeline: one here in `file_artifact` and one in
    `ingest.ingest_file`, which returns DUPLICATE before this module is reached
    at all. Putting the clean-up inline in the first fixed nothing, because the
    caller never got that far — `_resolved/` on the Mac held exactly the two
    documents that had filed FRESH, and everything that came back through
    ingest's early return was still sitting in the queue.

    Two returns for the same concept is how a fix lands in the wrong one.
    """
    retire_quarantine_copy(roots, sha256)
    supersede_earlier_versions(conn, roots, source_ref, keep=artifact_id)


def supersede_earlier_versions(conn, roots: StorageRoots,
                               source_ref: str | None, *, keep: int) -> list[int]:
    """Retire artifacts of the SAME source that were never filed.

    Identity is the sha256 of the bytes, and for mail those bytes are a
    rendering rather than a fact — so improving the renderer changes the hash
    and the same message becomes a second artifact.

    That happened: dropping signature images from the `Attachments:` line
    changed the rendered text, and the State Farm reply filed correctly as a
    new row while the old one stayed at SUSPECTED_PHISHING with no filed_path
    and nothing that would ever resolve it. It appeared under WAITING ON YOU in
    the brief, permanently, describing a document sitting correctly filed in
    MRECAI/CLIENTS.

    One phantom entry in the section Matthew is supposed to act on and he
    learns to skim the section, which is the same as not having it.

    `source_ref` is exact here: for mail it is the Gmail message id, and for an
    attachment `<message id>/<attachment id>`. Two rows sharing one is
    necessarily two renderings of one thing, never two documents. Rows with no
    source_ref (a photograph named by the phone) are left alone — there the
    filename is not an identity.

    Marked DUPLICATE rather than deleted, because it IS one: the same source,
    seen again, already handled. Nothing is removed and the audit log keeps the
    reason it was originally refused.
    """
    if not source_ref:
        return []

    rows = conn.execute(
        "SELECT id, sha256 FROM artifacts "
        "WHERE source_ref = ? AND id != ? AND filed_path IS NULL "
        "AND status != 'DUPLICATE'",
        (source_ref, keep)).fetchall()

    superseded = []
    for row in rows:
        db.set_artifact_status(conn, int(row["id"]), "DUPLICATE")
        retire_quarantine_copy(roots, row["sha256"])
        db.log_action(
            conn, "SUPERSEDED", artifact_id=int(row["id"]),
            detail=f"an improved rendering of {source_ref} filed as #{keep}")
        superseded.append(int(row["id"]))
    return superseded


def retire_quarantine_copy(roots: StorageRoots, sha256: str) -> list[Path]:
    """Move a document out of the review queue once it has actually filed.

    The queue must describe the present. A document that quarantined on
    Tuesday and filed on Wednesday left its Tuesday copy sitting there, sidecar
    and all, saying `no text extracted — most likely a blank scan` about a
    GEICO insurance card that is now correctly filed in FINANCE. Anyone
    reviewing the queue is reading resolved work and cannot tell.

    Moved to `quarantine/_resolved/`, never deleted. By this point the bytes
    exist in TWO other places — the archive and `_originals/<sha256>` — so this
    copy is redundant, not precious; and keeping it means the history of "this
    was once refused, for this reason" survives.
    """
    moved: list[Path] = []
    if not roots.quarantine.exists():
        return moved

    resolved = roots.quarantine / "_resolved"
    for path in sorted(roots.quarantine.glob(f"{sha256[:8]}__*")):
        if not path.is_file():
            continue
        resolved.mkdir(parents=True, exist_ok=True)
        dest = resolved / path.name
        n = 1
        while dest.exists():
            dest = resolved / f"{path.stem}-{n}{path.suffix}"
            n += 1
        shutil.move(str(path), str(dest))
        moved.append(dest)
    return moved


def quarantine_artifact(conn, roots: StorageRoots, *, source_path: Path, sha256: str,
                        source: str, reason: str,
                        source_ref: str | None = None,
                        parent_id: int | None = None) -> Path:
    """Park something the pipeline will not file, and say why.

    Used for low-confidence classifications and for anything the phishing
    pre-checks flagged. Nothing here is deleted; a human decides.
    """
    roots.ensure()
    source_path = Path(source_path)
    target = roots.quarantine / f"{sha256[:8]}__{source_path.name}"
    if not target.exists():
        shutil.copy2(source_path, target)

    write_sidecar(target, {
        "sha256": sha256,
        "reason": reason,
        "source": source,
        "source_ref": source_ref,
        "original_name": source_path.name,
        "quarantined_at": db.now_iso(),
    })

    existing = db.find_artifact_by_hash(conn, sha256)
    artifact_id = int(existing["id"]) if existing else db.insert_artifact(
        conn, sha256=sha256, source=source, source_ref=source_ref,
        parent_id=parent_id,
        original_name=source_path.name, byte_size=source_path.stat().st_size,
        status="QUARANTINED",
    )
    if existing:
        db.set_artifact_status(conn, artifact_id, "QUARANTINED")

    db.log_action(conn, "QUARANTINED", artifact_id=artifact_id, detail=reason)
    return target
