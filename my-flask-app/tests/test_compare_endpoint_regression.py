"""Regression tests for the compare_page endpoint reference fix and Stage A repair pipeline.

Ensures that:
- The homepage renders successfully (no 500).
- The homepage contains a working link to /compare.
- No template references the invalid endpoint 'public.compare_page'.
- The comparison.compare_page endpoint resolves to /compare.
- Stage A repair receives raw model text (not the prompt).
- Research-status-only repair results are rejected.
- All-failed Stage A returns clean retryable errors.

All tests use mocks/stubs — no real AI calls or API keys required.
"""

import concurrent.futures
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import url_for

from app.services import comparison_service
from app.services.comparison import grounding as grounding_mod


TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"


def test_homepage_returns_200(client):
    """Homepage must not return 500."""
    resp = client.get("/")
    assert resp.status_code == 200


def test_homepage_contains_compare_link(client):
    """Landing page must contain a link to /compare."""
    resp = client.get("/")
    html = resp.get_data(as_text=True)
    assert '/compare' in html


def test_no_invalid_public_compare_page_references():
    """No template should reference the invalid endpoint 'public.compare_page'."""
    invalid_refs = [
        "url_for('public.compare_page')",
        'url_for("public.compare_page")',
    ]
    for template_file in TEMPLATES_DIR.rglob("*.html"):
        content = template_file.read_text(encoding="utf-8")
        for ref in invalid_refs:
            assert ref not in content, (
                f"Found invalid endpoint reference '{ref}' in {template_file}"
            )


def test_compare_page_route_resolves(app):
    """The comparison.compare_page endpoint must resolve to /compare."""
    with app.test_request_context():
        assert url_for("comparison.compare_page") == "/compare"


# ========================================================================
# Stage A repair pipeline regression tests
# ========================================================================

VALID_SINGLE_CAR = {
    "car_name": "Toyota Corolla 2020",
    "reliability": {"overall": "high"},
    "ownership_cost": {},
    "comfort_practicality": {},
    "performance_driving": {},
    "facts": {"horsepower": 132},
    "short_notes": ["reliable daily driver"],
    "sources": ["https://example.com/corolla"],
}


def _raw_result(parsed=None, error=None, raw_text="raw model output",
                grounding_meta=None, finish_reason="STOP"):
    return {
        "parsed": parsed,
        "error": error,
        "raw_text": raw_text,
        "grounding_meta": grounding_meta or {"grounding_successful": True, "source_count": 2},
        "finish_reason": finish_reason,
    }


# Test 1: malformed raw output → repair succeeds
def test_stage_a_malformed_raw_repaired_successfully(app, monkeypatch):
    """Stage A raw output is malformed JSON but repair succeeds → merged result is valid."""
    raw_calls = []
    repair_calls = []

    def fake_raw(prompt, car_label, timeout_sec, request_id, log):
        raw_calls.append(car_label)
        return _raw_result(
            parsed=None,
            error="MODEL_JSON_INVALID",
            raw_text='{"car_name":"Corolla"... truncated bad json',
        )

    def fake_repair(raw_text, car_label, original_grounding_meta, request_id, log):
        repair_calls.append(car_label)
        return VALID_SINGLE_CAR.copy(), None

    monkeypatch.setattr(grounding_mod, "_call_gemini_single_car_raw", fake_raw)
    monkeypatch.setattr(grounding_mod, "_attempt_json_repair", fake_repair)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        with app.app_context():
            import app.factory as factory
            monkeypatch.setattr(factory, "AI_EXECUTOR", executor)
            validated_cars = [{"make": "Toyota", "model": "Corolla", "year": 2020}]
            slots = comparison_service.map_cars_to_slots(validated_cars)
            merged, _si, errors = grounding_mod.call_stage_a_parallel(validated_cars, slots)

    assert errors == []
    assert len(raw_calls) == 1
    assert len(repair_calls) == 1
    assert merged["cars"]["car_1"]["reliability"]["overall"] == "high"


# Test 2: repair receives raw model response, NOT original prompt
def test_repair_receives_raw_text_not_prompt(app, monkeypatch):
    """The repair function must receive the raw model text, not the instruction prompt."""
    captured_raw_text = []
    sentinel = "THIS_IS_RAW_MODEL_OUTPUT_NOT_PROMPT_ab12cd34"

    def fake_raw(prompt, car_label, timeout_sec, request_id, log):
        return _raw_result(
            parsed=None,
            error="MODEL_JSON_INVALID",
            raw_text=sentinel,
        )

    def fake_repair(raw_text, car_label, original_grounding_meta, request_id, log):
        captured_raw_text.append(raw_text)
        return VALID_SINGLE_CAR.copy(), None

    monkeypatch.setattr(grounding_mod, "_call_gemini_single_car_raw", fake_raw)
    monkeypatch.setattr(grounding_mod, "_attempt_json_repair", fake_repair)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        with app.app_context():
            import app.factory as factory
            monkeypatch.setattr(factory, "AI_EXECUTOR", executor)
            validated_cars = [{"make": "Hyundai", "model": "Tucson", "year": 2022}]
            slots = comparison_service.map_cars_to_slots(validated_cars)
            grounding_mod.call_stage_a_parallel(validated_cars, slots)

    assert len(captured_raw_text) == 1
    assert captured_raw_text[0] == sentinel, (
        "Repair must receive raw model text, not the instruction prompt"
    )


# Test 3: repair returning research_status-only → rejected
def test_repair_rejects_research_status_only(app, monkeypatch):
    """A repair result containing only research_status keys must be rejected."""
    research_only = {
        "status": "partial",
        "checked_areas": ["reliability"],
        "open_fields": ["ownership_cost"],
        "sources_found": 2,
    }

    class _FakeModels:
        def generate_content(self, **kwargs):
            return SimpleNamespace(
                text=json.dumps(research_only),
                candidates=[],
            )

    monkeypatch.setattr(
        grounding_mod.extensions, "ai_client",
        SimpleNamespace(models=_FakeModels()),
    )

    with app.app_context():
        result, error = grounding_mod._attempt_json_repair(
            raw_text="some broken model text",
            car_label="car_1",
            original_grounding_meta={"grounding_successful": False, "source_count": 0},
            request_id="test-req",
        )

    assert result is None
    assert error is not None
    assert "RESEARCH_STATUS_ONLY" in error or "MODEL_JSON_INVALID" in error


# Test 4: parser extracts JSON from prose/code-fenced model output
def test_parser_extracts_json_from_code_fenced_output():
    """parse_single_car_json should extract JSON from markdown code fences."""
    fenced = '```json\n' + json.dumps(VALID_SINGLE_CAR) + '\n```'
    parsed, error = grounding_mod.parse_single_car_json(fenced)
    assert error is None
    assert parsed is not None
    assert parsed.get("car_name") or parsed.get("reliability")


def test_parser_extracts_json_from_prose_wrapped_output():
    """parse_single_car_json should extract JSON even with surrounding prose."""
    prose = 'Here is the analysis:\n' + json.dumps(VALID_SINGLE_CAR) + '\nEnd of analysis.'
    parsed, error = grounding_mod.parse_single_car_json(prose)
    assert error is None
    assert parsed is not None


# Test 5: regression: mock Stage A invalid → mock repair valid → HTTP 200
def test_stage_a_invalid_repair_valid_returns_200(app, logged_in_client, monkeypatch):
    """End-to-end: Stage A returns invalid JSON, repair succeeds → endpoint returns 200."""
    client, user_id = logged_in_client
    client.post(
        "/api/legal/accept",
        json={"legal_confirm": True},
        headers={"Content-Type": "application/json", "Origin": "http://localhost"},
    )

    decision_payload = {
        "decision_result": {
            "overall_decision": {"label": "car_1", "text": "עדיפות קלה לרכב הראשון."},
            "category_decisions": [],
            "key_differences": [],
            "choose_car_1_if": ["מי שמחפש אמינות."],
            "choose_car_2_if": ["מי שמחפש ביצועים."],
            "avoid_or_check_car_1_if": ["בדקו היסטוריית טיפולים."],
            "avoid_or_check_car_2_if": ["בדקו עלויות ביטוח."],
            "competitors_to_consider": [],
            "practical_summary": "שתי אפשרויות סבירות.",
        },
        "checked_versions": {},
        "sources": ["https://example.com"],
    }

    def fake_single_pass(*args, **kwargs):
        return decision_payload, None, {"grounding_successful": True, "source_count": 1}

    monkeypatch.setattr(
        comparison_service, "call_gemini_single_pass_compare", fake_single_pass
    )

    resp = client.post(
        "/api/compare",
        json={
            "cars": [
                {"make": "Toyota", "model": "Corolla", "year": 2020},
                {"make": "Honda", "model": "Civic", "year": 2021},
            ],
            "legal_confirm": True,
        },
        headers={"Content-Type": "application/json", "Origin": "http://localhost"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data is not None
    assert "error" not in data or data.get("error") is None


# Test 6: both cars failing unrecoverably → clean retryable error
def test_both_cars_fail_unrecoverably_returns_clean_error(app, logged_in_client, monkeypatch):
    """When all Stage A calls fail, the endpoint returns a clean retryable error."""
    client, user_id = logged_in_client
    client.post(
        "/api/legal/accept",
        json={"legal_confirm": True},
        headers={"Content-Type": "application/json", "Origin": "http://localhost"},
    )

    def fake_stage_a_parallel(_validated_cars, cars_selected_slots):
        merged = comparison_service._empty_stage_a_output(cars_selected_slots)
        sources_index = comparison_service.build_sources_index_from_flat(merged)
        errors = [f"{k}: CALL_TIMEOUT" for k in cars_selected_slots]
        return merged, sources_index, errors

    monkeypatch.setattr(comparison_service, "call_stage_a_parallel", fake_stage_a_parallel)
    monkeypatch.setattr(
        comparison_service, "call_gemini_compare_writer",
        lambda *a, **kw: (None, "CALL_TIMEOUT"),
    )

    resp = client.post(
        "/api/compare",
        json={
            "cars": [
                {"make": "Toyota", "model": "Corolla", "year": 2020},
                {"make": "Honda", "model": "Civic", "year": 2021},
            ],
            "legal_confirm": True,
        },
        headers={"Content-Type": "application/json", "Origin": "http://localhost"},
    )
    assert resp.status_code == 503
    data = resp.get_json()
    assert data is not None
    details = data.get("error", {}).get("details", {})
    assert details.get("retryable") is True
    error_code = details.get("error_code", "")
    assert "STAGE_A_ALL_FAILED" not in error_code, (
        "Internal error codes must not be exposed to users"
    )
    assert error_code == "stage_a_unavailable"
