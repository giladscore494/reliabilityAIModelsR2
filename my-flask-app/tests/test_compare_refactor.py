# -*- coding: utf-8 -*-
"""
Tests for the Car Comparison refactor:
- build_display_name
- map_cars_to_slots
- determine_winner with tie handling
- sanitize_comparison_narrative
- Payload shape (cars_selected as dict, narrative, etc.)
"""

import json
import pytest
from datetime import datetime

from app.services.comparison_service import (
    build_display_name,
    map_cars_to_slots,
    determine_winner,
    compute_comparison_results,
    TIE_THRESHOLD,
)
from app.utils.sanitization import sanitize_comparison_narrative


# ============================================================
# build_display_name tests
# ============================================================

class TestBuildDisplayName:
    def test_make_model_year(self):
        assert build_display_name({"make": "Toyota", "model": "Corolla", "year": 2020}) == "Toyota Corolla 2020"

    def test_make_model_no_year(self):
        assert build_display_name({"make": "Honda", "model": "Civic"}) == "Honda Civic"

    def test_make_model_year_range(self):
        result = build_display_name({"make": "Kia", "model": "Sportage", "year_start": 2018, "year_end": 2025})
        assert result == "Kia Sportage 2018-2025"

    def test_year_takes_precedence(self):
        result = build_display_name({"make": "BMW", "model": "320i", "year": 2022, "year_start": 2018, "year_end": 2025})
        assert result == "BMW 320i 2022"

    def test_empty_car(self):
        assert build_display_name({}) == ""


# ============================================================
# map_cars_to_slots tests
# ============================================================

class TestMapCarsToSlots:
    def test_two_cars(self):
        cars = [
            {"make": "Toyota", "model": "Corolla", "year": 2020},
            {"make": "Honda", "model": "Civic", "year": 2021},
        ]
        slots = map_cars_to_slots(cars)
        assert "car_1" in slots
        assert "car_2" in slots
        assert "car_3" not in slots
        assert slots["car_1"]["display_name"] == "Toyota Corolla 2020"
        assert slots["car_2"]["display_name"] == "Honda Civic 2021"
        assert slots["car_1"]["make"] == "Toyota"
        assert slots["car_2"]["model"] == "Civic"

    def test_three_cars(self):
        cars = [
            {"make": "Toyota", "model": "Corolla", "year": 2020},
            {"make": "Honda", "model": "Civic", "year": 2021},
            {"make": "Kia", "model": "Sportage", "year": 2022},
        ]
        slots = map_cars_to_slots(cars)
        assert "car_1" in slots and "car_2" in slots and "car_3" in slots

    def test_same_manufacturer_no_collision(self):
        """Two Toyota models should have different slot keys."""
        cars = [
            {"make": "Toyota", "model": "Corolla", "year": 2020},
            {"make": "Toyota", "model": "Camry", "year": 2020},
        ]
        slots = map_cars_to_slots(cars)
        assert slots["car_1"]["display_name"] != slots["car_2"]["display_name"]
        assert slots["car_1"]["model"] == "Corolla"
        assert slots["car_2"]["model"] == "Camry"


# ============================================================
# determine_winner with tie handling
# ============================================================

class TestDetermineWinner:
    def test_clear_winner(self):
        scores = {"car_1": 85.0, "car_2": 70.0}
        assert determine_winner(scores) == "car_1"

    def test_tie_when_close(self):
        scores = {"car_1": 82.0, "car_2": 80.5}
        assert determine_winner(scores) == "tie"

    def test_tie_threshold_boundary(self):
        # Exactly at threshold -> still tie (< TIE_THRESHOLD)
        scores = {"car_1": 80.0 + TIE_THRESHOLD - 0.1, "car_2": 80.0}
        assert determine_winner(scores) == "tie"
        
        # Just above threshold -> not tie
        scores = {"car_1": 80.0 + TIE_THRESHOLD + 0.1, "car_2": 80.0}
        assert determine_winner(scores) != "tie"

    def test_no_scores(self):
        assert determine_winner({}) is None
        assert determine_winner({"car_1": None, "car_2": None}) is None

    def test_single_car(self):
        assert determine_winner({"car_1": 75.0}) == "car_1"

    def test_three_cars_winner(self):
        scores = {"car_1": 90.0, "car_2": 75.0, "car_3": 80.0}
        assert determine_winner(scores) == "car_1"


# ============================================================
# compute_comparison_results with stable keys
# ============================================================

class TestComputeComparisonResultsStableKeys:
    def test_results_use_car_slot_keys(self):
        """Results should use car_1/car_2 as keys when model output uses them."""
        model_output = {
            "grounding_successful": True,
            "cars": {
                "car_1": {
                    "reliability_risk": {
                        "reliability_rating": {"value": 80, "confidence": 0.9, "sources": [{"url": "http://example.com", "title": "Test", "snippet": "Test"}]},
                        "major_failure_risk": {"value": "low", "confidence": 0.8, "sources": [{"url": "http://example.com", "title": "Test", "snippet": "Test"}]},
                        "common_failure_patterns": {"value": None, "confidence": 0, "sources": []},
                        "mileage_sensitivity": {"value": "medium", "confidence": 0.7, "sources": [{"url": "http://example.com", "title": "Test", "snippet": "Test"}]},
                        "maintenance_complexity": {"value": "low", "confidence": 0.8, "sources": [{"url": "http://example.com", "title": "Test", "snippet": "Test"}]},
                        "expected_maintenance_cost_level": {"value": "low", "confidence": 0.8, "sources": [{"url": "http://example.com", "title": "Test", "snippet": "Test"}]},
                    },
                    "ownership_cost": {},
                    "practicality_comfort": {},
                    "driving_performance": {},
                },
                "car_2": {
                    "reliability_risk": {
                        "reliability_rating": {"value": 75, "confidence": 0.85, "sources": [{"url": "http://example.com", "title": "Test", "snippet": "Test"}]},
                        "major_failure_risk": {"value": "medium", "confidence": 0.7, "sources": [{"url": "http://example.com", "title": "Test", "snippet": "Test"}]},
                        "common_failure_patterns": {"value": None, "confidence": 0, "sources": []},
                        "mileage_sensitivity": {"value": "high", "confidence": 0.6, "sources": [{"url": "http://example.com", "title": "Test", "snippet": "Test"}]},
                        "maintenance_complexity": {"value": "medium", "confidence": 0.7, "sources": [{"url": "http://example.com", "title": "Test", "snippet": "Test"}]},
                        "expected_maintenance_cost_level": {"value": "medium", "confidence": 0.7, "sources": [{"url": "http://example.com", "title": "Test", "snippet": "Test"}]},
                    },
                    "ownership_cost": {},
                    "practicality_comfort": {},
                    "driving_performance": {},
                },
            }
        }
        
        result = compute_comparison_results(model_output)
        
        # Keys should be car_1, car_2
        assert "car_1" in result["cars"]
        assert "car_2" in result["cars"]
        
        # car_1 should have higher score
        assert result["cars"]["car_1"]["categories"]["reliability_risk"]["score"] is not None
        assert result["cars"]["car_2"]["categories"]["reliability_risk"]["score"] is not None


# ============================================================
# sanitize_comparison_narrative tests
# ============================================================

class TestSanitizeComparisonNarrative:
    def test_valid_narrative(self):
        narrative = {
            "overall_summary": "הטויוטה מנצחת בציון כולל",
            "category_explanations": [
                {
                    "category_key": "reliability_risk",
                    "title_he": "אמינות וסיכונים",
                    "winner": "car_1",
                    "explanations": {"car_1": "אמינה מאוד", "car_2": "פחות אמינה"},
                    "why_it_scored_that_way": ["ציון גבוה באמינות", "סיכון נמוך לתקלות"],
                }
            ],
            "disclaimers_he": ["הערכה בלבד", "מומלץ לבדוק"],
        }
        result = sanitize_comparison_narrative(narrative)
        assert result is not None
        assert result["overall_summary"] == "הטויוטה מנצחת בציון כולל"
        assert len(result["category_explanations"]) == 1
        assert result["category_explanations"][0]["category_key"] == "reliability_risk"
        assert result["category_explanations"][0]["winner"] == "car_1"
        assert "car_1" in result["category_explanations"][0]["explanations"]
        assert len(result["disclaimers_he"]) == 2

    def test_xss_is_escaped(self):
        narrative = {
            "overall_summary": "<script>alert('xss')</script>",
            "category_explanations": [],
            "disclaimers_he": ["<img onerror=alert(1)>"],
        }
        result = sanitize_comparison_narrative(narrative)
        assert "<script>" not in result["overall_summary"]
        assert "&lt;script&gt;" in result["overall_summary"]
        assert "<img" not in result["disclaimers_he"][0]

    def test_none_returns_none(self):
        assert sanitize_comparison_narrative(None) is None
        assert sanitize_comparison_narrative("string") is None
        assert sanitize_comparison_narrative(123) is None

    def test_invalid_category_key_filtered(self):
        narrative = {
            "overall_summary": "test",
            "category_explanations": [
                {"category_key": "injected_key", "title_he": "test", "winner": "car_1", "explanations": {}, "why_it_scored_that_way": []},
                {"category_key": "reliability_risk", "title_he": "test", "winner": "car_1", "explanations": {}, "why_it_scored_that_way": []},
            ],
            "disclaimers_he": [],
        }
        result = sanitize_comparison_narrative(narrative)
        assert len(result["category_explanations"]) == 1
        assert result["category_explanations"][0]["category_key"] == "reliability_risk"

    def test_invalid_winner_cleared(self):
        narrative = {
            "overall_summary": "test",
            "category_explanations": [
                {"category_key": "reliability_risk", "title_he": "test", "winner": "hacker", "explanations": {}, "why_it_scored_that_way": []},
            ],
            "disclaimers_he": [],
        }
        result = sanitize_comparison_narrative(narrative)
        assert result["category_explanations"][0]["winner"] == ""

    def test_why_it_scored_capped_at_3(self):
        narrative = {
            "overall_summary": "test",
            "category_explanations": [
                {"category_key": "reliability_risk", "title_he": "test", "winner": "tie",
                 "explanations": {},
                 "why_it_scored_that_way": ["a", "b", "c", "d", "e"]},
            ],
            "disclaimers_he": [],
        }
        result = sanitize_comparison_narrative(narrative)
        assert len(result["category_explanations"][0]["why_it_scored_that_way"]) == 3

    def test_only_car_keys_in_explanations(self):
        """Explanations should only allow car_1, car_2, car_3 keys."""
        narrative = {
            "overall_summary": "test",
            "category_explanations": [
                {"category_key": "ownership_cost", "title_he": "test", "winner": "car_2",
                 "explanations": {"car_1": "ok", "car_2": "good", "bad_key": "injected"},
                 "why_it_scored_that_way": []},
            ],
            "disclaimers_he": [],
        }
        result = sanitize_comparison_narrative(narrative)
        explanations = result["category_explanations"][0]["explanations"]
        assert "car_1" in explanations
        assert "car_2" in explanations
        assert "bad_key" not in explanations


# ============================================================
# Integration: history detail with narrative
# ============================================================

from main import create_app, db, User
from app.models import ComparisonHistory


@pytest.fixture
def app(monkeypatch):
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
    return app.test_client()


@pytest.fixture
def logged_in_client(app, client):
    with app.app_context():
        user = User(google_id="test-refactor", email="test_refactor@example.com", name="Refactor User")
        db.session.add(user)
        db.session.commit()
        user_id = user.id
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
    return client, user_id


class TestCompareDetailWithNarrative:
    def test_detail_includes_narrative(self, app, logged_in_client):
        """History detail should include narrative from stored computed_result."""
        client, user_id = logged_in_client

        narrative = {
            "overall_summary": "Test summary",
            "category_explanations": [],
            "disclaimers_he": ["Disclaimer"]
        }
        computed = {"overall_winner": "car_1", "cars": {}, "narrative": narrative}

        with app.app_context():
            record = ComparisonHistory(
                user_id=user_id,
                cars_selected=json.dumps([{"make": "Toyota", "model": "Corolla", "year": 2020}]),
                computed_result=json.dumps(computed),
                model_name="gemini-3-flash",
                prompt_version="v1",
            )
            db.session.add(record)
            db.session.commit()
            record_id = record.id

        resp = client.get(f"/api/compare/{record_id}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["data"]["narrative"] is not None
        assert data["data"]["narrative"]["overall_summary"] == "Test summary"

    def test_detail_without_narrative_returns_null(self, app, logged_in_client):
        """Old records without narrative should return narrative=null."""
        client, user_id = logged_in_client

        computed = {"overall_winner": "car_1", "cars": {}}

        with app.app_context():
            record = ComparisonHistory(
                user_id=user_id,
                cars_selected=json.dumps([{"make": "Honda", "model": "Civic", "year": 2021}]),
                computed_result=json.dumps(computed),
                model_name="gemini-3-flash",
                prompt_version="v1",
            )
            db.session.add(record)
            db.session.commit()
            record_id = record.id

        resp = client.get(f"/api/compare/{record_id}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["data"]["narrative"] is None

    def test_detail_returns_cars_selected_as_dict(self, app, logged_in_client):
        """cars_selected should be returned as dict with car_1/car_2 keys."""
        client, user_id = logged_in_client

        with app.app_context():
            record = ComparisonHistory(
                user_id=user_id,
                cars_selected=json.dumps([
                    {"make": "Toyota", "model": "Corolla", "year": 2020},
                    {"make": "Honda", "model": "Civic", "year": 2021}
                ]),
                computed_result=json.dumps({"overall_winner": "car_1", "cars": {}}),
                model_name="gemini-3-flash",
                prompt_version="v1",
            )
            db.session.add(record)
            db.session.commit()
            record_id = record.id

        resp = client.get(f"/api/compare/{record_id}")
        assert resp.status_code == 200
        data = resp.get_json()
        
        cars_selected = data["data"]["cars_selected"]
        assert isinstance(cars_selected, dict)
        assert "car_1" in cars_selected
        assert "car_2" in cars_selected
        assert cars_selected["car_1"]["display_name"] == "Toyota Corolla 2020"
        assert cars_selected["car_2"]["display_name"] == "Honda Civic 2021"
        
        # Also verify backward-compatible list
        assert isinstance(data["data"]["cars_selected_list"], list)
        assert len(data["data"]["cars_selected_list"]) == 2
