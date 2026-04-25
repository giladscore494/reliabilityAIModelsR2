# -*- coding: utf-8 -*-
"""Analyze service logic."""

import os
import json
import hashlib
import logging
import traceback
import time as pytime
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set

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
from app.utils.http_helpers import api_ok, api_error, get_request_id, log_rejection, _utcnow
from app.utils.sanitization import sanitize_analyze_response, derive_missing_info
from app.utils.validation import validate_analyze_request, ValidationError
from app.factory import (
    build_combined_prompt,
    get_ai_call_fn,
    current_user_daily_limit,
    mileage_adjustment,
    normalize_text,
    MAX_CACHE_DAYS,
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
_SEVERITY_PENALTY = {"low": 1, "medium": 3, "high": 5}

# ── Frequency multiplier ──
_FREQUENCY_MULT = {"rare": 0.7, "sometimes": 1.0, "common": 1.0}

# ── System tier multiplier (3 tiers: critical=1.25, standard=1.0, minor=0.7) ──
_SYSTEM_TIER = {
    # critical
    "engine": 1.1, "transmission": 1.1, "brakes": 1.1,
    "hv battery": 1.1, "hv_battery": 1.1,
    # standard
    "suspension": 1.0, "steering": 1.0, "ac": 1.0,
    "electrical": 1.0, "sensors": 1.0, "cooling": 1.0,
    # minor
    "infotainment": 0.5, "trim": 0.5, "cosmetic": 0.5,
}
_SYSTEM_TIER_DEFAULT = 1.0  # standard tier for unknown systems

# ── Systemic penalty cap ──
_SYSTEMIC_PENALTY_CAP = 25
_MAX_SIGNALS = 50

# Recall penalty by severity tier:
# low = infotainment, cosmetic, convenience → zero penalty
# medium = AC, sensors, non-safety electrical → minor penalty per recall
# high = engine, transmission, brakes, cooling, steering, safety → significant penalty per recall
_RECALL_SEVERITY_PENALTY = {"low": 0, "medium": 1, "high": 3}
_RECALL_TOTAL_CAP = 9

# ── Maintenance cost pressure ──
_MCP_PENALTY = {"low": 0, "medium": 0, "high": 0}

# ── Clean bonus ──
_CLEAN_BONUS = 6

# ── Penalty cap: fraction of base that total penalties can consume (0.55 = 55%) ──
_PENALTY_CAP_FRACTION = 0.40

# ── Overall model-level reliability anchor (modest adjustment) ──
_OVERALL_RELIABILITY_ADJUSTMENT = {
    "high": 10,
    "medium": 0,
    "low": -10,
}
_MODEL_PRIMARY_BASE_SCORE = 80
_MODEL_JSON_RELIABILITY_BIAS = {"strong": 2, "neutral": 0, "weak": -2}
_MODEL_JSON_SENSITIVITY_SCALE = {"low": 0.7, "normal": 1.0, "high": 1.3}
_MODEL_JSON_CONFIDENCE_ALLOWED = {"low", "medium", "high"}
_MODEL_JSON_BIAS_ALLOWED = set(_MODEL_JSON_RELIABILITY_BIAS.keys())
_MODEL_JSON_SENSITIVITY_ALLOWED = set(_MODEL_JSON_SENSITIVITY_SCALE.keys())
_DEAL_RISK_MEDIUM_THRESHOLD = 25
_DEAL_RISK_HIGH_THRESHOLD = 55

_RECALL_LIKE_SIGNAL_FACTOR = 0.55
_RECALL_OVERLAP_DISCOUNT = 0.85
_RECALL_NOTES_TOKEN_MIN = 2
_RECALL_LIKE_STOPWORDS = {
    "the", "and", "with", "from", "that", "this", "issue", "issues", "problem",
    "problems", "risk", "failure", "failures", "system", "official", "vehicle",
    "vehicles", "models", "owner", "owners", "service", "campaign", "recall",
    "notice", "update", "software", "dealer", "repair", "replace", "inspection",
    "warning", "common", "sometimes", "rare", "בעיה", "בעיות", "תקלה", "תקלות",
    "סיכון", "כשל", "מערכת", "רכב", "רכבים", "בעלים", "שירות", "קמפיין", "ריקול",
    "עדכון", "תוכנה", "בדיקה", "החלפה", "אזהרה",
}
_RECALL_LIKE_MARKERS = (
    "recall",
    "ריקול",
    "campaign",
    "service campaign",
    "customer satisfaction program",
    "service action",
    "field action",
    "safety notice",
    "safety campaign",
    "קמפיין שירות",
    "קריאת שירות",
    "הודעת בטיחות",
)
_RECALL_REMEDY_MARKERS = (
    "software update",
    "software fix",
    "dealer update",
    "dealer inspection",
    "ota update",
    "reprogram",
    "reflash",
    "remedy",
    "factory fix",
    "עדכון תוכנה",
    "תכנות מחדש",
    "בדיקת יצרן",
)
_RECALL_PATTERN_HINTS = (
    r"\bbolt (loosening|loose)\b",
    r"\b(cluster|instrument).{0,20}\bblackout\b",
    r"\bblackout\b.{0,20}\b(cluster|instrument)\b",
    r"\b(inverter|dc-?dc).{0,20}\b(failure|risk)\b",
    r"\b(failure|risk)\b.{0,20}\b(inverter|dc-?dc)\b",
    r"\b(brake|braking|abs).{0,20}\bsoftware\b",
    r"\bsoftware\b.{0,20}\b(brake|braking|abs)\b",
    r"\b(loss of braking|loss of drive|fire risk)\b",
    r"ברגים משתחררים",
    r"כשל אינוורטר",
    r"עדכון תוכנה",
    r"לוח מחוונים.{0,20}כבה",
)

_NEGLECT_MARKERS_LITERAL = (
    "incomplete service history",
    "missing service history",
    "likely neglected by previous owner",
    "maintenance history is incomplete",
    "services were skipped",
    "unresolved recall",
    "היסטוריית טיפולים חסרה",
    "היסטוריית טיפולים לא מלאה",
    "הוזנח",
    "תחזוקה לקויה",
    "דילוג על טיפולים",
    "ריקול לא טופל",
)
_NEGLECT_MARKERS_WORD = (
    "likely neglected",
    "poor maintenance",
    "skipped service",
    "abuse",
    "neglect",
)


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


def _compute_confidence_category(risk_signals: dict) -> str:
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
    return label


def _overall_reliability_adjustment(value: Any) -> int:
    if isinstance(value, str):
        return _OVERALL_RELIABILITY_ADJUSTMENT.get(value.strip().lower(), 0)
    return 0


def _normalized_reliability_estimate(value: Any) -> Optional[str]:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("high", "medium", "low"):
            return normalized
    return None


def _derive_model_estimate_from_signals(risk_signals: Dict[str, Any]) -> str:
    recalls = risk_signals.get("recalls") if isinstance(risk_signals.get("recalls"), dict) else {}
    raw_recall_items = recalls.get("items")
    recall_items = raw_recall_items if isinstance(raw_recall_items, list) else []
    has_high_recall = False
    has_meaningful_recall = False
    for item in recall_items[:20]:
        if not isinstance(item, dict):
            continue
        sev = str(item.get("severity", "")).lower()
        if sev == "high":
            has_high_recall = True
            has_meaningful_recall = True
            break
        if sev == "medium":
            has_meaningful_recall = True

    if not recall_items:
        recall_count = _safe_int(recalls.get("count"), lo=0, hi=100)
        high_sev_count = _safe_int(recalls.get("high_severity_count"), lo=0, hi=100)
        has_high_recall = high_sev_count > 0
        has_meaningful_recall = high_sev_count > 0 or recall_count >= 3

    mcp = risk_signals.get("maintenance_cost_pressure") if isinstance(risk_signals.get("maintenance_cost_pressure"), dict) else {}
    mcp_level = str(mcp.get("level", "")).lower()

    has_high_issue = False
    has_meaningful_issue = False
    signals = risk_signals.get("systemic_issue_signals")
    if isinstance(signals, list):
        for sig in signals[:_MAX_SIGNALS]:
            if not isinstance(sig, dict):
                continue
            if (
                _contains_vehicle_specific_neglect_claim(sig.get("issue"))
                or _contains_vehicle_specific_neglect_claim(sig.get("evidence_text"))
                or _contains_vehicle_specific_neglect_claim(sig.get("typical_timing"))
            ):
                continue
            system = str(sig.get("system", "")).lower()
            severity = str(sig.get("severity", "")).lower()
            sys_mult = _SYSTEM_TIER.get(system, _SYSTEM_TIER_DEFAULT)
            if severity == "high":
                has_high_issue = True
                has_meaningful_issue = True
                break
            if severity == "medium" and sys_mult >= 1.0:
                has_meaningful_issue = True

    if has_high_recall or has_high_issue or mcp_level == "high":
        return "low"
    if has_meaningful_recall or has_meaningful_issue or mcp_level == "medium":
        return "medium"
    return "high"


def _bound_score(value: Any) -> int:
    return max(0, min(100, int(round(float(value)))))


def _normalize_optional_enum(value: Any, allowed: Set[str]) -> Optional[str]:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized if normalized in allowed else None


def _deal_risk_label(score: int) -> str:
    if score >= _DEAL_RISK_HIGH_THRESHOLD:
        return "גבוה"
    if score >= _DEAL_RISK_MEDIUM_THRESHOLD:
        return "בינוני"
    return "נמוך"


def _compute_model_json_calibration(
    model_output: Optional[Dict[str, Any]],
    *,
    has_major_systemic_issue: bool,
) -> Dict[str, Any]:
    """Optional model-JSON calibration only.

    The model output is the primary scoring engine. There is no external baseline
    dictionary anymore, and missing calibration keys must never penalize or break
    scoring. Only present, valid calibration fields are used.
    """
    payload = model_output if isinstance(model_output, dict) else {}
    reliability_bias = _normalize_optional_enum(
        payload.get("reliability_bias"),
        _MODEL_JSON_BIAS_ALLOWED,
    )
    recall_sensitivity = _normalize_optional_enum(
        payload.get("recall_penalty_sensitivity"),
        _MODEL_JSON_SENSITIVITY_ALLOWED,
    )
    maintenance_sensitivity = _normalize_optional_enum(
        payload.get("maintenance_penalty_sensitivity"),
        _MODEL_JSON_SENSITIVITY_ALLOWED,
    )
    systemic_sensitivity = _normalize_optional_enum(
        payload.get("systemic_penalty_sensitivity"),
        _MODEL_JSON_SENSITIVITY_ALLOWED,
    )
    calibration_confidence = _normalize_optional_enum(
        payload.get("calibration_confidence"),
        _MODEL_JSON_CONFIDENCE_ALLOWED,
    )

    raw_soft_floor = payload.get("soft_floor_if_no_major_systemic")
    soft_floor = None
    if raw_soft_floor is not None:
        try:
            soft_floor = _bound_score(raw_soft_floor)
        except Exception:
            soft_floor = None

    used_fields: List[str] = []
    bias_delta = 0
    if reliability_bias is not None:
        bias_delta = _MODEL_JSON_RELIABILITY_BIAS[reliability_bias]
        used_fields.append("reliability_bias")
    if recall_sensitivity is not None:
        used_fields.append("recall_penalty_sensitivity")
    if maintenance_sensitivity is not None:
        used_fields.append("maintenance_penalty_sensitivity")
    if systemic_sensitivity is not None:
        used_fields.append("systemic_penalty_sensitivity")

    soft_floor_applied = soft_floor is not None and not has_major_systemic_issue
    if soft_floor_applied:
        used_fields.append("soft_floor_if_no_major_systemic")

    return {
        "applied": bool(used_fields),
        "source": "model_json" if used_fields else "none",
        "delta": bias_delta,
        "recall_scale": _MODEL_JSON_SENSITIVITY_SCALE.get(recall_sensitivity, 1.0),
        "maintenance_scale": _MODEL_JSON_SENSITIVITY_SCALE.get(maintenance_sensitivity, 1.0),
        "systemic_scale": _MODEL_JSON_SENSITIVITY_SCALE.get(systemic_sensitivity, 1.0),
        "soft_floor": soft_floor if soft_floor_applied else None,
        "reliability_bias": reliability_bias,
        "recall_penalty_sensitivity": recall_sensitivity,
        "maintenance_penalty_sensitivity": maintenance_sensitivity,
        "systemic_penalty_sensitivity": systemic_sensitivity,
        "soft_floor_if_no_major_systemic": soft_floor,
        "calibration_confidence": calibration_confidence,
    }


def _contains_vehicle_specific_neglect_claim(text: Any) -> bool:
    if not isinstance(text, str):
        return False
    t = text.strip().lower()
    if not t:
        return False
    if any(marker in t for marker in _NEGLECT_MARKERS_LITERAL):
        return True
    return any(re.search(rf"\b{re.escape(marker)}\b", t) for marker in _NEGLECT_MARKERS_WORD)


def _signal_text(sig: Dict[str, Any]) -> str:
    fields = [
        sig.get("issue"),
        sig.get("evidence_text"),
        sig.get("typical_timing"),
    ]
    return " ".join(str(v).lower() for v in fields if isinstance(v, str))


def _tokenize_overlap_text(text: str) -> Set[str]:
    tokens = set()
    for token in re.findall(r"[a-z0-9א-ת]+", text.lower()):
        if len(token) < 4 or token in _RECALL_LIKE_STOPWORDS or token.isdigit():
            continue
        tokens.add(token)
    return tokens


def _is_recall_like_signal(sig: Dict[str, Any], recalls: Optional[Dict[str, Any]] = None) -> bool:
    joined = _signal_text(sig)
    if not joined:
        return False
    if any(marker in joined for marker in _RECALL_LIKE_MARKERS):
        return True
    if any(marker in joined for marker in _RECALL_REMEDY_MARKERS):
        return True

    recall_count = 0
    recall_notes = ""
    if isinstance(recalls, dict):
        recall_count = _safe_int(recalls.get("count"), lo=0, hi=100)
        recall_notes = str(recalls.get("notes") or "").lower()

    if recall_notes:
        overlap = _tokenize_overlap_text(joined) & _tokenize_overlap_text(recall_notes)
        if len(overlap) >= _RECALL_NOTES_TOKEN_MIN:
            return True

    if recall_count > 0:
        return any(re.search(pattern, joined) for pattern in _RECALL_PATTERN_HINTS)
    return False


def compute_reliability_score_and_banner(
    validated_input: Dict[str, Any],
    risk_signals: Any,
    overall_reliability_estimate: Any = None,
    model_output: Any = None,
    mileage_range: Optional[str] = None,
) -> Dict[str, Any]:
    """Compute dual reliability outputs from model JSON + deterministic penalties.

    The model output is the primary engine. No external baseline dictionary remains
    in active use. Optional calibration comes only from model JSON fields; missing
    calibration fields never penalize the score and never break analysis.
    """
    estimate_label = _normalized_reliability_estimate(overall_reliability_estimate)
    if not isinstance(risk_signals, dict) or not risk_signals:
        if estimate_label:
            score = _bound_score(_MODEL_PRIMARY_BASE_SCORE + _overall_reliability_adjustment(estimate_label))
            deal_risk_score = 0
            deal_risk_label = _deal_risk_label(deal_risk_score)
            return {
                "score_0_100": score,
                "banner_he": _banner_from_score(score),
                "confidence_label": "low",
                "model_reliability_score": score,
                "model_reliability_label": _banner_from_score(score),
                "deal_risk_score": deal_risk_score,
                "deal_risk_label": deal_risk_label,
                "calibration_applied": False,
                "calibration_source": "none",
                "calibration_delta": 0,
                "reliability_bias": None,
                "recall_penalty_sensitivity": None,
                "maintenance_penalty_sensitivity": None,
                "systemic_penalty_sensitivity": None,
                "soft_floor_if_no_major_systemic": None,
                "calibration_confidence": None,
                "mileage_note": None,
            }
        return {
            "score_0_100": 0,
            "banner_he": "לא ידוע",
            "confidence_label": "low",
            "model_reliability_score": 0,
            "model_reliability_label": "לא ידוע",
            "deal_risk_score": 0,
            "deal_risk_label": "לא ידוע",
            "calibration_applied": False,
            "calibration_source": "none",
            "calibration_delta": 0,
            "reliability_bias": None,
            "recall_penalty_sensitivity": None,
            "maintenance_penalty_sensitivity": None,
            "systemic_penalty_sensitivity": None,
            "soft_floor_if_no_major_systemic": None,
            "calibration_confidence": None,
            "mileage_note": None,
        }

    base = _MODEL_PRIMARY_BASE_SCORE + _overall_reliability_adjustment(estimate_label)
    base = max(15, min(95, base))

    recalls = risk_signals.get("recalls") if isinstance(risk_signals.get("recalls"), dict) else {}

    # ── Step 2: systemic issue penalties ──
    systemic_penalty = 0.0
    signals = risk_signals.get("systemic_issue_signals")
    has_meaningful_issues = False
    has_major_systemic_issue = False
    if isinstance(signals, list):
        for sig in signals[:_MAX_SIGNALS]:
            if not isinstance(sig, dict):
                continue
            if (
                _contains_vehicle_specific_neglect_claim(sig.get("issue"))
                or _contains_vehicle_specific_neglect_claim(sig.get("evidence_text"))
                or _contains_vehicle_specific_neglect_claim(sig.get("typical_timing"))
            ):
                continue

            severity = str(sig.get("severity", "")).lower()
            if severity not in ("low", "medium", "high"):
                continue

            penalty = _SEVERITY_PENALTY.get(severity, 0)
            systemic_penalty += penalty

            if severity in ("medium", "high"):
                has_meaningful_issues = True
            if severity == "high":
                has_major_systemic_issue = True
    systemic_penalty = min(systemic_penalty, _SYSTEMIC_PENALTY_CAP)

    # ── Step 3: recall penalty (severity-based, not count-based) ──
    raw_recall_items = recalls.get("items")
    recall_items = raw_recall_items if isinstance(raw_recall_items, list) else []
    recall_penalty = 0.0
    has_meaningful_recalls = False
    for item in recall_items[:20]:
        if not isinstance(item, dict):
            continue
        sev = str(item.get("severity", "")).lower()
        pen = _RECALL_SEVERITY_PENALTY.get(sev, 0)
        recall_penalty += pen
        if sev in ("medium", "high"):
            has_meaningful_recalls = True
    recall_penalty = min(recall_penalty, _RECALL_TOTAL_CAP)

    # Fallback: if LLM returned old format (count/high_severity_count) without items
    if not recall_items:
        recall_count = _safe_int(recalls.get("count"), lo=0, hi=100)
        high_sev_count = _safe_int(recalls.get("high_severity_count"), lo=0, hi=100)
        if recall_count > 0:
            recall_penalty = min(
                high_sev_count * _RECALL_SEVERITY_PENALTY["high"]
                + max(0, recall_count - high_sev_count) * _RECALL_SEVERITY_PENALTY["medium"],
                _RECALL_TOTAL_CAP,
            )
            has_meaningful_recalls = high_sev_count > 0 or recall_count >= 3

    # ── Step 4: maintenance cost pressure (disabled — cost ≠ reliability) ──
    mcp_penalty = 0
    mcp_level = ""
    mcp = risk_signals.get("maintenance_cost_pressure")
    if isinstance(mcp, dict):
        mcp_level = str(mcp.get("level", "unknown")).lower()

    calibration = _compute_model_json_calibration(
        model_output if isinstance(model_output, dict) else None,
        has_major_systemic_issue=has_major_systemic_issue,
    )
    # calibration scales disabled — scoring is deterministic only

    # ── Step 5: total penalty with cap ──
    total_penalty = systemic_penalty + recall_penalty + mcp_penalty
    penalty_cap = base * _PENALTY_CAP_FRACTION
    total_penalty = min(total_penalty, penalty_cap)

    # ── Step 6: clean bonus ──
    bonus = 0
    if (
        not has_meaningful_issues
        and not has_meaningful_recalls
    ):
        bonus = _CLEAN_BONUS

    model_reliability_score = _bound_score(base - total_penalty + bonus + calibration["delta"])
    if calibration["soft_floor"] is not None:
        model_reliability_score = max(model_reliability_score, calibration["soft_floor"])
    # Code-side floor: LLM's overall assessment protects against
    # accumulated minor/medium penalties dragging a reliable car down.
    # Disabled when any high-severity systemic issue exists.
    _ESTIMATE_FLOOR = {"high": 75, "medium": 55}
    if estimate_label in _ESTIMATE_FLOOR and not has_major_systemic_issue:
        model_reliability_score = max(model_reliability_score, _ESTIMATE_FLOOR[estimate_label])
    model_reliability_score = _bound_score(model_reliability_score)
    model_reliability_label = _banner_from_score(model_reliability_score)

    mileage_delta, mileage_note = mileage_adjustment(mileage_range or "")
    mileage_risk = abs(min(mileage_delta, 0))
    deal_risk_score = _bound_score((systemic_penalty * 2.0) + (recall_penalty * 2.5) + (mcp_penalty * 3.0) + mileage_risk)
    deal_risk_label = _deal_risk_label(deal_risk_score)

    # Confidence is messaging only and no longer uses hidden model-entry boosts.
    confidence = _compute_confidence_category(risk_signals)

    return {
        "score_0_100": model_reliability_score,
        "banner_he": model_reliability_label,
        "confidence_label": confidence,
        "model_reliability_score": model_reliability_score,
        "model_reliability_label": model_reliability_label,
        "deal_risk_score": deal_risk_score,
        "deal_risk_label": deal_risk_label,
        "calibration_applied": calibration["applied"],
        "calibration_source": calibration["source"],
        "calibration_delta": calibration["delta"],
        "reliability_bias": calibration["reliability_bias"],
        "recall_penalty_sensitivity": calibration["recall_penalty_sensitivity"],
        "maintenance_penalty_sensitivity": calibration["maintenance_penalty_sensitivity"],
        "systemic_penalty_sensitivity": calibration["systemic_penalty_sensitivity"],
        "soft_floor_if_no_major_systemic": calibration["soft_floor_if_no_major_systemic"],
        "calibration_confidence": calibration["calibration_confidence"],
        "mileage_note": mileage_note,
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
        # --- Deterministic scoring (model output is primary; no external baseline dict) ---
        det = compute_reliability_score_and_banner(
            validated,
            ai_output.get("risk_signals"),
            ai_output.get("overall_reliability_estimate"),
            model_output=ai_output,
            mileage_range=final_mileage,
        )
        ai_output["model_reliability_score"] = det["model_reliability_score"]
        ai_output["model_reliability_label"] = det["model_reliability_label"]
        ai_output["deal_risk_score"] = det["deal_risk_score"]
        ai_output["deal_risk_label"] = det["deal_risk_label"]
        ai_output["calibration_applied"] = det["calibration_applied"]
        ai_output["calibration_source"] = det["calibration_source"]
        for key in (
            "reliability_bias",
            "recall_penalty_sensitivity",
            "maintenance_penalty_sensitivity",
            "systemic_penalty_sensitivity",
            "soft_floor_if_no_major_systemic",
            "calibration_confidence",
        ):
            ai_output[key] = det[key]

        # Temporary UI compatibility until the frontend fully migrates to dual scores.
        ai_output["base_score_calculated"] = det["model_reliability_score"]
        ai_output["estimated_reliability"] = det["model_reliability_label"]

        # Sync reliability_report
        if isinstance(ai_output.get("reliability_report"), dict):
            ai_output["reliability_report"]["overall_score"] = det["model_reliability_score"]
            ai_output["reliability_report"]["confidence"] = det["confidence_label"]

        sanitized_output: Dict[str, Any] = {}
        try:
            ai_output['source_tag'] = f"מקור: ניתוח AI חדש (חיפוש {display_quota_count}/{limit_val})"
            ai_output['mileage_note'] = det.get("mileage_note")
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

    response_payload = dict(sanitized_output)
    response_payload["history_id"] = history_id

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
