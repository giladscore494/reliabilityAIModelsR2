import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.utils.ai_guardrails import apply_feature_guardrails
from app.utils.sanitization import sanitize_analyze_response


def test_sanitization_preserves_active_review_fields_and_shapes():
    payload = {
        "identity_snapshot": {"make": "Toyota", "model": "Corolla", "year": 2021},
        "overview": {"summary": "סקירה שימושית", "less_suitable_for": ["גרירה"]},
        "market_context": {
            "pricing_israel": {"used_price_range_ils": "80,000-95,000"},
            "license_fee_israel": {"annual_fee_ils": 1800},
            "trims_israel": [{"name": "Style", "price_ils": 120000}],
            "official_safety": {"rating": "5 stars", "organization": "Euro NCAP"},
            "warranty_israel": {"vehicle_warranty": "3 שנים"},
        },
        "risk_analysis": {"recalls_or_service_campaigns": [{"year": 2020, "issue": "עדכון תוכנה"}]},
        "ownership_cost": {"expensive_items_to_check": [{"issue": "מצמד", "cost_range_ILS": "2,000-4,000"}]},
        "buyer_checklist": {"paperwork_checks": ["רישיון רכב"], "test_drive_checks": ["רעידות"]},
        "common_competitors_brief": [
            {"model_name": "Mazda 3", "why_relevant": "משפחתית דומה", "advantage": "התנהגות כביש"}
        ],
        "issues_with_costs": [
            {"issue": "משאבת מים", "avg_cost_ILS": 2200, "min_cost_ILS": 1500, "max_cost_ILS": 3000}
        ],
        "research_status": {"open_fields": ["internal only"]},
        "request_id": "req_123",
    }

    sanitized = sanitize_analyze_response(payload)

    assert sanitized["identity_snapshot"]["make"] == "Toyota"
    assert sanitized["overview"]["summary"] == "סקירה שימושית"
    assert sanitized["market_context"]["pricing_israel"]["used_price_range_ils"] == "80,000-95,000"
    assert sanitized["market_context"]["trims_israel"][0]["name"] == "Style"
    assert sanitized["market_context"]["official_safety"]["rating"] == "5 stars"
    assert sanitized["risk_analysis"]["recalls_or_service_campaigns"][0]["issue"] == "עדכון תוכנה"
    assert sanitized["ownership_cost"]["expensive_items_to_check"][0]["cost_range_ILS"] == "2,000-4,000"
    assert sanitized["buyer_checklist"]["paperwork_checks"] == ["רישיון רכב"]
    assert sanitized["common_competitors_brief"][0]["model"] == "Mazda 3"
    assert sanitized["common_competitors_brief"][0]["brief_summary"] == "משפחתית דומה"
    assert sanitized["issues_with_costs"][0]["avg_cost_ILS"] == 2200
    assert sanitized["issues_with_costs"][0]["min_cost_ILS"] == 1500
    assert "request_id" not in sanitized


def test_guardrail_warnings_do_not_hide_valid_active_review_tabs():
    result = {
        "identity_snapshot": {"make": "Toyota", "model": "Corolla", "year": 2021},
        "overview": {"summary": "סקירה שימושית"},
        "market_context": {"pricing_israel": {"used_price_range_ils": "80,000-95,000"}},
        "sources": [],  # produces warnings only, not critical repair/hide
    }

    guarded, report = apply_feature_guardrails(
        "reliability_analysis",
        {"make": "Toyota", "model": "Corolla", "year": 2021},
        result,
        log_validation=False,
    )

    assert report["critical_issues"] == []
    assert report["warnings"]
    assert guarded["identity_snapshot"]["model"] == "Corolla"
    assert guarded["market_context"]["pricing_israel"]["used_price_range_ils"] == "80,000-95,000"
