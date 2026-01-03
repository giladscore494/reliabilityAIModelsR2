import pytest

import main
from main import DailyQuotaUsage, QuotaReservation, SearchHistory, db, compute_quota_window, resolve_app_timezone


def _valid_payload():
    return {
        "make": "Toyota",
        "model": "Corolla",
        "year": 2020,
        "mileage_range": "0-50k",
        "fuel_type": "בנזין",
        "transmission": "אוטומטית",
        "sub_model": "",
    }


def test_redirect_www_to_apex(client):
    resp = client.get("/", base_url="https://www.yedaarechev.com")
    assert resp.status_code == 301
    assert resp.headers.get("Location", "").startswith("https://yedaarechev.com/")


def test_api_schema_error(logged_in_client):
    client, _ = logged_in_client
    resp = client.post("/analyze", json={"make": "", "model": "", "year": ""})
    data = resp.get_json()
    assert resp.status_code == 400
    assert data["ok"] is False
    assert data["error"]["code"] == "validation_error"
    assert "request_id" in data


def test_api_schema_success(client):
    resp = client.get("/healthz")
    data = resp.get_json()
    assert resp.status_code == 200
    assert data["ok"] is True
    assert data["data"]["status"] == "ok"
    assert "request_id" in data


def test_quota_refund_on_failure(app, logged_in_client, monkeypatch):
    client, user_id = logged_in_client
    monkeypatch.setenv("SIMULATE_AI_FAIL", "1")

    resp = client.post("/analyze", json=_valid_payload())
    data = resp.get_json()

    assert resp.status_code >= 500
    assert data["ok"] is False

    with app.app_context():
        tz, _ = resolve_app_timezone()
        day_key, *_ = compute_quota_window(tz)
        quota = DailyQuotaUsage.query.filter_by(user_id=user_id, day=day_key).first()
        assert quota is None or quota.count == 0
        reserved_active = QuotaReservation.query.filter_by(user_id=user_id, day=day_key, status="reserved").count()
        assert reserved_active == 0


def test_quota_atomic_limit(app, logged_in_client, monkeypatch):
    client, user_id = logged_in_client
    monkeypatch.setattr(main, "USER_DAILY_LIMIT", 1)

    def fake_gemini(_prompt):
        return (
            {
                "ok": True,
                "base_score_calculated": 55,
                "search_performed": True,
                "search_queries": [],
                "sources": [],
                "reliability_report": {},
            },
            None,
        )

    monkeypatch.setattr(main, "call_gemini_grounded_once", fake_gemini)

    resp_ok = client.post("/analyze", json=_valid_payload())
    data_ok = resp_ok.get_json()
    assert resp_ok.status_code == 200
    assert data_ok["ok"] is True

    with app.app_context():
        SearchHistory.query.delete()
        db.session.commit()

    resp_block = client.post("/analyze", json=_valid_payload())
    data_block = resp_block.get_json()
    assert resp_block.status_code == 429
    assert data_block["ok"] is False
    assert data_block["error"]["code"] == "quota_exceeded"

    with app.app_context():
        tz, _ = resolve_app_timezone()
        day_key, *_ = compute_quota_window(tz)
        quota = DailyQuotaUsage.query.filter_by(user_id=user_id, day=day_key).first()
        assert quota and quota.count == 1
        reserved_active = QuotaReservation.query.filter_by(user_id=user_id, day=day_key, status="reserved").count()
        assert reserved_active == 0
