"""Tests for the Single Vehicle Intelligence Card (vehicle_profile) feature."""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import main  # noqa: E402
from app.utils.sanitization import sanitize_analyze_response  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_vehicle_profile(**overrides):
    vp = {
        "vehicle_identity": {
            "make": "Toyota",
            "model": "Corolla",
            "year": "2020",
            "generation": None,
            "body_type": "sedan",
            "segment": "C",
            "israel_market_status": "sold_new",
            "year_discontinued_in_israel": None,
        },
        "pricing_israel": {
            "new_price_range_ils": "120,000–145,000 ₪",
            "used_price_range_ils": "80,000–110,000 ₪",
            "price_notes": [],
            "sources": [],
        },
        "license_fee_israel": {
            "annual_fee_ils": 1800,
            "method": "official",
            "notes": [],
            "sources": [],
        },
        "trim_levels_israel": [],
        "recommended_trim": {
            "trim_name": "Executive",
            "reason": "ציוד עשיר במחיר סביר",
            "confidence": "medium",
        },
        "powertrain_specs": {
            "engine": "1.8L",
            "gearbox": "CVT",
            "drivetrain": "FWD",
            "horsepower": 140,
            "torque_nm": 172,
            "battery_kwh": None,
            "ev_range_km": None,
            "zero_to_100_sec": 9.2,
            "trunk_liters": 371,
            "seats": 5,
            "sources": [],
        },
        "fuel_consumption": {
            "official_value": "6.5L/100km",
            "real_world_value": "7.2L/100km",
            "method": "review_based",
            "notes": [],
            "sources": [],
        },
        "official_safety": {
            "rating": "5 stars",
            "organization": "Euro NCAP",
            "test_year": 2019,
            "adult_score": "88%",
            "child_score": "85%",
            "safety_assist_score": "79%",
            "notes": [],
            "sources": [],
        },
        "warranty_israel": {
            "vehicle_warranty": "3 שנים / 100,000 ק״מ",
            "battery_warranty": None,
            "importer_notes": [],
            "sources": [],
        },
        "recalls_israel": {
            "known_recalls": [],
            "checked_against_official_source": True,
            "notes": [],
            "sources": [],
        },
        "ownership_cost_notes": {
            "maintenance_cost_pressure": "low",
            "insurance_cost_pressure": "medium",
            "depreciation_risk": "low",
            "parts_availability": "high",
            "notes": [],
        },
        "competitors": [
            {
                "model": "Mazda 3",
                "why_relevant": "same_segment",
                "advantage_vs_current": "נהיגה ספורטיבית יותר",
                "disadvantage_vs_current": "צריכת דלק גבוהה יותר",
            }
        ],
        "best_for": ["משפחות קטנות", "נסיעות עירוניות"],
        "not_ideal_for": ["שטח"],
        "buyer_summary": "הרכב מתאים למשפחות קטנות המחפשות אמינות. חשוב לבדוק היסטוריית שירות.",
        "analysis_metadata": {
            "data_freshness": "current_year",
            "confidence_per_section": {
                "pricing": "high",
                "trims": "medium",
                "safety": "high",
                "recalls": "medium",
            },
            "sources_count": 5,
        },
    }
    vp.update(overrides)
    return vp


def _minimal_ai_response(vehicle_profile=None):
    resp = {
        "ok": True,
        "search_performed": True,
        "search_queries": [],
        "sources": [],
        "reliability_report": {},
    }
    if vehicle_profile is not None:
        resp["vehicle_profile"] = vehicle_profile
    return resp


def _analyze_payload():
    return {
        "make": "Toyota",
        "model": "Corolla",
        "year": 2020,
        "mileage_range": "0-50k",
        "fuel_type": "בנזין",
        "transmission": "אוטומטית",
        "sub_model": "",
        "legal_confirm": True,
    }


# ---------------------------------------------------------------------------
# Test 1: vehicle_profile passes sanitize and appears in response
# ---------------------------------------------------------------------------

def test_analyze_response_includes_vehicle_profile(logged_in_client, monkeypatch):
    client, _ = logged_in_client
    vp = _base_vehicle_profile()

    def fake_gemini(_prompt):
        return _minimal_ai_response(vehicle_profile=vp), None

    monkeypatch.setattr(main, "call_gemini_grounded_once", fake_gemini)
    client.post("/api/legal/accept", json={"legal_confirm": True})
    resp = client.post("/analyze", json=_analyze_payload(), headers={"Origin": "http://localhost"})
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert "vehicle_profile" in data
    assert data["vehicle_profile"]["vehicle_identity"]["make"] == "Toyota"
    assert data["vehicle_profile"]["vehicle_identity"]["israel_market_status"] == "sold_new"


# ---------------------------------------------------------------------------
# Test 2: optional null fields allowed
# ---------------------------------------------------------------------------

def test_vehicle_profile_optional_fields_can_be_null(logged_in_client, monkeypatch):
    client, _ = logged_in_client
    vp = _base_vehicle_profile()
    vp["official_safety"] = {
        "rating": None,
        "organization": None,
        "test_year": None,
        "adult_score": None,
        "child_score": None,
        "safety_assist_score": None,
        "notes": [],
        "sources": [],
    }
    vp["trim_levels_israel"] = []
    vp["recalls_israel"] = {
        "known_recalls": [],
        "checked_against_official_source": True,
        "notes": [],
        "sources": [],
    }

    def fake_gemini(_prompt):
        return _minimal_ai_response(vehicle_profile=vp), None

    monkeypatch.setattr(main, "call_gemini_grounded_once", fake_gemini)
    client.post("/api/legal/accept", json={"legal_confirm": True})
    resp = client.post("/analyze", json=_analyze_payload(), headers={"Origin": "http://localhost"})
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert "vehicle_profile" in data
    assert data["vehicle_profile"]["trim_levels_israel"] == []
    assert data["vehicle_profile"]["official_safety"]["rating"] is None


# ---------------------------------------------------------------------------
# Test 3: numeric score in buyer_summary rejected
# ---------------------------------------------------------------------------

def test_vehicle_profile_no_numeric_score_in_buyer_summary(logged_in_client, monkeypatch):
    client, _ = logged_in_client
    vp = _base_vehicle_profile()
    vp["buyer_summary"] = "הרכב קיבל 84/100 במבחן אמינות."

    def fake_gemini(_prompt):
        return _minimal_ai_response(vehicle_profile=vp), None

    monkeypatch.setattr(main, "call_gemini_grounded_once", fake_gemini)
    client.post("/api/legal/accept", json={"legal_confirm": True})
    resp = client.post("/analyze", json=_analyze_payload(), headers={"Origin": "http://localhost"})
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert "vehicle_profile" in data
    assert data["vehicle_profile"]["buyer_summary"] is None


# ---------------------------------------------------------------------------
# Test 4: first-person phrase in buyer_summary rejected
# ---------------------------------------------------------------------------

def test_vehicle_profile_buyer_summary_no_first_person(logged_in_client, monkeypatch):
    client, _ = logged_in_client
    vp = _base_vehicle_profile()
    vp["buyer_summary"] = "אני ממליץ לרכוש את הרכב הזה."

    def fake_gemini(_prompt):
        return _minimal_ai_response(vehicle_profile=vp), None

    monkeypatch.setattr(main, "call_gemini_grounded_once", fake_gemini)
    client.post("/api/legal/accept", json={"legal_confirm": True})
    resp = client.post("/analyze", json=_analyze_payload(), headers={"Origin": "http://localhost"})
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert "vehicle_profile" in data
    assert data["vehicle_profile"]["buyer_summary"] is None


# ---------------------------------------------------------------------------
# Test 5: license_fee method only official|unknown
# ---------------------------------------------------------------------------

def test_license_fee_method_only_official_or_unknown():
    raw = _minimal_ai_response(vehicle_profile=_base_vehicle_profile())
    raw["vehicle_profile"]["license_fee_israel"]["method"] = "derived_from_price_group"
    sanitized = sanitize_analyze_response(raw)
    assert sanitized["vehicle_profile"]["license_fee_israel"]["method"] == "unknown"


# ---------------------------------------------------------------------------
# Test 6: recalls cleared when not checked against official source
# ---------------------------------------------------------------------------

def test_recalls_only_shown_when_official_source_checked():
    vp = _base_vehicle_profile()
    vp["recalls_israel"] = {
        "known_recalls": [{"year": 2022, "issue": "airbag issue", "source": None}],
        "checked_against_official_source": False,
        "notes": [],
        "sources": [],
    }
    raw = _minimal_ai_response(vehicle_profile=vp)
    sanitized = sanitize_analyze_response(raw)
    recalls = sanitized["vehicle_profile"]["recalls_israel"]
    assert recalls["known_recalls"] == []
    assert recalls["checked_against_official_source"] is False
    assert any("לא בוצעה" in n for n in recalls["notes"])


# ---------------------------------------------------------------------------
# Test 7: script.js contains vpCompetitors reference
# ---------------------------------------------------------------------------

def test_competitors_rendered_separately_from_uncertainties():
    script_path = os.path.join(
        os.path.dirname(__file__), "..", "static", "script.js"
    )
    assert os.path.exists(script_path)
    size = os.path.getsize(script_path)
    assert size > 0
    with open(script_path, encoding="utf-8") as f:
        content = f.read()
    assert "vpCompetitors" in content


# ---------------------------------------------------------------------------
# Test 8: legacy history without vehicle_profile still renders
# ---------------------------------------------------------------------------

def test_legacy_history_without_vehicle_profile_renders(logged_in_client, monkeypatch):
    client, _ = logged_in_client

    def fake_gemini(_prompt):
        return _minimal_ai_response(), None  # no vehicle_profile

    monkeypatch.setattr(main, "call_gemini_grounded_once", fake_gemini)
    client.post("/api/legal/accept", json={"legal_confirm": True})
    resp = client.post("/analyze", json=_analyze_payload(), headers={"Origin": "http://localhost"})
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert "vehicle_profile" not in data


# ---------------------------------------------------------------------------
# Test 9: field is named buyer_summary, not friend_advice_summary
# ---------------------------------------------------------------------------

def test_buyer_summary_renamed_not_friend_advice():
    vp = _base_vehicle_profile()
    raw = _minimal_ai_response(vehicle_profile=vp)
    sanitized = sanitize_analyze_response(raw)
    vp_out = sanitized["vehicle_profile"]
    assert "buyer_summary" in vp_out
    assert "friend_advice_summary" not in vp_out
