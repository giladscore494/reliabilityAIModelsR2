"""Tests for information-quality review calculation."""

from app.services.analyze_service import compute_reliability_score_and_banner


def _validated_payload(**overrides):
    payload = {
        "make": "Toyota",
        "model": "Corolla",
        "year": 2020,
        "mileage_range": "0-50k",
        "mileage_km": 42000,
        "fuel_type": "בנזין",
        "transmission": "אוטומטית",
        "sub_model": "Sun",
        "trim": "Sun",
        "engine": "1.8",
        "ownership_history": "יד שנייה",
    }
    payload.update(overrides)
    return payload


def _model_output(**overrides):
    payload = {
        "sources": [
            {"title": "A", "url": "https://a.example", "domain": "a.example"},
            {"title": "B", "url": "https://b.example", "domain": "b.example"},
            {"title": "C", "url": "https://c.example", "domain": "c.example"},
            {"title": "D", "url": "https://d.example", "domain": "d.example"},
        ],
        "recommended_checks": ["בדיקת גיר", "אימות ספר טיפולים"],
        "reliability_report": {
            "based_on_available_information": "המידע חלקי אך שימושי.",
            "key_risk_areas_to_examine": [
                {"risk_area": "גיר", "why_to_check": "דורש אימות"},
            ],
            "what_must_be_checked_before_a_decision": {
                "mechanical_inspection_points": ["בדיקת מנוע"],
                "documents_to_verify": ["ספר טיפולים"],
                "questions_to_ask_seller": ["מי טיפל ברכב?"],
                "red_flags_to_look_for": ["חוסר מסמכים"],
            },
            "known_uncertainties": [],
        },
    }
    payload.update(overrides)
    return payload


def test_information_review_is_ready_when_sources_and_focus_exist():
    result = compute_reliability_score_and_banner(
        _validated_payload(),
        {"missing_data_flags": []},
        model_output=_model_output(),
    )

    assert result["data_quality_label"] == "חלקית"
    assert result["decision_readiness"] in ["נדרש אימות נוסף", "מוכן לבדיקה מקצועית"]
    assert "גיר: דורש אימות" in result["verification_focus"][0]
    assert "בדיקת גיר" in result["verification_focus"]


def test_information_review_marks_missing_sources_as_low_quality():
    result = compute_reliability_score_and_banner(
        _validated_payload(),
        {},
        model_output=_model_output(sources=[]),
    )

    assert result["data_quality_label"] == "חסרה"
    assert result["decision_readiness"] == "חסר מידע קריטי"
    assert any("מקורות חיצוניים" in item for item in result["missing_critical_info"])


def test_information_review_uses_request_missing_fields():
    result = compute_reliability_score_and_banner(
        _validated_payload(sub_model=None, mileage_range=None, ownership_history=None),
        {},
        model_output=_model_output(),
    )

    assert result["data_quality_label"] in ["חסרה", "חלקית"]
    assert any("תת-דגם/תצורה" in item for item in result["missing_critical_info"])
    assert any("טווח קילומטראז׳" in item for item in result["missing_critical_info"])


def test_information_review_preserves_explicit_labels_when_valid():
    result = compute_reliability_score_and_banner(
        _validated_payload(),
        {},
        model_output=_model_output(
            data_quality_label="טובה",
            decision_readiness="מוכן לבדיקה מקצועית",
            missing_critical_info=["ספר טיפולים מלא"],
            verification_focus=["בדיקת מחשב"],
        ),
    )

    assert result["data_quality_label"] == "טובה"
    assert result["decision_readiness"] == "מוכן לבדיקה מקצועית"
    assert "ספר טיפולים מלא" in result["missing_critical_info"]
    assert result["verification_focus"] == ["בדיקת מחשב"]


def test_information_review_carries_mileage_note():
    result = compute_reliability_score_and_banner(
        _validated_payload(mileage_range="מעל 200,000 ק\"מ"),
        {},
        model_output=_model_output(),
        mileage_range="מעל 200,000 ק\"מ",
    )

    assert "mileage_note" in result
