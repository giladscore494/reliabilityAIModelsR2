import pytest
from datetime import datetime

import main
from main import (
    DailyQuotaUsage,
    IpRateLimit,
    QuotaReservation,
    SearchHistory,
    db,
    compute_quota_window,
    reserve_daily_quota,
    resolve_app_timezone,
)


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


def test_ip_rate_limit_single_row(app):
    with app.app_context():
        IpRateLimit.query.delete()
        db.session.commit()

        now = datetime(2024, 1, 1, 12, 0, 0)
        ok1, count1, _ = main.check_and_increment_ip_rate_limit("1.2.3.4", limit=5, now_utc=now)
        ok2, count2, _ = main.check_and_increment_ip_rate_limit("1.2.3.4", limit=5, now_utc=now)

        assert ok1 and ok2
        assert count1 == 1
        assert count2 == 2
        rows = IpRateLimit.query.filter_by(ip="1.2.3.4", window_start=now.replace(second=0, microsecond=0)).all()
        assert len(rows) == 1
        assert rows[0].count == 2


def test_quota_row_created_once(app, logged_in_client):
    _client, user_id = logged_in_client
    with app.app_context():
        tz, _ = resolve_app_timezone()
        day_key, *_ = compute_quota_window(tz)
        DailyQuotaUsage.query.delete()
        QuotaReservation.query.delete()
        db.session.commit()

        ok1, consumed1, reserved1, res_id1 = reserve_daily_quota(user_id, day_key, limit=3, request_id="req-1", now_utc=datetime.utcnow())
        ok2, consumed2, reserved2, res_id2 = reserve_daily_quota(user_id, day_key, limit=3, request_id="req-2", now_utc=datetime.utcnow())

        assert ok1 and ok2
        assert consumed1 == 0
        assert consumed2 == 0
        assert reserved2 == 2
        rows = DailyQuotaUsage.query.filter_by(user_id=user_id, day=day_key).all()
        assert len(rows) == 1
        assert rows[0].count == 0


def test_login_returns_google_redirect(monkeypatch, client):
    # Avoid live calls to Google during tests
    def fake_authorize_redirect(redirect_uri):
        return main.redirect(f"https://accounts.google.com/o/oauth2/v2/auth?redirect_uri={redirect_uri}")

    monkeypatch.setattr(main.oauth.google, "authorize_redirect", fake_authorize_redirect)

    resp = client.get("/login", base_url="https://yedaarechev.com")
    assert resp.status_code in (302, 303)
    location = resp.headers.get("Location", "")
    assert "accounts.google.com" in location
    assert "yedaarechev.com/auth" in location
