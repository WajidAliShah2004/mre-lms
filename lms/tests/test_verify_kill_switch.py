"""verify_setup [11]: the kill switch must answer, not just be present.

On Nov 3 the rotated @MREOC18bot token was stored wrong. The bot ran, polled
into HTTP 404, and pre-flight still said "All checks passed" because [11]
only looked for a token in the Keychain. Telegram is faked; launchd too.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))

# _halt binds os.killpg as a default argument, which Windows lacks. Nothing
# here kills anything, so a stand-in for the import keeps this file runnable
# off the Mac (test_halt.py itself is Mac-only).
_SHIM = not hasattr(os, "killpg")
if _SHIM:
    os.killpg = None
import _halt                                                 # noqa: E402
import halt_bot                                              # noqa: E402
import verify_setup                                          # noqa: E402
if _SHIM:
    del os.killpg

TOKEN = "123456789:AAsecretsecretsecretsecretsecretsec"
OWNER = "987654321"


@pytest.fixture
def keychain(monkeypatch):
    store = {halt_bot.TOKEN_SERVICE: TOKEN, halt_bot.OWNER_SERVICE: OWNER}
    monkeypatch.setattr(halt_bot, "keychain", store.get)
    return store


@pytest.fixture
def verify(monkeypatch, keychain):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(verify_setup, "failures", [])
    monkeypatch.setattr(verify_setup, "warnings", [])
    monkeypatch.setattr(_halt, "loaded", lambda: {_halt.SELF_LABEL: 4242})
    monkeypatch.setattr(_halt, "read_flag", lambda: None)
    return verify_setup


def telegram(monkeypatch, answer):
    calls = []

    def call(self, method, **params):
        calls.append(method)
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(halt_bot.Telegram, "call", call)
    return calls


def test_a_token_telegram_accepts_passes(verify, monkeypatch, capsys):
    calls = telegram(monkeypatch, {"ok": True,
                                   "result": {"username": "MREOC18bot"}})
    verify.check_kill_switch()
    assert calls == ["getMe"]
    assert verify.failures == []
    out = capsys.readouterr().out
    assert "@MREOC18bot" in out
    assert TOKEN not in out


def test_a_token_telegram_refuses_fails(verify, monkeypatch, capsys):
    telegram(monkeypatch, RuntimeError(
        'getMe: HTTP 404 {"ok":false,"error_code":404,"description":"Not Found"}'))
    verify.check_kill_switch()
    assert len(verify.failures) == 1
    assert "HTTP 404" in verify.failures[0]
    assert "wrong or revoked" in verify.failures[0]
    assert TOKEN not in capsys.readouterr().out


def test_no_token_fails_without_calling_telegram(verify, keychain, monkeypatch):
    calls = telegram(monkeypatch, {"ok": True, "result": {}})
    del keychain[halt_bot.TOKEN_SERVICE]
    verify.check_kill_switch()
    assert calls == []
    assert any("no halt bot token" in f for f in verify.failures)
