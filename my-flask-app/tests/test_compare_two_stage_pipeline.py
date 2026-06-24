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
                "facts": {
                    "horsepower": 138,
                    "weight_kg": 1310,
                    "body_type": "sedan",
                    "fuel_type": "petrol",
                },
                "car_profile": {
                    "vehicle_identity": {
                        "make": "Toyota",
                        "model": "Corolla",
                        "year": "2020",
                    },
                    "recommended_trim": {"trim_name": "Sun", "confidence": "medium"},
                    "powertrain_specs": {
                        "engine": "1.8 Hybrid",
                        "gearbox": "CVT",
                        "drivetrain": "FWD",
                        "seats": 5,
                        "sources": ["https://example.com/toyota/spec"],
                    },
                },
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
                "facts": {
                    "horsepower": 158,
                    "weight_kg": 1325,
                    "body_type": "sedan",
                    "fuel_type": "petrol",
                },
                "car_profile": {
                    "vehicle_identity": {
                        "make": "Honda",
                        "model": "Civic",
                        "year": "2020",
                    },
                    "recommended_trim": {"trim_name": "Sport", "confidence": "medium"},
                    "powertrain_specs": {
                        "engine": "1.5 Turbo",
                        "gearbox": "Automatic",
                        "drivetrain": "FWD",
                        "seats": 5,
                        "sources": ["https://example.com/honda/spec"],
                    },
                },
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
        "facts": {
            "horsepower": 186,
            "weight_kg": 1380,
            "body_type": "hatchback",
            "fuel_type": "petrol",
        },
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


def test_compare_two_stage_keeps_server_authoritative_numbers(
    app, logged_in_client, monkeypatch
):
    client, _user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})

    grounded_output = _grounded_output_fixture()
    expected = comparison_service.compute_comparison_results(grounded_output)

    monkeypatch.setattr(
        comparison_service,
        "call_stage_a_parallel",
        _fake_stage_a_parallel(grounded_output),
    )

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


def test_compare_two_stage_handles_stage_b_failure_gracefully(
    app, logged_in_client, monkeypatch
):
    client, _user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})

    grounded_output = _grounded_output_fixture()

    monkeypatch.setattr(
        comparison_service,
        "call_stage_a_parallel",
        _fake_stage_a_parallel(grounded_output),
    )

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


def test_compare_stage_b_length_error_returns_fallback_200_fast(
    app, logged_in_client, monkeypatch
):
    client, _user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})

    monkeypatch.setattr(
        comparison_service,
        "call_stage_a_parallel",
        _fake_stage_a_parallel(_grounded_output_fixture()),
    )

    def fake_stage_b(
        _prompt, timeout_sec=comparison_service.COMPARE_WRITER_TIMEOUT_SEC
    ):
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


def test_compare_stage_b_json_schema_parsed_into_narrative(
    app, logged_in_client, monkeypatch
):
    client, _user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})

    monkeypatch.setattr(
        comparison_service,
        "call_stage_a_parallel",
        _fake_stage_a_parallel(_grounded_output_fixture()),
    )

    def fake_stage_b(
        _prompt, timeout_sec=comparison_service.COMPARE_WRITER_TIMEOUT_SEC
    ):
        return {
            "summary": "טויוטה מובילה מעט באמינות ובתמונה הכוללת.",
            "winner": "carA",
            "categories": [
                {
                    "name": "reliability_risk",
                    "winner": "carA",
                    "why": "פחות סיכון לתקלות משמעותיות לפי הניקוד.",
                    "explanations": {
                        "car_1": "מראה פחות סיכון לתקלות ולכן קיבל ציון חזק יותר.",
                        "car_2": "הציון מעט נמוך יותר בגלל סיכון גבוה יותר.",
                    },
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
    assert cat["explanations"]["car_1"]
    assert payload["ai"]["status"] == "ok"
    assert payload["ai"]["reason"] is None


def test_compare_response_includes_checked_versions(app, logged_in_client, monkeypatch):
    client, _user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})

    monkeypatch.setattr(
        comparison_service,
        "call_stage_a_parallel",
        _fake_stage_a_parallel(_grounded_output_fixture()),
    )

    def fake_stage_b(
        _prompt, timeout_sec=comparison_service.COMPARE_WRITER_TIMEOUT_SEC
    ):
        return {
            "decision_result": {
                "overall_decision": {
                    "label": "car_1",
                    "text": "לטויוטה יש עדיפות קלה בתמונה הכוללת.",
                },
                "category_decisions": [],
                "key_differences": [],
                "competitors_to_consider": [],
                "practical_summary": "ההשוואה תלויה גם בהתאמה לשימוש ובבדיקה בפועל.",
                "choose_car_1_if": ["מחפשים מוניטין אמינות חזק."],
                "choose_car_2_if": ["חשוב יותר אופי נהיגה מעט חד יותר."],
                "avoid_or_check_car_1_if": ["לאמת רמת גימור ורשימת אבזור."],
                "avoid_or_check_car_2_if": ["לאמת היסטוריית טיפולים ותיבה."],
            },
            "checked_versions": {
                "car_1": {
                    "make": "Toyota",
                    "model": "Corolla",
                    "year": "2020",
                    "trim": "Sun",
                    "engine_type": "1.8 Hybrid",
                    "transmission": "CVT",
                    "drivetrain": "FWD",
                    "seats": "5",
                    "data_basis": "mixed",
                    "confidence": "medium",
                    "notes": "סוג התיבה עדיין דורש אימות מול מפרט היבואן.",
                },
                "car_2": {
                    "make": "Honda",
                    "model": "Civic",
                    "year": "2020",
                    "trim": "Sport",
                    "engine_type": "1.5 Turbo",
                    "transmission": "Automatic",
                    "drivetrain": "FWD",
                    "seats": "5",
                    "data_basis": "mixed",
                    "confidence": "medium",
                    "notes": "רמת הגימור נבחרה לפי המידע הזמין.",
                },
            },
            "sources": ["https://example.com/toyota/spec"],
        }, None

    monkeypatch.setattr(comparison_service, "call_gemini_compare_writer", fake_stage_b)

    resp = client.post(
        "/api/compare",
        json={
            "cars": [
                {
                    "make": "Toyota",
                    "model": "Corolla",
                    "year": 2020,
                    "engine_type": "היברידי",
                    "gearbox": "רציפה",
                },
                {
                    "make": "Honda",
                    "model": "Civic",
                    "year": 2020,
                    "engine_type": "בנזין",
                    "gearbox": "אוטומטית",
                },
            ],
            "legal_confirm": True,
        },
        headers={"Content-Type": "application/json", "Origin": "http://localhost"},
    )

    assert resp.status_code == 200
    payload = resp.get_json()["data"]
    assert payload["checked_versions"]["car_1"]["transmission"] == "רציפה"
    assert payload["checked_versions"]["car_1"]["trim"] == "Sun"
    assert payload["checked_versions"]["car_1"]["notes"]
    assert payload["checked_versions"]["car_2"]["data_basis"] == "mixed"


def test_compare_three_cars_stage_b_slot_schema_parsed_into_narrative(
    app, logged_in_client, monkeypatch
):
    client, _user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})

    grounded_output = _grounded_output_fixture_three_cars()
    expected = comparison_service.compute_comparison_results(grounded_output)

    monkeypatch.setattr(
        comparison_service,
        "call_stage_a_parallel",
        _fake_stage_a_parallel(grounded_output),
    )

    def fake_stage_b(
        _prompt, timeout_sec=comparison_service.COMPARE_WRITER_TIMEOUT_SEC
    ):
        return {
            "summary": "מאזדה בולטת בנהיגה, בזמן שהפער הכללי מול האחרות אינו גדול.",
            "winner": "car_3",
            "categories": [
                {
                    "name": "driving_performance",
                    "winner": "car_3",
                    "why": "הניקוד הדינמי הגבוה ביותר שייך לה.",
                    "explanations": {
                        "car_1": "פחות חדה מהשלישית בנהיגה.",
                        "car_2": "מאוזנת אבל לא מובילה בנהיגה.",
                        "car_3": "הדינמיקה העדיפה נותנת לה יתרון ברור.",
                    },
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
    assert set(
        payload["narrative"]["category_explanations"][0]["explanations"].keys()
    ) == {"car_1", "car_2", "car_3"}
    assert payload["ai"]["stage_a"]["winner"] == expected["overall_winner"]
    assert payload["ai"]["status"] == "ok"


def test_compare_writer_prompt_requires_checked_versions():
    cars_selected = {
        "car_1": {
            "make": "Toyota",
            "model": "Corolla",
            "year": 2020,
            "engine_type": "היברידי",
            "gearbox": "רציפה",
            "display_name": "Toyota Corolla 2020",
        },
        "car_2": {
            "make": "Honda",
            "model": "Civic",
            "year": 2020,
            "engine_type": "בנזין",
            "gearbox": "אוטומטית",
            "display_name": "Honda Civic 2020",
        },
    }
    grounded = _grounded_output_fixture()
    computed = comparison_service.compute_comparison_results(grounded)

    prompt = comparison_service.build_compare_writer_prompt(
        cars_selected, computed, grounded
    )

    assert '"checked_versions"' in prompt
    assert "Do not use DSG" in prompt
    assert "לא ידוע / לבדיקה" in prompt


def test_compare_stage_b_empty_decision_arrays_are_backfilled(
    app, logged_in_client, monkeypatch
):
    client, _user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})

    grounded_output = _grounded_output_fixture()
    monkeypatch.setattr(
        comparison_service,
        "call_stage_a_parallel",
        _fake_stage_a_parallel(grounded_output),
    )

    def fake_stage_b(
        _prompt, timeout_sec=comparison_service.COMPARE_WRITER_TIMEOUT_SEC
    ):
        return {
            "decision_result": {
                "overall_decision": {
                    "label": "car_1",
                    "text": "לטויוטה יש עדיפות קלה בתמונה הכוללת.",
                },
                "category_decisions": [
                    {
                        "category_key": "pricing_and_value",
                        "category_name_he": "מחיר ותמורה",
                        "preferred": "car_1",
                        "why": "היא משתלמת יותר.",
                        "important_caveat": "בדקו היסטוריית טיפולים מלאה.",
                    }
                ],
                "key_differences": [
                    {
                        "title": "אופי שימוש",
                        "car_1": "מתאימה יותר לשימוש רגוע.",
                        "car_2": "מרגישה מעט חדה יותר.",
                        "meaning_for_buyer": "תלוי במה חשוב לך ביום יום.",
                    }
                ],
                "choose_car_1_if": [],
                "choose_car_2_if": [],
                "avoid_or_check_car_1_if": [],
                "avoid_or_check_car_2_if": [],
                "competitors_to_consider": [],
                "practical_summary": "בדקו מצב ועלויות לפני החלטה.",
            },
            "sources": [],
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
    decision = resp.get_json()["data"]["decision_result"]
    assert decision["choose_car_1_if"]
    assert decision["choose_car_2_if"]
    assert decision["avoid_or_check_car_1_if"]
    assert decision["avoid_or_check_car_2_if"]


def test_compare_stage_b_missing_per_car_keys_are_backfilled_for_car_3(
    app, logged_in_client, monkeypatch
):
    client, _user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})

    grounded_output = _grounded_output_fixture_three_cars()
    monkeypatch.setattr(
        comparison_service,
        "call_stage_a_parallel",
        _fake_stage_a_parallel(grounded_output),
    )

    def fake_stage_b(
        _prompt, timeout_sec=comparison_service.COMPARE_WRITER_TIMEOUT_SEC
    ):
        return {
            "decision_result": {
                "overall_decision": {
                    "label": "car_3",
                    "text": "לרכב השלישי יש עדיפות קלה.",
                },
                "category_decisions": [
                    {
                        "category_key": "powertrain_and_performance",
                        "category_name_he": "מכלולים וביצועים",
                        "preferred": "car_3",
                        "why": "הוא חד יותר לנהיגה.",
                        "important_caveat": "בדקו צמיגים ובלמים.",
                    }
                ],
                "key_differences": [
                    {
                        "title": "תחושת נהיגה",
                        "car_1": "רגועה יותר.",
                        "car_2": "מאוזנת.",
                        "car_3": "חדה יותר.",
                        "meaning_for_buyer": "משפיע בעיקר על מי שנהנה מנהיגה.",
                    }
                ],
                "competitors_to_consider": [],
                "practical_summary": "השלישי מתאים יותר למי שמחפש תחושה דינמית.",
            },
            "sources": [],
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
    decision = resp.get_json()["data"]["decision_result"]
    assert decision["choose_car_3_if"]
    assert decision["avoid_or_check_car_3_if"]


# --- Regression tests: transmission contradiction and empty field backfill ---


def test_compare_stage_b_transmission_mismatch_is_overwritten_in_final_payload(
    app, logged_in_client, monkeypatch
):
    """User selected automatic; AI Stage B returns manual in checked_versions → final must not show ידנית."""
    client, _user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})

    grounded_output = _grounded_output_fixture()
    monkeypatch.setattr(
        comparison_service,
        "call_stage_a_parallel",
        _fake_stage_a_parallel(grounded_output),
    )

    def fake_stage_b(
        _prompt, timeout_sec=comparison_service.COMPARE_WRITER_TIMEOUT_SEC
    ):
        return {
            "decision_result": {
                "overall_decision": {
                    "label": "car_1",
                    "text": "לטויוטה עדיפה קלה.",
                },
                "category_decisions": [
                    {
                        "category_key": "pricing_and_value",
                        "category_name_he": "מחיר ותמורה",
                        "preferred": "car_1",
                        "why": "משתלמת יותר.",
                        "important_caveat": None,
                    }
                ],
                "key_differences": [],
                "choose_car_1_if": ["מי שמחפש אחזקה נמוכה."],
                "choose_car_2_if": ["מי שמחפש ביצועים."],
                "avoid_or_check_car_1_if": ["בדקו היסטוריית תאונות."],
                "avoid_or_check_car_2_if": ["בדקו עלויות ביטוח."],
                "competitors_to_consider": [],
                "practical_summary": "שתי אפשרויות סבירות.",
            },
            "checked_versions": {
                "car_1": {
                    "make": "Toyota",
                    "model": "Corolla",
                    "year": "2020",
                    "trim": "Comfort",
                    "engine_type": "בנזין",
                    "transmission": "ידנית",  # ← AI mistake: user selected automatic
                    "drivetrain": "FWD",
                    "seats": "5",
                    "notes": "גרסה מייצגת.",
                },
                "car_2": {
                    "make": "Honda",
                    "model": "Civic",
                    "year": "2020",
                    "trim": "Comfort",
                    "engine_type": "בנזין",
                    "transmission": "אוטומטית",
                    "drivetrain": "FWD",
                    "seats": "5",
                    "notes": "גרסה מייצגת.",
                },
            },
            "sources": [],
        }, None

    monkeypatch.setattr(comparison_service, "call_gemini_compare_writer", fake_stage_b)

    resp = client.post(
        "/api/compare",
        json={
            "cars": [
                {"make": "Toyota", "model": "Corolla", "year": 2020, "gearbox": "אוטומטית"},
                {"make": "Honda", "model": "Civic", "year": 2020, "gearbox": "אוטומטית"},
            ],
            "legal_confirm": True,
        },
        headers={"Content-Type": "application/json", "Origin": "http://localhost"},
    )
    assert resp.status_code == 200
    payload = resp.get_json()["data"]
    car1_transmission = payload["checked_versions"]["car_1"]["transmission"]
    assert "ידנית" not in car1_transmission, (
        f"Transmission mismatch not corrected: got '{car1_transmission}' for user who selected automatic"
    )
    assert "אוטומטית" in car1_transmission, (
        f"Expected אוטומטית after mismatch correction but got: '{car1_transmission}'"
    )


def test_compare_stage_b_checked_versions_empty_fields_are_backfilled_in_final_payload(
    app, logged_in_client, monkeypatch
):
    """AI Stage B returns checked_versions with empty required fields → final payload must have non-empty values."""
    client, _user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})

    grounded_output = _grounded_output_fixture()
    monkeypatch.setattr(
        comparison_service,
        "call_stage_a_parallel",
        _fake_stage_a_parallel(grounded_output),
    )

    def fake_stage_b(
        _prompt, timeout_sec=comparison_service.COMPARE_WRITER_TIMEOUT_SEC
    ):
        return {
            "decision_result": {
                "overall_decision": {
                    "label": "car_1",
                    "text": "לטויוטה עדיפה קלה.",
                },
                "category_decisions": [],
                "key_differences": [],
                "choose_car_1_if": ["מי שמחפש אחזקה נמוכה."],
                "choose_car_2_if": ["מי שמחפש ביצועים."],
                "avoid_or_check_car_1_if": ["בדקו היסטוריית תאונות."],
                "avoid_or_check_car_2_if": ["בדקו עלויות ביטוח."],
                "competitors_to_consider": [],
                "practical_summary": "שתי אפשרויות סבירות.",
            },
            "checked_versions": {
                "car_1": {
                    "make": "Toyota",
                    "model": "Corolla",
                    "year": "",
                    "trim": "",
                    "engine_type": "",
                    "transmission": "אוטומטית",
                    "drivetrain": "",
                    "seats": "",
                    "notes": "",
                },
                "car_2": {
                    "make": "Honda",
                    "model": "Civic",
                    "year": "2020",
                    "trim": "",
                    "engine_type": "בנזין",
                    "transmission": "אוטומטית",
                    "drivetrain": "FWD",
                    "seats": "5",
                    "notes": "גרסה מייצגת.",
                },
            },
            "sources": [],
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
    checked = payload["checked_versions"]
    for slot_key in ("car_1", "car_2"):
        slot = checked[slot_key]
        for field in ("trim", "engine_type", "drivetrain", "seats", "notes"):
            assert slot.get(field), (
                f"checked_versions.{slot_key}.{field} must not be empty after backfill, "
                f"got: {slot.get(field)!r}"
            )


def test_compare_writer_prompt_hard_rules_contain_critical_transmission_rule():
    """Stage B prompt must include the critical transmission anti-contradiction rule."""
    cars_selected = {
        "car_1": {
            "make": "Toyota",
            "model": "Corolla",
            "year": 2020,
            "transmission": "automatic",
            "display_name": "Toyota Corolla 2020",
        },
        "car_2": {
            "make": "Honda",
            "model": "Civic",
            "year": 2020,
            "transmission": "automatic",
            "display_name": "Honda Civic 2020",
        },
    }
    grounded = _grounded_output_fixture()
    computed = comparison_service.compute_comparison_results(grounded)
    prompt = comparison_service.build_compare_writer_prompt(cars_selected, computed, grounded)

    assert "CRITICAL" in prompt, "Prompt must have CRITICAL-marked rules"
    assert "ידנית" in prompt or "manual" in prompt.lower(), (
        "Prompt must mention manual/ידנית in the context of critical rules"
    )
    assert "לא ידוע / לבדיקה" in prompt
    assert "14." in prompt, "Prompt must include rule 14 (transmission critical rule)"
    assert "15." in prompt, "Prompt must include rule 15 (required fields rule)"
    assert "16." in prompt, "Prompt must include rule 16 (decision text rule)"


def test_compare_stage_a_timeout_returns_503_with_retryable_error(
    app, logged_in_client, monkeypatch
):
    client, _user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})

    def fake_stage_a_parallel(validated_cars, cars_selected_slots):
        empty = comparison_service._empty_stage_a_output(cars_selected_slots)
        sources_index = comparison_service.build_sources_index_from_flat(empty)
        errors = [f"{k}: CALL_TIMEOUT" for k in cars_selected_slots]
        return empty, sources_index, errors

    monkeypatch.setattr(
        comparison_service, "call_stage_a_parallel", fake_stage_a_parallel
    )
    monkeypatch.setattr(
        comparison_service,
        "call_gemini_compare_writer",
        lambda _prompt, timeout_sec=comparison_service.COMPARE_WRITER_TIMEOUT_SEC: (
            None,
            "CALL_TIMEOUT",
        ),
    )

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


def test_stage_a_config_is_bounded_and_grounding_enabled(app, monkeypatch):
    """Stage A must be bounded AND must enable Google Search grounding
    (catalog-first rebuild: grounding is mandatory for Stage A evidence)."""
    captured = {}

    class _FakeModels:
        def generate_content(self, *, model, contents, config):
            captured["config"] = config
            captured["model"] = model
            return SimpleNamespace(
                text='{"car_name":"Toyota Corolla 2020","reliability":{"overall":"high"},"ownership_cost":{},"comfort_practicality":{},"performance_driving":{},"facts":{},"short_notes":[],"sources":[]}',
                candidates=[],
            )

    monkeypatch.setattr(
        comparison_service.extensions,
        "ai_client",
        SimpleNamespace(models=_FakeModels()),
    )
    with app.app_context():
        out, err = comparison_service.call_gemini_single_car(
            "{}", "car_1", timeout_sec=1
        )
    assert err is None
    assert isinstance(out, dict)
    cfg = captured["config"]
    assert (
        int(getattr(cfg, "max_output_tokens", 0))
        == comparison_service.COMPARE_STAGE_A_MAX_OUTPUT_TOKENS
    )
    # Google Search grounding tool must be present.
    tools = getattr(cfg, "tools", None) or []
    assert any(getattr(t, "google_search", None) is not None for t in tools)
    # JSON mime type must NOT be combined with grounding tools.
    assert getattr(cfg, "response_mime_type", None) in (None, "")
    assert "flash" in captured["model"].lower()


def test_call_gemini_compare_writer_exception_path_returns_error(app, monkeypatch):
    class _FailingModels:
        def generate_content(self, **_kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr(
        comparison_service.extensions,
        "ai_client",
        SimpleNamespace(models=_FailingModels()),
    )
    with app.app_context():
        out, err = comparison_service.call_gemini_compare_writer("{}", timeout_sec=1)
    assert out is None
    assert err and err.startswith("CALL_FAILED:")


def test_compare_ai_regenerate_updates_ai_only(app, logged_in_client, monkeypatch):
    client, user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})

    grounded_output = _grounded_output_fixture()
    monkeypatch.setattr(
        comparison_service,
        "call_stage_a_parallel",
        _fake_stage_a_parallel(grounded_output),
    )
    monkeypatch.setattr(
        comparison_service,
        "call_gemini_compare_writer",
        lambda _prompt, timeout_sec=comparison_service.COMPARE_WRITER_TIMEOUT_SEC: (
            {
                "summary": "סיכום ראשון.",
                "winner": "carA",
                "categories": [],
                "caveats": [],
            },
            None,
        ),
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
        lambda _prompt, timeout_sec=comparison_service.COMPARE_WRITER_TIMEOUT_SEC: (
            {
                "summary": "סיכום מעודכן.",
                "winner": "carA",
                "categories": [],
                "caveats": [],
            },
            None,
        ),
    )

    regen = client.post(
        f"/api/compare/ai-regenerate?comparison_id={comparison_id}",
        json={"legal_confirm": True},
        headers={"Origin": "http://localhost", "Referer": "http://localhost/compare"},
        environ_overrides={
            "HTTP_ORIGIN": "http://localhost",
            "HTTP_REFERER": "http://localhost/compare",
        },
    )
    assert regen.status_code == 200
    regen_payload = regen.get_json()["data"]
    assert regen_payload["ai"]["status"] == "ok"
    assert regen_payload["ai"]["stage_b"] is not None


def test_compare_ai_regenerate_keeps_usable_long_narrative(
    app, logged_in_client, monkeypatch
):
    client, _user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})

    grounded_output = _grounded_output_fixture()
    monkeypatch.setattr(
        comparison_service,
        "call_stage_a_parallel",
        _fake_stage_a_parallel(grounded_output),
    )
    monkeypatch.setattr(
        comparison_service,
        "call_gemini_compare_writer",
        lambda _prompt, timeout_sec=comparison_service.COMPARE_WRITER_TIMEOUT_SEC: (
            {
                "summary": "סיכום ראשון.",
                "winner": "carA",
                "categories": [],
                "caveats": [],
            },
            None,
        ),
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
        lambda _prompt, timeout_sec=comparison_service.COMPARE_WRITER_TIMEOUT_SEC: (
            {
                "summary": " ".join(["מעודכן"] * 90),
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
                        "tips": [" ".join(["בדקו"] * 35)],
                        "extra_field": "ignored",
                    }
                ],
                "caveats": [" ".join(["שימו"] * 40)],
                "extra_payload_field": True,
            },
            None,
        ),
    )

    regen = client.post(
        f"/api/compare/ai-regenerate?comparison_id={comparison_id}",
        json={"legal_confirm": True},
        headers={"Origin": "http://localhost", "Referer": "http://localhost/compare"},
        environ_overrides={
            "HTTP_ORIGIN": "http://localhost",
            "HTTP_REFERER": "http://localhost/compare",
        },
    )
    assert regen.status_code == 200
    regen_payload = regen.get_json()["data"]
    assert regen_payload["ai"]["status"] == "ok"
    assert regen_payload["narrative"]["overall_summary"]
    assert len(regen_payload["narrative"]["overall_summary"].split()) == 80
    assert regen_payload["narrative"]["category_explanations"][0]["explanations"][
        "car_1"
    ]


def test_compare_ai_regenerate_backfills_missing_decision_arrays(
    app, logged_in_client, monkeypatch
):
    client, _user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})

    grounded_output = _grounded_output_fixture()
    monkeypatch.setattr(
        comparison_service,
        "call_stage_a_parallel",
        _fake_stage_a_parallel(grounded_output),
    )
    monkeypatch.setattr(
        comparison_service,
        "call_gemini_compare_writer",
        lambda _prompt, timeout_sec=comparison_service.COMPARE_WRITER_TIMEOUT_SEC: (
            {
                "decision_result": {
                    "overall_decision": {
                        "label": "car_1",
                        "text": "לטויוטה יש עדיפות קלה.",
                    },
                    "category_decisions": [
                        {
                            "category_key": "pricing_and_value",
                            "category_name_he": "מחיר ותמורה",
                            "preferred": "car_1",
                            "why": "היא נראית משתלמת יותר.",
                            "important_caveat": "בדקו היסטוריית טיפולים מלאה.",
                        }
                    ],
                    "practical_summary": "בדקו מצב ועלויות לפני החלטה.",
                },
                "sources": [],
            },
            None,
        ),
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

    regen = client.post(
        f"/api/compare/ai-regenerate?comparison_id={comparison_id}",
        json={"legal_confirm": True},
        headers={"Origin": "http://localhost", "Referer": "http://localhost/compare"},
        environ_overrides={
            "HTTP_ORIGIN": "http://localhost",
            "HTTP_REFERER": "http://localhost/compare",
        },
    )
    assert regen.status_code == 200
    decision = regen.get_json()["data"]["decision_result"]
    assert decision["choose_car_1_if"]
    assert decision["choose_car_2_if"]
    assert decision["avoid_or_check_car_1_if"]
    assert decision["avoid_or_check_car_2_if"]


def test_compare_stage_a_json_invalid_returns_503_without_persisting_success(
    app, logged_in_client, monkeypatch
):
    client, _user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})

    def fake_stage_a_parallel(validated_cars, cars_selected_slots):
        empty = comparison_service._empty_stage_a_output(cars_selected_slots)
        sources_index = comparison_service.build_sources_index_from_flat(empty)
        errors = [f"{k}: MODEL_JSON_INVALID" for k in cars_selected_slots]
        return empty, sources_index, errors

    monkeypatch.setattr(
        comparison_service, "call_stage_a_parallel", fake_stage_a_parallel
    )
    monkeypatch.setattr(
        comparison_service,
        "call_gemini_compare_writer",
        lambda _prompt, timeout_sec=comparison_service.COMPARE_WRITER_TIMEOUT_SEC: (
            None,
            "CALL_TIMEOUT",
        ),
    )

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


def test_compare_partial_stage_a_failure_returns_200_partial_fallback(
    app, logged_in_client, monkeypatch
):
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

    monkeypatch.setattr(
        comparison_service, "call_stage_a_parallel", fake_stage_a_parallel
    )
    monkeypatch.setattr(
        comparison_service,
        "call_gemini_compare_writer",
        lambda _prompt, timeout_sec=comparison_service.COMPARE_WRITER_TIMEOUT_SEC: (
            {
                "summary": "סיכום חלקי.",
                "winner": "carA",
                "categories": [],
                "caveats": [],
            },
            None,
        ),
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


def test_compare_stage_b_extra_keys_and_long_text_still_render(
    app, logged_in_client, monkeypatch
):
    client, _user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})

    grounded_output = _grounded_output_fixture()
    monkeypatch.setattr(
        comparison_service,
        "call_stage_a_parallel",
        _fake_stage_a_parallel(grounded_output),
    )
    monkeypatch.setattr(
        comparison_service,
        "call_gemini_compare_writer",
        lambda _prompt, timeout_sec=comparison_service.COMPARE_WRITER_TIMEOUT_SEC: (
            {
                "summary": " ".join(["טויוטה"] * 90),
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
                        "tips": [" ".join(["בדקו"] * 35)],
                        "extra_field": "ignored",
                    }
                ],
                "caveats": [" ".join(["שימו"] * 40)],
                "extra_payload_field": {"ignored": True},
            },
            None,
        ),
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
    assert payload["ai"]["status"] == "ok"
    assert payload["ai"]["reason"] is None
    assert payload["narrative"]["overall_summary"]
    assert len(payload["narrative"]["overall_summary"].split()) == 80
    assert payload["narrative"]["category_explanations"][0]["explanations"]["car_1"]
    assert (
        len(
            payload["narrative"]["category_explanations"][0]["explanations"][
                "car_1"
            ].split()
        )
        == 60
    )
    assert (
        payload["ai"]["stage_b"]["narrative"] == payload["narrative"]["overall_summary"]
    )


def test_compare_partial_stage_a_usable_stage_b_kept_visible(
    app, logged_in_client, monkeypatch
):
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
        return output, sources_index, ["car_2: MODEL_JSON_INVALID"]

    monkeypatch.setattr(
        comparison_service, "call_stage_a_parallel", fake_stage_a_parallel
    )
    monkeypatch.setattr(
        comparison_service,
        "call_gemini_compare_writer",
        lambda _prompt, timeout_sec=comparison_service.COMPARE_WRITER_TIMEOUT_SEC: (
            {
                "summary": "השוואה חלקית אבל מועילה לקונה לפני החלטה.",
                "winner": "carA",
                "categories": [
                    {
                        "name": "reliability_risk",
                        "winner": "carA",
                        "why": "הטויוטה נתמכת ביותר ראיות אמינות זמינות ולכן שומרת על יתרון יחסי.",
                        "explanations": {
                            "car_1": "לטויוטה יש תמונת אמינות יציבה יותר גם במסלול החלקי.",
                            "car_2": "להונדה יש פחות מידע מאומת ולכן צריך בדיקה נוספת.",
                        },
                        "tips": ["בדקו היסטוריית טיפולים", "אמתו מסמכים"],
                    }
                ],
                "caveats": ["חלק מהמידע חסר ולכן צריך להשלים בדיקה."],
                "extra_payload_field": True,
            },
            None,
        ),
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
    assert payload["narrative"]["category_explanations"][0]["explanations"]["car_1"]
    assert payload["ai"]["stage_b"]["categories"]


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
        merged, _sources_index, errors = comparison_service.call_stage_a_parallel(
            validated_cars, slots
        )

    assert "car_1: CALL_TIMEOUT" in errors
    assert "car_2: CALL_CANCELLED" in errors
    assert "car_3: CALL_FAILED:RuntimeError" in errors
    assert merged["cars"]["car_4"]["reliability"]["overall"] == "high"


def test_call_stage_a_parallel_real_threads_do_not_require_worker_app_context(
    app, monkeypatch
):
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

    monkeypatch.setattr(
        comparison_service.extensions,
        "ai_client",
        SimpleNamespace(models=_FakeModels()),
    )
    monkeypatch.setattr(
        comparison_service.genai_types, "AutomaticFunctionCallingConfig", _FakeAuto
    )
    monkeypatch.setattr(
        comparison_service.genai_types, "GenerateContentConfig", _FakeConfig
    )

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as real_executor:
        with app.app_context():
            import app.factory as factory

            monkeypatch.setattr(factory, "AI_EXECUTOR", real_executor)
            validated_cars = [
                {"make": "Toyota", "model": "Corolla", "year": 2020},
                {"make": "Honda", "model": "Civic", "year": 2020},
            ]
            slots = comparison_service.map_cars_to_slots(validated_cars)
            merged, _sources_index, errors = comparison_service.call_stage_a_parallel(
                validated_cars, slots
            )

    assert errors == []
    assert merged["cars"]["car_1"]["reliability"]["overall"] == "high"
    assert merged["cars"]["car_2"]["reliability"]["overall"] == "high"


def test_call_stage_a_parallel_retries_json_invalid_once(app, monkeypatch):
    calls = []

    def fake_single_car(prompt, car_label, timeout_sec, request_id, log):
        calls.append((prompt, car_label))
        if len(calls) == 1:
            return None, "MODEL_JSON_INVALID"
        return {
            "car_name": "Toyota Corolla 2020",
            "reliability": {"overall": "high"},
            "ownership_cost": {},
            "comfort_practicality": {},
            "performance_driving": {},
            "facts": {},
            "short_notes": [],
            "sources": ["https://example.com/toyota"],
        }, None

    monkeypatch.setattr(comparison_service, "call_gemini_single_car", fake_single_car)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as real_executor:
        with app.app_context():
            import app.factory as factory

            monkeypatch.setattr(factory, "AI_EXECUTOR", real_executor)
            validated_cars = [{"make": "Toyota", "model": "Corolla", "year": 2020}]
            slots = comparison_service.map_cars_to_slots(validated_cars)
            merged, _sources_index, errors = comparison_service.call_stage_a_parallel(
                validated_cars, slots
            )

    assert errors == []
    assert len(calls) == 2
    assert "FINAL JSON REMINDER" in calls[1][0]
    assert merged["cars"]["car_1"]["reliability"]["overall"] == "high"


def test_compare_quota_released_on_full_stage_a_failure(
    app, logged_in_client, monkeypatch
):
    client, user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})

    def fake_stage_a_parallel(_validated_cars, cars_selected_slots):
        empty = comparison_service._empty_stage_a_output(cars_selected_slots)
        sources_index = comparison_service.build_sources_index_from_flat(empty)
        errors = [f"{k}: CALL_TIMEOUT" for k in cars_selected_slots]
        return empty, sources_index, errors

    monkeypatch.setattr(
        comparison_service, "call_stage_a_parallel", fake_stage_a_parallel
    )
    monkeypatch.setattr(
        comparison_service,
        "call_gemini_compare_writer",
        lambda *_args, **_kwargs: (None, "CALL_TIMEOUT"),
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
    assert resp.status_code == 503
    with app.app_context():
        tz, _ = resolve_app_timezone()
        day_key, *_ = compute_quota_window(tz)
        quota = DailyQuotaUsage.query.filter_by(user_id=user_id, day=day_key).first()
        assert quota is None or quota.count == 0


def test_compare_ai_regenerate_writer_exception_returns_200_fallback(
    app, logged_in_client, monkeypatch
):
    client, _user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})

    grounded_output = _grounded_output_fixture()
    monkeypatch.setattr(
        comparison_service,
        "call_stage_a_parallel",
        _fake_stage_a_parallel(grounded_output),
    )
    monkeypatch.setattr(
        comparison_service,
        "call_gemini_compare_writer",
        lambda _prompt, timeout_sec=comparison_service.COMPARE_WRITER_TIMEOUT_SEC: (
            {
                "summary": "סיכום ראשון.",
                "winner": "carA",
                "categories": [],
                "caveats": [],
            },
            None,
        ),
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
        db.session.add(
            DailyQuotaUsage(
                user_id=user_id, day=day_key, count=5, updated_at=datetime.utcnow()
            )
        )
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
    assert data["error"]["code"] == "daily_limit_reached"
    assert "reset_at" in data["error"]["details"]


def test_compare_idempotency_key_does_not_consume_quota_twice(
    app, logged_in_client, monkeypatch
):
    client, user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})

    def fake_handle(_data, _uid, _sid, owner_bypass=False):
        from app.utils.http_helpers import api_ok

        return api_ok(
            {
                "computed_result": {"overall_winner": "car_1"},
                "cars_selected": {},
                "narrative": None,
            }
        )

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
        db.session.add(
            DailyQuotaUsage(
                user_id=user_id, day=day_key, count=5, updated_at=datetime.utcnow()
            )
        )
        db.session.commit()

    def fake_handle(_data, _uid, _sid, owner_bypass=False):
        from app.utils.http_helpers import api_ok

        return api_ok(
            {
                "computed_result": {"overall_winner": "car_1"},
                "cars_selected": {},
                "narrative": None,
            }
        )

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


def test_compare_owner_bypasses_internal_history_gate(
    app, logged_in_client, monkeypatch
):
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
                    model_json_raw="{}",
                    computed_result="{}",
                    sources_index="{}",
                )
            )
        db.session.commit()

    monkeypatch.setattr(
        comparison_service,
        "call_stage_a_parallel",
        _fake_stage_a_parallel(_grounded_output_fixture()),
    )
    monkeypatch.setattr(
        comparison_service,
        "call_gemini_compare_writer",
        lambda _prompt, timeout_sec=60: (None, "CALL_TIMEOUT"),
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
