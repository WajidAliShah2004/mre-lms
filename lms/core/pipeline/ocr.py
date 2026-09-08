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

PDF_SUFFIXES = {".pdf"}

# Suffixes this module can turn into text at all. Anything outside it reaches
# the classifier with an empty body unless the caller supplied one, which is
# the failure D-024 describes. ingest.py checks against this set and refuses.
#
# This set and read_any()'s dispatch must agree. D-024 was precisely their
# disagreement, so read_any() is the ONLY way in and a test walks this set
# asserting every member has an arm.
READABLE_SUFFIXES = IMAGE_SUFFIXES | TEXT_SUFFIXES | PDF_SUFFIXES

# A scanned PDF costs one inference call per page. Twenty pages is a long
# insurance policy; two hundred is either a mistake or a way to occupy the
# machine for an hour. Pages past the cap are not read, and the engine string
# says so rather than the document silently appearing complete.
MAX_PDF_PAGES = 20

# Rendering resolution for scanned pages. 200 dpi is comfortably above what
# Vision needs for printed text and keeps a full page under a few megapixels.
PDF_RENDER_DPI = 200

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

    text, confidence = _vision_read_cgimage(image)
    return OCRResult(text=text, engine="apple-vision", confidence=confidence)


def _vision_read_cgimage(image) -> tuple[str, float | None]:
    """Run Vision over an in-memory CGImage.

    Split out of vision_ocr so a rasterised PDF page can use the identical
    recognition path without first being written to a temp file. A page that
    never touches the disk is also one that cannot be left behind on it.
    """
    import Vision

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

    return "\n".join(lines), (sum(confidences) / len(confidences)) if confidences else None


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
# PDF
#
# Most real mail arrives as a PDF, and there are two entirely different kinds
# wearing the same extension:
#
#   * a generated PDF — an emailed invoice, a policy document — which carries
#     its text as text. Reading it is exact and costs nothing.
#   * a scan — a photographed or faxed page wrapped in a PDF — which carries
#     only pixels and needs the same OCR as a phone photo.
#
# Trying the text layer first is not just an optimisation. OCR of a generated
# PDF INTRODUCES errors into a document that had none: a transposed digit in
# an account number that was perfectly legible in the source.
# ---------------------------------------------------------------------------

def pdfkit_available() -> bool:
    try:
        from Quartz import PDFDocument  # noqa: F401
    except Exception:
        return False
    return True


def _open_pdf(path: Path):
    from Foundation import NSURL
    from Quartz import PDFDocument

    doc = PDFDocument.alloc().initWithURL_(NSURL.fileURLWithPath_(str(path)))
    if doc is None:
        raise OCRError(f"could not open PDF: {path}")
    # An encrypted PDF is not a failed read, it is a locked document, and the
    # difference matters to whoever finds it in the review queue.
    if doc.isEncrypted() and not doc.isUnlocked():
        raise OCRError("PDF is password-protected — it is filed unread, "
                       "nothing was guessed about its contents")
    return doc


def pdf_text_layer(path: Path) -> OCRResult:
    """The text a generated PDF already contains. No OCR, no guessing."""
    try:
        doc = _open_pdf(Path(path))
    except ImportError as exc:                    # pragma: no cover - mac only
        raise OCRError(f"PDFKit unavailable: {exc}") from exc

    total = int(doc.pageCount())
    if total == 0:
        raise OCRError("PDF has no pages")

    read = min(total, MAX_PDF_PAGES)
    parts = []
    for i in range(read):
        page = doc.pageAtIndex_(i)
        if page is None:
            continue
        s = page.string()
        if s:
            parts.append(str(s))

    engine = "pdfkit-text"
    if read < total:
        engine += f"+truncated-{read}of{total}"
    return OCRResult(text="\n".join(parts), engine=engine, pages=read)


def pdf_page_images(path: Path, dpi: int = PDF_RENDER_DPI, limit: int = MAX_PDF_PAGES):
    """Render pages to in-memory CGImages for OCR.

    Yields (index, image, total_pages). Nothing is written to disk: a rendered
    page of someone's tax return has no business existing as a temp file.
    """
    import Quartz
    from Foundation import NSURL

    src = Quartz.CGPDFDocumentCreateWithURL(NSURL.fileURLWithPath_(str(path)))
    if src is None:
        raise OCRError(f"could not open PDF for rendering: {path}")

    total = int(Quartz.CGPDFDocumentGetNumberOfPages(src))
    scale = dpi / 72.0

    for i in range(min(total, limit)):
        page = Quartz.CGPDFDocumentGetPage(src, i + 1)      # 1-based
        if page is None:
            continue
        rect = Quartz.CGPDFPageGetBoxRect(page, Quartz.kCGPDFMediaBox)
        w = max(1, int(rect.size.width * scale))
        h = max(1, int(rect.size.height * scale))

        ctx = Quartz.CGBitmapContextCreate(
            None, w, h, 8, 0, Quartz.CGColorSpaceCreateDeviceRGB(),
            Quartz.kCGImageAlphaNoneSkipLast)
        if ctx is None:
            raise OCRError(f"could not allocate a {w}x{h} bitmap for page {i + 1}")

        # White ground. A PDF page is transparent by default and Vision reads
        # dark-on-transparent as dark-on-black, which recognises very badly.
        Quartz.CGContextSetRGBFillColor(ctx, 1.0, 1.0, 1.0, 1.0)
        Quartz.CGContextFillRect(ctx, Quartz.CGRectMake(0, 0, w, h))
        Quartz.CGContextScaleCTM(ctx, scale, scale)
        Quartz.CGContextDrawPDFPage(ctx, page)

        image = Quartz.CGBitmapContextCreateImage(ctx)
        if image is None:
            raise OCRError(f"could not render page {i + 1}")
        yield i, image, total


def pdf_ocr(path: Path) -> OCRResult:
    """OCR a scanned PDF, page by page, through Vision."""
    if not vision_available():
        raise OCRError("no OCR engine for a scanned PDF: Vision is unavailable "
                       "on this machine")

    pages: list[str] = []
    confidences: list[float] = []
    total = 0

    try:
        for i, image, total in pdf_page_images(Path(path)):
            text, conf = _vision_read_cgimage(image)
            if text.strip():
                pages.append(text)
            if conf is not None:
                confidences.append(conf)
    except ImportError as exc:                    # pragma: no cover - mac only
        raise OCRError(f"Quartz unavailable: {exc}") from exc

    read = min(total, MAX_PDF_PAGES)
    engine = "pdfkit+apple-vision"
    if read < total:
        engine += f"+truncated-{read}of{total}"

    return OCRResult(
        text="\n\n".join(pages), engine=engine, pages=read,
        confidence=(sum(confidences) / len(confidences)) if confidences else None,
    )


def extract_pdf(path: Path) -> OCRResult:
    """Text layer first, OCR only if there wasn't one.

    `usable` is the switch: a generated PDF returns hundreds of characters
    immediately, a scan returns nothing at all. The threshold also catches the
    awkward middle case — a scan with a cover page of generated text — where
    reading only the text layer would file a document on the strength of its
    letterhead.
    """
    path = Path(path)
    if path.suffix.lower() not in PDF_SUFFIXES:
        raise OCRError(f"not a PDF: {path.suffix}")

    errors: list[str] = []

    try:
        result = pdf_text_layer(path)
        if result.usable:
            return result
        errors.append(f"text layer held {len(result.text.strip())} chars "
                      f"(under {MIN_USEFUL_CHARS}) — treating as a scan")
    except OCRError as exc:
        # A password-protected PDF fails here and must not then be rendered:
        # rasterising it would produce pages of nothing and report them as a
        # successful empty read.
        if "password-protected" in str(exc):
            raise
        errors.append(f"text layer: {exc}")

    try:
        return pdf_ocr(path)
    except OCRError as exc:
        errors.append(f"page OCR: {exc}")
        raise OCRError("could not read PDF: " + " | ".join(errors)) from exc


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

def read_any(path: Path, *, client: LMStudio | None = None) -> OCRResult:
    """The only way in. Dispatches on suffix and never guesses.

    D-024 was READABLE_SUFFIXES and the code acting on it disagreeing: ingest
    checked one set, extracted against another, and a .txt fell through the
    gap into an empty body the model was then asked about. One entry point
    means the set and the dispatch are the same decision, and
    `test_every_readable_suffix_has_a_reader` walks the set to prove it.
    """
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix in TEXT_SUFFIXES:
        return read_text_file(path)
    if suffix in PDF_SUFFIXES:
        return extract_pdf(path)
    if suffix in IMAGE_SUFFIXES:
        return extract_text(path, client=client)

    raise OCRError(
        f"no reader for {suffix or '(no extension)'} — READABLE_SUFFIXES and "
        f"read_any() disagree, which is the D-024 defect exactly")


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
