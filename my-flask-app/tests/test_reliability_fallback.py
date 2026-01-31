from pathlib import Path

import main
from main import db, SearchHistory


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


def test_history_detail_uses_estimated_reliability(logged_in_client, app):
    client, user_id = logged_in_client
    with app.app_context():
        history = SearchHistory(
            user_id=user_id,
            make="Toyota",
            model="Corolla",
            year=2020,
            mileage_range="0-50k",
            fuel_type="בנזין",
            transmission="אוטומטית",
            result_json='{"ok": true}',
        )
        db.session.add(history)
        db.session.commit()
        history_id = history.id

    resp = client.get(f"/search-details/{history_id}")
    assert resp.status_code == 200
    payload = resp.get_json()
    data = payload["data"]["data"]
    assert data["estimated_reliability"] in ["נמוך", "בינוני", "גבוה", "לא ידוע"]


def test_detail_template_no_numeric_reliability():
    template_path = Path(__file__).resolve().parents[1] / "templates" / "dashboard.html"
    with open(template_path, encoding="utf-8") as handle:
        html = handle.read()
    assert "ציון אמינות" not in html
    assert "מתוך 100" not in html
