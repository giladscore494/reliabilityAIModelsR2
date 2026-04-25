"""Tests for the optional post-result research card partials and consent modal."""

from pathlib import Path

import pytest


CONSENT_LABEL = (
    "אני מסכים/ה למסור מידע מחקרי אופציונלי לצורך שיפור מאגר האמינות והמודלים של ידע רכב. "
    "המידע יישמר בנפרד מנתוני השימוש הרגילים, וינותח בצורה מצרפית או לאחר הסרת מזהים ככל שניתן. "
    "אפשר להשתמש בשירות גם בלי להסכים."
)

HELPER_TEXT = (
    "החלק הזה אופציונלי לגמרי. "
    "התוצאה שלך כבר מוכנה ואפשר לפתוח אותה בלי לענות."
)

PARTIALS = [
    "_research_advisor_fields.html",
    "_research_reliability_panel.html",
    "_research_compare_panel.html",
]


def _templates_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "templates"


@pytest.mark.parametrize("partial", PARTIALS)
def test_partial_has_optional_research_helper_text(partial):
    content = (_templates_dir() / partial).read_text(encoding="utf-8")
    assert HELPER_TEXT in content, (
        f"Required optional-research helper text is missing from {partial}"
    )


@pytest.mark.parametrize("partial", PARTIALS)
def test_partial_has_non_blocking_result_ctas(partial):
    content = (_templates_dir() / partial).read_text(encoding="utf-8")
    assert "עונה עכשיו" in content, f'Answer CTA is missing from {partial}'
    assert "לא עכשיו" in content, f'Skip CTA is missing from {partial}'
    assert "פתח תוצאה עכשיו" in content, (
        f'Open-result CTA is missing from {partial}'
    )


def test_research_consent_modal_keeps_separate_explicit_consent():
    content = (_templates_dir() / "_research_consent_modal.html").read_text(
        encoding="utf-8"
    )
    assert CONSENT_LABEL in content
    assert 'type="checkbox"' in content
    assert 'id="{{ checkbox_id|default(\'researchConsentCheckbox\') }}"' in content
