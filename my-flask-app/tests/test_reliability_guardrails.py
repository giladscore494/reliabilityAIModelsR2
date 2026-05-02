from app.utils import ai_guardrails


def _base_payload(**overrides):
    payload = {
        "make": "Toyota",
        "model": "Corolla",
        "year": 2020,
        "fuel_type": "petrol",
        "transmission": "automatic",
        "mileage_range": "0-50k",
    }
    payload.update(overrides)
    return payload


def _base_result(**overrides):
    result = {
        "vehicle_identity": {
            "make": "Toyota",
            "model": "Corolla",
            "year": 2020,
            "engine_type": "petrol",
            "transmission": "automatic",
        },
        "reliability_summary": "לפי המידע הזמין יש צורך בבדיקה לפני קנייה.",
        "what_to_check": ["בדיקת גיר", "בדיקת שילדה"],
        "confidence": 55,
        "source_type": "verified_source",
        "sources": [{"title": "Source", "url": "https://example.com"}],
        "data_quality_label": "חלקית",
    }
    result.update(overrides)
    return result


def test_wrong_vehicle_identity_triggers_critical():
    _, report = ai_guardrails.apply_feature_guardrails(
        "reliability_analysis",
        _base_payload(),
        _base_result(vehicle_identity={"make": "Honda", "model": "Civic", "year": 2020}),
    )
    assert report["status"] == "critical"
    assert any("wrong make identity" in issue or "wrong model identity" in issue for issue in report["critical_issues"])


def test_wrong_year_triggers_critical():
    _, report = ai_guardrails.apply_feature_guardrails(
        "reliability_analysis",
        _base_payload(),
        _base_result(vehicle_identity={"make": "Toyota", "model": "Corolla", "year": 2018}),
    )
    assert "wrong vehicle year" in report["critical_issues"]


def test_petrol_automatic_vs_diesel_manual_triggers_critical():
    _, report = ai_guardrails.apply_feature_guardrails(
        "reliability_analysis",
        _base_payload(),
        _base_result(
            vehicle_identity={
                "make": "Toyota",
                "model": "Corolla",
                "year": 2020,
                "engine_type": "diesel",
                "transmission": "manual",
            }
        ),
    )
    assert "engine or fuel mismatch" in report["critical_issues"]
    assert "transmission mismatch" in report["critical_issues"]


def test_unsupported_known_defect_stated_as_fact_is_flagged():
    result, report = ai_guardrails.apply_feature_guardrails(
        "reliability_analysis",
        _base_payload(),
        _base_result(known_risks="Known defect confirmed in this model", sources=[]),
    )
    assert "unsupported major defect stated as fact" in report["critical_issues"]
    assert "ספר טיפולים" in result["known_risks"]


def test_low_data_quality_high_confidence_is_downgraded():
    result, report = ai_guardrails.apply_feature_guardrails(
        "reliability_analysis",
        _base_payload(),
        _base_result(
            reliability_summary="מוכח שאין ספק שזה הרכב הכי אמין.",
            confidence=92,
            data_quality_label="חסרה",
            sources=[],
            source_type="ai_estimate",
        ),
    )
    assert report["status"] == "critical"
    assert "confidence_note" in report["affected_sections"]
    assert "ברמת ודאות" in " ".join(result.get("guardrail_caveats", []))


def test_missing_mileage_prevents_service_history_inference():
    result, report = ai_guardrails.apply_feature_guardrails(
        "reliability_analysis",
        _base_payload(mileage_range=None),
        _base_result(what_to_check="הרכב מוזנח כנראה בגלל חוסר טיפולים"),
    )
    assert "mileage missing" in report["warnings"]
    assert report["status"] == "critical"
    assert "בדיקה מקצועית" in result["what_to_check"]


def test_specific_car_condition_claim_adds_inspection_caveat():
    result, report = ai_guardrails.apply_feature_guardrails(
        "reliability_analysis",
        _base_payload(),
        _base_result(reliability_summary="הרכב הספציפי הזה תקין מכנית לחלוטין"),
    )
    assert report["status"] == "critical"
    assert any("בדיקה מקצועית" in caveat for caveat in result.get("guardrail_caveats", []))
    assert "בדיקה מקצועית" in result["reliability_summary"]


def test_truncated_hebrew_output_is_hidden_or_trimmed():
    result, report = ai_guardrails.apply_feature_guardrails(
        "reliability_analysis",
        _base_payload(),
        _base_result(reliability_summary="המידע מראה שהרכב בדרך כלל"),
    )
    assert report["status"] in {"warnings", "critical", "passed"}
    assert result["reliability_summary"] != "המידע מראה שהרכב בדרך כלל"


def test_internal_score_does_not_leak_as_primary_ui_field():
    result, report = ai_guardrails.apply_feature_guardrails(
        "reliability_analysis",
        _base_payload(),
        _base_result(score_0_100=81, internal_score=81),
    )
    assert "score_0_100" not in result
    assert "internal_score" not in result
    assert "internal score should not be primary user output" in report["warnings"]


def test_warning_only_path_does_not_trigger_repair(monkeypatch):
    called = {"value": False}

    def fake_repair(result):
        called["value"] = True
        return result

    monkeypatch.setattr(ai_guardrails, "_repair_reliability_result", fake_repair)
    _, report = ai_guardrails.apply_feature_guardrails(
        "reliability_analysis",
        _base_payload(mileage_range=None),
        _base_result(),
    )
    assert report["status"] == "warnings"
    assert called["value"] is False
