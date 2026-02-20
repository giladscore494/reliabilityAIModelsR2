# -*- coding: utf-8 -*-
import copy

from app.services import comparison_service


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
