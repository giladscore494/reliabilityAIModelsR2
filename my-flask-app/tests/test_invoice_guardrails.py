import json

from app.utils import ai_guardrails
from app.models import ServiceInvoice
from main import db


def test_invoice_total_mismatch_detected():
    _, report = ai_guardrails.apply_feature_guardrails(
        "invoice_scanner",
        {},
        {
            "items": [{"price_ils": 100, "qty": "x2"}],
            "totals": {"total_price_ils": 500},
        },
    )
    assert "invoice total mismatch beyond tolerance" in report["critical_issues"]


def test_pii_redacted_from_final_output():
    result, report = ai_guardrails.apply_feature_guardrails(
        "invoice_scanner",
        {},
        {"phone": "050-1234567", "items": []},
    )
    assert report["status"] == "critical"
    assert "[REDACTED]" in result["phone"]


def test_raw_invoice_bytes_removed():
    result, report = ai_guardrails.apply_feature_guardrails(
        "invoice_scanner",
        {},
        {"raw_invoice_bytes": b"abc", "items": []},
    )
    assert report["status"] == "critical"
    assert "raw_invoice_bytes" not in result


def test_report_json_string_safe_handling():
    result, report = ai_guardrails.apply_feature_guardrails(
        "invoice_scanner",
        {},
        {"report_json": json.dumps({"totals": {"total_price_ils": 100}}), "items": []},
    )
    assert report["status"] in {"passed", "warnings"}
    assert isinstance(result["report_json"], dict)


def test_service_prices_report_template_handles_string_report_json(logged_in_client, app):
    client, user_id = logged_in_client
    with app.app_context():
        invoice = ServiceInvoice(
            user_id=user_id,
            make="Toyota",
            model="Corolla",
            year=2020,
            parsed_json="{}",
            report_json=json.dumps(json.dumps({"totals": {"total_price_ils": 500}, "items": []})),
        )
        db.session.add(invoice)
        db.session.commit()
        invoice_id = invoice.id
    response = client.get(f"/service-prices/report/{invoice_id}")
    assert response.status_code == 200
    assert "דוח בדיקת חשבונית מוסך" in response.get_data(as_text=True)


def test_ai_cannot_overwrite_deterministic_totals():
    _, report = ai_guardrails.apply_feature_guardrails(
        "invoice_scanner",
        {},
        {
            "items": [{"price_ils": 100, "qty": 1}],
            "totals": {"total_price_ils": 500},
            "report_json": {"totals": {"total_price_ils": 500}},
        },
    )
    assert "invoice total mismatch beyond tolerance" in report["critical_issues"]
