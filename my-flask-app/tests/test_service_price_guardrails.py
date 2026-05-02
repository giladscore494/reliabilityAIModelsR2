from app.utils.ai_guardrails import apply_feature_guardrails, validate_service_prices_result


def test_qty_strings_do_not_crash():
    report = validate_service_prices_result(
        {},
        {"items": [{"canonical_code": "oil_change", "category": "engine", "price_ils": 200, "qty": "2 יחידות"}]},
    )
    assert report["status"] == "passed"


def test_total_mismatch_detected():
    report = validate_service_prices_result(
        {},
        {
            "items": [{"canonical_code": "oil_change", "category": "engine", "price_ils": 200, "qty": 2}],
            "total_price_ils": 100,
        },
    )
    assert report["status"] == "critical"


def test_low_sample_size_caveat_shown():
    result, report = apply_feature_guardrails(
        "service_prices",
        {},
        {"items": [{"canonical_code": "oil_change", "category": "engine", "price_ils": 200, "qty": 1}], "sample_size": 2},
    )
    assert report["status"] == "warnings"
    assert result["sample_size_caveat"]


def test_unknown_canonical_item_stays_low_confidence():
    result, report = apply_feature_guardrails(
        "service_prices",
        {},
        {"items": [{"canonical_code": "unknown_requires_review", "category": "other", "price_ils": 200, "qty": 1, "confidence": 20}]},
    )
    assert "unknown canonical item requires review" in report["warnings"][0]
    assert result["items"][0]["review_status"] == "דורש בדיקה"
