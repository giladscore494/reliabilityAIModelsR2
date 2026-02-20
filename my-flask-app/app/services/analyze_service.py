# -*- coding: utf-8 -*-
"""Analyze service logic."""

import os
import json
import hashlib
import traceback
import time as pytime
from datetime import datetime, timedelta
from typing import Any, Dict, List

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
from app.utils.validation import validate_analyze_request, ValidationError
from app.factory import (
    apply_mileage_logic,
    build_combined_prompt,
    get_ai_call_fn,
    current_user_daily_limit,
    normalize_text,
    MAX_CACHE_DAYS,
)


# ---------------------------------------------------------------------------
# Deterministic reliability score & banner
# ---------------------------------------------------------------------------

_BANNER_MAP = {
    "high": "גבוה",
    "medium": "בינוני",
    "low": "נמוך",
    "unknown": "לא ידוע",
}

_EVIDENCE_FACTOR = {"strong": 1.0, "medium": 0.7, "weak": 0.4}


def _safe_float(val: Any, lo: float = 0.0, hi: float = 1.0, default: float = 0.0) -> float:
    try:
        if isinstance(val, bool):
            return default
        f = float(val)
    except Exception:
        return default
    return max(lo, min(hi, f))


def _safe_int(val: Any, lo: int = 0, hi: int = 1000, default: int = 0) -> int:
    try:
        if isinstance(val, bool):
            return default
        n = int(float(val))
    except Exception:
        return default
    return max(lo, min(hi, n))


def _banner_from_score(score: int) -> str:
    if score >= 70:
        return "גבוה"
    if score >= 45:
        return "בינוני"
    return "נמוך"


def _confidence_label(c: float) -> str:
    if c >= 0.8:
        return "high"
    if c >= 0.6:
        return "medium"
    return "low"


def compute_reliability_score_and_banner(
    validated_input: Dict[str, Any],
    risk_signals: Any,
) -> Dict[str, Any]:
    """Deterministic score (0-100) and Hebrew banner from risk_signals.

    Returns dict with keys: score_0_100, banner_he, confidence_0_1.
    """
    if not isinstance(risk_signals, dict) or not risk_signals:
        return {
            "score_0_100": 0,
            "banner_he": "לא ידוע",
            "confidence_0_1": 0.25,
        }

    # --- base ---
    base = 80

    # --- usage penalties (0..20) ---
    usage = validated_input.get("usage_profile") or {}
    usage_penalty = 0.0
    annual_km = 0
    try:
        annual_km = int(usage.get("annual_km", 15000) or 15000)
    except Exception:
        annual_km = 15000
    if annual_km > 30000:
        usage_penalty += 6
    elif annual_km > 20000:
        usage_penalty += 3

    try:
        city_pct = int(usage.get("city_pct", 50) or 50)
    except Exception:
        city_pct = 50
    if city_pct > 80:
        usage_penalty += 4
    elif city_pct > 60:
        usage_penalty += 2

    driver_style = str(usage.get("driver_style", "normal") or "normal").lower()
    if driver_style == "aggressive":
        usage_penalty += 5

    load = str(usage.get("load", "family") or "family").lower()
    if load == "heavy":
        usage_penalty += 5
    elif load == "light":
        usage_penalty += 0

    usage_penalty = min(usage_penalty, 20)

    # --- recalls ---
    recalls = risk_signals.get("recalls") if isinstance(risk_signals.get("recalls"), dict) else {}
    high_sev = _safe_int(recalls.get("high_severity_count"), lo=0, hi=100)
    other_recalls = max(0, _safe_int(recalls.get("count"), lo=0, hi=100) - high_sev)
    recall_penalty = min(high_sev * 8, 24) + min(other_recalls * 2, 10)

    # --- systemic issue signals (cap -40) ---
    systemic_penalty = 0.0
    signals = risk_signals.get("systemic_issue_signals")
    if isinstance(signals, list):
        for sig in signals[:_MAX_SIGNALS]:
            if not isinstance(sig, dict):
                continue
            system = str(sig.get("system", "")).lower()
            severity = str(sig.get("severity", "")).lower()
            freq = str(sig.get("repeat_frequency", "")).lower()
            ev = str(sig.get("evidence_strength", "medium")).lower()
            ev_factor = _EVIDENCE_FACTOR.get(ev, 0.7)

            raw_pen = 0.0
            if severity == "high" and freq == "common":
                if system == "transmission":
                    raw_pen = 18
                elif system == "engine":
                    raw_pen = 15
                elif system in ("electrical", "cooling"):
                    raw_pen = 8
                else:
                    raw_pen = 8
            elif severity == "medium" and freq == "common":
                raw_pen = 6
            elif severity == "low" and freq == "common":
                raw_pen = 2
            elif severity == "high" and freq == "sometimes":
                if system == "transmission":
                    raw_pen = 12
                elif system == "engine":
                    raw_pen = 10
                else:
                    raw_pen = 5
            elif severity == "medium" and freq == "sometimes":
                raw_pen = 3
            elif severity == "high" and freq == "rare":
                raw_pen = 4
            systemic_penalty += raw_pen * ev_factor
    systemic_penalty = min(systemic_penalty, 40)

    # --- maintenance cost pressure ---
    mcp = risk_signals.get("maintenance_cost_pressure")
    mcp_level = ""
    if isinstance(mcp, dict):
        mcp_level = str(mcp.get("level", "unknown")).lower()
    mcp_penalty = 0
    if mcp_level == "high":
        mcp_penalty = 10
    elif mcp_level == "medium":
        mcp_penalty = 5

    # --- final score ---
    total_penalty = usage_penalty + recall_penalty + systemic_penalty + mcp_penalty
    score = max(0, min(100, int(round(base - total_penalty))))
    banner = _banner_from_score(score)

    # --- confidence ---
    confidence = 0.85
    conf_meta = risk_signals.get("confidence_meta")
    if isinstance(conf_meta, dict):
        dc = _safe_float(conf_meta.get("data_completeness"), 0.0, 1.0, 0.5)
        sq = str(conf_meta.get("source_quality", "medium")).lower()
        if dc < 0.5:
            confidence -= 0.25
        elif dc < 0.7:
            confidence -= 0.15
        if sq == "low":
            confidence -= 0.15
        elif sq == "medium":
            confidence -= 0.05

    vr = risk_signals.get("vehicle_resolution")
    if isinstance(vr, dict):
        vr_conf = _safe_float(vr.get("confidence"), 0.0, 1.0, 0.5)
        if vr_conf < 0.7:
            confidence -= 0.10

    confidence = max(0.25, min(0.95, round(confidence, 2)))

    return {
        "score_0_100": score,
        "banner_he": banner,
        "confidence_0_1": confidence,
    }


_MAX_SIGNALS = 50  # cap to prevent abuse


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
        cache_key = None

        if not (final_make and final_model and final_year):
            log_access_decision('/analyze', user_id, 'rejected', 'validation error: missing required fields')
            return api_error("validation_error", "שגיאת קלט (שלב 0): נא למלא יצרן, דגם ושנה", status=400, details={"field": "payload"})
    except ValidationError as e:
        log_access_decision('/analyze', user_id, 'rejected', f'validation error: {e.field}')
        return api_error("validation_error", e.message, status=400, details={"field": e.field})
    except Exception:
        log_access_decision('/analyze', user_id, 'rejected', 'validation error: invalid payload')
        return api_error("validation_error", "שגיאת קלט (שלב 0): בקשת JSON לא תקינה.", status=400, details={"field": "payload"})

    # 1) Cache disabled: always perform new AI analysis
    cache_hit = False

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
        # --- Deterministic scoring (overrides any LLM-produced values) ---
        det = compute_reliability_score_and_banner(
            validated, ai_output.get("risk_signals"),
        )
        ai_output["base_score_calculated"] = det["score_0_100"]
        ai_output["estimated_reliability"] = det["banner_he"]

        # Apply mileage adjustment (modifies base_score_calculated in-place)
        ai_output, note = apply_mileage_logic(ai_output, final_mileage)

        # Re-derive banner from the mileage-adjusted score
        try:
            adjusted_score = int(round(float(ai_output.get("base_score_calculated", 0))))
        except Exception:
            adjusted_score = 0
        adjusted_score = max(0, min(100, adjusted_score))
        ai_output["base_score_calculated"] = adjusted_score
        ai_output["estimated_reliability"] = _banner_from_score(adjusted_score) if det["banner_he"] != "לא ידוע" else "לא ידוע"

        # Sync reliability_report
        if isinstance(ai_output.get("reliability_report"), dict):
            ai_output["reliability_report"]["overall_score"] = adjusted_score
            ai_output["reliability_report"]["confidence"] = _confidence_label(det["confidence_0_1"])

        sanitized_output: Dict[str, Any] = {}
        try:
            ai_output['source_tag'] = f"מקור: ניתוח AI חדש (חיפוש {display_quota_count}/{limit_val})"
            ai_output['mileage_note'] = note
            ai_output['km_warn'] = False
            ai_output.pop("reliability_score", None)
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
