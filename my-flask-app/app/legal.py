import hashlib
import os
from ipaddress import ip_address, ip_network

# General legal versions
TERMS_VERSION = os.environ.get("TERMS_VERSION", "2026-02-07")
PRIVACY_VERSION = os.environ.get("PRIVACY_VERSION", "2026-02-07")
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "support@yedaarechev.com")
LEGAL_IP_HASH_SALT = os.environ.get("LEGAL_IP_HASH_SALT", "").strip()

# Feature-specific consent constants (invoice scanner)
INVOICE_FEATURE_KEY = "invoice_scanner"
INVOICE_FEATURE_CONSENT_VERSION = os.environ.get("INVOICE_FEATURE_CONSENT_VERSION", "2026-02-07")

# Feature-specific consent constants - SPLIT into TWO consents
INVOICE_EXT_PROCESSING_KEY = "invoice_scanner_external_processing"
INVOICE_ANON_STORAGE_KEY = "invoice_scanner_anonymized_storage"
INVOICE_EXT_PROCESSING_VERSION = os.environ.get("INVOICE_EXT_PROCESSING_VERSION", "2026-02-07")
INVOICE_ANON_STORAGE_VERSION = os.environ.get("INVOICE_ANON_STORAGE_VERSION", "2026-02-07")

# Gemini Vision model for invoice OCR
GEMINI_VISION_MODEL_ID = os.environ.get("GEMINI_VISION_MODEL_ID", "gemini-2.0-flash")


def normalize_legal_ip(raw_ip: str) -> str:
    """
    Reduce IP precision before storing consent audit records.
    This keeps auditability while limiting exposure of full IP data.
    """
    if not raw_ip:
        return "unknown"
    if LEGAL_IP_HASH_SALT:
        digest = hashlib.sha256(f"{LEGAL_IP_HASH_SALT}{raw_ip}".encode("utf-8")).hexdigest()
        return digest
    try:
        parsed = ip_address(raw_ip)
    except ValueError:
        return raw_ip[:64]
    if parsed.version == 4:
        network = ip_network(f"{parsed}/24", strict=False)
        return str(network.network_address)
    network = ip_network(f"{parsed}/64", strict=False)
    return str(network.network_address)


def parse_legal_confirm(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def has_accepted_feature(user_id: int, feature_key: str, version: str) -> bool:
    """
    Check if user has accepted a specific feature consent version.
    Returns True if acceptance exists, False otherwise.
    """
    from app.models import LegalFeatureAcceptance
    acceptance = LegalFeatureAcceptance.query.filter_by(
        user_id=user_id,
        feature_key=feature_key,
        version=version,
    ).first()
    return acceptance is not None


def record_feature_acceptance(user_id: int, feature_key: str, version: str) -> None:
    """
    Record a feature-specific consent acceptance.
    Idempotent: if already exists, does nothing.
    """
    from datetime import datetime
    from sqlalchemy.exc import IntegrityError
    from app.extensions import db
    from app.models import LegalFeatureAcceptance

    existing = LegalFeatureAcceptance.query.filter_by(
        user_id=user_id,
        feature_key=feature_key,
        version=version,
    ).first()
    if existing:
        return

    acceptance = LegalFeatureAcceptance(
        user_id=user_id,
        feature_key=feature_key,
        version=version,
        accepted_at=datetime.utcnow(),
    )
    db.session.add(acceptance)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        # Already exists (race condition), ignore
