import json

import main
from main import db
from app.models import SearchHistory


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


def _prepare_history(app, user_id, result):
    with app.app_context():
        history = SearchHistory(
            user_id=user_id,
            make="Toyota",
            model="Corolla",
            year=2020,
            mileage_range="0-50k",
            fuel_type="בנזין",
            transmission="אוטומטית",
            result_json=json.dumps(result, ensure_ascii=False),
        )
        db.session.add(history)
        db.session.commit()
        return history.id


def test_estimated_reliability_present(logged_in_client, monkeypatch):
    client = _setup_client(logged_in_client)

    def fake_ai(_prompt):
        return (
            {
                "ok": True,
                "base_score_calculated": 85,
                "estimated_reliability": "גבוה",
                "reliability_report": {},
            },
            None,
        )

    monkeypatch.setattr(main, "call_gemini_grounded_once", fake_ai)
    resp = client.post("/analyze", json=_base_payload(), headers={"Origin": "http://localhost"})
    data = resp.get_json()["data"]
    assert data["estimated_reliability"] in ["נמוך", "בינוני", "גבוה", "לא ידוע"]


def test_estimated_reliability_derives_from_base_score(logged_in_client, monkeypatch):
    client = _setup_client(logged_in_client)

    def fake_ai(_prompt):
        return (
            {
                "ok": True,
                "base_score_calculated": 62,
                # missing estimated_reliability
                "reliability_report": {},
            },
            None,
        )

    monkeypatch.setattr(main, "call_gemini_grounded_once", fake_ai)
    resp = client.post("/analyze", json=_base_payload(), headers={"Origin": "http://localhost"})
    data = resp.get_json()["data"]
    assert data["estimated_reliability"] == "בינוני"


def test_numeric_removed_but_estimated_stays(logged_in_client, monkeypatch):
    client = _setup_client(logged_in_client)

    def fake_ai(_prompt):
        return (
            {
                "ok": True,
                "base_score_calculated": 40,
                "reliability_score": 3,
                # missing estimated_reliability
                "reliability_report": {},
            },
            None,
        )

    monkeypatch.setattr(main, "call_gemini_grounded_once", fake_ai)
    resp = client.post("/analyze", json=_base_payload(), headers={"Origin": "http://localhost"})
    data = resp.get_json()["data"]
    assert "base_score_calculated" not in data
    assert "reliability_score" not in data
    assert data["estimated_reliability"] == "נמוך"


def test_history_detail_estimated_reliability_fallback(app, logged_in_client):
    client, user_id = logged_in_client
    client = _setup_client(logged_in_client)

    history_id = _prepare_history(
        app,
        user_id,
        {
            "ok": True,
            "base_score_calculated": 82,
            "reliability_score": 9,
            "reliability_report": {},
        },
    )

    resp = client.get(f"/search-details/{history_id}")
    payload = resp.get_json()["data"]["data"]
    assert payload["estimated_reliability"] == "גבוה"
    assert payload["estimated_reliability"] in ["נמוך", "בינוני", "גבוה", "לא ידוע"]
    assert "base_score_calculated" not in payload
    assert "reliability_score" not in payload


def test_history_detail_ui_uses_estimated_only(app, logged_in_client):
    client, user_id = logged_in_client
    client = _setup_client(logged_in_client)

    history_id = _prepare_history(
        app,
        user_id,
        {
            "ok": True,
            "estimated_reliability": "בינוני",
            "reliability_report": {},
        },
    )

    resp = client.get(f"/search-details/{history_id}")
    detail = resp.get_json()["data"]["data"]
    assert "estimated_reliability" in detail
    assert detail["estimated_reliability"] in ["נמוך", "בינוני", "גבוה", "לא ידוע"]

    resp = client.get("/dashboard")
    html = resp.get_data(as_text=True)
    assert "מתוך 100" not in html
    assert "ציון אמינות כללי" not in html
