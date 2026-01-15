# -*- coding: utf-8 -*-
"""Analyze service logic."""

import os
import json
import hashlib
import traceback
import time as pytime
from datetime import datetime, timedelta
from typing import Any, Dict

from flask import current_app

from app.extensions import db
from app.models import SearchHistory
from app.quota import (
    compute_quota_window,
    reserve_daily_quota,
    finalize_quota_reservation,
    release_quota_reservation,
    get_daily_quota_usage,
    log_access_decision,
    QuotaInternalError,
    ModelOutputInvalidError,
)
from app.utils.http_helpers import api_ok, api_error, get_request_id, log_rejection
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
)


def handle_analyze_request(
    data: Dict[str, Any],
    *,
    app_tz,
    start_time_ms: int,
    bypass_owner: bool,
    reservation_ttl: int,
    user_id: int,
):
    logger = current_app.logger

    day_key, _, _, resets_at, _, retry_after_seconds = compute_quota_window(app_tz)
    resets_at_iso = resets_at.isoformat()
    cache_hit = False
    reservation_id = None
    consumed_count = get_daily_quota_usage(user_id, day_key)
    reserved_count = 0
    quota_used_after = consumed_count
    display_quota_count = quota_used_after
    model_duration_ms = 0

    analyze_allowed_fields = {
        "make",
        "model",
        "year",
        "mileage_range",
        "fuel_type",
        "transmission",
        "sub_model",
        "legal_confirm",
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

    try:
        validated = validate_analyze_request(data, allowed_fields=analyze_allowed_fields)

        logger.info(f"[ANALYZE 0/6] request_id={get_request_id()} user={user_id} payload validated")
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
            SearchHistory.user_id == user_id,
            SearchHistory.cache_key == cache_key,
            SearchHistory.timestamp >= cutoff_date
        ).order_by(SearchHistory.timestamp.desc()).first()
        if cached:
            cache_hit = True
            logger.info(
                "[CACHE] hit user_id=%s cache_key=%s request_id=%s",
                user_id,
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
            user_id,
            cache_key,
            get_request_id(),
        )

    # 2) Quota enforcement (only on cache miss)
    limit_val = current_user_daily_limit()
    if not bypass_owner:
        try:
            allowed, consumed_count, reserved_count, reservation_id = reserve_daily_quota(
                user_id,
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
    else:
        reserved_count = 0
    quota_used_after = consumed_count
    if not cache_hit and not bypass_owner:
        display_quota_count = consumed_count + 1

    # 3) AI call (single grounded call)
    missing_info = derive_missing_info(validated)
    ai_output: Dict[str, Any] = {}
    try:
        if os.environ.get("SIMULATE_AI_FAIL", "").lower() in ("1", "true", "yes"):
            raise RuntimeError("SIMULATED_AI_FAILURE")
        prompt = build_combined_prompt(validated, missing_info)
        ai_call = get_ai_call_fn()
        model_start = pytime.perf_counter()
        model_output, ai_error = ai_call(prompt)
        model_duration_ms = int((pytime.perf_counter() - model_start) * 1000)
        if ai_error == "CALL_TIMEOUT":
            if not bypass_owner:
                release_quota_reservation(reservation_id, user_id, day_key)
            return api_error("ai_timeout", "תשובת ה-AI התעכבה. נסה שוב מאוחר יותר.", status=504)
        if model_output is None:
            raise ModelOutputInvalidError(ai_error or "MODEL_JSON_INVALID")
        if not isinstance(model_output, dict):
            model_output = {}
        ai_output = model_output
    except ModelOutputInvalidError:
        if not bypass_owner:
            release_quota_reservation(reservation_id, user_id, day_key)
        return api_error("model_json_invalid", "פלט ה-AI לא הובן. נסה שוב בעוד רגע.", status=502)
    except Exception:
        if not bypass_owner:
            release_quota_reservation(reservation_id, user_id, day_key)
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

    try:
        ai_output, note = apply_mileage_logic(ai_output, final_mileage)

        sanitized_output: Dict[str, Any] = {}
        try:
            ai_output['source_tag'] = f"מקור: ניתוח AI חדש (חיפוש {display_quota_count}/{limit_val})"
            ai_output['mileage_note'] = note
            ai_output['km_warn'] = False
            ai_output["micro_reliability"] = compute_micro_reliability(ai_output, usage_profile)
            ai_output["timeline_plan"] = build_timeline_plan(usage_profile, ai_output["micro_reliability"], {"mileage_range": final_mileage})
            ai_output["sim_model"] = build_sim_model(usage_profile, ai_output["micro_reliability"], ai_output["timeline_plan"])

            sanitized_output = sanitize_analyze_response(ai_output)

            new_log = SearchHistory(
                user_id=user_id,
                cache_key=cache_key,
                make=final_make,
                model=final_model,
                year=final_year,
                mileage_range=final_mileage,
                fuel_type=final_fuel,
                transmission=final_trans,
                result_json=json.dumps(sanitized_output, ensure_ascii=False),
                duration_ms=model_duration_ms
            )
            db.session.add(new_log)
            db.session.commit()
            logger.info(
                "[CACHE] stored cache_key=%s user_id=%s request_id=%s",
                cache_key,
                user_id,
                get_request_id(),
            )
        except Exception as e:
            print(f"[DB] ⚠️ save failed: {e}")
            db.session.rollback()
            sanitized_output = sanitized_output or sanitize_analyze_response(ai_output)
    except Exception as e:
        if not bypass_owner:
            release_quota_reservation(reservation_id, user_id, day_key)
        log_rejection("server_error", f"Post-processing failed: {type(e).__name__}")
        traceback.print_exc()
        return api_error("analyze_postprocess_failed", "שגיאת שרת (שלב 5): נסה שוב מאוחר יותר.", status=500)

    if not bypass_owner:
        reservation_finalized, quota_used_after = finalize_quota_reservation(reservation_id, user_id, day_key)
        if not reservation_finalized:
            logger.error(
                "[QUOTA] finalize failed request_id=%s reservation_id=%s",
                get_request_id(),
                reservation_id,
            )
            release_quota_reservation(reservation_id, user_id, day_key)
            return api_error("quota_finalize_failed", "שגיאת שרת בעת עדכון המכסה.", status=500)
    else:
        quota_used_after = get_daily_quota_usage(user_id, day_key)

    logger.info(
        f"[QUOTA] method=POST path=/analyze uid={user_id} cache_hit={cache_hit} "
        f"consumed={quota_used_after} reserved_active={reserved_count} "
        f"limit={limit_val} resets_at={resets_at.isoformat()} request_id={get_request_id()}"
    )

    return api_ok(sanitized_output)
