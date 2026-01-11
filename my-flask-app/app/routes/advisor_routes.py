# -*- coding: utf-8 -*-
"""Advisor routes blueprint."""

from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_required, current_user

from app.quota import check_and_increment_ip_rate_limit, get_client_ip, log_access_decision, PER_IP_PER_MIN_LIMIT
from app.utils.http_helpers import api_error, is_owner_user
from app.utils.validation import validate_analyze_request, ValidationError
from app.services import advisor_service

bp = Blueprint('advisor', __name__)


@bp.route('/recommendations')
@login_required
def recommendations():
    advisor_owner_only = current_app.config.get('ADVISOR_OWNER_ONLY', False)
    if advisor_owner_only and not is_owner_user():
        flash("גישה למנוע ההמלצות זמינה לבעלי המערכת בלבד.", "error")
        return redirect(url_for('dashboard.dashboard'))
    user_email = getattr(current_user, "email", "") if current_user.is_authenticated else ""
    return render_template(
        'recommendations.html',
        user=current_user,
        user_email=user_email,
        is_owner=is_owner_user(),
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
        log_access_decision('/advisor_api', getattr(current_user, "id", None), 'rejected', 'owner only')
        return api_error("forbidden", "גישה למנוע ההמלצות זמינה לבעלי המערכת בלבד.", status=403)
    # Log access decision
    user_id = current_user.id if current_user.is_authenticated else None
    log_access_decision('/advisor_api', user_id, 'allowed', 'authenticated user')

    client_ip = get_client_ip()
    ip_allowed, ip_count, ip_resets_at = check_and_increment_ip_rate_limit(client_ip, limit=per_ip_limit)
    if not ip_allowed:
        retry_after = max(0, int((ip_resets_at - datetime.utcnow()).total_seconds()))
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
        log_access_decision('/advisor_api', user_id, 'rejected', 'validation error: content-type')
        return api_error("invalid_content_type", "Content-Type חייב להיות application/json", status=415, details={"field": "payload"})

    try:
        payload = request.get_json(silent=False) or {}
    except Exception:
        log_access_decision('/advisor_api', user_id, 'rejected', 'validation error: invalid JSON')
        return api_error("invalid_json", "קלט JSON לא תקין", status=400, details={"field": "payload"})

    # Validate request before processing
    try:
        validated = validate_analyze_request(payload)
    except ValidationError as e:
        log_access_decision('/advisor_api', user_id, 'rejected', f'validation error: {e.field}')
        return api_error("validation_error", e.message, status=400, details={"field": e.field})

    return advisor_service.handle_advisor_logic(validated, current_user, user_id)
