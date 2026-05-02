from app.utils.ai_guardrails import apply_feature_guardrails, validate_recommendation_result


def _card(**overrides):
    base = {
        "make": "Toyota",
        "model": "Corolla",
        "price_ils": 90000,
        "seats": 5,
        "transmission": "automatic",
        "fuel_type": "petrol",
        "why_it_fits": "מתאים למשפחה קטנה",
        "tradeoff": "לא הכי חזק",
        "what_to_check": "בדקו היסטוריית טיפולים",
        "confidence": "בינונית",
        "price_caveat": "דורש אימות",
    }
    base.update(overrides)
    return base


def test_over_budget_recommendation_blocked_unless_stretch():
    report = validate_recommendation_result({"budget_max": 80000}, {"recommended_cars": [_card(price_ils=95000)]})
    assert report["status"] == "critical"


def test_seven_seat_requirement_enforced():
    report = validate_recommendation_result({"seats_choice": 7}, {"recommended_cars": [_card(seats=5)]})
    assert report["status"] == "critical"


def test_automatic_preference_enforced():
    report = validate_recommendation_result({"transmission": "automatic"}, {"recommended_cars": [_card(transmission="manual")]})
    assert report["status"] == "critical"


def test_ev_rejection_enforced():
    report = validate_recommendation_result({"reject_ev": True}, {"recommended_cars": [_card(fuel_type="electric")]})
    assert report["status"] == "critical"


def test_each_recommendation_has_required_fields():
    result, report = apply_feature_guardrails("recommendations", {"budget_max": 100000}, {"recommended_cars": [_card(why_it_fits=None)]})
    assert report["status"] == "critical"
    assert result["recommended_cars"][0]["why_it_fits"]
