"""When Matthew overrides the system — spec §Day-5.5.

    "Every correction Matthew makes (re-classify, edit draft, change due
     date) → corrections table."

The table and `db.record_correction()` have existed since Day 1 and nothing
called either. This is what calls them.

WHY A CORRECTION HAS TO MOVE THE FILE
-------------------------------------
Recording that the system got something wrong, and leaving the document where
it was put, produces a database that knows the truth and an archive that does
not. Matthew looks in folders, not in SQLite. A correction that only logs
disagreement is a note about a problem rather than a fix for it.

So a correction does three things, and all three or none:

  1. writes the old and new values to `corrections` — never pruned, and the
     only record of where the classifier was wrong. It is the training signal
     for the next engagement, so the model's original answer is preserved
     here even though the classification row is overwritten.
  2. re-files the document, its sidecar and its transcription
  3. marks the classification `decided_by = 'human'`, so nothing downstream
     mistakes his judgement for the model's

WHY MOVING A FILED DOCUMENT IS ALLOWED HERE
-------------------------------------------
`tests/test_hard_stops.py` forbids `shutil.move` in core/ and requires any
exception to be argued in its EXEMPT table. The argument for this one:

`file_artifact` keeps an untouched copy of every incoming byte under
`_originals/<sha256><ext>` and never writes there again. The copy in the
archive tree is *derived* — a renamed duplicate whose whole purpose is to sit
in the right folder under the right name. Moving a derived copy, at a person's
explicit instruction, to the place that person says is correct, destroys
nothing: the bytes exist in `_originals` throughout, and the move is within
one filesystem.

The alternative — refusing to move it — leaves a document filed under the
wrong business permanently, which is the failure this whole system exists to
prevent.

Note what is deliberately NOT done: nothing is deleted, ever. A move is a
rename; if the destination already exists the correction is refused rather
than overwriting, exactly as first-time filing refuses.
"""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from ..db import database as db
from . import filing, naming
from .registry import Registry, RegistryError

# Distinguishes "leave this alone" from "set it to None". `subcategory=None`
# is a legitimate instruction — file it at the category level — so absence
# cannot be signalled with None.
UNSET: object = object()

# What travels with a document when it moves. The sidecar carries everything
# the filename cannot; the .txt is the transcription of a photograph, and a
# document that arrives as text has none (D-025).
COMPANIONS = (".meta.json", ".txt")


class CorrectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Change:
    field: str
    old: str | None
    new: str | None

    def __str__(self) -> str:
        return f"{self.field}: {self.old!r} -> {self.new!r}"


@dataclass(frozen=True)
class CorrectionResult:
    artifact_id: int
    changes: list[Change] = field(default_factory=list)
    filed_path: Path | None = None
    refiled: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.changes)


# ---------------------------------------------------------------------------

def _artifact(conn: sqlite3.Connection, artifact_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM artifacts WHERE id = ?",
                       (artifact_id,)).fetchone()
    if row is None:
        raise CorrectionError(f"no artifact with id {artifact_id}")
    return row


def _classification(conn: sqlite3.Connection, artifact_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM classifications WHERE artifact_id = ? "
        "ORDER BY id DESC LIMIT 1", (artifact_id,)).fetchone()
    if row is None:
        raise CorrectionError(
            f"artifact {artifact_id} has no classification. Quarantined "
            f"documents are not corrected, they are classified for the first "
            f"time — that is a different operation")
    return row


def _move(src: Path, dest: Path) -> None:
    """Move a filed document. See the module docstring for why this is allowed.

    Refuses rather than overwrites, for the same reason first-time filing
    does: losing a document silently is the one failure this system must not
    have.
    """
    if dest.exists():
        raise CorrectionError(
            f"refusing to overwrite an existing file: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))


def apply_correction(conn: sqlite3.Connection, roots: filing.StorageRoots,
                     registry: Registry, artifact_id: int, *,
                     entity_id: str | None = None,
                     category: str | None = None,
                     subcategory: object = UNSET,
                     doc_date: str | None = None,
                     due_date: object = UNSET,
                     source: str = "human") -> CorrectionResult:
    """Apply Matthew's judgement, and make the archive agree with it."""
    art = _artifact(conn, artifact_id)
    cls = _classification(conn, artifact_id)

    after = {
        "entity_id": entity_id or cls["entity_id"],
        "category": category or cls["category"],
        "subcategory": cls["subcategory"] if subcategory is UNSET else subcategory,
        "doc_date": doc_date or art["doc_date"],
    }

    # Validate BEFORE recording anything. A correction that half-applies is
    # worse than one that is refused: the point of this path is that the
    # database and the archive end up agreeing.
    try:
        registry.get(after["entity_id"])
        registry.validate_category(after["entity_id"], after["category"],
                                   after["subcategory"])
    except RegistryError as exc:
        raise CorrectionError(str(exc)) from exc

    changes = [
        Change(f, str(old) if old is not None else None,
               str(new) if new is not None else None)
        for f, old, new in (
            ("entity_id", cls["entity_id"], after["entity_id"]),
            ("category", cls["category"], after["category"]),
            ("subcategory", cls["subcategory"], after["subcategory"]),
            ("doc_date", art["doc_date"], after["doc_date"]),
        )
        if (old or None) != (new or None)
    ]

    task_row = None
    if due_date is not UNSET:
        task_row = conn.execute(
            "SELECT id, due_date FROM tasks WHERE source_artifact = ? "
            "ORDER BY id DESC LIMIT 1", (artifact_id,)).fetchone()
        if task_row is None:
            raise CorrectionError(
                f"artifact {artifact_id} has no task, so there is no due date "
                f"to change")
        if (task_row["due_date"] or None) != (due_date or None):
            changes.append(Change("due_date", task_row["due_date"], due_date))

    if not changes:
        # Agreement is not a correction. Recording one would put noise into
        # the only table that says where the classifier was wrong.
        return CorrectionResult(artifact_id=artifact_id,
                                filed_path=Path(art["filed_path"])
                                if art["filed_path"] else None)

    for c in changes:
        db.record_correction(conn, artifact_id, c.field, c.old, c.new)

    # ---- re-file, if the correction moved it ------------------------------
    refiled = False
    filed_path = Path(art["filed_path"]) if art["filed_path"] else None

    filing_fields = {"entity_id", "category", "subcategory", "doc_date"}
    if filed_path and filing_fields.intersection(c.field for c in changes):
        if not filed_path.exists():
            raise CorrectionError(
                f"the database says this is filed at {filed_path}, and it is "
                f"not there. Correcting it would guess at what happened")

        new_name = naming.build_filename(
            doc_date=after["doc_date"], entity_id=after["entity_id"],
            category=after["category"], counterparty=cls["counterparty"],
            descriptor=cls["descriptor"], amount_cents=cls["amount_cents"],
            sha256=art["sha256"], extension=filed_path.suffix,
            currency=cls["currency"] or "USD",
        )
        dest_dir = filing.destination_dir(roots, registry, after["entity_id"],
                                          after["category"], after["subcategory"])
        target = dest_dir / new_name

        # Companions first: if one of them collides we have not yet touched
        # the document itself, so nothing is stranded.
        for suffix in COMPANIONS:
            companion = filed_path.with_name(filed_path.name + suffix)
            if companion.exists():
                _move(companion, target.with_name(target.name + suffix))

        _move(filed_path, target)
        filed_path, refiled = target, True

        conn.execute("UPDATE artifacts SET filed_path = ?, doc_date = ? WHERE id = ?",
                     (str(target), after["doc_date"], artifact_id))

        filing.write_sidecar(target, {
            **_sidecar_of(target), "entity_id": after["entity_id"],
            "entity_tree": registry.get(after["entity_id"]).tree,
            "category": after["category"], "subcategory": after["subcategory"],
            "doc_date": after["doc_date"], "filed_path": str(target),
            "corrected_at": db.now_iso(), "corrected_by": source,
        })

    # ---- the classification now reflects a person, not a model ------------
    conn.execute(
        "UPDATE classifications SET entity_id = ?, category = ?, "
        "subcategory = ?, decided_by = 'human' WHERE id = ?",
        (after["entity_id"], after["category"], after["subcategory"], cls["id"]))

    if task_row is not None and any(c.field == "due_date" for c in changes):
        conn.execute("UPDATE tasks SET due_date = ? WHERE id = ?",
                     (due_date, task_row["id"]))

    db.log_action(conn, "CORRECTED", artifact_id=artifact_id,
                  detail="; ".join(str(c) for c in changes)[:400],
                  approved_by=source)
    conn.commit()

    return CorrectionResult(artifact_id=artifact_id, changes=changes,
                            filed_path=filed_path, refiled=refiled)


def _sidecar_of(target: Path) -> dict:
    """The existing sidecar, so a correction edits it rather than replacing it.

    Losing `prompt_hash` or `ocr_engine` because someone fixed a category
    would quietly destroy the provenance that makes a filing auditable.
    """
    import json
    path = target.with_name(target.name + ".meta.json")
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def history(conn: sqlite3.Connection, artifact_id: int | None = None,
            limit: int = 50) -> list[sqlite3.Row]:
    """What the system got wrong, most recent first."""
    if artifact_id is None:
        return conn.execute(
            "SELECT * FROM corrections ORDER BY id DESC LIMIT ?",
            (limit,)).fetchall()
    return conn.execute(
        "SELECT * FROM corrections WHERE artifact_id = ? ORDER BY id DESC LIMIT ?",
        (artifact_id, limit)).fetchall()
