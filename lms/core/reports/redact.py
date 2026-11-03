"""Redaction for the digest (R15).

The addendum of Sept 28, §5:

    "Redact taxpayer IDs, health details and credentials from routine digests."

The brief leaves the Mac. It is written to iCloud Drive (D-046), synced to a
phone, previewed on a lock screen, and will go to Telegram (R10). Everything in
it is a copy of something the archive already holds, so nothing is lost by
masking it here, and a digest is the one artefact of this system most likely
to be read over a shoulder, forwarded, or left on a screen.

WHERE IT HAPPENS
----------------
At render time, on the text, after the brief has been built. The database keeps
the truth: a task titled "EIN 93-4868827 confirmation" stays exactly that in
`tasks.title`, `done.py --show` prints it at the Mac, and only what leaves the
machine is masked. Redacting at ingest would destroy the one copy that lets a
person check what the document actually said.

`render_text` runs every line through `redact`, so a section added later
(approvals, R19; anything after it) is covered without remembering to be.

WHAT IS FOUND BY PATTERN
------------------------
  * Taxpayer IDs. SSN and ITIN (NNN-NN-NNNN, or spaced; an ITIN is the one that
    starts with 9, which an SSN never does), EIN (NN-NNNNNNN), and any bare
    nine-digit run, which could be any of the three. The last four digits are
    kept — `EIN ••-•••8827` — because that is how a person tells two of his
    own entities apart, and four digits identify nothing on their own.
  * Card and account numbers: 12–19 digits, contiguous or in groups of four.
    Last four kept, as every statement does.
  * Credentials. A value after "password:", "token=", "api key:" and their
    kin; strings shaped like API keys and tokens (known prefixes, JWTs, long
    mixed letter-and-digit runs); and one-time codes. Credentials are removed
    whole — no last four: a partial secret is still a secret.

One-time codes have no shape of their own. "187634" is a code in "Security
code 187634 for The Magic Brand" and a reference number anywhere else. So a
line that is ABOUT a code (it says verification/security/login code, OTP,
passcode, PIN …) has every standalone 4–12 character token containing a digit
masked. A year in such a line goes with it. That is the intended trade.

WHAT IS NOT: HEALTH DETAILS
---------------------------
Health redaction is by CLASSIFICATION, not by pattern. No regex can tell
"Dr. Patel follow-up" or "Lisinopril refill" from an ordinary errand, and a
keyword list that catches some of them is worse than none because it looks
like it works. So the brief decides which tasks are private from what the
system already knows — `tasks.sensitivity` (spec §5.6: high or private) and
the category of the documents behind the task (HEALTH) — and prints a neutral
line for those instead of the title (`brief.private_task_ids`). This module
never sees a health task's title.

THE TRADE
---------
Over-redaction is acceptable; under-redaction is the defect. A nine-digit
invoice number shown as `TIN •••••4821` costs Matthew a second; an SSN on a
lock screen cannot be taken back. Every rule here errs that way, and the tests
pin the negatives that matter (amounts, dates, phone numbers, ZIP+4) so the
error stays small.

Deterministic, no model, no network: the same text always redacts the same
way, and `redact(redact(x)) == redact(x)`.
"""

from __future__ import annotations

import re

DOT = "•"

# ---------------------------------------------------------------------------
# Taxpayer IDs
# ---------------------------------------------------------------------------

# NNN-NN-NNNN, or the same with spaces; the two separators must agree, so
# "212-55 1234" is not read as one. `(?<!\d)` and `(?!\d)`, not `\b`: an ID in
# a filename slug ("w2_ssn-123-45-6789.pdf") is preceded by a hyphen or an
# underscore, and it must still be found.
_SSN = re.compile(r"(?<!\d)(\d{3})([- ])(\d{2})\2(\d{4})(?!\d)")

# NN-NNNNNNN. A date never has this shape (2-2-4, 4-2-2), nor a phone number
# (3-3-4), nor a ZIP+4 (5-4).
_EIN = re.compile(r"(?<!\d)(\d{2})-(\d{7})(?!\d)")

# Nine digits with nothing either side that makes it part of something larger.
# A dollar amount without commas ($123456789) or a decimal is excluded; a
# number that merely could be an ID is masked, which is the trade above.
_BARE9 = re.compile(r"(?<![\d$.,])(\d{9})(?![\d,]|\.\d)")

# The text already names the ID ("EIN 93-4868827", "SSN: 123-45-6789"), so the
# mask does not add a second label.
_LABEL_BEFORE = re.compile(
    r"(?i)(?<![a-z])(?:f?ein|ssn|itin|tin|tax\s*id|taxpayer\s*id(?:entification)?|"
    r"social\s+security|employer\s+id(?:entification)?)"
    r"(?:\s*(?:no\.?|number|#))?[\s:#=.-]*$")


def _labelled(m: re.Match, kind: str, masked: str) -> str:
    before = m.string[max(0, m.start() - 32):m.start()]
    return masked if _LABEL_BEFORE.search(before) else f"{kind} {masked}"


def _ssn(m: re.Match) -> str:
    sep, last4 = m.group(2), m.group(4)
    kind = "ITIN" if m.group(1).startswith("9") else "SSN"
    return _labelled(m, kind, f"{DOT * 3}{sep}{DOT * 2}{sep}{last4}")


def _ein(m: re.Match) -> str:
    return _labelled(m, "EIN", f"{DOT * 2}-{DOT * 3}{m.group(2)[-4:]}")


def _bare9(m: re.Match) -> str:
    return _labelled(m, "TIN", f"{DOT * 5}{m.group(1)[-4:]}")


# ---------------------------------------------------------------------------
# Card and account numbers
# ---------------------------------------------------------------------------

_CARD = re.compile(
    r"(?<!\d)(?:\d{12,19}"                          # contiguous
    r"|\d{4}(?:[ -]\d{4}){2,3}(?:[ -]\d{1,4})?"     # 4-4-4(-4)(-n)
    r"|\d{4}[ -]\d{6}[ -]\d{5})(?!\d)")             # 4-6-5


def _card(m: re.Match) -> str:
    digits = re.sub(r"\D", "", m.group(0))
    return f"{DOT * 4}{digits[-4:]}"


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

REMOVED = "[removed]"
CODE_MASK = DOT * 6          # fixed width: the mask must not leak the length

# Strings that are a credential by their shape alone. Prefixes are the
# published formats of common key issuers; the last alternative is any 32+
# character run of letters and digits that has both, which is what a generated
# secret looks like and an English word, a slug or a scanner's filename does
# not (those have hyphens or underscores, and words have no digits). A sha256
# in a filename goes too; that is the trade.
_TOKEN = re.compile(
    r"\b(?:"
    r"sk-(?:proj-|ant-)?[A-Za-z0-9_-]{16,}"         # model-provider secret keys
    r"|[sr]k_(?:live|test)_[A-Za-z0-9]{10,}"        # payment-processor secret keys
    r"|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}"
    r"|xox[abprs]-[A-Za-z0-9-]{10,}"                # chat-workspace tokens
    r"|AKIA[0-9A-Z]{16}"                            # cloud access key id
    r"|AIza[0-9A-Za-z_-]{35}"                       # browser API key
    r"|ya29\.[0-9A-Za-z_-]{20,}"                    # OAuth access token
    r"|1//[0-9A-Za-z_-]{20,}"                       # OAuth refresh token
    r"|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"   # JWT
    r"|\d{8,10}:[A-Za-z0-9_-]{35}"                  # bot token
    r"|(?=[A-Za-z]*\d)(?=\d*[A-Za-z])[A-Za-z0-9]{32,}"   # generated secret
    r")")

# "password: hunter2", "api_key=…", "Your password is sunshine". The separator
# is required: "password-reset-email" and "token expires" are about a
# credential, not one, and stay readable. Everything after the separator goes,
# to the end of the line: a passphrase has spaces in it, and there is no
# telling where one ends.
_SECRET = re.compile(
    r"(?i)\b(pass(?:word|wd|phrase|code)|pwd|secret|client[\s_-]?secret|"
    r"api[\s_-]?key|access[\s_-]?key|private[\s_-]?key|"
    r"(?:access[\s_-]?|bearer[\s_-]?)?token|pin)"
    r"(\s*[:=]\s*|\s+(?:is|was)\s+)(\S.*)")

# A line ABOUT a one-time code. Bare "code" is not enough — "zip code 10001",
# "tax code 1099" — it needs a word that makes it an access code.
_CODE_CONTEXT = re.compile(
    r"(?i)(?:\b(?:security|verification|verify|confirmation|login|log[\s-]?in|"
    r"sign[\s-]?in|access|authentication|auth|one[\s-]?time|2fa|mfa|two[\s-]?factor|"
    r"backup|recovery|reset|temporary|single[\s-]?use)[\s_-]*(?:code|pin|number|key)"
    r"|\b(?:otp|passcode|pin)\b|\byour\s+code\b|\bcode\s+is\b)")

# Inside such a line: any whole run of 4–12 letters and digits with a digit in
# it. A hyphen or underscore separates runs, so "login-code-187634" loses only
# the code and "G-482913" keeps its "G-". Not after "#" (a task id), "$" or a
# digit group separator (an amount), and not the brief's own age ("120d").
_CODE_TOKEN = re.compile(
    r"(?<![A-Za-z0-9#$.,/])(?!\d+d\b)(?=[A-Za-z]*\d)"
    r"[A-Za-z0-9]{4,12}(?![A-Za-z0-9,/]|\.\d)")


def _redact_line(line: str) -> str:
    line = _TOKEN.sub(REMOVED, line)
    line = _SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}{REMOVED}", line)
    line = _CARD.sub(_card, line)
    line = _SSN.sub(_ssn, line)
    line = _EIN.sub(_ein, line)
    line = _BARE9.sub(_bare9, line)
    if _CODE_CONTEXT.search(line):
        line = _CODE_TOKEN.sub(CODE_MASK, line)
    return line


def redact(text: str) -> str:
    """Mask taxpayer IDs, card numbers and credentials in a rendered digest.

    Line by line, because the one-time-code rule is decided by what a line is
    about, and one task's title must not make the next one's numbers vanish.
    Health details are not handled here; see the module note.
    """
    if not text:
        return text
    return "\n".join(_redact_line(line) for line in text.split("\n"))
