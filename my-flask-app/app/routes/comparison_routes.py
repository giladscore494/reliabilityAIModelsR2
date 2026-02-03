# -*- coding: utf-8 -*-
"""Comparison routes blueprint for Car Comparison feature."""

from datetime import datetime
from flask import Blueprint, render_template, request, current_app, session
from flask_login import current_user, login_required

from car_models_dict import israeli_car_market_full_compilation
from app.quota import check_and_increment_ip_rate_limit, get_client_ip, log_access_decision, PER_IP_PER_MIN_LIMIT
from app.utils.http_helpers import api_error, api_ok, is_owner_user, get_request_id
from app.services import comparison_service

bp = Blueprint('comparison', __name__)


@bp.route('/compare')
@login_required
def compare_page():
    """Render the car comparison page."""
    user_email = getattr(current_user, "email", "") if current_user.is_authenticated else ""
    return render_template(
        'compare.html',
        user=current_user,
        user_email=user_email,
        is_owner=is_owner_user(),
        car_models_data=israeli_car_market_full_compilation,
    )


@bp.route('/api/compare', methods=['POST'])
@login_required
def compare_api():
    """
    API endpoint for car comparison.
    Accepts a JSON payload with cars to compare (2-3 cars).
    Returns comparison results with deterministic scoring.
    """
    per_ip_limit = current_app.config.get('PER_IP_PER_MIN_LIMIT', PER_IP_PER_MIN_LIMIT)
    
    # Rate limit check
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
    
    # Log access
    user_id = current_user.id if current_user.is_authenticated else None
    log_access_decision('/api/compare', user_id, 'allowed', 'authenticated user')
    
    # Validate content type
    if not request.is_json:
        log_access_decision('/api/compare', user_id, 'rejected', 'validation error: content-type')
        return api_error("invalid_content_type", "Content-Type must be application/json", status=415)
    
    # Parse JSON payload
    try:
        data = request.get_json(silent=False) or {}
    except Exception:
        log_access_decision('/api/compare', user_id, 'rejected', 'validation error: invalid JSON')
        return api_error("invalid_json", "קלט JSON לא תקין", status=400)
    
    # Get session ID for anonymous tracking
    session_id = session.get('_id') if not user_id else None
    
    # Process comparison
    try:
        resp = comparison_service.handle_comparison_request(data, user_id, session_id)
        if resp.status_code == 403 and getattr(resp, "json", lambda: {}).get("error") == "TERMS_NOT_ACCEPTED":
            return resp
        return resp
    except Exception:
        current_app.logger.exception("compare_api failed")
        return api_error("server_error", "שגיאת שרת בעת השוואה", status=500)


@bp.route('/api/compare/history', methods=['GET'])
@login_required
def compare_history():
    """Get comparison history for the current user."""
    user_id = current_user.id
    limit = min(int(request.args.get('limit', 10)), 50)
    
    history = comparison_service.get_comparison_history(user_id, limit=limit)
    return api_ok({"history": history})


@bp.route('/api/compare/<int:comparison_id>', methods=['GET'])
@login_required
def compare_detail(comparison_id):
    """Get details of a specific comparison."""
    user_id = current_user.id
    
    detail = comparison_service.get_comparison_detail(comparison_id, user_id)
    if not detail:
        return api_error("not_found", "השוואה לא נמצאה", status=404)
    
    return api_ok(detail)


@bp.route('/api/compare/cars', methods=['GET'])
@login_required
def get_available_cars():
    """
    Return the car dictionary for the autocomplete.
    Returns a flat list suitable for frontend autocomplete.
    """
    cars_list = []
    for make, models in israeli_car_market_full_compilation.items():
        for model_entry in models:
            # Extract model name and year range from entries like "Corolla (1992-2026)"
            import re
            match = re.match(r'^(.+?)\s*\((\d{4})-(\d{4})\)$', model_entry)
            if match:
                model_name = match.group(1).strip()
                year_start = int(match.group(2))
                year_end = int(match.group(3))
            else:
                model_name = model_entry.strip()
                year_start = None
                year_end = None
            
            cars_list.append({
                "make": make,
                "model": model_name,
                "display": f"{make} {model_name}",
                "year_start": year_start,
                "year_end": year_end,
            })
    
    return api_ok({"cars": cars_list})
