import hashlib
import os
from ipaddress import ip_address, ip_network

# General legal versions
TERMS_VERSION = os.environ.get("TERMS_VERSION", "2026-04-25")
PRIVACY_VERSION = os.environ.get("PRIVACY_VERSION", "2026-04-25")
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "support@yedaarechev.com")
LEGAL_IP_HASH_SALT = os.environ.get("LEGAL_IP_HASH_SALT", "").strip()

if not LEGAL_IP_HASH_SALT:
    import logging as _legal_logging
    _legal_logging.getLogger(__name__).warning(
        "[LEGAL] LEGAL_IP_HASH_SALT is empty — IPs will be stored as /24 subnets. "
        "Set LEGAL_IP_HASH_SALT env var for hashed storage."
    )

# Result acknowledgement consents (audit trail before showing sensitive results)
RELIABILITY_RESULT_ACK_KEY = "reliability_results_acknowledgement"
COMPARE_RESULT_ACK_KEY = "compare_results_acknowledgement"
RELIABILITY_RESULT_ACK_VERSION = os.environ.get("RELIABILITY_RESULT_ACK_VERSION", "2026-05-06")
COMPARE_RESULT_ACK_VERSION = os.environ.get("COMPARE_RESULT_ACK_VERSION", "2026-05-06")


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
    from app.utils.http_helpers import _utcnow
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
        accepted_at=_utcnow(),
    )
    db.session.add(acceptance)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        # Already exists (race condition), ignore
