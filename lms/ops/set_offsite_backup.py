#!/usr/bin/env python3
"""Configure the off-site backup: B2 key, repository password, repository.

    ./ops/set_offsite_backup.py --bucket mre-lms-backup     # set up (R29)
    ./ops/set_offsite_backup.py --check                     # is it still right?
    ./ops/set_offsite_backup.py --show                      # print the off-site passphrase

Run once, at the Mac, after Matthew has created a PRIVATE bucket and an
application key restricted to it (ops/OFFSITE_APPLY.md). Until this finishes,
`offsite_backup.py` is inert.

WHAT IT DOES, IN THIS ORDER
---------------------------
  1. reads the B2 keyID and applicationKey from the CLIPBOARD, one at a time
  2. asks B2 what that key may do, and REFUSES it unless it is restricted to
     this one bucket, can read/write/delete files there, cannot manage keys,
     and the bucket is private
  3. stores both in the Keychain, reads them back, compares
  4. generates the off-site repository passphrase — only if there is not one
     already; an existing one is NEVER replaced (see below)
  5. `restic init --copy-chunker-params` on b2:<bucket>:<path>
  6. stores the repository location LAST

Step 6 is what turns the nightly copy on (`offsite_backup.load_config`). Any
failure before it leaves the machine exactly as inert as before, with whatever
was stored harmlessly reusable by a re-run.

WHY THE CLIPBOARD AND NOT A PROMPT
----------------------------------
D-035: on this Mac a no-echo prompt received bracketed-paste escape codes
instead of the pasted text, and `security` stored an empty password without
complaint, twice. `pbpaste` reads the clipboard directly; nothing passes
through the terminal, so nothing can be mangled or echoed, and the value never
reaches shell history or argv. The clipboard is cleared afterwards.

WHY THE KEY IS CHECKED, AND HOW
-------------------------------
"Bucket-restricted" is the whole safety argument for handing restic a key at
all (the key has to sit in restic's environment; offsite_backup.py says why).
An unrestricted key — or the master key — reaches every bucket and can create
more keys. That is cheap to check: one `b2_authorize_account` call returns
`allowed.bucketName` (null when unrestricted) and the key's capabilities, and
one `b2_list_buckets` returns whether the bucket is private. Both are made
here with the standard library; the key goes in an Authorization header, never
a URL. The master key is also recognisable offline — its keyID is the
12-character account id — and is refused before any network call.

WHY AN EXISTING OFF-SITE PASSPHRASE IS NEVER REPLACED
-----------------------------------------------------
If a repository already exists on B2, replacing the Keychain's passphrase makes
it unreadable — restic has no recovery path. A re-run after a failed `init`
must reuse the one it generated. Rotating it is rotate_backup_password.py's
procedure (add key, prove, remove), not this script's.

The passphrase is printed ONCE, when generated. It must reach the password
manager now: this is the copy that exists for the day the Mac — and its
Keychain — are gone.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import secrets
import string
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import offsite_backup as off                               # noqa: E402
from _env import load_lms_env                              # noqa: E402
from _reexec import ensure_venv                            # noqa: E402

LENGTH = 40
ALPHABET = string.ascii_letters + string.digits            # see set_backup_password.py

B2_AUTHORIZE = "https://api.backblazeb2.com/b2api/v2/b2_authorize_account"

# What restic does in a B2 bucket: list, read, write, and (for prune) delete
# files. listBuckets is how restic finds the bucket by name; it is reported if
# missing rather than refused, because `restic init` below will say so plainly.
REQUIRED_CAPABILITIES = {"listFiles", "readFiles", "writeFiles", "deleteFiles"}
# A key that can mint keys can mint an unrestricted one.
FORBIDDEN_CAPABILITIES = {"writeKeys", "deleteKeys", "listKeys"}

# B2 bucket names: 6-50 chars, letters/digits/hyphens, not starting "b2-".
_BUCKET_RE = re.compile(r"^(?!b2-)[A-Za-z0-9-]{6,50}$")
_PATH_RE = re.compile(r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$")
# Stored through `security -i`, which splits on whitespace and quotes. These
# values never contain either in practice; anything that does is refused
# rather than quoted and hoped about.
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9/+=:._-]+$")


class SetupError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# The key, checked before anything is stored
# ---------------------------------------------------------------------------

def check_key_shape(key_id: str, app_key: str) -> None:
    """Offline checks. The master key is recognisable without asking B2."""
    if not key_id or not app_key:
        raise SetupError("the keyID or the applicationKey is empty — was it "
                         "copied? Nothing was stored.")
    for label, v in (("keyID", key_id), ("applicationKey", app_key)):
        if not _SAFE_VALUE.match(v):
            raise SetupError(f"the {label} contains spaces or characters a B2 "
                             f"key does not have — the clipboard probably held "
                             f"something else. Nothing was stored.")
    if re.fullmatch(r"[0-9a-fA-F]{12}", key_id):
        raise SetupError(
            "that keyID is 12 characters — the shape of the account's MASTER "
            "application key, which reaches every bucket and can create keys. "
            "Create an application key restricted to the backup bucket "
            "(ops/OFFSITE_APPLY.md). Nothing was stored.")
    if key_id == app_key:
        raise SetupError("the keyID and the applicationKey are identical — the "
                         "same thing was copied twice. Nothing was stored.")


def key_problems(auth: dict, buckets: list[dict] | None, bucket: str,
                 path: str) -> list[str]:
    """Why this key must not be used, from what B2 itself says about it.

    `auth` is the b2_authorize_account response (v2); `buckets` the
    b2_list_buckets result for this bucket, or None if the key cannot list.
    Empty list = acceptable.
    """
    problems = []
    allowed = auth.get("allowed") or {}
    caps = set(allowed.get("capabilities") or [])

    restricted_to = allowed.get("bucketName")
    if not restricted_to:
        problems.append("the key is NOT restricted to a bucket — it reaches "
                        "every bucket in the account")
    elif restricted_to != bucket:
        problems.append(f"the key is restricted to {restricted_to!r}, not "
                        f"{bucket!r}")

    bad = sorted(caps & FORBIDDEN_CAPABILITIES)
    if bad:
        problems.append(f"the key can manage keys ({', '.join(bad)}) — it could "
                        f"create an unrestricted one")

    missing = sorted(REQUIRED_CAPABILITIES - caps)
    if missing:
        problems.append(f"the key lacks {', '.join(missing)}; restic needs to "
                        f"list, read, write and (to prune) delete files. "
                        f"Create it as 'Read and Write'")

    prefix = allowed.get("namePrefix")
    if prefix and not f"{path}/".startswith(prefix):
        problems.append(f"the key is limited to names starting {prefix!r}, and "
                        f"the repository path is {path!r}")

    if buckets is not None:
        match = [b for b in buckets if b.get("bucketName") == bucket]
        if not match:
            problems.append(f"bucket {bucket!r} does not exist or this key "
                            f"cannot see it")
        elif match[0].get("bucketType") != "allPrivate":
            problems.append(f"bucket {bucket!r} is {match[0].get('bucketType')!r}, "
                            f"not private — anyone with a link could fetch "
                            f"its files")
    return problems


def b2_probe(key_id: str, app_key: str, bucket: str,
             urlopen=urllib.request.urlopen) -> tuple[dict, list[dict] | None]:
    """Ask B2 what this key is. Two HTTPS calls, standard library only.

    The key travels in an Authorization header; nothing secret is in a URL,
    and nothing returned here is printed except bucket names and capability
    names.
    """
    basic = base64.b64encode(f"{key_id}:{app_key}".encode()).decode()
    req = urllib.request.Request(B2_AUTHORIZE,
                                 headers={"Authorization": f"Basic {basic}"})
    try:
        with urlopen(req, timeout=30) as resp:
            auth = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise SetupError("B2 rejected the key (401): the keyID and "
                             "applicationKey do not belong together, or the "
                             "key was deleted. Nothing was stored.") from None
        raise SetupError(f"B2 answered {exc.code} to b2_authorize_account. "
                         f"Nothing was stored.") from None
    except urllib.error.URLError as exc:
        raise SetupError(f"could not reach Backblaze ({exc.reason}). "
                         f"Nothing was stored.") from None

    caps = set((auth.get("allowed") or {}).get("capabilities") or [])
    if "listBuckets" not in caps:
        return auth, None

    body = json.dumps({"accountId": auth["accountId"],
                       "bucketName": bucket}).encode()
    req = urllib.request.Request(
        f"{auth['apiUrl']}/b2api/v2/b2_list_buckets", data=body,
        headers={"Authorization": auth["authorizationToken"],
                 "Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=30) as resp:
            listed = json.loads(resp.read().decode()).get("buckets", [])
    except urllib.error.URLError as exc:
        raise SetupError(f"b2_list_buckets failed "
                         f"({getattr(exc, 'code', '')} {getattr(exc, 'reason', '')}). "
                         f"Nothing was stored.") from None
    return auth, listed


# ---------------------------------------------------------------------------
# The Keychain
# ---------------------------------------------------------------------------

def keychain_store(service: str, value: str, account: str) -> None:
    """Via `security -i`, so the value is piped and never in argv (D-035)."""
    if not _SAFE_VALUE.match(value):
        raise SetupError(f"refusing to store an unexpected value in {service}")
    r = subprocess.run(["security", "-i"],
                       input=f"add-generic-password -a {account} -s {service} "
                             f"-U -w {value}\n",
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SetupError(f"security failed storing {service} (exit "
                         f"{r.returncode}): {r.stderr.strip() or '(no message)'}")
    back = off.keychain_read(service)
    if back != value:
        raise SetupError(f"stored {service} but read back something different "
                         f"({len(back or '')} chars, expected {len(value)}). "
                         f"The Keychain does not hold what was intended.")


def clipboard(prompt: str) -> str:
    input(f"\n{prompt}\n  Copy it in the Backblaze page, then press Return here. ")
    r = subprocess.run(["pbpaste"], capture_output=True, text=True)
    return r.stdout.strip()


def clear_clipboard() -> None:
    subprocess.run(["pbcopy"], input="", text=True)


def generate() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))


def ensure_password(account: str) -> tuple[str, bool]:
    """(passphrase, newly generated). An existing one is always kept."""
    from backup import MIN_PASSWORD_CHARS
    existing = off.keychain_read(off.PASSWORD_SERVICE)
    if existing:
        if len(existing) < MIN_PASSWORD_CHARS:
            raise SetupError(
                f"{off.PASSWORD_SERVICE} exists and is {len(existing)} "
                f"characters. Not replacing it automatically — if an off-site "
                f"repository was initialised with it, replacing it would make "
                f"that repository unreadable. Delete the item by hand only if "
                f"you are sure no repository uses it.")
        return existing, False
    pw = generate()
    keychain_store(off.PASSWORD_SERVICE, pw, account)
    return pw, True


def show(password: str) -> None:
    bar = "=" * 68
    print(f"\n{bar}\n  OFF-SITE RESTIC PASSPHRASE — record this NOW\n{bar}\n")
    print(f"    {password}\n")
    print("  Into the password manager, beside the LOCAL passphrase — it is a")
    print("  different one. This copy exists for the day the Mac is gone, and")
    print("  its Keychain with it. restic has no recovery path: without this")
    print("  passphrase the B2 copy is permanently unreadable.")
    print(bar)


# ---------------------------------------------------------------------------

def setup(bucket: str, path: str, account: str, reuse_key: bool,
          urlopen=urllib.request.urlopen) -> int:
    import backup

    if not _BUCKET_RE.match(bucket):
        raise SetupError(f"{bucket!r} is not a valid B2 bucket name "
                         f"(6-50 letters, digits, hyphens)")
    if not _PATH_RE.match(path):
        raise SetupError(f"{path!r} is not a usable repository path")
    repo = f"b2:{bucket}:{path}"
    off.validate_repo(repo)

    try:
        local_repo = backup.repo_path()
        local_password = backup.restic_password()
    except backup.BackupError as exc:
        raise SetupError(f"the local backup is not ready: {exc}") from None
    if not (local_repo / "config").exists():
        raise SetupError(f"no local repository at {local_repo}. The off-site "
                         f"repository copies from it — run ./ops/backup.py "
                         f"first.")

    # --- 1. the key ---------------------------------------------------------
    if reuse_key:
        key_id = off.keychain_read(off.KEY_ID_SERVICE) or ""
        app_key = off.keychain_read(off.APP_KEY_SERVICE) or ""
    else:
        try:
            key_id = clipboard("1/2  the application key's keyID")
            app_key = clipboard("2/2  the applicationKey (shown only once by "
                                "Backblaze)")
        finally:
            clear_clipboard()
    check_key_shape(key_id, app_key)

    # --- 2. what B2 says the key is -----------------------------------------
    auth, listed = b2_probe(key_id, app_key, bucket, urlopen=urlopen)
    problems = key_problems(auth, listed, bucket, path)
    if problems:
        print("\nREFUSED — this key is not the bucket-only key R29 requires:",
              file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print("Nothing was stored. Create the key as ops/OFFSITE_APPLY.md "
              "describes and run this again.", file=sys.stderr)
        return 1
    caps = sorted((auth.get("allowed") or {}).get("capabilities") or [])
    print(f"\nkey OK: restricted to {bucket!r}; capabilities {', '.join(caps)}")
    if listed is None:
        print("  (the key cannot list buckets, so whether the bucket is "
              "private was not checked — confirm it in the Backblaze page)")
    else:
        print("  bucket is private")

    # --- 3. store the key ---------------------------------------------------
    if not reuse_key:
        keychain_store(off.KEY_ID_SERVICE, key_id, account)
        keychain_store(off.APP_KEY_SERVICE, app_key, account)
        print(f"stored {off.KEY_ID_SERVICE} and {off.APP_KEY_SERVICE}, read "
              f"back unchanged")

    # --- 4. the off-site repository's own passphrase ------------------------
    password, new = ensure_password(account)
    print(f"{off.PASSWORD_SERVICE}: "
          f"{'generated and stored' if new else 'already present, kept'}")

    # --- 5. the repository --------------------------------------------------
    cfg = off.OffsiteConfig(repo=repo, password=password,
                            key_id=key_id, app_key=app_key)
    secrets_ = cfg.secrets() + [local_password]
    print(f"==> restic init {repo} (chunker parameters from {local_repo})")
    r = off.init(cfg, local_repo, local_password)
    if r.returncode != 0:
        err = off.scrub(r.stderr.strip(), secrets_)
        if "already" not in err.lower():
            print(f"\nrestic init failed:\n  {err}\nThe repository location "
                  f"was NOT stored, so the nightly copy stays off.",
                  file=sys.stderr)
            if new:
                show(password)
            return 1
        # A repository is already there. Usable only if OUR passphrase opens
        # it; otherwise it belongs to some other passphrase and enabling the
        # copy would fail every night.
        try:
            n = off.snapshot_count(cfg)
        except off.OffsiteError as exc:
            print(f"\nA repository already exists at {repo} and the stored "
                  f"passphrase does not open it:\n  {exc}\nNot enabling. Use "
                  f"a different --path, or the passphrase that created it.",
                  file=sys.stderr)
            return 1
        print(f"    repository already exists and opens with the stored "
              f"passphrase ({n} snapshot(s))")
    else:
        print("    initialised")

    # --- 6. LAST: the location, which turns the nightly copy on -------------
    keychain_store(off.REPO_SERVICE, repo, account)
    print(f"stored {off.REPO_SERVICE} = {repo} — the nightly backup will now "
          f"copy off-site")

    if new:
        show(password)
    print("\nNext, at the Mac:")
    print("    ./ops/offsite_backup.py          # first copy now; arms the brief's watch")
    print("    ./ops/restore_test.py --offsite  # prove it restores")
    print("    ./ops/verify_setup.py            # [8] should PASS the off-site line")
    return 0


def check(urlopen=urllib.request.urlopen) -> int:
    """Re-verify a configured setup: the key is still bucket-only, the bucket
    still private, and the passphrase still opens the repository."""
    try:
        cfg = off.load_config()
    except off.OffsiteError as exc:
        print(f"BROKEN: {exc}", file=sys.stderr)
        return 1
    if cfg is None:
        print(f"off-site backup is NOT configured ({off.REPO_SERVICE} absent). "
              f"Inert; see ops/OFFSITE_APPLY.md.")
        return 0
    _, bucket, path = cfg.repo.split(":", 2)
    print(f"repository: {cfg.repo}")
    auth, listed = b2_probe(cfg.key_id, cfg.app_key, bucket, urlopen=urlopen)
    problems = key_problems(auth, listed, bucket, path)
    for p in problems:
        print(f"  PROBLEM: {p}", file=sys.stderr)
    if not problems:
        print("key:        bucket-only, capabilities sufficient"
              + ("" if listed is None else "; bucket private"))
    try:
        print(f"snapshots:  {off.snapshot_count(cfg)}")
    except off.OffsiteError as exc:
        print(f"  PROBLEM: the passphrase does not open the repository: {exc}",
              file=sys.stderr)
        return 1
    return 1 if problems else 0


def main() -> int:
    ensure_venv("LMS_SETOFFSITE_REEXEC", script=__file__)
    load_lms_env()

    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bucket", help="the private B2 bucket Matthew created")
    p.add_argument("--path", default="lms",
                   help="repository path inside the bucket (default: lms)")
    p.add_argument("--reuse-key", action="store_true",
                   help="use the keyID/applicationKey already in the Keychain "
                        "(a re-run after a failed init)")
    p.add_argument("--check", action="store_true")
    p.add_argument("--show", action="store_true",
                   help="print the existing off-site passphrase")
    p.add_argument("--account", default=None)
    args = p.parse_args()

    if sys.platform != "darwin":
        print("The Keychain is macOS-only.", file=sys.stderr)
        return 2

    try:
        if args.show:
            pw = off.keychain_read(off.PASSWORD_SERVICE)
            if not pw:
                print(f"no {off.PASSWORD_SERVICE} in the Keychain", file=sys.stderr)
                return 1
            show(pw)
            return 0
        if args.check:
            return check()
        if not args.bucket:
            p.error("--bucket is required (or --check / --show)")
        import getpass
        return setup(args.bucket, args.path, args.account or getpass.getuser(),
                     args.reuse_key)
    except (SetupError, off.OffsiteError) as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
