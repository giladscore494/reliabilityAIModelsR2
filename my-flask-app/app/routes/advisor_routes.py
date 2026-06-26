# -*- coding: utf-8 -*-
"""Advisor routes blueprint."""

import json

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from app.quota import (
    PER_IP_PER_MIN_LIMIT,
    check_and_increment_ip_rate_limit,
    get_client_ip,
    log_access_decision,
)
from app.utils.http_helpers import _utcnow, api_error, get_request_id, is_owner_user
from app.utils.validation import ValidationError, validate_analyze_request
from app.models import AdvisorHistory
from app.services import advisor_service
from app.services.gemini_health_verdict import log_product_call_verdict_input
from app.extensions import GEMINI_RECOMMENDER_MODEL_ID

bp = Blueprint('advisor', __name__)


def _current_user_email() -> str:
    if not current_user.is_authenticated:
        return ""
    return getattr(current_user, "email", "")


@bp.route('/recommendations')
def recommendations():
    advisor_owner_only = current_app.config.get('ADVISOR_OWNER_ONLY', False)
    if current_user.is_authenticated and advisor_owner_only and not is_owner_user():
        flash("גישה למנוע ההמלצות זמינה לבעלי המערכת בלבד.", "error")
        return redirect(url_for('dashboard.dashboard'))
    return render_template(
        'recommendations.html',
        user=current_user,
        user_email=_current_user_email(),
        is_owner=is_owner_user(),
        advisor_history_profile=None,
        advisor_history_result=None,
        advisor_history_id=None,
    )


@bp.route('/recommendations/history/<int:history_id>')
@login_required
def recommendations_history(history_id):
    advisor_owner_only = current_app.config.get('ADVISOR_OWNER_ONLY', False)
    if advisor_owner_only and not is_owner_user():
        flash("גישה למנוע ההמלצות זמינה לבעלי המערכת בלבד.", "error")
        return redirect(url_for('dashboard.dashboard'))
    record = AdvisorHistory.query.filter_by(id=history_id, user_id=current_user.id).first()
    if not record:
        flash("השאלון המבוקש לא נמצא.", "error")
        return redirect(url_for('dashboard.dashboard'))
    try:
        profile = json.loads(record.profile_json) if record.profile_json else {}
    except Exception:
        profile = {}
    try:
        result = json.loads(record.result_json) if record.result_json else {}
    except Exception:
        result = {}
    return render_template(
        'recommendations.html',
        user=current_user,
        user_email=_current_user_email(),
        is_owner=is_owner_user(),
        advisor_history_profile=profile,
        advisor_history_result=result,
        advisor_history_id=record.id,
    )


@bp.route('/advisor_api', methods=['POST'])
@login_required
def advisor_api():
    """
    מקבל profile מה-JS (recommendations.js),
    בונה user_profile מלא כמו ב-Car Advisor (Streamlit),
    קורא ל-Gemini 3 Pro, שומר היסטוריה ומחזיר JSON מוכן להצגה.
    """
    advisor_owner_only = current_app.config.get('ADVISOR_OWNER_ONLY', False)
    per_ip_limit = current_app.config.get('PER_IP_PER_MIN_LIMIT', PER_IP_PER_MIN_LIMIT)

    if advisor_owner_only and not is_owner_user():
        log_access_decision(
            '/advisor_api',
            getattr(current_user, "id", None),
            'rejected',
            'owner only',
        )
        return api_error(
            "forbidden",
            "גישה למנוע ההמלצות זמינה לבעלי המערכת בלבד.",
            status=403,
        )
    # Log access decision
    user_id = current_user.id if current_user.is_authenticated else None
    log_access_decision('/advisor_api', user_id, 'allowed', 'authenticated user')

    client_ip = get_client_ip()
    ip_allowed, ip_count, ip_resets_at = check_and_increment_ip_rate_limit(client_ip, limit=per_ip_limit)
    if not ip_allowed:
        retry_after = max(0, int((ip_resets_at - _utcnow()).total_seconds()))
        resp = api_error(
            "rate_limited",
            "חרגת ממגבלת הבקשות לדקה.",
            status=429,
            details={
                "limit": per_ip_limit,
                "used": ip_count,
                "remaining": max(0, per_ip_limit - ip_count),
                "resets_at": ip_resets_at.isoformat(),
            },
        )
        resp.headers["Retry-After"] = str(retry_after)
        return resp
    
    if not request.is_json:
        log_access_decision(
            '/advisor_api',
            user_id,
            'rejected',
            'validation error: content-type',
        )
        return api_error(
            "invalid_content_type",
            "Content-Type חייב להיות application/json",
            status=415,
            details={"field": "payload"},
        )

    try:
        payload = request.get_json(silent=False) or {}
    except Exception:
        log_access_decision(
            '/advisor_api',
            user_id,
            'rejected',
            'validation error: invalid JSON',
        )
        return api_error("invalid_json", "קלט JSON לא תקין", status=400, details={"field": "payload"})

    # Validate request before processing
    try:
        validated = validate_analyze_request(payload, is_owner=is_owner_user())
    except ValidationError as e:
        log_access_decision(
            '/advisor_api',
            user_id,
            'rejected',
            f'validation error: {e.field}',
        )
        return api_error("validation_error", e.message, status=400, details={"field": e.field})

    log_product_call_verdict_input(
        request_id=get_request_id(),
        feature="recommendations",
        model=GEMINI_RECOMMENDER_MODEL_ID,
        api_method="interactions_grounded",
        endpoint_family="interactions",
        tools=["google_search"],
    )
    return advisor_service.handle_advisor_logic(validated, current_user, user_id)
