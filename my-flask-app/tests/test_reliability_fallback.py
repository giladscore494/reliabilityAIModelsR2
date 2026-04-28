import json

import main
from main import db


def _base_payload():
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


def _setup_client(logged_in_client):
    client, _ = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})
    return client


def test_analyze_returns_information_review_fields(logged_in_client, monkeypatch):
    client = _setup_client(logged_in_client)

    def fake_ai(_prompt):
        return (
            {
                "ok": True,
                "sources": [
                    {
                        "title": "Source",
                        "url": "https://example.com",
                        "domain": "example.com",
                    }
                ],
                "recommended_checks": ["אימות היסטוריית טיפולים"],
                "reliability_report": {
                    "based_on_available_information": "מידע חלקי",
                    "known_uncertainties": ["היסטוריית טיפולים לא הוצגה"],
                },
            },
            None,
        )

    monkeypatch.setattr(main, "call_gemini_grounded_once", fake_ai)
    resp = client.post("/analyze", json=_base_payload(), headers={"Origin": "http://localhost"})
    data = resp.get_json()["data"]
    assert data["data_quality_label"] in ["חסרה", "חלקית", "טובה"]
    assert data["decision_readiness"] in [
        "חסר מידע קריטי",
        "נדרש אימות נוסף",
        "מוכן לבדיקה מקצועית",
    ]
    assert "missing_critical_info" in data
    assert "verification_focus" in data
    assert "estimated_reliability" not in data
    assert "base_score_calculated" not in data


def test_analyze_success_payload_includes_request_id_and_flattened_report_fields(
    logged_in_client, monkeypatch
):
    client = _setup_client(logged_in_client)

    def fake_ai(_prompt):
        return (
            {
                "ok": True,
                "sources": [
                    {
                        "title": "Source",
                        "url": "https://example.com",
                        "domain": "example.com",
                    }
                ],
                "reliability_report": {
                    "based_on_available_information": "מידע קיים",
                    "key_risk_areas_to_examine": [
                        {
                            "risk_area": "גיר",
                            "why_to_check": "יש לאמת היסטוריית טיפולים",
                        }
                    ],
                    "what_must_be_checked_before_a_decision": {
                        "mechanical_inspection_points": ["בדיקת גיר"],
                    },
                    "known_uncertainties": ["היסטוריית טיפולים מלאה לא הוצגה"],
                    "estimated_cost_sensitivity": ["תיקון גיר עלול להיות יקר"],
                },
            },
            None,
        )

    monkeypatch.setattr(main, "call_gemini_grounded_once", fake_ai)
    resp = client.post("/analyze", json=_base_payload(), headers={"Origin": "http://localhost"})
    payload = resp.get_json()
    data = payload["data"]

    assert resp.status_code == 200
    assert payload["request_id"]
    assert data["request_id"] == payload["request_id"]
    assert data["based_on_available_information"] == "מידע קיים"
    assert data["key_risk_areas_to_examine"][0]["risk_area"] == "גיר"
    assert (
        data["what_must_be_checked_before_a_decision"]["mechanical_inspection_points"]
        == ["בדיקת גיר"]
    )
    assert "היסטוריית טיפולים מלאה לא הוצגה" in data["known_uncertainties"]
    assert data["estimated_cost_sensitivity"] == ["תיקון גיר עלול להיות יקר"]


def test_analyze_derives_missing_source_gap(logged_in_client, monkeypatch):
    client = _setup_client(logged_in_client)

    def fake_ai(_prompt):
        return (
            {
                "ok": True,
                "sources": [],
                "reliability_report": {},
            },
            None,
        )

    monkeypatch.setattr(main, "call_gemini_grounded_once", fake_ai)
    resp = client.post("/analyze", json=_base_payload(), headers={"Origin": "http://localhost"})
    data = resp.get_json()["data"]
    assert data["data_quality_label"] == "חסרה"
    assert any("מקורות חיצוניים" in item for item in data["missing_critical_info"])


def test_search_details_normalizes_old_history_to_information_review(
    logged_in_client, app
):
    client, user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})

    with app.app_context():
        db.session.add(
            main.SearchHistory(
                user_id=user_id,
                make="Honda",
                model="Civic",
                year=2018,
                mileage_range="50-100k",
                fuel_type="בנזין",
                transmission="אוטומטית",
                result_json=json.dumps(
                    {"reliability_summary": "ok"},
                    ensure_ascii=False,
                ),
            )
        )
        db.session.commit()
        search_id = main.SearchHistory.query.filter_by(user_id=user_id).first().id

    resp = client.get(f"/search-details/{search_id}")
    assert resp.status_code == 200
    payload = resp.get_json()["data"]["data"]
    assert payload["data_quality_label"] in ["חסרה", "חלקית", "טובה"]
    assert payload["decision_readiness"] in [
        "חסר מידע קריטי",
        "נדרש אימות נוסף",
        "מוכן לבדיקה מקצועית",
    ]
    assert "estimated_reliability" not in payload
    assert "base_score_calculated" not in payload
