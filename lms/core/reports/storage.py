"""Storage and dedup report — addendum §4 "Report storage accurately", R28.

    "Show original bytes by source, bytes copied into OpenClaw, bytes saved
     within the managed collection by deduplication, and potential original
     bytes that could be reclaimed after a separately approved cleanup."

Four numbers, and the rule that matters more than any of them: a number this
report cannot measure is printed as NOT MEASURED, with the reason, and never
estimated. An estimate in a storage report is read as a measurement the
moment it is pasted anywhere, and the decision it feeds — whether to clean
anything up — is the one decision in this system that cannot be undone.

WHERE EACH NUMBER COMES FROM
----------------------------
original bytes by source    artifacts.byte_size, the size of the bytes as they
                            arrived, grouped by source; plus duplicate_refs,
                            the same bytes seen again at another location.
                            What is still physically AT the source is measured
                            on disk where the source is a disk (the iCloud
                            inbox) and NOT MEASURED where it is a mailbox:
                            Gmail's storage of a message is not visible through
                            a read-only grant (D-039), and the rendered .eml.txt
                            the LMS files is not the size Gmail stores.
bytes copied in             the filesystem, walked: archive documents, their
                            sidecars, _originals, the review queue, the mail
                            spool. The database's own total for filed bytes is
                            printed beside it as a cross-check, and a mismatch
                            is a finding, not a rounding error.
bytes saved by dedup        one managed copy avoided per ADDITIONAL source
                            location of bytes already filed. A re-read of the
                            same location is not a saving: the mail poll
                            re-reads its whole window every run by design
                            (poll_mail.py, "why a window and not a cursor"),
                            and counting that would report savings that grow
                            with the number of polls, not with the archive.
reclaimable                 files at a source the LMS can see whose sha256
                            matches a FILED artifact — verified by hashing,
                            not inferred from names. Report only.

NOTHING HERE ACTS
-----------------
The addendum is explicit that the copy-only rule "prevents reclaiming space
occupied by originals", and that deduplication can produce "a reviewable
deletion proposal" but cannot lower original-drive usage "without a separate,
explicit authorization and backup plan". Deleting is policy level D and is a
hard stop (D-067). So "reclaimable" is a number on a page. This module opens
files to read and hash them; it has no write path at all, and the CLI opens
the database read-only.

The exact-dedup half is here too, because its arithmetic is the storage
report's: original bytes = distinct managed bytes + bytes saved. The near-
duplicate half lives in pipeline/neardup.py and is only rendered here.
"""

from __future__ import annotations

import os
import random
from dataclasses import dataclass, field
from pathlib import Path

from ..db import database as db

NOT_MEASURED = "not measured"

# Where the originals of each source live, in the words an operator needs.
WHERE = {
    "email": "Gmail — the message stays in the mailbox (read-only grant)",
    "attachment": "Gmail — inside the message that carried it",
    "photo": "iCloud watch folder (LMS_INBOX), retired to _done/ once filed",
}

POLICY = ("Report only. Removing anything is a `delete`: policy level D, refused "
          "outright by the never_delete_file hard stop (policy.py, D-067). Any "
          "cleanup is a separately approved action with a backup plan "
          "(addendum §0, §4).")


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------

@dataclass
class Tree:
    """Bytes on disk under one path. files/bytes None = could not measure."""
    label: str
    path: Path | None
    files: int | None = None
    bytes: int | None = None
    note: str = ""

    @property
    def measured(self) -> bool:
        return self.bytes is not None


@dataclass
class DupGroup:
    """One filed content, and every OTHER place its exact bytes were seen."""
    artifact_id: int
    sha256: str
    byte_size: int
    label: str
    canonical: tuple[str, str]
    locations: list[tuple[str, str, str]] = field(default_factory=list)  # source, ref, seen_at

    @property
    def saved(self) -> int:
        return self.byte_size * len(self.locations)


@dataclass
class ExactDedup:
    groups: list[DupGroup] = field(default_factory=list)
    resightings: int = 0          # same location read again: not a saving
    orphan_sightings: int = 0     # a sighting whose bytes are not filed (should be 0)
    superseded: int = 0           # earlier renderings of one source_ref
    superseded_bytes: int = 0

    @property
    def extra_locations(self) -> int:
        return sum(len(g.locations) for g in self.groups)

    @property
    def bytes_saved(self) -> int:
        return sum(g.saved for g in self.groups)


@dataclass
class SourceLine:
    source: str
    where: str
    artifacts: int = 0            # distinct contents first seen from this source
    bytes: int = 0                # their size as received
    filed: int = 0
    filed_bytes: int = 0
    extra_locations: int = 0      # exact copies seen again at a new location
    extra_bytes: int = 0
    unsized: int = 0              # rows with no recorded byte_size
    at_source: Tree | None = None

    @property
    def original_bytes(self) -> int:
        return self.bytes + self.extra_bytes


@dataclass
class Reclaim:
    label: str
    files: int | None
    bytes: int | None
    note: str


@dataclass
class SampleCheck:
    artifact_id: int
    label: str
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


@dataclass
class StorageReport:
    sources: list[SourceLine]
    managed: list[Tree]
    dedup: ExactDedup
    reclaimable: list[Reclaim]
    db_filed_bytes: int
    db_filed: int
    checks: list[str] = field(default_factory=list)
    not_measured: list[str] = field(default_factory=list)

    @property
    def original_bytes(self) -> int:
        return sum(s.original_bytes for s in self.sources)

    @property
    def distinct_bytes(self) -> int:
        return sum(s.bytes for s in self.sources)

    @property
    def managed_bytes(self) -> int | None:
        if any(not t.measured for t in self.managed):
            return None
        return sum(t.bytes or 0 for t in self.managed)


# Drives are read in place by read-only readers (D-083); what they hold is
# measured from the index the readers keep, not from the archive.
DRIVE_WHERE = "a drive, read in place by a read-only reader (D-083); originals stay there"


def drive_tree(conn, source_id: str) -> Tree:
    """What a drive holds, from its index."""
    r = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(size), 0) AS b, "
        "COALESCE(SUM(size IS NULL), 0) AS u FROM drive_files "
        "WHERE source_id = ? AND deleted_at IS NULL", (source_id,)).fetchone()
    note = "from the drive index"
    if r["u"]:
        note += (f"; {r['u']} file(s) have no size (Google Docs, iCloud placeholders) "
                 "and are not in the total")
    return Tree(f"{source_id} (indexed)", None, files=int(r["n"]), bytes=int(r["b"]),
                note=note)


def drive_reclaim(conn, source_id: str, filed_shas: set[str]) -> Reclaim:
    n = size = 0
    for r in conn.execute(
            "SELECT sha256, size FROM drive_files WHERE source_id = ? AND deleted_at IS NULL "
            "AND sha256 IS NOT NULL AND filed_revision = revision", (source_id,)):
        if r["sha256"] in filed_shas:
            n += 1
            size += int(r["size"] or 0)
    return Reclaim(f"{source_id} — drive files whose current bytes are filed", n, size,
                   "sha256 recorded when the reader fetched them; files never fetched "
                   f"are {NOT_MEASURED}. Not removable from here: the drive readers "
                   "are read-only")


# ---------------------------------------------------------------------------
# Exact duplicates (already collapsed at ingest; counted here)
# ---------------------------------------------------------------------------

def _location(source: str, source_ref: str | None, original_name: str | None) -> tuple[str, str]:
    """Where a copy was seen. The watcher records only a filename, so two
    drops of one name count as one location — which can only under-report a
    saving, never invent one.

    An attachment's location is its MESSAGE. Its source_ref is
    `<message id>/<attachment id>`, and Gmail issues a new attachment id every
    time the message is fetched, so on the Mac (Nov 3) each 12-hour re-poll
    looked like a new location: 460 "extra locations" and 251 MB "saved", nearly
    all of it one message read again. The same bytes in the same message are
    one attachment; a message carrying two identical files counts once, which
    under-reports, the safe direction.
    """
    if source == "attachment" and source_ref:
        return (source, source_ref.split("/", 1)[0])
    return (source or "", source_ref or original_name or "")


def exact_dedup(conn) -> ExactDedup:
    out = ExactDedup()
    filed = {}
    for r in conn.execute(
            "SELECT id, sha256, byte_size, source, source_ref, original_name, "
            "filed_path FROM artifacts WHERE filed_path IS NOT NULL"):
        filed[r["sha256"]] = DupGroup(
            artifact_id=int(r["id"]), sha256=r["sha256"],
            byte_size=int(r["byte_size"] or 0),
            label=Path(r["filed_path"]).name,
            canonical=_location(r["source"], r["source_ref"], r["original_name"]))

    seen: dict[str, set[tuple[str, str]]] = {}
    for r in conn.execute(
            "SELECT sha256, source, source_ref, original_name, seen_at "
            "FROM duplicate_refs ORDER BY id"):
        group = filed.get(r["sha256"])
        if group is None:
            out.orphan_sightings += 1
            continue
        loc = _location(r["source"], r["source_ref"], r["original_name"])
        known = seen.setdefault(r["sha256"], {group.canonical})
        if loc in known:
            out.resightings += 1
            continue
        known.add(loc)
        group.locations.append((loc[0], loc[1], r["seen_at"]))

    out.groups = sorted((g for g in filed.values() if g.locations),
                        key=lambda g: (-g.saved, g.artifact_id))

    row = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(byte_size), 0) FROM artifacts "
        "WHERE status = 'DUPLICATE'").fetchone()
    out.superseded, out.superseded_bytes = int(row[0]), int(row[1])
    return out


# ---------------------------------------------------------------------------
# Measuring disk
# ---------------------------------------------------------------------------

def measure_tree(label: str, path: Path | None, *, skip_dirs: tuple[str, ...] = (),
                 note: str = "") -> Tree:
    """Files and bytes under `path`, symlinks not followed. Missing = not measured."""
    if path is None:
        return Tree(label, None, note=note or f"{NOT_MEASURED}: no path configured")
    path = Path(path)
    if not path.is_dir():
        return Tree(label, path, note=note or f"{NOT_MEASURED}: {path} does not exist")
    files = total = 0
    for f in _walk(path, skip_dirs):
        files += 1
        total += f.stat().st_size
    return Tree(label, path, files, total, note)


def _walk(root: Path, skip_dirs: tuple[str, ...] = ()):
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in skip_dirs)
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if p.is_file() and not p.is_symlink():
                yield p


def is_sidecar(path: Path) -> bool:
    """`.meta.json`, or `<document>.txt` sitting beside its document (D-025).

    A rendered email is `<id>.eml.txt` with no `<id>.eml` beside it, so it is
    a document, which it is.
    """
    name = path.name
    if name.endswith(".meta.json"):
        return True
    return name.endswith(".txt") and path.with_name(name[:-4]).is_file()


def measure_archive(archive: Path) -> tuple[Tree, Tree]:
    docs = Tree("archive — documents", archive)
    side = Tree("archive — sidecars (.meta.json, extracted text)", archive)
    if not Path(archive).is_dir():
        note = f"{NOT_MEASURED}: {archive} does not exist"
        docs.note = side.note = note
        return docs, side
    docs.files = docs.bytes = side.files = side.bytes = 0
    for f in _walk(Path(archive)):
        t = side if is_sidecar(f) else docs
        t.files += 1
        t.bytes += f.stat().st_size
    return docs, side


def matching_files(root: Path | None, filed: set[str], *,
                   skip_dirs: tuple[str, ...] = ()) -> tuple[Tree, Tree, Tree]:
    """(first copy, further copies, not filed) of the files under `root`, by sha256.

    Hashing, not names: a reclaimable number built from filenames would
    count a re-scanned, different photo called IMG_0001.HEIC as safely filed.
    """
    first = Tree("", root, 0, 0)
    more = Tree("", root, 0, 0)
    other = Tree("", root, 0, 0)
    if root is None or not Path(root).is_dir():
        for t in (first, more, other):
            t.files = t.bytes = None
        return first, more, other
    seen: set[str] = set()
    for f in _walk(Path(root), skip_dirs):
        size = f.stat().st_size
        sha = db.sha256_file(f)
        t = other if sha not in filed else (more if sha in seen else first)
        seen.add(sha)
        t.files += 1
        t.bytes += size
    return first, more, other


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------

def source_lines(conn, dedup: ExactDedup) -> list[SourceLine]:
    """Per source, from the database. Superseded renderings (status DUPLICATE)
    are left out: they are earlier renderings of a message already counted."""
    lines: dict[str, SourceLine] = {}
    for r in conn.execute(
            "SELECT source, COUNT(*) AS n, COALESCE(SUM(byte_size), 0) AS b, "
            "SUM(filed_path IS NOT NULL) AS f, "
            "COALESCE(SUM(CASE WHEN filed_path IS NOT NULL THEN byte_size END), 0) AS fb, "
            "SUM(byte_size IS NULL) AS u "
            "FROM artifacts WHERE status != 'DUPLICATE' GROUP BY source ORDER BY source"):
        lines[r["source"]] = SourceLine(
            source=r["source"],
            where=WHERE.get(r["source"], DRIVE_WHERE if r["source"].startswith("storage:")
                            else "not described"),
            artifacts=int(r["n"]), bytes=int(r["b"]), filed=int(r["f"] or 0),
            filed_bytes=int(r["fb"]), unsized=int(r["u"] or 0))
    for r in conn.execute("SELECT DISTINCT source_id FROM drive_files ORDER BY source_id"):
        lines.setdefault(r["source_id"], SourceLine(source=r["source_id"], where=DRIVE_WHERE))
    for g in dedup.groups:
        for source, _ref, _seen in g.locations:
            line = lines.setdefault(source, SourceLine(
                source=source, where=WHERE.get(source, "not described")))
            line.extra_locations += 1
            line.extra_bytes += g.byte_size
    return list(lines.values())


def build_report(conn, roots, *, inbox: Path | None = None,
                 spool: Path | None = None, hash_sources: bool = True) -> StorageReport:
    """Measure everything that can be measured. `roots` is a filing.StorageRoots."""
    dedup = exact_dedup(conn)
    sources = source_lines(conn, dedup)
    not_measured: list[str] = []
    checks: list[str] = []

    filed_rows = conn.execute(
        "SELECT id, sha256, byte_size, filed_path FROM artifacts "
        "WHERE filed_path IS NOT NULL").fetchall()
    filed_shas = {r["sha256"] for r in filed_rows}
    db_filed_bytes = sum(int(r["byte_size"] or 0) for r in filed_rows)

    # ---- what is still at each source --------------------------------------
    for s in sources:
        if s.source == "photo":
            s.at_source = measure_tree("iCloud inbox (incl. _done, _failed)", inbox)
        elif s.source in ("email", "attachment"):
            s.at_source = Tree("Gmail", None, note=(
                f"{NOT_MEASURED}: Gmail's storage of the original message is not "
                "visible through a read-only grant"))
        elif s.source.startswith("storage:"):
            s.at_source = drive_tree(conn, s.source)
        else:
            s.at_source = Tree(s.source, None, note=f"{NOT_MEASURED}: no reader "
                                                    "for this source's storage")
        if s.at_source and not s.at_source.measured:
            not_measured.append(f"{s.source} originals at source — {s.at_source.note}")
        if s.unsized:
            checks.append(f"{s.unsized} {s.source} artifact(s) have no recorded "
                          "size; their bytes are missing from the totals above")
    not_measured.append("the managed OpenClaw root (R24), not built: " + NOT_MEASURED)

    # ---- what the LMS holds ------------------------------------------------
    docs, side = measure_archive(roots.archive)
    managed = [
        docs, side,
        measure_tree("_originals — byte-identical copy of every filed input",
                     roots.originals,
                     note="kept by design (filing.py): the archive copy may be "
                          "re-filed; this one is never rewritten"),
        measure_tree("quarantine — review queue, incl. _resolved/", roots.quarantine),
        measure_tree("mail spool — rendered messages and fetched attachments", spool),
    ]
    for t in managed:
        if not t.measured:
            not_measured.append(f"{t.label} — {t.note}")

    missing = [r for r in filed_rows if not Path(r["filed_path"]).is_file()]
    if missing:
        checks.append(f"{len(missing)} FILED artifact(s) are not at their filed_path "
                      f"(first: #{missing[0]['id']} {missing[0]['filed_path']})")
    if docs.measured and docs.bytes != db_filed_bytes:
        checks.append(f"archive documents on disk {docs.bytes:,} B ≠ database "
                      f"filed bytes {db_filed_bytes:,} B — investigate before "
                      "trusting either")
    orig = managed[2]
    if orig.measured and orig.files != len(filed_shas):
        checks.append(f"_originals holds {orig.files} file(s) for {len(filed_shas)} "
                      "filed content(s)")
    if dedup.orphan_sightings:
        checks.append(f"{dedup.orphan_sightings} duplicate sighting(s) point at bytes "
                      "that are not filed")

    # ---- reclaimable, report only ------------------------------------------
    reclaim: list[Reclaim] = []
    done = Path(inbox) / "_done" if inbox else None
    if not hash_sources:
        reclaim.append(Reclaim("iCloud inbox _done/", None, None,
                               f"{NOT_MEASURED}: hashing skipped (--no-hash)"))
        not_measured.append("reclaimable bytes — hashing skipped")
    elif done is None or not done.is_dir():
        reclaim.append(Reclaim("iCloud inbox _done/", None, None,
                               f"{NOT_MEASURED}: {done or 'LMS_INBOX not set'} "
                               "does not exist"))
        not_measured.append("reclaimable watch-folder originals — _done/ not found")
    else:
        first, more, other = matching_files(done, filed_shas)
        reclaim.append(Reclaim(
            "iCloud inbox _done/ — originals whose bytes are filed", first.files,
            first.bytes, "sha256 verified against the archive"))
        reclaim.append(Reclaim(
            "iCloud inbox _done/ — further copies of the same bytes", more.files,
            more.bytes, "exact duplicates within the source"))
        if other.files:
            reclaim.append(Reclaim(
                "iCloud inbox _done/ — NOT reclaimable", other.files, other.bytes,
                "bytes match nothing filed (quarantined, or changed after filing)"))
    reclaim.append(Reclaim("Gmail originals (mail and attachments)", None, None,
                           f"{NOT_MEASURED}: and not removable — the grant is "
                           "read-only (D-039)"))
    not_measured.append("Gmail originals — reclaimable bytes " + NOT_MEASURED)
    for s in sources:
        if s.source.startswith("storage:"):
            reclaim.append(drive_reclaim(conn, s.source, filed_shas))

    # LMS-internal copies are not "originals", and are kept apart so the two
    # numbers cannot be added together by accident.
    if hash_sources:
        sp_first, sp_more, _ = matching_files(spool, filed_shas)
        if sp_first.measured:
            reclaim.append(Reclaim(
                "LMS working copies — mail spool files already filed",
                sp_first.files + sp_more.files, sp_first.bytes + sp_more.bytes,
                "not originals: the LMS's own spool; bytes are in the archive and "
                "_originals"))
        resolved = Path(roots.quarantine) / "_resolved"
        rs_first, rs_more, _ = matching_files(resolved, filed_shas)
        if rs_first.measured:
            reclaim.append(Reclaim(
                "LMS working copies — quarantine/_resolved files already filed",
                rs_first.files + rs_more.files, rs_first.bytes + rs_more.bytes,
                "not originals: review-queue history (filing.retire_quarantine_copy)"))

    return StorageReport(sources=sources, managed=managed, dedup=dedup,
                         reclaimable=reclaim, db_filed_bytes=db_filed_bytes,
                         db_filed=len(filed_rows), checks=checks,
                         not_measured=not_measured)


# ---------------------------------------------------------------------------
# Sample verification — Phase 5 acceptance: "sample hash matches"
# ---------------------------------------------------------------------------

def verify_sample(conn, roots, n: int, *, seed: int | None = None) -> list[SampleCheck]:
    """Re-hash n random FILED artifacts: the archive copy and the _originals copy.

    Exact dedup rests on "a cryptographic hash of complete bytes and same file
    size" (addendum §4). This checks both, on disk, against what the database
    says — the managed copy is only a copy if it still is one.
    """
    rows = conn.execute(
        "SELECT id, sha256, byte_size, original_name, filed_path FROM artifacts "
        "WHERE filed_path IS NOT NULL ORDER BY id").fetchall()
    picked = random.Random(seed).sample(rows, min(n, len(rows)))
    out = []
    for r in sorted(picked, key=lambda r: r["id"]):
        check = SampleCheck(int(r["id"]), Path(r["filed_path"]).name)
        suffix = Path(r["original_name"] or "").suffix.lower()
        copies = (("archive", Path(r["filed_path"])),
                  ("_originals", Path(roots.originals) / f"{r['sha256']}{suffix}"))
        for what, p in copies:
            if not p.is_file():
                check.problems.append(f"{what} copy missing: {p}")
                continue
            size = p.stat().st_size
            if r["byte_size"] is not None and size != int(r["byte_size"]):
                check.problems.append(f"{what} size {size} ≠ recorded {r['byte_size']}")
            if db.sha256_file(p) != r["sha256"]:
                check.problems.append(f"{what} sha256 does not match")
        out.append(check)
    return out


# ---------------------------------------------------------------------------
# Rendering — plain text, like every other ops script
# ---------------------------------------------------------------------------

def fmt_bytes(n: int | None) -> str:
    if n is None:
        return NOT_MEASURED.upper()
    for unit, size in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= size:
            return f"{n:,} B ({n / size:.1f} {unit})"
    return f"{n:,} B"


def _rule(title: str) -> list[str]:
    return ["", title, "-" * len(title)]


def render_storage(r: StorageReport) -> str:
    out = ["STORAGE REPORT (addendum §4; R28)"]

    out += _rule("1. Original bytes by source (as received)")
    for s in r.sources:
        out.append(f"  {s.source:<11} {s.artifacts:>6} item(s)  {fmt_bytes(s.bytes)}")
        if s.extra_locations:
            out.append(f"  {'':<11} +{s.extra_locations:>5} exact copy(ies) seen at another "
                       f"location  {fmt_bytes(s.extra_bytes)}")
        out.append(f"  {'':<11} originals live in: {s.where}")
        if s.at_source is not None:
            if s.at_source.measured:
                out.append(f"  {'':<11} still at source: {s.at_source.files} file(s), "
                           f"{fmt_bytes(s.at_source.bytes)}")
            else:
                out.append(f"  {'':<11} still at source: {s.at_source.note}")
    out.append(f"  {'total':<11} {fmt_bytes(r.original_bytes)} received "
               f"= {fmt_bytes(r.distinct_bytes)} distinct + "
               f"{fmt_bytes(r.dedup.bytes_saved)} exact duplicates")

    out += _rule("2. Bytes copied into the LMS (measured on disk)")
    for t in r.managed:
        size = (f"{t.files:>6} file(s)  {fmt_bytes(t.bytes)}" if t.measured
                else t.note)
        out.append(f"  {t.label}")
        out.append(f"      {size}")
        if t.measured and t.note:
            out.append(f"      {t.note}")
    out.append(f"  total: {fmt_bytes(r.managed_bytes)}")
    out.append(f"  database cross-check: {r.db_filed} filed artifact(s), "
               f"{fmt_bytes(r.db_filed_bytes)}")

    out += _rule("3. Bytes saved in the managed collection by exact dedup")
    d = r.dedup
    out.append(f"  {fmt_bytes(d.bytes_saved)} — {d.extra_locations} additional source "
               f"location(s) of {len(d.groups)} filed document(s), one managed copy "
               "avoided each")
    out.append(f"  not counted: {d.resightings} re-read(s) of a location already "
               "seen (the poll window re-reads by design)")
    if d.superseded:
        out.append(f"  not counted: {d.superseded} superseded rendering(s), "
                   f"{fmt_bytes(d.superseded_bytes)} — an improved rendering of the "
                   "same message filed instead")

    out += _rule("4. Potentially reclaimable after a separately approved cleanup")
    for c in r.reclaimable:
        if c.bytes is None:
            out.append(f"  {c.label}: {c.note}")
        else:
            out.append(f"  {c.label}: {c.files} file(s), {fmt_bytes(c.bytes)}")
            out.append(f"      {c.note}")
    out.append(f"  {POLICY}")

    if r.checks:
        out += _rule("Findings")
        out += [f"  ! {c}" for c in r.checks]
    out += _rule("Not measured")
    out += [f"  - {m}" for m in r.not_measured] or ["  (everything above was measured)"]
    return "\n".join(out) + "\n"


def render_dedup(d: ExactDedup, near=None, *, limit: int = 25) -> str:
    """`near` is a neardup.NearDupResult, or None when it was not computed."""
    out = ["DEDUP REPORT (addendum §4; R25, R28)"]
    out += _rule("Exact duplicates — collapsed at ingest (sha256 of complete bytes)")
    out.append(f"  {len(d.groups)} filed document(s) seen at more than one location; "
               f"{d.extra_locations} extra location(s); {fmt_bytes(d.bytes_saved)} "
               "not copied again")
    for g in d.groups[:limit]:
        out.append(f"  #{g.artifact_id:<5} {g.sha256[:12]}  {fmt_bytes(g.byte_size)}  "
                   f"{g.label}")
        out.append(f"         first: {g.canonical[0]}:{g.canonical[1]}")
        for source, ref, seen in g.locations:
            out.append(f"         also:  {source}:{ref}  ({seen})")
    if len(d.groups) > limit:
        out.append(f"  … {len(d.groups) - limit} more")

    out += _rule("Near-duplicate CANDIDATES — text similarity; nothing merged or hidden")
    if near is None:
        out.append("  not computed this run")
        return "\n".join(out) + "\n"

    from ..pipeline import neardup
    out.append(f"  compared {len(near.compared)} document(s); "
               f"{len(near.too_short)} too short to compare; "
               f"{len(near.no_text)} with no extracted text")
    if near.truncated:
        out.append(f"  ! candidate search stopped at {neardup.MAX_CANDIDATE_PAIRS:,} "
                   "pairs — the list below is incomplete")
    for kind, title in ((neardup.NEAR_DUPLICATE, "near-duplicate candidates"),
                        (neardup.RELATED_VERSION, "related versions — keep both"),
                        (neardup.SIMILAR, "similar, not duplicates")):
        pairs = near.of_kind(kind)
        out.append("")
        out.append(f"  {title}: {len(pairs)}")
        for p in pairs[:limit]:
            vis = f" visual {p.visual:.2f}" if p.visual is not None else ""
            out.append(f"    #{p.a.key} ~ #{p.b.key}  jaccard {p.jaccard:.2f}  "
                       f"containment {p.containment:.2f}{vis}")
            out.append(f"        {p.a.label}")
            out.append(f"        {p.b.label}")
            out.append(f"        why: {p.reason}")
            if p.note:
                out.append(f"        note: {p.note}")
        if len(pairs) > limit:
            out.append(f"    … {len(pairs) - limit} more")
    if near.no_text:
        visual = (f"compared visually by {near.visual}" if near.visual else
                  "NOT compared: visual comparison is not available on this machine")
        out.append("")
        out.append(f"  {len(near.no_text)} document(s) with no extracted text — {visual}")
    return "\n".join(out) + "\n"


def render_samples(checks: list[SampleCheck]) -> str:
    out = ["SAMPLE HASH CHECK (Phase 5 acceptance: sample hash matches)"]
    bad = [c for c in checks if not c.ok]
    out.append(f"  {len(checks)} filed artifact(s) re-hashed: archive copy and "
               f"_originals copy, sha256 and size — {len(checks) - len(bad)} match, "
               f"{len(bad)} do not")
    for c in checks:
        out.append(f"  {'ok  ' if c.ok else 'FAIL'} #{c.artifact_id} {c.label}")
        out += [f"       {p}" for p in c.problems]
    return "\n".join(out) + "\n"
