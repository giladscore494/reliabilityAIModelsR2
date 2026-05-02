from app.utils.ai_guardrails import apply_feature_guardrails, validate_leasing_advisor_result


def test_ai_number_mismatch_repaired_to_deterministic_calc():
    result, report = apply_feature_guardrails(
        "leasing_advisor",
        {},
        {"monthly_payment": 5000, "assumptions": {"rate": 5}},
        deterministic_calc={"monthly_payment": 3500, "assumptions": {"rate": 4}},
    )
    assert report["status"] == "critical"
    assert result["monthly_payment"] == 3500


def test_balloon_final_payment_required():
    report = validate_leasing_advisor_result({}, {"monthly_payment": 3000}, {"final_payment": 20000})
    assert report["status"] == "critical"


def test_guaranteed_savings_language_removed():
    result, report = apply_feature_guardrails(
        "leasing_advisor",
        {},
        {"summary": "Guaranteed savings for every driver", "assumptions": {"rate": 5}},
        deterministic_calc={"assumptions": {"rate": 4}},
    )
    assert report["status"] == "critical"
    assert "Guaranteed" not in result["summary"]


def test_assumptions_shown():
    result, _ = apply_feature_guardrails(
        "leasing_advisor",
        {},
        {"summary": "ok"},
        deterministic_calc={"assumptions": {"interest": 4.5}},
    )
    assert result["assumptions"] == {"interest": 4.5}
