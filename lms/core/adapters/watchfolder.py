"""Watch the iCloud inbox and ingest what lands in it.

Polling, not FSEvents. That is a deliberate downgrade:

  * iCloud materialises files asynchronously. FSEvents fires when the
    PLACEHOLDER appears, which is often several seconds before the bytes do,
    so an event-driven reader reads an empty file and has to re-check anyway.
  * A poll loop has no PyObjC dependency and survives being killed mid-scan
    with no state to reconcile.
  * The latency that matters is "under 90 seconds" (the Day-7 acceptance
    test), not "under 100 milliseconds". A 5-second poll spends 5 of a 90
    second budget.

Two hazards this handles that a naive `for f in dir` does not:

  1. **Dataless files.** iCloud shows a filename whose bytes are still in the
     cloud. Reading it returns nothing or blocks. `brctl download` requests
     materialisation, then we wait for the size to become real.
  2. **Half-written files.** A photo still uploading grows between polls.
     Ingesting it produces a truncated image, a bad OCR, and a hash that will
     never match the finished file — so the finished file gets ingested again
     as a different document. A file must be size-stable across two
     consecutive polls before it is touched.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..db import database as db
from ..pipeline import filing, ingest
from ..pipeline.classify import Artifact, Classifier
from ..pipeline.registry import Registry, load_registry

POLL_SECONDS = 5
STABLE_POLLS = 2          # consecutive unchanged sizes before we touch it
MATERIALISE_TIMEOUT = 120  # iCloud can be slow on a large scan
DATALESS_SUFFIX = ".icloud"

# The Shortcut writes into one of these. A subfolder is easier to hit reliably
# from Shortcuts than a filename convention, and it survives a rename.
TAG_DIRS = {"business": "BUSINESS", "personal": "PERSONAL"}

SKIP_NAMES = {".DS_Store", ".localized"}


@dataclass
class WatchState:
    """Size seen per path, and how many polls it has been unchanged."""
    sizes: dict[Path, int] = field(default_factory=dict)
    stable: dict[Path, int] = field(default_factory=dict)

    def observe(self, path: Path, size: int) -> bool:
        """Record a size. True once the file has been stable long enough."""
        if self.sizes.get(path) == size:
            self.stable[path] = self.stable.get(path, 0) + 1
        else:
            self.sizes[path] = size
            self.stable[path] = 0
        return self.stable[path] >= STABLE_POLLS

    def forget(self, path: Path) -> None:
        self.sizes.pop(path, None)
        self.stable.pop(path, None)


def is_dataless(path: Path) -> bool:
    """A cloud placeholder, not the file.

    Two forms: the classic `.foo.pdf.icloud` stub, and a real filename whose
    blocks are not local. st_blocks == 0 with a non-zero size catches the
    second, which is what modern macOS actually produces.
    """
    if path.name.startswith(".") and path.name.endswith(DATALESS_SUFFIX):
        return True
    try:
        st = path.stat()
    except OSError:
        return True
    return st.st_size > 0 and getattr(st, "st_blocks", 1) == 0


def materialise(path: Path, timeout: int = MATERIALISE_TIMEOUT) -> Path:
    """Ask iCloud for the bytes and wait for them.

    `brctl download` is a request, not a transfer — it returns immediately and
    the download happens behind it. Waiting is the whole job.
    """
    real = path
    if path.name.startswith(".") and path.name.endswith(DATALESS_SUFFIX):
        real = path.with_name(path.name[1:-len(DATALESS_SUFFIX)])

    if shutil.which("brctl"):
        subprocess.run(["brctl", "download", str(path)],
                       capture_output=True, text=True)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if real.exists() and not is_dataless(real):
            return real
        time.sleep(1)
    raise TimeoutError(f"iCloud did not materialise {path} within {timeout}s")


def tag_for(path: Path, inbox: Path) -> str | None:
    """Personal or Business, from the subfolder the Shortcut dropped it in."""
    try:
        rel = path.relative_to(inbox)
    except ValueError:
        return None
    if len(rel.parts) < 2:
        return None
    return TAG_DIRS.get(rel.parts[0].lower())


def candidates(inbox: Path) -> list[Path]:
    out: list[Path] = []
    for p in sorted(inbox.rglob("*")):
        if not p.is_file():
            continue
        if p.name in SKIP_NAMES or p.name.startswith("._"):
            continue
        if "_done" in p.parts or "_failed" in p.parts:
            continue
        out.append(p)
    return out


def _retire(path: Path, inbox: Path, subdir: str) -> Path:
    """Move a handled file aside. Never delete — hard rule.

    Keeps the inbox small so scans stay cheap, and keeps the file recoverable.
    The archive already holds a copy and `_originals/` holds the exact bytes,
    so this copy is redundant, not precious.
    """
    dest_dir = inbox / subdir
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / path.name
    n = 1
    while dest.exists():
        dest = dest_dir / f"{path.stem}-{n}{path.suffix}"
        n += 1
    shutil.move(str(path), str(dest))
    return dest


def process_once(conn, roots: filing.StorageRoots, registry: Registry,
                 classifier: Classifier, inbox: Path,
                 state: WatchState) -> list[ingest.IngestResult]:
    """One scan. Returns what was ingested this pass."""
    results: list[ingest.IngestResult] = []
    inbox = Path(inbox)
    if not inbox.exists():
        return results

    for path in candidates(inbox):
        try:
            if is_dataless(path):
                try:
                    path = materialise(path)
                except TimeoutError as exc:
                    db.log_action(conn, "ICLOUD_TIMEOUT", detail=str(exc)[:400])
                    continue

            size = path.stat().st_size
            if size == 0:
                continue
            if not state.observe(path, size):
                continue          # still settling

            tag = tag_for(path, inbox)
            art = Artifact(source="photo", source_ref=path.name)
            if tag == "BUSINESS":
                art.subject = "[tagged BUSINESS by the phone shortcut]"
            elif tag == "PERSONAL":
                art.subject = "[tagged PERSONAL by the phone shortcut]"

            result = ingest.ingest_file(
                conn, roots, registry, classifier, path,
                source="photo", source_ref=path.name, artifact=art)

            results.append(result)
            state.forget(path)
            _retire(path, inbox, "_done" if result.ok else "_failed")

        except Exception as exc:                 # one bad file must not stop the loop
            db.log_action(conn, "INGEST_ERROR",
                          detail=f"{path.name}: {type(exc).__name__}: {exc}"[:400])
            state.forget(path)
            try:
                _retire(path, inbox, "_failed")
            except Exception:
                pass

    return results


def run(inbox: Path | None = None, *, once: bool = False) -> int:
    """Entry point for the launchd job."""
    inbox = Path(inbox or os.environ.get("LMS_INBOX", "~/LMS/inbox")).expanduser()
    roots = filing.StorageRoots.from_env()
    roots.ensure()
    inbox.mkdir(parents=True, exist_ok=True)
    for tag in TAG_DIRS:
        (inbox / tag).mkdir(exist_ok=True)

    registry = load_registry()

    outstanding = registry.placeholders()
    if outstanding:
        print(f"WARNING: C16 unanswered for {', '.join(outstanding)} — "
              "documents for these entities will file against placeholder "
              "legal names.", flush=True)

    conn = db.connect(os.environ.get("LMS_DB", str(roots.archive.parent / "lms.db")))
    classifier = Classifier(registry)
    state = WatchState()

    db.log_action(conn, "WATCHFOLDER_START", detail=str(inbox))

    try:
        while True:
            for r in process_once(conn, roots, registry, classifier, inbox, state):
                print(f"[{r.status}] {r.path}", flush=True)
            if once:
                return 0
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        return 0
    finally:
        db.log_action(conn, "WATCHFOLDER_STOP")
        conn.close()


if __name__ == "__main__":
    import sys
    raise SystemExit(run(once="--once" in sys.argv))
