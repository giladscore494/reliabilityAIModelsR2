# -*- coding: utf-8 -*-
"""Legal acceptance routes blueprint."""

from datetime import datetime

from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required, current_user
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.extensions import db
from app.legal import normalize_legal_ip, parse_legal_confirm
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
    data = request.get_json(silent=True) or {}
    if not parse_legal_confirm(data.get("legal_confirm")):
        return _legal_error("TERMS_NOT_ACCEPTED", "Please accept Terms & Privacy to continue.")

    terms_version = current_app.config.get("TERMS_VERSION")
    privacy_version = current_app.config.get("PRIVACY_VERSION")
    existing = LegalAcceptance.query.filter_by(
        user_id=current_user.id,
        terms_version=terms_version,
        privacy_version=privacy_version,
    ).first()
    if existing:
        return jsonify({"ok": True, "terms_version": terms_version, "privacy_version": privacy_version})

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
    except IntegrityError:
        db.session.rollback()
        existing = LegalAcceptance.query.filter_by(
            user_id=current_user.id,
            terms_version=terms_version,
            privacy_version=privacy_version,
        ).first()
        if existing:
            return jsonify({"ok": True, "terms_version": terms_version, "privacy_version": privacy_version})
        return _legal_error("LEGAL_ACCEPT_FAILED", "Unable to record acceptance.", status=500)
    except SQLAlchemyError:
        db.session.rollback()
        return _legal_error("LEGAL_ACCEPT_FAILED", "Unable to record acceptance.", status=500)

    return jsonify({"ok": True, "terms_version": terms_version, "privacy_version": privacy_version})
