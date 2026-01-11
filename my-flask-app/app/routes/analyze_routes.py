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
from app.quota import (
    check_and_increment_ip_rate_limit,
    get_client_ip,
    log_access_decision,
    PER_IP_PER_MIN_LIMIT,
)
from app.utils.http_helpers import api_error, is_owner_user
from app.utils.api import api_ok  # ✅ FIX: missing import
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
    reservation_ttl = current_app.config.get(
        "QUOTA_RESERVATION_TTL_SECONDS", QUOTA_RESERVATION_TTL_SECONDS
    )

    user_id = current_user.id if current_user.is_authenticated else None
    log_access_decision('/analyze', user_id, 'allowed', 'authenticated user')

    client_ip = get_client_ip()
    ip_allowed, ip_count, ip_resets_at = check_and_increment_ip_rate_limit(
        client_ip, limit=per_ip_limit
    )

    if not ip_allowed:
        retry_after = max(
            0, int((ip_resets_at - datetime.utcnow()).total_seconds())
        )
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
        log_access_decision('/analyze', user_id, 'rejected', 'invalid content-type')
        return api_error(
            "invalid_content_type",
            "Content-Type must be application/json",
            status=415,
            details={"field": "payload"},
        )

    try:
        data = request.get_json(silent=False) or {}
        if not data:
            return api_error(
                "invalid_json",
                "Invalid JSON payload",
                status=400,
                details={"field": "payload"},
            )
    except Exception:
        log_access_decision('/analyze', user_id, 'rejected', 'invalid json')
        return api_error(
            "validation_error",
            "שגיאת קלט (שלב 0): בקשת JSON לא תקינה.",
            status=400,
            details={"field": "payload"},
        )

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
    Supports both 'kind' (analyze|advisor) and 'endpoint' (analyze).
    Never raises – always returns a valid response.
    """
    raw_kind = request.args.get('kind') or request.args.get('endpoint') or 'analyze'
    kind = raw_kind.lower()

    if kind not in ('analyze', 'advisor'):
        return api_error(
            'INVALID_KIND',
            'Only "analyze" and "advisor" are supported',
            status=400,
        )

    if kind == 'analyze':
        HistoryModel = SearchHistory
    else:
        # ✅ Defensive import – prevents hard crash if model missing
        try:
            from app.models import AdvisorHistory
            HistoryModel = AdvisorHistory
        except Exception:
            HistoryModel = None

    default_estimate = DEFAULT_ESTIMATE_MS.get(kind, 15000)

    def _compute_stats(rows):
        values = []
        for (val,) in rows:
            try:
                v = int(val)
                if v > 0:
                    values.append(v)
            except Exception:
                continue

        if not values:
            return None

        values.sort()
        n = len(values)

        return {
            "avg_ms": int(sum(values) / n),
            "median_ms": values[n // 2],
            "p75_ms": values[min(n - 1, int(n * 0.75))],
            "sample_size": n,
        }

    stats = None
    source = "default"

    if HistoryModel is not None:
        try:
            user_rows = (
                db.session.query(HistoryModel.duration_ms)
                .filter(HistoryModel.user_id == current_user.id)
                .order_by(HistoryModel.timestamp.desc())
                .limit(TIMING_SAMPLE_LIMIT)
                .all()
            )
            stats = _compute_stats(user_rows)
            source = "user" if stats else source
        except Exception:
            stats = None

        if not stats:
            try:
                global_rows = (
                    db.session.query(HistoryModel.duration_ms)
                    .order_by(HistoryModel.timestamp.desc())
                    .limit(TIMING_SAMPLE_LIMIT)
                    .all()
                )
                stats = _compute_stats(global_rows)
                source = "global" if stats else source
            except Exception:
                stats = None

    if not stats:
        stats = {
            "avg_ms": None,
            "median_ms": None,
            "p75_ms": default_estimate,
            "sample_size": 0,
        }

    return api_ok(
        {
            "kind": kind,
            "avg_ms": stats["avg_ms"],
            "average_ms": stats["avg_ms"],  # backward compatibility
            "median_ms": stats["median_ms"],
            "estimate_ms": stats["avg_ms"] or default_estimate,
            "p75_ms": stats["p75_ms"],
            "sample_size": stats["sample_size"],
            "source": source,
        }
    )
