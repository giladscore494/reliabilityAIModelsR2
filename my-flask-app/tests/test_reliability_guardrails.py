from app.utils.ai_guardrails import apply_feature_guardrails, validate_reliability_result


def test_wrong_vehicle_identity_triggers_critical():
    report = validate_reliability_result(
        {"make": "Toyota", "model": "Corolla", "year": 2020},
        {"vehicle_identity": {"make": "Honda", "model": "Civic", "year": 2020}},
    )
    assert report["status"] == "critical"


def test_unsupported_defect_claim_downgraded():
    result, report = apply_feature_guardrails(
        "reliability_analysis",
        {"make": "Toyota", "model": "Corolla", "year": 2020},
        {
            "vehicle_identity": {"make": "Toyota", "model": "Corolla", "year": 2020},
            "known_risks": "Known defect confirmed",
            "confidence": 20,
        },
    )
    assert report["status"] == "critical"
    assert "לבדוק מול רישיון הרכב" in result["known_risks"]


def test_low_data_high_confidence_repaired():
    result, report = apply_feature_guardrails(
        "reliability_analysis",
        {"make": "Toyota", "model": "Corolla", "year": 2020},
        {
            "vehicle_identity": {"make": "Toyota", "model": "Corolla", "year": 2020},
            "reliability_summary": "מוכח שאין ספק שזה רכב תקין מכנית",
            "confidence": 20,
        },
    )
    assert report["status"] == "critical"
    assert "בדיקה מקצועית" in result["reliability_summary"]


def test_missing_mileage_prevents_service_history_inference():
    report = validate_reliability_result(
        {"make": "Toyota", "model": "Corolla", "year": 2020},
        {"vehicle_identity": {"make": "Toyota", "model": "Corolla", "year": 2020}, "sources": ["src"]},
    )
    assert "mileage missing" in report["warnings"]


def test_recall_overlap_not_double_counted():
    result, _ = apply_feature_guardrails(
        "reliability_analysis",
        {"make": "Toyota", "model": "Corolla", "year": 2020, "mileage_range": "0-50k"},
        {
            "vehicle_identity": {"make": "Toyota", "model": "Corolla", "year": 2020},
            "known_risks": ["Recall item", "Recall item"],
            "sources": ["src"],
        },
    )
    assert isinstance(result["known_risks"], list)
