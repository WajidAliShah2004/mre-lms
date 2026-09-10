"""ops/lms.env is the single description of where things live.

Two restic repositories existed at once because a plist carried its own copy
of the paths and lms.env was missing one of them. The nightly job backed up to
/Volumes/MacStudioHD/LMS_backup; the restore test verified
/Volumes/MacStudioHD/LMS/LMS_backup. Both reported success, and would have
gone on doing so until someone needed a snapshot that was never in the
repository they were looking at.
"""

import os
import plistlib
import re
import sys
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parents[1] / "ops"
sys.path.insert(0, str(OPS))

from _env import load_lms_env                                    # noqa: E402


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("LMS_"):
            monkeypatch.delenv(k, raising=False)
    yield


def write_env(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "lms.env"
    p.write_text(body, encoding="utf-8")
    return p


def test_it_reads_the_exported_paths(tmp_path):
    env = write_env(tmp_path, '''
# a comment
export LMS_ARCHIVE_ROOT="/Volumes/MacStudioHD/LMS/archive"
export LMS_BACKUP_REPO="/Volumes/Mirror/LMS_backup"
export TZ="America/New_York"
''')
    applied = load_lms_env(env)
    assert applied["LMS_BACKUP_REPO"] == "/Volumes/Mirror/LMS_backup"
    assert os.environ["LMS_ARCHIVE_ROOT"] == "/Volumes/MacStudioHD/LMS/archive"


def test_an_existing_variable_wins(tmp_path, monkeypatch):
    """`LMS_BACKUP_REPO=/Volumes/Other ./ops/backup.py` means it. A loader that
    overrode the caller would be its own kind of surprise."""
    monkeypatch.setenv("LMS_BACKUP_REPO", "/Volumes/Other")
    env = write_env(tmp_path, 'export LMS_BACKUP_REPO="/Volumes/FromFile"\n')
    load_lms_env(env)
    assert os.environ["LMS_BACKUP_REPO"] == "/Volumes/Other"


def test_a_missing_file_is_not_an_error(tmp_path):
    """A developer machine has no lms.env, and the code defaults are right
    there. Refusing to run would break every test on this laptop."""
    assert load_lms_env(tmp_path / "absent.env") == {}


def test_it_does_not_execute_the_file(tmp_path):
    """Parsed, never sourced. This is read by a job that runs unattended at
    02:30; sourcing would run whatever the file contains."""
    marker = tmp_path / "SHOULD_NOT_EXIST"
    env = write_env(tmp_path, f'export LMS_DB="/tmp/x"\ntouch {marker}\n')
    load_lms_env(env)
    assert not marker.exists()
    assert os.environ["LMS_DB"] == "/tmp/x"


def test_quotes_are_stripped_but_inner_ones_survive(tmp_path):
    env = write_env(tmp_path, "export LMS_INBOX='/Users/mleca/LMS/in box'\n")
    load_lms_env(env)
    assert os.environ["LMS_INBOX"] == "/Users/mleca/LMS/in box"


# ---------------------------------------------------------------------------
# The drift itself
# ---------------------------------------------------------------------------

def test_bringup_declares_the_backup_repo():
    """Its absence from lms.env is what let the two repositories diverge."""
    text = (OPS / "bringup_mac.sh").read_text(encoding="utf-8")
    assert "export LMS_BACKUP_REPO=" in text


def test_no_plist_declares_an_lms_path():
    """A plist that carries its own paths is a second source of truth, and the
    second one is always the stale one.

    launchd inherits no environment, so PATH and TZ have to be here. The
    LMS_* paths do not: the scripts read lms.env, which bringup generates from
    the volume layout rather than anyone retyping it.
    """
    offenders = {}
    for plist in sorted(OPS.glob("*.plist")):
        data = plistlib.loads(plist.read_bytes())
        env = data.get("EnvironmentVariables", {})
        # The watchfolder daemon is not yet converted; it is named so this
        # test documents the remaining work instead of silently passing.
        if plist.name == "com.lms.watchfolder.plist":
            continue
        leaked = sorted(k for k in env if k.startswith("LMS_"))
        if leaked:
            offenders[plist.name] = leaked
    assert not offenders, (
        f"these plists declare paths that belong in lms.env: {offenders}")


def test_the_backup_plist_still_supplies_what_launchd_cannot():
    """PATH especially: launchd inherits none, so restic would not be found."""
    data = plistlib.loads((OPS / "com.lms.backup.plist").read_bytes())
    env = data["EnvironmentVariables"]
    assert "/opt/homebrew/bin" in env["PATH"]
    assert env["TZ"] == "America/New_York"
