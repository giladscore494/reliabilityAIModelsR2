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
                # missing estimated_reliability AND risk_signals
                "reliability_report": {},
            },
            None,
        )

    monkeypatch.setattr(main, "call_gemini_grounded_once", fake_ai)
    resp = client.post("/analyze", json=_base_payload(), headers={"Origin": "http://localhost"})
    data = resp.get_json()["data"]
    # Without risk_signals the deterministic scorer returns "לא ידוע"
    assert data["estimated_reliability"] == "לא ידוע"


def test_numeric_removed_but_estimated_stays(logged_in_client, monkeypatch):
    client = _setup_client(logged_in_client)

    def fake_ai(_prompt):
        return (
            {
                "ok": True,
                "base_score_calculated": 40,
                "reliability_score": 3,
                # missing estimated_reliability AND risk_signals
                "reliability_report": {},
            },
            None,
        )

    monkeypatch.setattr(main, "call_gemini_grounded_once", fake_ai)
    resp = client.post("/analyze", json=_base_payload(), headers={"Origin": "http://localhost"})
    data = resp.get_json()["data"]
    # base_score_calculated is now preserved (deterministic value)
    assert "base_score_calculated" in data
    assert "reliability_score" not in data
    # Without risk_signals the deterministic scorer returns "לא ידוע"
    assert data["estimated_reliability"] == "לא ידוע"


def test_search_details_returns_estimated_without_numeric(logged_in_client, monkeypatch, app):
    client, user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True})

    # Seed history entry
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
                result_json='{"base_score_calculated": 82, "reliability_summary": "ok"}',
            )
        )
        db.session.commit()
        search_id = main.SearchHistory.query.filter_by(user_id=user_id).first().id

    resp = client.get(f"/search-details/{search_id}")
    assert resp.status_code == 200
    payload = resp.get_json()["data"]["data"]
    assert "estimated_reliability" in payload
    assert payload["estimated_reliability"] == "גבוה"
    assert "base_score_calculated" not in payload
    assert "reliability_score" not in payload
