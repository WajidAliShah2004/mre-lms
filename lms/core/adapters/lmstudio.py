"""LM Studio client. The only place in this system that talks to a model.

Deliberately small and deliberately paranoid about one thing: the endpoint
must be loopback. That is checked on every call, not once at import, because
the check costs nothing and the property it guards — "no client or tax data
leaves this machine" — is the reason the client agreed to any of this.

Uses urllib rather than requests. One fewer dependency to audit, and this is
a handful of POSTs to localhost.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

DEFAULT_BASE_URL = os.environ.get("LMS_MODEL_BASE_URL", "http://localhost:1234/v1")

# Confirmed loaded on the Mac 2026-09-08. Tier names are the contract; these
# are substitutable. See openclaw/agents/agents.fragment.json.
TIER_MODELS = {
    "TIER-L1": "qwen3.6-35b-a3b-mlx",
    "TIER-L2": "qwen3.5-122b-a10b",
    "TIER-OCR": "glm-ocr",
    "TIER-A": "qwen3-asr-1.7b",
    "TIER-E": "text-embedding-qwen3-embedding-4b",
}

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


class ModelError(RuntimeError):
    pass


class NotLoopbackError(ModelError):
    """The endpoint is not on this machine. Refuse rather than proceed."""


def assert_loopback(base_url: str) -> None:
    host = urlparse(base_url).hostname
    if host not in LOOPBACK_HOSTS:
        raise NotLoopbackError(
            f"model endpoint {base_url!r} is not loopback (host={host!r}). "
            "D-003/D-004: all inference is local. Refusing to send content."
        )


_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.S | re.I)


def _first_json_object(text: str) -> str | None:
    """Extract the first balanced {...} from a string.

    Reasoning models wrap or precede their answer with prose often enough that
    scanning for the object is more robust than trusting the whole string to
    be JSON. Brace-counting rather than a regex, because JSON nests.
    """
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                return text[start:i + 1]
    return None


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    elapsed_s: float
    finish_reason: str | None = None
    channel: str = "content"     # which field the text actually came from

    def json(self) -> Any:
        """Parse the response as JSON.

        Tolerant of two things reasoning models actually do, and intolerant of
        everything else:

          * a <think>...</think> block wrapped around or before the answer
          * prose either side of the object

        Truncation still fails loudly. A model that hit a token limit returns
        JSON that is *nearly* valid, and half-parsing it would file a document
        against whichever fields happened to survive.
        """
        candidate = _THINK_BLOCK.sub("", self.text).strip()

        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

        extracted = _first_json_object(candidate)
        if extracted is not None:
            try:
                return json.loads(extracted)
            except json.JSONDecodeError:
                pass

        raise ModelError(
            f"model returned unparseable JSON (finish_reason="
            f"{self.finish_reason!r}, channel={self.channel!r}): "
            f"{candidate[:400]!r}"
        )


class LMStudio:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: int = 180):
        assert_loopback(base_url)
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # -- plumbing ---------------------------------------------------------

    def _post(self, path: str, payload: dict) -> dict:
        assert_loopback(self.base_url)      # re-checked per call, on purpose
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:400]
            raise ModelError(f"HTTP {exc.code} from LM Studio: {body}") from exc
        except urllib.error.URLError as exc:
            raise ModelError(
                f"cannot reach LM Studio at {self.base_url}: {exc.reason}. "
                "Is the local server running, and is a model loaded?"
            ) from exc

    # -- api --------------------------------------------------------------

    def models(self) -> list[str]:
        assert_loopback(self.base_url)
        with urllib.request.urlopen(f"{self.base_url}/models", timeout=30) as r:
            return [m["id"] for m in json.load(r).get("data", [])]

    def complete(self, *, tier: str, system: str, user: str,
                 schema: dict | None = None, temperature: float = 0.0,
                 max_tokens: int = 2048, retries: int = 2) -> Completion:
        """One chat completion.

        `schema` requests json_schema constrained decoding. That is the whole
        reason the classifier cannot emit anything but the agreed shape — it
        is enforced by the sampler, not by asking the model nicely in a prompt
        that arriving text might argue with.

        temperature defaults to 0. Classification is not a creative task, and
        a document should file the same way today and next Tuesday.
        """
        model = TIER_MODELS.get(tier)
        if model is None:
            raise ModelError(f"unknown tier {tier!r}; known: {sorted(TIER_MODELS)}")

        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "lms_response", "strict": True, "schema": schema},
            }

        last: Exception | None = None
        for attempt in range(retries + 1):
            started = time.monotonic()
            try:
                data = self._post("/chat/completions", payload)
            except ModelError as exc:
                last = exc
                if attempt < retries:
                    time.sleep(2 ** attempt)
                    continue
                raise
            elapsed = time.monotonic() - started

            try:
                choice = data["choices"][0]
                message = choice["message"]
            except (KeyError, IndexError) as exc:
                raise ModelError(f"unexpected response shape: {data}") from exc

            # --- reasoning models put the answer in the wrong field ---------
            #
            # Measured on the Mac, 2026-09-08, qwen3.6-35b-a3b-mlx via
            # LM Studio:
            #
            #   WITHOUT response_format:  content = the answer
            #                             reasoning_content = the thinking
            #   WITH response_format:     content = ""            <-- empty
            #                             reasoning_content = the constrained
            #                                                 schema output
            #
            # So the runtime routes the constrained decode into the reasoning
            # channel and leaves content blank. Reading only `content` — the
            # obvious, documented thing to do — yields an empty string on every
            # single classification, which surfaces as "model error" and
            # quarantines every document.
            #
            # No mocked-model test can catch this. It was found by running one
            # real document through on the machine.
            #
            # Preferring content and falling back is right in both directions:
            # if a future LM Studio release fixes this, content is populated
            # and the fallback never fires.
            text = (message.get("content") or "").strip()
            channel = "content"
            if not text:
                text = (message.get("reasoning_content") or "").strip()
                channel = "reasoning_content"

            return Completion(
                text=text,
                model=model,
                elapsed_s=elapsed,
                finish_reason=choice.get("finish_reason"),
                channel=channel,
            )

        raise ModelError(f"exhausted retries: {last}")

    def health(self) -> tuple[bool, str]:
        """Cheap liveness check for the 5-minute launchd ping (§4.6.5)."""
        try:
            loaded = self.models()
        except Exception as exc:
            return False, str(exc)
        if not loaded:
            return False, "server is up but no models are loaded"
        missing = [t for t, m in TIER_MODELS.items() if m not in loaded]
        if missing:
            return False, f"tiers not loaded: {', '.join(missing)}"
        return True, f"{len(loaded)} models loaded"
