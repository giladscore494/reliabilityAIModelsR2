# -*- coding: utf-8 -*-
"""Tests for JSON stability improvements across vehicle review, comparison, and advisor."""

import json
import types
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# Helpers: mock Gemini responses
# ---------------------------------------------------------------------------

def _mock_response(text, grounding_successful=False, source_count=0, queries=None, finish_reason=None):
    """Build a fake Gemini SDK response object."""
    chunks = [types.SimpleNamespace()] * source_count if source_count else []
    gm = types.SimpleNamespace(
        web_search_queries=queries or [],
        grounding_chunks=chunks,
    )
    candidate = types.SimpleNamespace(
        grounding_metadata=gm if (queries or chunks) else None,
        finish_reason=finish_reason,
    )
    resp = types.SimpleNamespace(
        text=text,
        candidates=[candidate],
    )
    return resp


def _mock_response_no_grounding(text):
    return _mock_response(text, grounding_successful=False)


# ---------------------------------------------------------------------------
# 1. Vehicle Review (reliability_model_service)
# ---------------------------------------------------------------------------

class TestVehicleReviewJsonStability:
    """Test parse_model_json robustness and call_gemini_grounded_once repair flow."""

    def test_prose_before_json_parses(self):
        from app.services.reliability_model_service import parse_model_json
        raw = 'Here is the analysis:\n\n{"make": "Toyota", "model": "Corolla", "year": 2020}'
        parsed, err = parse_model_json(raw)
        assert err is None
        assert parsed["make"] == "Toyota"

    def test_code_fenced_json_parses(self):
        from app.services.reliability_model_service import parse_model_json
        raw = '```json\n{"make": "Honda", "model": "Civic"}\n```'
        parsed, err = parse_model_json(raw)
        assert err is None
        assert parsed["make"] == "Honda"

    def test_malformed_json_triggers_repair_and_succeeds(self):
        from app.services.reliability_model_service import parse_model_json
        raw = '{"make": "Toyota", "model": "Corolla",}'
        parsed, err = parse_model_json(raw)
        assert err is None
        assert parsed["make"] == "Toyota"

    def test_invalid_json_after_all_repair_returns_error(self):
        from app.services.reliability_model_service import parse_model_json
        raw = "This is not JSON at all, just plain prose without braces."
        parsed, err = parse_model_json(raw)
        assert parsed is None
        assert err == "MODEL_JSON_INVALID"

    def test_empty_input_returns_empty_response(self):
        from app.services.reliability_model_service import parse_model_json
        parsed, err = parse_model_json("")
        assert parsed is None
        assert err == "EMPTY_RESPONSE"

    def test_grounded_call_repair_preserves_grounding_meta(self):
        """When grounded call returns unparseable JSON, repair call fires
        and original grounding_meta is preserved on the result."""
        from app.services import reliability_model_service as svc

        bad_json = '{"make": "Toyota" this is broken'
        good_json = '{"make": "Toyota", "model": "Corolla"}'

        grounded_resp = _mock_response(
            bad_json, grounding_successful=True, source_count=3,
            queries=["toyota corolla reliability"]
        )
        repair_resp = _mock_response_no_grounding(good_json)

        call_count = {"n": 0}
        def fake_generate(model, contents, config):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return grounded_resp
            return repair_resp

        mock_client = mock.MagicMock()
        mock_client.models.generate_content.side_effect = fake_generate

        with mock.patch.object(svc.extensions, "ai_client", mock_client), \
             mock.patch.object(svc, "AI_CALL_TIMEOUT_SEC", 30):
            parsed, err = svc.call_gemini_grounded_once("test prompt")

        assert err is None
        assert parsed["make"] == "Toyota"
        meta = parsed.get("_grounding_meta", {})
        assert meta["grounding_successful"] is True
        assert meta["source_count"] == 3
        assert "toyota corolla reliability" in meta["search_queries"]

    def test_invalid_json_after_repair_returns_502_cleanly(self):
        """If both grounded call and repair produce invalid JSON, return error without crash."""
        from app.services import reliability_model_service as svc

        bad_text = "completely unparseable garbage without any json"
        grounded_resp = _mock_response(bad_text)
        repair_resp = _mock_response_no_grounding("still not json")

        call_count = {"n": 0}
        def fake_generate(model, contents, config):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return grounded_resp
            return repair_resp

        mock_client = mock.MagicMock()
        mock_client.models.generate_content.side_effect = fake_generate

        with mock.patch.object(svc.extensions, "ai_client", mock_client), \
             mock.patch.object(svc, "AI_CALL_TIMEOUT_SEC", 30):
            parsed, err = svc.call_gemini_grounded_once("test prompt")

        assert parsed is None
        assert err == "MODEL_JSON_INVALID"


# ---------------------------------------------------------------------------
# 2. Comparison Stage A (grounding.py)
# ---------------------------------------------------------------------------

class TestComparisonStageAJsonStability:
    """Test Stage A JSON stability, repair, schema echo rejection."""

    def test_valid_json_with_json_mime_succeeds(self):
        """Simulate the response_mime_type path producing valid JSON."""
        from app.services.comparison.grounding import _call_gemini_single_car_raw
        import app.extensions as extensions

        valid_payload = {
            "car_name": "Toyota Corolla 2020",
            "reliability": {"known_issues": "low", "recall_history": "low", "long_term_durability": "medium", "parts_availability": "high"},
            "ownership_cost": {"insurance_cost": "medium", "maintenance_cost": "low", "fuel_consumption": "low", "depreciation": "medium"},
            "comfort_practicality": {"interior_space": "medium", "ride_comfort": "medium", "noise_levels": "low", "cargo_capacity": "medium"},
            "performance_driving": {"acceleration": "low", "handling": "medium", "braking": "medium", "transmission_quality": "medium"},
            "facts": {"horsepower": 132, "weight_kg": 1300},
            "short_notes": ["Reliable commuter"],
            "sources": [],
        }
        resp = _mock_response(
            json.dumps(valid_payload),
            grounding_successful=True,
            source_count=2,
            queries=["corolla reliability"],
        )

        mock_client = mock.MagicMock()
        mock_client.models.generate_content.return_value = resp

        with mock.patch.object(extensions, "ai_client", mock_client):
            result = _call_gemini_single_car_raw("prompt", "car_1", timeout_sec=60)

        assert result["error"] is None
        assert result["parsed"] is not None
        assert result["grounding_meta"]["grounding_successful"] is True

    def test_sdk_rejection_of_json_mime_falls_back(self):
        """When GenerateContentConfig rejects tools + response_mime_type,
        the code should fall back to tools-only config."""
        from app.services.comparison import grounding
        from google.genai import types as genai_types

        valid_payload = {
            "car_name": "Toyota Corolla 2020",
            "reliability": {"known_issues": "low", "recall_history": "low", "long_term_durability": "medium", "parts_availability": "high"},
            "ownership_cost": {"insurance_cost": "medium", "maintenance_cost": "low", "fuel_consumption": "low", "depreciation": "medium"},
            "comfort_practicality": {"interior_space": "medium", "ride_comfort": "medium", "noise_levels": "low", "cargo_capacity": "medium"},
            "performance_driving": {"acceleration": "low", "handling": "medium", "braking": "medium", "transmission_quality": "medium"},
            "facts": {"horsepower": 132, "weight_kg": 1300},
            "short_notes": ["Reliable"],
            "sources": [],
        }
        resp = _mock_response(
            json.dumps(valid_payload),
            grounding_successful=True,
            source_count=1,
            queries=["corolla"],
        )

        original_config_init = genai_types.GenerateContentConfig.__init__
        call_count = {"n": 0}

        def patched_init(self, *args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1 and "response_mime_type" in kwargs:
                raise TypeError("response_mime_type not supported with tools")
            return original_config_init(self, *args, **kwargs)

        mock_client = mock.MagicMock()
        mock_client.models.generate_content.return_value = resp
        import app.extensions as extensions_mod

        with mock.patch.object(extensions_mod, "ai_client", mock_client), \
             mock.patch.object(genai_types.GenerateContentConfig, "__init__", patched_init):
            result = grounding._call_gemini_single_car_raw("prompt", "car_1", timeout_sec=60)

        assert result["error"] is None
        assert result["parsed"] is not None

    def test_truncated_json_triggers_repair(self):
        """Unbalanced/truncated JSON from Stage A triggers local + API repair."""
        from app.services.comparison.grounding import _call_gemini_single_car_raw
        import app.extensions as extensions

        truncated = '{"car_name": "Toyota Corolla", "reliability": {"known_issues": "low"'
        resp = _mock_response(truncated, grounding_successful=True, source_count=1, queries=["test"])

        mock_client = mock.MagicMock()
        mock_client.models.generate_content.return_value = resp

        with mock.patch.object(extensions, "ai_client", mock_client):
            result = _call_gemini_single_car_raw("prompt", "car_1", timeout_sec=60)

        assert result["error"] is not None
        assert result["raw_text"] == truncated

    def test_schema_echo_is_rejected(self):
        """Payload with schema echo patterns like 'high|medium|low' is rejected."""
        from app.services.comparison.parsing import _is_schema_echo

        echo_payload = {
            "car_name": "string",
            "reliability": {"known_issues": "high|medium|low"},
        }
        assert _is_schema_echo(echo_payload) is True

    def test_schema_echo_string_values_rejected(self):
        from app.services.comparison.parsing import _is_schema_echo

        echo_payload = {
            "car_name": "string",
            "facts": {"body_type": "string", "fuel_type": "string"},
        }
        assert _is_schema_echo(echo_payload) is True

    def test_repair_receives_raw_text_not_original_prompt(self):
        """The repair call must receive the raw model text, not the original prompt."""
        from app.services.comparison.grounding import _attempt_json_repair
        import app.extensions as extensions

        raw_model_text = '{"car_name": "Honda Civic", "reliability": {"known_issues": "low", "recall_history": "low", "long_term_durability": "medium", "parts_availability": "high"}, "ownership_cost": {"insurance_cost": "medium", "maintenance_cost": "low", "fuel_consumption": "low", "depreciation": "medium"}, "comfort_practicality": {"interior_space": "medium", "ride_comfort": "medium", "noise_levels": "low", "cargo_capacity": "medium"}, "performance_driving": {"acceleration": "low", "handling": "medium", "braking": "medium", "transmission_quality": "medium"}, "facts": {"horsepower": 158, "weight_kg": 1300}, "short_notes": ["Reliable"], "sources": []}'
        resp = _mock_response_no_grounding(raw_model_text)

        captured_contents = {}
        def capture_generate(model, contents, config):
            captured_contents["contents"] = contents
            return resp

        mock_client = mock.MagicMock()
        mock_client.models.generate_content.side_effect = capture_generate

        with mock.patch.object(extensions, "ai_client", mock_client):
            result, err = _attempt_json_repair(
                raw_model_text, "car_1",
                {"grounding_successful": True, "source_count": 2},
            )

        assert "contents" in captured_contents
        assert "MODEL RESPONSE:" in captured_contents["contents"]
        assert raw_model_text[:100] in captured_contents["contents"]

    def test_repair_research_status_only_rejected(self):
        """Repair result that is only research_status keys is rejected
        (either by parse_single_car_json validation or the explicit check)."""
        from app.services.comparison.grounding import _attempt_json_repair
        import app.extensions as extensions

        research_only = json.dumps({
            "status": "partial",
            "checked_areas": ["pricing"],
            "open_fields": ["safety"],
            "sources_found": 2,
        })
        resp = _mock_response_no_grounding(research_only)

        mock_client = mock.MagicMock()
        mock_client.models.generate_content.return_value = resp

        with mock.patch.object(extensions, "ai_client", mock_client):
            result, err = _attempt_json_repair(
                "some raw text", "car_1",
                {"grounding_successful": False, "source_count": 0},
            )

        assert result is None
        assert err is not None
        # Rejected either as invalid payload or as research-status-only
        assert "REPAIR_" in err or "MODEL_JSON_INVALID" in err

    def test_stage_a_partial_failure_returns_partial_not_crash(self):
        """If one car fails in parallel Stage A, other cars still return."""
        from app.services.comparison.grounding import call_stage_a_parallel
        from flask import Flask

        valid_payload = {
            "car_name": "Toyota Corolla 2020",
            "reliability": {"known_issues": "low", "recall_history": "low", "long_term_durability": "medium", "parts_availability": "high"},
            "ownership_cost": {"insurance_cost": "medium", "maintenance_cost": "low", "fuel_consumption": "low", "depreciation": "medium"},
            "comfort_practicality": {"interior_space": "medium", "ride_comfort": "medium", "noise_levels": "low", "cargo_capacity": "medium"},
            "performance_driving": {"acceleration": "low", "handling": "medium", "braking": "medium", "transmission_quality": "medium"},
            "facts": {"horsepower": 132, "weight_kg": 1300},
            "short_notes": ["Reliable"],
            "sources": [],
        }

        def fake_single_car_raw(prompt, car_label, timeout_sec=60, request_id=None, log=None):
            if car_label == "car_2":
                return {
                    "parsed": None, "error": "CALL_TIMEOUT",
                    "raw_text": None, "grounding_meta": {"grounding_successful": False, "source_count": 0},
                    "finish_reason": None,
                }
            from app.services.comparison.grounding import parse_single_car_json, _extract_stage_a_grounding
            resp = _mock_response(
                json.dumps(valid_payload),
                grounding_successful=True, source_count=1, queries=["test"],
            )
            parsed, err = parse_single_car_json(json.dumps(valid_payload))
            gmeta = _extract_stage_a_grounding(resp)
            if parsed:
                parsed["_grounding_meta"] = gmeta
            return {
                "parsed": parsed, "error": err,
                "raw_text": json.dumps(valid_payload),
                "grounding_meta": gmeta,
                "finish_reason": "STOP",
            }

        cars = [
            {"make": "Toyota", "model": "Corolla", "year": 2020, "display_name": "Toyota Corolla 2020"},
            {"make": "Honda", "model": "Civic", "year": 2020, "display_name": "Honda Civic 2020"},
        ]
        slots = {"car_1": cars[0], "car_2": cars[1]}

        mock_executor = mock.MagicMock()
        mock_executor.submit.side_effect = lambda fn, *args, **kwargs: mock.MagicMock(
            result=lambda timeout: fn(*args, **kwargs)
        )

        app = Flask(__name__)
        with app.app_context():
            with mock.patch("app.services.comparison.grounding._call_gemini_single_car_raw", side_effect=fake_single_car_raw), \
                 mock.patch("app.services.comparison.grounding.get_request_id", return_value="test-123"), \
                 mock.patch("app.factory.AI_EXECUTOR", mock_executor):
                merged, sources_idx, errors = call_stage_a_parallel(cars, slots)

        assert "car_1" in merged["cars"]
        assert merged["cars"]["car_1"] is not None
        assert len(errors) > 0

    def test_all_stage_a_failures_return_clean_error(self):
        """All Stage A failures return errors list, not crash."""
        from app.services.comparison.grounding import call_stage_a_parallel
        from flask import Flask

        def fake_single_car_raw(prompt, car_label, timeout_sec=60, request_id=None, log=None):
            return {
                "parsed": None, "error": "CALL_TIMEOUT",
                "raw_text": None, "grounding_meta": {"grounding_successful": False, "source_count": 0},
                "finish_reason": None,
            }

        cars = [
            {"make": "Toyota", "model": "Corolla", "year": 2020, "display_name": "Toyota Corolla 2020"},
        ]
        slots = {"car_1": cars[0]}

        mock_executor = mock.MagicMock()
        mock_executor.submit.side_effect = lambda fn, *args, **kwargs: mock.MagicMock(
            result=lambda timeout: fn(*args, **kwargs)
        )

        app = Flask(__name__)
        with app.app_context():
            with mock.patch("app.services.comparison.grounding._call_gemini_single_car_raw", side_effect=fake_single_car_raw), \
                 mock.patch("app.services.comparison.grounding.get_request_id", return_value="test-123"), \
                 mock.patch("app.factory.AI_EXECUTOR", mock_executor):
                merged, sources_idx, errors = call_stage_a_parallel(cars, slots)

        assert len(errors) > 0
        assert merged["cars"]["car_1"] is not None  # empty payload placeholder


# ---------------------------------------------------------------------------
# 3. Advisor (advisor_ai_service)
# ---------------------------------------------------------------------------

class TestAdvisorJsonStability:
    """Test advisor grounding honesty and JSON handling."""

    def test_valid_json_still_works(self):
        from app.services import advisor_ai_service as svc

        valid_result = {
            "search_performed": True,
            "search_queries": ["test query"],
            "recommended_cars": [
                {"brand": "Toyota", "model": "Corolla", "year": 2020, "fuel": "gasoline",
                 "gear": "automatic", "turbo": False, "engine_cc": 1800,
                 "price_range_nis": [80000, 100000], "avg_fuel_consumption": 14,
                 "fuel_method": "official", "annual_fee": 2000, "fee_method": "official",
                 "reliability_score": 8, "reliability_method": "test",
                 "maintenance_cost": 3000, "maintenance_method": "test",
                 "safety_rating": 8, "safety_method": "test",
                 "insurance_cost": 4000, "insurance_method": "test",
                 "resale_value": 7, "resale_method": "test",
                 "performance_score": 6, "performance_method": "test",
                 "comfort_features": 7, "comfort_method": "test",
                 "suitability": 9, "suitability_method": "test",
                 "market_supply": "גבוה", "supply_method": "test",
                 "fit_score": 85, "comparison_comment": "מתאים", "not_recommended_reason": None}
            ],
        }
        resp = _mock_response(
            json.dumps(valid_result),
            grounding_successful=True, source_count=5,
            queries=["used cars israel"],
        )

        mock_client = mock.MagicMock()
        mock_client.models.generate_content.return_value = resp

        with mock.patch.object(svc.extensions, "advisor_client", mock_client), \
             mock.patch.object(svc, "AI_CALL_TIMEOUT_SEC", 30):
            result = svc.car_advisor_call_gemini_with_search({"budget_nis": [50000, 120000]})

        assert "_error" not in result
        assert result["search_performed"] is True
        assert "_grounding_meta" in result
        assert result["_grounding_meta"]["grounding_successful"] is True

    def test_json_decode_error_returns_clean_advisor_error(self):
        from app.services import advisor_ai_service as svc

        resp = _mock_response_no_grounding("This is not valid JSON at all")

        mock_client = mock.MagicMock()
        mock_client.models.generate_content.return_value = resp

        with mock.patch.object(svc.extensions, "advisor_client", mock_client), \
             mock.patch.object(svc, "AI_CALL_TIMEOUT_SEC", 30):
            result = svc.car_advisor_call_gemini_with_search({"budget_nis": [50000, 120000]})

        assert "_error" in result
        assert "JSON" in result["_error"]

    def test_grounding_metadata_extracted_when_present(self):
        from app.services import advisor_ai_service as svc

        valid_result = {"search_performed": True, "search_queries": [], "recommended_cars": []}
        resp = _mock_response(
            json.dumps(valid_result),
            grounding_successful=True, source_count=3,
            queries=["israeli car market"],
        )

        mock_client = mock.MagicMock()
        mock_client.models.generate_content.return_value = resp

        with mock.patch.object(svc.extensions, "advisor_client", mock_client), \
             mock.patch.object(svc, "AI_CALL_TIMEOUT_SEC", 30):
            result = svc.car_advisor_call_gemini_with_search({"budget_nis": [50000, 120000]})

        assert result["_grounding_meta"]["grounding_successful"] is True
        assert result["_grounding_meta"]["source_count"] == 3

    def test_model_claimed_search_not_treated_as_proof(self):
        """search_performed=true from the model without real grounding metadata
        should be logged as a warning, not treated as proof."""
        from app.services import advisor_ai_service as svc

        valid_result = {"search_performed": True, "search_queries": ["fake query"], "recommended_cars": []}
        resp = _mock_response_no_grounding(json.dumps(valid_result))

        mock_client = mock.MagicMock()
        mock_client.models.generate_content.return_value = resp

        with mock.patch.object(svc.extensions, "advisor_client", mock_client), \
             mock.patch.object(svc, "AI_CALL_TIMEOUT_SEC", 30):
            result = svc.car_advisor_call_gemini_with_search({"budget_nis": [50000, 120000]})

        assert result["_grounding_meta"]["grounding_successful"] is False
        assert result["search_performed"] is True  # model claim preserved but not trusted

    def test_postprocess_passes_grounding_metadata(self):
        from app.services.advisor_ai_service import car_advisor_postprocess

        parsed = {
            "search_performed": True,
            "search_queries": [],
            "recommended_cars": [],
            "_grounding_meta": {"grounding_successful": True, "source_count": 5, "search_queries": []},
        }
        result = car_advisor_postprocess({"annual_km": 15000}, parsed)
        assert "_grounding_meta" in result
        assert result["_grounding_meta"]["grounding_successful"] is True

    def test_postprocess_no_grounding_marks_unverified(self):
        from app.services.advisor_ai_service import car_advisor_postprocess

        parsed = {
            "search_performed": True,
            "search_queries": [],
            "recommended_cars": [],
            "_grounding_meta": {"grounding_successful": False, "source_count": 0, "search_queries": []},
        }
        result = car_advisor_postprocess({"annual_km": 15000}, parsed)
        assert result.get("grounding_confidence") == "unverified"
