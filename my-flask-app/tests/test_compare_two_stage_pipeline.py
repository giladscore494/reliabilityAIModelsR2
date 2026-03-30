# -*- coding: utf-8 -*-
import copy
import time
from datetime import datetime
from types import SimpleNamespace
import concurrent.futures

from app.services import comparison_service
from app.quota import compute_quota_window, resolve_app_timezone
from app.models import DailyQuotaUsage, ComparisonHistory
from main import db


def _grounded_output_fixture():
    return {
        "cars": {
            "car_1": {
                "car_name": "Toyota Corolla 2020",
                "reliability": {
                    "overall": "high",
                    "issue_frequency": "low",
                    "issue_severity": "low",
                    "repair_cost_risk": "low",
                    "recall_risk": "low",
                    "parts_complexity": "low",
                },
                "ownership_cost": {
                    "fuel_cost": "low",
                    "routine_maintenance": "low",
                    "repair_burden": "low",
                    "insurance_burden": "medium",
                    "depreciation_risk": "low",
                },
                "comfort_practicality": {
                    "space": "medium",
                    "ride_comfort": "medium",
                    "trunk_usefulness": "medium",
                    "daily_usability": "high",
                },
                "performance_driving": {
                    "power_feel": "medium",
                    "power_to_weight": "medium",
                    "braking_confidence": "medium",
                    "handling_agility": "medium",
                    "fun_to_drive": "medium",
                },
                "facts": {"horsepower": 138, "weight_kg": 1310, "body_type": "sedan", "fuel_type": "petrol"},
                "short_notes": ["מוניטין אמינות חזק", "אחזקה צפויה ונפוצה"],
                "sources": ["https://example.com/toyota"],
            },
            "car_2": {
                "car_name": "Honda Civic 2020",
                "reliability": {
                    "overall": "medium",
                    "issue_frequency": "medium",
                    "issue_severity": "medium",
                    "repair_cost_risk": "medium",
                    "recall_risk": "low",
                    "parts_complexity": "medium",
                },
                "ownership_cost": {
                    "fuel_cost": "medium",
                    "routine_maintenance": "medium",
                    "repair_burden": "medium",
                    "insurance_burden": "medium",
                    "depreciation_risk": "medium",
                },
                "comfort_practicality": {
                    "space": "medium",
                    "ride_comfort": "medium",
                    "trunk_usefulness": "medium",
                    "daily_usability": "medium",
                },
                "performance_driving": {
                    "power_feel": "medium",
                    "power_to_weight": "medium",
                    "braking_confidence": "medium",
                    "handling_agility": "high",
                    "fun_to_drive": "high",
                },
                "facts": {"horsepower": 158, "weight_kg": 1325, "body_type": "sedan", "fuel_type": "petrol"},
                "short_notes": ["קצת יותר מהנה לנהיגה"],
                "sources": ["https://example.com/honda"],
            },
        },
        "sources": ["https://example.com/toyota", "https://example.com/honda"],
    }


def _grounded_output_fixture_three_cars():
    grounded = _grounded_output_fixture()
    grounded["cars"]["car_3"] = {
        "car_name": "Mazda 3 2020",
        "reliability": {
            "overall": "medium",
            "issue_frequency": "low",
            "issue_severity": "low",
            "repair_cost_risk": "medium",
            "recall_risk": "low",
            "parts_complexity": "medium",
        },
        "ownership_cost": {
            "fuel_cost": "medium",
            "routine_maintenance": "medium",
            "repair_burden": "medium",
            "insurance_burden": "medium",
            "depreciation_risk": "medium",
        },
        "comfort_practicality": {
            "space": "medium",
            "ride_comfort": "medium",
            "trunk_usefulness": "medium",
            "daily_usability": "medium",
        },
        "performance_driving": {
            "power_feel": "high",
            "power_to_weight": "high",
            "braking_confidence": "high",
            "handling_agility": "high",
            "fun_to_drive": "high",
        },
        "facts": {"horsepower": 186, "weight_kg": 1380, "body_type": "hatchback", "fuel_type": "petrol"},
        "short_notes": ["מרגישה חדה יותר בכביש"],
        "sources": ["https://example.com/mazda"],
    }
    grounded["sources"].append("https://example.com/mazda")
    return grounded


def _fake_stage_a_parallel(grounded_output):
    """Return a fake call_stage_a_parallel that returns the fixture."""
    def _inner(validated_cars, cars_selected_slots):
        import copy
        merged = copy.deepcopy(grounded_output)
        sources_index = comparison_service.build_sources_index_from_flat(merged)
        return merged, sources_index, []
    return _inner


def test_compare_two_stage_keeps_server_authoritative_numbers(app, logged_in_client, monkeypatch):
    client, _user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})

    grounded_output = _grounded_output_fixture()
    expected = comparison_service.compute_comparison_results(grounded_output)

    monkeypatch.setattr(comparison_service, "call_stage_a_parallel", _fake_stage_a_parallel(grounded_output))

    def fake_stage_b(_prompt, timeout_sec=60):
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
    assert payload["computed_result"] == expected
    assert payload["narrative"]["overall_summary"] == "סיכום בדיקה"


def test_compare_two_stage_handles_stage_b_failure_gracefully(app, logged_in_client, monkeypatch):
    client, _user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})

    grounded_output = _grounded_output_fixture()

    monkeypatch.setattr(comparison_service, "call_stage_a_parallel", _fake_stage_a_parallel(grounded_output))

    def fake_stage_b(_prompt, timeout_sec=60):
        return None, "CALL_TIMEOUT"

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
    assert payload["computed_result"]["overall_winner"] == "car_1"
    assert payload["narrative"] is not None
    assert "הסבר ai לא זמין" in payload["narrative"]["overall_summary"].lower()
    assert len(payload["narrative"]["category_explanations"]) == 4
    assert payload["ai"]["status"] == "fallback"
    assert payload["ai"]["reason"] == "stage_b_error"


def test_compare_stage_b_length_error_returns_fallback_200_fast(app, logged_in_client, monkeypatch):
    client, _user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})

    monkeypatch.setattr(comparison_service, "call_stage_a_parallel", _fake_stage_a_parallel(_grounded_output_fixture()))

    def fake_stage_b(_prompt, timeout_sec=comparison_service.COMPARE_WRITER_TIMEOUT_SEC):
        return None, "CALL_FAILED_OUTPUT_TOO_LONG"

    monkeypatch.setattr(comparison_service, "call_gemini_compare_writer", fake_stage_b)

    start = time.perf_counter()
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
    elapsed = time.perf_counter() - start

    assert resp.status_code == 200
    assert elapsed < (comparison_service.COMPARE_WRITER_TIMEOUT_SEC + 5)
    payload = resp.get_json()["data"]
    assert payload["narrative"] is not None
    assert "הסבר ai לא זמין" in payload["narrative"]["overall_summary"].lower()
    assert len(payload["narrative"]["category_explanations"]) == 4
    assert payload["ai"]["status"] == "fallback"
    assert payload["ai"]["reason"] == "stage_b_error"


def test_compare_stage_b_json_schema_parsed_into_narrative(app, logged_in_client, monkeypatch):
    client, _user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})

    monkeypatch.setattr(comparison_service, "call_stage_a_parallel", _fake_stage_a_parallel(_grounded_output_fixture()))

    def fake_stage_b(_prompt, timeout_sec=comparison_service.COMPARE_WRITER_TIMEOUT_SEC):
        return {
            "summary": "טויוטה מובילה מעט באמינות ובתמונה הכוללת.",
            "winner": "carA",
            "categories": [
                {
                    "name": "reliability_risk",
                    "winner": "carA",
                    "why": "פחות סיכון לתקלות משמעותיות לפי הניקוד.",
                    "tips": ["בדקו היסטוריית טיפולים", "בצעו בדיקת קנייה"],
                }
            ],
            "caveats": ["הנתונים עשויים להשתנות לפי רמת תחזוקה."],
        }, None

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
    assert payload["narrative"]["overall_summary"]
    cat = payload["narrative"]["category_explanations"][0]
    assert cat["category_key"] == "reliability_risk"
    assert cat["winner"] == "car_1"
    assert payload["ai"]["status"] == "ok"
    assert payload["ai"]["reason"] is None


def test_compare_three_cars_stage_b_slot_schema_parsed_into_narrative(app, logged_in_client, monkeypatch):
    client, _user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})

    grounded_output = _grounded_output_fixture_three_cars()
    expected = comparison_service.compute_comparison_results(grounded_output)

    monkeypatch.setattr(comparison_service, "call_stage_a_parallel", _fake_stage_a_parallel(grounded_output))

    def fake_stage_b(_prompt, timeout_sec=comparison_service.COMPARE_WRITER_TIMEOUT_SEC):
        return {
            "summary": "מאזדה בולטת בנהיגה, בזמן שהפער הכללי מול האחרות אינו גדול.",
            "winner": "car_3",
            "categories": [
                {
                    "name": "driving_performance",
                    "winner": "car_3",
                    "why": "הניקוד הדינמי הגבוה ביותר שייך לה.",
                    "tips": ["בדקו מצב צמיגים"],
                }
            ],
            "caveats": ["כדאי לבדוק היסטוריית טיפולים."],
        }, None

    monkeypatch.setattr(comparison_service, "call_gemini_compare_writer", fake_stage_b)

    resp = client.post(
        "/api/compare",
        json={
            "cars": [
                {"make": "Toyota", "model": "Corolla", "year": 2020},
                {"make": "Honda", "model": "Civic", "year": 2020},
                {"make": "Mazda", "model": "3", "year": 2020},
            ],
            "legal_confirm": True,
        },
        headers={"Content-Type": "application/json", "Origin": "http://localhost"},
    )

    assert resp.status_code == 200
    payload = resp.get_json()["data"]
    assert payload["computed_result"] == expected
    assert payload["narrative"]["category_explanations"][0]["winner"] == "car_3"
    assert set(payload["narrative"]["category_explanations"][0]["explanations"].keys()) == {"car_1", "car_2", "car_3"}
    assert payload["ai"]["stage_a"]["winner"] == expected["overall_winner"]
    assert payload["ai"]["status"] == "ok"


def test_compare_stage_a_timeout_returns_503_with_retryable_error(app, logged_in_client, monkeypatch):
    client, _user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})

    def fake_stage_a_parallel(validated_cars, cars_selected_slots):
        empty = comparison_service._empty_stage_a_output(cars_selected_slots)
        sources_index = comparison_service.build_sources_index_from_flat(empty)
        errors = [f"{k}: CALL_TIMEOUT" for k in cars_selected_slots]
        return empty, sources_index, errors

    monkeypatch.setattr(comparison_service, "call_stage_a_parallel", fake_stage_a_parallel)
    monkeypatch.setattr(comparison_service, "call_gemini_compare_writer", lambda _prompt, timeout_sec=comparison_service.COMPARE_WRITER_TIMEOUT_SEC: (None, "CALL_TIMEOUT"))

    start = time.perf_counter()
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
    elapsed = time.perf_counter() - start
    assert resp.status_code == 503
    assert elapsed < 20
    body = resp.get_json()
    assert body["ok"] is False
    assert body["error"]["code"] == "comparison_ai_unavailable"
    assert body["error"]["details"]["stage"] == "stage_a"
    assert body["error"]["details"]["retryable"] is True


def test_parse_stage_a_json_handles_fences_and_text():
    raw = """prefix
```json
{"grounding_successful": true, "search_queries_used": [], "assumptions": {}, "cars": {}}
```
suffix"""
    parsed, err = comparison_service.parse_stage_a_json(raw)
    assert err is None
    assert parsed["grounding_successful"] is True


def test_parse_stage_a_json_repairs_trailing_comma():
    raw = '{"grounding_successful": true, "search_queries_used": [], "assumptions": {}, "cars": {},}'
    parsed, err = comparison_service.parse_stage_a_json(raw)
    assert err is None
    assert isinstance(parsed, dict)


def test_parse_stage_a_json_invalid_returns_model_json_invalid():
    parsed, err = comparison_service.parse_stage_a_json("not-json")
    assert parsed is None
    assert err == "MODEL_JSON_INVALID"


def test_stage_a_config_is_bounded_and_tools_disabled(app, monkeypatch):
    captured = {}

    class _FakeModels:
        def generate_content(self, *, model, contents, config):
            captured["config"] = config
            return SimpleNamespace(text='{"car_name":"Toyota Corolla 2020","reliability":{"overall":"high"},"ownership_cost":{},"comfort_practicality":{},"performance_driving":{},"facts":{},"short_notes":[],"sources":[]}')

    monkeypatch.setattr(comparison_service.extensions, "ai_client", SimpleNamespace(models=_FakeModels()))
    with app.app_context():
        out, err = comparison_service.call_gemini_single_car("{}", "car_1", timeout_sec=1)
    assert err is None
    assert isinstance(out, dict)
    cfg = captured["config"]
    assert int(getattr(cfg, "max_output_tokens", 0)) == 2048
    assert not getattr(cfg, "tools", None)


def test_call_gemini_compare_writer_exception_path_returns_error(app, monkeypatch):
    class _FailingModels:
        def generate_content(self, **_kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr(comparison_service.extensions, "ai_client", SimpleNamespace(models=_FailingModels()))
    with app.app_context():
        out, err = comparison_service.call_gemini_compare_writer("{}", timeout_sec=1)
    assert out is None
    assert err and err.startswith("CALL_FAILED:")


def test_compare_ai_regenerate_updates_ai_only(app, logged_in_client, monkeypatch):
    client, user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})

    grounded_output = _grounded_output_fixture()
    monkeypatch.setattr(comparison_service, "call_stage_a_parallel", _fake_stage_a_parallel(grounded_output))
    monkeypatch.setattr(
        comparison_service,
        "call_gemini_compare_writer",
        lambda _prompt, timeout_sec=comparison_service.COMPARE_WRITER_TIMEOUT_SEC: ({
            "summary": "סיכום ראשון.",
            "winner": "carA",
            "categories": [],
            "caveats": [],
        }, None),
    )

    first = client.post(
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
    assert first.status_code == 200
    comparison_id = first.get_json()["data"]["comparison_id"]

    monkeypatch.setattr(
        comparison_service,
        "call_gemini_compare_writer",
        lambda _prompt, timeout_sec=comparison_service.COMPARE_WRITER_TIMEOUT_SEC: ({
            "summary": "סיכום מעודכן.",
            "winner": "carA",
            "categories": [],
            "caveats": [],
        }, None),
    )

    regen = client.post(
        f"/api/compare/ai-regenerate?comparison_id={comparison_id}",
        json={"legal_confirm": True},
        headers={"Origin": "http://localhost", "Referer": "http://localhost/compare"},
        environ_overrides={"HTTP_ORIGIN": "http://localhost", "HTTP_REFERER": "http://localhost/compare"},
    )
    assert regen.status_code == 200
    regen_payload = regen.get_json()["data"]
    assert regen_payload["ai"]["status"] == "ok"
    assert regen_payload["ai"]["stage_b"] is not None


def test_compare_stage_a_json_invalid_returns_503_without_persisting_success(app, logged_in_client, monkeypatch):
    client, _user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})

    def fake_stage_a_parallel(validated_cars, cars_selected_slots):
        empty = comparison_service._empty_stage_a_output(cars_selected_slots)
        sources_index = comparison_service.build_sources_index_from_flat(empty)
        errors = [f"{k}: MODEL_JSON_INVALID" for k in cars_selected_slots]
        return empty, sources_index, errors

    monkeypatch.setattr(comparison_service, "call_stage_a_parallel", fake_stage_a_parallel)
    monkeypatch.setattr(comparison_service, "call_gemini_compare_writer", lambda _prompt, timeout_sec=comparison_service.COMPARE_WRITER_TIMEOUT_SEC: (None, "CALL_TIMEOUT"))

    start = time.perf_counter()
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
    elapsed = time.perf_counter() - start
    assert resp.status_code == 503
    assert elapsed < 20
    body = resp.get_json()
    assert body["ok"] is False
    assert body["error"]["code"] == "comparison_ai_unavailable"
    details = body["error"]["details"]
    assert details["stage"] == "stage_a"
    assert details["error_code"] == "MODEL_JSON_INVALID"
    assert details["retryable"] is True
    with app.app_context():
        assert ComparisonHistory.query.count() == 0


def test_compare_partial_stage_a_failure_returns_200_partial_fallback(app, logged_in_client, monkeypatch):
    client, _user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})

    def fake_stage_a_parallel(_validated_cars, cars_selected_slots):
        output = comparison_service._empty_stage_a_output(cars_selected_slots)
        output["cars"]["car_1"] = {
            "car_name": "Toyota Corolla 2020",
            "reliability": {
                "overall": "high",
                "issue_frequency": "low",
                "issue_severity": "low",
                "repair_cost_risk": "low",
                "recall_risk": "low",
                "parts_complexity": "low",
            },
            "ownership_cost": {},
            "comfort_practicality": {},
            "performance_driving": {},
            "facts": {},
            "short_notes": [],
            "sources": ["https://example.com/toyota"],
        }
        sources_index = comparison_service.build_sources_index_from_flat(output)
        return output, sources_index, ["car_2: CALL_TIMEOUT"]

    monkeypatch.setattr(comparison_service, "call_stage_a_parallel", fake_stage_a_parallel)
    monkeypatch.setattr(
        comparison_service,
        "call_gemini_compare_writer",
        lambda _prompt, timeout_sec=comparison_service.COMPARE_WRITER_TIMEOUT_SEC: ({
            "summary": "סיכום חלקי.",
            "winner": "carA",
            "categories": [],
            "caveats": [],
        }, None),
    )

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
    assert payload["ai"]["status"] == "partial_fallback"
    assert payload["ai"]["reason"] == "stage_a_partial"
    assert "השוואה חלקית" in payload["narrative"]["overall_summary"]
    assert any("חלקית" in item for item in payload["narrative"]["disclaimers_he"])


def test_call_stage_a_parallel_error_classification(app, monkeypatch):
    class _FakeFuture:
        def __init__(self, behavior):
            self.behavior = behavior

        def result(self, timeout=None):
            if self.behavior == "timeout":
                raise concurrent.futures.TimeoutError("took too long")
            if self.behavior == "cancelled":
                raise concurrent.futures.CancelledError("cancelled")
            if self.behavior == "runtime":
                raise RuntimeError("boom")
            return (
                {
                    "car_name": "Kia Sportage 2020",
                    "reliability": {"overall": "high"},
                    "ownership_cost": {},
                    "comfort_practicality": {},
                    "performance_driving": {},
                    "facts": {},
                    "short_notes": [],
                    "sources": ["https://example.com/kia"],
                },
                None,
            )

        def cancel(self):
            return True

    class _FakeExecutor:
        def __init__(self):
            self.calls = 0
            self.behaviors = ["timeout", "cancelled", "runtime", "ok"]

        def submit(self, *_args, **_kwargs):
            behavior = self.behaviors[self.calls]
            self.calls += 1
            return _FakeFuture(behavior)

    fake_executor = _FakeExecutor()
    with app.app_context():
        import app.factory as factory
        monkeypatch.setattr(factory, "AI_EXECUTOR", fake_executor)
        validated_cars = [
            {"make": "Toyota", "model": "Corolla", "year": 2020},
            {"make": "Honda", "model": "Civic", "year": 2020},
            {"make": "Mazda", "model": "3", "year": 2020},
            {"make": "Kia", "model": "Sportage", "year": 2020},
        ]
        slots = comparison_service.map_cars_to_slots(validated_cars)
        merged, _sources_index, errors = comparison_service.call_stage_a_parallel(validated_cars, slots)

    assert "car_1: CALL_TIMEOUT" in errors
    assert "car_2: CALL_CANCELLED" in errors
    assert "car_3: CALL_FAILED:RuntimeError" in errors
    assert merged["cars"]["car_4"]["reliability"]["overall"] == "high"


def test_call_stage_a_parallel_real_threads_do_not_require_worker_app_context(app, monkeypatch):
    class _FakeAuto:
        def __init__(self, **kwargs):
            self.disable = kwargs.get("disable")
            self.maximum_remote_calls = kwargs.get("maximum_remote_calls")

    class _FakeConfig:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class _FakeModels:
        def generate_content(self, **_kwargs):
            return SimpleNamespace(
                text='{"car_name":"Toyota Corolla 2020","reliability":{"overall":"high"},"ownership_cost":{},"comfort_practicality":{},"performance_driving":{},"facts":{},"short_notes":[],"sources":["https://example.com/toyota"]}'
            )

    monkeypatch.setattr(comparison_service.extensions, "ai_client", SimpleNamespace(models=_FakeModels()))
    monkeypatch.setattr(comparison_service.genai_types, "AutomaticFunctionCallingConfig", _FakeAuto)
    monkeypatch.setattr(comparison_service.genai_types, "GenerateContentConfig", _FakeConfig)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as real_executor:
        with app.app_context():
            import app.factory as factory
            monkeypatch.setattr(factory, "AI_EXECUTOR", real_executor)
            validated_cars = [
                {"make": "Toyota", "model": "Corolla", "year": 2020},
                {"make": "Honda", "model": "Civic", "year": 2020},
            ]
            slots = comparison_service.map_cars_to_slots(validated_cars)
            merged, _sources_index, errors = comparison_service.call_stage_a_parallel(validated_cars, slots)

    assert errors == []
    assert merged["cars"]["car_1"]["reliability"]["overall"] == "high"
    assert merged["cars"]["car_2"]["reliability"]["overall"] == "high"


def test_compare_quota_released_on_full_stage_a_failure(app, logged_in_client, monkeypatch):
    client, user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})

    def fake_stage_a_parallel(_validated_cars, cars_selected_slots):
        empty = comparison_service._empty_stage_a_output(cars_selected_slots)
        sources_index = comparison_service.build_sources_index_from_flat(empty)
        errors = [f"{k}: CALL_TIMEOUT" for k in cars_selected_slots]
        return empty, sources_index, errors

    monkeypatch.setattr(comparison_service, "call_stage_a_parallel", fake_stage_a_parallel)
    monkeypatch.setattr(comparison_service, "call_gemini_compare_writer", lambda *_args, **_kwargs: (None, "CALL_TIMEOUT"))

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
    assert resp.status_code == 503
    with app.app_context():
        tz, _ = resolve_app_timezone()
        day_key, *_ = compute_quota_window(tz)
        quota = DailyQuotaUsage.query.filter_by(user_id=user_id, day=day_key).first()
        assert quota is None or quota.count == 0


def test_compare_ai_regenerate_writer_exception_returns_200_fallback(app, logged_in_client, monkeypatch):
    client, _user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})

    grounded_output = _grounded_output_fixture()
    monkeypatch.setattr(comparison_service, "call_stage_a_parallel", _fake_stage_a_parallel(grounded_output))
    monkeypatch.setattr(
        comparison_service,
        "call_gemini_compare_writer",
        lambda _prompt, timeout_sec=comparison_service.COMPARE_WRITER_TIMEOUT_SEC: ({
            "summary": "סיכום ראשון.",
            "winner": "carA",
            "categories": [],
            "caveats": [],
        }, None),
    )
    first = client.post(
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
    comparison_id = first.get_json()["data"]["comparison_id"]
    def _raise_writer(*_args, **_kwargs):
        raise RuntimeError("writer boom")

    monkeypatch.setattr(comparison_service, "call_gemini_compare_writer", _raise_writer)

    regen = client.post(
        f"/api/compare/ai-regenerate?comparison_id={comparison_id}",
        json={"legal_confirm": True},
        headers={"Origin": "http://localhost", "Referer": "http://localhost/compare"},
    )
    assert regen.status_code == 200
    regen_payload = regen.get_json()["data"]
    assert regen_payload["ai"]["status"] == "fallback"
    assert regen_payload["ai"]["reason"] == "stage_b_error"


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
    assert data["error"] == "daily_limit_reached"
    assert "reset_at" in data


def test_compare_idempotency_key_does_not_consume_quota_twice(app, logged_in_client, monkeypatch):
    client, user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})

    def fake_handle(_data, _uid, _sid, owner_bypass=False):
        from app.utils.http_helpers import api_ok
        return api_ok({"computed_result": {"overall_winner": "car_1"}, "cars_selected": {}, "narrative": None})

    monkeypatch.setattr(comparison_service, "handle_comparison_request", fake_handle)

    headers = {
        "Content-Type": "application/json",
        "Origin": "http://localhost",
        "X-Idempotency-Key": "same-request-key",
    }
    payload = {
        "cars": [
            {"make": "Toyota", "model": "Corolla", "year": 2020},
            {"make": "Honda", "model": "Civic", "year": 2020},
        ],
        "legal_confirm": True,
    }

    resp1 = client.post("/api/compare", json=payload, headers=headers)
    resp2 = client.post("/api/compare", json=payload, headers=headers)
    assert resp1.status_code == 200
    assert resp2.status_code == 200

    with app.app_context():
        tz, _ = resolve_app_timezone()
        day_key, *_ = compute_quota_window(tz)
        quota = DailyQuotaUsage.query.filter_by(user_id=user_id, day=day_key).first()
        assert quota and quota.count == 1


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

    monkeypatch.setattr(
        comparison_service,
        "call_stage_a_parallel",
        _fake_stage_a_parallel(_grounded_output_fixture()),
    )
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
