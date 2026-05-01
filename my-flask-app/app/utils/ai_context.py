"""AI reasoning context utilities.

Builds sanitized user context for AI prompts without exposing PII.
"""

import json
from app.extensions import db
from app.models import ResearchResponseSession, ResearchResponse, ResearchConsent
from app.utils.sanitization import sanitize_context_for_ai


def build_user_context_for_reasoning(user_id: int | None, current_request: dict) -> dict:
    """
    Build sanitized user context for AI reasoning.
    
    Pulls latest owner_profile session for the user (if consent active),
    extracts allowlisted fields, and runs sanitization.
    
    Returns empty dict if no data or no consent.
    Never raises exceptions.
    """
    if user_id is None:
        return {}
    
    try:
        # Find latest owner_profile session with active consent
        session = (
            db.session.query(ResearchResponseSession)
            .join(ResearchConsent, ResearchResponseSession.consent_id == ResearchConsent.id)
            .filter(
                ResearchResponseSession.user_id == user_id,
                ResearchResponseSession.flow_type == "owner_profile",
                ResearchConsent.consent_given == True,
                ResearchConsent.revoked_at.is_(None),
            )
            .order_by(ResearchResponseSession.created_at.desc())
            .first()
        )
        
        if not session:
            return {}
        
        # Extract responses into dict
        context = {}
        for response in session.responses:
            question_code = response.question_code
            try:
                value = json.loads(response.response_json)
                context[question_code] = value
            except:
                pass
        
        # Sanitize for AI
        sanitized = sanitize_context_for_ai(context)
        return sanitized
    
    except Exception:
        # Never fail - just return empty
        return {}


def build_internal_dataset_summary(make, model, year_range=None):
    """
    Build internal dataset summary for a vehicle.
    
    Stub that calls research_aggregation_service.
    """
    from app.services.research_aggregation_service import aggregate_vehicle_reliability_reports
    
    try:
        if year_range and isinstance(year_range, (list, tuple)) and len(year_range) >= 2:
            year_from, year_to = year_range[0], year_range[1]
        else:
            year_from, year_to = None, None
        
        return aggregate_vehicle_reliability_reports(make, model, year_from, year_to)
    except Exception:
        return {
            "available": False,
            "reason": "error",
        }
