# -*- coding: utf-8 -*-
"""
Tests for /api/compare/history endpoint and _safe_json_obj helper.

Regression tests to prevent recurrence of the 500 error when
computed_result/cars_selected contain double-encoded JSON strings.
"""

import json
import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock

from main import create_app, db, User
from app.models import ComparisonHistory
from app.services.comparison_service import _safe_json_obj


class TestSafeJsonObj:
    """Unit tests for _safe_json_obj helper function."""

    def test_none_returns_default(self):
        assert _safe_json_obj(None, default={}) == {}
        assert _safe_json_obj(None, default=[]) == []
        assert _safe_json_obj(None, default="fallback") == "fallback"

    def test_already_dict_returns_dict(self):
        d = {"key": "value"}
        assert _safe_json_obj(d, default={}) == {"key": "value"}

    def test_already_list_returns_list(self):
        lst = [1, 2, 3]
        assert _safe_json_obj(lst, default=[]) == [1, 2, 3]

    def test_valid_json_string_dict(self):
        s = '{"overall_winner": "car_a"}'
        result = _safe_json_obj(s, default={})
        assert result == {"overall_winner": "car_a"}

    def test_valid_json_string_list(self):
        s = '[{"make": "Toyota", "model": "Corolla"}]'
        result = _safe_json_obj(s, default=[])
        assert result == [{"make": "Toyota", "model": "Corolla"}]

    def test_double_encoded_dict(self):
        """Test the exact scenario causing the 500 error in production."""
        # Double-encoded: JSON string containing another JSON string
        inner = json.dumps({"overall_winner": "car_a"})
        double_encoded = json.dumps(inner)  # '"{\"overall_winner\": \"car_a\"}"'
        
        result = _safe_json_obj(double_encoded, default={})
        assert result == {"overall_winner": "car_a"}

    def test_double_encoded_list(self):
        """Test double-encoded array scenario."""
        inner = json.dumps([{"make": "Toyota", "model": "Corolla"}])
        double_encoded = json.dumps(inner)
        
        result = _safe_json_obj(double_encoded, default=[])
        assert result == [{"make": "Toyota", "model": "Corolla"}]

    def test_empty_string_returns_default(self):
        assert _safe_json_obj("", default={}) == {}
        assert _safe_json_obj("   ", default=[]) == []

    def test_invalid_json_returns_default(self):
        assert _safe_json_obj("not json at all", default={}) == {}
        assert _safe_json_obj("{broken", default=[]) == []

    def test_triple_encoded_returns_default(self):
        """Triple encoding should return default (only handle up to double)."""
        inner = json.dumps({"key": "value"})
        double = json.dumps(inner)
        triple = json.dumps(double)
        
        # After two decodes, we'd still have a string, so return default
        assert _safe_json_obj(triple, default={}) == {}

    def test_json_primitive_int_returns_default(self):
        """JSON primitives (not dict/list) should return default."""
        assert _safe_json_obj("123", default={}) == {}
        assert _safe_json_obj("true", default=[]) == []
        assert _safe_json_obj('"just a string"', default={}) == {}

    def test_unexpected_type_returns_default(self):
        """Unexpected input types should return default."""
        assert _safe_json_obj(12345, default={}) == {}
        assert _safe_json_obj(True, default=[]) == []


@pytest.fixture
def app(monkeypatch):
    """Create application for testing."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.delenv("SKIP_CREATE_ALL", raising=False)
    app = create_app()
    app.config.update(TESTING=True)
    with app.app_context():
        db.create_all()
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    """Create test client."""
    return app.test_client()


@pytest.fixture
def logged_in_client(app, client):
    """Create logged-in test client with a user."""
    with app.app_context():
        user = User(google_id="test-compare-history", email="test_compare@example.com", name="Test User")
        db.session.add(user)
        db.session.commit()
        user_id = user.id

    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True

    return client, user_id


class TestCompareHistoryAPI:
    """Integration tests for /api/compare/history endpoint."""

    def test_history_returns_200_with_empty_list(self, logged_in_client):
        """No comparison history returns 200 with empty array."""
        client, _ = logged_in_client
        resp = client.get("/api/compare/history?limit=20")
        
        assert resp.status_code == 200
        assert resp.content_type == "application/json"
        data = resp.get_json()
        assert data["ok"] is True
        assert data["data"]["history"] == []

    def test_history_with_normal_records(self, app, logged_in_client):
        """Normal (properly encoded) records are returned correctly."""
        client, user_id = logged_in_client
        
        with app.app_context():
            record = ComparisonHistory(
                user_id=user_id,
                cars_selected=json.dumps([{"make": "Toyota", "model": "Corolla"}]),
                computed_result=json.dumps({"overall_winner": "Toyota Corolla"}),
                model_name="gemini-3-flash",
                prompt_version="v1",
            )
            db.session.add(record)
            db.session.commit()
        
        resp = client.get("/api/compare/history?limit=20")
        
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert len(data["data"]["history"]) == 1
        assert data["data"]["history"][0]["overall_winner"] == "Toyota Corolla"
        assert data["data"]["history"][0]["cars"] == [{"make": "Toyota", "model": "Corolla"}]

    def test_history_with_double_encoded_records(self, app, logged_in_client):
        """Double-encoded JSON records are handled correctly (regression test)."""
        client, user_id = logged_in_client
        
        # This is the exact scenario causing the 500 error in production
        inner_computed = json.dumps({"overall_winner": "car_a"})
        double_encoded_computed = json.dumps(inner_computed)
        
        inner_cars = json.dumps([{"make": "Toyota", "model": "Corolla"}])
        double_encoded_cars = json.dumps(inner_cars)
        
        with app.app_context():
            record = ComparisonHistory(
                user_id=user_id,
                cars_selected=double_encoded_cars,
                computed_result=double_encoded_computed,
                model_name="gemini-3-flash",
                prompt_version="v1",
            )
            db.session.add(record)
            db.session.commit()
        
        resp = client.get("/api/compare/history?limit=20")
        
        # Should NOT crash with 500
        assert resp.status_code == 200
        assert resp.content_type == "application/json"
        data = resp.get_json()
        assert data["ok"] is True
        assert len(data["data"]["history"]) == 1
        # Should correctly decode double-encoded values
        assert data["data"]["history"][0]["overall_winner"] == "car_a"
        assert data["data"]["history"][0]["cars"] == [{"make": "Toyota", "model": "Corolla"}]

    def test_history_with_mixed_records(self, app, logged_in_client):
        """Mix of normal and corrupted records - should not crash."""
        client, user_id = logged_in_client
        
        with app.app_context():
            # Normal record
            normal = ComparisonHistory(
                user_id=user_id,
                cars_selected=json.dumps([{"make": "Honda", "model": "Civic"}]),
                computed_result=json.dumps({"overall_winner": "Honda Civic"}),
                model_name="gemini-3-flash",
                prompt_version="v1",
            )
            db.session.add(normal)
            
            # Double-encoded record
            inner_computed = json.dumps({"overall_winner": "Toyota Corolla"})
            double = ComparisonHistory(
                user_id=user_id,
                cars_selected=json.dumps([{"make": "Toyota", "model": "Corolla"}]),
                computed_result=json.dumps(inner_computed),  # double-encoded
                model_name="gemini-3-flash",
                prompt_version="v1",
            )
            db.session.add(double)
            db.session.commit()
        
        resp = client.get("/api/compare/history?limit=20")
        
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        # Both records should be returned
        assert len(data["data"]["history"]) == 2

    def test_history_with_null_computed_result(self, app, logged_in_client):
        """Records with null computed_result should not crash."""
        client, user_id = logged_in_client
        
        with app.app_context():
            record = ComparisonHistory(
                user_id=user_id,
                cars_selected=json.dumps([{"make": "Toyota", "model": "Corolla"}]),
                computed_result=None,  # null
                model_name="gemini-3-flash",
                prompt_version="v1",
            )
            db.session.add(record)
            db.session.commit()
        
        resp = client.get("/api/compare/history?limit=20")
        
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert len(data["data"]["history"]) == 1
        assert data["data"]["history"][0]["overall_winner"] is None

    def test_history_returns_json_on_auth_required(self, client):
        """Unauthenticated requests should return proper response (not HTML error)."""
        resp = client.get("/api/compare/history")
        # Should be a redirect to login or 401, but not 500
        assert resp.status_code != 500


class TestCompareDetailAPI:
    """Integration tests for /api/compare/<id> endpoint."""

    def test_detail_with_double_encoded_records(self, app, logged_in_client):
        """Double-encoded JSON in detail endpoint is handled correctly."""
        client, user_id = logged_in_client
        
        inner_computed = json.dumps({"overall_winner": "car_a", "scores": {"car_a": 85}})
        double_encoded_computed = json.dumps(inner_computed)
        
        inner_cars = json.dumps([{"make": "Toyota", "model": "Corolla"}])
        double_encoded_cars = json.dumps(inner_cars)
        
        with app.app_context():
            record = ComparisonHistory(
                user_id=user_id,
                cars_selected=double_encoded_cars,
                computed_result=double_encoded_computed,
                model_json_raw=json.dumps({"raw": "data", "assumptions": {"note": "test"}}),
                sources_index=json.dumps({"source1": "url1"}),
                model_name="gemini-3-flash",
                prompt_version="v1",
            )
            db.session.add(record)
            db.session.commit()
            record_id = record.id
        
        resp = client.get(f"/api/compare/{record_id}")
        
        assert resp.status_code == 200
        assert resp.content_type == "application/json"
        data = resp.get_json()
        assert data["ok"] is True
        # Should correctly decode double-encoded values
        assert data["data"]["computed_result"]["overall_winner"] == "car_a"
        assert data["data"]["cars_selected"] == [{"make": "Toyota", "model": "Corolla"}]

    def test_detail_not_found(self, logged_in_client):
        """Non-existent comparison returns 404 JSON response."""
        client, _ = logged_in_client
        resp = client.get("/api/compare/99999")
        
        assert resp.status_code == 404
        assert resp.content_type == "application/json"
        data = resp.get_json()
        assert data["ok"] is False
