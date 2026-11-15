"""No drive reader can change a drive (R5, D-083; addendum §2.3)."""

import re
from pathlib import Path

CORE = Path(__file__).resolve().parents[1] / "core"
READERS = [CORE / "adapters" / n for n in ("drive.py", "icloud_drive.py", "gdrive.py",
                                           "dropbox.py")]
FORBIDDEN = [
    r"\bos\.(rename|replace|remove|unlink|rmdir|chmod|chown)\b",
    r"\bshutil\.(move|rmtree|copytree)\b",
    r"\.(unlink|rename|rmdir|chmod|touch)\(",
    r"\bfiles\(\)\.(delete|update|create|copy|emptyTrash)\b",
    r"\bpermissions\(\)",
    r"files/(delete|move|upload|copy|create_folder|save_url)",
    r"\bsharing/",
]


def code(path: Path) -> str:
    return "\n".join(line for line in path.read_text(encoding="utf-8").splitlines()
                     if not line.lstrip().startswith("#"))


def test_no_reader_calls_anything_that_changes_a_drive():
    for path in READERS:
        text = code(path)
        for pattern in FORBIDDEN:
            assert not re.search(pattern, text), f"{path.name} matches {pattern}"


def test_the_engine_only_removes_its_own_temp_directories():
    text = code(CORE / "pipeline" / "drives.py")
    for pattern in (r"\bos\.(rename|replace|remove|unlink)\b", r"\bshutil\.move\b"):
        assert not re.search(pattern, text)
    assert text.count("shutil.rmtree(") == 2, "only the pass and per-file temp directories"


def test_the_grants_are_read_only():
    from core.adapters import dropbox, gdrive
    assert gdrive.SCOPES == ("https://www.googleapis.com/auth/drive.readonly",)
    assert dropbox.SCOPES == ("files.metadata.read", "files.content.read", "account_info.read")
