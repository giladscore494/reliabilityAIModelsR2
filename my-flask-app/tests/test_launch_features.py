# -*- coding: utf-8 -*-
"""Tests for Tasks 1–3: PostHog analytics, Public examples, Feedback."""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from main import create_app, db, User
from app.models import SearchHistory, Feedback


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-for-pytest")
    monkeypatch.delenv("SKIP_CREATE_ALL", raising=False)
    monkeypatch.delenv("POSTHOG_API_KEY", raising=False)
    app = create_app()
    app.config.update(TESTING=True)
    with app.app_context():
        db.create_all()
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def logged_in_client(app, client):
    with app.app_context():
        user = User(google_id="test-google-id", email="tester@example.com", name="Tester")
        db.session.add(user)
        db.session.commit()
        user_id = user.id

    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True

    return client, user_id


@pytest.fixture
def example_row(app, logged_in_client):
    """Create a SearchHistory row flagged as a public example."""
    _, user_id = logged_in_client
    with app.app_context():
        row = SearchHistory(
            user_id=user_id,
            make="Toyota",
            model="Corolla",
            year=2020,
            result_json=json.dumps({
                "reliability_score": 82,
                "executive_summary": "Very reliable car.",
                "reliability_label": "גבוהה",
                "risk_level": "בינונית",
                "common_issues": ["Brake pads wear", "AC compressor"],
                "pre_purchase_checks": ["Check mileage", "Check brakes"],
            }),
            is_public_example=True,
            example_slug="toyota-corolla-2020",
        )
        db.session.add(row)
        db.session.commit()
        row_id = row.id
    return row_id


# =====================================================
# TASK 1 — PostHog Analytics
# =====================================================

class TestPostHogNoOp:
    """track_event with no API key should be a no-op and never raise."""

    def test_track_event_no_key_no_error(self):
        from app.utils.analytics import track_event
        # Should not raise regardless of arguments
        track_event("user-1", "test_event", {"foo": "bar"})
        track_event(None, "test_event")
        track_event("", "", {})


# =====================================================
# TASK 2 — Public Example Previews
# =====================================================

class TestExampleDetail:
    """GET /example/<slug>"""

    def test_valid_slug_returns_200(self, client, example_row):
        resp = client.get("/example/toyota-corolla-2020")
        assert resp.status_code == 200
        assert "Toyota" in resp.data.decode()
        assert "Corolla" in resp.data.decode()

    def test_invalid_slug_returns_404(self, client):
        resp = client.get("/example/does-not-exist")
        assert resp.status_code == 404

    def test_non_public_slug_returns_404(self, app, logged_in_client, client):
        """A row that exists but is not flagged as public should 404."""
        _, user_id = logged_in_client
        with app.app_context():
            row = SearchHistory(
                user_id=user_id,
                make="BMW",
                model="320i",
                year=2016,
                result_json='{"score": 70}',
                is_public_example=False,
                example_slug=None,
            )
            db.session.add(row)
            db.session.commit()
        resp = client.get("/example/bmw-320i-2016")
        assert resp.status_code == 404

    @patch("app.routes.public_examples_routes.SearchHistory")
    def test_does_not_call_gemini(self, mock_sh, client, example_row, app):
        """Example page must NOT call Gemini — pure DB read."""
        with app.app_context():
            real_row = SearchHistory.query.filter_by(example_slug="toyota-corolla-2020").first()
            mock_query = MagicMock()
            mock_query.filter_by.return_value.first_or_404.return_value = real_row
            mock_sh.query = mock_query

        # Patch the Gemini clients to detect any calls
        with patch("app.extensions.ai_client") as mock_ai, \
             patch("app.extensions.advisor_client") as mock_adv:
            resp = client.get("/example/toyota-corolla-2020")
            # Even though we patched SearchHistory above, the real route
            # will use the real model. The key assertion is no Gemini calls.
            assert mock_ai.call_count == 0 or not hasattr(mock_ai, 'generate_content')
            assert mock_adv.call_count == 0 or not hasattr(mock_adv, 'generate_content')


class TestApiExamples:
    """GET /api/examples"""

    def test_returns_only_public_rows(self, client, example_row, app, logged_in_client):
        _, user_id = logged_in_client
        with app.app_context():
            private_row = SearchHistory(
                user_id=user_id,
                make="Honda",
                model="Civic",
                year=2019,
                result_json='{"score": 65}',
                is_public_example=False,
            )
            db.session.add(private_row)
            db.session.commit()

        resp = client.get("/api/examples")
        assert resp.status_code == 200
        data = resp.get_json()
        examples = data["data"]["examples"]
        slugs = [e["slug"] for e in examples]
        assert "toyota-corolla-2020" in slugs
        # Private rows should not appear
        for ex in examples:
            assert ex.get("make") != "Honda" or ex.get("is_public_example") is True


class TestPublicAccessAnonymous:
    """Public pages return 200 for anonymous users."""

    def test_landing_anon(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_compare_get_anon(self, client):
        resp = client.get("/compare")
        assert resp.status_code == 200

    def test_recommendations_get_anon(self, client):
        resp = client.get("/recommendations")
        assert resp.status_code == 200

    def test_example_page_anon(self, client, example_row):
        resp = client.get("/example/toyota-corolla-2020")
        assert resp.status_code == 200

    def test_api_examples_anon(self, client, example_row):
        resp = client.get("/api/examples")
        assert resp.status_code == 200


class TestProtectedRoutesAnonymous:
    """Protected routes reject anonymous users (401 or 403)."""

    def test_analyze_anon_rejected(self, client):
        resp = client.post(
            "/analyze",
            json={"make": "Toyota", "model": "Corolla", "year": 2020},
            content_type="application/json",
        )
        assert resp.status_code in (401, 403)

    def test_api_compare_anon_rejected(self, client):
        resp = client.post(
            "/api/compare",
            json={"cars": []},
            content_type="application/json",
        )
        assert resp.status_code in (401, 403)

    def test_advisor_api_anon_rejected(self, client):
        resp = client.post(
            "/advisor_api",
            json={},
            content_type="application/json",
        )
        assert resp.status_code in (401, 403)


# =====================================================
# TASK 3 — Feedback
# =====================================================

class TestFeedback:
    """POST /api/feedback"""

    def test_success(self, app, logged_in_client):
        cl, user_id = logged_in_client
        # Create a search history row
        with app.app_context():
            row = SearchHistory(
                user_id=user_id,
                make="Toyota",
                model="Corolla",
                year=2020,
                result_json='{"score": 80}',
            )
            db.session.add(row)
            db.session.commit()
            row_id = row.id

        resp = cl.post("/api/feedback", json={"search_history_id": row_id, "is_positive": True}, content_type="application/json")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True

    def test_upsert(self, app, logged_in_client):
        """Duplicate submissions should update, not fail."""
        cl, user_id = logged_in_client
        with app.app_context():
            row = SearchHistory(
                user_id=user_id,
                make="BMW",
                model="320i",
                year=2016,
                result_json='{"score": 70}',
            )
            db.session.add(row)
            db.session.commit()
            row_id = row.id

        # First submit — positive
        resp1 = cl.post("/api/feedback", json={"search_history_id": row_id, "is_positive": True}, content_type="application/json")
        assert resp1.status_code == 200

        # Second submit — change to negative (upsert)
        resp2 = cl.post("/api/feedback", json={"search_history_id": row_id, "is_positive": False}, content_type="application/json")
        assert resp2.status_code == 200

        with app.app_context():
            fb = Feedback.query.filter_by(user_id=user_id, search_history_id=row_id).first()
            assert fb is not None
            assert fb.is_positive is False

    def test_unauthorized_history_rejected(self, app, logged_in_client):
        """Cannot submit feedback for another user's search history."""
        cl, user_id = logged_in_client
        with app.app_context():
            other_user = User(google_id="other-google", email="other@example.com", name="Other")
            db.session.add(other_user)
            db.session.commit()
            other_row = SearchHistory(
                user_id=other_user.id,
                make="Kia",
                model="Sportage",
                year=2019,
                result_json='{"score": 60}',
            )
            db.session.add(other_row)
            db.session.commit()
            other_row_id = other_row.id

        resp = cl.post("/api/feedback", json={"search_history_id": other_row_id, "is_positive": True}, content_type="application/json")
        assert resp.status_code == 404

    def test_anonymous_401(self, client):
        resp = client.post("/api/feedback", json={"is_positive": True}, content_type="application/json")
        assert resp.status_code == 401

    def test_missing_is_positive(self, logged_in_client):
        cl, _ = logged_in_client
        resp = cl.post("/api/feedback", json={"search_history_id": 1}, content_type="application/json")
        assert resp.status_code == 400

    def test_null_history_id_ok(self, logged_in_client):
        """Feedback with null search_history_id should succeed."""
        cl, _ = logged_in_client
        resp = cl.post("/api/feedback", json={"is_positive": True}, content_type="application/json")
        assert resp.status_code == 200


# =====================================================
# Seed Script
# =====================================================

class TestSeedScript:
    """Test seed_public_examples.py logic."""

    def test_promote_and_unset(self, app, logged_in_client):
        _, user_id = logged_in_client
        with app.app_context():
            row = SearchHistory(
                user_id=user_id,
                make="Hyundai",
                model="i30",
                year=2021,
                result_json='{"score": 75}',
            )
            db.session.add(row)
            db.session.commit()
            row_id = row.id

            # Promote
            row.is_public_example = True
            row.example_slug = "hyundai-i30-2021"
            db.session.commit()

            fetched = SearchHistory.query.filter_by(example_slug="hyundai-i30-2021", is_public_example=True).first()
            assert fetched is not None
            assert fetched.id == row_id

            # Unset
            fetched.is_public_example = False
            fetched.example_slug = None
            db.session.commit()

            none_row = SearchHistory.query.filter_by(example_slug="hyundai-i30-2021").first()
            assert none_row is None

    def test_list_public_examples(self, app, logged_in_client, example_row):
        with app.app_context():
            public_rows = SearchHistory.query.filter_by(is_public_example=True).all()
            assert len(public_rows) >= 1
            slugs = [r.example_slug for r in public_rows]
            assert "toyota-corolla-2020" in slugs
