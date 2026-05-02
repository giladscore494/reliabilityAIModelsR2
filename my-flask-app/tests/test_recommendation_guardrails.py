from app.utils import ai_guardrails


def _payload(**overrides):
    payload = {
        "budget_max": 50000,
        "seats_choice": 5,
        "transmission": "automatic",
        "reject_ev": False,
        "body_style": "כללי",
    }
    payload.update(overrides)
    return payload


def _card(**overrides):
    card = {
        "brand": "Toyota",
        "model": "Corolla",
        "price_range_nis": [45000, 50000],
        "seats": 5,
        "gear": "automatic",
        "fuel": "petrol",
        "why_it_fits": "מתאים לתקציב ולשימוש",
        "tradeoff": "פחות חזק בעליות",
        "what_to_check": "בדקו היסטוריית טיפולים",
        "confidence": "בינונית",
        "price_caveat": "מחיר משוער — דורש אימות",
    }
    card.update(overrides)
    return card


def test_budget_violation_is_critical():
    _, report = ai_guardrails.apply_feature_guardrails(
        "recommendations",
        _payload(budget_max=50000),
        {"recommended_cars": [_card(price_range_nis=[85000, 90000])]},
    )
    assert "recommendation_1: over budget" in report["critical_issues"]


def test_seven_seat_requirement_is_enforced():
    _, report = ai_guardrails.apply_feature_guardrails(
        "recommendations",
        _payload(seats_choice=7),
        {"recommended_cars": [_card(seats=5, body_style="hatchback")]},
    )
    assert "recommendation_1: insufficient seats" in report["critical_issues"]


def test_automatic_requirement_blocks_manual():
    _, report = ai_guardrails.apply_feature_guardrails(
        "recommendations",
        _payload(transmission="automatic"),
        {"recommended_cars": [_card(gear="manual")]},
    )
    assert "recommendation_1: manual despite automatic requirement" in report["critical_issues"]


def test_ev_rejection_blocks_ev_recommendation():
    _, report = ai_guardrails.apply_feature_guardrails(
        "recommendations",
        _payload(reject_ev=True),
        {"recommended_cars": [_card(fuel="electric")]},
    )
    assert "recommendation_1: EV rejected by user" in report["critical_issues"]


def test_missing_reason_or_tradeoff_gets_patched():
    result, report = ai_guardrails.apply_feature_guardrails(
        "recommendations",
        _payload(budget_max=100000),
        {"recommended_cars": [_card(why_it_fits=None, reason_he="מתאים לתקציב", tradeoff=None)]},
    )
    assert report["status"] == "warnings"
    assert result["recommended_cars"][0]["why_it_fits"]
    assert result["recommended_cars"][0]["tradeoff"]


def test_estimated_price_gets_caveat():
    result, _ = ai_guardrails.apply_feature_guardrails(
        "recommendations",
        _payload(budget_max=100000),
        {"recommended_cars": [_card(price_caveat="", price_note="estimated market sample")]},
    )
    assert "דורש אימות" in result["recommended_cars"][0]["price_caveat"]


def test_trim_uncertainty_stays_displayable_with_warning():
    _, report = ai_guardrails.apply_feature_guardrails(
        "recommendations",
        _payload(budget_max=100000),
        {"recommended_cars": [_card(trim="לא מאומת")]},
    )
    assert report["status"] == "warnings"
    assert "recommendation_1: trim/version uncertain" in report["warnings"]


def test_truncated_recommendation_text_is_trimmed_or_hidden():
    result, _ = ai_guardrails.apply_feature_guardrails(
        "recommendations",
        _payload(budget_max=100000),
        {"recommended_cars": [_card(why_it_fits="הדגם הזה מתאים בדרך כלל")]},
    )
    assert result["recommended_cars"][0]["why_it_fits"] != "הדגם הזה מתאים בדרך כלל"


def test_warning_only_path_does_not_trigger_repair(monkeypatch):
    called = {"value": False}

    def fake_repair(result, report):
        called["value"] = True
        return result

    monkeypatch.setattr(ai_guardrails, "_repair_recommendations_result", fake_repair)
    _, report = ai_guardrails.apply_feature_guardrails(
        "recommendations",
        _payload(budget_max=100000),
        {"recommended_cars": [_card(trim="לא מאומת")]},
    )
    assert report["status"] == "warnings"
    assert called["value"] is False
