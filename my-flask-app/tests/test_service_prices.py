# -*- coding: utf-8 -*-
"""Tests for Service Price Check feature."""

from datetime import datetime
from io import BytesIO
from unittest.mock import patch, MagicMock

import pytest

from app.legal import (
    TERMS_VERSION, PRIVACY_VERSION,
    INVOICE_FEATURE_KEY, INVOICE_FEATURE_CONSENT_VERSION,
    has_accepted_feature, record_feature_acceptance,
)
from app.models import LegalAcceptance, LegalFeatureAcceptance, ServiceInvoice, User
from app.services.service_prices_service import (
    compute_percentiles, canonicalize_line_items, normalize_text,
    deterministic_sanitize_no_pii, parse_price,
)
from main import db


# ============================================
# AUTH TESTS
# ============================================

def test_service_prices_page_requires_login(client):
    """GET /service-prices without login should redirect to login."""
    resp = client.get("/service-prices")
    assert resp.status_code in (302, 401)


def test_service_prices_api_requires_login(client):
    """POST /api/service-prices/analyze without login should return 401."""
    resp = client.post("/api/service-prices/analyze")
    assert resp.status_code in (302, 401)


def test_service_prices_page_accessible_when_logged_in(logged_in_client):
    """GET /service-prices should be accessible when logged in."""
    client, _ = logged_in_client
    resp = client.get("/service-prices")
    assert resp.status_code == 200
    assert "בדיקת מחירי טיפול" in resp.data.decode("utf-8")


# ============================================
# LEGAL ACCEPTANCE GATING TESTS
# ============================================

def test_analyze_invoice_requires_legal_acceptance(logged_in_client, app):
    """POST /api/service-prices/analyze without legal acceptance should return 428 LEGAL_ACCEPTANCE_REQUIRED."""
    client, user_id = logged_in_client
    
    # Create a dummy image
    image_data = BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    
    resp = client.post(
        "/api/service-prices/analyze",
        data={
            "invoice_image": (image_data, "test.png", "image/png"),
        },
        content_type="multipart/form-data",
    )
    
    assert resp.status_code in (403, 428)
    data = resp.get_json()
    assert data.get("error", {}).get("code") == "LEGAL_ACCEPTANCE_REQUIRED"
    assert "required" in data


# ============================================
# FEATURE CONSENT GATING TESTS
# ============================================

def test_analyze_invoice_requires_feature_consent(logged_in_client, app):
    """POST /api/service-prices/analyze with legal but without feature consent should return 428 FEATURE_CONSENT_REQUIRED."""
    client, user_id = logged_in_client
    
    # Accept legal terms
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
    
    # Create a dummy image
    image_data = BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    
    resp = client.post(
        "/api/service-prices/analyze",
        data={
            "invoice_image": (image_data, "test.png", "image/png"),
        },
        content_type="multipart/form-data",
    )
    
    assert resp.status_code in (403, 428)
    data = resp.get_json()
    assert data.get("error", {}).get("code") == "FEATURE_CONSENT_REQUIRED"
    assert data.get("required", {}).get("feature_key") == INVOICE_FEATURE_KEY


def test_vision_not_called_without_feature_consent(logged_in_client, app):
    """Ensure vision_extract_invoice is NOT called when feature consent is missing."""
    client, user_id = logged_in_client
    
    # Accept legal terms
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
        image_data = BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        
        resp = client.post(
            "/api/service-prices/analyze",
            data={
                "invoice_image": (image_data, "test.png", "image/png"),
            },
            content_type="multipart/form-data",
        )
        
        # Vision should NOT be called
        mock_vision.assert_not_called()


# ============================================
# ACCEPTANCE ENDPOINT IDEMPOTENCY TESTS
# ============================================

def test_feature_consent_acceptance_is_idempotent(logged_in_client, app):
    """POST /api/legal/accept with feature_consents twice should create only one row."""
    client, user_id = logged_in_client
    
    # First acceptance
    resp = client.post("/api/legal/accept", json={
        "legal_confirm": True,
        "feature_consents": [
            {"feature_key": INVOICE_FEATURE_KEY, "version": INVOICE_FEATURE_CONSENT_VERSION}
        ]
    })
    assert resp.status_code == 200
    
    with app.app_context():
        count = LegalFeatureAcceptance.query.filter_by(
            user_id=user_id,
            feature_key=INVOICE_FEATURE_KEY,
            version=INVOICE_FEATURE_CONSENT_VERSION,
        ).count()
        assert count == 1
    
    # Second acceptance (should be idempotent)
    resp = client.post("/api/legal/accept", json={
        "legal_confirm": True,
        "feature_consents": [
            {"feature_key": INVOICE_FEATURE_KEY, "version": INVOICE_FEATURE_CONSENT_VERSION}
        ]
    })
    assert resp.status_code == 200
    
    with app.app_context():
        count = LegalFeatureAcceptance.query.filter_by(
            user_id=user_id,
            feature_key=INVOICE_FEATURE_KEY,
            version=INVOICE_FEATURE_CONSENT_VERSION,
        ).count()
        assert count == 1


def test_has_accepted_feature_returns_false_initially(logged_in_client, app):
    """has_accepted_feature should return False for new users."""
    _, user_id = logged_in_client
    
    with app.app_context():
        assert has_accepted_feature(user_id, INVOICE_FEATURE_KEY, INVOICE_FEATURE_CONSENT_VERSION) is False


def test_has_accepted_feature_returns_true_after_acceptance(logged_in_client, app):
    """has_accepted_feature should return True after recording acceptance."""
    _, user_id = logged_in_client
    
    with app.app_context():
        record_feature_acceptance(user_id, INVOICE_FEATURE_KEY, INVOICE_FEATURE_CONSENT_VERSION)
        assert has_accepted_feature(user_id, INVOICE_FEATURE_KEY, INVOICE_FEATURE_CONSENT_VERSION) is True


# ============================================
# PERCENTILES DETERMINISTIC CORRECTNESS TESTS
# ============================================

def test_compute_percentiles_empty_list():
    """compute_percentiles with empty list should return None values."""
    result = compute_percentiles([])
    assert result["p50"] is None
    assert result["p75"] is None
    assert result["p90"] is None


def test_compute_percentiles_single_value():
    """compute_percentiles with single value should return that value for all percentiles."""
    result = compute_percentiles([500])
    assert result["p50"] == 500
    assert result["p75"] == 500
    assert result["p90"] == 500


def test_compute_percentiles_known_values():
    """compute_percentiles should return correct percentiles for known data."""
    # 10 values: 100, 200, 300, 400, 500, 600, 700, 800, 900, 1000
    prices = [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]
    result = compute_percentiles(prices)
    
    # p50 (median) should be 550 (linear interpolation between 500 and 600)
    assert result["p50"] == 550
    # p75 should be at position 6.75, between 700 and 800
    assert 700 <= result["p75"] <= 800
    # p90 should be at position 8.1, between 900 and 1000
    assert 800 <= result["p90"] <= 1000


def test_compute_percentiles_unsorted_input():
    """compute_percentiles should work with unsorted input."""
    prices = [500, 100, 300, 200, 400]
    result = compute_percentiles(prices)
    assert result["p50"] == 300  # Median of 100, 200, 300, 400, 500


# ============================================
# CANONICALIZATION TESTS
# ============================================

def test_normalize_text():
    """normalize_text should lowercase, strip, and unify Hebrew final letters."""
    assert normalize_text("שֶׁמֶן") == "שמנ"  # Final nun -> regular nun
    assert normalize_text("  OIL  ") == "oil"
    assert normalize_text("Oil-Change") == "oilchange"


def test_parse_price():
    """parse_price should handle various formats."""
    assert parse_price(500) == 500
    assert parse_price("500") == 500
    assert parse_price("₪500") == 500
    assert parse_price("1,500") == 1500
    assert parse_price("1,500.50") == 1500  # Rounded (int)
    assert parse_price(None) is None
    assert parse_price("invalid") is None


def test_canonicalize_line_items_groups_by_code():
    """canonicalize_line_items should group items by canonical code."""
    items = [
        {"description": "החלפת שמן", "price_ils": 300},
        {"description": "שמן מנוע", "price_ils": 200},
    ]
    result = canonicalize_line_items(items)
    
    # Should be grouped into single oil_change entry
    oil_changes = [r for r in result if r["canonical_code"] == "oil_change"]
    assert len(oil_changes) == 1
    assert oil_changes[0]["price_ils"] == 500  # Sum of prices


def test_canonicalize_line_items_detects_labor():
    """canonicalize_line_items should detect labor items."""
    items = [
        {"description": "עבודה התקנה", "price_ils": 200},
    ]
    result = canonicalize_line_items(items)
    
    assert len(result) == 1
    assert result[0]["labor_ils"] == 200
    assert result[0]["parts_ils"] == 0


# ============================================
# SANITIZATION TESTS
# ============================================

def test_deterministic_sanitize_no_pii_redacts_phone():
    """deterministic_sanitize_no_pii should redact phone numbers."""
    obj = {"phone": "050-123-4567", "name": "Test"}
    result = deterministic_sanitize_no_pii(obj)
    assert "[REDACTED]" in result["phone"]


def test_deterministic_sanitize_no_pii_redacts_email():
    """deterministic_sanitize_no_pii should redact email addresses."""
    obj = {"email": "test@example.com", "name": "Test"}
    result = deterministic_sanitize_no_pii(obj)
    assert "[REDACTED]" in result["email"]


def test_deterministic_sanitize_no_pii_handles_nested():
    """deterministic_sanitize_no_pii should handle nested structures."""
    obj = {
        "outer": {
            "phone": "050-111-2222",
            "inner": {
                "email": "nested@test.com"
            }
        },
        "list": ["052-333-4444"]
    }
    result = deterministic_sanitize_no_pii(obj)
    assert "[REDACTED]" in result["outer"]["phone"]
    assert "[REDACTED]" in result["outer"]["inner"]["email"]
    assert "[REDACTED]" in result["list"][0]


# ============================================
# SUCCESS PATH TEST (with mocked AI)
# ============================================

def test_analyze_invoice_success_path(logged_in_client, app):
    """Full success path: legal + feature consent + valid file -> persists and returns report."""
    client, user_id = logged_in_client
    
    # Setup: accept legal and feature consent
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
        record_feature_acceptance(user_id, INVOICE_FEATURE_KEY, INVOICE_FEATURE_CONSENT_VERSION)
        db.session.commit()
        
        initial_count = User.query.get(user_id).service_price_checks_count or 0
    
    # Mock the vision extraction
    mock_extraction = {
        "car": {"make": "Toyota", "model": "Corolla", "year": 2020, "mileage": 80000},
        "invoice": {"date": "2026-01-15", "total_price_ils": 1500, "region": "center", "garage_type": "dealer"},
        "line_items": [
            {"description": "החלפת שמן", "price_ils": 400, "qty": 1},
            {"description": "פילטר אוויר", "price_ils": 200, "qty": 1},
        ],
        "redaction": {"applied": True, "notes": "test"},
        "confidence": {"overall": 0.95},
    }
    
    with patch("app.services.service_prices_service.vision_extract_invoice", return_value=mock_extraction):
        image_data = BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        
        resp = client.post(
            "/api/service-prices/analyze",
            data={
                "invoice_image": (image_data, "test.png", "image/png"),
            },
            content_type="multipart/form-data",
        )
    
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert "invoice_id" in data.get("data", {})
    assert "report" in data.get("data", {})
    
    # Verify persistence
    with app.app_context():
        invoice_id = data["data"]["invoice_id"]
        invoice = ServiceInvoice.query.get(invoice_id)
        assert invoice is not None
        assert invoice.user_id == user_id
        assert invoice.make == "Toyota"
        assert len(invoice.items) > 0
        
        # Verify user counter incremented
        user = User.query.get(user_id)
        assert user.service_price_checks_count == initial_count + 1


def test_download_report_works(logged_in_client, app):
    """GET /api/service-prices/download/<id> should return JSON file."""
    client, user_id = logged_in_client
    
    # Create a mock invoice
    with app.app_context():
        invoice = ServiceInvoice(
            user_id=user_id,
            make="Test",
            model="Car",
            year=2020,
            parsed_json='{"test": true}',
            report_json='{"fairness_score": 75}',
        )
        db.session.add(invoice)
        db.session.commit()
        invoice_id = invoice.id
    
    resp = client.get(f"/api/service-prices/download/{invoice_id}")
    assert resp.status_code == 200
    assert resp.content_type == "application/json"
    assert b"fairness_score" in resp.data


def test_download_report_not_found_for_other_user(logged_in_client, app):
    """GET /api/service-prices/download/<id> should return 404 for another user's invoice."""
    client, user_id = logged_in_client
    
    # Create invoice for a different user
    with app.app_context():
        other_user = User(google_id="other-google-id", email="other@example.com", name="Other")
        db.session.add(other_user)
        db.session.commit()
        
        invoice = ServiceInvoice(
            user_id=other_user.id,
            make="Test",
            model="Car",
            year=2020,
            parsed_json='{"test": true}',
            report_json='{"fairness_score": 75}',
        )
        db.session.add(invoice)
        db.session.commit()
        invoice_id = invoice.id
    
    resp = client.get(f"/api/service-prices/download/{invoice_id}")
    assert resp.status_code == 404
