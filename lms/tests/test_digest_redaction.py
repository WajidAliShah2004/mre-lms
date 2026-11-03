"""What the digest may not say (R15, addendum §5).

"Redact taxpayer IDs, health details and credentials from routine digests."

The positives prove each pattern catches what it is for. The negatives matter
as much: a redactor that masks every amount and date makes the brief useless,
and a useless brief is not read — which is the failure D-034 exists to
prevent. Over-redaction of a number that merely LOOKS like an ID is accepted
and pinned below, so a later "fix" that narrows a rule has to argue with it.
"""

from datetime import datetime
from pathlib import Path

import pytest

from core.db import database as db
from core.pipeline.registry import load_registry
from core.reports import brief as reports
from core.reports.redact import redact

CONFIG = Path(__file__).resolve().parents[1] / "config"
NOW = datetime(2026, 9, 8, 6, 30)            # a Tuesday: no backlog section


@pytest.fixture
def registry():
    return load_registry(CONFIG)


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "lms.db")
    yield c
    c.close()


def task(conn, title, *, due="2026-09-20", sensitivity="normal",
         status="TRIAGED", source_artifact=None) -> int:
    return db.insert_task(conn, title=title, due_date=due, urgency="NORMAL",
                          entity_id="B_MRE", status=status,
                          sensitivity=sensitivity,
                          source_artifact=source_artifact)


_sha = 0


def document(conn, *, category, amount_cents=None) -> int:
    global _sha
    _sha += 1
    aid = int(conn.execute(
        "INSERT INTO artifacts (sha256, source, status, original_name, ingested_at)"
        " VALUES (?,?,?,?,?)",
        (f"{_sha:064x}", "mail", "FILED", "doc.pdf", db.now_iso())).lastrowid)
    db.insert_classification(conn, artifact_id=aid, domain="PERSONAL",
                             entity_id="P_MRE", category=category,
                             urgency="NORMAL", confidence=0.95,
                             amount_cents=amount_cents, model="fake",
                             prompt_hash="0" * 16, decided_by="rule")
    return aid


def brief_text(conn, registry, now=NOW) -> str:
    return reports.render_text(reports.build_brief(conn, now=now), registry)


# ---------------------------------------------------------------------------
# Taxpayer IDs: the last four stay, so he can still tell his entities apart
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    # EIN, NN-NNNNNNN — the format his entities' numbers take (D-057)
    ("EIN 93-4868827 confirmation", "EIN ••-•••8827 confirmation"),
    ("CP575 for 93-4868827", "CP575 for EIN ••-•••8827"),
    ("Employer ID: 93-4868827", "Employer ID: ••-•••8827"),
    # SSN, NNN-NN-NNNN, dashed or spaced
    ("SSN 123-45-6789 on file", "SSN •••-••-6789 on file"),
    ("W-2 for 123-45-6789", "W-2 for SSN •••-••-6789"),
    ("form 123 45 6789", "form SSN ••• •• 6789"),
    # ITIN: an SSN never starts with 9, an ITIN always does
    ("ITIN 912-70-1234 renewal", "ITIN •••-••-1234 renewal"),
    ("renewal 912-70-1234", "renewal ITIN •••-••-1234"),
    # undashed: any of the three
    ("EIN 934868827", "EIN •••••8827"),
    ("ref 123456789 letter", "ref TIN •••••6789 letter"),
    # inside a filename slug, where \b would have missed it
    ("w2_ssn-123-45-6789.pdf", "w2_ssn-•••-••-6789.pdf"),
    ("scan_934868827.pdf", "scan_TIN •••••8827.pdf"),
])
def test_taxpayer_ids_are_masked_to_the_last_four(text, expected):
    assert redact(text) == expected


@pytest.mark.parametrize("text", [
    "$1,234.56 due",
    "$93,486.88",
    "$123456789",                          # an amount, however unlikely
    "due 2026-09-08",
    "due 09-08-2026",
    "due 09/08/2026",
    "2026-09-08T06:30:00-04:00",
    "call 212-555-0187",
    "(212) 555-0187",
    "2125550187",                          # ten digits: a phone number
    "ZIP 10001-1234",
    "INV-2026-0042",
    "2025 form 5498",
    "1099-NEC",
    "12345678",                            # eight digits
    "1234567890",                          # ten
    "0.123456789",
    "3d OVERDUE · HIGH · $9,255.00",
])
def test_amounts_dates_and_phone_numbers_are_not_taxpayer_ids(text):
    assert redact(text) == text


# ---------------------------------------------------------------------------
# Card and account numbers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("card 4111 1111 1111 1111", "card ••••1111"),
    ("card 4111-1111-1111-1111", "card ••••1111"),
    ("amex 3782 822463 10005", "amex ••••0005"),
    ("account 123456789012", "account ••••9012"),
])
def test_card_and_account_numbers_keep_only_the_last_four(text, expected):
    assert redact(text) == expected


# ---------------------------------------------------------------------------
# Credentials: removed whole
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    # the real title, from filed mail
    ("Security code 187634 for The Magic Brand",
     "Security code •••••• for The Magic Brand"),
    ("Your verification code is 482913", "Your verification code is ••••••"),
    ("482913 is your verification code", "•••••• is your verification code"),
    ("Your code is G-482913", "Your code is G-••••••"),
    ("Login code: 4821", "Login code: ••••••"),
    ("one-time passcode 99812733", "one-time passcode ••••••"),
    ("Amazon OTP 552901", "Amazon OTP ••••••"),
    ("PIN 4821", "PIN ••••••"),
    ("2FA code A7K2Q9", "2FA code ••••••"),
    ("login-code-187634", "login-code-••••••"),
])
def test_one_time_codes_are_masked(text, expected):
    assert redact(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("password: hunter2!", "password: [removed]"),
    ("Password=Tr0ub4dor&3", "Password=[removed]"),
    ("Your password is sunshine", "Your password is [removed]"),
    # to the end of the line: a passphrase has spaces, and no visible end
    ("passphrase: correct horse battery", "passphrase: [removed]"),
    ("Wi-Fi password: guest 2026 (office)", "Wi-Fi password: [removed]"),
    ("api_key=abc123", "api_key=[removed]"),
    ("API key: abc123", "API key: [removed]"),
    ("token: abc", "token: [removed]"),
    ("client_secret: s3cr3t", "client_secret: [removed]"),
])
def test_labelled_secrets_lose_their_value(text, expected):
    assert redact(text) == expected


@pytest.mark.parametrize("secret", [
    "sk-proj-abcdefghijklmnop1234",
    "sk-ant-api03-abcdefghijklmnop",
    "sk_live_51HxAbCdEfGhIjKl",
    "ghp_" + "a1B2" * 9,
    "xoxb-1234567890-abcdefghij",
    "AKIAIOSFODNN7EXAMPLE",
    "AIza" + "SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q",
    "ya29.a0AfH6SMBabcdefghijklmnopqrstuv",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.abcdefghijk",
    "1234567890:AAHabcdefghijklmnopqrstuvwxyz012345",
    "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8",
])
def test_token_shaped_strings_are_removed(secret):
    out = redact(f"the key {secret} was rotated")
    assert secret not in out
    assert "[removed]" in out


@pytest.mark.parametrize("text", [
    # real titles: ABOUT a credential, not one. They say what the mail is.
    "portal-login-code",
    "login-credentials-email",
    "password-reset-email",
    "Password reset requested",
    "Your token expires soon",
    "IRS wage & income transcript",
    # "code" alone is not an access code
    "tax code 1099",
    "zip code 10001",
    "Promo code for 2026",
    # long words and slugs are not keys
    "irs-wage-and-income-transcript-2025-form-5498-final",
    "IMG_20260908_063000_final_copy_of_scan_v2.pdf",
])
def test_things_that_mention_credentials_are_left_readable(text):
    assert redact(text) == text


def test_redaction_is_idempotent():
    """render_text redacts every line, and the backlog redacts titles before
    truncating them; running twice must not mangle the first run's output."""
    once = redact("EIN 93-4868827 · SSN 123-45-6789 · Security code 187634 · "
                  "password: x · card 4111 1111 1111 1111")
    assert redact(once) == once


def test_a_code_line_does_not_mask_the_next_line():
    """Decided per line: one task's 'verification code' must not blank the
    next task's invoice number."""
    out = redact("Security code 187634\nInvoice 4821 due")
    assert out == "Security code ••••••\nInvoice 4821 due"


def test_the_redactor_has_no_health_keywords():
    """Health is by classification. A keyword list would catch some and look
    like it catches all — say so by its absence."""
    assert redact("Dr. Patel follow-up, Lisinopril refill") == \
        "Dr. Patel follow-up, Lisinopril refill"


# ---------------------------------------------------------------------------
# The brief applies it, and the database keeps the truth
# ---------------------------------------------------------------------------

def test_the_brief_never_prints_an_id_or_a_code(conn, registry):
    task(conn, "EIN 93-4868827 confirmation letter")
    task(conn, "Security code 187634 for The Magic Brand")
    text = brief_text(conn, registry)

    assert "4868827" not in text and "187634" not in text
    assert "••-•••8827" in text, "the last four are how he recognises it"
    # ... and the record is unchanged: redaction is render-time only.
    titles = {r["title"] for r in conn.execute("SELECT title FROM tasks")}
    assert "EIN 93-4868827 confirmation letter" in titles


def test_every_section_is_redacted_not_just_tasks(conn, registry):
    """A filename in the review queue leaves the Mac as surely as a title."""
    conn.execute(
        "INSERT INTO artifacts (sha256, source, status, original_name, ingested_at)"
        " VALUES (?,?,?,?,?)",
        ("f" * 64, "photo", "QUARANTINED", "ssn card 123-45-6789.jpg",
         db.now_iso()))
    text = brief_text(conn, registry)
    assert "WAITING ON YOU" in text
    assert "123-45-6789" not in text


# ---------------------------------------------------------------------------
# Health details: by classification
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sensitivity", ["high", "private"])
def test_a_sensitive_task_is_shown_without_its_title(conn, registry, sensitivity):
    tid = task(conn, "Cardiology follow-up, Dr. Patel", sensitivity=sensitivity)
    text = brief_text(conn, registry)

    assert "Cardiology" not in text and "Patel" not in text
    assert f"Private item #{tid}" in text
    assert f"./ops/done.py {tid} --show" in text, "it must say where the rest is"


def test_a_normal_task_keeps_its_title(conn, registry):
    task(conn, "Renew the licence", sensitivity="normal")
    assert "Renew the licence" in brief_text(conn, registry)


def test_a_health_document_makes_its_task_private(conn, registry):
    """Nothing sets `sensitivity` at ingest yet, so the category of the
    document behind the task decides — read at render time."""
    tid = task(conn, "Refill — CVS Pharmacy",
               source_artifact=document(conn, category="HEALTH"))
    text = brief_text(conn, registry)
    assert "CVS" not in text
    assert f"Private item #{tid}" in text


def test_health_evidence_added_later_makes_the_task_private(conn, registry):
    """One obligation, many sources (D-066): the second could be the medical one."""
    tid = task(conn, "Statement — Northwell",
               source_artifact=document(conn, category="FINANCE"))
    conn.execute(
        "INSERT INTO task_sources (task_id, source_key, source, artifact_id, seen_at)"
        " VALUES (?,?,?,?,?)",
        (tid, "artifact:x", "mail", document(conn, category="HEALTH"),
         db.now_iso()))
    assert "Northwell" not in brief_text(conn, registry)


def test_a_private_item_keeps_its_due_date_but_not_its_amount(conn, registry):
    tid = task(conn, "Hospital bill", due="2026-09-07",
               source_artifact=document(conn, category="HEALTH",
                                        amount_cents=1_234_500))
    [t] = reports.todo_list(conn, today=NOW.date())
    assert t.private and t.task_id == tid
    assert "OVERDUE" in t.shown_reason, "when it is due is why it ranks here"
    assert "$" not in t.shown_reason
    assert "12,345" not in brief_text(conn, registry)


def test_a_private_line_fits_a_phone(conn, registry):
    for _ in range(3):
        task(conn, "x", sensitivity="private")
    db.insert_task(conn, title="y", entity_id="B_MRE", status="TRIAGED",
                   sensitivity="private", due_date="2026-09-20")
    text = brief_text(conn, registry)
    assert not [l for l in text.splitlines() if len(l) > 78]
