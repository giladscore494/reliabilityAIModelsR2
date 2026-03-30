# -*- coding: utf-8 -*-
"""Analyze service logic."""

import os
import json
import hashlib
import logging
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
from app.utils.http_helpers import api_ok, api_error, get_request_id, log_rejection, _utcnow
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
from app.services.scoring_baseline import (
    get_make_profile,
    get_model_override,
    get_combined_score_modifier,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Deterministic reliability score & banner
# ---------------------------------------------------------------------------

# ── Banner thresholds (easy to tune) ──
_BANNER_HIGH_THRESHOLD = 67
_BANNER_MEDIUM_THRESHOLD = 45

_BANNER_MAP = {
    "high": "גבוה",
    "medium": "בינוני",
    "low": "נמוך",
    "unknown": "לא ידוע",
}

# ── Severity base penalty ──
_SEVERITY_PENALTY = {"low": 2, "medium": 4, "high": 7}

# ── Frequency multiplier ──
_FREQUENCY_MULT = {"rare": 0.7, "sometimes": 1.0, "common": 1.3}

# ── System tier multiplier (3 tiers: critical=1.25, standard=1.0, minor=0.7) ──
_SYSTEM_TIER = {
    # critical
    "engine": 1.25, "transmission": 1.25, "brakes": 1.25,
    "hv battery": 1.25, "hv_battery": 1.25,
    # standard
    "suspension": 1.0, "steering": 1.0, "ac": 1.0,
    "electrical": 1.0, "sensors": 1.0, "cooling": 1.0,
    # minor
    "infotainment": 0.7, "trim": 0.7, "cosmetic": 0.7,
}
_SYSTEM_TIER_DEFAULT = 1.0  # standard tier for unknown systems

# ── Systemic penalty cap ──
_SYSTEMIC_PENALTY_CAP = 40
_MAX_SIGNALS = 50

# ── Recall buckets ──
_RECALL_PENALTY = {"none": 0, "low": 1, "medium": 5, "high": 10}

# ── Maintenance cost pressure ──
_MCP_PENALTY = {"low": 0, "medium": 3, "high": 8}

# ── Clean bonus ──
_CLEAN_BONUS = 4

# ── Penalty cap: fraction of base that total penalties can consume (0.55 = 55%) ──
_PENALTY_CAP_FRACTION = 0.55


def _safe_int(val: Any, lo: int = 0, hi: int = 1000, default: int = 0) -> int:
    try:
        if isinstance(val, bool):
            return default
        n = int(float(val))
    except Exception:
        return default
    return max(lo, min(hi, n))


def _banner_from_score(score: int) -> str:
    if score >= _BANNER_HIGH_THRESHOLD:
        return "גבוה"
    if score >= _BANNER_MEDIUM_THRESHOLD:
        return "בינוני"
    return "נמוך"


def _confidence_label(c) -> str:
    """Accept a categorical confidence string (low/medium/high).

    For backward compatibility, also accepts a float and maps it to a label.
    """
    if isinstance(c, str) and c.lower() in ("high", "medium", "low"):
        return c.lower()
    try:
        f = float(c)
    except Exception:
        return "medium"
    if f >= 0.8:
        return "high"
    if f >= 0.6:
        return "medium"
    return "low"


def _classify_recall_bucket(count: int, high_severity_count: int) -> str:
    """Map recall counts into one of 4 deterministic buckets."""
    if count == 0:
        return "none"
    if high_severity_count >= 2 or count >= 5:
        return "high"
    if high_severity_count >= 1 or count >= 3:
        return "medium"
    return "low"


def _compute_confidence_category(risk_signals: dict, has_model_override: bool) -> str:
    """Derive a simple confidence category from signal quality indicators.

    Returns 'high', 'medium', or 'low'.  Used only for messaging / debug.
    """
    ac = risk_signals.get("analysis_confidence")
    if isinstance(ac, str) and ac.lower() in ("high", "medium", "low"):
        label = ac.lower()
    elif isinstance(ac, dict):
        level = str(ac.get("level", "")).lower()
        label = level if level in ("high", "medium", "low") else "medium"
    else:
        label = "medium"

    # Boost to high if we have a model override (well-known vehicle)
    if has_model_override and label == "medium":
        label = "high"

    return label


def compute_reliability_score_and_banner(
    validated_input: Dict[str, Any],
    risk_signals: Any,
) -> Dict[str, Any]:
    """Deterministic score (0-100) and Hebrew banner from risk_signals.

    Returns dict with keys: score_0_100, banner_he, confidence_label.
    """
    if not isinstance(risk_signals, dict) or not risk_signals:
        return {
            "score_0_100": 0,
            "banner_he": "לא ידוע",
            "confidence_label": "low",
        }

    # ── Step 1: baseline ──
    raw_make = str(validated_input.get("make") or "").strip()
    raw_model = str(validated_input.get("model") or "").strip()
    make_profile = get_make_profile(raw_make)
    model_override = get_model_override(raw_make, raw_model)
    score_mod, _conf_boost, _trans_default = get_combined_score_modifier(raw_make, raw_model)
    base = 62 + score_mod
    base = max(20, min(90, base))

    # ── Step 2: systemic issue penalties ──
    systemic_penalty = 0.0
    signals = risk_signals.get("systemic_issue_signals")
    has_meaningful_issues = False
    if isinstance(signals, list):
        for sig in signals[:_MAX_SIGNALS]:
            if not isinstance(sig, dict):
                continue
            system = str(sig.get("system", "")).lower()
            severity = str(sig.get("severity", "")).lower()
            freq = str(sig.get("repeat_frequency", "")).lower()

            sev_val = _SEVERITY_PENALTY.get(severity, 0)
            freq_mult = _FREQUENCY_MULT.get(freq, 1.0)
            sys_mult = _SYSTEM_TIER.get(system, _SYSTEM_TIER_DEFAULT)

            penalty = sev_val * freq_mult * sys_mult
            systemic_penalty += penalty
            if severity in ("medium", "high"):
                has_meaningful_issues = True
    systemic_penalty = min(systemic_penalty, _SYSTEMIC_PENALTY_CAP)

    # ── Step 3: recall penalty ──
    recalls = risk_signals.get("recalls") if isinstance(risk_signals.get("recalls"), dict) else {}
    recall_count = _safe_int(recalls.get("count"), lo=0, hi=100)
    high_sev_count = _safe_int(recalls.get("high_severity_count"), lo=0, hi=100)
    recall_bucket = _classify_recall_bucket(recall_count, high_sev_count)
    recall_penalty = _RECALL_PENALTY[recall_bucket] * make_profile["recall_multiplier"]
    has_meaningful_recalls = recall_bucket not in ("none", "low")

    # ── Step 4: maintenance cost pressure ──
    mcp = risk_signals.get("maintenance_cost_pressure")
    mcp_level = ""
    if isinstance(mcp, dict):
        mcp_level = str(mcp.get("level", "unknown")).lower()
    raw_mcp = _MCP_PENALTY.get(mcp_level, 0)
    mcp_penalty = raw_mcp * make_profile["mcp_multiplier"]

    # ── Step 5: total penalty with cap ──
    total_penalty = systemic_penalty + recall_penalty + mcp_penalty
    penalty_cap = base * _PENALTY_CAP_FRACTION
    total_penalty = min(total_penalty, penalty_cap)

    # ── Step 6: clean bonus ──
    bonus = 0
    if (
        make_profile.get("bonus_eligible")
        and not has_meaningful_issues
        and not has_meaningful_recalls
        and mcp_level in ("low", "")
    ):
        bonus = _CLEAN_BONUS

    # ── Step 7: final score & banner ──
    score = max(0, min(100, int(round(base - total_penalty + bonus))))
    banner = _banner_from_score(score)

    # ── Step 8: confidence (messaging only, does not affect score) ──
    confidence = _compute_confidence_category(risk_signals, model_override is not None)

    return {
        "score_0_100": score,
        "banner_he": banner,
        "confidence_label": confidence,
    }


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
                now_utc=_utcnow(),
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
        has_valid_risk_data = det["banner_he"] != "לא ידוע"
        ai_output["estimated_reliability"] = _banner_from_score(adjusted_score) if has_valid_risk_data else "לא ידוע"

        # Sync reliability_report
        if isinstance(ai_output.get("reliability_report"), dict):
            ai_output["reliability_report"]["overall_score"] = adjusted_score
            ai_output["reliability_report"]["confidence"] = det["confidence_label"]

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
            logger.warning("[DB] save failed: %s", e)
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
