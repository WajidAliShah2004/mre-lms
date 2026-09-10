#!/usr/bin/env python3
"""Write a small PDF with a real text layer, using nothing but the standard library.

WHY THIS EXISTS
---------------
Testing the D-029 PDF path needs a PDF, and macOS turns out to have no
dependable command-line way to make one:

  * `cupsfilter` wrote a 0-byte file
  * `sips` is an image tool and cannot convert RTF
  * /System/Library/Printers/Libraries/convert no longer ships
  * everything else means opening a GUI app and clicking Export

So the fixture is generated here instead, in about forty lines of PDF syntax.
It is deterministic, needs no third-party package, and — importantly — carries
its text as TEXT, which is the branch worth exercising first: a generated PDF
must be read through PDFKit's text layer and never OCR'd, because OCR of a
document that already has text introduces errors it did not have.

This is a TEST FIXTURE GENERATOR. It is not a PDF writer for the product, and
nothing in the pipeline imports it.

    .venv/bin/python ops/make_test_pdf.py out.pdf
    .venv/bin/python ops/make_test_pdf.py out.pdf --lines "ACME" "TOTAL: $10"
    .venv/bin/python ops/make_test_pdf.py scan.pdf --no-text   # image-only-ish
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT_LINES = [
    "ACME SUPPLY COMPANY",
    "120 Industrial Way, Queens NY 11101",
    "",
    "INVOICE 7010",
    "Date: 2026-09-06",
    "Bill to: MRECAI, 11 W Mill Dr, Great Neck NY 11021",
    "",
    "Consulting services, September 2026 .......... $3,240.00",
    "TOTAL DUE: $3,240.00",
    "Payment due 2026-10-06",
]


def _escape(s: str) -> str:
    """PDF string literals escape backslash and both parentheses."""
    return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def build_pdf(lines: list[str], *, with_text: bool = True) -> bytes:
    """A one-page PDF. Objects are assembled first, offsets measured after.

    The xref table records the byte offset of every object, so the offsets
    cannot be written until the objects exist. Getting this wrong produces a
    file that most viewers repair silently and PDFKit refuses — which would
    look like a bug in the reader rather than in the fixture.
    """
    if with_text:
        parts = ["BT", "/F1 11 Tf", "1 0 0 1 72 720 Tm", "13 TL"]
        for line in lines:
            parts.append(f"({_escape(line)}) Tj" if line else "()Tj")
            parts.append("T*")
        parts.append("ET")
        content = "\n".join(parts).encode("latin-1")
    else:
        # A page with no text at all: this is what a scan looks like to the
        # text layer, and it is what should send extract_pdf to OCR.
        content = b"0.9 g 72 600 468 120 re f"

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n"
        + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_at}\n%%EOF\n").encode()
    return bytes(out)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("out", type=Path)
    p.add_argument("--lines", nargs="*", default=None,
                   help="override the invoice text")
    p.add_argument("--no-text", action="store_true",
                   help="write a page with no text layer, to exercise the OCR branch")
    args = p.parse_args()

    data = build_pdf(args.lines if args.lines is not None else DEFAULT_LINES,
                     with_text=not args.no_text)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(data)

    # Say the size. A 0-byte file is exactly the failure that made this script
    # necessary, and it should never be discovered three commands later.
    size = args.out.stat().st_size
    print(f"wrote {args.out}  ({size} bytes, "
          f"{'text layer' if not args.no_text else 'NO text layer'})")
    if size == 0:
        print("ERROR: wrote nothing", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
