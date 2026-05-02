from app.utils.ai_guardrails import apply_feature_guardrails, validate_dashboard_history_payload


def test_unresolved_critical_result_not_saved_normally():
    report = validate_dashboard_history_payload({"phone": "050-1234567"})
    assert report["status"] == "critical"


def test_old_guardrail_version_marked_legacy():
    result, report = apply_feature_guardrails(
        "dashboard_history",
        {},
        {"guardrail_meta": {"guardrail_version": "old-version"}},
    )
    assert report["status"] == "warnings"
    assert result["legacy_notice"]


def test_pii_not_exposed():
    report = validate_dashboard_history_payload({"customer_phone": "050-1234567"})
    assert report["status"] == "critical"
