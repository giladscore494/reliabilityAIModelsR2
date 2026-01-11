# -*- coding: utf-8 -*-
"""Analyze routes blueprint."""

import math
import time as pytime
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Blueprint, current_app, request
from flask_login import login_required, current_user

from app.extensions import db
from app.models import SearchHistory
from app.quota import check_and_increment_ip_rate_limit, get_client_ip, log_access_decision, PER_IP_PER_MIN_LIMIT
from app.utils.http_helpers import api_error, api_ok, is_owner_user
from app.services import analyze_service
from app.factory import QUOTA_RESERVATION_TTL_SECONDS

bp = Blueprint('analyze', __name__)
DEFAULT_ESTIMATE_MS = {"analyze": 15000, "advisor": 12000}
TIMING_SAMPLE_LIMIT = 50


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
    Supports both 'kind' (analyze|advisor) and 'endpoint' (analyze) parameters for backward compatibility.
    Filters out null/zero/negative durations and never fails even if history is missing.
    """
    # Support both 'kind' and 'endpoint' parameters
    raw_kind = request.args.get('kind') or request.args.get('endpoint') or 'analyze'
    kind = raw_kind.lower()
    
    if kind not in ['analyze', 'advisor']:
        return api_error('INVALID_KIND', 'Only "analyze" and "advisor" are supported', status=400)

    # Choose the right table based on kind
    if kind == 'analyze':
        from app.models import SearchHistory as HistoryModel
    else:  # advisor
        from app.models import AdvisorHistory as HistoryModel

    default_estimate = DEFAULT_ESTIMATE_MS.get(kind, 15000)

    def _extract_duration(row):
        if row is None:
            return None
        if hasattr(row, "duration_ms"):
            return getattr(row, "duration_ms", None)
        try:
            return row[0]
        except (TypeError, IndexError, KeyError):
            return None

    def _compute_stats(records):
        durations = []
        for row in records:
            raw = _extract_duration(row)
            if raw is None:
                continue
            try:
                val = int(raw)
            except (TypeError, ValueError):
                continue
            if val <= 0:
                continue
            durations.append(val)
        if not durations:
            return None
        durations_sorted = sorted(durations)
        avg_ms = int(sum(durations_sorted) / len(durations_sorted))
        mid = len(durations_sorted) // 2
        if len(durations_sorted) % 2:
            median_ms = durations_sorted[mid]
        else:
            median_ms = int((durations_sorted[mid - 1] + durations_sorted[mid]) / 2)
        p75_index = min(len(durations_sorted) - 1, max(0, int(len(durations_sorted) * 0.75)))
        p75_ms = durations_sorted[p75_index]
        return {
            "avg_ms": avg_ms,
            "median_ms": median_ms,
            "p75_ms": p75_ms,
            "sample_size": len(durations_sorted),
        }

    def _safe_query(fetch_fn, scope_kind: str):
        try:
            return fetch_fn()
        except Exception as e:
            try:
                db.session.rollback()
            except Exception:
                current_app.logger.exception("[TIMING] rollback failed for %s", scope_kind)
            current_app.logger.warning("Timing estimate query failed for %s: %s", scope_kind, e)
            return []

    try:
        user_records = _safe_query(
            lambda: db.session.query(HistoryModel.duration_ms)
            .filter(HistoryModel.user_id == current_user.id)
            .order_by(HistoryModel.timestamp.desc())
            .limit(TIMING_SAMPLE_LIMIT)
            .all(),
            f"{kind}_user",
        )
        stats = _compute_stats(user_records)
        source = "user" if stats else "global"

        if not stats:
            global_records = _safe_query(
                lambda: db.session.query(HistoryModel.duration_ms)
                .order_by(HistoryModel.timestamp.desc())
                .limit(TIMING_SAMPLE_LIMIT)
                .all(),
                f"{kind}_global",
            )
            stats = _compute_stats(global_records)
            if not stats:
                source = "default"

        if stats:
            estimate_ms = stats["avg_ms"]
            avg_ms = stats["avg_ms"]
            median_ms = stats["median_ms"]
            p75_ms = stats["p75_ms"]
            sample_size = stats["sample_size"]
        else:
            estimate_ms = default_estimate
            avg_ms = None
            median_ms = None
            p75_ms = default_estimate
            sample_size = 0

        return api_ok(
            {
                "kind": kind,
                "avg_ms": avg_ms,
                # average_ms kept for backward compatibility; TODO: remove once all clients use avg_ms/estimate_ms.
                "average_ms": avg_ms,
                "median_ms": median_ms,
                "estimate_ms": estimate_ms,
                "p75_ms": p75_ms,
                "sample_size": sample_size,
                "source": source,
            }
        )
    except Exception as e:
        current_app.logger.warning("Timing estimate failed: %s", e)
        try:
            db.session.rollback()
        except Exception:
            current_app.logger.exception("[TIMING] rollback failed after fatal error")
        fallback_estimate = default_estimate
        return api_ok(
            {
                "kind": kind,
                "avg_ms": None,
                "average_ms": None,
                "median_ms": None,
                "estimate_ms": fallback_estimate,
                "p75_ms": fallback_estimate,
                "sample_size": 0,
                "source": "default",
            }
        )
