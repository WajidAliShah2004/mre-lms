"""ops/authorise_gdrive.py and ops/authorise_dropbox.py (R5, D-083)."""

import base64
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))

import authorise_dropbox as adb     # noqa: E402
import authorise_gdrive as agd      # noqa: E402

READONLY = "https://www.googleapis.com/auth/drive.readonly"


def test_gdrive_accepts_exactly_drive_readonly():
    assert agd.scope_problem([READONLY]) is None
    assert agd.scope_problem([]) is None, "Google did not say: the token itself is checked"
    wider = agd.scope_problem([READONLY, "https://www.googleapis.com/auth/drive"])
    assert "more than asked" in wider and "Nothing was stored" in wider
    assert "did not grant" in agd.scope_problem(["https://www.googleapis.com/auth/gmail.readonly"])


def test_the_pkce_challenge_is_the_s256_of_the_verifier():
    verifier, challenge = adb.pkce_pair()
    assert 43 <= len(verifier) <= 128
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    assert challenge == expected.decode().rstrip("=")
    assert adb.pkce_pair()[0] != verifier


def test_the_dropbox_url_asks_for_an_offline_read_only_grant():
    url = adb.authorize_url("APPKEY", "CHALLENGE")
    q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    assert urlparse(url).netloc == "www.dropbox.com"
    assert q == {"client_id": "APPKEY", "response_type": "code", "code_challenge": "CHALLENGE",
                 "code_challenge_method": "S256", "token_access_type": "offline",
                 "scope": "files.metadata.read files.content.read account_info.read"}


def test_dropbox_accepts_exactly_the_three_scopes():
    assert adb.scope_problem("account_info.read files.content.read files.metadata.read") is None
    assert adb.scope_problem(None) is None
    assert "more than asked" in adb.scope_problem(
        "files.metadata.read files.content.read account_info.read files.content.write")
    assert "did not grant files.content.read" in adb.scope_problem(
        "files.metadata.read account_info.read")


def test_the_code_is_exchanged_with_the_verifier_and_no_secret():
    sent = []

    def post(url, data, headers):
        sent.append((url, parse_qs(data.decode())))
        return json.dumps({"refresh_token": "RT", "access_token": "AT"}).encode()

    assert adb.exchange("APPKEY", "CODE", "VERIFIER", post=post)["refresh_token"] == "RT"
    url, form = sent[0]
    assert url == "https://api.dropboxapi.com/oauth2/token"
    assert form == {"code": ["CODE"], "grant_type": ["authorization_code"],
                    "code_verifier": ["VERIFIER"], "client_id": ["APPKEY"]}
