"""Opening a PDF through PDFKit — the encrypted cases.

Oct 2 2026: the scheduled mail job died on every run with
`AttributeError: 'PDFDocument' object has no attribute 'isUnlocked'`.
PDFKit has `isEncrypted` and `isLocked`; `isUnlocked` never existed. The line
had only ever been reached by unencrypted PDFs, where `and` short-circuits.

The fakes below are deliberately strict: like a pyobjc proxy, they raise
AttributeError for any selector PDFKit does not have.
"""

import sys
from types import SimpleNamespace

import pytest

from core.pipeline import ocr


class _FakeDoc:
    def __init__(self, encrypted: bool, locked: bool):
        self._encrypted, self._locked = encrypted, locked

    def isEncrypted(self):
        return self._encrypted

    def isLocked(self):
        return self._locked

    def pageCount(self):
        return 1


def _pdfkit(monkeypatch, doc):
    alloc = SimpleNamespace(initWithURL_=lambda url: doc)
    monkeypatch.setitem(sys.modules, "Quartz", SimpleNamespace(
        PDFDocument=SimpleNamespace(alloc=lambda: alloc)))
    monkeypatch.setitem(sys.modules, "Foundation", SimpleNamespace(
        NSURL=SimpleNamespace(fileURLWithPath_=lambda p: p)))


def test_plain_pdf_opens(monkeypatch, tmp_path):
    doc = _FakeDoc(encrypted=False, locked=False)
    _pdfkit(monkeypatch, doc)
    assert ocr._open_pdf(tmp_path / "a.pdf") is doc


def test_owner_password_only_pdf_opens(monkeypatch, tmp_path):
    # Encrypted for permissions (no printing, no copying) but no password to
    # open. Common on invoices and statements. PDFKit reports it unlocked and
    # its text is readable — treating it as locked would file it unread.
    doc = _FakeDoc(encrypted=True, locked=False)
    _pdfkit(monkeypatch, doc)
    assert ocr._open_pdf(tmp_path / "a.pdf") is doc


def test_password_to_open_pdf_is_an_ocr_error_not_a_crash(monkeypatch, tmp_path):
    _pdfkit(monkeypatch, _FakeDoc(encrypted=True, locked=True))
    with pytest.raises(ocr.OCRError, match="password-protected"):
        ocr._open_pdf(tmp_path / "a.pdf")
