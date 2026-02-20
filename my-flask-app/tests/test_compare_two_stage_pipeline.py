# -*- coding: utf-8 -*-
import copy
from datetime import datetime

from app.services import comparison_service
from app.quota import compute_quota_window, resolve_app_timezone
from app.models import DailyQuotaUsage, ComparisonHistory
from main import db


def _grounded_output_fixture():
    src = [{"url": "https://example.com", "title": "example", "snippet": "snippet"}]
    return {
        "grounding_successful": True,
        "assumptions": {"year_assumption": "2020"},
        "search_queries_used": ["toyota corolla reliability"],
        "cars": {
            "car_1": {
                "reliability_risk": {
                    "reliability_rating": {"value": 85, "sources": src},
                },
            },
            "car_2": {
                "reliability_risk": {
                    "reliability_rating": {"value": 70, "sources": src},
                },
            },
        },
    }


def test_compare_two_stage_keeps_server_authoritative_numbers(app, logged_in_client, monkeypatch):
    client, _user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})

    grounded_output = _grounded_output_fixture()
    expected = comparison_service.compute_comparison_results(grounded_output)

    calls = {"stage_a": 0, "stage_b": 0}

    def fake_stage_a(_prompt, timeout_sec=comparison_service.AI_CALL_TIMEOUT_SEC):
        calls["stage_a"] += 1
        return copy.deepcopy(grounded_output), None

    def fake_stage_b(_prompt, timeout_sec=60):
        calls["stage_b"] += 1
        drifted = copy.deepcopy(expected)
        drifted["cars"]["car_1"]["overall_score"] = 1.0
        return {
            "computed_result": drifted,
            "narrative": {
                "overall_summary": "סיכום בדיקה",
                "category_explanations": [],
                "disclaimers_he": ["בדיקה"],
            },
        }, None

    monkeypatch.setattr(comparison_service, "call_gemini_comparison", fake_stage_a)
    monkeypatch.setattr(comparison_service, "call_gemini_compare_writer", fake_stage_b)

    resp = client.post(
        "/api/compare",
        json={
            "cars": [
                {"make": "Toyota", "model": "Corolla", "year": 2020},
                {"make": "Honda", "model": "Civic", "year": 2020},
            ],
            "legal_confirm": True,
        },
        headers={"Content-Type": "application/json", "Origin": "http://localhost"},
    )
    assert resp.status_code == 200
    payload = resp.get_json()["data"]
    assert calls == {"stage_a": 1, "stage_b": 1}
    assert payload["computed_result"] == expected
    assert payload["narrative"]["overall_summary"] == "סיכום בדיקה"


def test_compare_two_stage_handles_stage_b_failure_gracefully(app, logged_in_client, monkeypatch):
    client, _user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})

    grounded_output = _grounded_output_fixture()
    calls = {"stage_a": 0, "stage_b": 0}

    def fake_stage_a(_prompt, timeout_sec=comparison_service.AI_CALL_TIMEOUT_SEC):
        calls["stage_a"] += 1
        return copy.deepcopy(grounded_output), None

    def fake_stage_b(_prompt, timeout_sec=60):
        calls["stage_b"] += 1
        return None, "CALL_TIMEOUT"

    monkeypatch.setattr(comparison_service, "call_gemini_comparison", fake_stage_a)
    monkeypatch.setattr(comparison_service, "call_gemini_compare_writer", fake_stage_b)

    resp = client.post(
        "/api/compare",
        json={
            "cars": [
                {"make": "Toyota", "model": "Corolla", "year": 2020},
                {"make": "Honda", "model": "Civic", "year": 2020},
            ],
            "legal_confirm": True,
        },
        headers={"Content-Type": "application/json", "Origin": "http://localhost"},
    )
    assert resp.status_code == 200
    payload = resp.get_json()["data"]
    assert calls == {"stage_a": 1, "stage_b": 1}
    assert payload["computed_result"]["overall_winner"] == "car_1"
    assert payload["narrative"] is None


def test_compare_quota_blocks_non_owner(app, logged_in_client):
    client, user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})

    with app.app_context():
        tz, _ = resolve_app_timezone()
        day_key, *_ = compute_quota_window(tz)
        db.session.add(DailyQuotaUsage(user_id=user_id, day=day_key, count=5, updated_at=datetime.utcnow()))
        db.session.commit()

    resp = client.post(
        "/api/compare",
        json={
            "cars": [
                {"make": "Toyota", "model": "Corolla", "year": 2020},
                {"make": "Honda", "model": "Civic", "year": 2020},
            ],
            "legal_confirm": True,
        },
        headers={"Content-Type": "application/json", "Origin": "http://localhost"},
    )
    assert resp.status_code == 429
    data = resp.get_json()
    assert data["error"]["code"] == "DAILY_LIMIT_REACHED"


def test_compare_owner_bypasses_quota(app, logged_in_client, monkeypatch):
    client, user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})
    app.config["OWNER_EMAILS"] = {"tester@example.com"}

    with app.app_context():
        tz, _ = resolve_app_timezone()
        day_key, *_ = compute_quota_window(tz)
        db.session.add(DailyQuotaUsage(user_id=user_id, day=day_key, count=5, updated_at=datetime.utcnow()))
        db.session.commit()

    def fake_handle(_data, _uid, _sid, owner_bypass=False):
        from app.utils.http_helpers import api_ok
        return api_ok({"computed_result": {"overall_winner": "car_1"}, "cars_selected": {}, "narrative": None})

    monkeypatch.setattr(comparison_service, "handle_comparison_request", fake_handle)

    resp = client.post(
        "/api/compare",
        json={
            "cars": [
                {"make": "Toyota", "model": "Corolla", "year": 2020},
                {"make": "Honda", "model": "Civic", "year": 2020},
            ],
            "legal_confirm": True,
        },
        headers={"Content-Type": "application/json", "Origin": "http://localhost"},
    )
    assert resp.status_code == 200


def test_compare_owner_bypasses_internal_history_gate(app, logged_in_client, monkeypatch):
    client, user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})
    app.config["OWNER_EMAILS"] = {"tester@example.com"}

    with app.app_context():
        for _ in range(3):
            db.session.add(
                ComparisonHistory(
                    user_id=user_id,
                    session_id=None,
                    cars_selected='[{"make":"Toyota","model":"Corolla","year":2020},{"make":"Honda","model":"Civic","year":2020}]',
                    model_json_raw='{}',
                    computed_result='{}',
                    sources_index='{}',
                )
            )
        db.session.commit()

    def fake_stage_a(_prompt, timeout_sec=comparison_service.AI_CALL_TIMEOUT_SEC):
        return _grounded_output_fixture(), None

    monkeypatch.setattr(comparison_service, "call_gemini_comparison", fake_stage_a)
    monkeypatch.setattr(comparison_service, "call_gemini_compare_writer", lambda _prompt, timeout_sec=60: (None, "CALL_TIMEOUT"))

    resp = client.post(
        "/api/compare",
        json={
            "cars": [
                {"make": "Toyota", "model": "Corolla", "year": 2020},
                {"make": "Honda", "model": "Civic", "year": 2020},
            ],
            "legal_confirm": True,
        },
        headers={"Content-Type": "application/json", "Origin": "http://localhost"},
    )
    assert resp.status_code == 200
