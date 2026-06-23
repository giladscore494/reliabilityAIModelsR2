# -*- coding: utf-8 -*-
"""Tests for the global exception handler in error_handlers.py."""

import json
import pytest

from main import create_app, db


@pytest.fixture
def error_app(monkeypatch):
    """Create app with a temporary route that raises RuntimeError."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-for-pytest")
    monkeypatch.delenv("SKIP_CREATE_ALL", raising=False)
    app = create_app()
    app.config.update(TESTING=True)

    # Register a temporary route that always raises.
    @app.route("/_test_error")
    def _raise():
        raise RuntimeError("deliberate test error")

    with app.app_context():
        db.create_all()
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


class TestGlobalExceptionHandler:

    def test_json_request_gets_json_500(self, error_app):
        client = error_app.test_client()
        response = client.get(
            "/_test_error",
            headers={"Accept": "application/json"},
        )
        assert response.status_code == 500
        body = response.get_json()
        assert body["ok"] is False
        assert "request_id" in body
        assert body["error"]["code"] == "server_error"

    def test_html_request_gets_safe_html_500(self, error_app):
        client = error_app.test_client()
        response = client.get(
            "/_test_error",
            headers={"Accept": "text/html"},
        )
        assert response.status_code == 500
        html = response.data.decode("utf-8")
        assert "request_id" in html
        # Must NOT expose the actual exception message
        assert "deliberate test error" not in html
        # Must contain Hebrew error text
        assert "שגיאת שרת" in html

    def test_api_route_gets_json_500(self, error_app):
        """Routes starting with /api/ should automatically get JSON responses."""

        @error_app.route("/api/_test_error")
        def _raise_api():
            raise RuntimeError("api error")

        client = error_app.test_client()
        response = client.get("/api/_test_error")
        assert response.status_code == 500
        body = response.get_json()
        assert body["ok"] is False

    def test_404_stays_404(self, error_app):
        """Normal HTTP exceptions like 404 must not be converted to 500."""
        client = error_app.test_client()
        response = client.get("/nonexistent-page-xyz")
        assert response.status_code == 404

    def test_405_stays_405(self, error_app):
        """Method Not Allowed should stay 405."""
        client = error_app.test_client()
        # POST to a GET-only route
        response = client.post("/_test_error")
        assert response.status_code == 405
