"""Prepare untrusted content for a model.

Everything that arrives from outside passes through here first. The job is
narrow and boring on purpose:

  * strip HTML to text, so markup cannot smuggle structure
  * drop remote image references, so nothing is fetched
  * truncate to a budget, so a huge document cannot push the instructions out
    of the context window
  * wrap the result in markers, so the model can see where hostile text starts

What this does NOT do, and must not
-----------------------------------
It does not try to detect or remove prompt injection. That is unwinnable —
every filter is one paraphrase away from being bypassed, and a filter that
usually works is worse than none because it breeds confidence.

The defence is architectural, not textual. The component reading this text has
no tools (D-021: tools.allow is empty globally), returns a schema-constrained
JSON object, and every effect downstream is deterministic code. An injection
that "succeeds" produces a wrong field in a review queue.

So this module is hygiene, not security. Do not add a `looks_malicious()`
function here. Someone will eventually trust it.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

CONFIG = Path(__file__).resolve().parents[2] / "config"

_SCRIPT_STYLE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_IMG = re.compile(r"<img[^>]*>", re.I)
_WS = re.compile(r"[ \t]+")
_BLANKS = re.compile(r"\n{3,}")

# Zero-width and bidi controls. Not a security filter — see the module note —
# but these render invisibly, so text containing them does not say on screen
# what it says in the bytes, and a human reviewing the quarantine queue should
# not be shown something different from what the model read.
_INVISIBLE = re.compile(
    "[​-‏‪-‮⁦-⁩﻿­]"
)


def _rules() -> dict[str, Any]:
    return yaml.safe_load((CONFIG / "rules.yaml").read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Sanitised:
    text: str
    truncated: bool
    original_chars: int
    removed_invisible: int
    had_remote_images: bool

    def wrapped(self, open_marker: str, close_marker: str) -> str:
        return f"{open_marker}\n{self.text}\n{close_marker}"


def strip_html(raw: str) -> tuple[str, bool]:
    """HTML to plain text. Returns (text, had_remote_images)."""
    had_images = bool(_IMG.search(raw))
    out = _SCRIPT_STYLE.sub(" ", raw)
    out = _IMG.sub(" ", out)
    out = re.sub(r"<br\s*/?>", "\n", out, flags=re.I)
    out = re.sub(r"</(p|div|tr|h[1-6]|li)>", "\n", out, flags=re.I)
    out = _TAG.sub(" ", out)
    out = html.unescape(out)
    return out, had_images


def sanitise(raw: str, *, max_chars: int | None = None,
             is_html: bool | None = None) -> Sanitised:
    """Normalise one blob of untrusted text."""
    if raw is None:
        raw = ""
    original = len(raw)

    if is_html is None:
        is_html = bool(re.search(r"<(html|body|div|p|table|a|img)\b", raw, re.I))

    had_images = False
    if is_html:
        raw, had_images = strip_html(raw)

    cleaned, n_invisible = _INVISIBLE.subn("", raw)

    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = _WS.sub(" ", cleaned)
    cleaned = "\n".join(line.strip() for line in cleaned.split("\n"))
    cleaned = _BLANKS.sub("\n\n", cleaned).strip()

    if max_chars is None:
        max_chars = int(_rules()["sanitisation"]["max_chars_to_model"])

    truncated = len(cleaned) > max_chars
    if truncated:
        # Hard cut, with a visible marker. Deliberately NOT a summarise-then-
        # send: summarising untrusted text with a model and then treating the
        # summary as trustworthy just moves the injection one step earlier.
        cleaned = cleaned[:max_chars].rstrip() + "\n\n[TRUNCATED]"

    return Sanitised(
        text=cleaned,
        truncated=truncated,
        original_chars=original,
        removed_invisible=n_invisible,
        had_remote_images=had_images,
    )


def wrap_untrusted(raw: str, *, max_chars: int | None = None,
                   is_html: bool | None = None) -> tuple[str, Sanitised]:
    """Sanitise and wrap in the configured markers. The normal entry point."""
    s = _rules()["sanitisation"]
    result = sanitise(raw, max_chars=max_chars, is_html=is_html)
    return result.wrapped(s["wrapper_open"], s["wrapper_close"]), result
