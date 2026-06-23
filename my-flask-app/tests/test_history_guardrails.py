import json

from app.models import SearchHistory
from app.utils import ai_guardrails
from main import db


def test_critical_unresolved_result_is_not_displayed_normally():
    result, report = ai_guardrails.apply_feature_guardrails(
        "dashboard_history",
        {},
        {"phone": "050-1234567", "prompt": "secret"},
    )
    # After the new dashboard-history repair, PII is redacted and debug fields
    # are removed, so the *repaired* payload should no longer be critical.
    assert report["status"] in ("passed", "warnings")
    assert "050-1234567" not in json.dumps(result, ensure_ascii=False)
    assert "prompt" not in {k for k in result if k != "guardrail_meta"}


def test_legacy_result_shows_caveat():
    result, report = ai_guardrails.apply_feature_guardrails(
        "dashboard_history",
        {},
        {"guardrail_meta": {"guardrail_version": "legacy-version"}},
    )
    assert report["status"] == "warnings"
    assert result["legacy_notice"]


def test_pii_not_exposed_in_dashboard_json(logged_in_client, app):
    client, user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})
    with app.app_context():
        db.session.add(
            SearchHistory(
                user_id=user_id,
                make="Toyota",
                model="Corolla",
                year=2020,
                mileage_range="0-50k",
                fuel_type="בנזין",
                transmission="אוטומטית",
                result_json=json.dumps({"summary": "call 050-1234567", "prompt": "hidden"}),
            )
        )
        db.session.commit()
        search_id = SearchHistory.query.filter_by(user_id=user_id).first().id
    response = client.get(f"/search-details/{search_id}")
    body = response.get_json()["data"]["data"]
    dumped = json.dumps(body, ensure_ascii=False)
    assert "050-1234567" not in dumped
    assert "prompt" not in dumped


def test_old_guardrail_version_marked_legacy():
    report = ai_guardrails.validate_dashboard_history_payload(
        {"guardrail_meta": {"guardrail_version": "old"}}
    )
    assert "cached result uses old guardrail version" in report["warnings"]


def test_internal_debug_fields_hidden():
    result, _ = ai_guardrails.apply_feature_guardrails(
        "dashboard_history",
        {},
        {"debug": {"a": 1}, "prompt_text": "secret", "summary": "ok"},
    )
    assert "debug" not in result
    assert "prompt_text" not in result
