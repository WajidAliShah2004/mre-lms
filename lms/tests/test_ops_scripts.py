"""Repo hygiene for the ops scripts.

A script with a `#!/usr/bin/env python3` line is making a promise: that
`./ops/thing.py` runs it. If the executable bit is missing the promise fails
with `zsh: permission denied`, which reads like a permissions problem on the
machine rather than a missing mode bit in git.

This happened twice. The first time produced the commit "verify_setup: mark
executable". The second time `./ops/backup.py` was refused in the middle of
verifying the first backup the system has ever taken — six scripts written
during this build were all 100644, because the development machine mounts the
repo over a filesystem where `chmod` silently does nothing.

The git index is therefore the only place the mode is real, and the only
place worth asserting it.
"""

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
OPS = Path(__file__).resolve().parents[1] / "ops"


def indexed_modes() -> dict[str, str]:
    """Path -> mode, straight from the git index.

    Not os.access(X_OK): on a Windows or network mount every file can look
    executable, or none can, and neither answer says what will be checked out
    on the Mac.
    """
    try:
        out = subprocess.run(["git", "ls-files", "-s", "lms/ops"],
                             cwd=REPO, capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pytest.skip("git unavailable")
    if out.returncode != 0:
        pytest.skip("not a git checkout")

    modes = {}
    for line in out.stdout.splitlines():
        meta, _, path = line.partition("\t")
        modes[path] = meta.split()[0]
    return modes


def has_shebang(path: Path) -> bool:
    try:
        return path.read_text(encoding="utf-8", errors="replace").startswith("#!")
    except OSError:
        return False


def test_every_ops_script_with_a_shebang_is_executable():
    modes = indexed_modes()
    broken = [
        p for p, mode in sorted(modes.items())
        if p.endswith(".py") and mode != "100755"
        and has_shebang(REPO / p)
    ]
    assert not broken, (
        "these declare themselves runnable and are not marked executable, so "
        f"./{'  ./'.join(broken)} fails with 'permission denied': {broken}")


def test_a_module_that_is_not_a_script_is_not_marked_executable():
    """_reexec.py is imported, never run. Marking it executable would be a
    small lie about what it is, and an invitation to run it."""
    modes = indexed_modes()
    wrong = [
        p for p, mode in sorted(modes.items())
        if p.endswith(".py") and mode == "100755"
        and not has_shebang(REPO / p)
    ]
    assert not wrong, f"executable but not runnable: {wrong}"
