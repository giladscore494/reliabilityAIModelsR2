"""Research aggregation service for vehicle reliability reports.

Aggregates owner profile data while respecting consent and privacy.
Only returns aggregate statistics when sample size >= MIN_AGGREGATION_SAMPLE_SIZE.
"""

from app.extensions import db
from app.models import ResearchResponseSession, ResearchResponse, ResearchConsent

MIN_AGGREGATION_SAMPLE_SIZE = 10


def aggregate_vehicle_reliability_reports(make, model, year_from=None, year_to=None):
    """
    Aggregate reliability reports for a specific vehicle.
    
    Returns dict with aggregate statistics or {"available": False, "reason": "..."}.
    """
    query = (
        db.session.query(ResearchResponseSession)
        .join(ResearchConsent, ResearchResponseSession.consent_id == ResearchConsent.id)
        .filter(
            ResearchResponseSession.flow_type == "owner_profile",
            ResearchConsent.consent_given == True,
            ResearchConsent.revoked_at.is_(None),
        )
    )
    
    # Filter by vehicle via vehicle_context_json or responses
    # For simplicity, query responses for make/model
    make_responses = (
        db.session.query(ResearchResponse.session_id)
        .filter(
            ResearchResponse.flow_type == "owner_profile",
            ResearchResponse.question_code == "make",
            ResearchResponse.response_json.contains(f'"{make}"'),
        )
        .subquery()
    )
    
    model_responses = (
        db.session.query(ResearchResponse.session_id)
        .filter(
            ResearchResponse.flow_type == "owner_profile",
            ResearchResponse.question_code == "model",
            ResearchResponse.response_json.contains(f'"{model}"'),
        )
        .subquery()
    )
    
    query = query.filter(
        ResearchResponseSession.id.in_(make_responses),
        ResearchResponseSession.id.in_(model_responses),
    )
    
    count = query.count()
    
    if count < MIN_AGGREGATION_SAMPLE_SIZE:
        return {
            "available": False,
            "reason": "insufficient_sample_size",
            "sample_size": count,
        }
    
    # Compute aggregate stats
    sessions = query.all()
    
    # Extract responses for satisfaction_score, would_buy_again, had_major_faults
    satisfaction_scores = []
    would_buy_again_count = 0
    had_major_faults_count = 0
    
    for session in sessions:
        for response in session.responses:
            if response.question_code == "satisfaction_score":
                try:
                    import json
                    score = json.loads(response.response_json)
                    if isinstance(score, (int, float)):
                        satisfaction_scores.append(float(score))
                except:
                    pass
            elif response.question_code == "would_buy_again":
                try:
                    import json
                    val = json.loads(response.response_json)
                    if val is True:
                        would_buy_again_count += 1
                except:
                    pass
            elif response.question_code == "had_major_faults":
                try:
                    import json
                    val = json.loads(response.response_json)
                    if val is True:
                        had_major_faults_count += 1
                except:
                    pass
    
    avg_satisfaction = (
        sum(satisfaction_scores) / len(satisfaction_scores)
        if satisfaction_scores
        else None
    )
    pct_would_buy_again = (
        (would_buy_again_count / count * 100) if count > 0 else None
    )
    pct_had_major_faults = (
        (had_major_faults_count / count * 100) if count > 0 else None
    )
    
    return {
        "available": True,
        "sample_size": count,
        "avg_satisfaction_score": round(avg_satisfaction, 2) if avg_satisfaction else None,
        "pct_would_buy_again": round(pct_would_buy_again, 1) if pct_would_buy_again is not None else None,
        "pct_had_major_faults": round(pct_had_major_faults, 1) if pct_had_major_faults is not None else None,
    }


def get_owner_satisfaction_summary(make, model, year_from=None, year_to=None):
    """
    Get owner satisfaction summary for a vehicle.
    """
    return aggregate_vehicle_reliability_reports(make, model, year_from, year_to)


def get_real_world_cost_summary(make, model, year_from=None, year_to=None):
    """
    Get real-world cost summary (fuel consumption, maintenance) for a vehicle.
    """
    # Placeholder: would aggregate actual_fuel_consumption_bucket and maintenance data
    query = (
        db.session.query(ResearchResponseSession)
        .join(ResearchConsent, ResearchResponseSession.consent_id == ResearchConsent.id)
        .filter(
            ResearchResponseSession.flow_type == "owner_profile",
            ResearchConsent.consent_given == True,
            ResearchConsent.revoked_at.is_(None),
        )
    )
    
    count = query.count()
    
    if count < MIN_AGGREGATION_SAMPLE_SIZE:
        return {
            "available": False,
            "reason": "insufficient_sample_size",
            "sample_size": count,
        }
    
    return {
        "available": True,
        "sample_size": count,
        "note": "Real-world cost aggregation not yet implemented",
    }
