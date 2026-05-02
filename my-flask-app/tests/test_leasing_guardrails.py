from app.utils import ai_guardrails


def _finance_result(**overrides):
    payload = {
        "monthly_payment": 3200,
        "down_payment": 10000,
        "final_payment": 25000,
        "total_cost": 180000,
        "assumptions": "interest 4.5, cpi indexed, mileage 20000, residual estimated",
        "summary": "לפי המידע הזמין זו אפשרות סבירה.",
    }
    payload.update(overrides)
    return payload


def _finance_calc(**overrides):
    payload = {
        "monthly_payment": 3000,
        "down_payment": 10000,
        "final_payment": 25000,
        "total_cost": 175000,
        "assumptions": "interest 4.0, cpi indexed, mileage 18000, residual estimated",
        "tolerance": 50,
    }
    payload.update(overrides)
    return payload


def test_ai_monthly_payment_mismatch_is_repaired():
    result, report = ai_guardrails.apply_feature_guardrails(
        "leasing_advisor",
        {},
        _finance_result(monthly_payment=4500),
        deterministic_calc=_finance_calc(),
    )
    assert report["status"] == "critical"
    assert result["monthly_payment"] == 3000


def test_balloon_payment_omitted_is_critical():
    _, report = ai_guardrails.apply_feature_guardrails(
        "leasing_advisor",
        {},
        _finance_result(final_payment=None),
        deterministic_calc=_finance_calc(final_payment=25000),
    )
    assert "missing final_payment" in report["critical_issues"]


def test_total_cost_mismatch_is_critical():
    _, report = ai_guardrails.apply_feature_guardrails(
        "leasing_advisor",
        {},
        _finance_result(total_cost=120000),
        deterministic_calc=_finance_calc(total_cost=175000),
    )
    assert "total_cost differs from deterministic calculation" in report["critical_issues"]


def test_guaranteed_savings_language_removed():
    result, report = ai_guardrails.apply_feature_guardrails(
        "leasing_advisor",
        {},
        _finance_result(summary="Guaranteed savings and the best deal available"),
        deterministic_calc=_finance_calc(),
    )
    assert report["status"] == "critical"
    assert "Guaranteed" not in result["summary"]


def test_missing_cpi_indexation_assumption_warns():
    _, report = ai_guardrails.apply_feature_guardrails(
        "leasing_advisor",
        {},
        _finance_result(assumptions="interest 4.0, mileage 18000"),
        deterministic_calc=_finance_calc(cpi_indexed=True),
    )
    assert "CPI/indexation assumption missing" in report["warnings"]


def test_estimated_residual_value_is_marked_estimate():
    result, report = ai_guardrails.apply_feature_guardrails(
        "leasing_advisor",
        {},
        _finance_result(summary="future value residual will remain high"),
        deterministic_calc=_finance_calc(),
    )
    assert "residual value estimated" in report["warnings"]
    assert "הערכה בלבד" in result["summary"]


def test_ai_cannot_override_deterministic_calc():
    result, _ = ai_guardrails.apply_feature_guardrails(
        "leasing_advisor",
        {},
        {"top3": [{"make": "Toyota", "model": "Corolla", "monthly_bik": 6000, "list_price_ils": 200000, "reason_he": "ok"}]},
        deterministic_calc={
            "candidates": [{"make": "Toyota", "model": "Corolla", "monthly_bik": 3400, "list_price_ils": 150000}],
            "frame": {"source": "catalog", "max_bik": 3500},
        },
    )
    assert result["top3"][0]["monthly_bik"] == 3400
    assert result["top3"][0]["list_price_ils"] == 150000


def test_truncated_finance_explanation_is_trimmed_or_hidden():
    result, _ = ai_guardrails.apply_feature_guardrails(
        "leasing_advisor",
        {},
        _finance_result(summary="לפי ההנחות האלו המסלול מתאים בדרך כלל"),
        deterministic_calc=_finance_calc(),
    )
    assert result["summary"] != "לפי ההנחות האלו המסלול מתאים בדרך כלל"


def test_warning_only_path_does_not_trigger_repair(monkeypatch):
    called = {"value": False}

    def fake_repair(result, calc):
        called["value"] = True
        return result

    monkeypatch.setattr(ai_guardrails, "_repair_leasing_result", fake_repair)
    _, report = ai_guardrails.apply_feature_guardrails(
        "leasing_advisor",
        {},
        _finance_calc(assumptions="interest 4.0, residual estimated", cpi_indexed=False),
        deterministic_calc=_finance_calc(cpi_indexed=False),
    )
    assert report["status"] == "warnings"
    assert called["value"] is False
