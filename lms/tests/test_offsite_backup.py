"""The off-site copy to Backblaze B2 (R29).

No network and no restic: restic is a fake that records what it was asked and
reads the passwords off the pipes exactly as restic would, and the Keychain is
a dict. What is under test is the part that decides whether the copy is worth
anything — that it does nothing until configured, that both passwords arrive
by fd and nowhere else, that retention cannot silently keep everything, that a
failure is loud and a missing configuration is not, and that the setup script
refuses a key that is not bucket-only.
"""

import io
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parents[1] / "ops"
sys.path.insert(0, str(OPS))

import backup                                                    # noqa: E402
import offsite_backup as off                                     # noqa: E402
import restore_test                                              # noqa: E402
import set_offsite_backup as setoff                              # noqa: E402
from core.db import database as db                               # noqa: E402
from core.reports import brief as reports                        # noqa: E402

LOCAL_PW = "LOCALpassphrase0123456789abcdef"
OFF_PW = "OFFSITEpassphrase9876543210zyxwv"
KEY_ID = "005a1b2c3d4e5f60000000001"
APP_KEY = "K005SeCrEtApPlIcAtIoNkEyVaLuE01"
REPO = "b2:mre-lms-backup:lms"
SECRETS = [LOCAL_PW, OFF_PW, KEY_ID, APP_KEY]


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

@pytest.fixture
def keychain(monkeypatch):
    items: dict[str, str] = {}
    monkeypatch.setattr(off, "keychain_read", lambda service: items.get(service))
    return items


def configure(items: dict, **override) -> dict:
    items.update({off.REPO_SERVICE: REPO, off.PASSWORD_SERVICE: OFF_PW,
                  off.KEY_ID_SERVICE: KEY_ID, off.APP_KEY_SERVICE: APP_KEY})
    items.update(override)
    return items


SUBCOMMANDS = ("copy", "forget", "init", "restore", "snapshots", "unlock")


class FakeRestic:
    """Stands in for subprocess.run. Reads /dev/fd/N the way restic does."""

    def __init__(self, results: dict | None = None):
        self.calls: list[dict] = []
        self.results = results or {}

    def __call__(self, argv, **kw):
        assert argv[0] == "restic", argv
        passwords = {}
        for flag in ("--password-file", "--from-password-file"):
            if flag in argv:
                path = argv[argv.index(flag) + 1]
                assert path.startswith("/dev/fd/"), path
                passwords[flag] = os.read(int(path.rsplit("/", 1)[1]), 4096).decode()
        sub = next(a for a in argv if a in SUBCOMMANDS)
        self.calls.append({"argv": list(argv), "env": kw.get("env"),
                           "passwords": passwords, "sub": sub,
                           "pass_fds": kw.get("pass_fds")})
        result = self.results.get(sub, (0, f"{sub} ok\n", ""))
        if isinstance(result, BaseException):
            raise result
        return subprocess.CompletedProcess(argv, *result)

    @property
    def subs(self) -> list[str]:
        return [c["sub"] for c in self.calls]

    def call(self, sub: str) -> dict:
        return next(c for c in self.calls if c["sub"] == sub)


@pytest.fixture
def restic(monkeypatch):
    fake = FakeRestic()
    monkeypatch.setattr(off.subprocess, "run", fake)
    monkeypatch.setattr(off.shutil, "which", lambda name: f"/opt/homebrew/bin/{name}")
    return fake


@pytest.fixture
def lmsdb(tmp_path):
    path = tmp_path / "lms.db"
    db.connect(path).close()
    return path


def actions(db_path: Path) -> list[tuple[str, str]]:
    conn = db.connect(db_path)
    try:
        return [(r[0], r[1] or "") for r in conn.execute(
            "SELECT action, detail FROM actions_log ORDER BY id").fetchall()]
    finally:
        conn.close()


def offsite_rows(db_path: Path) -> list[str]:
    return [a for a, _ in actions(db_path) if a.startswith("OFFSITE_")]


LOCAL_REPO = Path("/Users/mleca/LMS/backup")


def run(db_path, **kw):
    return off.run_after_local_backup(db_path, LOCAL_REPO, LOCAL_PW, **kw)


# ---------------------------------------------------------------------------
# Unconfigured: inert, visible, and logged once
# ---------------------------------------------------------------------------

def test_unconfigured_does_nothing_and_succeeds(keychain, restic, lmsdb, capsys):
    """The bucket does not exist yet. No restic, no failure, exit 0 — and one
    line in the nightly log saying the local copy is the only one."""
    assert run(lmsdb) == 0
    assert restic.calls == []
    out = capsys.readouterr()
    assert "not configured (R29)" in out.out
    assert out.err == "", "an unconfigured machine must not produce failure noise"


def test_not_configured_is_logged_once_not_every_night(keychain, restic, lmsdb):
    """365 identical rows a year is how an audit log stops being read."""
    for _ in range(5):
        run(lmsdb)
    assert offsite_rows(lmsdb) == ["OFFSITE_NOT_CONFIGURED"]


def test_becoming_unconfigured_again_is_logged_again(keychain, restic, lmsdb):
    """Configured, copied, then the Keychain items vanished: that is a change
    of state and the log should show when it happened."""
    configure(keychain)
    assert run(lmsdb) == 0
    keychain.clear()
    run(lmsdb)
    run(lmsdb)
    assert offsite_rows(lmsdb) == ["OFFSITE_COPY_COMPLETED",
                                   "OFFSITE_NOT_CONFIGURED"]


def test_unconfigured_reads_only_the_location_item(monkeypatch, restic, lmsdb):
    """One `security` call a night on an unconfigured machine, not four."""
    asked = []
    monkeypatch.setattr(off, "keychain_read",
                        lambda s: asked.append(s) or None)
    run(lmsdb)
    assert asked == [off.REPO_SERVICE]


def test_a_dry_run_writes_nothing(keychain, restic, lmsdb):
    assert run(lmsdb, dry_run=True) == 0
    configure(keychain)
    assert run(lmsdb, dry_run=True) == 0
    assert restic.calls == []
    assert offsite_rows(lmsdb) == []


# ---------------------------------------------------------------------------
# Configured: copy, then retention, with the passwords on pipes
# ---------------------------------------------------------------------------

def test_configured_copies_then_forgets(keychain, restic, lmsdb):
    configure(keychain)
    assert run(lmsdb) == 0
    assert restic.subs == ["copy", "forget"]
    assert offsite_rows(lmsdb) == ["OFFSITE_COPY_COMPLETED"]


def test_the_copy_reads_from_local_and_writes_to_b2(keychain, restic, lmsdb):
    configure(keychain)
    run(lmsdb)
    argv = restic.call("copy")["argv"]
    assert argv[argv.index("-r") + 1] == REPO
    assert argv[argv.index("--from-repo") + 1] == str(LOCAL_REPO)


def test_each_password_reaches_its_own_repository_by_fd(keychain, restic, lmsdb):
    """restic copy opens two repositories. Swapped passwords would fail every
    night; a shared one would put a known-exposed key (D-049) on the copy that
    leaves the building."""
    configure(keychain)
    run(lmsdb)
    copy = restic.call("copy")
    assert copy["passwords"] == {"--password-file": OFF_PW,
                                 "--from-password-file": LOCAL_PW}
    assert len(copy["pass_fds"]) == 2
    assert restic.call("forget")["passwords"] == {"--password-file": OFF_PW}


def test_no_secret_is_on_any_command_line(keychain, restic, lmsdb):
    """argv is in `ps` for every process on the machine."""
    configure(keychain)
    run(lmsdb)
    for c in restic.calls:
        joined = " ".join(c["argv"])
        for s in SECRETS:
            assert s not in joined, (c["sub"], s)


def test_repo_passwords_are_not_in_the_environment(keychain, restic, lmsdb,
                                                   monkeypatch):
    """Including one inherited from an operator's shell: it must neither win
    over the Keychain nor be handed to a repository it was never meant for."""
    monkeypatch.setenv("RESTIC_PASSWORD", "INHERITED-WRONG-PASSWORD")
    monkeypatch.setenv("RESTIC_REPOSITORY", "/somewhere/else")
    configure(keychain)
    run(lmsdb)
    for c in restic.calls:
        env = c["env"]
        assert "RESTIC_PASSWORD" not in env
        assert "RESTIC_REPOSITORY" not in env
        for value in env.values():
            assert LOCAL_PW not in value and OFF_PW not in value


def test_b2_credentials_reach_restic_and_only_restic(keychain, restic, lmsdb):
    """restic accepts B2 credentials only from its environment. The child's,
    never this process's own."""
    configure(keychain)
    run(lmsdb)
    env = restic.call("copy")["env"]
    assert env["B2_ACCOUNT_ID"] == KEY_ID
    assert env["B2_ACCOUNT_KEY"] == APP_KEY
    assert "B2_ACCOUNT_KEY" not in os.environ


def test_retention_flags(keychain, restic, lmsdb):
    """90 daily, 24 monthly long-terms (spec §11.4), a litigation hold that is
    never forgotten, and pruning so forgotten data actually goes."""
    configure(keychain)
    run(lmsdb)
    argv = restic.call("forget")["argv"]
    assert "--prune" in argv
    assert argv[argv.index("--keep-daily") + 1] == "90"
    assert argv[argv.index("--keep-monthly") + 1] == "24"
    assert argv[argv.index("--keep-tag") + 1] == "hold"


def test_retention_groups_by_host_not_by_paths(keychain, restic, lmsdb):
    """The one that would fail silently.

    backup.py snapshots a fresh mkdtemp staging directory every night, so no
    two snapshots share a path set. restic's default grouping (host,paths)
    would make every snapshot a group of one, keep the newest of each — keep
    everything — and report success. Tags fail the same way: each snapshot
    carries its own timestamp tag.
    """
    configure(keychain)
    run(lmsdb)
    argv = restic.call("forget")["argv"]
    assert argv[argv.index("--group-by") + 1] == "host"


def test_nothing_secret_reaches_the_audit_log(keychain, restic, lmsdb):
    configure(keychain)
    run(lmsdb)
    restic.results["copy"] = (1, "", f"Fatal: auth failed for {KEY_ID}:{APP_KEY}")
    run(lmsdb)
    for _, detail in actions(lmsdb):
        for s in SECRETS:
            assert s not in detail


# ---------------------------------------------------------------------------
# Failure: loud, logged, and never mistaken for a failed local backup
# ---------------------------------------------------------------------------

def test_a_failed_copy_is_logged_and_non_zero(keychain, restic, lmsdb, capsys):
    configure(keychain)
    restic.results["copy"] = (1, "", "Fatal: unable to open repository")
    assert run(lmsdb) == 1
    rows = [(a, d) for a, d in actions(lmsdb) if a.startswith("OFFSITE_")]
    assert rows[-1][0] == "OFFSITE_COPY_FAILED"
    assert "step=copy" in rows[-1][1] and "unable to open" in rows[-1][1]
    assert "FAILED" in capsys.readouterr().err


def test_a_failed_copy_does_not_go_on_to_delete(keychain, restic, lmsdb):
    """A repository we could not write to tonight is not one to start
    forgetting snapshots in."""
    configure(keychain)
    restic.results["copy"] = (1, "", "boom")
    run(lmsdb)
    assert restic.subs == ["copy"]


def test_a_secret_in_restic_stderr_is_scrubbed_from_the_terminal(
        keychain, restic, lmsdb, capsys):
    configure(keychain)
    restic.results["copy"] = (1, "", f"b2 said no to {APP_KEY} / {OFF_PW}")
    run(lmsdb)
    err = capsys.readouterr().err
    assert APP_KEY not in err and OFF_PW not in err
    assert "[redacted]" in err


def test_a_hung_copy_times_out_as_a_failure(keychain, restic, lmsdb):
    """A stuck TCP connection must not hold the 02:30 job — and the
    repository lock — until someone notices."""
    configure(keychain)
    restic.results["copy"] = subprocess.TimeoutExpired("restic", 1)
    assert run(lmsdb) == 1
    assert offsite_rows(lmsdb) == ["OFFSITE_COPY_FAILED"]
    assert restic.subs == ["copy"]


def test_a_failed_prune_keeps_the_completed_copy(keychain, restic, lmsdb):
    """The data is off-site; only the pruning failed. The brief's watch stays
    satisfied, the exit code does not."""
    configure(keychain)
    restic.results["forget"] = (1, "", "repository is already locked")
    assert run(lmsdb) == 1
    assert offsite_rows(lmsdb) == ["OFFSITE_COPY_COMPLETED",
                                   "OFFSITE_PRUNE_FAILED"]


def test_half_configured_fails_loudly_rather_than_going_quiet(keychain, restic,
                                                              lmsdb):
    """Location set, a credential missing: someone started, and the nightly
    copy cannot work. That is a failure, not 'not configured'."""
    configure(keychain)
    del keychain[off.APP_KEY_SERVICE]
    assert run(lmsdb) == 1
    assert offsite_rows(lmsdb) == ["OFFSITE_COPY_FAILED"]
    assert restic.calls == []


def test_a_short_offsite_password_is_refused(keychain, restic, lmsdb):
    """D-035: an encrypted backup whose key is "" is a plaintext backup with
    extra steps — and this one sits on a third party's disks."""
    configure(keychain, **{off.PASSWORD_SERVICE: "short"})
    assert run(lmsdb) == 1
    assert restic.calls == []


def test_a_malformed_location_is_refused(keychain, restic, lmsdb):
    configure(keychain, **{off.REPO_SERVICE: "s3:somewhere-else"})
    assert run(lmsdb) == 1
    assert restic.calls == []


# ---------------------------------------------------------------------------
# The Keychain reader
# ---------------------------------------------------------------------------

def _security(monkeypatch, code, out="", err=""):
    monkeypatch.setattr(off.subprocess, "run", lambda argv, **kw:
                        subprocess.CompletedProcess(argv, code, out, err))


def test_keychain_item_not_found_is_none(monkeypatch):
    _security(monkeypatch, 44, err="could not be found")
    assert off.keychain_read(off.REPO_SERVICE) is None


def test_keychain_value_is_returned(monkeypatch):
    _security(monkeypatch, 0, out=REPO + "\n")
    assert off.keychain_read(off.REPO_SERVICE) == REPO


def test_keychain_access_denied_is_an_error_not_unconfigured(monkeypatch):
    """A denied read is not "nobody configured this". Treating it as such
    would turn a working off-site copy into a silent, inert one."""
    _security(monkeypatch, 51, err="User interaction is not allowed.")
    with pytest.raises(off.OffsiteError) as exc:
        off.keychain_read(off.REPO_SERVICE)
    assert "51" in str(exc.value)


# ---------------------------------------------------------------------------
# backup.py: the off-site step runs after, and only after, a good local backup
# ---------------------------------------------------------------------------

@pytest.fixture
def nightly(tmp_path, monkeypatch, lmsdb):
    archive = tmp_path / "archive"
    archive.mkdir()
    monkeypatch.setenv("LMS_ARCHIVE_ROOT", str(archive))
    monkeypatch.setenv("LMS_DB", str(lmsdb))
    monkeypatch.setenv("LMS_BACKUP_REPO", str(tmp_path / "repo"))
    monkeypatch.setattr(backup, "ensure_venv", lambda *a, **k: None)
    monkeypatch.setattr(backup, "load_lms_env", lambda *a, **k: None)
    monkeypatch.setattr(backup, "restic_password", lambda: LOCAL_PW)
    monkeypatch.setattr(backup, "ensure_repo", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", ["backup.py"])

    state = {"restic": (0, "snapshot 1a2b3c4d saved\n", ""), "offsite": []}

    def fake_local(args, repo, password, dry_run=False):
        return subprocess.CompletedProcess(args, *state["restic"])
    monkeypatch.setattr(backup, "run_restic", fake_local)

    def fake_offsite(db_path, repo, password, dry_run=False):
        state["offsite"].append((Path(db_path), Path(repo), password))
        return state.get("offsite_rc", 0)
    monkeypatch.setattr(off, "run_after_local_backup", fake_offsite)
    return state


def test_a_good_night_is_zero_and_copies_off_site(nightly, lmsdb, tmp_path):
    assert backup.main() == 0
    assert len(nightly["offsite"]) == 1
    db_path, repo, password = nightly["offsite"][0]
    assert db_path == lmsdb.resolve()
    assert repo == (tmp_path / "repo").resolve()
    assert password == LOCAL_PW


def test_an_off_site_failure_is_3_and_the_local_backup_still_counts(
        nightly, lmsdb):
    """A B2 outage must never make a good local snapshot look like a failed
    one: BACKUP_COMPLETED is written first, and the exit code says which half
    failed."""
    nightly["offsite_rc"] = 1
    assert backup.main() == 3
    assert "BACKUP_COMPLETED" in [a for a, _ in actions(lmsdb)]


def test_a_failed_local_backup_is_never_copied(nightly, lmsdb):
    nightly["restic"] = (1, "", "Fatal: disk full")
    assert backup.main() == 1
    assert nightly["offsite"] == []
    assert "BACKUP_COMPLETED" not in [a for a, _ in actions(lmsdb)]


def test_backup_and_offsite_agree_on_the_database(monkeypatch, tmp_path):
    """One derivation (D-035): offsite_backup.py asks backup.py."""
    monkeypatch.setenv("LMS_ARCHIVE_ROOT", str(tmp_path / "LMS" / "archive"))
    monkeypatch.delenv("LMS_DB", raising=False)
    assert backup.live_db_path() == (tmp_path / "LMS" / "lms.db").resolve()


# ---------------------------------------------------------------------------
# The brief: watches a configured copy, never nags an unconfigured one
# ---------------------------------------------------------------------------

NY = timezone(timedelta(hours=-4))
NOW = datetime(2026, 11, 3, 6, 30, tzinfo=NY)


def logged_at(conn, action, when):
    conn.execute("INSERT INTO actions_log (ts, action, detail) VALUES (?, ?, ?)",
                 (when.isoformat(timespec="seconds"), action, "x"))
    conn.commit()


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "brief.db")
    yield c
    c.close()


def test_the_offsite_copy_is_watched():
    assert "OFFSITE_COPY_COMPLETED" in reports.WATCHED_JOBS
    name, hours = reports.WATCHED_JOBS["OFFSITE_COPY_COMPLETED"]
    assert hours == 48, "nightly, like the local backup: two missed runs"


def test_an_unconfigured_offsite_never_reaches_the_brief(conn):
    """Months of OFFSITE_NOT_CONFIGURED must not arm the watch."""
    logged_at(conn, "OFFSITE_NOT_CONFIGURED", NOW - timedelta(days=60))
    assert reports.stalled_jobs(conn, NOW) == []


def test_a_configured_offsite_that_stopped_needs_attention(conn):
    logged_at(conn, "OFFSITE_COPY_COMPLETED", NOW - timedelta(days=4))
    logged_at(conn, "OFFSITE_COPY_FAILED", NOW - timedelta(days=1))
    text = reports.render_text(reports.build_brief(conn, kind="morning", now=NOW))
    assert "THE SYSTEM NEEDS ATTENTION" in text
    assert "off-site backup: last ran 4d ago" in text


def test_a_healthy_offsite_copy_is_silent(conn):
    logged_at(conn, "OFFSITE_COPY_COMPLETED", NOW - timedelta(hours=4))
    assert reports.stalled_jobs(conn, NOW) == []


# ---------------------------------------------------------------------------
# verify_setup [8]
# ---------------------------------------------------------------------------

@pytest.fixture
def verify(monkeypatch):
    import verify_setup
    monkeypatch.setattr(verify_setup, "failures", [])
    monkeypatch.setattr(verify_setup, "warnings", [])
    return verify_setup


def test_verify_setup_warns_off_site_not_configured(verify, keychain):
    verify.check_offsite()
    assert verify.failures == [], "not configured is a WARN, never a FAIL"
    assert any("off-site not configured (R29)" in w for w in verify.warnings)


def test_verify_setup_passes_a_configured_offsite(verify, keychain, capsys):
    configure(keychain)
    verify.check_offsite()
    assert verify.failures == [] and verify.warnings == []
    out = capsys.readouterr().out
    assert REPO in out
    assert OFF_PW not in out


def test_verify_setup_fails_a_half_configured_offsite(verify, keychain):
    configure(keychain)
    del keychain[off.KEY_ID_SERVICE]
    verify.check_offsite()
    assert any("off-site" in f for f in verify.failures)


# ---------------------------------------------------------------------------
# restore_test.py --offsite
# ---------------------------------------------------------------------------

def test_offsite_restore_uses_fds_and_the_b2_environment(keychain, restic,
                                                         tmp_path):
    configure(keychain)
    restore = restore_test._offsite_restorer()
    code, _ = restore(tmp_path / "dest")
    c = restic.call("restore")
    assert code == 0
    assert c["argv"][c["argv"].index("-r") + 1] == REPO
    assert c["passwords"] == {"--password-file": OFF_PW}
    assert c["env"]["B2_ACCOUNT_KEY"] == APP_KEY
    assert "latest" in c["argv"]


def test_offsite_restore_when_unconfigured_says_so(keychain, monkeypatch,
                                                   tmp_path, capsys):
    monkeypatch.setattr(restore_test, "ensure_venv", lambda *a, **k: None)
    monkeypatch.setattr(restore_test, "load_lms_env", lambda *a, **k: None)
    monkeypatch.setattr(restore_test.shutil, "which", lambda n: "/x/restic")
    monkeypatch.setenv("LMS_ARCHIVE_ROOT", str(tmp_path / "archive"))
    assert restore_test.main(["--offsite"]) == 2
    assert "not configured (R29)" in capsys.readouterr().err


def test_offsite_restore_failure_output_is_scrubbed(keychain, restic, tmp_path):
    configure(keychain)
    restic.results["restore"] = (1, "", f"denied for key {APP_KEY}")
    code, err = restore_test._offsite_restorer()(tmp_path / "dest")
    assert code == 1 and APP_KEY not in err


# ---------------------------------------------------------------------------
# set_offsite_backup.py: the key must be bucket-only
# ---------------------------------------------------------------------------

GOOD_CAPS = ["listBuckets", "listFiles", "readFiles", "writeFiles",
             "deleteFiles", "shareFiles"]


def auth(bucket="mre-lms-backup", caps=GOOD_CAPS, prefix=None):
    return {"accountId": "a1b2c3d4e5f6", "apiUrl": "https://api005.backblazeb2.com",
            "authorizationToken": "TOKEN",
            "allowed": {"bucketName": bucket, "bucketId": "bid" if bucket else None,
                        "capabilities": list(caps), "namePrefix": prefix}}


PRIVATE = [{"bucketName": "mre-lms-backup", "bucketType": "allPrivate"}]


def test_a_bucket_only_private_key_is_accepted():
    assert setoff.key_problems(auth(), PRIVATE, "mre-lms-backup", "lms") == []


def test_an_unrestricted_key_is_refused():
    problems = setoff.key_problems(auth(bucket=None), PRIVATE,
                                   "mre-lms-backup", "lms")
    assert any("NOT restricted" in p for p in problems)


def test_a_key_for_another_bucket_is_refused():
    problems = setoff.key_problems(auth(bucket="someone-elses"), PRIVATE,
                                   "mre-lms-backup", "lms")
    assert any("restricted to 'someone-elses'" in p for p in problems)


def test_a_key_that_can_mint_keys_is_refused():
    problems = setoff.key_problems(auth(caps=GOOD_CAPS + ["writeKeys"]),
                                   PRIVATE, "mre-lms-backup", "lms")
    assert any("writeKeys" in p for p in problems)


def test_a_read_only_key_is_refused():
    """restic prune deletes; a key without deleteFiles fails at the first
    retention run, a month later, in a log nobody reads."""
    problems = setoff.key_problems(auth(caps=["listBuckets", "listFiles",
                                              "readFiles"]),
                                   PRIVATE, "mre-lms-backup", "lms")
    assert any("deleteFiles" in p and "writeFiles" in p for p in problems)


def test_a_public_bucket_is_refused():
    problems = setoff.key_problems(
        auth(), [{"bucketName": "mre-lms-backup", "bucketType": "allPublic"}],
        "mre-lms-backup", "lms")
    assert any("not private" in p for p in problems)


def test_a_name_prefix_that_excludes_the_repository_is_refused():
    problems = setoff.key_problems(auth(prefix="other/"), PRIVATE,
                                   "mre-lms-backup", "lms")
    assert any("names starting" in p for p in problems)
    assert setoff.key_problems(auth(prefix="lms/"), PRIVATE,
                               "mre-lms-backup", "lms") == []


def test_the_master_key_is_refused_without_asking_b2():
    """Its keyID is the 12-character account id."""
    with pytest.raises(setoff.SetupError) as exc:
        setoff.check_key_shape("a1b2c3d4e5f6", APP_KEY)
    assert "MASTER" in str(exc.value)


def test_a_clipboard_holding_something_else_is_refused():
    with pytest.raises(setoff.SetupError):
        setoff.check_key_shape(KEY_ID, "some sentence that was on the clipboard")
    with pytest.raises(setoff.SetupError):
        setoff.check_key_shape(KEY_ID, KEY_ID)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def test_the_b2_probe_sends_the_key_in_a_header_not_a_url():
    seen = []

    def urlopen(req, timeout=None):
        seen.append(req)
        if req.full_url.endswith("b2_authorize_account"):
            return FakeResponse(json.dumps(auth()).encode())
        return FakeResponse(json.dumps({"buckets": PRIVATE}).encode())

    a, listed = setoff.b2_probe(KEY_ID, APP_KEY, "mre-lms-backup", urlopen=urlopen)
    assert listed == PRIVATE
    assert len(seen) == 2
    for req in seen:
        assert KEY_ID not in req.full_url and APP_KEY not in req.full_url
    assert seen[0].get_header("Authorization").startswith("Basic ")


def test_a_rejected_key_says_so(monkeypatch):
    import urllib.error

    def urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "unauthorized", {}, None)

    with pytest.raises(setoff.SetupError) as exc:
        setoff.b2_probe(KEY_ID, APP_KEY, "mre-lms-backup", urlopen=urlopen)
    assert "401" in str(exc.value)


# ---------------------------------------------------------------------------
# set_offsite_backup.py: storage and ordering
# ---------------------------------------------------------------------------

def test_keychain_store_pipes_the_secret(monkeypatch, keychain):
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"], seen["input"] = argv, kw.get("input", "")
        keychain[off.APP_KEY_SERVICE] = APP_KEY
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(setoff.subprocess, "run", fake_run)
    setoff.keychain_store(off.APP_KEY_SERVICE, APP_KEY, "mleca")
    assert seen["argv"] == ["security", "-i"]
    assert APP_KEY in seen["input"]
    assert APP_KEY not in " ".join(seen["argv"])


def test_an_existing_offsite_password_is_never_replaced(monkeypatch, keychain):
    """Replacing it would make an existing B2 repository unreadable; restic
    has no recovery path."""
    keychain[off.PASSWORD_SERVICE] = OFF_PW
    monkeypatch.setattr(setoff, "keychain_store",
                        lambda *a: pytest.fail("tried to overwrite it"))
    assert setoff.ensure_password("mleca") == (OFF_PW, False)


@pytest.fixture
def setup_env(monkeypatch, keychain, restic, tmp_path):
    """Everything set_offsite_backup.setup touches, faked."""
    local = tmp_path / "local-repo"
    local.mkdir()
    (local / "config").write_text("x")
    monkeypatch.setattr(backup, "repo_path", lambda override=None: local)
    monkeypatch.setattr(backup, "restic_password", lambda: LOCAL_PW)

    clip = iter([KEY_ID, APP_KEY])
    monkeypatch.setattr(setoff, "clipboard", lambda prompt: next(clip))
    monkeypatch.setattr(setoff, "clear_clipboard", lambda: None)

    order = []

    def store(service, value, account):
        order.append(service)
        keychain[service] = value
    monkeypatch.setattr(setoff, "keychain_store", store)

    def urlopen(req, timeout=None):
        if req.full_url.endswith("b2_authorize_account"):
            return FakeResponse(json.dumps(auth()).encode())
        return FakeResponse(json.dumps({"buckets": PRIVATE}).encode())

    return {"order": order, "urlopen": urlopen, "local": local}


def test_setup_stores_the_location_last(setup_env, restic, keychain, capsys):
    """The location is what turns the nightly copy on, so it is written only
    once everything it depends on exists."""
    rc = setoff.setup("mre-lms-backup", "lms", "mleca", False,
                      urlopen=setup_env["urlopen"])
    assert rc == 0
    assert setup_env["order"][-1] == off.REPO_SERVICE
    assert keychain[off.REPO_SERVICE] == REPO
    assert restic.subs == ["init"]
    argv = restic.call("init")["argv"]
    assert "--copy-chunker-params" in argv
    assert argv[argv.index("--from-repo") + 1] == str(setup_env["local"])
    assert restic.call("init")["passwords"]["--from-password-file"] == LOCAL_PW
    # the generated passphrase is shown once, because it must reach the
    # password manager
    assert keychain[off.PASSWORD_SERVICE] in capsys.readouterr().out


def test_a_failed_init_leaves_the_copy_off(setup_env, restic, keychain):
    restic.results["init"] = (1, "", "Fatal: create repository failed: b2 error")
    rc = setoff.setup("mre-lms-backup", "lms", "mleca", False,
                      urlopen=setup_env["urlopen"])
    assert rc == 1
    assert off.REPO_SERVICE not in keychain
    assert off.load_config() is None, "the nightly job must still be inert"


def test_a_refused_key_stores_nothing(setup_env, restic, keychain):
    def unrestricted(req, timeout=None):
        if req.full_url.endswith("b2_authorize_account"):
            return FakeResponse(json.dumps(auth(bucket=None)).encode())
        return FakeResponse(json.dumps({"buckets": PRIVATE}).encode())

    rc = setoff.setup("mre-lms-backup", "lms", "mleca", False,
                      urlopen=unrestricted)
    assert rc == 1
    assert setup_env["order"] == []
    assert restic.calls == []


def test_an_existing_repository_is_accepted_only_if_our_password_opens_it(
        setup_env, restic, keychain):
    restic.results["init"] = (1, "", "Fatal: config file already exists")
    restic.results["snapshots"] = (0, "[{}, {}]", "")
    assert setoff.setup("mre-lms-backup", "lms", "mleca", False,
                        urlopen=setup_env["urlopen"]) == 0
    assert keychain[off.REPO_SERVICE] == REPO


def test_an_existing_repository_with_another_password_is_not_enabled(
        setup_env, restic, keychain):
    restic.results["init"] = (1, "", "Fatal: config file already exists")
    restic.results["snapshots"] = (1, "", "Fatal: wrong password or no key found")
    assert setoff.setup("mre-lms-backup", "lms", "mleca", False,
                        urlopen=setup_env["urlopen"]) == 1
    assert off.REPO_SERVICE not in keychain
