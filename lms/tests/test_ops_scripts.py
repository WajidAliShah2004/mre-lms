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


# --- D-052: verify_setup checks 9 and 10 -------------------------------------

def _vs():
    """Import verify_setup WITHOUT its re-exec guard.

    At import it os.execv()s into .venv/bin/python when sys.prefix differs —
    right for `./ops/verify_setup.py`, fatal inside pytest: the test process is
    replaced and the run ends mid-file with no failure reported.
    """
    import importlib.util
    import os
    from pathlib import Path
    os.environ["LMS_VERIFY_REEXEC"] = "1"
    p = Path(__file__).resolve().parents[1] / "ops" / "verify_setup.py"
    spec = importlib.util.spec_from_file_location("verify_setup_d052", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_zone_from_localtime_link():
    vs = _vs()
    assert vs.zone_from_localtime_link(
        "/var/db/timezone/zoneinfo/America/New_York") == "America/New_York"
    assert vs.zone_from_localtime_link("/usr/share/zoneinfo/Asia/Karachi") == "Asia/Karachi"
    assert vs.zone_from_localtime_link("/etc/something") is None


def test_offending_providers():
    vs = _vs()
    assert vs.offending_providers(
        {"lmstudio": {"baseUrl": "http://localhost:1234/v1"}}) == []
    assert vs.offending_providers(
        {"lmstudio": {"baseUrl": "http://127.0.0.1:1234/v1"}}) == []
    assert vs.offending_providers(
        {"lmstudio": {"baseUrl": "http://192.168.1.5:1234/v1"}})
    assert vs.offending_providers(
        {"openrouter": {"baseUrl": "https://openrouter.ai/api/v1"}})
    # a loopback URL under a different provider name is still unexpected
    assert vs.offending_providers({"custom": {"baseUrl": "http://localhost:4000/v1"}})


# --- Sept 29: array unmounted for two weeks, nobody told --------------------

def test_archive_volume_problem_reports_missing_mount(tmp_path):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))
    from _env import archive_volume_problem
    msg = archive_volume_problem("/Volumes/NoSuchArrayXYZ/LMS")
    assert msg and "not mounted" in msg
    (tmp_path / "LMS").mkdir()
    assert archive_volume_problem(str(tmp_path / "LMS")) is None
    assert "does not exist" in archive_volume_problem(str(tmp_path / "missing"))


# --- Oct 2: one bad attachment stopped every mail run -----------------------

def _poll_mail():
    import importlib.util
    p = Path(__file__).resolve().parents[1] / "ops" / "poll_mail.py"
    spec = importlib.util.spec_from_file_location("poll_mail_oct2", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_one_failing_message_does_not_stop_the_pass(tmp_path):
    """The window re-reads the same mail every run, so a message that crashes
    the loop crashes EVERY run until it ages out — and everything after it in
    the listing is never filed. It must be logged, counted, and skipped."""
    from types import SimpleNamespace
    from core.db import database as db
    pm = _poll_mail()
    conn = db.connect(tmp_path / "lms.db")

    def msg(i):
        return SimpleNamespace(message_id=f"m{i}", subject=f"s{i}", date="")

    def ingest(m):
        if m.message_id == "m1":
            raise AttributeError("'PDFDocument' object has no attribute 'isUnlocked'")
        filed = SimpleNamespace(status="FILED", path=f"/a/{m.message_id}")
        return SimpleNamespace(email=filed, attachments=[], skipped=[])

    counts = pm.file_messages(conn, [msg(0), msg(1), msg(2)], ingest)
    assert counts["filed"] == 2
    assert counts["failed"] == 1

    rows = conn.execute(
        "SELECT detail FROM actions_log WHERE action = 'MAIL_MESSAGE_FAILED'"
    ).fetchall()
    assert len(rows) == 1
    assert "m1" in rows[0][0] and "isUnlocked" in rows[0][0]


def test_poll_mail_sets_a_network_timeout():
    """httplib2 waits forever without one (Oct 3: a run sat in recv_into)."""
    src = (Path(__file__).resolve().parents[1] / "ops" / "poll_mail.py").read_text(
        encoding="utf-8")
    assert "socket.setdefaulttimeout(" in src


# --- Oct 3: more than one mailbox -------------------------------------------

def _registry_with(*emails):
    from types import SimpleNamespace
    ents = {f"E{i}": SimpleNamespace(emails=[e]) for i, e in enumerate(emails)}
    ents["X"] = SimpleNamespace(emails=[emails[0].upper()])   # same box, other case
    return SimpleNamespace(entities=ents)


def test_all_polls_authorised_mailboxes_and_names_the_rest():
    pm = _poll_mail()
    reg = _registry_with("matthew@mrecai.com", "matthew@mleca.com", "mattyeps@gmail.com")
    chosen, skipped = pm.choose_mailboxes(
        [], True, reg, granted=lambda a: a != "mattyeps@gmail.com")
    assert chosen == ["matthew@mrecai.com", "matthew@mleca.com"]
    assert skipped == ["mattyeps@gmail.com"]


def test_a_named_mailbox_is_polled_even_without_a_grant():
    """Asked for by name, it must fail loudly rather than vanish from the run."""
    pm = _poll_mail()
    reg = _registry_with("matthew@mrecai.com")
    chosen, skipped = pm.choose_mailboxes(["Matthew@MLECA.com"], False, reg,
                                          granted=lambda a: False)
    assert chosen == ["matthew@mleca.com"] and skipped == []


def test_one_broken_mailbox_does_not_stop_the_others(tmp_path, monkeypatch):
    """A revoked grant on one mailbox is logged; the next mailbox still runs."""
    from types import SimpleNamespace
    from core.db import database as db
    pm = _poll_mail()
    conn = db.connect(tmp_path / "lms.db")
    seen = []

    def build(address):
        if address == "bad@x.com":
            raise SystemExit("no OAuth grant for bad@x.com")
        seen.append(address)
        return SimpleNamespace(recent=lambda **kw: [])

    monkeypatch.setattr(pm, "build_client", build)
    args = SimpleNamespace(days=2, limit=500, query="")
    bad = sum(pm.poll_mailbox(conn, a, args, None, None, None, tmp_path)
              for a in ("bad@x.com", "good@x.com"))
    assert bad == 1 and seen == ["good@x.com"]
    actions = [r[0] for r in conn.execute("SELECT action FROM actions_log")]
    assert "MAIL_MAILBOX_FAILED" in actions and "MAIL_POLL_STOP" in actions


def _authorise():
    import importlib.util
    p = Path(__file__).resolve().parents[1] / "ops" / "authorise_gmail.py"
    spec = importlib.util.spec_from_file_location("authorise_gmail_oct3", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_a_grant_for_the_wrong_mailbox_is_refused():
    """The browser is usually already signed in as matthew@mrecai.com. A token
    for that mailbox stored as matthew@mleca.com would file MRECAI mail twice
    and MLE mail never."""
    au = _authorise()
    assert au.wrong_mailbox("matthew@mleca.com", "Matthew@MLECA.com") is None
    msg = au.wrong_mailbox("matthew@mleca.com", "matthew@mrecai.com")
    assert msg and "matthew@mrecai.com" in msg
    assert au.wrong_mailbox("matthew@mleca.com", "")
