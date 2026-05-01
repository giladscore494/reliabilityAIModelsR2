# -*- coding: utf-8 -*-
from pathlib import Path


def test_recommendations_ui_uses_fit_score_as_preference_fit():
    combined = Path("static/recommendations.js").read_text(encoding="utf-8") + Path("templates/recommendations.html").read_text(encoding="utf-8")
    assert "התאמת העדפות" in combined


def test_recommendations_ui_does_not_call_reliability_score_truth():
    combined = Path("static/recommendations.js").read_text(encoding="utf-8") + Path("templates/recommendations.html").read_text(encoding="utf-8")
    forbidden = ["ציון אמינות", "אמינות מוערכת", "הרכב הכי אמין", "מומלץ לקנייה"]
    assert not any(token in combined for token in forbidden)


def test_advisor_prompt_includes_richer_fields():
    text = Path("app/factory.py").read_text(encoding="utf-8")
    for token in ["official_safety", "license_fee_israel", "trim_levels_israel", "warranty_israel", "competitors"]:
        assert token in text
    assert "אל תמציא ציוני בטיחות" in text
