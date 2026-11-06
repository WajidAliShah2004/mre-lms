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

import os
import shutil
import subprocess
from pathlib import Path

MAX_SECONDS = 600


class ASRUnavailable(RuntimeError):
    pass


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
        import mlx_whisper
    except ImportError as exc:
        raise ASRUnavailable("mlx-whisper not installed in the venv") from exc
    wav = Path(tmp_dir) / (Path(src).stem + ".wav")
    try:
        subprocess.run([ffmpeg, "-nostdin", "-loglevel", "error", "-y", "-i",
                        str(src), "-ar", "16000", "-ac", "1", str(wav)],
                       check=True, timeout=300)
    except (subprocess.SubprocessError, OSError) as exc:
        raise ASRUnavailable(
            f"ffmpeg could not decode {Path(src).name}") from exc
    try:
        out = mlx_whisper.transcribe(str(wav), path_or_hf_repo=str(model))
        return " ".join(str(out.get("text", "")).split())
    except Exception as exc:
        raise ASRUnavailable(f"speech model failed: {exc}") from exc
