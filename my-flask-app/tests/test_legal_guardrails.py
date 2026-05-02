from datetime import datetime

from app.legal import TERMS_VERSION, PRIVACY_VERSION
from app.models import LegalAcceptance
from app.utils.ai_guardrails import validate_feature_legal_access
from main import db


def test_invoice_scanner_without_consent_blocks_file_read_vision_call(logged_in_client, app):
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
        error = validate_feature_legal_access(user_id, "invoice_scanner", "read")
    assert error["error"] == "LEGAL_ACCEPTANCE_REQUIRED"


def test_old_terms_version_blocked(logged_in_client, app):
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
        error = validate_feature_legal_access(user_id, "leasing_advisor", "analyze")
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
