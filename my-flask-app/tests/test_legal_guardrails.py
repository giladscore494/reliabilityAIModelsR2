from datetime import datetime
from io import BytesIO
from unittest.mock import patch

from app.legal import (
    TERMS_VERSION,
    PRIVACY_VERSION,
    INVOICE_EXT_PROCESSING_KEY,
    INVOICE_EXT_PROCESSING_VERSION,
)
from app.models import LegalAcceptance
from app.utils.ai_guardrails import validate_feature_legal_access
from main import db


def test_invoice_scanner_without_feature_consent_blocks_file_read(logged_in_client, app):
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
    with patch("app.services.service_prices_service.vision_extract_invoice") as mock_vision:
        response = client.post(
            "/api/service-prices/analyze",
            data={"invoice_image": (BytesIO(b"123"), "test.png", "image/png")},
            content_type="multipart/form-data",
        )
    assert response.status_code in (403, 428)
    mock_vision.assert_not_called()


def test_old_terms_privacy_version_blocked(logged_in_client, app):
    _, user_id = logged_in_client
    with app.app_context():
        db.session.add(
            LegalAcceptance(
                user_id=user_id,
                terms_version="2025-01-01",
                privacy_version="2025-01-01",
                accepted_at=datetime.utcnow(),
                accepted_ip="1.2.3.0",
            )
        )
        db.session.commit()
        error = validate_feature_legal_access(user_id, "vehicle_comparison", "analyze")
    assert error["error"] == "LEGAL_ACCEPTANCE_REQUIRED"


def test_research_without_consent_blocked(logged_in_client, app):
    _, user_id = logged_in_client
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
        error = validate_feature_legal_access(user_id, "research_collection", "write")
    assert error["error"] == "LEGAL_ACCEPTANCE_REQUIRED"


def test_frontend_checkbox_without_backend_record_is_rejected(logged_in_client):
    client, _ = logged_in_client
    response = client.post(
        "/api/leasing/recommend",
        json={"candidates": [{"make": "Toyota", "model": "Corolla"}], "prefs": {}, "legal_confirm": True},
        headers={"Origin": "http://localhost"},
    )
    assert response.status_code == 403


def test_normal_comparison_with_base_terms_is_allowed(logged_in_client, app):
    _, user_id = logged_in_client
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
        error = validate_feature_legal_access(user_id, "vehicle_comparison", "analyze")
    assert error is None


def test_invoice_scanner_without_consent_blocks_vision_call(logged_in_client, app):
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
    with patch("app.services.service_prices_service.vision_extract_invoice_with_web_benchmarks") as mock_vision:
        response = client.post(
            "/api/service-prices/analyze",
            data={"invoice_image": (BytesIO(b"123"), "test.png", "image/png")},
            content_type="multipart/form-data",
        )
    assert response.status_code in (403, 428)
    mock_vision.assert_not_called()
