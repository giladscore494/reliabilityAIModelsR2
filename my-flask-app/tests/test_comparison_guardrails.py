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


# --- Regression tests: transmission contradiction overwrite ---


def test_automatic_selected_manual_returned_by_ai_is_overwritten():
    """User selected automatic; AI returned manual → final must show אוטומטית, not ידנית."""
    user_payload = {"cars": [{"make": "Toyota", "model": "Corolla", "year": 2020, "transmission": "automatic"}]}
    ai_result = {
        "checked_versions": {
            "car_1": {
                "make": "Toyota",
                "model": "Corolla",
                "year": "2020",
                "trim": "Sport",
                "engine_type": "בנזין",
                "transmission": "ידנית",
                "drivetrain": "FWD",
                "seats": "5",
                "notes": "גרסה מייצגת.",
            }
        }
    }
    result, report = apply_feature_guardrails("vehicle_comparison", user_payload, ai_result)
    assert report["status"] == "critical"
    transmission = result["checked_versions"]["car_1"]["transmission"]
    assert "ידנית" not in transmission, f"Expected no ידנית but got: {transmission}"
    assert "אוטומטית" in transmission, f"Expected אוטומטית but got: {transmission}"


def test_manual_selected_automatic_returned_by_ai_is_overwritten():
    """User selected manual; AI returned automatic → final must show ידנית, not אוטומטית."""
    user_payload = {"cars": [{"make": "Suzuki", "model": "Swift", "year": 2019, "transmission": "manual"}]}
    ai_result = {
        "checked_versions": {
            "car_1": {
                "make": "Suzuki",
                "model": "Swift",
                "year": "2019",
                "trim": "Basic",
                "engine_type": "בנזין",
                "transmission": "אוטומטית",
                "drivetrain": "FWD",
                "seats": "5",
                "notes": "גרסה מייצגת.",
            }
        }
    }
    result, report = apply_feature_guardrails("vehicle_comparison", user_payload, ai_result)
    assert report["status"] == "critical"
    transmission = result["checked_versions"]["car_1"]["transmission"]
    assert "אוטומטית" not in transmission, f"Expected no אוטומטית but got: {transmission}"
    assert "ידנית" in transmission, f"Expected ידנית but got: {transmission}"


def test_automatic_selected_via_gearbox_manual_returned_by_ai_is_overwritten():
    """User passed automatic via gearbox field; AI returned ידנית → must be corrected."""
    user_payload = {"cars": [{"make": "Honda", "model": "Civic", "year": 2021, "gearbox": "automatic"}]}
    ai_result = {
        "checked_versions": {
            "car_1": {
                "make": "Honda",
                "model": "Civic",
                "year": "2021",
                "trim": "Sport",
                "engine_type": "בנזין",
                "transmission": "ידנית",
                "drivetrain": "FWD",
                "seats": "5",
                "notes": "גרסה מייצגת.",
            }
        }
    }
    result, report = apply_feature_guardrails("vehicle_comparison", user_payload, ai_result)
    assert report["status"] == "critical"
    transmission = result["checked_versions"]["car_1"]["transmission"]
    assert "ידנית" not in transmission, f"Expected no ידנית but got: {transmission}"
    assert "אוטומטית" in transmission, f"Expected אוטומטית but got: {transmission}"


# --- Regression tests: empty required fields are backfilled ---


def test_checked_versions_empty_required_fields_are_backfilled():
    """AI returns empty strings for required checked_versions fields → must be backfilled."""
    user_payload = {"cars": [{"make": "Mazda", "model": "3", "year": 2022, "transmission": "automatic"}]}
    ai_result = {
        "checked_versions": {
            "car_1": {
                "make": "Mazda",
                "model": "3",
                "year": "",
                "trim": "",
                "engine_type": "",
                "transmission": "אוטומטית",
                "drivetrain": "",
                "seats": "",
                "notes": "",
            }
        }
    }
    result, _ = apply_feature_guardrails("vehicle_comparison", user_payload, ai_result)
    slot = result["checked_versions"]["car_1"]
    for field in ("trim", "engine_type", "drivetrain", "seats", "notes"):
        assert slot.get(field), f"Expected non-empty {field}, got: {slot.get(field)!r}"


def test_checked_versions_missing_fields_are_backfilled():
    """AI returns checked_versions slot with missing keys → must be backfilled."""
    user_payload = {"cars": [{"make": "Kia", "model": "Sportage", "year": 2023, "transmission": "automatic"}]}
    ai_result = {
        "checked_versions": {
            "car_1": {
                "make": "Kia",
                "model": "Sportage",
                "transmission": "אוטומטית",
            }
        }
    }
    result, _ = apply_feature_guardrails("vehicle_comparison", user_payload, ai_result)
    slot = result["checked_versions"]["car_1"]
    for field in ("trim", "engine_type", "drivetrain", "seats", "notes"):
        assert slot.get(field), f"Expected non-empty {field}, got: {slot.get(field)!r}"


def test_checked_versions_missing_block_is_rebuilt_with_fallbacks():
    """When checked_versions is entirely absent, it is rebuilt with safe fallback values."""
    user_payload = {
        "cars": [
            {"make": "Toyota", "model": "Yaris", "year": 2022, "transmission": "automatic"},
            {"make": "Honda", "model": "Jazz", "year": 2022, "transmission": "automatic"},
        ]
    }
    ai_result = {
        "decision_result": {
            "overall_decision": {"label": "car_1", "text": "טויוטה עדיפה קלות."},
            "category_decisions": [],
            "key_differences": [],
        }
    }
    result, _ = apply_feature_guardrails("vehicle_comparison", user_payload, ai_result)
    assert "checked_versions" in result
    for slot_key in ("car_1", "car_2"):
        slot = result["checked_versions"][slot_key]
        for field in ("trim", "drivetrain", "seats", "notes"):
            assert slot.get(field), f"{slot_key}.{field} must not be empty"

