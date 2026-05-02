from app.utils.ai_guardrails import apply_feature_guardrails, validate_comparison_result


def test_automatic_vs_manual_triggers_repair():
    result, report = apply_feature_guardrails(
        "vehicle_comparison",
        {"cars": [{"make": "Toyota", "model": "Corolla", "year": 2020, "transmission": "automatic"}]},
        {"checked_versions": {"car_1": {"make": "Toyota", "model": "Corolla", "year": 2020, "transmission": "manual"}}},
    )
    assert report["status"] == "critical"
    assert "visible_warning" in result


def test_petrol_vs_hybrid_triggers_repair():
    report = validate_comparison_result(
        {"cars": [{"make": "Toyota", "model": "Corolla", "year": 2020, "fuel_type": "petrol"}]},
        {"checked_versions": {"car_1": {"make": "Toyota", "model": "Corolla", "year": 2020, "engine_type": "hybrid"}}},
    )
    assert report["status"] == "critical"


def test_central_differences_fallback_works():
    result, _ = apply_feature_guardrails(
        "vehicle_comparison",
        {"cars": [{"make": "Toyota", "model": "Corolla"}, {"make": "Honda", "model": "Civic"}]},
        {
            "central_differences": [],
            "decision_result": {
                "category_decisions": [
                    {"category_name_he": "צריכת דלק", "preferred": "car_1", "why": "חסכוני יותר"},
                    {"category_name_he": "מרחב", "preferred": "car_2", "why": "מרווח יותר"},
                ]
            },
        },
    )
    assert len(result["central_differences"]) >= 2


def test_prices_labeled_estimated_if_unverified():
    result, _ = apply_feature_guardrails(
        "vehicle_comparison",
        {"cars": [{"make": "Toyota", "model": "Corolla"}]},
        {"prices_estimated": True, "price_note": "estimated from samples"},
    )
    assert "דורש אימות" in result["price_note"]


def test_fuel_units_shown_consistently():
    result, _ = apply_feature_guardrails(
        "vehicle_comparison",
        {"cars": []},
        {"fuel_consumption": "16 km/l"},
    )
    assert result["fuel_consumption"] == "16 ק״מ לליטר"
