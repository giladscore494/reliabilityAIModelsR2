# -*- coding: utf-8 -*-
"""Legal acceptance routes blueprint."""

from flask import Blueprint, current_app, jsonify, request, session
from flask_login import login_required, current_user
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.extensions import db
from app.legal import normalize_legal_ip, parse_legal_confirm, record_feature_acceptance
from app.models import (
    LegalAcceptance,
    ResearchConsent,
    ResearchResponse,
    ResearchResponseSession,
)
from app.quota import get_client_ip
from app.research import (
    RESEARCH_CONSENT_TYPE,
    RESEARCH_NOTICE_VERSION,
    RESEARCH_QUESTION_VERSION,
    ensure_anon_id,
    validate_research_payload,
)
from app.utils.validation import ValidationError
from app.utils.http_helpers import get_request_id, _utcnow

bp = Blueprint("legal", __name__)


def _legal_error(code: str, message: str, status: int = 412):
    rid = get_request_id()
    resp = jsonify({"error": code, "message": message, "request_id": rid})
    resp.status_code = status
    resp.headers["X-Request-ID"] = rid
    return resp


def _research_subject():
    if current_user.is_authenticated:
        return current_user.id, None
    return None, ensure_anon_id(session)


def _find_research_consent(*, consent_id=None):
    terms_version = current_app.config.get("TERMS_VERSION")
    privacy_version = current_app.config.get("PRIVACY_VERSION")
    research_notice_version = current_app.config.get(
        "RESEARCH_NOTICE_VERSION", RESEARCH_NOTICE_VERSION
    )
    user_id, anon_id = _research_subject()
    query = ResearchConsent.query.filter_by(
        consent_type=current_app.config.get(
            "RESEARCH_CONSENT_TYPE", RESEARCH_CONSENT_TYPE
        ),
        terms_version=terms_version,
        privacy_version=privacy_version,
        research_notice_version=research_notice_version,
    )
    if consent_id:
        query = query.filter_by(id=consent_id)
    if user_id:
        return query.filter_by(user_id=user_id).first()
    return query.filter_by(anon_id=anon_id).first()


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
        return _legal_error(
            "TERMS_NOT_ACCEPTED", "Please accept Terms & Privacy to continue."
        )

    terms_version = data.get("terms_version") or current_app.config.get("TERMS_VERSION")
    privacy_version = data.get("privacy_version") or current_app.config.get(
        "PRIVACY_VERSION"
    )

    existing = LegalAcceptance.query.filter_by(
        user_id=current_user.id,
        terms_version=terms_version,
        privacy_version=privacy_version,
    ).first()

    if not existing:
        # Store a normalized IP to limit PII while preserving auditability.
        raw_ip = get_client_ip()
        acceptance = LegalAcceptance(
            user_id=current_user.id,
            terms_version=terms_version,
            privacy_version=privacy_version,
            accepted_at=_utcnow(),
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
            if not existing:
                return _legal_error(
                    "LEGAL_ACCEPT_FAILED", "Unable to record acceptance.", status=500
                )
        except SQLAlchemyError:
            db.session.rollback()
            return _legal_error(
                "LEGAL_ACCEPT_FAILED", "Unable to record acceptance.", status=500
            )

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
                processed_features.append(
                    {"feature_key": feature_key, "version": version}
                )
            except Exception:
                # Log but don't fail the entire request
                current_app.logger.warning(
                    "Failed to record feature consent: %s=%s for user=%s",
                    feature_key,
                    version,
                    current_user.id,
                )

    return jsonify(
        {
            "ok": True,
            "terms_version": terms_version,
            "privacy_version": privacy_version,
            "feature_consents": processed_features,
        }
    )


@bp.route("/api/legal/status", methods=["GET"])
@login_required
def legal_status():
    """Return the current legal acceptance status for the logged-in user."""
    terms_version = current_app.config.get("TERMS_VERSION")
    privacy_version = current_app.config.get("PRIVACY_VERSION")

    accepted = (
        LegalAcceptance.query.filter_by(
            user_id=current_user.id,
            terms_version=terms_version,
            privacy_version=privacy_version,
        ).first()
        is not None
    )

    return jsonify(
        {
            "accepted": accepted,
            "terms_version": terms_version,
            "privacy_version": privacy_version,
        }
    )


@bp.route("/api/research/status", methods=["GET"])
def research_status():
    consent = _find_research_consent()
    return jsonify(
        {
            "accepted": consent is not None,
            "consent_id": consent.id if consent else None,
            "terms_version": current_app.config.get("TERMS_VERSION"),
            "privacy_version": current_app.config.get("PRIVACY_VERSION"),
            "research_notice_version": current_app.config.get(
                "RESEARCH_NOTICE_VERSION", RESEARCH_NOTICE_VERSION
            ),
        }
    )


@bp.route("/api/research/consent", methods=["POST"])
def accept_research_consent():
    data = request.get_json(silent=True) or {}
    if not parse_legal_confirm(data.get("research_confirm")):
        return _legal_error(
            "RESEARCH_CONSENT_REQUIRED",
            "נדרשת הסכמה מפורשת לשמירת תשובות המחקר.",
        )

    terms_version = current_app.config.get("TERMS_VERSION")
    privacy_version = current_app.config.get("PRIVACY_VERSION")
    research_notice_version = current_app.config.get(
        "RESEARCH_NOTICE_VERSION", RESEARCH_NOTICE_VERSION
    )
    consent_type = current_app.config.get(
        "RESEARCH_CONSENT_TYPE", RESEARCH_CONSENT_TYPE
    )
    user_id, anon_id = _research_subject()
    existing = _find_research_consent()
    if existing:
        return jsonify(
            {
                "ok": True,
                "accepted": True,
                "consent_id": existing.id,
                "terms_version": terms_version,
                "privacy_version": privacy_version,
                "research_notice_version": research_notice_version,
            }
        )

    consent = ResearchConsent(
        user_id=user_id,
        anon_id=anon_id,
        consent_type=consent_type,
        terms_version=terms_version,
        privacy_version=privacy_version,
        research_notice_version=research_notice_version,
        accepted_at=_utcnow(),
        accepted_ip=normalize_legal_ip(get_client_ip()),
        accepted_user_agent=(request.headers.get("User-Agent") or "")[:512],
        accepted_lang=(request.accept_languages.best or "")[:32],
        accepted_source=((data.get("accepted_source") or "web")[:64]),
        is_explicit=True,
        is_informed=True,
    )
    db.session.add(consent)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        consent = _find_research_consent()
        if not consent:
            return _legal_error(
                "RESEARCH_CONSENT_SAVE_FAILED",
                "לא הצלחנו לשמור את הסכמת המחקר.",
                status=500,
            )
    except SQLAlchemyError:
        db.session.rollback()
        return _legal_error(
            "RESEARCH_CONSENT_SAVE_FAILED",
            "לא הצלחנו לשמור את הסכמת המחקר.",
            status=500,
        )

    return jsonify(
        {
            "ok": True,
            "accepted": True,
            "consent_id": consent.id,
            "terms_version": terms_version,
            "privacy_version": privacy_version,
            "research_notice_version": research_notice_version,
        }
    )


@bp.route("/api/research/responses", methods=["POST"])
def save_research_responses():
    if not request.is_json:
        return _legal_error(
            "INVALID_CONTENT_TYPE", "Content-Type must be application/json.", status=415
        )

    data = request.get_json(silent=True) or {}
    consent = _find_research_consent(consent_id=data.get("consent_id"))
    if consent is None:
        return _legal_error(
            "RESEARCH_CONSENT_REQUIRED",
            "יש לאשר את הודעת המחקר לפני שמירת התשובות.",
        )

    flow_type = (data.get("flow_type") or "").strip().lower()
    try:
        context_obj, context_json, validated_responses = validate_research_payload(
            flow_type,
            data.get("responses"),
            data.get("vehicle_context") or {},
        )
    except ValidationError as exc:
        return _legal_error("VALIDATION_ERROR", exc.message, status=400)

    source_analysis_type = (data.get("source_analysis_type") or "").strip()
    if source_analysis_type not in {
        "search_history",
        "comparison_history",
        "advisor_history",
    }:
        return _legal_error(
            "VALIDATION_ERROR", "source_analysis_type לא נתמך.", status=400
        )

    source_record_id = data.get("source_record_id")
    if source_record_id is None:
        return _legal_error(
            "VALIDATION_ERROR", "source_record_id is required.", status=400
        )
    try:
        source_record_id = int(source_record_id)
    except (TypeError, ValueError):
        return _legal_error(
            "VALIDATION_ERROR", "source_record_id must be an integer.", status=400
        )
    if source_record_id <= 0:
        return _legal_error(
            "VALIDATION_ERROR", "source_record_id must be positive.", status=400
        )

    user_id, anon_id = _research_subject()
    session_query = ResearchResponseSession.query.filter_by(
        flow_type=flow_type,
        source_analysis_type=source_analysis_type,
        source_record_id=source_record_id,
    )
    if user_id:
        response_session = session_query.filter_by(user_id=user_id).first()
    else:
        response_session = session_query.filter_by(anon_id=anon_id).first()

    if response_session is None:
        response_session = ResearchResponseSession(
            user_id=user_id,
            anon_id=anon_id,
            flow_type=flow_type,
            source_analysis_type=source_analysis_type,
            source_record_id=source_record_id,
            vehicle_context_json=context_json,
            consent_id=consent.id,
            status="submitted",
        )
        db.session.add(response_session)
        db.session.flush()
    else:
        response_session.vehicle_context_json = context_json
        response_session.consent_id = consent.id
        response_session.status = "submitted"

    for validated in validated_responses:
        record = ResearchResponse.query.filter_by(
            session_id=response_session.id,
            question_code=validated["question_code"],
        ).first()
        if record is None:
            record = ResearchResponse(
                session_id=response_session.id,
                question_code=validated["question_code"],
                flow_type=flow_type,
                consent_id=consent.id,
                question_version=RESEARCH_QUESTION_VERSION,
            )
            db.session.add(record)
        record.response_json = validated["response_json"]
        record.answered_at = _utcnow()
        record.is_required = validated["is_required"]
        record.question_version = RESEARCH_QUESTION_VERSION
        record.consent_id = consent.id

    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        return _legal_error(
            "RESEARCH_SAVE_FAILED", "לא הצלחנו לשמור את תשובות המחקר.", status=500
        )

    return jsonify(
        {
            "ok": True,
            "session_id": response_session.id,
            "saved_count": len(validated_responses),
            "vehicle_context": context_obj,
        }
    )


@bp.route("/api/research_consent/revoke", methods=["POST"])
def revoke_research_consent():
    """
    Revoke research consent for the current user/session.
    Sets revoked_at timestamp on all active research consents.
    """
    user_id, anon_id = _research_subject()
    
    if not user_id and not anon_id:
        return _legal_error("NO_SUBJECT", "No user or session identified", status=400)
    
    # Find all active consents
    query = ResearchConsent.query.filter(
        ResearchConsent.revoked_at.is_(None),
        ResearchConsent.consent_given == True,
    )
    
    if user_id:
        query = query.filter_by(user_id=user_id)
    else:
        query = query.filter_by(anon_id=anon_id)
    
    consents = query.all()
    
    if not consents:
        return jsonify({"ok": True, "message": "No active consents to revoke", "revoked_count": 0})
    
    # Revoke all
    revoked_at = _utcnow()
    for consent in consents:
        consent.revoked_at = revoked_at
    
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        return _legal_error("REVOKE_FAILED", "Failed to revoke consent", status=500)
    
    return jsonify({
        "ok": True,
        "message": "Research consent revoked successfully",
        "revoked_count": len(consents),
        "revoked_at": revoked_at.isoformat(),
    })
