"""Rotating the backup passphrase.

restic has no recovery path. Removing the old key before proving the new one
works makes every snapshot permanently unreadable — the backup is still there
and nothing on earth opens it.

D-037 is why these tests exist rather than a careful runbook: on this project a
destructive step already ran once on the strength of a verification that had
passed against a different repository. The rule that came out of it was that a
destructive step must be gated on the specific fact in question, and here the
fact is "the new passphrase reads the same snapshots the old one did".

Nothing here touches a real repository. The order of operations is the thing
under test, and it is testable against a fake restic.
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parents[1] / "ops"
sys.path.insert(0, str(OPS))

import rotate_backup_password as rot                          # noqa: E402


def code_only(path: Path) -> str:
    """The source with comments and docstrings removed.

    A test that greps a whole source file fails on the comment explaining why
    the forbidden thing is not used — and a test that fails on its own
    documentation teaches people to delete the documentation. This module
    argues at length about `--password-command` and RESTIC_PASSWORD precisely
    because it does not use them.

    `ast.unparse` after stripping docstrings, rather than a token walk: comments
    are gone for free, and there is no chance of the traversal being subtly
    wrong about where a docstring begins.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = node.body
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(ast.fix_missing_locations(tree))


# ---------------------------------------------------------------------------
# The generated passphrase
# ---------------------------------------------------------------------------

def test_it_is_generated_not_typed():
    """Four attempts at pasting into a no-echo prompt on this Mac produced an
    empty password twice and a five-character one once — bracketed paste turns
    the text into escape codes. Removing the human removed the class."""
    a, b = rot.generate(), rot.generate()
    assert a != b
    assert len(a) == rot.NEW_LENGTH == 40


def test_it_is_long_enough_to_matter():
    """This key is the only thing protecting client tax and NPI data in an
    off-machine copy. A short one makes the encryption decorative."""
    import backup as backup_ops
    assert rot.NEW_LENGTH >= backup_ops.MIN_PASSWORD_CHARS * 3


def test_it_has_no_shell_metacharacters():
    """It passes through `security -i` and a pipe. Anything a shell would
    reinterpret is a silent truncation waiting to happen."""
    for _ in range(200):
        p = rot.generate()
        assert p.isalnum(), p


# ---------------------------------------------------------------------------
# Neither passphrase may reach the process table
# ---------------------------------------------------------------------------

def test_no_passphrase_is_passed_on_the_command_line(monkeypatch):
    """`ps` output is readable by every process on the machine."""
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"] = argv
        seen["env"] = kw.get("env")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(rot.subprocess, "run", fake_run)
    rot.add_key("OLD-SECRET-VALUE", "NEW-SECRET-VALUE", "/tmp/repo")

    joined = " ".join(seen["argv"])
    assert "OLD-SECRET-VALUE" not in joined
    assert "NEW-SECRET-VALUE" not in joined
    assert "/dev/fd/" in joined, "expected the passphrases to arrive by fd"


def test_no_passphrase_is_passed_in_the_environment(monkeypatch):
    """RESTIC_PASSWORD in the environment is visible in ps for the life of the
    call, which is the reason it is not used here."""
    seen = {}

    def fake_run(argv, **kw):
        seen["env"] = kw.get("env")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(rot.subprocess, "run", fake_run)
    rot.add_key("OLD-SECRET-VALUE", "NEW-SECRET-VALUE", "/tmp/repo")

    env = seen["env"] or {}
    for value in env.values():
        assert "SECRET-VALUE" not in str(value)


def test_the_source_names_no_temp_file():
    """A plaintext passphrase on disk, however briefly, on the machine whose
    theft this backup exists to survive."""
    src = code_only(Path(rot.__file__))
    for bad in ("NamedTemporaryFile", "mkstemp", "mktemp"):
        assert bad not in src, f"{bad} would put the passphrase on disk"


def test_restic_reads_its_passwords_from_files_not_arguments():
    src = code_only(Path(rot.__file__))
    assert "--password-file" in src
    assert "--password-command" not in src, (
        "a password-command string ends up in an argv")
    assert "RESTIC_PASSWORD" not in src, (
        "an env var is visible in ps for the life of the call")


# ---------------------------------------------------------------------------
# The order, which is the whole thing
# ---------------------------------------------------------------------------

def test_the_removal_is_gated_on_a_verified_read():
    """`key remove` must come AFTER a snapshot count taken with the NEW
    passphrase, and that count must match.

    Read out of the source rather than executed, because the failure mode is
    an ordering one and the only honest way to test ordering against a real
    repository is to have a real repository.
    """
    src = Path(rot.__file__).read_text(encoding="utf-8")

    verify = src.index("n_new = snapshot_count(new, repo)")
    compare = src.index("if n_new != n:")
    remove = src.index('run(["key", "remove"')

    assert verify < compare < remove, (
        "the old key is removed before the new one is proved to work — that "
        "makes every snapshot permanently unreadable")


def test_a_failed_verification_leaves_the_old_key():
    src = Path(rot.__file__).read_text(encoding="utf-8")
    failure = src.index("The new key does not work")
    remove = src.index('run(["key", "remove"')
    assert failure < remove
    assert "has NOT been removed" in src


def test_an_empty_repository_is_refused():
    """Rotating the key of a repository with no snapshots protects nothing,
    and would leave someone believing the rotation had been exercised."""
    src = Path(rot.__file__).read_text(encoding="utf-8")
    assert "if n == 0:" in src


def test_the_keychain_write_is_verified_before_the_old_key_goes():
    src = Path(rot.__file__).read_text(encoding="utf-8")
    readback = src.index("back = backup_ops.restic_password()")
    remove = src.index('run(["key", "remove"')
    assert readback < remove, (
        "the old key is removed before the Keychain is confirmed to hold the "
        "new passphrase — the nightly job would then fail with no way back")


def test_the_new_passphrase_is_printed_exactly_once():
    """It has to reach the password manager: a secret that exists only in a
    Keychain dies with the Mac, which is the failure this backup exists to
    survive. Printed once, and only after everything else succeeded."""
    src = Path(rot.__file__).read_text(encoding="utf-8")
    assert src.count("    {new}\\n") == 1
    printed = src.index("THE NEW BACKUP PASSPHRASE")
    remove = src.index('run(["key", "remove"')
    assert remove < printed, "printed before the rotation completed"


# ---------------------------------------------------------------------------
# One place computes the repository path
# ---------------------------------------------------------------------------

def test_the_repo_path_comes_from_backup_py(monkeypatch, tmp_path):
    """D-035: the nightly job and the restore test each computed their own,
    disagreed, and both reported success against different repositories."""
    import backup as backup_ops

    monkeypatch.setenv("LMS_ARCHIVE_ROOT", str(tmp_path / "archive"))
    monkeypatch.setenv("LMS_BACKUP_REPO", str(tmp_path / "repo"))
    assert backup_ops.repo_path() == tmp_path / "repo"

    src = Path(rot.__file__).read_text(encoding="utf-8")
    assert "backup_ops.repo_path()" in src
    assert "LMS_BACKUP_REPO" not in src, (
        "a second place deriving the repository path from the environment")
