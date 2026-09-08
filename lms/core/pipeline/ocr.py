"""Text extraction from photographed mail.

Two backends, tried in order:

  1. Apple Vision — on-device, fast, free, and very good at printed documents
     photographed at an angle, which is exactly what a phone produces.
  2. glm-ocr in LM Studio — the fallback when Vision returns little or returns
     it with low confidence.

Both are local. Neither sends the image anywhere (D-004).

Vision is reached through PyObjC and is OPTIONAL: if the bindings are absent
the module still imports and the model fallback carries the load. That keeps
the test suite runnable off a Mac, which matters because the workstation this
is written on is not one.

HEIC is converted with `sips`, which ships with macOS. The original is never
modified — spec §6.3 keeps the untouched original regardless.
"""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..adapters.lmstudio import LMStudio, ModelError

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tiff", ".tif", ".gif", ".bmp"}

# Files that are already text. No OCR, just read them.
#
# Added after D-024: the watched folder happily accepted a .txt invoice, found
# no OCR path for it, and passed an EMPTY body to the classifier. The model was
# asked to classify nothing, answered with low confidence — correctly — and the
# document quarantined. Nothing in the pipeline noticed it had never read the
# file.
TEXT_SUFFIXES = {".txt", ".text", ".md", ".csv", ".log"}

# Suffixes this module can turn into text at all. Anything outside it reaches
# the classifier with an empty body unless the caller supplied one, which is
# the failure D-024 describes. ingest.py checks against this set and refuses.
READABLE_SUFFIXES = IMAGE_SUFFIXES | TEXT_SUFFIXES

# Below this, Vision's output is treated as a failed read rather than a short
# document. A photograph of a bill that yields 40 characters usually means the
# page was blurred, cropped, or upside down — not that the bill was short.
MIN_USEFUL_CHARS = 60


class OCRError(RuntimeError):
    pass


@dataclass(frozen=True)
class OCRResult:
    text: str
    engine: str
    confidence: float | None = None
    pages: int = 1

    @property
    def usable(self) -> bool:
        return len(self.text.strip()) >= MIN_USEFUL_CHARS


# ---------------------------------------------------------------------------
# Apple Vision
# ---------------------------------------------------------------------------

def vision_available() -> bool:
    try:
        import Vision  # noqa: F401
        import Quartz  # noqa: F401
    except Exception:
        return False
    return True


def vision_ocr(path: Path) -> OCRResult:
    """On-device text recognition via the Vision framework.

    `accurate` rather than `fast`: a phone photo of a bill is a hard input,
    and this runs once per document rather than in a loop.
    """
    try:
        import Quartz
        import Vision
        from Foundation import NSURL
    except Exception as exc:                      # pragma: no cover - mac only
        raise OCRError(f"Vision bindings unavailable: {exc}") from exc

    url = NSURL.fileURLWithPath_(str(path))
    src = Quartz.CGImageSourceCreateWithURL(url, None)
    if src is None:
        raise OCRError(f"could not read image: {path}")
    image = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
    if image is None:
        raise OCRError(f"could not decode image: {path}")

    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setUsesLanguageCorrection_(True)

    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(image, None)
    ok, err = handler.performRequests_error_([request], None)
    if not ok:
        raise OCRError(f"Vision request failed: {err}")

    lines: list[str] = []
    confidences: list[float] = []
    for obs in request.results() or []:
        best = obs.topCandidates_(1)
        if best:
            lines.append(best[0].string())
            confidences.append(float(best[0].confidence()))

    return OCRResult(
        text="\n".join(lines),
        engine="apple-vision",
        confidence=(sum(confidences) / len(confidences)) if confidences else None,
    )


# ---------------------------------------------------------------------------
# LM Studio vision model
# ---------------------------------------------------------------------------

OCR_SYSTEM = """You transcribe documents. Return the text you can see, in reading order.

Preserve line breaks, amounts, dates, account numbers, and addresses exactly as
printed. Do not summarise, correct, complete, or interpret anything.

If a region is illegible write [illegible] rather than guessing. A guessed
account number or amount is worse than a gap, because a gap is visible and a
wrong number is not.

Return only the transcription."""


def model_ocr(path: Path, client: LMStudio | None = None) -> OCRResult:
    client = client or LMStudio()
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    suffix = path.suffix.lower().lstrip(".")
    mime = {"jpg": "jpeg", "tif": "tiff"}.get(suffix, suffix)

    payload_user = json.dumps([
        {"type": "text", "text": "Transcribe this document."},
        {"type": "image_url", "image_url": {"url": f"data:image/{mime};base64,{data}"}},
    ])

    try:
        completion = client.complete(
            tier="TIER-OCR", system=OCR_SYSTEM, user=payload_user,
            max_tokens=4096, temperature=0.0,
        )
    except ModelError as exc:
        raise OCRError(f"model OCR failed: {exc}") from exc

    return OCRResult(text=completion.text.strip(), engine="glm-ocr")


# ---------------------------------------------------------------------------
# Files that are already text
# ---------------------------------------------------------------------------

def read_text_file(path: Path) -> OCRResult:
    """Read a text file. Not OCR, but the same contract, so callers don't branch.

    `errors="replace"` rather than strict: a mojibake character in the middle
    of an invoice is a nuisance, and refusing the whole document over it would
    send a perfectly filable bill to the review queue.
    """
    path = Path(path)
    if path.suffix.lower() not in TEXT_SUFFIXES:
        raise OCRError(f"not a text file this module handles: {path.suffix}")
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise OCRError(f"could not read {path}: {exc}") from exc
    return OCRResult(text=text, engine="plain-text")


# ---------------------------------------------------------------------------
# Format conversion
# ---------------------------------------------------------------------------

def heic_to_pdf(src: Path, dest: Path) -> Path:
    """Convert with sips, which ships with macOS. The original is untouched.

    HEIC is an Apple container that plenty of tools cannot open. Archiving a
    PDF alongside means the filed copy is readable in ten years by something
    other than a Mac — while `_originals/` still holds the exact bytes the
    phone produced.
    """
    if shutil.which("sips") is None:
        raise OCRError("sips not found — this conversion only runs on macOS")
    dest.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["sips", "-s", "format", "pdf", str(src), "--out", str(dest)],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not dest.exists():
        raise OCRError(f"sips failed: {result.stderr.strip()}")
    return dest


def heic_to_jpeg(src: Path) -> Path:
    """Vision reads HEIC directly, but the vision model wants a common format."""
    if shutil.which("sips") is None:
        raise OCRError("sips not found — macOS only")
    tmp = Path(tempfile.mkdtemp()) / (src.stem + ".jpg")
    subprocess.run(["sips", "-s", "format", "jpeg", str(src), "--out", str(tmp)],
                   capture_output=True, text=True, check=True)
    return tmp


# ---------------------------------------------------------------------------
# The entry point
# ---------------------------------------------------------------------------

def extract_text(path: Path, *, client: LMStudio | None = None,
                 prefer_vision: bool = True) -> OCRResult:
    """Vision first, model second, and say which one produced the text.

    The engine is recorded in the sidecar. When a document turns out to have
    been read wrongly, the first question is always which engine read it, and
    guessing later is not possible.
    """
    path = Path(path)
    if path.suffix.lower() not in IMAGE_SUFFIXES:
        raise OCRError(f"not an image this module handles: {path.suffix}")

    errors: list[str] = []

    if prefer_vision and vision_available():
        try:
            result = vision_ocr(path)
            if result.usable:
                return result
            errors.append(
                f"vision returned {len(result.text.strip())} chars "
                f"(under {MIN_USEFUL_CHARS}), falling back")
        except OCRError as exc:
            errors.append(f"vision: {exc}")

    target = path
    if path.suffix.lower() in {".heic", ".heif"}:
        try:
            target = heic_to_jpeg(path)
        except Exception as exc:
            errors.append(f"heic conversion: {exc}")

    try:
        result = model_ocr(target, client=client)
    except OCRError as exc:
        errors.append(f"model: {exc}")
        raise OCRError("all OCR engines failed: " + " | ".join(errors)) from exc

    if not result.usable:
        # Deliberately not an exception. A document that resists OCR still
        # needs to reach the quarantine queue with whatever was read, because
        # a human can look at the photograph in two seconds.
        return OCRResult(text=result.text, engine=result.engine + "+unusable")
    return result
