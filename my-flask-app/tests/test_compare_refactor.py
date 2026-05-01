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

from app.models import ComparisonHistory
from app.services.comparison_service import (
    build_display_name,
    map_cars_to_slots,
    determine_winner,
    compute_comparison_results,
    parse_single_car_json,
    TIE_THRESHOLD,
    build_compare_writer_prompt,
    build_single_car_prompt,
    convert_writer_response_to_narrative,
    infer_compare_segment,
    validate_compare_writer_response,
    COMPARE_WRITER_PROMPT_CHAR_CAP,
)
from app.utils.sanitization import sanitize_comparison_narrative
from main import create_app, db, User


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
                        "power_to_weight": None,
                        "braking_confidence": "medium",
                        "handling_agility": "medium",
                        "fun_to_drive": "medium",
                    },
                    "facts": {"horsepower": 138, "weight_kg": 1310, "body_type": "sedan", "fuel_type": "petrol"},
                    "short_notes": ["אמינות חזקה"],
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
                        "power_feel": "high",
                        "power_to_weight": None,
                        "braking_confidence": "medium",
                        "handling_agility": "high",
                        "fun_to_drive": "high",
                    },
                    "facts": {"horsepower": 210, "weight_kg": 1325, "body_type": "sedan", "fuel_type": "petrol"},
                    "short_notes": ["מהנה יותר"],
                    "sources": ["https://example.com/honda"],
                },
            },
            "sources": ["https://example.com/toyota", "https://example.com/honda"],
        }
        
        result = compute_comparison_results(model_output)
        
        # Keys should be car_1, car_2
        assert "car_1" in result["cars"]
        assert "car_2" in result["cars"]
        
        # car_1 should have higher score
        assert result["cars"]["car_1"]["categories"]["reliability_risk"]["score"] is not None
        assert result["cars"]["car_2"]["categories"]["reliability_risk"]["score"] is not None
        assert result["comparison_status"]["balanced"] is True
        assert result["metric_winners"]["driving_performance"]["power_capability"] == "car_2"

    def test_results_handle_tie_without_crashing(self):
        model_output = {
            "cars": {
                "car_1": {
                    "car_name": "Toyota Corolla 2020",
                    "reliability": {"overall": "high", "issue_frequency": "low", "issue_severity": "low", "repair_cost_risk": "low", "recall_risk": "low", "parts_complexity": "low"},
                    "ownership_cost": {"fuel_cost": "low", "routine_maintenance": "low", "repair_burden": "low", "insurance_burden": "low", "depreciation_risk": "low"},
                    "comfort_practicality": {"space": "medium", "ride_comfort": "medium", "trunk_usefulness": "medium", "daily_usability": "high"},
                    "performance_driving": {"power_feel": "medium", "power_to_weight": None, "braking_confidence": "medium", "handling_agility": "medium", "fun_to_drive": "medium"},
                },
                "car_2": {
                    "car_name": "Honda Civic 2020",
                    "reliability": {"overall": "high", "issue_frequency": "low", "issue_severity": "low", "repair_cost_risk": "low", "recall_risk": "low", "parts_complexity": "low"},
                    "ownership_cost": {"fuel_cost": "low", "routine_maintenance": "low", "repair_burden": "low", "insurance_burden": "low", "depreciation_risk": "low"},
                    "comfort_practicality": {"space": "medium", "ride_comfort": "medium", "trunk_usefulness": "medium", "daily_usability": "high"},
                    "performance_driving": {"power_feel": "medium", "power_to_weight": None, "braking_confidence": "medium", "handling_agility": "medium", "fun_to_drive": "medium"},
                },
            }
        }

        result = compute_comparison_results(model_output)

        assert result["overall_winner"] == "tie"
        assert result["overall_winner_message"]
        assert isinstance(result["top_reasons"], list)
        assert result["top_reasons"]

    def test_results_handle_missing_winner_softly(self):
        result = compute_comparison_results({"cars": {"car_1": {}, "car_2": {}}})

        assert result["overall_winner"] is None
        assert result["overall_winner_message"]
        assert isinstance(result["top_reasons"], list)
        assert result["top_reasons"]

    def test_parse_single_car_json_normalizes_compact_stage_a_payload(self):
        raw = """
        {
          "car_name": "Seat Ibiza 2011",
          "reliability": {"overall": "medium", "issue_frequency": "low"},
          "ownership_cost": {"fuel_cost": "low"},
          "comfort_practicality": {"space": "medium"},
          "performance_driving": {"power_feel": "medium"},
          "facts": {"horsepower": "105", "weight_kg": "1100", "body_type": "hatchback", "fuel_type": "petrol"},
          "short_notes": ["note 1", "note 2", "note 3", "note 4", "note 5"],
          "sources": [
            "https://example.com/1",
            {"url": "https://example.com/2"},
            "javascript:alert(1)"
          ]
        }
        """
        parsed, err = parse_single_car_json(raw)
        assert err is None
        assert parsed["car_name"] == "Seat Ibiza 2011"
        assert parsed["reliability"]["issue_frequency"] == "low"
        assert parsed["ownership_cost"]["repair_burden"] is None
        assert parsed["facts"]["horsepower"] == 105.0
        assert parsed["short_notes"] == ["note 1", "note 2", "note 3", "note 4"]
        assert parsed["sources"] == ["https://example.com/1", "https://example.com/2"]

    def test_results_mark_unbalanced_when_one_car_has_no_stage_a_evidence(self):
        model_output = {
            "cars": {
                "car_1": {
                    "car_name": "Fiat Bravo 2008",
                    "reliability": {"overall": "medium"},
                    "ownership_cost": {},
                    "comfort_practicality": {},
                    "performance_driving": {},
                    "facts": {},
                    "short_notes": [],
                    "sources": ["https://example.com/fiat"],
                },
                "car_2": {
                    "car_name": "Seat Ibiza 2011",
                    "reliability": {},
                    "ownership_cost": {},
                    "comfort_practicality": {},
                    "performance_driving": {},
                    "facts": {},
                    "short_notes": [],
                    "sources": [],
                },
            },
            "sources": ["https://example.com/fiat"],
        }
        result = compute_comparison_results(model_output)
        assert result["comparison_status"]["balanced"] is False
        assert result["comparison_status"]["cars_with_evidence"] == 1
        assert result["cars"]["car_2"]["evidence_available"] is False


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


class TestCompareWriterPromptAndValidation:
    def test_writer_prompt_is_capped(self):
        cars_selected_slots = {
            "car_1": {"display_name": "Toyota " + ("X" * 5000)},
            "car_2": {"display_name": "Honda " + ("Y" * 5000)},
        }
        computed_result = {
            "overall_winner": "car_1",
            "category_winners": {"reliability_risk": "car_1", "ownership_cost": "car_2", "practicality_comfort": "tie", "driving_performance": "car_1"},
            "cars": {
                "car_1": {"overall_score": 82, "categories": {}},
                "car_2": {"overall_score": 79, "categories": {}},
            },
        }
        prompt = build_compare_writer_prompt(cars_selected_slots, computed_result, {"cars": {}, "assumptions": {}})
        assert len(prompt) <= COMPARE_WRITER_PROMPT_CHAR_CAP

    def test_writer_prompt_uses_slot_schema_for_three_cars(self):
        cars_selected_slots = {
            "car_1": {"display_name": "Toyota Corolla 2020"},
            "car_2": {"display_name": "Honda Civic 2020"},
            "car_3": {"display_name": "Mazda 3 2020"},
        }
        computed_result = {
            "overall_winner": "car_3",
            "category_winners": {
                "reliability_risk": "car_1",
                "ownership_cost": "car_2",
                "practicality_comfort": "tie",
                "driving_performance": "car_3",
            },
            "cars": {
                "car_1": {"overall_score": 80, "categories": {"reliability_risk": {"score": 84}}},
                "car_2": {"overall_score": 79, "categories": {"ownership_cost": {"score": 82}}},
                "car_3": {"overall_score": 83, "categories": {"driving_performance": {"score": 90}}},
            },
            "comparison_status": {"balanced": True},
        }

        prompt = build_compare_writer_prompt(cars_selected_slots, computed_result, {"cars": {}, "assumptions": {}})

        assert '"cars":{"car_1"' in prompt
        assert '"car_3":{"label":"Mazda 3 2020","evidence"' in prompt
        assert '"label":"car_1|car_2|car_3|tie|depends|unknown"' in prompt
        assert "carA|carB|tie" not in prompt

    def test_writer_validator_accepts_extra_keys_and_truncates_long_fields(self):
        payload = {
            "summary": " ".join(["סיכום"] * 90),
            "winner": "carA",
            "categories": [
                {
                    "name": "reliability_risk",
                    "winner": "carA",
                    "why": " ".join(["אמינות"] * 65),
                    "explanations": {
                        "car_1": " ".join(["לטויוטה"] * 70),
                        "car_2": " ".join(["להונדה"] * 62),
                    },
                    "tips": [
                        " ".join(["בדקו"] * 35),
                        "Verify recall completion",
                    ],
                    "extra_field": "ignored",
                }
            ],
            "caveats": [" ".join(["שימו"] * 40)],
            "extra_payload_field": {"ignored": True},
        }
        validated = validate_compare_writer_response(payload)
        assert validated is not None
        assert len(validated["summary"].split()) == 80
        assert len(validated["categories"][0]["why"].split()) == 60
        assert len(validated["categories"][0]["explanations"]["car_1"].split()) == 60
        assert len(validated["categories"][0]["tips"][0].split()) == 30
        assert len(validated["caveats"][0].split()) == 30

    def test_writer_validator_rejects_missing_required_keys(self):
        invalid_payload = {
            "summary": "short summary",
            "winner": "carA",
            "categories": [],
        }
        assert validate_compare_writer_response(invalid_payload) is None

    def test_writer_validator_accepts_slot_based_third_car_and_converts_narrative(self):
        payload = {
            "summary": "המועמד השלישי מוביל בתמונה הכוללת.",
            "winner": "car_3",
            "categories": [
                {
                    "name": "driving_performance",
                    "winner": "car_3",
                    "why": "הוא מציג יתרון דינמי ברור לפי הניקוד.",
                    "explanations": {
                        "car_1": "פחות חד ודינמי לפי הניקוד.",
                        "car_2": "מאוזן אך לא מוביל בנהיגה.",
                        "car_3": "מרגיש חד יותר ולכן מוביל.",
                    },
                    "tips": ["בדקו צמיגים", "בדקו בלמים"],
                }
            ],
            "caveats": ["התחזוקה משפיעה מאוד."],
        }

        validated = validate_compare_writer_response(payload)

        assert validated is not None
        assert validated["winner"] == "car_3"

        narrative = convert_writer_response_to_narrative(
            validated,
            {
                "car_1": {"display_name": "Toyota Corolla 2020"},
                "car_2": {"display_name": "Honda Civic 2020"},
                "car_3": {"display_name": "Mazda 3 2020"},
            },
        )
        assert narrative["category_explanations"][0]["winner"] == "car_3"
        assert set(narrative["category_explanations"][0]["explanations"].keys()) == {"car_1", "car_2", "car_3"}
        assert narrative["category_explanations"][0]["explanations"]["car_3"] == "מרגיש חד יותר ולכן מוביל."


class TestCompareSegmentInference:
    def test_infer_compare_segment_city_mini(self):
        segment = infer_compare_segment({"make": "Kia", "model": "Picanto", "display_name": "Kia Picanto 2020"}, {})
        assert segment == "city_mini"

    def test_build_single_car_prompt_embeds_segment_context(self):
        prompt = build_single_car_prompt({"make": "Kia", "model": "Sportage", "year": 2020})
        assert '"segment_key": "crossover_soft_suv"' in prompt
        assert "family usability" in prompt
        assert "CATEGORY_BEHAVIOR_RULES" in prompt


# ============================================================
# Integration: history detail with narrative
# ============================================================


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-for-pytest")
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
                model_name="gemini-3.1-flash",
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
                model_name="gemini-3.1-flash",
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

    def test_detail_recovers_legacy_stage_b_narrative_shape(self, app, logged_in_client):
        client, user_id = logged_in_client

        computed = {
            "overall_winner": "car_1",
            "cars": {},
            "ai": {
                "status": "ok",
                "reason": None,
                "stage_b": {
                    "summary": "Legacy summary from stored AI payload.",
                    "categories": [
                        {
                            "name": "reliability_risk",
                            "winner": "car_1",
                            "why": "Legacy explanation text.",
                            "tips": ["Legacy tip"],
                        }
                    ],
                    "caveats": ["Legacy caveat"],
                },
            },
        }

        with app.app_context():
            record = ComparisonHistory(
                user_id=user_id,
                cars_selected=json.dumps([
                    {"make": "Toyota", "model": "Corolla", "year": 2020},
                    {"make": "Honda", "model": "Civic", "year": 2021},
                ]),
                computed_result=json.dumps(computed),
                model_name="gemini-3.1-flash",
                prompt_version="v1",
            )
            db.session.add(record)
            db.session.commit()
            record_id = record.id

        resp = client.get(f"/api/compare/{record_id}")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["narrative"]["overall_summary"] == "Legacy summary from stored AI payload."
        assert data["narrative"]["category_explanations"][0]["category_key"] == "reliability_risk"
        assert data["narrative"]["category_explanations"][0]["explanations"]["car_1"] == "Legacy explanation text."
        assert data["ai"]["stage_b"]["narrative"] == "Legacy summary from stored AI payload."

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
                model_name="gemini-3.1-flash",
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


def test_compare_response_contains_decision_result_shape():
    from app.services.comparison_service import build_deterministic_decision_result

    slots = map_cars_to_slots([
        {"make": "Toyota", "model": "Corolla", "year": 2020},
        {"make": "Hyundai", "model": "Elantra", "year": 2020},
    ])
    result = build_deterministic_decision_result(slots, {"overall_winner": "tie", "cars": {"car_1": {}, "car_2": {}}})
    assert "overall_decision" in result
    assert "category_decisions" in result
    assert result["overall_decision"]["label"] in {"tie", "depends", "unknown", "car_1", "car_2"}


def test_compare_decision_result_has_no_visible_numeric_scores():
    from app.services.comparison_service import sanitize_decision_result

    cleaned = sanitize_decision_result(
        {
            "overall_decision": {"label": "car_1", "text": "84/100 ואני ממליץ"},
            "category_decisions": [{"category_key": "pricing_and_value", "preferred": "car_1", "why": "winnerScore 9/10"}],
            "practical_summary": "overall_score מתוך 100",
        },
        {"car_1": {}, "car_2": {}},
        {"cars": {"car_1": {}, "car_2": {}}},
        "test-request",
    )
    serialized = json.dumps(cleaned, ensure_ascii=False)
    forbidden = ["/100", "/10", "winnerScore", "overall_score", "category_score"]
    assert not any(token in serialized for token in forbidden)


def test_compare_template_does_not_render_score_markers():
    from pathlib import Path

    text = Path("templates/compare.html").read_text(encoding="utf-8")
    forbidden = ["/100", "מהציון", "winnerScore", "category score", "overall score"]
    assert not any(token in text for token in forbidden)


def test_compare_category_decisions_render_preference_labels():
    from pathlib import Path

    text = Path("templates/compare.html").read_text(encoding="utf-8")
    for token in ["עדיפות", "למה זה משנה", "מה לבדוק"]:
        assert token in text


def test_compare_accepts_optional_buyer_profile_validation():
    from app.services.comparison_service import validate_buyer_profile

    ok, error, profile = validate_buyer_profile({
        "budget_min": 50000,
        "budget_max": 90000,
        "main_use": "family",
        "annual_km": 18000,
        "family_size": "זוג + 2",
        "priority_weights": {"reliability": 11, "fuel": 7},
    })
    assert ok is True
    assert error is None
    assert profile["main_use"] == "family"
    assert profile["priority_weights"]["reliability"] == 10


def test_compare_rejects_or_sanitizes_bad_buyer_profile():
    from app.services.comparison_service import validate_buyer_profile

    ok, error, profile = validate_buyer_profile({
        "budget_max": 999999999,
        "main_use": "<script>bad</script>",
        "annual_km": -5,
        "family_size": "x" * 500,
    })
    assert ok is True
    assert profile["main_use"] == "unknown"
    assert "budget_max" not in profile
    assert len(profile["family_size"]) <= 80


def test_buyer_profile_does_not_override_vehicle_facts_in_prompt():
    prompt = build_compare_writer_prompt(
        {"car_1": {"display_name": "Toyota Corolla"}, "car_2": {"display_name": "Hyundai Elantra"}},
        {"cars": {"car_1": {}, "car_2": {}}, "overall_winner": "tie"},
        {"cars": {"car_1": {"car_profile": {"official_safety": {"rating": "5"}}}, "car_2": {}}},
        {"main_use": "family", "priority_weights": {"safety": 10}},
    )
    assert "must not override factual vehicle data" in prompt
    assert "User preference context only" in prompt


def test_compare_stage_b_forbidden_score_text_is_sanitized():
    from app.services.comparison_service import sanitize_decision_result

    cleaned = sanitize_decision_result(
        {"overall_decision": {"label": "car_1", "text": "84/100"}, "practical_summary": "תקנה עכשיו"},
        {"car_1": {}, "car_2": {}},
        {"cars": {"car_1": {}, "car_2": {}}},
        "req",
    )
    serialized = json.dumps(cleaned, ensure_ascii=False)
    assert "84/100" not in serialized
    assert cleaned["overall_decision"]["text"] != "84/100"
    assert "תקנה" not in cleaned["practical_summary"]


def test_sanitize_decision_result_fills_missing_per_car_arrays_from_fallback():
    """Missing choose_car_X_if / avoid_or_check_car_X_if must be filled from fallback."""
    from app.services.comparison_service import sanitize_decision_result

    slots = {"car_1": {"display_name": "Toyota Corolla"}, "car_2": {"display_name": "Hyundai Elantra"}}
    # AI writer omits per-car arrays entirely
    cleaned = sanitize_decision_result(
        {"overall_decision": {"label": "car_1", "text": "טויוטה עדיפה."}},
        slots,
        {"cars": {"car_1": {}, "car_2": {}}},
        "test-req",
    )
    assert isinstance(cleaned["choose_car_1_if"], list) and len(cleaned["choose_car_1_if"]) > 0
    assert isinstance(cleaned["choose_car_2_if"], list) and len(cleaned["choose_car_2_if"]) > 0
    assert isinstance(cleaned["avoid_or_check_car_1_if"], list) and len(cleaned["avoid_or_check_car_1_if"]) > 0
    assert isinstance(cleaned["avoid_or_check_car_2_if"], list) and len(cleaned["avoid_or_check_car_2_if"]) > 0


def test_sanitize_decision_result_fills_empty_per_car_arrays_from_fallback():
    """Empty choose_car_X_if / avoid_or_check_car_X_if lists must be filled from fallback."""
    from app.services.comparison_service import sanitize_decision_result

    slots = {"car_1": {"display_name": "Toyota Corolla"}, "car_2": {"display_name": "Hyundai Elantra"}}
    cleaned = sanitize_decision_result(
        {
            "overall_decision": {"label": "car_1", "text": "טויוטה עדיפה."},
            "choose_car_1_if": [],
            "choose_car_2_if": [],
            "avoid_or_check_car_1_if": [],
            "avoid_or_check_car_2_if": [],
        },
        slots,
        {"cars": {"car_1": {}, "car_2": {}}},
        "test-req",
    )
    assert len(cleaned["choose_car_1_if"]) > 0
    assert len(cleaned["choose_car_2_if"]) > 0
    assert len(cleaned["avoid_or_check_car_1_if"]) > 0
    assert len(cleaned["avoid_or_check_car_2_if"]) > 0


def test_sanitize_decision_result_supports_car_3():
    """car_3 slot must also receive populated choose/avoid arrays."""
    from app.services.comparison_service import sanitize_decision_result

    slots = {
        "car_1": {"display_name": "Toyota Corolla"},
        "car_2": {"display_name": "Hyundai Elantra"},
        "car_3": {"display_name": "Alfa Romeo Stelvio"},
    }
    # AI omits car_3 arrays
    cleaned = sanitize_decision_result(
        {
            "overall_decision": {"label": "car_1", "text": "טויוטה עדיפה."},
            "choose_car_1_if": ["מתאים למשפחה"],
            "choose_car_2_if": ["מתאים לעיר"],
        },
        slots,
        {"cars": {"car_1": {}, "car_2": {}, "car_3": {}}},
        "test-req",
    )
    assert "choose_car_3_if" in cleaned
    assert isinstance(cleaned["choose_car_3_if"], list) and len(cleaned["choose_car_3_if"]) > 0
    assert "avoid_or_check_car_3_if" in cleaned
    assert isinstance(cleaned["avoid_or_check_car_3_if"], list) and len(cleaned["avoid_or_check_car_3_if"]) > 0
    # car_3 fallback should provide non-empty content
    assert len(cleaned["choose_car_3_if"][0]) > 0


def test_sanitize_decision_result_fills_missing_why_from_fallback():
    """category_decisions.why must be filled from fallback when AI omits it."""
    from app.services.comparison_service import sanitize_decision_result

    slots = {"car_1": {"display_name": "Toyota"}, "car_2": {"display_name": "Hyundai"}}
    cleaned = sanitize_decision_result(
        {
            "overall_decision": {"label": "car_1", "text": "בחר בטויוטה."},
            "category_decisions": [
                {"category_key": "pricing_and_value", "preferred": "car_1", "why": ""},
            ],
        },
        slots,
        {"cars": {"car_1": {}, "car_2": {}}},
        "test-req",
    )
    pricing_item = next(d for d in cleaned["category_decisions"] if d["category_key"] == "pricing_and_value")
    assert pricing_item["why"] and len(pricing_item["why"]) > 0


def test_sanitize_decision_result_fills_missing_important_caveat_from_fallback():
    """category_decisions.important_caveat must be filled from fallback when AI omits it."""
    from app.services.comparison_service import sanitize_decision_result

    slots = {"car_1": {"display_name": "Toyota"}, "car_2": {"display_name": "Hyundai"}}
    cleaned = sanitize_decision_result(
        {
            "overall_decision": {"label": "car_1", "text": "בחר בטויוטה."},
            "category_decisions": [
                {"category_key": "official_safety", "preferred": "car_1", "why": "שניהם 5 כוכבים.", "important_caveat": None},
            ],
        },
        slots,
        {"cars": {"car_1": {}, "car_2": {}}},
        "test-req",
    )
    safety_item = next(d for d in cleaned["category_decisions"] if d["category_key"] == "official_safety")
    assert safety_item["important_caveat"] is not None and len(safety_item["important_caveat"]) > 0
