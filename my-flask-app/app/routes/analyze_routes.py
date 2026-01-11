# -*- coding: utf-8 -*-
"""Analyze routes blueprint."""

import time as pytime
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Blueprint, current_app, request
from flask_login import login_required, current_user

from app.extensions import db
from app.models import SearchHistory
from app.quota import check_and_increment_ip_rate_limit, get_client_ip, log_access_decision, PER_IP_PER_MIN_LIMIT
from app.utils.http_helpers import api_error, is_owner_user
from app.services import analyze_service
from app.factory import QUOTA_RESERVATION_TTL_SECONDS

bp = Blueprint('analyze', __name__)


@bp.route('/reliability_report', methods=['POST'])
@login_required
def reliability_report():
    """
    API המחזיר דו"ח אמינות תמציתי בפורמט JSON קשיח כפי שמוגדר בדרישות החדשות.
    """
    return api_error("endpoint_deprecated", "הדו\"ח נכלל כעת בתשובת /analyze", status=410)


@bp.route('/analyze', methods=['POST'])
@login_required
def analyze_car():
    start_time_ms = int(pytime.time() * 1000)
    app_tz = current_app.config.get("APP_TZ_OBJ", ZoneInfo("UTC"))
    owner_bypass_quota = current_app.config.get("OWNER_BYPASS_QUOTA", False)
    per_ip_limit = current_app.config.get("PER_IP_PER_MIN_LIMIT", PER_IP_PER_MIN_LIMIT)
    reservation_ttl = current_app.config.get("QUOTA_RESERVATION_TTL_SECONDS", QUOTA_RESERVATION_TTL_SECONDS)
    
    # Log access decision
    user_id = current_user.id if current_user.is_authenticated else None
    log_access_decision('/analyze', user_id, 'allowed', 'authenticated user')

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
        log_access_decision('/analyze', user_id, 'rejected', 'validation error: content-type')
        return api_error("invalid_content_type", "Content-Type must be application/json", status=415, details={"field": "payload"})

    try:
        data = request.get_json(silent=False) or {}
        if not data:
            return api_error("invalid_json", "Invalid JSON payload", status=400, details={"field": "payload"})
    except Exception:
        log_access_decision('/analyze', user_id, 'rejected', 'validation error: invalid payload')
        return api_error("validation_error", "שגיאת קלט (שלב 0): בקשת JSON לא תקינה.", status=400, details={"field": "payload"})

    bypass_owner = owner_bypass_quota and is_owner_user()
    return analyze_service.handle_analyze_request(
        data,
        app_tz=app_tz,
        start_time_ms=start_time_ms,
        bypass_owner=bypass_owner,
        reservation_ttl=reservation_ttl,
        user_id=user_id,
    )


@bp.route('/api/timing/estimate', methods=['GET'])
@login_required
def timing_estimate():
    """
    Returns estimated timing for an endpoint.
    Calculates user-specific average/p75, fallback to global aggregated stats.
    Supports both 'kind' (analyze|advisor) and 'endpoint' (analyze) parameters for backward compatibility.
    """
    # Support both 'kind' and 'endpoint' parameters
    kind = request.args.get('kind') or request.args.get('endpoint', 'analyze')
    
    if kind not in ['analyze', 'advisor']:
        return api_error('INVALID_KIND', 'Only "analyze" and "advisor" are supported', status=400)
    
    try:
        # Choose the right table based on kind
        if kind == 'analyze':
            from app.models import SearchHistory as HistoryModel
            user_limit = 20
            global_limit = 100
        else:  # advisor
            from app.models import AdvisorHistory as HistoryModel
            user_limit = 20
            global_limit = 100
        
        # Try user-specific stats first
        user_records = db.session.query(HistoryModel.duration_ms).filter(
            HistoryModel.user_id == current_user.id,
            HistoryModel.duration_ms.isnot(None)
        ).order_by(HistoryModel.timestamp.desc()).limit(user_limit).all()
        
        if user_records and len(user_records) >= 3:
            durations = [r[0] for r in user_records if r[0] is not None]
            avg_ms = int(sum(durations) / len(durations))
            sorted_durations = sorted(durations)
            p75_index = int(len(sorted_durations) * 0.75)
            p75_ms = sorted_durations[p75_index]
            
            return api_ok({
                'kind': kind,
                'average_ms': avg_ms,
                'p75_ms': p75_ms,
                'sample_size': len(durations),
                'source': 'user'
            })
        
        # Fallback to global aggregated stats
        global_records = db.session.query(HistoryModel.duration_ms).filter(
            HistoryModel.duration_ms.isnot(None)
        ).order_by(HistoryModel.timestamp.desc()).limit(global_limit).all()
        
        if global_records and len(global_records) >= 10:
            durations = [r[0] for r in global_records if r[0] is not None]
            avg_ms = int(sum(durations) / len(durations))
            sorted_durations = sorted(durations)
            p75_index = int(len(sorted_durations) * 0.75)
            p75_ms = sorted_durations[p75_index]
            
            return api_ok({
                'kind': kind,
                'average_ms': avg_ms,
                'p75_ms': p75_ms,
                'sample_size': len(durations),
                'source': 'global'
            })
        
        # Default fallback if no data
        default_avg = 15000 if kind == 'analyze' else 12000  # advisor is typically faster
        default_p75 = 20000 if kind == 'analyze' else 15000
        
        return api_ok({
            'kind': kind,
            'average_ms': default_avg,
            'p75_ms': default_p75,
            'sample_size': 0,
            'source': 'default'
        })
        
    except Exception as e:
        current_app.logger.error(f"Timing estimate error: {str(e)}")
        return api_error('ESTIMATE_FAILED', 'Failed to calculate timing estimate', status=500)
