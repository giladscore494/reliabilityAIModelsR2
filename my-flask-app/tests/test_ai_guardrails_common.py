from app.utils.ai_guardrails import (
    apply_feature_guardrails,
    is_probably_truncated_text,
    normalize_currency_ils,
    normalize_fuel_consumption,
    repair_or_hide_truncated_text,
    validate_percentage_range,
    validate_score_range,
)


def test_hebrew_truncated_text_repaired_or_hidden():
    assert is_probably_truncated_text("זה רכב טוב אבל")
    repaired = repair_or_hide_truncated_text({"summary": "זה רכב טוב אבל"}, "reliability_analysis")
    assert repaired["summary"]
    assert repaired["summary"] != "זה רכב טוב אבל"


def test_low_confidence_strong_phrases_downgraded():
    result, _ = apply_feature_guardrails(
        "reliability_analysis",
        {"make": "Toyota", "model": "Corolla", "year": 2020},
        {
            "vehicle_identity": {"make": "Toyota", "model": "Corolla", "year": 2020},
            "reliability_summary": "זה בוודאות הרכב הכי אמין.",
            "confidence": 20,
            "sources": ["src"],
        },
    )
    assert "בוודאות" not in result["reliability_summary"]
    assert "הכי אמין" not in result["reliability_summary"]


def test_score_and_percent_range_validation():
    assert validate_score_range(80, 0, 100) is True
    assert validate_score_range(120, 0, 100) is False
    assert validate_percentage_range("55%", 0, 100) is True
    assert validate_percentage_range("-1%", 0, 100) is False


def test_fuel_unit_normalization():
    assert normalize_fuel_consumption("15 km/l") == "15 ק״מ לליטר"
    assert normalize_fuel_consumption("5 l/100km") == "20 ק״מ לליטר"


def test_currency_normalization():
    assert normalize_currency_ils("₪ 12,345") == 12345
