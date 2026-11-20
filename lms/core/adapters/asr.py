"""Voice note to text, on this machine (D-079; TIER-A).

LM Studio serves chat and embeddings, not speech, so speech runs in-process:
ffmpeg decodes WhatsApp's .opus to 16 kHz mono WAV, and mlx-whisper runs
Whisper large-v3-turbo from a local directory with HF_HUB_OFFLINE=1, so
nothing is fetched at run time and no audio leaves the Mac (D-004).

Any failure raises ASRUnavailable: missing ffmpeg, mlx-whisper or the model,
a note ffmpeg cannot decode, and an error inside the model. The caller marks
the note "not transcribed" and the pass is PARTIAL, not FAILED.
"""

from __future__ import annotations

import contextlib
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

MAX_SECONDS = 600


class ASRUnavailable(RuntimeError):
    pass


@contextlib.contextmanager
def _no_shadowed_coverage():
    """Hide a plain coverage.py module while the speech stack loads.

    Every ops script puts ops/ first on sys.path, and ops/coverage.py (the
    coverage register) then answers `import coverage`. numba, which
    mlx-whisper pulls in, probes for the coverage package and uses
    coverage.types when it finds one; on Nov 19 every chat day with a voice
    note failed with "module 'coverage' has no attribute 'types'". The real
    package is a directory, so a single file is hidden: numba then sees no
    coverage at all, which is what it expects outside a test run."""
    try:
        spec = importlib.util.find_spec("coverage")
    except (ImportError, ValueError):
        spec = None
    if spec is None or spec.submodule_search_locations is not None:
        yield
        return
    missing = object()
    before = sys.modules.get("coverage", missing)
    sys.modules["coverage"] = None          # `import coverage` -> ImportError
    try:
        yield
    finally:
        if before is missing:
            sys.modules.pop("coverage", None)
        else:
            sys.modules["coverage"] = before


def model_dir() -> Path:
    return Path(os.environ.get(
        "LMS_ASR_MODEL", "~/LMS/models/whisper-large-v3-turbo")).expanduser()


def transcribe(src: Path, tmp_dir: Path) -> str:
    model = model_dir()
    if not model.is_dir():
        raise ASRUnavailable(f"speech model not found at {model}")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ASRUnavailable("ffmpeg not installed (brew install ffmpeg)")
    # Set before the import: huggingface_hub reads this flag at import time.
    os.environ["HF_HUB_OFFLINE"] = "1"
    try:
        with _no_shadowed_coverage():
            import mlx_whisper
    except ImportError as exc:
        raise ASRUnavailable("mlx-whisper not installed in the venv") from exc
    except Exception as exc:                                  # noqa: BLE001
        # A note left untranscribed, not a chat day that fails every hour.
        raise ASRUnavailable(
            f"could not load mlx-whisper: {type(exc).__name__}: {exc}") from exc
    wav = Path(tmp_dir) / (Path(src).stem + ".wav")
    try:
        subprocess.run([ffmpeg, "-nostdin", "-loglevel", "error", "-y", "-i",
                        str(src), "-ar", "16000", "-ac", "1", str(wav)],
                       check=True, timeout=300)
    except (subprocess.SubprocessError, OSError) as exc:
        raise ASRUnavailable(
            f"ffmpeg could not decode {Path(src).name}") from exc
    try:
        with _no_shadowed_coverage():           # numba may load it at first compile
            out = mlx_whisper.transcribe(str(wav), path_or_hf_repo=str(model))
        return " ".join(str(out.get("text", "")).split())
    except Exception as exc:
        raise ASRUnavailable(f"speech model failed: {exc}") from exc
