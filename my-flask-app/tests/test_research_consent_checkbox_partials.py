"""Tests proving the explicit research-consent checkbox text is present in
all three research partials (advisor, reliability, compare).
"""

from pathlib import Path

import pytest


CONSENT_LABEL = (
    "אני מסכים/ה למסור מידע מחקרי אופציונלי לצורך שיפור מאגר האמינות והמודלים של ידע רכב. "
    "המידע יישמר בנפרד מנתוני השימוש הרגילים, וינותח בצורה מצרפית או לאחר הסרת מזהים ככל שניתן. "
    "אפשר להשתמש בשירות גם בלי להסכים."
)

HELPER_TEXT = (
    "החלק הזה אופציונלי לגמרי. "
    "התוצאה שלך כבר מוכנה ואפשר להשתמש בשירות גם בלי לענות."
)

PARTIALS = [
    "_research_advisor_fields.html",
    "_research_reliability_panel.html",
    "_research_compare_panel.html",
]


def _templates_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "templates"


@pytest.mark.parametrize("partial", PARTIALS)
def test_partial_has_research_consent_checkbox_label(partial):
    content = (_templates_dir() / partial).read_text(encoding="utf-8")
    assert CONSENT_LABEL in content, (
        f"Required research-consent label is missing from {partial}"
    )


@pytest.mark.parametrize("partial", PARTIALS)
def test_partial_has_optional_research_helper_text(partial):
    content = (_templates_dir() / partial).read_text(encoding="utf-8")
    assert HELPER_TEXT in content, (
        f"Required optional-research helper text is missing from {partial}"
    )


@pytest.mark.parametrize("partial", PARTIALS)
def test_partial_has_separate_research_consent_checkbox_input(partial):
    content = (_templates_dir() / partial).read_text(encoding="utf-8")
    # Must be a real <input type="checkbox"> dedicated to research consent,
    # not bundled into Terms/Privacy acceptance.
    assert 'name="research_consent_optin"' in content, (
        f"Dedicated research-consent checkbox input is missing from {partial}"
    )
    assert 'type="checkbox"' in content, (
        f"Research-consent control must be a checkbox in {partial}"
    )
