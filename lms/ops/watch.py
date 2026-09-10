#!/usr/bin/env python3
"""The watched-folder daemon's entry point.

    ./ops/watch.py            # run forever
    ./ops/watch.py --once     # one scan, for checking it works

`core.adapters.watchfolder` is the daemon; this is the two lines around it that
belong to operations rather than to the pipeline — the venv guard and reading
ops/lms.env.

WHY THIS FILE EXISTS AT ALL
---------------------------
com.lms.watchfolder.plist used to run `python -m core.adapters.watchfolder`
directly, and therefore had to declare every LMS_* path itself. The comment in
it read: "Mirrors ops/lms.env ... they are not linked."

They were not linked, and they diverged. The plist declared

    LMS_INBOX = ~/Library/Mobile Documents/com~apple~CloudDocs/LMS/inbox

which is right — that is where the phone Shortcut writes. lms.env declared

    LMS_INBOX = ~/LMS/inbox

which is an empty directory nothing has ever written to. So the scheduled job
looked in the right place and anything run from a shell looked at nothing,
found nothing, and reported no error — the exact failure that plist's own
comment warns about, one variable further down.

That is D-035 for the second time. lms.env is generated from the volume layout
and is the only description of these paths that is derived rather than retyped,
so everything reads it and the plists carry only what launchd cannot supply any
other way: PATH, TZ, PYTHONUNBUFFERED. `test_plists.py` now fails on any plist
that declares an LMS_* variable, which is what makes this the last time.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _env import load_lms_env                              # noqa: E402
from _reexec import ensure_venv                            # noqa: E402


def main() -> int:
    ensure_venv("LMS_WATCH_REEXEC", script=__file__)
    load_lms_env()

    from core.adapters import watchfolder
    return watchfolder.run(once="--once" in sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
