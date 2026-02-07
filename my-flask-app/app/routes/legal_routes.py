# -*- coding: utf-8 -*-
"""Legal acceptance routes blueprint."""

from datetime import datetime

from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required, current_user
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.extensions import db
from app.legal import normalize_legal_ip, parse_legal_confirm, record_feature_acceptance
from app.models import LegalAcceptance
from app.quota import get_client_ip
from app.utils.http_helpers import get_request_id

bp = Blueprint("legal", __name__)


def _legal_error(code: str, message: str, status: int = 412):
    rid = get_request_id()
    resp = jsonify({"error": code, "message": message, "request_id": rid})
    resp.status_code = status
    resp.headers["X-Request-ID"] = rid
    return resp


@bp.route("/api/legal/accept", methods=["POST"])
@login_required
def accept_legal():
    """
    Accept Terms of Use and Privacy Policy.
    Optionally includes feature-specific consents.
    
    Payload:
    {
      "legal_confirm": true,
      "terms_version": "2026-02-07",  # optional, uses config default
      "privacy_version": "2026-02-07",  # optional, uses config default
      "feature_consents": [  # optional
        {"feature_key": "invoice_scanner", "version": "2026-02-07"}
      ]
    }
    """
    data = request.get_json(silent=True) or {}
    if not parse_legal_confirm(data.get("legal_confirm")):
        return _legal_error("TERMS_NOT_ACCEPTED", "Please accept Terms & Privacy to continue.")

    terms_version = data.get("terms_version") or current_app.config.get("TERMS_VERSION")
    privacy_version = data.get("privacy_version") or current_app.config.get("PRIVACY_VERSION")
    
    existing = LegalAcceptance.query.filter_by(
        user_id=current_user.id,
        terms_version=terms_version,
        privacy_version=privacy_version,
    ).first()
    
    terms_acceptance_recorded = existing is not None
    
    if not existing:
        # Store a normalized IP (reduced precision) to limit PII while keeping auditability.
        raw_ip = get_client_ip()
        acceptance = LegalAcceptance(
            user_id=current_user.id,
            terms_version=terms_version,
            privacy_version=privacy_version,
            accepted_at=datetime.utcnow(),
            accepted_ip=normalize_legal_ip(raw_ip),
            accepted_user_agent=(request.headers.get("User-Agent") or "")[:512],
            source="web",
        )
        db.session.add(acceptance)
        try:
            db.session.commit()
            terms_acceptance_recorded = True
        except IntegrityError:
            db.session.rollback()
            existing = LegalAcceptance.query.filter_by(
                user_id=current_user.id,
                terms_version=terms_version,
                privacy_version=privacy_version,
            ).first()
            if existing:
                terms_acceptance_recorded = True
            else:
                return _legal_error("LEGAL_ACCEPT_FAILED", "Unable to record acceptance.", status=500)
        except SQLAlchemyError:
            db.session.rollback()
            return _legal_error("LEGAL_ACCEPT_FAILED", "Unable to record acceptance.", status=500)

    # Process feature consents if provided
    feature_consents = data.get("feature_consents") or []
    processed_features = []
    
    for consent in feature_consents:
        if not isinstance(consent, dict):
            continue
        feature_key = consent.get("feature_key")
        version = consent.get("version")
        if feature_key and version:
            try:
                record_feature_acceptance(current_user.id, feature_key, version)
                processed_features.append({"feature_key": feature_key, "version": version})
            except Exception:
                # Log but don't fail the entire request
                current_app.logger.warning(
                    f"Failed to record feature consent: {feature_key}={version} for user={current_user.id}"
                )

    return jsonify({
        "ok": True,
        "terms_version": terms_version,
        "privacy_version": privacy_version,
        "feature_consents": processed_features,
    })
