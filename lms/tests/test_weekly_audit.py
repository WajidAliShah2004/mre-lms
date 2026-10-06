"""ops/weekly_audit.py: the summary it records, and that the brief watches it."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import weekly_audit                                          # noqa: E402
from core.reports import brief                               # noqa: E402


def test_parses_the_summary_line_the_mac_prints():
    # Verbatim from the Mac, Oct 6.
    assert weekly_audit.parse_summary("Summary: 0 critical · 2 warn · 2 info") == (0, 2, 2)


def test_no_summary_is_not_a_clean_audit():
    """An audit that crashed must not be recorded as zero findings."""
    assert weekly_audit.parse_summary("Error: gateway unreachable") is None


def test_the_brief_watches_the_audit_for_about_two_missed_weeks():
    name, hours = brief.WATCHED_JOBS["AUDIT_COMPLETED"]
    assert name == "audit" and 14 * 24 < hours <= 17 * 24
