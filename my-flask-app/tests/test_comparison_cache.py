# -*- coding: utf-8 -*-
"""
Tests for comparison cache parsing and timing estimate fixes.
"""

import json
import pytest
from datetime import datetime

from main import db, User
from app.models import ComparisonHistory
from app.services.comparison_service import _safe_parse_json_cached


class TestSafeParseJsonCached:
    """Tests for the _safe_parse_json_cached helper function."""
    
    def test_normal_json_dict(self):
        """Normal JSON dict string parses correctly."""
        raw = '{"a": 1, "b": "hello"}'
        result, was_double = _safe_parse_json_cached(raw, "test")
        assert result == {"a": 1, "b": "hello"}
        assert was_double is False
    
    def test_normal_json_list(self):
        """Normal JSON list string parses correctly."""
        raw = '[1, 2, 3]'
        result, was_double = _safe_parse_json_cached(raw, "test")
        assert result == [1, 2, 3]
        assert was_double is False
    
    def test_double_encoded_dict(self):
        """Double-encoded JSON dict is unwrapped correctly."""
        inner = {"a": 1, "assumptions": {"year": 2020}}
        double_encoded = json.dumps(json.dumps(inner))
        result, was_double = _safe_parse_json_cached(double_encoded, "test")
        assert result == inner
        assert was_double is True
    
    def test_double_encoded_list(self):
        """Double-encoded JSON list is unwrapped correctly."""
        inner = [{"make": "Toyota", "model": "Camry"}]
        double_encoded = json.dumps(json.dumps(inner))
        result, was_double = _safe_parse_json_cached(double_encoded, "test")
        assert result == inner
        assert was_double is True
    
    def test_none_input(self):
        """None input returns (None, False)."""
        result, was_double = _safe_parse_json_cached(None, "test")
        assert result is None
        assert was_double is False
    
    def test_invalid_json(self):
        """Invalid JSON returns (None, False) without throwing."""
        result, was_double = _safe_parse_json_cached("not json at all", "test")
        assert result is None
        assert was_double is False
    
    def test_string_that_is_not_json_inside(self):
        """A JSON string that parses to a non-JSON string."""
        raw = '"just a plain string"'
        result, was_double = _safe_parse_json_cached(raw, "test")
        assert result == "just a plain string"
        assert was_double is False
    
    def test_already_parsed_dict(self):
        """Already parsed dict (e.g., from JSONB) is returned as-is."""
        value = {"already": "parsed"}
        result, was_double = _safe_parse_json_cached(value, "test")
        assert result == {"already": "parsed"}
        assert was_double is False
    
    def test_assumptions_extraction_from_double_encoded(self):
        """Ensure assumptions can be extracted after unwrapping double-encoded JSON."""
        model_output = {
            "cars": [{"make": "Toyota"}],
            "assumptions": {"engine_type": "hybrid", "year": 2021}
        }
        double_encoded = json.dumps(json.dumps(model_output))
        result, was_double = _safe_parse_json_cached(double_encoded, "model_json_raw")
        
        assert was_double is True
        assert isinstance(result, dict)
        # This is the key fix: result.get("assumptions") must work without AttributeError
        assumptions = result.get("assumptions", {})
        assert assumptions == {"engine_type": "hybrid", "year": 2021}


class TestComparisonCacheHit:
    """Integration tests for comparison cache hit with corrupted data."""
    
    def test_double_encoded_cache_returns_200(self, app, logged_in_client, monkeypatch):
        """
        A cache row with double-encoded JSON should return 200, not 500.
        This tests the fix for the AttributeError: 'str' object has no attribute 'get'.
        """
        client, user_id = logged_in_client
        
        # Accept legal terms first
        client.post("/api/legal/accept", json={"legal_confirm": True})
        
        # Create a cached comparison row with DOUBLE-ENCODED JSON
        cars_selected = [
            {"make": "Toyota", "model": "Camry", "year": 2020},
            {"make": "Honda", "model": "Accord", "year": 2020}
        ]
        model_output = {
            "cars": [
                {"make": "Toyota", "model": "Camry"},
                {"make": "Honda", "model": "Accord"}
            ],
            "assumptions": {"engine_type": "gasoline", "year": 2020}
        }
        computed_result = {
            "overall_winner": "Toyota Camry",
            "category_winners": {},
            "scores": {}
        }
        sources_index = {"car_0": [], "car_1": []}
        
        # Double-encode the JSON (simulating the bug)
        double_encoded_model = json.dumps(json.dumps(model_output))
        double_encoded_computed = json.dumps(json.dumps(computed_result))
        double_encoded_cars = json.dumps(json.dumps(cars_selected))
        double_encoded_sources = json.dumps(json.dumps(sources_index))
        
        # Compute the same hash that the comparison service would
        from app.services.comparison_service import compute_request_hash
        request_hash = compute_request_hash(cars_selected)
        
        with app.app_context():
            # Insert the corrupted cache row directly
            corrupted_row = ComparisonHistory(
                created_at=datetime.utcnow(),
                user_id=user_id,
                session_id="test-session",
                cars_selected=double_encoded_cars,
                model_json_raw=double_encoded_model,
                computed_result=double_encoded_computed,
                sources_index=double_encoded_sources,
                model_name="test-model",
                grounding_enabled=True,
                prompt_version="v1",
                request_hash=request_hash,
                duration_ms=1000,
            )
            db.session.add(corrupted_row)
            db.session.commit()
            row_id = corrupted_row.id
        
        # Now call the compare API with the same cars - should hit cache
        resp = client.post(
            "/api/compare",
            json={"cars": cars_selected, "legal_confirm": True},
            headers={"Content-Type": "application/json", "Origin": "http://localhost"}
        )
        
        # Should return 200, not 500
        assert resp.status_code == 200, f"Expected 200 but got {resp.status_code}: {resp.get_json()}"
        
        data = resp.get_json()
        assert data["ok"] is True
        assert data["data"]["cached"] is True
        
        # Verify assumptions is a dict, not causing AttributeError
        assert isinstance(data["data"].get("assumptions", {}), dict)


class TestTimingEstimateCompare:
    """Tests for /api/timing/estimate with kind=compare."""
    
    def test_timing_estimate_compare_returns_200(self, app, logged_in_client):
        """
        GET /api/timing/estimate?kind=compare should return 200.
        This tests the fix for: type object 'ComparisonHistory' has no attribute 'timestamp'
        """
        client, user_id = logged_in_client
        
        # Create a ComparisonHistory row with duration_ms to ensure stats can be computed
        with app.app_context():
            comparison = ComparisonHistory(
                created_at=datetime.utcnow(),
                user_id=user_id,
                session_id="test-session",
                cars_selected=json.dumps([{"make": "Honda", "model": "Civic"}]),
                model_json_raw=json.dumps({"cars": []}),
                computed_result=json.dumps({"overall_winner": "Honda Civic"}),
                sources_index=json.dumps({}),
                model_name="test-model",
                grounding_enabled=True,
                prompt_version="v1",
                request_hash="test-hash-123",
                duration_ms=5000,
            )
            db.session.add(comparison)
            db.session.commit()
        
        # Call timing estimate for compare
        resp = client.get("/api/timing/estimate?kind=compare")
        
        # Should return 200, not fail with AttributeError
        assert resp.status_code == 200, f"Expected 200 but got {resp.status_code}: {resp.get_json()}"
        
        data = resp.get_json()
        assert data["ok"] is True
        assert "estimate_ms" in data["data"]
        assert data["data"]["kind"] == "compare"
    
    def test_timing_estimate_compare_with_no_history(self, app, logged_in_client):
        """
        GET /api/timing/estimate?kind=compare with no history should return default estimate.
        """
        client, _ = logged_in_client
        
        # Ensure no comparison history exists
        with app.app_context():
            ComparisonHistory.query.delete()
            db.session.commit()
        
        resp = client.get("/api/timing/estimate?kind=compare")
        
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["data"]["source"] == "default"
        # Default estimate for compare is 70000ms
        assert data["data"]["estimate_ms"] == 70000
