# -*- coding: utf-8 -*-
"""Analyze routes blueprint."""

import os
import json
import hashlib
import traceback
import time as pytime
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Blueprint, current_app, request
from flask_login import login_required, current_user

from app.extensions import db
from app.models import SearchHistory
from app.quota import (
    compute_quota_window,
    reserve_daily_quota,
    finalize_quota_reservation,
    release_quota_reservation,
    check_and_increment_ip_rate_limit,
    get_daily_quota_usage,
    get_client_ip,
    log_access_decision,
    QuotaInternalError,
    ModelOutputInvalidError,
    PER_IP_PER_MIN_LIMIT,
)
from app.utils.http_helpers import api_ok, api_error, get_request_id, is_owner_user, log_rejection
from app.utils.sanitization import sanitize_analyze_response, derive_missing_info
from app.utils.micro_reliability import compute_micro_reliability
from app.utils.timeline_plan import build_timeline_plan
from app.utils.sim_model import build_sim_model
from app.utils.validation import validate_analyze_request, ValidationError
from app.factory import (
    apply_mileage_logic,
    build_combined_prompt,
    get_ai_call_fn,
    current_user_daily_limit,
    normalize_text,
    MAX_CACHE_DAYS,
    QUOTA_RESERVATION_TTL_SECONDS,
)

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
    # Start timing
    start_time_ms = int(pytime.time() * 1000)
    logger = current_app.logger
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

    day_key, _, _, resets_at, _, retry_after_seconds = compute_quota_window(app_tz)
    resets_at_iso = resets_at.isoformat()
    cache_hit = False
    bypass_owner = owner_bypass_quota and is_owner_user()
    reservation_id = None
    reservation_finalized = False
    consumed_count = get_daily_quota_usage(current_user.id, day_key)
    reserved_count = 0
    quota_used_after = consumed_count
    display_quota_count = quota_used_after

    analyze_allowed_fields = {
        "make",
        "model",
        "year",
        "mileage_range",
        "fuel_type",
        "transmission",
        "sub_model",
        "annual_km",
        "city_pct",
        "terrain",
        "climate",
        "parking",
        "driver_style",
        "load",
        "mileage_km",
        "trim",
        "engine",
        "ownership_history",
        "budget",
        "budget_min",
        "budget_max",
        "usage_city_pct",
    }
    if not request.is_json:
        log_access_decision('/analyze', user_id, 'rejected', 'validation error: content-type')
        return api_error("invalid_content_type", "Content-Type must be application/json", status=415, details={"field": "payload"})

    try:
        data = request.get_json(silent=False) or {}
        if not data:
            return api_error("invalid_json", "Invalid JSON payload", status=400, details={"field": "payload"})

        validated = validate_analyze_request(data, allowed_fields=analyze_allowed_fields)

        logger.info(f"[ANALYZE 0/6] request_id={get_request_id()} user={current_user.id} payload validated")
        final_make = normalize_text(validated.get('make'))
        final_model = normalize_text(validated.get('model'))
        final_sub_model = normalize_text(validated.get('sub_model'))
        final_year = int(validated.get('year')) if validated.get('year') else None
        final_mileage = str(validated.get('mileage_range'))
        final_fuel = str(validated.get('fuel_type'))
        final_trans = str(validated.get('transmission'))
        usage_profile = validated.get("usage_profile") or {}
        cache_key = hashlib.sha256(
            json.dumps(
                {
                    "make": final_make,
                    "model": final_model,
                    "sub_model": final_sub_model,
                    "year": final_year,
                    "mileage_range": final_mileage,
                    "fuel_type": final_fuel,
                    "transmission": final_trans,
                    "usage_profile": usage_profile,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

        if not (final_make and final_model and final_year):
            log_access_decision('/analyze', user_id, 'rejected', 'validation error: missing required fields')
            return api_error("validation_error", "שגיאת קלט (שלב 0): נא למלא יצרן, דגם ושנה", status=400, details={"field": "payload"})
    except ValidationError as e:
        log_access_decision('/analyze', user_id, 'rejected', f'validation error: {e.field}')
        return api_error("validation_error", e.message, status=400, details={"field": e.field})
    except Exception:
        log_access_decision('/analyze', user_id, 'rejected', 'validation error: invalid payload')
        return api_error("validation_error", "שגיאת קלט (שלב 0): בקשת JSON לא תקינה.", status=400, details={"field": "payload"})

    # 1) Cache first (no quota impact on hit)
    try:
        cutoff_date = datetime.utcnow() - timedelta(days=MAX_CACHE_DAYS)
        cached = SearchHistory.query.filter(
            SearchHistory.user_id == current_user.id,
            SearchHistory.cache_key == cache_key,
            SearchHistory.timestamp >= cutoff_date
        ).order_by(SearchHistory.timestamp.desc()).first()
        if cached:
            cache_hit = True
            logger.info(
                "[CACHE] hit user_id=%s cache_key=%s request_id=%s",
                current_user.id,
                cache_key,
                get_request_id(),
            )
            result = json.loads(cached.result_json)
            if not all(k in result for k in ("micro_reliability", "timeline_plan", "sim_model")):
                micro = compute_micro_reliability(result, usage_profile)
                timeline = build_timeline_plan(usage_profile, micro, {**result, "mileage_range": final_mileage})
                sim = build_sim_model(usage_profile, micro, timeline)
                result.update(
                    {
                        "micro_reliability": micro,
                        "timeline_plan": timeline,
                        "sim_model": sim,
                    }
                )
                result = sanitize_analyze_response(result)
                try:
                    cached.result_json = json.dumps(result, ensure_ascii=False)
                    db.session.commit()
                except Exception:
                    db.session.rollback()
            result['source_tag'] = f"מקור: מטמון DB (נשמר ב-{cached.timestamp.strftime('%Y-%m-%d')})"
            result = sanitize_analyze_response(result)
            return api_ok(result)
    except Exception:
        try:
            if db.session.get_transaction() or db.session.is_active:
                db.session.rollback()
        except Exception:
            logger.exception("[CACHE] rollback failed after cache lookup error")
        logger.exception("[CACHE] cache lookup failed request_id=%s", get_request_id())
    if not cache_hit:
        logger.info(
            "[CACHE] miss user_id=%s cache_key=%s request_id=%s",
            current_user.id,
            cache_key,
            get_request_id(),
        )

    # 2) Quota enforcement (only on cache miss)
    limit_val = current_user_daily_limit()
    if not bypass_owner:
        try:
            allowed, consumed_count, reserved_count, reservation_id = reserve_daily_quota(
                current_user.id,
                day_key,
                limit_val,
                get_request_id(),
                now_utc=datetime.utcnow(),
            )
        except QuotaInternalError:
            log_rejection("server_error", "quota subsystem failure")
            return api_error(
                "quota_internal_error",
                "שגיאת שרת במערכת המכסות. נסה שוב מאוחר יותר.",
                status=500,
            )
        if not allowed:
            logger.warning(
                "[QUOTA] reject request_id=%s user=%s consumed=%s reserved_active=%s limit=%s day=%s",
                get_request_id(),
                user_id,
                consumed_count,
                reserved_count,
                limit_val,
                day_key.isoformat(),
            )
            if reserved_count > 0 and consumed_count < limit_val:
                retry_after = reservation_ttl
                resp = api_error(
                    "analysis_in_progress",
                    "בקשה קודמת עדיין בתהליך. נסה שוב בעוד רגע.",
                    status=409,
                    details={
                        "limit": limit_val,
                        "used": consumed_count,
                        "reserved": reserved_count,
                        "resets_at": resets_at_iso,
                    },
                )
                resp.headers["Retry-After"] = str(retry_after)
                return resp
            log_access_decision('/analyze', user_id, 'rejected', f'quota exceeded: {consumed_count}/{limit_val}')
            remaining = max(0, limit_val - (consumed_count + reserved_count))
            resp = api_error(
                "quota_exceeded",
                "שגיאת מגבלה: ניצלת את כל החיפושים להיום. נסה שוב מחר.",
                status=429,
                details={
                    "limit": limit_val,
                    "used": consumed_count,
                    "reserved": reserved_count,
                    "remaining": remaining,
                    "resets_at": resets_at_iso,
                },
            )
            resp.headers["Retry-After"] = str(retry_after_seconds)
            return resp
    quota_used_after = consumed_count
    if not cache_hit and not bypass_owner:
        display_quota_count = consumed_count + 1

    # 3) AI call (single grounded call)
    missing_info = derive_missing_info(validated)
    ai_output = {}
    try:
        if os.environ.get("SIMULATE_AI_FAIL", "").lower() in ("1", "true", "yes"):
            raise RuntimeError("SIMULATED_AI_FAILURE")
        prompt = build_combined_prompt(validated, missing_info)
        ai_call = get_ai_call_fn()
        model_output, ai_error = ai_call(prompt)
        if model_output is None:
            raise ModelOutputInvalidError(ai_error or "MODEL_JSON_INVALID")
        if not isinstance(model_output, dict):
            model_output = {}
        ai_output = model_output
    except ModelOutputInvalidError:
        if not bypass_owner:
            release_quota_reservation(reservation_id, current_user.id, day_key)
        return api_error("model_json_invalid", "פלט ה-AI לא הובן. נסה שוב בעוד רגע.", status=502)
    except Exception:
        if not bypass_owner:
            release_quota_reservation(reservation_id, current_user.id, day_key)
        log_rejection("server_error", "AI model call failed")
        traceback.print_exc()
        return api_error("ai_call_failed", "שגיאה בתקשורת עם מנוע ה-AI. נסה שוב מאוחר יותר.", status=500)

    # Ensure reliability_report presence even if malformed
    reliability_report = ai_output.get("reliability_report") if isinstance(ai_output, dict) else None
    if not isinstance(reliability_report, dict):
        ai_output["reliability_report"] = {"available": False, "reason": "MISSING_OR_INVALID"}

    # defaults for search data
    ai_output.setdefault("ok", True)
    ai_output.setdefault("search_performed", True)
    ai_output.setdefault("search_queries", [])
    ai_output.setdefault("sources", [])

    history_saved = False
    try:
        ai_output, note = apply_mileage_logic(ai_output, final_mileage)

        sanitized_output = {}
        try:
            ai_output['source_tag'] = f"מקור: ניתוח AI חדש (חיפוש {display_quota_count}/{limit_val})"
            ai_output['mileage_note'] = note
            ai_output['km_warn'] = False
            ai_output["micro_reliability"] = compute_micro_reliability(ai_output, usage_profile)
            ai_output["timeline_plan"] = build_timeline_plan(usage_profile, ai_output["micro_reliability"], {"mileage_range": final_mileage})
            ai_output["sim_model"] = build_sim_model(usage_profile, ai_output["micro_reliability"], ai_output["timeline_plan"])

            sanitized_output = sanitize_analyze_response(ai_output)

            # Calculate duration
            duration_ms = int(pytime.time() * 1000) - start_time_ms

            new_log = SearchHistory(
                user_id=current_user.id,
                cache_key=cache_key,
                make=final_make,
                model=final_model,
                year=final_year,
                mileage_range=final_mileage,
                fuel_type=final_fuel,
                transmission=final_trans,
                result_json=json.dumps(sanitized_output, ensure_ascii=False),
                duration_ms=duration_ms
            )
            db.session.add(new_log)
            db.session.commit()
            logger.info(
                "[CACHE] stored cache_key=%s user_id=%s request_id=%s",
                cache_key,
                current_user.id,
                get_request_id(),
            )
            history_saved = True
        except Exception as e:
            print(f"[DB] ⚠️ save failed: {e}")
            db.session.rollback()
            sanitized_output = sanitized_output or sanitize_analyze_response(ai_output)
    except Exception as e:
        if not bypass_owner:
            release_quota_reservation(reservation_id, current_user.id, day_key)
        log_rejection("server_error", f"Post-processing failed: {type(e).__name__}")
        traceback.print_exc()
        return api_error("analyze_postprocess_failed", "שגיאת שרת (שלב 5): נסה שוב מאוחר יותר.", status=500)

    if not bypass_owner:
        reservation_finalized, quota_used_after = finalize_quota_reservation(reservation_id, current_user.id, day_key)
        if not reservation_finalized:
            logger.error(
                "[QUOTA] finalize failed request_id=%s reservation_id=%s",
                get_request_id(),
                reservation_id,
            )
            release_quota_reservation(reservation_id, current_user.id, day_key)
            return api_error("quota_finalize_failed", "שגיאת שרת בעת עדכון המכסה.", status=500)
    else:
        quota_used_after = get_daily_quota_usage(current_user.id, day_key)

    logger.info(
        f"[QUOTA] method=POST path=/analyze uid={user_id} cache_hit={cache_hit} "
        f"consumed={quota_used_after} reserved_active={reserved_count} "
        f"limit={limit_val} resets_at={resets_at.isoformat()} request_id={get_request_id()}"
    )

    return api_ok(sanitized_output)


@bp.route('/api/timing/estimate', methods=['GET'])
@login_required
def timing_estimate():
    """
    Returns estimated timing for an endpoint.
    Calculates user-specific average/p75, fallback to global aggregated stats.
    """
    endpoint = request.args.get('endpoint', 'analyze')
    
    if endpoint != 'analyze':
        return api_error('INVALID_ENDPOINT', 'Only "analyze" endpoint is supported', status=400)
    
    try:
        # Try user-specific stats first
        user_records = db.session.query(SearchHistory.duration_ms).filter(
            SearchHistory.user_id == current_user.id,
            SearchHistory.duration_ms.isnot(None)
        ).order_by(SearchHistory.timestamp.desc()).limit(20).all()
        
        if user_records and len(user_records) >= 3:
            durations = [r[0] for r in user_records if r[0] is not None]
            avg_ms = int(sum(durations) / len(durations))
            sorted_durations = sorted(durations)
            p75_index = int(len(sorted_durations) * 0.75)
            p75_ms = sorted_durations[p75_index]
            
            return api_ok({
                'endpoint': 'analyze',
                'average_ms': avg_ms,
                'p75_ms': p75_ms,
                'sample_size': len(durations),
                'source': 'user'
            })
        
        # Fallback to global aggregated stats
        global_records = db.session.query(SearchHistory.duration_ms).filter(
            SearchHistory.duration_ms.isnot(None)
        ).order_by(SearchHistory.timestamp.desc()).limit(100).all()
        
        if global_records and len(global_records) >= 10:
            durations = [r[0] for r in global_records if r[0] is not None]
            avg_ms = int(sum(durations) / len(durations))
            sorted_durations = sorted(durations)
            p75_index = int(len(sorted_durations) * 0.75)
            p75_ms = sorted_durations[p75_index]
            
            return api_ok({
                'endpoint': 'analyze',
                'average_ms': avg_ms,
                'p75_ms': p75_ms,
                'sample_size': len(durations),
                'source': 'global'
            })
        
        # Default fallback if no data
        return api_ok({
            'endpoint': 'analyze',
            'average_ms': 15000,  # 15 seconds default
            'p75_ms': 20000,      # 20 seconds p75 default
            'sample_size': 0,
            'source': 'default'
        })
        
    except Exception as e:
        current_app.logger.error(f"Timing estimate error: {str(e)}")
        return api_error('ESTIMATE_FAILED', 'Failed to calculate timing estimate', status=500)
