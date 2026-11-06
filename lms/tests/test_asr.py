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
