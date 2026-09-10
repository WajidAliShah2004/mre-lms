"""Re-run an ops script under the interpreter the daemon uses.

Every script in ops/ carries `#!/usr/bin/env python3`, which resolves to
whatever Python is first on PATH — Homebrew's, Apple's, or a venv somebody
left activated. The LaunchAgents name `.venv/bin/python` absolutely. D-032 is
what happens when those differ: verify_setup reported "PDFKit UNAVAILABLE —
EVERY PDF will quarantine" while the watcher was filing PDFs perfectly well,
because the two were running in different environments.

WHY sys.prefix AND NOT THE EXECUTABLE PATH
------------------------------------------
D-033. A venv's `python` is a symlink to the base interpreter, so resolving it
collapses every venv onto the same file:

    /tmp/va/bin/python -> /usr/bin/python3.10    sys.prefix = /tmp/va
    /tmp/vb/bin/python -> /usr/bin/python3.10    sys.prefix = /tmp/vb
    /usr/bin/python3   -> /usr/bin/python3.10    sys.prefix = /usr

All three compare equal by path. `sys.prefix` is the environment, and it is
the only one of the three that differs.

WHY THIS IS A FUNCTION AND NOT A BLOCK AT THE TOP OF EACH SCRIPT
----------------------------------------------------------------
It started as a block at module level, copied into three files. That made
those modules re-exec on IMPORT — so a test that imported backup.py to
exercise its snapshot logic instead re-launched the script, which exited
complaining that LMS_ARCHIVE_ROOT was unset. Code that cannot be imported
cannot be unit-tested, and the backup path is the one place where "we think
it works" is least acceptable.

Call it from main(). Import stays free of side effects.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def ensure_venv(guard: str, *, script: str | None = None) -> None:
    """Re-exec under <repo>/.venv if we are not already inside it.

    `guard` is an environment variable name that stops a loop if the venv's
    interpreter somehow still reports a different prefix. Returns normally
    when nothing needs doing, so callers can treat it as a no-op.
    """
    caller = Path(script or sys.argv[0]).resolve()
    venv = caller.parents[1] / ".venv"
    python = venv / "bin" / "python"

    if not python.exists():
        return                      # no venv here; run with what we have
    if Path(sys.prefix).resolve() == venv.resolve():
        return                      # already the right environment
    if os.environ.get(guard):
        return                      # already tried once; do not loop

    os.environ[guard] = "1"
    os.execv(str(python), [str(python), str(caller), *sys.argv[1:]])
