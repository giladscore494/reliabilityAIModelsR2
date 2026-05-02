from app.utils import ai_guardrails


def test_qty_variants_do_not_crash():
    for qty in ("1.00", "x2", "", None):
        _, report = ai_guardrails.apply_feature_guardrails(
            "service_prices",
            {},
            {"items": [{"canonical_code": "oil_change", "category": "engine", "price_ils": 200, "qty": qty}]},
        )
        assert report["status"] in {"passed", "warnings"}


def test_price_string_parses_or_warns():
    _, report = ai_guardrails.apply_feature_guardrails(
        "service_prices",
        {},
        {"items": [{"canonical_code": "oil_change", "category": "engine", "price_ils": "₪250", "qty": 1}]},
    )
    assert report["status"] in {"passed", "warnings"}


def test_labor_parts_total_mismatch_detected():
    _, report = ai_guardrails.apply_feature_guardrails(
        "service_prices",
        {},
        {
            "items": [
                {"canonical_code": "oil_change", "category": "engine", "price_ils": 200, "qty": 1},
                {"canonical_code": "labor", "category": "labor", "price_ils": 150, "qty": 1},
            ],
            "total_price_ils": 100,
        },
    )
    assert "total mismatch beyond tolerance" in report["critical_issues"]


def test_low_sample_size_gets_caveat():
    result, report = ai_guardrails.apply_feature_guardrails(
        "service_prices",
        {},
        {"items": [{"canonical_code": "oil_change", "category": "engine", "price_ils": 200, "qty": 1}], "sample_size": 2},
    )
    assert "benchmark sample size is low" in report["warnings"]
    assert result["sample_size_caveat"]


def test_unknown_canonical_item_stays_low_confidence():
    result, report = ai_guardrails.apply_feature_guardrails(
        "service_prices",
        {},
        {"items": [{"category": "other", "price_ils": 200, "qty": 1, "confidence": 20}]},
    )
    assert result["items"][0]["canonical_code"] == "unknown_requires_review"
    assert result["items"][0]["review_status"] == "דורש בדיקה"
    assert report["status"] in {"warnings", "critical"}


def test_truncated_service_report_text_is_trimmed_or_hidden():
    result, _ = ai_guardrails.apply_feature_guardrails(
        "service_prices",
        {},
        {
            "items": [{"canonical_code": "oil_change", "category": "engine", "price_ils": 200, "qty": 1}],
            "narrative": {"summary": "המחיר נראה סביר בדרך כלל"},
        },
    )
    assert result["narrative"]["summary"] != "המחיר נראה סביר בדרך כלל"
