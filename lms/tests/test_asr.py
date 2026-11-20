"""Local speech-to-text for voice notes (D-079). ffmpeg and mlx-whisper are
faked here; the real pair is exercised once on the Mac (WHATSAPP_APPLY.md)."""

import sys
import types

import pytest

from core.adapters import asr


def test_no_ffmpeg_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(asr.shutil, "which", lambda name: None)
    model = tmp_path / "model"
    model.mkdir()
    monkeypatch.setenv("LMS_ASR_MODEL", str(model))
    with pytest.raises(asr.ASRUnavailable, match="ffmpeg"):
        asr.transcribe(tmp_path / "a.opus", tmp_path)


def test_no_model_directory_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("LMS_ASR_MODEL", str(tmp_path / "absent"))
    with pytest.raises(asr.ASRUnavailable, match="model"):
        asr.transcribe(tmp_path / "a.opus", tmp_path)


def test_transcribes_offline_from_the_local_model(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")      # restored after the test
    model = tmp_path / "model"
    model.mkdir()
    monkeypatch.setenv("LMS_ASR_MODEL", str(model))
    monkeypatch.setattr(asr.shutil, "which", lambda name: "/opt/homebrew/bin/ffmpeg")
    ran = []
    monkeypatch.setattr(asr.subprocess, "run",
                        lambda argv, **kw: ran.append(argv))
    seen = {}

    def fake_transcribe(path, path_or_hf_repo):
        import os
        seen.update(path=path, repo=path_or_hf_repo,
                    offline=os.environ.get("HF_HUB_OFFLINE"))
        return {"text": "  Hi Matthew,\n the invoice is due Friday. "}
    monkeypatch.setitem(sys.modules, "mlx_whisper",
                        types.SimpleNamespace(transcribe=fake_transcribe))
    text = asr.transcribe(tmp_path / "a.opus", tmp_path)
    assert text == "Hi Matthew, the invoice is due Friday."
    assert ran[0][:2] == ["/opt/homebrew/bin/ffmpeg", "-nostdin"]
    assert "16000" in ran[0]
    assert seen["repo"] == str(model) and seen["offline"] == "1"


def _model_dir(tmp_path, monkeypatch):
    model = tmp_path / "model"
    model.mkdir()
    monkeypatch.setenv("LMS_ASR_MODEL", str(model))
    monkeypatch.setattr(asr.shutil, "which", lambda name: "/opt/homebrew/bin/ffmpeg")


def test_corrupt_note_is_unavailable_not_a_crash(tmp_path, monkeypatch):
    _model_dir(tmp_path, monkeypatch)

    def bad_ffmpeg(argv, **kw):
        raise asr.subprocess.CalledProcessError(1, argv)
    monkeypatch.setattr(asr.subprocess, "run", bad_ffmpeg)
    monkeypatch.setitem(sys.modules, "mlx_whisper",
                        types.SimpleNamespace(transcribe=None))
    with pytest.raises(asr.ASRUnavailable, match="ffmpeg could not decode"):
        asr.transcribe(tmp_path / "a.opus", tmp_path)


def test_model_error_is_unavailable_not_a_crash(tmp_path, monkeypatch):
    _model_dir(tmp_path, monkeypatch)
    monkeypatch.setattr(asr.subprocess, "run", lambda argv, **kw: None)

    def broken(path, path_or_hf_repo):
        raise RuntimeError("metal out of memory")
    monkeypatch.setitem(sys.modules, "mlx_whisper",
                        types.SimpleNamespace(transcribe=broken))
    with pytest.raises(asr.ASRUnavailable, match="speech model failed"):
        asr.transcribe(tmp_path / "a.opus", tmp_path)


NUMBA_LIKE = '''
try:
    import coverage
except ImportError:
    pass
else:
    coverage.types.Tracer      # numba's coverage_support subclasses this at import

def transcribe(path, path_or_hf_repo):
    return {"text": "on the way"}
'''


def _fresh_import(monkeypatch, shadow):
    """Put `shadow` first on sys.path, as every ops script puts ops/ first,
    with nothing named coverage or mlx_whisper imported yet."""
    monkeypatch.syspath_prepend(str(shadow))
    for name in ("mlx_whisper", "coverage"):
        monkeypatch.setitem(sys.modules, name, None)
        monkeypatch.delitem(sys.modules, name)


def test_ops_coverage_script_does_not_break_the_speech_import(tmp_path, monkeypatch):
    """Nov 19: ops/coverage.py shadowed the coverage package numba probes for,
    and every chat day with a voice note failed with "module 'coverage' has no
    attribute 'types'"."""
    _model_dir(tmp_path, monkeypatch)
    monkeypatch.setattr(asr.subprocess, "run", lambda argv, **kw: None)
    shadow = tmp_path / "ops"
    shadow.mkdir()
    (shadow / "coverage.py").write_text('"""the LMS coverage register"""\n')
    (shadow / "mlx_whisper.py").write_text(NUMBA_LIKE)
    _fresh_import(monkeypatch, shadow)
    assert asr.transcribe(tmp_path / "a.opus", tmp_path) == "on the way"
    assert sys.modules.get("coverage", "absent") is not None, "the hiding is undone"


def test_a_broken_speech_import_is_unavailable_not_a_crash(tmp_path, monkeypatch):
    _model_dir(tmp_path, monkeypatch)
    monkeypatch.setattr(asr.subprocess, "run", lambda argv, **kw: None)
    shadow = tmp_path / "site"
    shadow.mkdir()
    (shadow / "mlx_whisper.py").write_text("raise AttributeError('no types')\n")
    _fresh_import(monkeypatch, shadow)
    with pytest.raises(asr.ASRUnavailable, match="could not load"):
        asr.transcribe(tmp_path / "a.opus", tmp_path)


def test_missing_mlx_whisper_is_unavailable(tmp_path, monkeypatch):
    _model_dir(tmp_path, monkeypatch)
    monkeypatch.setattr(asr.subprocess, "run", lambda argv, **kw: None)
    monkeypatch.setitem(sys.modules, "mlx_whisper", None)
    with pytest.raises(asr.ASRUnavailable, match="mlx-whisper"):
        asr.transcribe(tmp_path / "a.opus", tmp_path)
