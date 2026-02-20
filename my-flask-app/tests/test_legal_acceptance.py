from datetime import datetime

from app.legal import TERMS_VERSION, PRIVACY_VERSION
from app.models import LegalAcceptance
from main import db


def test_protected_endpoint_requires_acceptance(logged_in_client):
    client, _ = logged_in_client
    resp = client.post(
        "/analyze",
        json={"legal_confirm": True},
        headers={"Origin": "http://localhost"},
    )
    assert resp.status_code == 403
    data = resp.get_json()
    assert data["error"] == "TERMS_NOT_ACCEPTED"


def test_dashboard_read_only_allows_without_acceptance(logged_in_client):
    client, _ = logged_in_client
    resp = client.get("/dashboard")
    assert resp.status_code == 200


def test_legal_accept_is_idempotent(logged_in_client, app):
    client, user_id = logged_in_client
    resp = client.post("/api/legal/accept", json={"legal_confirm": True})
    assert resp.status_code == 200
    with app.app_context():
        assert LegalAcceptance.query.filter_by(user_id=user_id).count() == 1
    resp = client.post("/api/legal/accept", json={"legal_confirm": True})
    assert resp.status_code == 200
    with app.app_context():
        assert LegalAcceptance.query.filter_by(user_id=user_id).count() == 1


def test_version_mismatch_requires_reacceptance(logged_in_client, app):
    client, user_id = logged_in_client
    with app.app_context():
        db.session.add(
            LegalAcceptance(
                user_id=user_id,
                terms_version=TERMS_VERSION,
                privacy_version=PRIVACY_VERSION,
                accepted_at=datetime.utcnow(),
                accepted_ip="1.2.3.0",
            )
        )
        db.session.commit()
        app.config["TERMS_VERSION"] = "2026-02-01"
        app.config["PRIVACY_VERSION"] = "2026-02-01"
    resp = client.post(
        "/analyze",
        json={"legal_confirm": True},
        headers={"Origin": "http://localhost"},
    )
    assert resp.status_code == 403
    data = resp.get_json()
    assert data["error"] == "TERMS_VERSION_MISMATCH"


def test_delete_account_removes_legal_acceptance(logged_in_client, app):
    client, user_id = logged_in_client
    with app.app_context():
        db.session.add(
            LegalAcceptance(
                user_id=user_id,
                terms_version=TERMS_VERSION,
                privacy_version=PRIVACY_VERSION,
                accepted_at=datetime.utcnow(),
                accepted_ip="1.2.3.0",
            )
        )
        db.session.commit()
    resp = client.post(
        "/api/account/delete",
        json={"confirm": "DELETE"},
        content_type="application/json",
        headers={"Origin": "http://localhost"},
    )
    assert resp.status_code == 200
    with app.app_context():
        assert LegalAcceptance.query.filter_by(user_id=user_id).count() == 0


def test_compare_page_shows_submit_legal_checkbox(logged_in_client, app):
    client, user_id = logged_in_client
    with app.app_context():
        db.session.add(
            LegalAcceptance(
                user_id=user_id,
                terms_version=TERMS_VERSION,
                privacy_version=PRIVACY_VERSION,
                accepted_at=datetime.utcnow(),
                accepted_ip="1.2.3.0",
            )
        )
        db.session.commit()

    resp = client.get("/compare")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    assert 'id="compareLegalConfirm"' in html
    legal_checkbox_tag = html.split('id="compareLegalConfirm"')[1].split(">")[0]
    assert "checked" not in legal_checkbox_tag
    assert "disabled" not in legal_checkbox_tag


def test_compare_page_requires_reacceptance_on_version_change(logged_in_client, app):
    client, user_id = logged_in_client
    with app.app_context():
        db.session.add(
            LegalAcceptance(
                user_id=user_id,
                terms_version=TERMS_VERSION,
                privacy_version=PRIVACY_VERSION,
                accepted_at=datetime.utcnow(),
                accepted_ip="1.2.3.0",
            )
        )
        db.session.commit()
        app.config["TERMS_VERSION"] = "2026-02-01"
        app.config["PRIVACY_VERSION"] = "2026-02-01"

    resp = client.get("/compare")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    assert 'id="compareLegalConfirm"' in html
    assert "checked" not in html.split('id="compareLegalConfirm"')[1].split(">")[0]
