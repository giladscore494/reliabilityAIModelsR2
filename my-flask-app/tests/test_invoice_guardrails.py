from app.utils.ai_guardrails import apply_feature_guardrails, validate_invoice_analysis_result


def test_pii_redacted():
    result, report = apply_feature_guardrails(
        "invoice_scanner",
        {},
        {"customer_name": "דני", "phone": "050-1234567", "items": []},
    )
    assert report["status"] == "critical"
    assert "[REDACTED]" in result["phone"]


def test_report_json_string_safe_handling():
    report = validate_invoice_analysis_result({}, "raw-string")
    assert report["status"] == "critical"


def test_raw_invoice_bytes_blocked():
    report = validate_invoice_analysis_result({}, {"raw_invoice_bytes": b"abc"})
    assert report["status"] == "critical"
