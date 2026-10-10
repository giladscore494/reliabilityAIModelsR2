# -*- coding: utf-8 -*-
"""Vehicle Review (/app + POST /analyze) is owner-only by default.

Every lock point is checked in four states:
  anon  - not logged in
  user  - logged in, not an owner (OWNER_EMAILS empty)
  owner - logged in, email in OWNER_EMAILS
  open  - RELIABILITY_OWNER_ONLY=False, logged in regular user
"""

import json
import re
from urllib.parse import parse_qs, urlparse

import pytest

from app.extensions import oauth
from app.models import Feedback, IpRateLimit, SearchHistory
from app.utils.http_helpers import api_ok, reliability_access_allowed
from main import User, create_app, db

LOCK_MESSAGE = "סקירת הרכב זמינה כרגע לבעלי המערכת בלבד."
OWNER_EMAIL = "tester@example.com"
STATES = ("anon", "user", "owner", "open")
ALLOWED = {"owner", "open"}
APP_HREF = re.compile(r'href="/app(?=["?#/])')


def _login(app, client, email=OWNER_EMAIL):
    with app.app_context():
        user = User(google_id="lock-test-google-id", email=email, name="Tester")
        db.session.add(user)
        db.session.commit()
        user_id = user.id
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
    return user_id


@pytest.fixture(params=STATES)
def actor(request, app, client):
    """(state, client, user_id) for one of the four access states."""
    state = request.param
    app.config["OWNER_EMAILS"] = {OWNER_EMAIL} if state == "owner" else set()
    app.config["RELIABILITY_OWNER_ONLY"] = state != "open"
    user_id = None
    if state != "anon":
        user_id = _login(app, client)
        client.post("/api/legal/accept", json={"legal_confirm": True})
    return state, client, user_id


def _add_search(app, user_id, **extra):
    with app.app_context():
        row = SearchHistory(
            user_id=user_id,
            make="Toyota",
            model="Corolla",
            year=2020,
            result_json=json.dumps({"reliability_summary": "סיכום"}),
            **extra,
        )
        db.session.add(row)
        db.session.commit()
        return row.id


def _add_public_example(app):
    with app.app_context():
        owner = User(google_id="example-owner", email="example-owner@example.com", name="Owner")
        db.session.add(owner)
        db.session.commit()
        owner_id = owner.id
    return _add_search(app, owner_id, is_public_example=True, example_slug="toyota-corolla-2020")


class _NoDb:
    """Stand-in for SearchHistory: any DB access fails the test."""

    def __getattr__(self, name):
        raise AssertionError(f"SearchHistory.{name} accessed while Vehicle Review is locked")


def _flashes(client):
    with client.session_transaction() as sess:
        return list(sess.get("_flashes", []))


# ---------------------------------------------------------------- flag + helper

def test_flag_defaults_to_locked_without_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-for-pytest")
    monkeypatch.delenv("RELIABILITY_OWNER_ONLY", raising=False)
    app = create_app()
    assert app.config["RELIABILITY_OWNER_ONLY"] is True


@pytest.mark.parametrize(("raw", "expected"), [("0", False), ("false", False), ("1", True), ("yes", True)])
def test_flag_reads_env_once_at_startup(monkeypatch, raw, expected):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-for-pytest")
    monkeypatch.setenv("RELIABILITY_OWNER_ONLY", raw)
    app = create_app()
    assert app.config["RELIABILITY_OWNER_ONLY"] is expected


def test_helper_is_locked_when_config_missing(app):
    app.config.pop("RELIABILITY_OWNER_ONLY", None)
    with app.test_request_context():
        assert reliability_access_allowed() is False


# ---------------------------------------------------------------- 1. GET /app

def test_app_page(actor, monkeypatch):
    state, client, _ = actor
    import app.routes.public_routes as public_routes

    calls = []
    real_catalog = public_routes.get_vehicle_catalog_ui_data

    def spy_catalog(*args, **kwargs):
        calls.append(1)
        return real_catalog(*args, **kwargs)

    monkeypatch.setattr(public_routes, "get_vehicle_catalog_ui_data", spy_catalog)
    resp = client.get("/app")

    if state in ALLOWED:
        assert resp.status_code == 200
        assert len(calls) == 1
        return
    assert resp.status_code == 302
    assert calls == []
    if state == "anon":
        assert urlparse(resp.headers["Location"]).path == "/"
        assert _flashes(client) == []
    else:
        assert urlparse(resp.headers["Location"]).path == "/compare"
        assert ("error", LOCK_MESSAGE) in _flashes(client)


# ---------------------------------------------------------------- 2. POST /analyze

def test_analyze(actor, app, monkeypatch):
    state, client, _ = actor
    import app.routes.analyze_routes as analyze_routes

    handled, ip_checks, verdict_logs, decisions = [], [], [], []
    real_ip_check = analyze_routes.check_and_increment_ip_rate_limit

    def spy_ip_check(*args, **kwargs):
        ip_checks.append(1)
        return real_ip_check(*args, **kwargs)

    def fake_handle(data, **kwargs):
        handled.append(kwargs)
        return api_ok({"stub": True})

    monkeypatch.setattr(analyze_routes, "check_and_increment_ip_rate_limit", spy_ip_check)
    monkeypatch.setattr(analyze_routes, "log_product_call_verdict_input", lambda **kw: verdict_logs.append(kw))
    monkeypatch.setattr(analyze_routes, "log_access_decision", lambda *a: decisions.append(a))
    monkeypatch.setattr(analyze_routes.analyze_service, "handle_analyze_request", fake_handle)

    resp = client.post(
        "/analyze",
        json={"make": "Toyota", "model": "Corolla", "year": 2020, "legal_confirm": True},
        headers={"Origin": "http://localhost"},
    )
    with app.app_context():
        ip_rows = IpRateLimit.query.count()

    if state in ALLOWED:
        assert resp.status_code == 200
        assert len(handled) == 1
        assert handled[0]["bypass_owner"] is (state == "owner")
        assert len(ip_checks) == 1 and ip_rows == 1
        assert len(verdict_logs) == 1
        return
    assert resp.status_code in (401, 403)
    assert handled == []
    assert ip_checks == [] and ip_rows == 0
    assert verdict_logs == []
    if state == "user":
        body = resp.get_json()
        assert resp.status_code == 403
        assert body["error"]["code"] == "forbidden"
        assert body["error"]["message"] == LOCK_MESSAGE
        assert decisions == [("/analyze", actor[2], "rejected", "owner only")]


# ---------------------------------------------------------------- 3. POST /reliability_report

def test_reliability_report_stays_gone(actor):
    state, client, _ = actor
    resp = client.post("/reliability_report", json={})
    assert resp.status_code == (401 if state == "anon" else 410)


# ---------------------------------------------------------------- 4. GET /example/<slug>

def test_example_detail(actor, app, monkeypatch):
    state, client, _ = actor
    _add_public_example(app)
    if state not in ALLOWED:
        monkeypatch.setattr("app.routes.public_examples_routes.SearchHistory", _NoDb())
    resp = client.get("/example/toyota-corolla-2020")
    assert resp.status_code == (200 if state in ALLOWED else 404)


# ---------------------------------------------------------------- 5. GET /api/examples

def test_api_examples(actor, app, monkeypatch):
    state, client, _ = actor
    _add_public_example(app)
    if state not in ALLOWED:
        monkeypatch.setattr("app.routes.public_examples_routes.SearchHistory", _NoDb())
    resp = client.get("/api/examples")
    assert resp.status_code == 200
    examples = resp.get_json()["data"]["examples"]
    if state in ALLOWED:
        assert [e["slug"] for e in examples] == ["toyota-corolla-2020"]
    else:
        assert examples == []


# ---------------------------------------------------------------- 6. GET /search-details/<id> (+ history API)

@pytest.mark.parametrize("path", ["/search-details/{id}", "/api/history/item/{id}", "/api/history/list"])
def test_search_history_reads(actor, app, path):
    state, client, user_id = actor
    search_id = _add_search(app, user_id) if user_id else 1
    resp = client.get(path.format(id=search_id))
    if state == "anon":
        assert resp.status_code in (302, 401)
    elif state in ALLOWED:
        assert resp.status_code == 200
    else:
        assert resp.status_code == 404


# ---------------------------------------------------------------- 7. POST /api/feedback

def test_feedback_on_search_history(actor, app):
    state, client, user_id = actor
    search_id = _add_search(app, user_id) if user_id else 1
    resp = client.post("/api/feedback", json={"is_positive": True, "search_history_id": search_id})
    with app.app_context():
        stored = Feedback.query.count()
    if state == "anon":
        assert resp.status_code in (302, 401)
        assert stored == 0
    elif state in ALLOWED:
        assert resp.status_code == 200
        assert stored == 1
    else:
        assert resp.status_code == 403
        assert resp.get_json()["error"]["code"] == "forbidden"
        assert stored == 0


def test_feedback_without_search_history_stays_open(app, client):
    app.config["OWNER_EMAILS"] = set()
    _login(app, client)
    resp = client.post("/api/feedback", json={"is_positive": False})
    assert resp.status_code == 200


# ---------------------------------------------------------------- 8. GET /dashboard

def test_dashboard(actor, app):
    state, client, user_id = actor
    if user_id:
        _add_search(app, user_id)
    resp = client.get("/dashboard")
    if state == "anon":
        assert resp.status_code == 302
        return
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'id="tab-comparisons"' in html and 'id="tab-advisor"' in html
    if state in ALLOWED:
        assert 'id="tab-reliability"' in html
        assert "data-search-id=" in html
    else:
        assert 'id="tab-reliability"' not in html
        assert 'id="tab-content-reliability"' not in html
        assert "data-search-id=" not in html
        assert 'class="dashboard-tab is-active whitespace-nowrap"' in html.split('id="tab-comparisons"')[1][:80]
        assert 'id="tab-content-comparisons" data-tab-panel="comparisons" class="relative z-10 space-y-4"' in html


# ---------------------------------------------------------------- 9. Post-login redirect

class _UserInfo:
    ok = True
    status_code = 200
    text = ""

    @staticmethod
    def json():
        return {"id": "lock-oauth-id", "email": OWNER_EMAIL, "name": "OAuth User"}


@pytest.fixture
def google(monkeypatch):
    google_client = oauth.google
    monkeypatch.setattr(google_client, "client_id", "test-client-id", raising=False)
    monkeypatch.setattr(
        google_client,
        "fetch_access_token",
        lambda redirect_uri=None, **kw: {"access_token": "t", "token_type": "Bearer", "expires_in": 3600},
    )
    monkeypatch.setattr(google_client, "get", lambda path, **kw: _UserInfo())
    monkeypatch.setattr("app.routes.public_routes.track_event", lambda *a, **k: None)


@pytest.mark.parametrize("state", ["user", "owner", "open"])
def test_post_login_redirect(app, client, google, state):
    app.config["OWNER_EMAILS"] = {OWNER_EMAIL} if state == "owner" else set()
    app.config["RELIABILITY_OWNER_ONLY"] = state != "open"
    login = client.get("/login")
    state_param = parse_qs(urlparse(login.headers["Location"]).query)["state"][0]
    resp = client.get(f"/auth?code=test-code&state={state_param}")
    assert resp.status_code == 302
    expected = "/app" if state in ALLOWED else "/compare"
    assert urlparse(resp.headers["Location"]).path == expected
    assert _flashes(client) == []


# ---------------------------------------------------------------- 10. GET /api/timing/estimate

def test_timing_estimate_unchanged(actor):
    state, client, _ = actor
    resp = client.get("/api/timing/estimate?kind=analyze")
    assert resp.status_code == (302 if state == "anon" else 200)


# ---------------------------------------------------------------- UI: no /app links for non-owners

@pytest.mark.parametrize("path", ["/", "/dashboard", "/compare", "/recommendations", "/coming-soon"])
def test_pages_link_to_app_only_when_allowed(actor, app, path):
    state, client, _ = actor
    # The advisor page has its own owner lock; open it to check only this one.
    app.config["ADVISOR_OWNER_ONLY"] = False
    resp = client.get(path)
    if state == "anon" and path == "/dashboard":
        assert resp.status_code == 302
        return
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    if state in ALLOWED:
        assert APP_HREF.search(html)
    else:
        assert not APP_HREF.search(html)
        assert "סקירת רכב</a>" not in html


def test_landing_shows_two_cards_for_non_owner(actor):
    state, client, _ = actor
    html = client.get("/").get_data(as_text=True)
    assert "השוואת רכבים" in html and "התאמת רכב אישית" in html
    if state in ALLOWED:
        assert "בדיקת אמינות" in html and "עלויות וסיכונים" in html
        assert "ארבעה תחומי בדיקה מרכזיים" in html
    else:
        assert "בדיקת אמינות" not in html and "עלויות וסיכונים" not in html
        assert "שני תחומי בדיקה מרכזיים" in html


def test_coming_soon_button(actor):
    state, client, _ = actor
    html = client.get("/coming-soon").get_data(as_text=True)
    if state in ALLOWED:
        assert "לעבור לסקירת הרכב" in html
    else:
        assert "לעבור לסקירת הרכב" not in html
        assert 'href="/compare" class="yr-btn">לעבור להשוואת רכבים</a>' in html
