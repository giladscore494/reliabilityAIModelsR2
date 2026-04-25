"""Owner Profile flow route.

Collects existing vehicle ownership experience data for:
1. Building AI reasoning context (sanitized, no PII)
2. Research dataset (with explicit consent only)

UX disclaimers (Hebrew):
1. Before data entry:
   "מסירת פרטי רכב שבבעלותך היא רשות ומסייעת לנו לספק המלצות מותאמות יותר."

2. Consent checkbox:
   "אני מסכים/ה שנתוני הרכב שלי ישמשו למחקר אנונימי לשיפור השירות (ניתן לבטל בכל עת)"

3. If owner_profile is required for free usage (not currently enforced):
   "כדי להמשיך להשתמש בשירות ללא תשלום, עלינו לבקש ממך לשתף פרטים בסיסיים על רכבך הנוכחי או הקודם."
"""

from flask import Blueprint, request, jsonify, session as flask_session
from flask_login import current_user
import json

from app.extensions import db
from app.models import ResearchConsent, ResearchResponseSession, ResearchResponse
from app.legal import PRIVACY_VERSION, TERMS_VERSION
from app.research import (
    RESEARCH_CONSENT_VERSION,
    RESEARCH_NOTICE_VERSION,
    ensure_anon_id,
    OWNER_PROFILE_FLOW,
)
from app.utils.sanitization import sanitize_research_answer
from app.utils.validation import ValidationError
from app.utils.http_helpers import api_ok, api_error, _utcnow

owner_profile_bp = Blueprint("owner_profile", __name__)


def validate_owner_profile_payload(responses: dict) -> list:
    """
    Validate owner profile payload.
    Returns list of validated (question_code, sanitized_value) tuples.
    Raises ValidationError if invalid.
    """
    required_keys = {
        "has_current_vehicle",
        "make",
        "model",
        "year",
        "fuel_type",
        "transmission",
        "mileage_bucket",
        "ownership_duration_bucket",
        "had_major_faults",
        "satisfaction_score",
        "would_buy_again",
    }
    
    # Check for prohibited fields
    prohibited_keys = {
        "license_plate",
        "plate",
        "address",
        "gender",
        "violations",
        "email",
        "phone",
        "phone_number",
    }
    
    for key in prohibited_keys:
        if key in responses:
            raise ValidationError(key, f"Prohibited field: {key}")
    
    # Check required fields
    for key in required_keys:
        if key not in responses:
            raise ValidationError(key, f"Missing required field: {key}")
    
    # Validate each response
    validated = []
    for question_code, answer in responses.items():
        sanitized = sanitize_research_answer(question_code, answer)
        validated.append((question_code, sanitized))
    
    return validated


@owner_profile_bp.route("/api/owner_profile", methods=["POST"])
def submit_owner_profile():
    """
    POST /api/owner_profile
    
    Submit owner profile data. Requires active research consent to save.
    Without consent, returns success but doesn't save.
    """
    try:
        payload = request.get_json() or {}
        responses = payload.get("responses", {})
        
        # Validate payload
        validated = validate_owner_profile_payload(responses)
        
        # Check for active consent
        user_id = current_user.id if current_user.is_authenticated else None
        anon_id = ensure_anon_id(flask_session) if not user_id else None
        
        consent = None
        if user_id:
            consent = (
                ResearchConsent.query.filter_by(
                    user_id=user_id,
                    consent_type="research_questions",
                )
                .filter(
                    ResearchConsent.revoked_at.is_(None),
                    ResearchConsent.consent_given == True,
                )
                .order_by(ResearchConsent.accepted_at.desc())
                .first()
            )
        elif anon_id:
            consent = (
                ResearchConsent.query.filter_by(
                    anon_id=anon_id,
                    consent_type="research_questions",
                )
                .filter(
                    ResearchConsent.revoked_at.is_(None),
                    ResearchConsent.consent_given == True,
                )
                .order_by(ResearchConsent.accepted_at.desc())
                .first()
            )
        
        if not consent:
            return api_ok({
                "saved": False,
                "reason": "consent_required",
                "message": "Research consent required to save owner profile data",
            })
        
        # Create session and responses
        vehicle_context = {
            "make": responses.get("make"),
            "model": responses.get("model"),
            "year": responses.get("year"),
        }
        
        session_record = ResearchResponseSession(
            user_id=user_id,
            anon_id=anon_id,
            flow_type=OWNER_PROFILE_FLOW,
            source_analysis_type="owner_profile_form",
            source_record_id=None,
            vehicle_context_json=json.dumps(vehicle_context),
            consent_id=consent.id,
            status="submitted",
            question_version=RESEARCH_CONSENT_VERSION,
        )
        db.session.add(session_record)
        db.session.flush()
        
        # Create individual responses
        for question_code, sanitized_value in validated:
            response_record = ResearchResponse(
                session_id=session_record.id,
                question_code=question_code,
                flow_type=OWNER_PROFILE_FLOW,
                response_json=json.dumps(sanitized_value),
                answered_at=_utcnow(),
                is_required=question_code in {
                    "has_current_vehicle",
                    "make",
                    "model",
                    "year",
                    "fuel_type",
                    "transmission",
                    "mileage_bucket",
                    "ownership_duration_bucket",
                    "had_major_faults",
                    "satisfaction_score",
                    "would_buy_again",
                },
                question_version=RESEARCH_CONSENT_VERSION,
                consent_id=consent.id,
            )
            db.session.add(response_record)
        
        db.session.commit()
        
        return api_ok({
            "saved": True,
            "session_id": session_record.id,
            "message": "Owner profile saved successfully",
        })
    
    except ValidationError as e:
        return api_error(e.field, e.message, 400)
    except Exception as e:
        db.session.rollback()
        return api_error("server_error", str(e), 500)
