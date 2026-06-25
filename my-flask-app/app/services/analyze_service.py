# -*- coding: utf-8 -*-
"""Analyze service logic."""

import os
import re as _re
import json
import logging
import traceback
import time as pytime
from typing import Any, Dict, Optional

from flask import current_app

from app.extensions import db
from app.models import SearchHistory
from app.utils.analytics import track_event
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
from app.utils.http_helpers import (
    _utcnow,
    api_error,
    api_ok,
    get_request_id,
    log_rejection,
)
from app.utils.sanitization import (
    sanitize_analyze_response,
    derive_missing_info,
    derive_information_status,
)
from app.utils.ai_guardrails import apply_feature_guardrails
from app.utils.validation import validate_analyze_request, ValidationError
from app.factory import (
    build_combined_prompt,
    get_ai_call_fn,
    current_user_daily_limit,
    mileage_adjustment,
    normalize_text,
)
from app.utils.production_observability import log_slow_operation
from app.services.gemini_grounding_client import GROUNDING_FAILED_CODE, GROUNDING_HE_MESSAGE
from app.services.vehicle_catalog_service import (
    CatalogUnavailableError,
    catalog_is_available,
    get_catalog_hash,
    resolve_vehicle_selection,
)

logger = logging.getLogger(__name__)

# Identity fields owned by the catalog; the AI must never overwrite them.
_LOCKED_IDENTITY_FIELDS = (
    "make",
    "model",
    "canonical_model",
    "version_or_trim",
    "body_type",
    "fuel_type",
    "engine",
    "engine_displacement_l",
    "horsepower_hp",
    "transmission",
    "drivetrain",
    "year_start",
    "year_end",
    "support_level",
)


def _build_identity_snapshot(resolution: Dict[str, Any]) -> Dict[str, Any]:
    """Server-owned identity snapshot derived strictly from the catalog."""
    snapshot = {"source": "catalog", "variant_id": resolution.get("variant_id"),
                "selected_year": resolution.get("selected_year")}
    for field in _LOCKED_IDENTITY_FIELDS:
        snapshot[field] = resolution.get(field)
    return snapshot


def _build_research_status(grounding_meta: Dict[str, Any], ai_output: Dict[str, Any]) -> Dict[str, Any]:
    """Honest research_status: grounding flags come from real call metadata."""
    meta = grounding_meta if isinstance(grounding_meta, dict) else {}
    grounded = bool(meta.get("grounding_successful"))
    source_count = int(meta.get("source_count") or 0)
    limitations = []
    rs = ai_output.get("research_status") if isinstance(ai_output.get("research_status"), dict) else {}
    if isinstance(rs.get("limitations"), list):
        limitations.extend([str(x) for x in rs["limitations"] if x])
    if not grounded:
        limitations.append("חיפוש האינטרנט לא אומת לבקשה זו — הראיות מוגבלות.")
    return {
        "web_search_required": True,
        "web_search_performed": grounded,
        "grounding_successful": grounded,
        "source_count": source_count,
        "limitations": limitations[:8],
    }


def _enforce_catalog_identity(ai_output: Dict[str, Any], resolution: Dict[str, Any], request_id: str) -> Dict[str, Any]:
    """Catalog identity always wins; AI identity fields are discarded."""
    snapshot = _build_identity_snapshot(resolution)
    ai_identity = ai_output.get("identity_snapshot")
    if isinstance(ai_identity, dict):
        for field in _LOCKED_IDENTITY_FIELDS:
            ai_val = ai_identity.get(field)
            cat_val = snapshot.get(field)
            if ai_val not in (None, "", []) and cat_val not in (None, "") and str(ai_val).strip().lower() != str(cat_val).strip().lower():
                logging.getLogger(__name__).warning(
                    "[ANALYZE] AI identity drift ignored field=%s ai=%s catalog=%s request_id=%s",
                    field, ai_val, cat_val, request_id,
                )
    ai_output["identity_snapshot"] = snapshot
    ai_output["catalog_resolution"] = {
        "resolution_status": resolution.get("resolution_status"),
        "variant_id": resolution.get("variant_id"),
        "profile_confidence": resolution.get("profile_confidence"),
        "catalog_hash": resolution.get("catalog_hash"),
        "catalog_generated_at": resolution.get("catalog_generated_at"),
        "missing_catalog_fields": resolution.get("missing_catalog_fields", []),
        "notes": resolution.get("notes", []),
    }
    return ai_output


_DEPRECATED_SCORE_KEYS = (
    "base_score_calculated",
    "estimated_reliability",
    "model_reliability_score",
    "model_reliability_label",
    "deal_risk_score",
    "deal_risk_label",
    "score_0_100",
    "banner_he",
)


def _validate_vehicle_profile_buyer_summary(ai_output: dict, request_id: str) -> dict:
    """Validate buyer_summary in vehicle_profile for forbidden content.

    If validation fails: set buyer_summary to None, add error.code to vehicle_profile,
    log a warning. Do NOT raise exception.
    """
    profile = ai_output.get("vehicle_profile")
    if not isinstance(profile, dict):
        return ai_output

    buyer_summary = profile.get("buyer_summary")
    if not isinstance(buyer_summary, str):
        return ai_output

    forbidden_patterns = [
        (r'\d+\s*/\s*100', 'numeric_score_100'),
        (r'\d+\s*/\s*10(?!\d)', 'numeric_score_10'),
        (r'\d+\s*%', 'percentage_score'),
        (r'(?:אני\s+ממליץ|הייתי\s+קונה|מומלץ\s+לקנות|כדאי\s+לקנות|אל\s+תקנה)', 'first_person_or_verdict'),
    ]

    for pattern, reason in forbidden_patterns:
        if _re.search(pattern, buyer_summary):
            logging.getLogger(__name__).warning(
                "[VEHICLE_PROFILE] buyer_summary rejected: forbidden_content=%s request_id=%s",
                reason, request_id,
            )
            profile["buyer_summary"] = None
            profile["_buyer_summary_rejected"] = reason
            ai_output["vehicle_profile"] = profile
            break

    return ai_output



def _adapt_catalog_first_reliability_output(ai_output: Dict[str, Any]) -> Dict[str, Any]:
    """Backfill legacy keys from the compact catalog-first review schema."""
    if not isinstance(ai_output, dict):
        return ai_output
    overview = ai_output.get("overview") if isinstance(ai_output.get("overview"), dict) else {}
    risk = ai_output.get("risk_analysis") if isinstance(ai_output.get("risk_analysis"), dict) else {}
    checklist = ai_output.get("buyer_checklist") if isinstance(ai_output.get("buyer_checklist"), dict) else {}
    ownership = ai_output.get("ownership_cost") if isinstance(ai_output.get("ownership_cost"), dict) else {}
    market = ai_output.get("market_context") if isinstance(ai_output.get("market_context"), dict) else {}
    identity = ai_output.get("identity_snapshot") if isinstance(ai_output.get("identity_snapshot"), dict) else {}

    ai_output.setdefault("reliability_summary", overview.get("based_on_available_information") or overview.get("plain_summary") or "")
    ai_output.setdefault("reliability_summary_simple", overview.get("plain_summary") or overview.get("based_on_available_information") or "")
    ai_output.setdefault("common_issues", [x.get("issue") for x in risk.get("systemic_issues", []) if isinstance(x, dict) and x.get("issue")])
    ai_output.setdefault("recommended_checks", (checklist.get("mechanical_inspection_points") or [])[:10])
    ai_output.setdefault("issues_with_costs", [
        {"issue": x.get("issue"), "avg_cost_ILS": 0, "source": x.get("source"), "severity": x.get("severity")}
        for x in (ownership.get("issue_cost_ranges") or []) if isinstance(x, dict)
    ])
    competitors = []
    for row in (market.get("competitors") or [])[:5]:
        if isinstance(row, dict):
            competitors.append({
                "model": row.get("model_name"),
                "brief_summary": row.get("why_relevant") or row.get("advantage_vs_reviewed_vehicle"),
            })
    ai_output.setdefault("common_competitors_brief", competitors)
    ai_output.setdefault("reliability_report", {
        "based_on_available_information": overview.get("based_on_available_information") or "הניתוח מבוסס על מידע ציבורי זמין ועל נתוני הקטלוג המקומי.",
        "key_risk_areas_to_examine": risk.get("top_risks") or [],
        "what_must_be_checked_before_a_decision": checklist,
        "known_uncertainties": risk.get("known_uncertainties") or [],
        "estimated_cost_sensitivity": ownership.get("cost_sensitivity_notes") or [],
        "final_line": ai_output.get("final_line") or "This information highlights areas to verify and is not a substitute for a professional inspection.",
    })
    ai_output.setdefault("risk_signals", {
        "vehicle_resolution": {
            "generation": None,
            "engine_family": identity.get("engine"),
            "transmission_type": identity.get("transmission") or "unknown",
        },
        "recalls": {"count": len(risk.get("recalls") or []), "items": risk.get("recalls") or [], "notes": ""},
        "systemic_issue_signals": risk.get("systemic_issues") or [],
        "maintenance_cost_pressure": {"level": ownership.get("maintenance_cost_pressure") or "unknown", "explanation": ""},
        "analysis_confidence": (ai_output.get("catalog_resolution") or {}).get("confidence") or "low",
        "missing_data_flags": risk.get("known_uncertainties") or [],
    })
    # NOTE: We intentionally do NOT synthesize a legacy ``vehicle_profile`` when
    # the model omits one. The canonical identity is ``identity_snapshot`` (built
    # server-side from the catalog); ``vehicle_profile`` only passes through when
    # the model actually returned it, preserving the "absent when not provided"
    # contract the UI relies on.
    return ai_output

def derive_information_quality_review(
    validated_input: Dict[str, Any],
    risk_signals: Any,
    overall_reliability_estimate: Any = None,
    model_output: Any = None,
    mileage_range: Optional[str] = None,
) -> Dict[str, Any]:
    """Compute information-quality outputs for the reliability review flow."""
    payload = model_output if isinstance(model_output, dict) else {}
    mileage_note = mileage_adjustment(mileage_range or "")[1]
    info_status = derive_information_status(
        {
            "search_performed": payload.get("search_performed"),
            "sources": payload.get("sources"),
            "recommended_checks": payload.get("recommended_checks"),
            "reliability_report": payload.get("reliability_report"),
            "risk_signals": risk_signals if isinstance(risk_signals, dict) else {},
            "missing_critical_info": payload.get("missing_critical_info"),
            "verification_focus": payload.get("verification_focus"),
            "data_quality_label": payload.get("data_quality_label"),
            "decision_readiness": payload.get("decision_readiness"),
        },
        payload=validated_input,
    )
    info_status["mileage_note"] = mileage_note
    return info_status


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
    grounding_duration_ms = 0
    sanitization_duration_ms = 0
    guardrail_duration_ms = 0
    history_id = None

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
        "variant_id",
        "version_or_trim",
        "body_type",
        "catalog_fuel_type",
        "catalog_engine",
        "catalog_horsepower_hp",
        "catalog_transmission",
        "catalog_drivetrain",
    }

    try:
        validated = validate_analyze_request(
            data,
            allowed_fields=analyze_allowed_fields,
        )

        logger.info(
            "[ANALYZE 0/6] request_id=%s user=%s payload validated",
            get_request_id(),
            user_id,
        )
        final_make = normalize_text(validated.get("make"))
        final_model = normalize_text(validated.get("model"))
        final_year = int(validated.get("year")) if validated.get("year") else None
        final_mileage = str(validated.get("mileage_range"))
        final_fuel = str(validated.get("fuel_type"))
        final_trans = str(validated.get("transmission"))
        cache_key = None

        if not (final_make and final_model and final_year):
            log_access_decision(
                "/analyze",
                user_id,
                "rejected",
                "validation error: missing required fields",
            )
            return api_error(
                "validation_error",
                "שגיאת קלט (שלב 0): נא למלא יצרן, דגם ושנה",
                status=400,
                details={"field": "payload"},
            )

        # --- Catalog-first identity resolution (catalog decides identity) ---
        try:
            if not catalog_is_available():
                return api_error(
                    "catalog_unavailable",
                    "מאגר הרכבים אינו זמין כעת. נסה שוב מאוחר יותר.",
                    status=503,
                )
            resolution = resolve_vehicle_selection(validated)
        except CatalogUnavailableError:
            log_rejection("server_error", "catalog unreadable")
            return api_error(
                "catalog_unavailable",
                "מאגר הרכבים אינו קריא כעת. נסה שוב מאוחר יותר.",
                status=503,
            )
        # Ambiguous selection without a chosen variant → ask the UI to choose;
        # never guess (PART 9).
        if resolution.get("resolution_status") == "ambiguous" and not validated.get("variant_id"):
            return api_ok(
                {
                    "needs_variant_selection": True,
                    "catalog_resolution": {
                        "resolution_status": "ambiguous",
                        "make": resolution.get("make"),
                        "model": resolution.get("model"),
                        "selected_year": resolution.get("selected_year"),
                        "catalog_hash": resolution.get("catalog_hash"),
                    },
                    "ambiguity_options": resolution.get("ambiguity_options", []),
                    "message": "נמצאו כמה גרסאות טכניות תואמות. בחר/י גרסה מדויקת כדי להמשיך.",
                    "request_id": get_request_id(),
                }
            )
    except ValidationError as e:
        log_access_decision(
            "/analyze",
            user_id,
            "rejected",
            f"validation error: {e.field}",
        )
        return api_error(
            "validation_error",
            e.message,
            status=400,
            details={"field": e.field},
        )
    except Exception:
        log_access_decision(
            "/analyze",
            user_id,
            "rejected",
            "validation error: invalid payload",
        )
        return api_error(
            "validation_error",
            "שגיאת קלט (שלב 0): בקשת JSON לא תקינה.",
            status=400,
            details={"field": "payload"},
        )

    # 1) Cache disabled: always perform new AI analysis
    cache_hit = False

    # 2) Quota enforcement (only on cache miss)
    limit_val = current_user_daily_limit()
    if not bypass_owner:
        try:
            allowed, consumed_count, reserved_count, reservation_id = (
                reserve_daily_quota(
                    user_id,
                    day_key,
                    limit_val,
                    get_request_id(),
                    now_utc=_utcnow(),
                )
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
                (
                    "[QUOTA] reject request_id=%s user=%s consumed=%s "
                    "reserved_active=%s limit=%s day=%s"
                ),
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
            log_access_decision(
                "/analyze",
                user_id,
                "rejected",
                f"quota exceeded: {consumed_count}/{limit_val}",
            )
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
        prompt = build_combined_prompt(validated, missing_info, resolution=resolution)
        # Inject compact user_context_for_reasoning (no PII) when available.
        # Optional context that may improve AI personalization without distorting
        # vehicle reliability factuality. Safe to skip if no data / consent.
        try:
            from app.utils.ai_context import build_user_context_for_reasoning

            _user_ctx = build_user_context_for_reasoning(user_id, validated)
            if _user_ctx:
                import json as _json

                prompt = (
                    f"{prompt}\n\n"
                    f"user_context_for_reasoning: "
                    f"{_json.dumps(_user_ctx, ensure_ascii=False)}"
                )
        except Exception:
            # Never let optional context block the AI call.
            logger.debug("[AI] user_context_for_reasoning skipped", exc_info=True)
        ai_call = get_ai_call_fn()
        model_start = pytime.perf_counter()
        model_output, ai_error = ai_call(prompt)
        model_duration_ms = int((pytime.perf_counter() - model_start) * 1000)
        log_slow_operation(logger, feature="vehicle_review", stage="model_call", duration_ms=model_duration_ms, request_id=get_request_id())
        logger.info("[ANALYZE_TIMING] request_id=%s stage=model_call duration_ms=%s", get_request_id(), model_duration_ms)
        if ai_error == "CALL_TIMEOUT":
            if not bypass_owner:
                release_quota_reservation(reservation_id, user_id, day_key)
            return api_error(
                "ai_timeout", "תשובת ה-AI התעכבה. נסה שוב מאוחר יותר.", status=504
            )
        if ai_error == GROUNDING_FAILED_CODE:
            if not bypass_owner:
                release_quota_reservation(reservation_id, user_id, day_key)
            return api_error(GROUNDING_FAILED_CODE, GROUNDING_HE_MESSAGE, status=503)
        if model_output is None:
            raise ModelOutputInvalidError(ai_error or "MODEL_JSON_INVALID")
        if not isinstance(model_output, dict):
            model_output = {}
        ai_output = model_output
        grounding_meta_start = pytime.perf_counter()
        grounding_meta = ai_output.pop("_grounding_meta", {}) if isinstance(ai_output, dict) else {}
        grounding_duration_ms = int((pytime.perf_counter() - grounding_meta_start) * 1000)
        logger.info("[ANALYZE_TIMING] request_id=%s stage=grounding_metadata duration_ms=%s grounding_successful=%s source_count=%s", get_request_id(), grounding_duration_ms, grounding_meta.get("grounding_successful"), grounding_meta.get("source_count"))
        ai_output = _enforce_catalog_identity(ai_output, resolution, get_request_id())
        ai_output["research_status"] = _build_research_status(grounding_meta, ai_output)
        ai_output = _adapt_catalog_first_reliability_output(ai_output)
        ai_output = _validate_vehicle_profile_buyer_summary(ai_output, get_request_id())
    except ModelOutputInvalidError:
        if not bypass_owner:
            release_quota_reservation(reservation_id, user_id, day_key)
        return api_error(
            "model_json_invalid", "פלט ה-AI לא הובן. נסה שוב בעוד רגע.", status=502
        )
    except Exception:
        if not bypass_owner:
            release_quota_reservation(reservation_id, user_id, day_key)
        log_rejection("server_error", "AI model call failed")
        traceback.print_exc()
        return api_error(
            "ai_call_failed",
            "שגיאה בתקשורת עם מנוע ה-AI. נסה שוב מאוחר יותר.",
            status=500,
        )

    # Ensure reliability_report presence even if malformed
    reliability_report = (
        ai_output.get("reliability_report") if isinstance(ai_output, dict) else None
    )
    if not isinstance(reliability_report, dict):
        ai_output["reliability_report"] = {
            "available": False,
            "reason": "MISSING_OR_INVALID",
        }

    # defaults for search data. search_performed reflects REAL grounding only
    # (no fake claim) — research_status is the source of truth.
    ai_output.setdefault("ok", True)
    _rs = ai_output.get("research_status") if isinstance(ai_output.get("research_status"), dict) else {}
    ai_output["search_performed"] = bool(_rs.get("grounding_successful"))
    ai_output.setdefault("search_queries", [])
    ai_output.setdefault("sources", [])

    try:
        # --- Information-quality review summary ---
        det = derive_information_quality_review(
            validated,
            ai_output.get("risk_signals"),
            ai_output.get("overall_reliability_estimate"),
            model_output=ai_output,
            mileage_range=final_mileage,
        )
        ai_output["data_quality_label"] = det["data_quality_label"]
        ai_output["decision_readiness"] = det["decision_readiness"]
        ai_output["missing_critical_info"] = det["missing_critical_info"]
        ai_output["verification_focus"] = det["verification_focus"]

        sanitized_output: Dict[str, Any] = {}
        try:
            ai_output["source_tag"] = (
                f"מקור: ניתוח AI חדש (חיפוש {display_quota_count}/{limit_val})"
            )
            ai_output["mileage_note"] = det.get("mileage_note")
            ai_output["km_warn"] = False
            ai_output.pop("reliability_score", None)
            for deprecated_key in _DEPRECATED_SCORE_KEYS:
                ai_output.pop(deprecated_key, None)
            sanitization_start = pytime.perf_counter()
            sanitized_output = sanitize_analyze_response(ai_output)
            sanitization_duration_ms = int((pytime.perf_counter() - sanitization_start) * 1000)
            guardrail_start = pytime.perf_counter()
            sanitized_output, validation_report = apply_feature_guardrails(
                "reliability_analysis",
                validated,
                sanitized_output,
            )
            guardrail_duration_ms = int((pytime.perf_counter() - guardrail_start) * 1000)
            logger.info("[ANALYZE_TIMING] request_id=%s stage=sanitization duration_ms=%s", get_request_id(), sanitization_duration_ms)
            logger.info("[ANALYZE_TIMING] request_id=%s stage=guardrail duration_ms=%s critical_count=%s warning_count=%s", get_request_id(), guardrail_duration_ms, len(validation_report.get("critical_issues", [])), len(validation_report.get("warnings", [])))
            if validation_report.get("critical_issues"):
                logger.warning(
                    "[GUARDRAIL] blocked original reliability result request_id=%s critical=%s",
                    get_request_id(),
                    validation_report.get("critical_issues"),
                )

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
                duration_ms=model_duration_ms,
            )
            db.session.add(new_log)
            db.session.commit()
            history_id = new_log.id
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
            sanitized_output, _ = apply_feature_guardrails(
                "reliability_analysis",
                validated,
                sanitized_output,
            )
    except Exception as e:
        if not bypass_owner:
            release_quota_reservation(reservation_id, user_id, day_key)
        log_rejection("server_error", f"Post-processing failed: {type(e).__name__}")
        traceback.print_exc()
        return api_error(
            "analyze_postprocess_failed",
            "שגיאת שרת (שלב 5): נסה שוב מאוחר יותר.",
            status=500,
        )

    if not bypass_owner:
        reservation_finalized, quota_used_after = finalize_quota_reservation(
            reservation_id, user_id, day_key
        )
        if not reservation_finalized:
            logger.error(
                "[QUOTA] finalize failed request_id=%s reservation_id=%s",
                get_request_id(),
                reservation_id,
            )
            release_quota_reservation(reservation_id, user_id, day_key)
            return api_error(
                "quota_finalize_failed", "שגיאת שרת בעת עדכון המכסה.", status=500
            )
    else:
        quota_used_after = get_daily_quota_usage(user_id, day_key)

    total_duration_ms = int(pytime.time() * 1000) - int(start_time_ms)
    log_slow_operation(logger, feature="vehicle_review", stage="total", duration_ms=total_duration_ms, request_id=get_request_id())
    logger.info(
        "[ANALYZE_TIMING] request_id=%s total_ms=%s model_ms=%s grounding_ms=%s sanitization_ms=%s guardrail_ms=%s",
        get_request_id(), total_duration_ms, model_duration_ms, grounding_duration_ms, sanitization_duration_ms, guardrail_duration_ms,
    )

    logger.info(
        f"[QUOTA] method=POST path=/analyze uid={user_id} cache_hit={cache_hit} quota_bypass_reason={'owner/admin' if bypass_owner else 'none'} "
        f"consumed={quota_used_after} reserved_active={reserved_count} "
        f"limit={limit_val} resets_at={resets_at.isoformat()} "
        f"request_id={get_request_id()}"
    )

    response_payload = dict(sanitized_output)
    response_payload["history_id"] = history_id
    response_payload["request_id"] = get_request_id()
    report_payload = (
        response_payload.get("reliability_report")
        if isinstance(response_payload.get("reliability_report"), dict)
        else {}
    )
    for field_name in (
        "based_on_available_information",
        "key_risk_areas_to_examine",
        "what_must_be_checked_before_a_decision",
        "known_uncertainties",
        "estimated_cost_sensitivity",
    ):
        if field_name in report_payload and field_name not in response_payload:
            response_payload[field_name] = report_payload[field_name]

    # PostHog: analyze_completed
    try:
        track_event(
            str(user_id),
            "analyze_completed",
            {"cache_hit": cache_hit, "request_id": get_request_id()},
        )
    except Exception:
        pass

    return api_ok(response_payload)
