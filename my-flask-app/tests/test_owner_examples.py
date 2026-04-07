# -*- coding: utf-8 -*-
"""Tests for the Owner Examples Management UI."""

import json
import os
import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from main import create_app, db, User
from app.models import SearchHistory


OWNER_EMAIL = "owner@example.com"


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-for-pytest")
    monkeypatch.setenv("OWNER_EMAIL", OWNER_EMAIL)
    monkeypatch.delenv("SKIP_CREATE_ALL", raising=False)
    a = create_app()
    a.config.update(TESTING=True)
    with a.app_context():
        db.create_all()
    yield a
    with a.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def _make_user(app, email, name="Test"):
    with app.app_context():
        user = User(
            google_id=f"gid-{email}",
            email=email,
            name=name,
        )
        db.session.add(user)
        db.session.commit()
        uid = user.id
    return uid


def _login_as(client, user_id):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True


def _make_search_row(app, user_id, make="Toyota", model="Corolla", year=2020):
    with app.app_context():
        row = SearchHistory(
            user_id=user_id,
            make=make,
            model=model,
            year=year,
            result_json=json.dumps({"score": 80}),
        )
        db.session.add(row)
        db.session.commit()
        rid = row.id
    return rid


class TestOwnerExamplesGET:
    """GET /owner/examples"""

    def test_anonymous_redirect(self, client):
        resp = client.get("/owner/examples")
        assert resp.status_code in (302, 401)

    def test_non_owner_gets_404(self, app, client):
        uid = _make_user(app, "regular@example.com")
        _login_as(client, uid)
        resp = client.get("/owner/examples")
        assert resp.status_code == 404

    def test_owner_gets_200(self, app, client):
        uid = _make_user(app, OWNER_EMAIL)
        _login_as(client, uid)
        resp = client.get("/owner/examples")
        assert resp.status_code == 200
        assert "ניהול דוגמאות" in resp.data.decode()


class TestOwnerExamplesUpdate:
    """POST /owner/examples/update"""

    def test_non_owner_gets_404(self, app, client):
        uid = _make_user(app, "regular@example.com")
        _login_as(client, uid)
        resp = client.post(
            "/owner/examples/update",
            json={"selections": []},
            content_type="application/json",
        )
        assert resp.status_code == 404

    def test_valid_selections(self, app, client):
        uid = _make_user(app, OWNER_EMAIL)
        _login_as(client, uid)
        r1_id = _make_search_row(app, uid, "Toyota", "Corolla", 2020)
        r2_id = _make_search_row(app, uid, "BMW", "320i", 2016)

        resp = client.post(
            "/owner/examples/update",
            json={
                "selections": [
                    {"history_id": r1_id, "slug": "toyota-corolla-2020"},
                    {"history_id": r2_id, "slug": "bmw-320i-2016"},
                ]
            },
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["data"]["count"] == 2

        # Verify DB state
        with app.app_context():
            pub = SearchHistory.query.filter_by(
                is_public_example=True,
            ).all()
            slugs = {r.example_slug for r in pub}
            assert slugs == {"toyota-corolla-2020", "bmw-320i-2016"}

    def test_replaces_old_examples(self, app, client):
        """Re-submitting with different selections clears old ones."""
        uid = _make_user(app, OWNER_EMAIL)
        _login_as(client, uid)
        r1_id = _make_search_row(app, uid, "Toyota", "Corolla", 2020)
        r2_id = _make_search_row(app, uid, "BMW", "320i", 2016)
        r3_id = _make_search_row(app, uid, "Kia", "Sportage", 2019)

        # First: set r1 + r2
        client.post(
            "/owner/examples/update",
            json={
                "selections": [
                    {"history_id": r1_id, "slug": "toyota-corolla-2020"},
                    {"history_id": r2_id, "slug": "bmw-320i-2016"},
                ]
            },
            content_type="application/json",
        )

        # Second: set only r3
        resp = client.post(
            "/owner/examples/update",
            json={
                "selections": [
                    {"history_id": r3_id, "slug": "kia-sportage-2019"},
                ]
            },
            content_type="application/json",
        )
        assert resp.status_code == 200

        with app.app_context():
            pub = SearchHistory.query.filter_by(
                is_public_example=True,
            ).all()
            assert len(pub) == 1
            assert pub[0].example_slug == "kia-sportage-2019"

    def test_other_users_history_rejected(self, app, client):
        """Cannot promote another user's SearchHistory row."""
        uid = _make_user(app, OWNER_EMAIL)
        other_uid = _make_user(app, "other@example.com")
        _login_as(client, uid)
        other_rid = _make_search_row(app, other_uid, "Honda", "Civic", 2019)

        resp = client.post(
            "/owner/examples/update",
            json={
                "selections": [
                    {"history_id": other_rid, "slug": "honda-civic-2019"},
                ]
            },
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_five_selections_rejected(self, app, client):
        uid = _make_user(app, OWNER_EMAIL)
        _login_as(client, uid)
        ids = [
            _make_search_row(app, uid, "Car", f"Model{i}", 2020)
            for i in range(5)
        ]

        resp = client.post(
            "/owner/examples/update",
            json={
                "selections": [
                    {"history_id": ids[i], "slug": f"car-model{i}-2020"}
                    for i in range(5)
                ]
            },
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_invalid_slug_rejected(self, app, client):
        uid = _make_user(app, OWNER_EMAIL)
        _login_as(client, uid)
        r_id = _make_search_row(app, uid)

        resp = client.post(
            "/owner/examples/update",
            json={
                "selections": [
                    {"history_id": r_id, "slug": "INVALID SLUG!"},
                ]
            },
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_empty_selections_clears_all(self, app, client):
        """Empty selections list → zero public examples."""
        uid = _make_user(app, OWNER_EMAIL)
        _login_as(client, uid)
        r_id = _make_search_row(app, uid)

        with app.app_context():
            row = SearchHistory.query.get(r_id)
            row.is_public_example = True
            row.example_slug = "test-slug"
            db.session.commit()

        resp = client.post(
            "/owner/examples/update",
            json={"selections": []},
            content_type="application/json",
        )
        assert resp.status_code == 200

        with app.app_context():
            pub = SearchHistory.query.filter_by(
                is_public_example=True,
            ).all()
            assert len(pub) == 0
