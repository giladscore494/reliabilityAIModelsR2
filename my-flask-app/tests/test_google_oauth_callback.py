# -*- coding: utf-8 -*-
"""Google OAuth login end to end, with only the network mocked: /login stores Authlib's state in the session and
redirects to Google; /auth validates the returned state, exchanges the code (mocked token endpoint) and logs the user
in with Flask-Login. A wrong state never logs anyone in. Guards the Authlib / Flask / Werkzeug / Flask-Login versions."""

from urllib.parse import parse_qs, urlparse

import pytest

from app.extensions import oauth
from main import User


class _UserInfo:
    ok = True
    status_code = 200
    text = ""

    @staticmethod
    def json():
        return {"id": "google-oauth-test-id", "email": "oauth-user@example.com", "name": "OAuth User"}


@pytest.fixture
def google(monkeypatch):
    """The registered Google client with a client id and the two network calls mocked (state checks stay real)."""
    client = oauth.google
    monkeypatch.setattr(client, "client_id", "test-client-id", raising=False)
    exchanges = []

    def fake_fetch_access_token(redirect_uri=None, **kwargs):
        exchanges.append({"redirect_uri": redirect_uri, **kwargs})
        return {"access_token": "test-access-token", "token_type": "Bearer", "expires_in": 3600}

    monkeypatch.setattr(client, "fetch_access_token", fake_fetch_access_token)
    monkeypatch.setattr(client, "get", lambda path, **kwargs: _UserInfo())
    monkeypatch.setattr("app.routes.public_routes.track_event", lambda *a, **k: None)
    return exchanges


def _start_login(client):
    resp = client.get("/login")
    assert resp.status_code == 302
    location = urlparse(resp.headers["Location"])
    assert location.netloc == "accounts.google.com"
    query = parse_qs(location.query)
    assert query["client_id"] == ["test-client-id"] and query["redirect_uri"][0].endswith("/auth")
    return query["state"][0]


def test_google_callback_checks_state_exchanges_the_code_and_logs_in(app, client, google):
    state = _start_login(client)
    with client.session_transaction() as sess:
        assert any(state in key for key in sess)                  # Authlib kept the state in the session
        assert "_user_id" not in sess

    resp = client.get(f"/auth?code=test-code&state={state}")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/app")
    assert len(google) == 1 and google[0]["code"] == "test-code"   # the token exchange ran once, with the code

    with app.app_context():
        user = User.query.filter_by(google_id="google-oauth-test-id").one()
        assert user.email == "oauth-user@example.com"
        user_id = str(user.id)
    with client.session_transaction() as sess:
        assert sess.get("_user_id") == user_id                    # Flask-Login session
        assert not any(state in key for key in sess)              # the state is single use
    assert client.get("/dashboard").status_code == 200            # a login_required page now opens


def test_google_callback_with_a_wrong_state_never_logs_in(app, client, google):
    _start_login(client)
    resp = client.get("/auth?code=test-code&state=forged-state")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/login")
    assert google == []                                           # no token exchange
    with client.session_transaction() as sess:
        assert "_user_id" not in sess
    with app.app_context():
        assert User.query.filter_by(google_id="google-oauth-test-id").count() == 0
