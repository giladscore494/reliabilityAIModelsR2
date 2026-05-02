# -*- coding: utf-8 -*-
"""
Comparison service logic for Car Comparison feature.
Uses Gemini 3 Flash with web grounding to retrieve car metrics.
All scoring is computed deterministically in code only.
"""

import os
import json
import hashlib
import logging
import re
import time as pytime
from typing import Any, Dict, List, Optional, Tuple

from flask import current_app, has_app_context

from app.extensions import db
from app.models import ComparisonHistory
from app.utils.http_helpers import api_ok, api_error, get_request_id, _utcnow
from app.utils.prompt_defense import (
    escape_prompt_input,
    wrap_user_input_in_boundary,
    create_data_only_instruction,
)
import app.extensions as extensions
from google.genai import types as genai_types
from app.utils.sanitization import sanitize_comparison_narrative, _sanitize_url
from app.utils.ai_guardrails import apply_feature_guardrails

logger = logging.getLogger(__name__)


# ============================================================
# JSON PARSING HELPERS
# ============================================================


def _safe_json_obj(value, default):
    """
    Safely decode a JSON value that may be None, already decoded, or double-encoded.

    Args:
        value: The value to decode (may be None, str, dict, or list)
        default: The default value to return on any error

    Returns:
        The decoded value as dict/list, or default on any failure.
        This function NEVER raises an exception.
    """
    try:
        if value is None:
            return default

        # Already decoded dict or list
        if isinstance(value, (dict, list)):
            return value

        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return default

            # First decode attempt
            result = json.loads(stripped)

            # Check if result is still a string (double-encoded)
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except (json.JSONDecodeError, TypeError, ValueError):
                    # Second decode failed, return default
                    return default

            # Verify final result is dict or list
            if isinstance(result, (dict, list)):
                return result
            return default

        # Unexpected type
        return default
    except (json.JSONDecodeError, TypeError, ValueError):
        return default


def build_display_name(car: Dict[str, Any]) -> str:
    """Build a human-readable display name for a car.
    Format: "{make} {model} {year}" or "{make} {model}" if no year.
    """
    parts = [car.get("make", ""), car.get("model", "")]
    year = car.get("year")
    if year:
        parts.append(str(year))
    elif car.get("year_start") and car.get("year_end"):
        parts.append(f"{car['year_start']}-{car['year_end']}")
    return " ".join(p for p in parts if p).strip()


CHECKED_VERSION_UNKNOWN_HE = "לא ידוע / לבדיקה"
CHECKED_VERSION_NOT_VERIFIED_HE = "לא מאומת"
CHECKED_VERSION_DATA_BASIS_ALLOWED = {
    "user_input",
    "verified_source",
    "ai_inference",
    "mixed",
}
CHECKED_VERSION_CONFIDENCE_ALLOWED = {"high", "medium", "low", "unverified"}


def _normalize_general_transmission_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return CHECKED_VERSION_UNKNOWN_HE

    lowered = text.lower()
    if any(
        token in lowered
        for token in ("dsg", "dct", "dual clutch", "dual-clutch", "robot", "רובוט")
    ):
        return "רובוטית"
    if any(token in lowered for token in ("cvt", "רציפ", "continuously variable")):
        return "רציפה"
    if any(token in lowered for token in ("manual", "ידני", "ידנית")):
        return "ידנית"
    if any(
        token in lowered
        for token in (
            "unknown",
            "not verified",
            "needs verification",
            "לא ידוע",
            "לבדיקה",
            "לא מאומת",
        )
    ):
        return CHECKED_VERSION_UNKNOWN_HE
    if any(
        token in lowered
        for token in ("automatic", "auto", "אוטומט", "planetary", "פלנטר")
    ):
        return "אוטומטית"
    return text[:80]


def _normalize_checked_version_text(value: Any, default: str = "") -> str:
    text = " ".join(str(value or "").split()).strip()
    return text[:180] if text else default


def _sanitize_checked_versions(
    payload: Any, slot_keys: List[str]
) -> Dict[str, Dict[str, str]]:
    if not isinstance(payload, dict):
        return {}

    sanitized: Dict[str, Dict[str, str]] = {}
    for slot_key in slot_keys:
        raw = payload.get(slot_key)
        if not isinstance(raw, dict):
            continue
        data_basis = raw.get("data_basis")
        confidence = raw.get("confidence")
        sanitized[slot_key] = {
            "make": _normalize_checked_version_text(raw.get("make")),
            "model": _normalize_checked_version_text(raw.get("model")),
            "year": _normalize_checked_version_text(raw.get("year")),
            "trim": _normalize_checked_version_text(raw.get("trim")),
            "engine_type": _normalize_checked_version_text(raw.get("engine_type")),
            "transmission": _normalize_general_transmission_label(
                raw.get("transmission")
            ),
            "drivetrain": _normalize_checked_version_text(raw.get("drivetrain")),
            "seats": _normalize_checked_version_text(raw.get("seats")),
            "data_basis": data_basis
            if data_basis in CHECKED_VERSION_DATA_BASIS_ALLOWED
            else "ai_inference",
            "confidence": confidence
            if confidence in CHECKED_VERSION_CONFIDENCE_ALLOWED
            else "low",
            "notes": _normalize_checked_version_text(raw.get("notes")),
        }
    return sanitized


def _normalize_grounded_cars_format(grounded_output: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    grounded_cars_raw = (
        ((grounded_output or {}).get("cars") or {})
        if isinstance(grounded_output, dict)
        else {}
    )
    if isinstance(grounded_cars_raw, list):
        return {
            f"car_{index + 1}": item
            for index, item in enumerate(grounded_cars_raw)
            if isinstance(item, dict)
        }
    return grounded_cars_raw if isinstance(grounded_cars_raw, dict) else {}


def build_checked_versions(
    cars_selected_slots: Dict[str, Dict[str, Any]],
    grounded_output: Optional[Dict[str, Any]],
    ai_checked_versions: Optional[Dict[str, Dict[str, str]]] = None,
) -> Dict[str, Dict[str, str]]:
    grounded_cars = _normalize_grounded_cars_format(grounded_output)
    slot_keys = _ordered_compare_slot_keys(cars_selected_slots or {}, grounded_cars, ai_checked_versions or {})
    ai_checked_versions = _sanitize_checked_versions(ai_checked_versions, slot_keys)
    result: Dict[str, Dict[str, str]] = {}

    for slot_key in slot_keys:
        selection = (cars_selected_slots or {}).get(slot_key, {}) or {}
        grounded_car = (
            grounded_cars.get(slot_key, {})
            if isinstance(grounded_cars.get(slot_key, {}), dict)
            else {}
        )
        profile = (
            grounded_car.get("car_profile")
            if isinstance(grounded_car.get("car_profile"), dict)
            else {}
        )
        identity = (
            profile.get("vehicle_identity")
            if isinstance(profile.get("vehicle_identity"), dict)
            else {}
        )
        powertrain = (
            profile.get("powertrain_specs")
            if isinstance(profile.get("powertrain_specs"), dict)
            else {}
        )
        recommended_trim = (
            profile.get("recommended_trim")
            if isinstance(profile.get("recommended_trim"), dict)
            else {}
        )
        trims = (
            profile.get("trim_levels_israel")
            if isinstance(profile.get("trim_levels_israel"), list)
            else []
        )

        make = _normalize_checked_version_text(
            identity.get("make")
        ) or _normalize_checked_version_text(selection.get("make"))
        model = _normalize_checked_version_text(
            identity.get("model")
        ) or _normalize_checked_version_text(selection.get("model"))
        year = (
            _normalize_checked_version_text(identity.get("year"))
            or _normalize_checked_version_text(selection.get("year"))
            or _normalize_checked_version_text(selection.get("year_start"))
        )

        trim_confidence = str(recommended_trim.get("confidence") or "").strip().lower()
        trim = _normalize_checked_version_text(recommended_trim.get("trim_name"))
        if not trim and len(trims) == 1 and isinstance(trims[0], dict):
            trim = _normalize_checked_version_text(trims[0].get("trim_name"))
        if not trim or trim_confidence == "low":
            trim = CHECKED_VERSION_NOT_VERIFIED_HE

        engine_type = (
            _normalize_checked_version_text(powertrain.get("engine"))
            or _normalize_checked_version_text(selection.get("engine_type"))
            or _normalize_checked_version_text(
                (grounded_car.get("facts") or {}).get("fuel_type")
            )
            or CHECKED_VERSION_UNKNOWN_HE
        )
        transmission = _normalize_general_transmission_label(
            powertrain.get("gearbox") or selection.get("gearbox")
        )
        drivetrain = _normalize_checked_version_text(
            powertrain.get("drivetrain"),
            CHECKED_VERSION_NOT_VERIFIED_HE,
        )
        seats_value = powertrain.get("seats")
        seats = (
            _normalize_checked_version_text(seats_value)
            if seats_value not in (None, "")
            else CHECKED_VERSION_NOT_VERIFIED_HE
        )

        has_profile = bool(profile)
        has_sources = bool(
            powertrain.get("sources")
            or profile.get("sources")
            or grounded_car.get("sources")
            or (
                trims[0].get("source") if trims and isinstance(trims[0], dict) else None
            )
        )
        has_user_specific = bool(
            selection.get("year")
            or selection.get("engine_type")
            or selection.get("gearbox")
        )

        if has_sources and has_user_specific:
            data_basis = "mixed"
        elif has_sources:
            data_basis = "verified_source"
        elif has_profile and has_user_specific:
            data_basis = "mixed"
        elif has_profile:
            data_basis = "ai_inference"
        else:
            data_basis = "user_input"

        if has_sources and year and trim != CHECKED_VERSION_NOT_VERIFIED_HE:
            confidence = "high"
        elif has_sources and year:
            confidence = "medium"
        elif has_profile or has_user_specific:
            confidence = "low"
        else:
            confidence = "unverified"

        note_parts: List[str] = []
        if has_user_specific and (
            selection.get("engine_type") or selection.get("gearbox")
        ):
            note_parts.append(
                "סוג המנוע או התיבה נבחרו כערכים כלליים בטופס "
                "ויש לאמת מול מפרט היבואן או רישיון הרכב."
            )
        if trim == CHECKED_VERSION_NOT_VERIFIED_HE:
            note_parts.append("רמת הגימור לא אומתה במידע הזמין.")
        if not year:
            note_parts.append("השנתון המדויק לא אומת.")
        if data_basis in {"mixed", "ai_inference"}:
            note_parts.append(
                "ההשוואה מתייחסת לגרסה מייצגת לפי המידע הזמין "
                "וייתכנו הבדלים בין רמות גימור, מנועים ותיבות הילוכים."
            )
        if not note_parts:
            note_parts.append("יש לאמת את המפרט מול מקור רשמי לפני החלטת רכישה.")

        fallback = {
            "make": make,
            "model": model,
            "year": year or CHECKED_VERSION_NOT_VERIFIED_HE,
            "trim": trim,
            "engine_type": engine_type,
            "transmission": transmission,
            "drivetrain": drivetrain,
            "seats": seats,
            "data_basis": data_basis,
            "confidence": confidence,
            "notes": " ".join(note_parts[:2]),
        }

        merged = dict(fallback)
        ai_version = ai_checked_versions.get(slot_key) or {}
        for key, value in ai_version.items():
            if value:
                merged[key] = value
        if not merged.get("transmission"):
            merged["transmission"] = CHECKED_VERSION_UNKNOWN_HE
        merged["transmission"] = _normalize_general_transmission_label(
            merged.get("transmission")
        )
        result[slot_key] = merged

    return result


def map_cars_to_slots(validated_cars: List[Dict]) -> Dict[str, Dict]:
    """Map validated cars to stable slot keys: car_1, car_2, car_3.
    Each slot includes the original selection fields plus display_name.
    """
    slots = {}
    for i, car in enumerate(validated_cars):
        slot_key = f"car_{i + 1}"
        slot_data = dict(car)  # copy
        slot_data["display_name"] = build_display_name(car)
        slots[slot_key] = slot_data
    return slots


def _ordered_compare_slot_keys(*sources: Any) -> List[str]:
    seen = set()
    ordered: List[str] = []
    for source in sources:
        if isinstance(source, dict):
            keys = source.keys()
        else:
            keys = source or []
        for key in keys:
            if isinstance(key, str) and _COMPARE_SLOT_RE.match(key) and key not in seen:
                seen.add(key)
                ordered.append(key)
    return sorted(
        ordered, key=lambda value: int(_COMPARE_SLOT_RE.match(value).group(1))
    )


def _normalize_compare_writer_winner(
    value: Any, allowed_slot_keys: List[str]
) -> Optional[str]:
    if value == "tie":
        return "tie"
    if not isinstance(value, str):
        return None
    if value in allowed_slot_keys:
        return value
    legacy_map = {
        "carA": "car_1",
        "carB": "car_2",
        "carC": "car_3",
    }
    normalized = legacy_map.get(value)
    if normalized in allowed_slot_keys:
        return normalized
    return None


def _extract_decision_slot_keys(decision_result: Any) -> List[str]:
    if not isinstance(decision_result, dict):
        return []
    extracted: List[str] = []
    for key in decision_result.keys():
        match = _DECISION_SLOT_FIELD_RE.match(str(key))
        if match:
            extracted.append(match.group(1))
    key_differences = decision_result.get("key_differences")
    for item in key_differences if isinstance(key_differences, list) else []:
        if not isinstance(item, dict):
            continue
        for key in item.keys():
            if isinstance(key, str) and _COMPARE_SLOT_RE.match(key):
                extracted.append(key)
    return _ordered_compare_slot_keys(extracted)


def _segment_text_tokens(
    car_slot: Optional[Dict[str, Any]], grounded_car_data: Optional[Dict[str, Any]]
) -> str:
    car_slot = car_slot or {}
    grounded_car_data = grounded_car_data or {}
    facts = (
        (grounded_car_data.get("facts") or {})
        if isinstance(grounded_car_data, dict)
        else {}
    )
    text_parts = [
        car_slot.get("make"),
        car_slot.get("model"),
        car_slot.get("trim"),
        car_slot.get("display_name"),
        car_slot.get("engine_type"),
        car_slot.get("gearbox"),
        grounded_car_data.get("car_name")
        if isinstance(grounded_car_data, dict)
        else None,
        facts.get("body_type"),
        facts.get("fuel_type"),
        " ".join((grounded_car_data.get("short_notes") or [])[:4])
        if isinstance(grounded_car_data, dict)
        else None,
    ]
    return " ".join(str(part).lower() for part in text_parts if part)


def _infer_compare_segment_details(
    car_slot: Optional[Dict[str, Any]],
    grounded_car_data: Optional[Dict[str, Any]],
) -> Tuple[str, List[str]]:
    text = _segment_text_tokens(car_slot, grounded_car_data)
    facts = (
        ((grounded_car_data or {}).get("facts") or {})
        if isinstance(grounded_car_data, dict)
        else {}
    )
    body_type = str(facts.get("body_type") or "").lower()

    def _matches(*keywords: str) -> List[str]:
        return [keyword for keyword in keywords if keyword in text]

    def _body_matches(*keywords: str) -> List[str]:
        return [keyword for keyword in keywords if keyword in body_type]

    pickup_hits = _matches(
        "pickup",
        "pick-up",
        "truck",
        "ute",
        "hilux",
        "ranger",
        "navara",
        "amarok",
        "d-max",
        "l200",
        "triton",
        "ram ",
        "f-150",
        "silverado",
    ) + _body_matches("pickup", "truck")
    if pickup_hits:
        return "pickup_truck", pickup_hits[:3]

    mpv_hits = _matches(
        "minivan",
        "mpv",
        "people carrier",
        "grand c4 spacetourer",
        "touran",
        "carens",
        "s-max",
        "galaxy",
        "berlingo",
        "doblo",
        "caddy",
    ) + _body_matches("minivan", "mpv", "van")
    if mpv_hits:
        return "minivan_mpv", mpv_hits[:3]

    offroad_hits = _matches(
        "land cruiser",
        "prado",
        "wrangler",
        "defender",
        "jimny",
        "pajero",
        "patrol",
        "grenadier",
        "g-class",
        "g wagon",
        "g-wagon",
        "4runner",
    ) + _body_matches("4x4", "off-road", "off road")
    if offroad_hits:
        return "hardcore_4x4", offroad_hits[:3]

    family_3row_hits = _matches(
        "7 seat",
        "7-seat",
        "7 seater",
        "seven seat",
        "third row",
        "3 row",
        "3-row",
        "highlander",
        "pilot",
        "sorento",
        "palisade",
        "telluride",
        "pathfinder",
        "xc90",
        "explorer",
        "everest",
        "kodiaq",
    )
    if family_3row_hits:
        return "three_row_family_suv", family_3row_hits[:3]

    sporty_hits = _matches(
        "gti",
        "type r",
        "type-r",
        "sti",
        "gr86",
        "86",
        "brz",
        "mx-5",
        "miata",
        "cupra",
        "amg",
        "m sport",
        " m ",
        "rs ",
        "n line",
        "n ",
        "vrs",
        "track",
        "sportback performance",
        "hot hatch",
        "roadster",
        "coupe",
    )
    if sporty_hits:
        return "sporty_dynamic", sporty_hits[:3]

    executive_hits = _matches(
        "executive",
        "luxury",
        "premium",
        "5 series",
        "7 series",
        "a6",
        "a8",
        "e-class",
        "s-class",
        "es ",
        "gs ",
        "ls ",
        "g80",
        "g90",
        "s90",
        "xf",
        "xj",
    )
    if executive_hits:
        return "executive_luxury", executive_hits[:3]

    city_hits = _matches(
        "city",
        "mini",
        "aygo",
        "i10",
        "picanto",
        "up!",
        "up ",
        "c1",
        "108",
        "spark",
        "alto",
        "mii",
        "ka ",
        "twingo",
    )
    if city_hits:
        return "city_mini", city_hits[:3]

    supermini_hits = _matches(
        "supermini",
        "polo",
        "ibiza",
        "fiesta",
        "yaris",
        "clio",
        "corsa",
        "jazz",
        "fit",
        "i20",
        "rio",
        "208",
        "mazda2",
        "swift",
        "fabia",
    )
    if supermini_hits:
        return "supermini_hatch", supermini_hits[:3]

    crossover_hits = _matches(
        "crossover",
        "cross",
        "cuv",
        "suv",
        "sportage",
        "qashqai",
        "cx-5",
        "cx5",
        "tucson",
        "rav4",
        "cr-v",
        "crv",
        "x-trail",
        "xtrail",
        "kadjar",
        "3008",
    ) + _body_matches("suv", "crossover", "cuv")
    if crossover_hits:
        return "crossover_soft_suv", crossover_hits[:3]

    family_body_hits = _matches(
        "sedan",
        "saloon",
        "hatch",
        "hatchback",
        "wagon",
        "estate",
        "tourer",
        "fastback",
        "liftback",
    ) + _body_matches(
        "sedan", "saloon", "hatch", "wagon", "estate", "fastback", "liftback"
    )
    if family_body_hits:
        return "family_sedan_hatch_wagon", family_body_hits[:3]

    return "general_private_car", ["default_private_car"]


def infer_compare_segment(
    car_slot: Optional[Dict[str, Any]], grounded_car_data: Optional[Dict[str, Any]]
) -> str:
    """Infer a lightweight compare segment without relying on a missing taxonomy field."""
    segment_key, _signals = _infer_compare_segment_details(car_slot, grounded_car_data)
    return segment_key


# ============================================================
# CONFIGURATION
# ============================================================

COMPARISON_PROMPT_VERSION = "v4"
COMPARISON_MODEL_ID = "gemini-3-flash-preview"
AI_CALL_TIMEOUT_SEC = int(os.environ.get("AI_CALL_TIMEOUT_SEC", "170"))
COMPARE_STAGE_A_TIMEOUT_SEC = int(os.environ.get("COMPARE_STAGE_A_TIMEOUT_SEC", "30"))
COMPARE_STAGE_A_MAX_OUTPUT_TOKENS = int(
    os.environ.get("COMPARE_STAGE_A_MAX_OUTPUT_TOKENS", "4096")
)
COMPARE_STAGE_A_TEMPERATURE = float(
    os.environ.get("COMPARE_STAGE_A_TEMPERATURE", "0.25")
)
COMPARE_WRITER_TIMEOUT_SEC = int(os.environ.get("COMPARE_WRITER_TIMEOUT_SEC", "30"))
# Stage B now emits full decision_result content for up to three cars, including
# per-car guidance arrays, so leave more room to avoid truncating those fields.
COMPARE_WRITER_MAX_OUTPUT_TOKENS = int(
    os.environ.get("COMPARE_WRITER_MAX_OUTPUT_TOKENS", "3200")
)
COMPARE_WRITER_RETRY_MAX_OUTPUT_TOKENS = int(
    os.environ.get("COMPARE_WRITER_RETRY_MAX_OUTPUT_TOKENS", "500")
)
COMPARE_WRITER_PROMPT_CHAR_CAP = int(
    os.environ.get("COMPARE_WRITER_PROMPT_CHAR_CAP", "16000")
)
TIE_THRESHOLD = 5  # Score delta below this = "tie" (צמוד)
PARALLEL_GRACE_SEC = (
    5  # Extra seconds when collecting parallel futures beyond per-call timeout
)
ELLIPSIS_LEN = 3
# Performance heuristics for typical passenger cars. HP_PER_TON_* thresholds are
# horsepower per metric ton, while HORSEPOWER_* thresholds are absolute engine
# output bands. They intentionally create coarse low/medium/high buckets rather
# than precise rankings so code-side scoring stays stable across runs.
KILOGRAMS_PER_TON = 1000.0
MIN_REASONABLE_VEHICLE_WEIGHT_KG = 500.0
HP_PER_TON_HIGH_THRESHOLD = 140
HP_PER_TON_MEDIUM_THRESHOLD = 95
HORSEPOWER_HIGH_THRESHOLD = 180
HORSEPOWER_MEDIUM_THRESHOLD = 120
PARTIAL_COMPARISON_SUMMARY_PREFIX = "השוואה חלקית:"
PARTIAL_COMPARISON_DISCLAIMER = "ההשוואה חלקית כי לא נמצא מידע מלא על כל הרכבים."
COMPARE_SCORE_EXPLANATION_TEMPLATE_HE = ""

COMPARE_AI_METRICS = {
    "compare_ai_calls_total": 0,
    "compare_ai_failures_total": {},
    "compare_ai_fallback_used_total": 0,
    "compare_ai_output_tokens_estimate": 0,
    "compare_stage_a_timeout_total": 0,
    "compare_stage_a_error_total": 0,
    "compare_stage_a_json_invalid_total": 0,
    "compare_stage_b_error_total": 0,
    "compare_ai_regenerate_used_total": 0,
    "compare_ai_regenerate_error_total": 0,
    "compare_ai_regenerate_fallback_total": 0,
}

# Category weights for overall score calculation
CATEGORY_WEIGHTS = {
    "reliability_risk": 40,
    "ownership_cost": 25,
    "practicality_comfort": 20,
    "driving_performance": 15,
}

ORDINAL_SCORES_NEGATIVE = {
    "low": 100,
    "medium": 60,
    "high": 20,
}

ORDINAL_SCORES_POSITIVE = {
    "low": 20,
    "medium": 60,
    "high": 100,
}

CATEGORY_LABELS_HE = {
    "reliability_risk": "אמינות וסיכונים",
    "ownership_cost": "עלות אחזקה",
    "practicality_comfort": "נוחות ופרקטיות",
    "driving_performance": "ביצועים ונהיגה",
}

_LABEL_VALUES = {"low", "medium", "high"}
# Keep Stage A tiny so the model returns deterministic JSON and the UI stays concise.
_MAX_STAGE_A_NOTES = 4
_MAX_STAGE_A_SOURCES = 5
COMPARE_CATEGORY_NAMES = tuple(CATEGORY_LABELS_HE.keys())
DECISION_CATEGORY_DEFINITIONS = [
    ("pricing_and_value", "מחיר ותמורה"),
    ("trim_and_equipment", "רמות גימור ואבזור"),
    ("license_fee_and_running_cost", "אגרה ועלויות שוטפות"),
    ("fuel_consumption", "צריכת דלק/חשמל"),
    ("official_safety", "בטיחות רשמית"),
    ("powertrain_and_performance", "מכלולים וביצועים"),
    ("reliability_and_risk", "אמינות וסיכונים"),
    ("family_daily_use", "שימוש יומי ומשפחתי"),
    ("resale_and_market_confidence", "סחירות וירידת ערך"),
]
DECISION_ALLOWED_LABELS = {"car_1", "car_2", "car_3", "tie", "depends", "unknown"}
DECISION_TEXT_FALLBACK_HE = (
    "המערכת לא הצליחה לנסח סיכום ניטרלי, לכן יש להסתמך על פירוט הקטגוריות."
)
DECISION_FORBIDDEN_TEXT_RE = re.compile(
    r"(\d+\s*/\s*100|\d+\s*/\s*10|winnerScore|overall_score|category_score|ציון|ניקוד|מתוך 100|נקודות מתוך|אני ממליץ|הייתי קונה|תקנה|אל תקנה|המנצח הברור|הרכב הטוב ביותר)",
    re.IGNORECASE,
)
BUYER_PROFILE_MAIN_USE_ALLOWED = {
    "city",
    "highway",
    "family",
    "commuting",
    "long_trips",
    "work",
    "mixed",
    "unknown",
}
BUYER_PROFILE_SAFETY_ALLOWED = {"yes", "no"}
BUYER_PROFILE_FUELS_ALLOWED = {
    "gasoline",
    "diesel",
    "hybrid",
    "plug_in_hybrid",
    "electric",
    "lpg",
    "בנזין",
    "דיזל",
    "היברידי",
    "חשמלי",
}
BUYER_PROFILE_GEARS_ALLOWED = {
    "automatic",
    "manual",
    "robotic",
    "אוטומטית",
    "ידנית",
    "רובוטית",
}
BUYER_PROFILE_PRIORITY_KEYS = {
    "reliability",
    "fuel",
    "safety",
    "comfort",
    "performance",
    "cost",
}
BUYER_PROFILE_PRIORITY_MIN = 0
BUYER_PROFILE_PRIORITY_MAX = 10
CAR_PROFILE_MAX_NESTING_DEPTH = 5
_COMPARE_SLOT_RE = re.compile(r"^car_(\d+)$")
_DECISION_SLOT_FIELD_RE = re.compile(r"^(?:choose|avoid_or_check)_(car_\d+)_if$")

# Segment inference is intentionally lightweight and deterministic because compare
# currently has no authoritative taxonomy field. We infer from visible name/body
# style hints so Stage A can judge each car against its likely mission.
COMPARE_SEGMENT_PROMPT_RULES = {
    "city_mini": {
        "focus_more": [
            "urban maneuverability",
            "parking ease",
            "fuel economy / efficiency",
            "low routine running costs",
            "reliability under city use",
            "visibility",
        ],
        "focus_less": [
            "high-speed performance",
            "towing",
            "off-road ability",
        ],
    },
    "supermini_hatch": {
        "focus_more": [
            "efficiency",
            "reliability",
            "city + intercity usability balance",
            "hatch practicality",
            "ease of ownership",
            "cabin/package efficiency",
            "value for money",
        ],
    },
    "family_sedan_hatch_wagon": {
        "focus_more": [
            "safety",
            "rear-seat usability",
            "trunk / cargo usability",
            "ride comfort",
            "highway refinement",
            "fuel economy",
            "ownership stability / reliability",
        ],
        "focus_less": [
            "sporty handling unless this is a sporty trim",
        ],
        "special_note": "If hatchback or wagon hints appear, reward cargo flexibility and easier loading access.",
    },
    "crossover_soft_suv": {
        "focus_more": [
            "family usability",
            "seating height / ease of entry",
            "cargo space",
            "comfort",
            "safety tech",
            "efficiency relative to size",
            "reliability / ownership simplicity",
        ],
        "focus_less": [
            "hardcore off-road capability unless clearly relevant",
        ],
    },
    "three_row_family_suv": {
        "focus_more": [
            "real 3rd-row usability",
            "passenger space in all rows",
            "cargo space with seats up/down",
            "family safety",
            "comfort on long trips",
            "ease of child-seat/family use",
            "efficiency and ownership burden",
            "practical value",
        ],
        "focus_less": [
            "sporty driving feel unless very relevant",
        ],
    },
    "hardcore_4x4": {
        "focus_more": [
            "real 4WD capability",
            "low range if present",
            "locking differentials",
            "ground clearance",
            "approach / breakover / departure angles",
            "durability",
            "tire/underbody readiness",
            "payload / trail utility",
            "reliability in rough use",
        ],
        "focus_less": [
            "ride softness penalties when the vehicle is clearly off-road focused",
        ],
        "special_note": "Do penalize high ownership burden and chronic durability risks.",
    },
    "pickup_truck": {
        "focus_more": [
            "towing",
            "payload",
            "bed utility",
            "drivetrain suitability",
            "work/family mission fit",
            "durability",
            "fuel/running cost",
            "comfort/noise if it is also a daily driver",
        ],
        "focus_less": [
            "family sedan priorities",
        ],
    },
    "minivan_mpv": {
        "focus_more": [
            "passenger space",
            "cargo flexibility",
            "family ergonomics",
            "sliding-door practicality if applicable",
            "comfort",
            "child-seat friendliness",
            "low-stress ownership",
            "value",
        ],
        "focus_less": [
            "sporty styling or handling",
        ],
    },
    "sporty_dynamic": {
        "focus_more": [
            "steering response",
            "steering feedback",
            "body control",
            "balance",
            "braking confidence",
            "traction",
            "throttle/power delivery",
            "driver engagement",
            "stability at speed",
        ],
        "focus_less": [
            "family-car practicality expectations",
        ],
        "special_note": "Still include reliability and running costs, but do not judge it by the same comfort/practicality standard as a family car.",
    },
    "executive_luxury": {
        "focus_more": [
            "refinement",
            "cabin isolation",
            "seat comfort",
            "material quality",
            "tech usability",
            "highway comfort",
            "prestige-appropriate ownership burden",
            "reliability risk of complex systems",
            "resale / ownership cost realism",
        ],
    },
    "general_private_car": {
        "focus_more": [
            "safety",
            "reliability",
            "ownership cost",
            "comfort",
            "practicality",
            "efficiency",
            "drivability",
        ],
    },
}

COMPARE_CATEGORY_BEHAVIOR_RULES = {
    "reliability_risk": (
        "Use segment-aware reliability expectations. Family cars should emphasize reliability consistency, "
        "safety-related faults, gearbox/engine risk, and long-term ownership stress. Hardcore 4x4s should "
        "include drivetrain durability and rugged-use tolerance. Sporty cars should include brake/thermal "
        "stress, drivetrain complexity, and whether performance hardware raises failure exposure."
    ),
    "ownership_cost": (
        "Use segment-aware cost expectations. City cars should emphasize fuel, tires, routine maintenance, "
        "and insurance burden. Family SUVs should include fuel, tires, maintenance, and depreciation pressure. "
        "Sporty/luxury cars should include consumables, tires, brakes, complex systems, and premium repairs. "
        "Pickups/4x4s should include fuel, tires, suspension wear, and drivetrain/service burden."
    ),
    "practicality_comfort": (
        "Evaluate according to mission. Family cars should emphasize rear seat, trunk, and child/family use. "
        "Hatches/wagons should reward flexibility and loading ease. 3-row SUVs should emphasize usable third row "
        "and cargo tradeoffs. Minivans should emphasize family ergonomics and space efficiency. Sporty cars only "
        "need enough daily usability; do not demand SUV practicality."
    ),
    "driving_performance": (
        "Use segment-aware meaning. Family cars should emphasize confidence, smoothness, stability, and easy "
        "drivability. Crossovers/SUVs should emphasize predictability, visibility, comfort, and adequate power. "
        "Sporty cars should emphasize handling precision, balance, response, braking, and engagement. Off-roaders "
        "should include off-road control plus acceptable on-road competence. Pickups should emphasize loaded "
        "stability, torque delivery, and towing confidence when relevant."
    ),
}

SINGLE_CAR_CATEGORY_TEMPLATE = {
    "reliability": {
        "overall": None,
        "issue_frequency": None,
        "issue_severity": None,
        "repair_cost_risk": None,
        "recall_risk": None,
        "parts_complexity": None,
    },
    "ownership_cost": {
        "fuel_cost": None,
        "routine_maintenance": None,
        "repair_burden": None,
        "insurance_burden": None,
        "depreciation_risk": None,
    },
    "comfort_practicality": {
        "space": None,
        "ride_comfort": None,
        "trunk_usefulness": None,
        "daily_usability": None,
    },
    "performance_driving": {
        "power_feel": None,
        "power_to_weight": None,
        "braking_confidence": None,
        "handling_agility": None,
        "fun_to_drive": None,
    },
}

SINGLE_CAR_FACTS_TEMPLATE = {
    "horsepower": None,
    "weight_kg": None,
    "body_type": None,
    "fuel_type": None,
}

CATEGORY_SCORE_CONFIG = {
    "reliability_risk": {
        "stage_a_key": "reliability",
        "weight": 40,
        "subfactors": {
            "reliability_reputation": {
                "weight": 12,
                "kind": "positive_label",
                "field": "overall",
            },
            "issue_frequency": {
                "weight": 8,
                "kind": "negative_label",
                "field": "issue_frequency",
            },
            "issue_severity": {
                "weight": 8,
                "kind": "negative_label",
                "field": "issue_severity",
            },
            "repair_cost_risk": {
                "weight": 5,
                "kind": "negative_label",
                "field": "repair_cost_risk",
            },
            "recall_risk": {
                "weight": 4,
                "kind": "negative_label",
                "field": "recall_risk",
            },
            "parts_complexity": {
                "weight": 3,
                "kind": "negative_label",
                "field": "parts_complexity",
            },
        },
    },
    "ownership_cost": {
        "stage_a_key": "ownership_cost",
        "weight": 25,
        "subfactors": {
            "fuel_cost": {"weight": 8, "kind": "negative_label", "field": "fuel_cost"},
            "routine_maintenance": {
                "weight": 6,
                "kind": "negative_label",
                "field": "routine_maintenance",
            },
            "repair_burden": {
                "weight": 5,
                "kind": "negative_label",
                "field": "repair_burden",
            },
            "insurance_burden": {
                "weight": 3,
                "kind": "negative_label",
                "field": "insurance_burden",
            },
            "depreciation_risk": {
                "weight": 3,
                "kind": "negative_label",
                "field": "depreciation_risk",
            },
        },
    },
    "practicality_comfort": {
        "stage_a_key": "comfort_practicality",
        "weight": 20,
        "subfactors": {
            "space": {"weight": 7, "kind": "positive_label", "field": "space"},
            "ride_comfort": {
                "weight": 5,
                "kind": "positive_label",
                "field": "ride_comfort",
            },
            "trunk_usefulness": {
                "weight": 4,
                "kind": "positive_label",
                "field": "trunk_usefulness",
            },
            "daily_usability": {
                "weight": 4,
                "kind": "positive_label",
                "field": "daily_usability",
            },
        },
    },
    "driving_performance": {
        "stage_a_key": "performance_driving",
        "weight": 15,
        "subfactors": {
            "power_capability": {"weight": 6, "kind": "power_capability"},
            "braking_confidence": {
                "weight": 3,
                "kind": "positive_label",
                "field": "braking_confidence",
            },
            "handling_agility": {
                "weight": 4,
                "kind": "positive_label",
                "field": "handling_agility",
            },
            "fun_to_drive": {
                "weight": 2,
                "kind": "positive_label",
                "field": "fun_to_drive",
            },
        },
    },
}


# ============================================================
# SAFE JSON CACHE PARSING
# ============================================================


def _safe_parse_json_cached(
    raw_value: Any, field_name: str = "unknown"
) -> Tuple[Any, bool]:
    """
    Safely parse possibly double-encoded JSON from cached database rows.

    Handles the case where old cached rows stored double-encoded JSON strings,
    e.g., '"{\\\"a\\\": 1}"' which when parsed once returns a string '{"a": 1}'
    that itself needs another json.loads() call.

    Args:
        raw_value: The raw value from the database (string, dict, list, or None).
                   Can be a JSON string, already-parsed dict/list (from JSONB), or None.
        field_name: Name of the field (for logging)

    Returns:
        Tuple of (parsed_value, was_double_encoded)
        - parsed_value: The parsed dict/list, or the original value if not parseable
        - was_double_encoded: True if double-encoding was detected and unwrapped

    Never throws; returns (None, False) for truly invalid data.
    """
    if raw_value is None:
        return None, False

    if not isinstance(raw_value, str):
        # Already parsed (e.g., JSONB column returned dict/list directly)
        return raw_value, False

    try:
        # First parse attempt
        parsed = json.loads(raw_value)

        # Check if result is still a string that looks like JSON
        if isinstance(parsed, str):
            stripped = parsed.strip()
            if stripped.startswith("{") or stripped.startswith("["):
                # Attempt second parse (unwrap double-encoding)
                try:
                    parsed_inner = json.loads(parsed)
                    return parsed_inner, True  # was double-encoded
                except (json.JSONDecodeError, TypeError):
                    # Inner string wasn't valid JSON, return outer parse
                    return parsed, False

        return parsed, False
    except (json.JSONDecodeError, TypeError):
        # Could not parse at all
        return None, False


def _normalize_label(value: Any) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return normalized if normalized in _LABEL_VALUES else None


def _normalize_number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_short_text(value: Any, max_len: int = 120) -> Optional[str]:
    if value is None:
        return None
    text = " ".join(str(value).split()).strip()
    if not text:
        return None
    return text[:max_len]


def _empty_single_car_payload() -> Dict[str, Any]:
    payload = {
        "car_name": None,
        "facts": dict(SINGLE_CAR_FACTS_TEMPLATE),
        "short_notes": [],
        "sources": [],
        "car_profile": {},
    }
    for category_name, template in SINGLE_CAR_CATEGORY_TEMPLATE.items():
        payload[category_name] = dict(template)
    return payload


def _normalize_sources(value: Any) -> List[str]:
    out: List[str] = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict):
            raw_url = item.get("url")
        else:
            raw_url = item
        clean_url = _sanitize_url(raw_url)
        if clean_url and clean_url not in out:
            out.append(clean_url)
        if len(out) >= _MAX_STAGE_A_SOURCES:
            break
    return out


def _normalize_car_profile(value: Any) -> Dict[str, Any]:
    """Keep grounded factual profile data in a bounded JSON shape for UI/prompts."""
    if not isinstance(value, dict):
        return {}

    def _clean(obj: Any, depth: int = 0) -> Any:
        if depth > CAR_PROFILE_MAX_NESTING_DEPTH:
            return None
        if isinstance(obj, dict):
            cleaned = {}
            for key, item in obj.items():
                if isinstance(key, str) and len(key) <= 80:
                    cleaned[key] = _clean(item, depth + 1)
            return cleaned
        if isinstance(obj, list):
            return [_clean(item, depth + 1) for item in obj[:12]]
        if isinstance(obj, str):
            return " ".join(obj.split())[:500]
        if isinstance(obj, (int, float)) and not isinstance(obj, bool):
            return obj
        if obj is None or isinstance(obj, bool):
            return obj
        return None

    cleaned = _clean(value)
    return cleaned if isinstance(cleaned, dict) else {}


def normalize_single_car_payload(
    payload: Dict[str, Any], fallback_name: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Normalize Stage A per-car payload into a compact, stable shape."""
    if not isinstance(payload, dict):
        return None

    normalized = _empty_single_car_payload()
    normalized["car_name"] = _normalize_short_text(
        payload.get("car_name") or fallback_name, 140
    )

    category_seen = False
    for category_name, template in SINGLE_CAR_CATEGORY_TEMPLATE.items():
        source_category = payload.get(category_name)
        if not isinstance(source_category, dict):
            continue
        for field_name in template.keys():
            label = _normalize_label(source_category.get(field_name))
            normalized[category_name][field_name] = label
            category_seen = category_seen or (label is not None)

    facts = payload.get("facts")
    if isinstance(facts, dict):
        normalized["facts"]["horsepower"] = _normalize_number(facts.get("horsepower"))
        normalized["facts"]["weight_kg"] = _normalize_number(facts.get("weight_kg"))
        normalized["facts"]["body_type"] = _normalize_short_text(
            facts.get("body_type"), 60
        )
        normalized["facts"]["fuel_type"] = _normalize_short_text(
            facts.get("fuel_type"), 60
        )

    raw_notes = (
        payload.get("short_notes")
        if isinstance(payload.get("short_notes"), list)
        else []
    )
    normalized_notes: List[str] = []
    for item in raw_notes:
        note = _normalize_short_text(item, 120)
        if note:
            normalized_notes.append(note)
        if len(normalized_notes) >= _MAX_STAGE_A_NOTES:
            break
    normalized["short_notes"] = normalized_notes
    normalized["sources"] = _normalize_sources(payload.get("sources"))
    normalized["car_profile"] = _normalize_car_profile(payload.get("car_profile"))

    has_facts = any(value is not None for value in normalized["facts"].values())
    has_notes = bool(normalized["short_notes"])
    has_sources = bool(normalized["sources"])
    if not (category_seen or has_facts or has_notes or has_sources):
        return None
    return normalized


# ============================================================
# PROMPT BUILDING
# ============================================================


def build_compare_grounding_prompt(
    cars: List[Dict[str, str]], region: str = "IL", language: str = "he/en"
) -> str:
    """Build Stage A grounded prompt (sources + factual metrics)."""
    return f"{build_comparison_prompt(cars)}\n\nGrounding scope: region={region}, language={language}."


def build_single_car_prompt(car: Dict, region: str = "IL") -> str:
    """Build Stage A prompt for a SINGLE car using a compact evidence schema."""
    sanitized = {
        "make": escape_prompt_input(car.get("make", ""), max_length=50),
        "model": escape_prompt_input(car.get("model", ""), max_length=100),
    }
    if car.get("year"):
        sanitized["year"] = int(car["year"])
    if car.get("engine_type"):
        sanitized["engine_type"] = escape_prompt_input(
            car["engine_type"], max_length=50
        )
    if car.get("gearbox"):
        sanitized["gearbox"] = escape_prompt_input(car["gearbox"], max_length=50)

    car_json = json.dumps(sanitized, ensure_ascii=False)
    bounded_car = wrap_user_input_in_boundary(car_json, boundary_tag="car_input")
    data_instruction = create_data_only_instruction()
    segment_key, segment_signals = _infer_compare_segment_details(sanitized, {})
    segment_rule = COMPARE_SEGMENT_PROMPT_RULES.get(
        segment_key,
        COMPARE_SEGMENT_PROMPT_RULES["general_private_car"],
    )
    segment_context = {
        "segment_key": segment_key,
        "inference_signals": segment_signals,
        "focus_more": segment_rule.get("focus_more", []),
        "focus_less": segment_rule.get("focus_less", []),
        "special_note": segment_rule.get("special_note"),
    }
    category_behavior = {
        key: COMPARE_CATEGORY_BEHAVIOR_RULES[key] for key in COMPARE_CATEGORY_NAMES
    }

    return f"""{data_instruction}

You are acting as an experienced automotive analyst giving a first-impression assessment. Estimates based on your general knowledge, market reputation, and segment norms are valid and encouraged. You do NOT need official verified sources for every metric — provide your best reasoned assessment. Include source URLs when available, but omit them if you don't have one.

You are a car research extractor for ONE car.
Return ONLY compact evidence in JSON.
Return ONLY valid JSON. No markdown. No code fences. No prose outside JSON.

{bounded_car}

Region: {region}

SEGMENT_CONTEXT (deterministic; use this mission when assigning labels):
{json.dumps(segment_context, ensure_ascii=False)}

CATEGORY_BEHAVIOR_RULES:
{json.dumps(category_behavior, ensure_ascii=False)}

Return this exact JSON structure:
{{
  "car_name": "string",
  "reliability": {{
    "overall": "high"|"medium"|"low"|null,
    "issue_frequency": "low"|"medium"|"high"|null,
    "issue_severity": "low"|"medium"|"high"|null,
    "repair_cost_risk": "low"|"medium"|"high"|null,
    "recall_risk": "low"|"medium"|"high"|null,
    "parts_complexity": "low"|"medium"|"high"|null
  }},
  "ownership_cost": {{
    "fuel_cost": "low"|"medium"|"high"|null,
    "routine_maintenance": "low"|"medium"|"high"|null,
    "repair_burden": "low"|"medium"|"high"|null,
    "insurance_burden": "low"|"medium"|"high"|null,
    "depreciation_risk": "low"|"medium"|"high"|null
  }},
  "comfort_practicality": {{
    "space": "low"|"medium"|"high"|null,
    "ride_comfort": "low"|"medium"|"high"|null,
    "trunk_usefulness": "low"|"medium"|"high"|null,
    "daily_usability": "low"|"medium"|"high"|null
  }},
  "performance_driving": {{
    "power_feel": "low"|"medium"|"high"|null,
    "power_to_weight": "low"|"medium"|"high"|null,
    "braking_confidence": "low"|"medium"|"high"|null,
    "handling_agility": "low"|"medium"|"high"|null,
    "fun_to_drive": "low"|"medium"|"high"|null
  }},
  "facts": {{
    "horsepower": <number or null>,
    "weight_kg": <number or null>,
    "body_type": "string or null",
    "fuel_type": "string or null"
  }},
  "car_profile": {{
    "vehicle_identity": {{"make":"string","model":"string","year":"string|null","generation":"string|null","body_type":"string|null","segment":"string|null","israel_market_status":"sold_new|sold_used_only|parallel_import|discontinued_in_israel|unclear|null"}},
    "pricing_israel": {{"new_price_range_ils":"string|null","used_price_range_ils":"string|null","notes":["string"],"sources":["url"]}},
    "license_fee_israel": {{"annual_fee_ils": <number or null>, "method": "official|unknown", "notes": ["string"], "sources": ["url"]}},
    "trim_levels_israel": [{{"trim_name":"string","price_ils": <number or null>, "main_equipment":["string"],"powertrain":"string|null","safety_equipment":["string"],"source":"url|null"}}],
    "powertrain_specs": {{"engine":"string|null","gearbox":"string|null","drivetrain":"string|null","horsepower": <number or null>,"torque_nm": <number or null>,"battery_kwh": <number or null>,"ev_range_km": <number or null>,"zero_to_100_sec": <number or null>,"trunk_liters": <number or null>,"seats": <number or null>,"sources":["url"]}},
    "fuel_consumption": {{"official_value":"string|null","real_world_value":"string|null","method":"official|review_based|owner_reported|unknown","notes":["string"],"sources":["url"]}},
    "official_safety": {{"rating":"string|null","organization":"Euro NCAP|IIHS|NHTSA|ANCAP|Israeli Ministry/Importer|unknown|null","test_year": <number or null>,"notes":["string"],"sources":["url"]}},
    "warranty_israel": {{"vehicle_warranty":"string|null","battery_warranty":"string|null","sources":["url"]}},
    "recalls": {{"known_recalls":[{{"year": <number or null>, "issue":"string", "source":"url|null"}}],"checked_against_official_source": <boolean>,"sources":["url"]}},
    "reliability_risks": ["string"],
    "ownership_cost_notes": {{"maintenance_cost_pressure":"low|medium|high|unknown","insurance_cost_pressure":"low|medium|high|unknown","depreciation_risk":"low|medium|high|unknown","parts_availability":"low|medium|high|unknown","notes":["string"]}},
    "best_for": ["string"],
    "not_ideal_for": ["string"],
    "sources": ["url"]
  }},
  "short_notes": ["up to 4 short bullets"],
  "sources": ["up to 5 source URLs"]
}}

RULES:
1. Google Search grounding is mandatory. Search Hebrew and English when useful.
2. Prefer official importer pages, Israeli Ministry of Transport, official safety organizations, official recall data, and reputable Israeli car sites.
3. Keep the legacy labels compact and stable, but also fill car_profile with sourced factual facts where available.
4. Do NOT compare against another car. Do NOT score numerically. Do NOT write long explanations.
5. Do not invent official safety ratings, Israeli trim levels, prices, license fees, recalls, specs, consumption, or warranty terms. Use null/unknown plus notes when unavailable.
6. license_fee_israel.method can only be official or unknown.
7. If exact official safety rating is not found, set rating=null, organization="unknown", and add an explanatory note.
8. Keep source URLs for factual claims about price, trims, license fee, safety, recalls, specs, fuel/energy consumption, and warranty.
9. Do not return visible numeric quality scores or score-like quality fields. Percentages are allowed only for factual specs or official safety sub-scores from official sources.
10. short_notes must contain at most 4 short items; sources must contain direct http/https URLs.
11. Segment-aware labels are required: judge the car against realistic expectations of its inferred mission, not one universal standard.
12. Return ONLY valid JSON.
""".strip()


def build_comparison_prompt(cars: List[Dict[str, str]]) -> str:
    """Build the comparison prompt for Gemini with strict JSON output."""

    # Sanitize car inputs including year, engine_type, and gearbox
    sanitized_cars = []
    for car in cars:
        sanitized_car = {
            "make": escape_prompt_input(car.get("make", ""), max_length=50),
            "model": escape_prompt_input(car.get("model", ""), max_length=100),
        }
        # Include explicit year if provided (single year, not a range)
        if car.get("year"):
            sanitized_car["year"] = int(car.get("year"))
        elif car.get("year_start"):
            sanitized_car["year_start"] = car.get("year_start")
            sanitized_car["year_end"] = car.get("year_end")
        # Include engine type and gearbox as explicit assumptions
        if car.get("engine_type"):
            sanitized_car["engine_type"] = escape_prompt_input(
                car.get("engine_type", ""), max_length=50
            )
        if car.get("gearbox"):
            sanitized_car["gearbox"] = escape_prompt_input(
                car.get("gearbox", ""), max_length=50
            )
        sanitized_cars.append(sanitized_car)

    cars_json = json.dumps(sanitized_cars, ensure_ascii=False, indent=2)
    bounded_cars = wrap_user_input_in_boundary(cars_json, boundary_tag="cars_input")
    data_instruction = create_data_only_instruction()

    # Build slot mapping for stable keys
    slot_mapping = {}
    segment_context = {}
    for i, car in enumerate(sanitized_cars):
        slot_key = f"car_{i + 1}"
        slot_mapping[slot_key] = build_display_name(car)
        segment_key, segment_signals = _infer_compare_segment_details(car, {})
        segment_rule = COMPARE_SEGMENT_PROMPT_RULES.get(
            segment_key,
            COMPARE_SEGMENT_PROMPT_RULES["general_private_car"],
        )
        segment_context[slot_key] = {
            "segment_key": segment_key,
            "inference_signals": segment_signals,
            "focus_more": segment_rule.get("focus_more", []),
            "focus_less": segment_rule.get("focus_less", []),
            "special_note": segment_rule.get("special_note"),
        }

    slot_mapping_text = "\n".join(f"  {k}: {v}" for k, v in slot_mapping.items())

    return f"""
{data_instruction}

You are a car comparison data analyst.

🔴 CRITICAL: You are a data retrieval agent ONLY. You MUST NOT:
- Decide winners or compare scores between cars
- Compute any scores or rankings
- Make recommendations or judgments
- State which car is "better" in any way

Your ONLY job is to retrieve factual data for each metric for each car.
Return ONLY JSON. No markdown. No code fences. No commentary.
Use double quotes only. Do not include trailing commas.
Do not output tables, repetition, or long paragraphs.

{bounded_cars}

IMPORTANT: Use these EXACT keys in the "cars" object:
{slot_mapping_text}

Return data for each car using the slot key (car_1, car_2, etc.) NOT the car name.

SEGMENT_CONTEXT_BY_SLOT (deterministic; use this mission when assigning labels):
{json.dumps(segment_context, ensure_ascii=False, indent=2)}

CATEGORY_BEHAVIOR_RULES:
{json.dumps(COMPARE_CATEGORY_BEHAVIOR_RULES, ensure_ascii=False, indent=2)}

Return a SINGLE JSON object with this EXACT structure:

{{
  "grounding_successful": true,
  "search_queries_used": ["list of actual search queries you ran"],
  "assumptions": {{
    "year_assumption": "If year range wasn't clear, state what years you assumed",
    "engine_assumption": "If specific engine wasn't given, state what you assumed",
    "trim_assumption": "If specific trim wasn't given, state what you assumed"
  }},
  "cars": {{
    "car_1": {{
      "reliability_risk": {{
        "reliability_rating": {{
          "value": 0-100 or null,
          "missing_reason": "reason if null",
          "sources": [
            {{"url": "https://...", "title": "Source title", "snippet": "Brief quote (max 25 words)"}}
          ]
        }},
        "major_failure_risk": {{
          "value": "low" | "medium" | "high" | null,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "common_failure_patterns": {{
          "value": [
            {{"issue": "Issue name", "frequency": "common/rare/occasional"}}
          ] or null,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "mileage_sensitivity": {{
          "value": "low" | "medium" | "high" | null,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "maintenance_complexity": {{
          "value": "low" | "medium" | "high" | null,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "expected_maintenance_cost_level": {{
          "value": "low" | "medium" | "high" | null,
          "missing_reason": "reason if null",
          "sources": [...]
        }}
      }},
      "ownership_cost": {{
        "fuel_economy_real_world": {{
          "value": <number in L/100km> or null,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "insurance_cost_level": {{
          "value": "low" | "medium" | "high" | null,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "depreciation_value_retention": {{
          "value": "low" | "medium" | "high" | null,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "parts_availability": {{
          "value": "low" | "medium" | "high" | null,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "service_network_ease": {{
          "value": "low" | "medium" | "high" | null,
          "missing_reason": "reason if null",
          "sources": [...]
        }}
      }},
      "practicality_comfort": {{
        "cabin_space": {{
          "value": "small" | "medium" | "large" | null,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "trunk_space_liters": {{
          "value": <number in liters> or null,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "ride_comfort": {{
          "value": "low" | "medium" | "high" | null,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "noise_insulation": {{
          "value": "low" | "medium" | "high" | null,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "city_driveability": {{
          "value": "low" | "medium" | "high" | null,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "features_value": {{
          "value": "low" | "medium" | "high" | null,
          "missing_reason": "reason if null",
          "sources": [...]
        }}
      }},
      "driving_performance": {{
        "acceleration_0_100": {{
          "value": <number in seconds> or null,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "engine_power_hp": {{
          "value": <number in hp> or null,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "handling_stability": {{
          "value": "low" | "medium" | "high" | null,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "braking_performance": {{
          "value": "low" | "medium" | "high" | null,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "highway_stability": {{
          "value": "low" | "medium" | "high" | null,
          "missing_reason": "reason if null",
          "sources": [...]
        }}
      }}
    }}
    "car_2": {{ ... }}
  }}
}}

RULES:
1. Use your best available knowledge to fill every metric. Official sources are preferred but NOT required — well-reasoned estimates based on general knowledge, segment norms, and known characteristics are acceptable and encouraged.
2. Only set value=null if you genuinely have no basis whatsoever to estimate. Prefer a low-confidence estimate over null.
3. Sources are optional. If you have a URL, include it. If not, you may omit the sources array or leave it empty — do NOT set value=null just because you lack a URL.
4. Segment-aware labels are required: judge each car against realistic expectations of its inferred mission, not one universal standard.
5. Keep the 4 main categories unchanged; only the sub-priority logic is segment-aware.
6. Do NOT compare cars or state winners - only provide raw data.
7. Return ONLY valid JSON. No markdown, no explanations.
8. Do not wrap the response in an array; return one object that starts with {{ and ends with }}.
9. If a required field is truly unknown, keep the key and use null instead of omitting it — but always try to estimate first.
""".strip()


def build_compare_writer_prompt(
    cars_selected_slots: Dict,
    computed_result: Dict,
    grounded_output: Dict,
    buyer_profile: Optional[Dict[str, Any]] = None,
) -> str:
    """Build Stage B prompt for a decision-based practical report."""

    def _truncate_text(value: Any, max_chars: int = 500) -> str:
        raw = str(value or "").strip()
        return raw[:max_chars]

    def _evidence_snapshot(slot_key: str) -> Dict[str, Any]:
        grounded_car = ((grounded_output or {}).get("cars", {}) or {}).get(slot_key, {})
        return {
            "car_profile": grounded_car.get("car_profile") or {},
            "legacy_labels": {
                "reliability": grounded_car.get("reliability") or {},
                "ownership_cost": grounded_car.get("ownership_cost") or {},
                "comfort_practicality": grounded_car.get("comfort_practicality") or {},
                "performance_driving": grounded_car.get("performance_driving") or {},
            },
            "notes": (grounded_car.get("short_notes") or [])[:3],
            "facts": grounded_car.get("facts") or {},
            "sources": (grounded_car.get("sources") or [])[:3],
        }

    slot_keys = _ordered_compare_slot_keys(
        cars_selected_slots,
        (computed_result.get("cars") or {})
        if isinstance(computed_result, dict)
        else {},
        ((grounded_output or {}).get("cars") or {})
        if isinstance(grounded_output, dict)
        else {},
    )
    allowed_labels = slot_keys + ["tie", "depends", "unknown"]
    per_slot_schema_lines = []
    for slot_key in slot_keys:
        per_slot_schema_lines.append(f'    "choose_{slot_key}_if": ["string"],')
        per_slot_schema_lines.append(f'    "avoid_or_check_{slot_key}_if": ["string"],')
    key_difference_fields = ",".join(f'"{slot_key}":"string"' for slot_key in slot_keys)
    deterministic_preferences = {
        "overall": _normalize_compare_writer_winner(
            computed_result.get("overall_winner"), slot_keys
        )
        or "depends",
        "legacy_category_winners": {
            category_key: _normalize_compare_writer_winner(winner, slot_keys)
            or "depends"
            for category_key, winner in (
                (computed_result.get("category_winners") or {}).items()
            )
        },
        "balanced_comparison": bool(
            ((computed_result.get("comparison_status") or {}).get("balanced", True))
        ),
    }
    checked_version_seed = build_checked_versions(cars_selected_slots, grounded_output)
    model_payload = {
        "cars": {
            slot_key: {
                "label": _truncate_text(
                    ((cars_selected_slots or {}).get(slot_key, {}) or {}).get(
                        "display_name"
                    )
                    or slot_key,
                    120,
                ),
                "user_selection": {
                    "make": _truncate_text(
                        ((cars_selected_slots or {}).get(slot_key, {}) or {}).get(
                            "make"
                        ),
                        80,
                    ),
                    "model": _truncate_text(
                        ((cars_selected_slots or {}).get(slot_key, {}) or {}).get(
                            "model"
                        ),
                        80,
                    ),
                    "year": _truncate_text(
                        ((cars_selected_slots or {}).get(slot_key, {}) or {}).get(
                            "year"
                        )
                        or ((cars_selected_slots or {}).get(slot_key, {}) or {}).get(
                            "year_start"
                        ),
                        40,
                    ),
                    "engine_type": _truncate_text(
                        ((cars_selected_slots or {}).get(slot_key, {}) or {}).get(
                            "engine_type"
                        ),
                        80,
                    ),
                    "transmission": _truncate_text(
                        ((cars_selected_slots or {}).get(slot_key, {}) or {}).get(
                            "gearbox"
                        ),
                        80,
                    ),
                },
                "checked_version_seed": checked_version_seed.get(slot_key) or {},
                "evidence": _evidence_snapshot(slot_key),
            }
            for slot_key in slot_keys
        },
        "decision_categories": [
            {"category_key": key, "category_name_he": name}
            for key, name in DECISION_CATEGORY_DEFINITIONS
        ],
        "deterministic_preference_hints": deterministic_preferences,
        "buyer_profile": buyer_profile,
        "buyer_profile_rule": "User preference context only; use it only to explain fit. It must not override factual vehicle data.",
        "sources": ((grounded_output or {}).get("sources") or [])[
            : _MAX_STAGE_A_SOURCES * max(1, len(slot_keys))
        ],
    }

    payload_json = json.dumps(model_payload, ensure_ascii=False, separators=(",", ":"))
    prompt = f"""You are a neutral Israeli-market car comparison writer.
Write simple, practical Hebrew for decision support. Use only MODEL_PAYLOAD below and do not invent facts.
MODEL_PAYLOAD contains grounded per-car facts and user preference context, if supplied.

MODEL_PAYLOAD:
{payload_json}

Return ONLY valid JSON with EXACTLY this top-level schema:
{{
  "decision_result": {{
    "overall_decision": {{"label":"{
        "|".join(allowed_labels)
    }","text":"Hebrew practical decision summary without scores"}},
{chr(10).join(per_slot_schema_lines)}
    "category_decisions": [
      {{"category_key":"pricing_and_value","category_name_he":"מחיר ותמורה","preferred":"{
        "|".join(allowed_labels)
    }","why":"string","important_caveat":"string|null"}}
    ],
    "key_differences": [{{"title":"string",{
        key_difference_fields
    },"meaning_for_buyer":"string"}}],
    "competitors_to_consider": [{{"model":"string","why_consider":"string"}}],
    "practical_summary":"Hebrew practical paragraph. Neutral. No first person. No direct buy/don't-buy command."
  }},
  "checked_versions": {{
    {
        ",".join(
            f'"{slot_key}":{{"make":"string","model":"string","year":"string","trim":"string","engine_type":"string","transmission":"string","drivetrain":"string","seats":"string","data_basis":"user_input|verified_source|ai_inference|mixed","confidence":"high|medium|low|unverified","notes":"string"}}'
            for slot_key in slot_keys
        )
    }
  }},
  "sources": ["url"]
}}

HARD RULES:
1. Do not output /100, /10, winnerScore, overall_score, category_score, category weights, "ציון", or "ניקוד".
2. Do not say "המנצח". Prefer "הבחירה הסבירה יותר", "תלוי שימוש", "אין הכרעה חד משמעית", "עדיפות קלה", "דורש בדיקה נוספת".
3. Do not use first person. Do not say "אני ממליץ", "הייתי קונה", "תקנה", or "אל תקנה".
4. No direct purchase advice and no "הרכב הטוב ביותר".
5. Google-grounded factual claims must keep source URLs. If official safety/prices/trims/fees/recalls/warranty are unavailable, use null/unknown or an explicit caveat.
6. Fill all decision_categories from MODEL_PAYLOAD. Use preferred="unknown" or "depends" when evidence is insufficient.
7. buyer_profile is preference context only; it may affect fit explanation only and never overrides car facts.
8. For EVERY selected car, `choose_car_X_if` and `avoid_or_check_car_X_if` must contain 1-3 non-empty Hebrew strings whenever MODEL_PAYLOAD includes any usable evidence for that car.
9. Never return [] for per-car arrays if `overall_decision`, `category_decisions`, `key_differences`, or the evidence snapshot can support safe fallback wording.
10. If evidence is thin, write cautious guidance about fit, trade-offs, and what to verify before purchase instead of leaving arrays empty.
11. `checked_versions` is mandatory for every selected car. It must clearly state the representative version being discussed, including uncertainty when trim, transmission, engine, or year are not fully verified.
12. In `checked_versions.transmission`, use general labels only: אוטומטית, רובוטית, רציפה, ידנית, לא ידוע / לבדיקה. Do not use DSG, DCT, DHT, or CVT as the visible default transmission label.
13. If the user selected a general engine/transmission value, do not present it as a fully verified exact specification. Use `notes` to explain that it still requires verification against the importer spec or vehicle license.
"""
    return prompt


def build_compare_writer_retry_prompt(
    cars_selected_slots: Dict, computed_result: Dict
) -> str:
    """Build a minimal retry prompt for summary+winner only."""
    slot_keys = _ordered_compare_slot_keys(
        cars_selected_slots,
        (computed_result.get("cars") or {})
        if isinstance(computed_result, dict)
        else {},
    )
    allowed_winners = slot_keys + ["tie"]
    retry_payload = {
        "cars": {
            slot_key: {
                "label": (
                    (cars_selected_slots.get(slot_key, {}) or {}).get(
                        "display_name", slot_key
                    )
                ),
            }
            for slot_key in slot_keys
        },
        "overall_winner": computed_result.get("overall_winner"),
        "overall_scores": {
            slot_key: (
                (computed_result.get("cars", {}).get(slot_key, {}) or {}).get(
                    "overall_score"
                )
            )
            for slot_key in slot_keys
        },
    }
    prompt = f"""RETRY_MODE_SUMMARY_ONLY
Return ONLY JSON:
{{
  "summary": "max 20 words",
  "winner": "{"|".join(allowed_winners)}",
  "categories": [],
  "caveats": []
}}
Do not add extra keys. Do not add categories.
DATA:{json.dumps(retry_payload, ensure_ascii=False, separators=(",", ":"))}
"""
    return prompt[: min(COMPARE_WRITER_PROMPT_CHAR_CAP, 4000)]


# ============================================================
# SCORING FUNCTIONS (DETERMINISTIC - CODE ONLY)
# ============================================================


def score_ordinal_negative(
    value: Optional[str], confidence: float = 1.0
) -> Optional[float]:
    """Score ordinal value where low is good (e.g., risk: low=good)."""
    normalized = _normalize_label(value)
    score = ORDINAL_SCORES_NEGATIVE.get(normalized) if normalized else None
    if score is None:
        return None
    return round(score * confidence, 1)


def score_ordinal_positive(
    value: Optional[str], confidence: float = 1.0
) -> Optional[float]:
    """Score ordinal value where high is good (e.g., comfort: high=good)."""
    normalized = _normalize_label(value)
    score = ORDINAL_SCORES_POSITIVE.get(normalized) if normalized else None
    if score is None:
        return None
    return round(score * confidence, 1)


def _average_scores(scores: List[Optional[float]]) -> Optional[float]:
    valid = [score for score in scores if score is not None]
    if not valid:
        return None
    return round(sum(valid) / len(valid), 1)


def _derive_power_to_weight_label(car_data: Dict[str, Any]) -> Optional[str]:
    performance = car_data.get("performance_driving", {})
    explicit = _normalize_label((performance or {}).get("power_to_weight"))
    if explicit:
        return explicit

    facts = car_data.get("facts", {})
    horsepower = _normalize_number((facts or {}).get("horsepower"))
    weight_kg = _normalize_number((facts or {}).get("weight_kg"))
    if (
        horsepower is None
        or weight_kg is None
        or weight_kg < MIN_REASONABLE_VEHICLE_WEIGHT_KG
    ):
        return None

    hp_per_ton = (horsepower * KILOGRAMS_PER_TON) / weight_kg
    if hp_per_ton >= HP_PER_TON_HIGH_THRESHOLD:
        return "high"
    if hp_per_ton >= HP_PER_TON_MEDIUM_THRESHOLD:
        return "medium"
    return "low"


def _derive_horsepower_label(car_data: Dict[str, Any]) -> Optional[str]:
    horsepower = _normalize_number(((car_data.get("facts") or {}).get("horsepower")))
    if horsepower is None:
        return None
    if horsepower >= HORSEPOWER_HIGH_THRESHOLD:
        return "high"
    if horsepower >= HORSEPOWER_MEDIUM_THRESHOLD:
        return "medium"
    return "low"


def _score_power_capability(car_data: Dict[str, Any]) -> Optional[float]:
    performance = car_data.get("performance_driving", {})
    return _average_scores(
        [
            score_ordinal_positive((performance or {}).get("power_feel")),
            score_ordinal_positive(
                _derive_power_to_weight_label(car_data)
                or _derive_horsepower_label(car_data)
            ),
        ]
    )


def _score_subfactor(
    car_data: Dict[str, Any], category_name: str, subfactor_def: Dict[str, Any]
) -> Optional[float]:
    stage_a_key = CATEGORY_SCORE_CONFIG[category_name]["stage_a_key"]
    section = car_data.get(stage_a_key, {})
    if subfactor_def.get("kind") == "positive_label":
        return score_ordinal_positive((section or {}).get(subfactor_def.get("field")))
    if subfactor_def.get("kind") == "negative_label":
        return score_ordinal_negative((section or {}).get(subfactor_def.get("field")))
    if subfactor_def.get("kind") == "power_capability":
        return _score_power_capability(car_data)
    return None


def _has_any_stage_a_evidence(car_data: Dict[str, Any]) -> bool:
    if not isinstance(car_data, dict):
        return False
    for category_name in SINGLE_CAR_CATEGORY_TEMPLATE.keys():
        for value in (car_data.get(category_name) or {}).values():
            if value is not None:
                return True
    if any(value is not None for value in (car_data.get("facts") or {}).values()):
        return True
    if car_data.get("short_notes"):
        return True
    if car_data.get("sources"):
        return True
    if car_data.get("car_profile"):
        return True
    return False


def compute_category_score(
    car_data: Dict, category_name: str
) -> Tuple[Optional[float], Dict[str, Optional[float]]]:
    """Compute deterministic weighted score for a category from simplified Stage A evidence."""
    category_def = CATEGORY_SCORE_CONFIG.get(category_name)
    if not category_def:
        return None, {}

    metric_scores = {}
    total_weighted = 0.0
    total_weights = 0.0
    for metric_name, metric_def in category_def.get("subfactors", {}).items():
        score = _score_subfactor(car_data, category_name, metric_def)
        metric_scores[metric_name] = score
        if score is None:
            continue
        weight = metric_def.get("weight", 0)
        total_weighted += score * weight
        total_weights += weight

    if total_weights == 0:
        return None, metric_scores
    return round(total_weighted / total_weights, 1), metric_scores


def compute_overall_score(
    category_scores: Dict[str, Optional[float]],
) -> Optional[float]:
    """Compute weighted overall score from category scores."""
    total_weighted = 0.0
    total_weights = 0.0
    for cat_name, weight in CATEGORY_WEIGHTS.items():
        score = category_scores.get(cat_name)
        if score is None:
            continue
        total_weighted += score * weight
        total_weights += weight
    if total_weights == 0:
        return None
    return round(total_weighted / total_weights, 1)


def determine_winner(
    scores: Dict[str, Optional[float]], tie_threshold: float = TIE_THRESHOLD
) -> Optional[str]:
    """Determine winner from a dict of car_id -> score. Returns 'tie' if scores are close."""
    valid_scores = {k: v for k, v in scores.items() if v is not None}
    if not valid_scores:
        return None
    if len(valid_scores) < 2:
        return next(iter(valid_scores))
    sorted_scores = sorted(valid_scores.items(), key=lambda x: x[1], reverse=True)
    top_score = sorted_scores[0][1]
    second_score = sorted_scores[1][1]
    if abs(top_score - second_score) < tie_threshold:
        return "tie"
    return sorted_scores[0][0]


def _is_real_winner_id(winner_id: Optional[str], results: Dict[str, Any]) -> bool:
    cars = (results.get("cars") or {}) if isinstance(results, dict) else {}
    return isinstance(winner_id, str) and winner_id in cars


def _build_single_winner_top_reasons(
    results: Dict[str, Any], winner_id: str
) -> List[str]:
    winner_cats = ((results.get("cars") or {}).get(winner_id) or {}).get(
        "categories"
    ) or {}
    sorted_cats = sorted(
        [
            (cat_name, data.get("score"))
            for cat_name, data in winner_cats.items()
            if isinstance(data, dict) and data.get("score") is not None
        ],
        key=lambda x: x[1],
        reverse=True,
    )
    # Numeric scores are intentionally omitted here to comply with the
    # no-score-in-UI policy (see COMPARE_SCORE_EXPLANATION_TEMPLATE_HE).
    return [
        f"יתרון ב{CATEGORY_LABELS_HE.get(cat_name, cat_name)}"
        for cat_name, score in sorted_cats[:3]
    ]


def _build_tie_top_reasons(results: Dict[str, Any]) -> List[str]:
    cars = results.get("cars") or {}
    category_winners = results.get("category_winners") or {}
    tie_candidates = []
    for cat_name, winner in category_winners.items():
        if winner != "tie":
            continue
        scores = []
        for car_data in cars.values():
            cat_data = ((car_data or {}).get("categories") or {}).get(cat_name) or {}
            score = cat_data.get("score")
            if score is not None:
                scores.append(score)
        if not scores:
            continue
        avg_score = sum(scores) / len(scores)
        spread = max(scores) - min(scores) if len(scores) >= 2 else 0.0
        tie_candidates.append((avg_score, spread, cat_name))

    tie_candidates.sort(key=lambda item: (-item[0], item[1], item[2]))

    reasons = [
        f"ב{CATEGORY_LABELS_HE.get(cat_name, cat_name)} הפער קטן מאוד בין הרכבים."
        for _avg_score, spread, cat_name in tie_candidates[:3]
    ]
    if reasons:
        return reasons
    return ["הציונים הכוללים קרובים מאוד, ולכן אין מנצח ברור בהשוואה."]


def _build_safe_top_reasons(results: Dict[str, Any]) -> List[str]:
    winner_id = results.get("overall_winner")
    if _is_real_winner_id(winner_id, results):
        reasons = _build_single_winner_top_reasons(results, winner_id)
        if reasons:
            return reasons
    if winner_id == "tie":
        return _build_tie_top_reasons(results)
    return ["לא ניתן לקבוע מנצח ברור על בסיס המידע הזמין."]


def _build_overall_winner_message(results: Dict[str, Any]) -> str:
    winner_id = results.get("overall_winner")
    if _is_real_winner_id(winner_id, results):
        return "נמצא יתרון כולל לאחד הרכבים."
    if winner_id == "tie":
        return "ההשוואה הכוללת צמודה ולכן הוגדרה כתיקו."
    return "לא ניתן לקבוע מנצח כולל על בסיס המידע הזמין."


def compute_comparison_results(model_output: Dict) -> Dict:
    """
    Compute all scores and determine winners based on model output.
    All scoring is done deterministically in code.
    """
    cars_data = model_output.get("cars", {})

    requested_cars = len(cars_data)
    evidence_cars = 0
    results = {
        "cars": {},
        "category_winners": {},
        "metric_winners": {},
        "overall_winner": None,
        "overall_winner_message": "",
        "top_reasons": [],
        "comparison_status": {
            "requested_cars": requested_cars,
            "cars_with_evidence": 0,
            "balanced": True,
        },
    }

    overall_scores = {}

    for car_id, car_data in cars_data.items():
        has_evidence = _has_any_stage_a_evidence(car_data)
        if has_evidence:
            evidence_cars += 1
        car_result = {
            "categories": {},
            "overall_score": None,
            "evidence_available": has_evidence,
            "evidence_summary": {
                "source_count": len(car_data.get("sources") or []),
                "note_count": len(car_data.get("short_notes") or []),
            },
        }

        category_scores = {}

        for cat_name in CATEGORY_SCORE_CONFIG.keys():
            cat_score, metric_scores = compute_category_score(car_data, cat_name)
            car_result["categories"][cat_name] = {
                "score": cat_score,
                "metrics": metric_scores,
            }
            category_scores[cat_name] = cat_score

        # Compute overall score
        car_result["overall_score"] = compute_overall_score(category_scores)
        overall_scores[car_id] = car_result["overall_score"]

        results["cars"][car_id] = car_result

    # Determine category winners
    for cat_name in CATEGORY_SCORE_CONFIG.keys():
        cat_scores = {
            car_id: results["cars"][car_id]["categories"][cat_name]["score"]
            for car_id in cars_data.keys()
        }
        results["category_winners"][cat_name] = determine_winner(cat_scores)

    # Determine metric winners
    for cat_name, cat_def in CATEGORY_SCORE_CONFIG.items():
        results["metric_winners"][cat_name] = {}
        for metric_name in cat_def.get("subfactors", {}).keys():
            metric_scores = {}
            for car_id in cars_data.keys():
                car_metrics = (
                    results["cars"][car_id]["categories"]
                    .get(cat_name, {})
                    .get("metrics", {})
                )
                metric_scores[car_id] = car_metrics.get(metric_name)
            results["metric_winners"][cat_name][metric_name] = determine_winner(
                metric_scores
            )

    # Determine overall winner
    results["overall_winner"] = determine_winner(overall_scores)
    results["comparison_status"] = {
        "requested_cars": requested_cars,
        "cars_with_evidence": evidence_cars,
        "balanced": evidence_cars == requested_cars,
    }

    results["overall_winner_message"] = _build_overall_winner_message(results)
    results["top_reasons"] = _build_safe_top_reasons(results)

    return results


# ============================================================
# AI CALL FUNCTION
# ============================================================


def _inc_compare_metric(metric: str, reason: Optional[str] = None) -> None:
    if metric == "compare_ai_failures_total":
        bucket = COMPARE_AI_METRICS.setdefault(metric, {})
        key = reason or "unknown"
        bucket[key] = int(bucket.get(key, 0)) + 1
        return
    COMPARE_AI_METRICS[metric] = int(COMPARE_AI_METRICS.get(metric, 0)) + 1


def _estimate_token_count(text: str) -> int:
    return max(1, int(len(text or "") / 4))


def _is_output_too_long_error(raw: str) -> bool:
    lowered = (raw or "").lower()
    return (
        "answer candidate length is too long" in lowered
        or "maximum token limit" in lowered
        or "token limit of 8192" in lowered
    )


_STAGE_A_REQUIRED_KEYS = {
    "grounding_successful",
    "search_queries_used",
    "assumptions",
    "cars",
}

_SINGLE_CAR_REQUIRED_CATEGORIES = {
    "reliability",
    "ownership_cost",
    "comfort_practicality",
    "performance_driving",
}


def _is_valid_single_car_payload(payload):
    """Validate single car payload for expected compact Stage A sections.

    Stage A is intentionally tolerant: if at least one expected section exists,
    normalization can fill the rest with nulls. This reduces false negatives from
    minor model omissions while still rejecting unrelated JSON objects.
    """
    if not isinstance(payload, dict):
        return False
    return bool(
        _SINGLE_CAR_REQUIRED_CATEGORIES.intersection(set(payload.keys()))
        or isinstance(payload.get("car_profile"), dict)
    )


def parse_single_car_json(raw_text: str) -> Tuple[Optional[Dict], Optional[str]]:
    """Parse and validate single-car JSON response."""
    candidate = _extract_first_json_object(_strip_json_code_fences(raw_text))
    for current in (candidate, _repair_json_once(candidate) if candidate else None):
        if not current:
            continue
        try:
            parsed = json.loads(current)
        except json.JSONDecodeError:
            continue
        if _is_valid_single_car_payload(parsed):
            normalized = normalize_single_car_payload(parsed)
            if normalized is not None:
                return normalized, None
    return None, "MODEL_JSON_INVALID"


def _strip_json_code_fences(text: str) -> str:
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", stripped, count=1)
        stripped = re.sub(r"\s*```$", "", stripped, count=1)
    return stripped.strip()


def _extract_first_json_object(text: str) -> Optional[str]:
    raw = text or ""
    start = raw.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    escaped = False
    for idx in range(start, len(raw)):
        ch = raw[idx]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return raw[start : idx + 1]
    return None


def _repair_json_once(text: str) -> str:
    repaired = (text or "").lstrip("\ufeff")
    smart_quotes = {
        "\u201c": '"',
        "\u201d": '"',
        "\u2018": "'",
        "\u2019": "'",
    }
    for src, dst in smart_quotes.items():
        repaired = repaired.replace(src, dst)
    repaired = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", repaired)
    repaired = re.sub(r",(\s*[}\]])", r"\1", repaired)
    return repaired.strip()


def _is_valid_stage_a_payload(payload: Any) -> bool:
    # Use superset check (>=) so extra keys returned by the LLM do not
    # discard an otherwise valid payload.  The four required keys are
    # validated individually below, so unexpected extra keys are harmless.
    return (
        isinstance(payload, dict)
        and set(payload.keys()) >= _STAGE_A_REQUIRED_KEYS
        and isinstance(payload.get("grounding_successful"), bool)
        and isinstance(payload.get("search_queries_used"), list)
        and isinstance(payload.get("assumptions"), dict)
        and isinstance(payload.get("cars"), dict)
    )


def parse_stage_a_json(raw_text: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    candidate = _extract_first_json_object(_strip_json_code_fences(raw_text))
    for current in (candidate, _repair_json_once(candidate) if candidate else None):
        if not current:
            continue
        try:
            parsed = json.loads(current)
        except json.JSONDecodeError:
            continue
        if _is_valid_stage_a_payload(parsed):
            return parsed, None
    return None, "MODEL_JSON_INVALID"


def _truncate_to_word_limit(value: Any, limit: int) -> Optional[str]:
    if not isinstance(value, str):
        return None
    compact = " ".join(value.split())
    if not compact:
        return None
    words = compact.split()
    if len(words) <= limit:
        return compact
    return " ".join(words[:limit])


def _decision_label(value: Any, allowed_slots: Optional[List[str]] = None) -> str:
    if isinstance(value, str):
        normalized = value.strip()
        if normalized in {"tie", "depends", "unknown"}:
            return normalized
        if allowed_slots and normalized in allowed_slots:
            return normalized
        if normalized in {"car_1", "car_2", "car_3"}:
            return normalized
    return "unknown"


def _is_forbidden_decision_text(value: Any) -> bool:
    return isinstance(value, str) and bool(DECISION_FORBIDDEN_TEXT_RE.search(value))


def _sanitize_decision_text(
    value: Any, request_id: Optional[str], field_path: str
) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return ""
    if _is_forbidden_decision_text(text):
        active_logger = current_app.logger if has_app_context() else logger
        active_logger.warning(
            "[COMPARISON] decision_result text sanitized request_id=%s field=%s",
            request_id or "unknown",
            field_path,
        )
        return DECISION_TEXT_FALLBACK_HE
    return text[:700]


def _sanitize_optional_decision_text(
    value: Any, request_id: Optional[str], field_path: str
) -> Optional[str]:
    if value is None:
        return None
    text = _sanitize_decision_text(value, request_id, field_path)
    return text or None


def _sanitize_decision_list(
    value: Any, request_id: Optional[str], field_path: str, max_items: int = 6
) -> List[str]:
    if not isinstance(value, list):
        return []
    out = []
    for idx, item in enumerate(value[:max_items]):
        text = _sanitize_decision_text(item, request_id, f"{field_path}.{idx}")
        if text:
            out.append(text)
    return out


def _decision_category_name_he(category_key: Any) -> str:
    normalized_key = str(category_key or "").strip()
    for key, name in DECISION_CATEGORY_DEFINITIONS:
        if key == normalized_key:
            return name
    return (
        CATEGORY_LABELS_HE.get(normalized_key)
        or normalized_key.replace("_", " ").strip()
    )


def _append_unique_text(target: List[str], text: str, max_items: int = 2) -> None:
    normalized = " ".join(str(text or "").split()).strip()
    if not normalized or normalized in target or len(target) >= max_items:
        return
    target.append(normalized)


def _join_hebrew_labels(labels: List[str]) -> str:
    normalized = [
        str(label or "").strip() for label in labels if str(label or "").strip()
    ]
    if not normalized:
        return ""
    if len(normalized) == 1:
        return normalized[0]
    if len(normalized) == 2:
        return f"{normalized[0]} ו{normalized[1]}"
    return f"{', '.join(normalized[:-1])} ו{normalized[-1]}"


def _build_slot_guidance_lists(
    slot_key: str,
    cars_selected_slots: Dict[str, Dict[str, Any]],
    computed_result: Dict[str, Any],
    source_decision_result: Any,
) -> Tuple[List[str], List[str]]:
    category_items = (
        source_decision_result.get("category_decisions")
        if isinstance(source_decision_result, dict)
        and isinstance(source_decision_result.get("category_decisions"), list)
        else []
    )
    key_differences = (
        source_decision_result.get("key_differences")
        if isinstance(source_decision_result, dict)
        and isinstance(source_decision_result.get("key_differences"), list)
        else []
    )
    overall = (
        source_decision_result.get("overall_decision")
        if isinstance(source_decision_result, dict)
        and isinstance(source_decision_result.get("overall_decision"), dict)
        else {}
    )
    practical_summary = _sanitize_decision_text(
        source_decision_result.get("practical_summary")
        if isinstance(source_decision_result, dict)
        else None,
        None,
        f"{slot_key}.practical_summary",
    )
    slot_keys = _ordered_compare_slot_keys(
        cars_selected_slots,
        computed_result.get("cars") or {},
        _extract_decision_slot_keys(source_decision_result),
    )
    car_label = (
        (cars_selected_slots.get(slot_key) or {}).get("display_name") or slot_key
    ).strip()
    category_advantages: List[str] = []
    category_tradeoffs: List[str] = []
    shared_caveats: List[str] = []

    for item in category_items:
        if not isinstance(item, dict):
            continue
        category_name = _decision_category_name_he(
            item.get("category_key") or item.get("category_name_he")
        )
        preferred = _decision_label(item.get("preferred"), slot_keys)
        if preferred == slot_key:
            _append_unique_text(category_advantages, category_name)
        elif preferred not in {"unknown", "depends", "tie"}:
            _append_unique_text(category_tradeoffs, category_name)
        caveat = _sanitize_optional_decision_text(
            item.get("important_caveat"),
            None,
            f"{slot_key}.important_caveat",
        )
        if caveat:
            _append_unique_text(shared_caveats, caveat, max_items=1)

    for category_key, winner in (
        (computed_result.get("category_winners") or {}).items()
        if isinstance(computed_result, dict)
        else []
    ):
        normalized_winner = _normalize_compare_writer_winner(winner, slot_keys)
        category_name = _decision_category_name_he(category_key)
        if normalized_winner == slot_key:
            _append_unique_text(category_advantages, category_name)
        elif normalized_winner not in {None, "unknown", "depends", "tie"}:
            _append_unique_text(category_tradeoffs, category_name)

    diff_insight = ""
    for item in key_differences:
        if not isinstance(item, dict):
            continue
        title = _sanitize_decision_text(
            item.get("title"), None, f"{slot_key}.diff_title"
        )
        detail = _sanitize_decision_text(
            item.get(slot_key), None, f"{slot_key}.diff_value"
        )
        if title and detail:
            diff_insight = f"{title}: {detail}"
            break

    choose_items: List[str] = []
    avoid_items: List[str] = []
    if category_advantages:
        _append_unique_text(
            choose_items,
            f"אם חשובים לך במיוחד {_join_hebrew_labels(category_advantages[:2])}.",
        )
    if _decision_label(overall.get("label"), slot_keys) == slot_key:
        _append_unique_text(
            choose_items,
            "אם בתמונה הכוללת זו נראית הבחירה הסבירה יותר עבורך, בכפוף למצב הרכב בפועל.",
        )
    elif diff_insight:
        _append_unique_text(
            choose_items, f"אם הפער הבא מתאים לשימוש שלך: {diff_insight}"
        )
    elif practical_summary:
        _append_unique_text(
            choose_items, f"אם הכיוון הכללי של ההשוואה מתאים לך: {practical_summary}"
        )

    if category_tradeoffs:
        _append_unique_text(
            avoid_items,
            f"בדוק אם הפשרה ב{_join_hebrew_labels(category_tradeoffs[:2])} מקובלת עליך מול החלופות.",
        )
    if shared_caveats:
        _append_unique_text(avoid_items, shared_caveats[0])
    _append_unique_text(
        avoid_items,
        "בדוק היסטוריית טיפולים, תאונות, אחריות ועלויות אחזקה לפני החלטה.",
    )

    if not choose_items:
        _append_unique_text(
            choose_items,
            f"אם {car_label} מתאים לצרכים שלך אחרי בדיקת מצב, היסטוריה ועלויות צפויות.",
        )
    return choose_items[:2], avoid_items[:2]


def build_deterministic_decision_result(
    cars_selected_slots: Dict[str, Dict[str, Any]],
    computed_result: Optional[Dict[str, Any]] = None,
    source_decision_result: Any = None,
) -> Dict[str, Any]:
    """Fallback decision layer for legacy cache or failed Stage B."""
    computed_result = computed_result if isinstance(computed_result, dict) else {}
    slot_keys = _ordered_compare_slot_keys(
        cars_selected_slots,
        computed_result.get("cars") or {},
        _extract_decision_slot_keys(source_decision_result),
    )
    winner = _normalize_compare_writer_winner(
        computed_result.get("overall_winner"), slot_keys
    )
    if winner in slot_keys:
        label = winner
        text = "קיימת עדיפות קלה לפי המידע הזמין, אך ההחלטה תלויה בבדיקת מצב הרכב בפועל ובהתאמה לשימוש."
    elif winner == "tie":
        label = "tie"
        text = "אין הכרעה חד משמעית על בסיס המידע הזמין; חשוב להשוות מצב, היסטוריית טיפולים ועלויות צפויות."
    else:
        label = "unknown"
        text = "אין מספיק מידע מאומת כדי לקבוע עדיפות ברורה בין הרכבים."

    category_decisions = []
    for key, name in DECISION_CATEGORY_DEFINITIONS:
        category_decisions.append(
            {
                "category_key": key,
                "category_name_he": name,
                "preferred": "unknown",
                "why": "אין מספיק מידע מנוסח ללא ציונים בקטגוריה זו.",
                "important_caveat": "יש לאמת נתונים מול מקורות רשמיים ובדיקת רכב בפועל.",
            }
        )

    # Build per-slot choose/avoid entries with distinct labels derived from slot display names
    result: Dict[str, Any] = {
        "overall_decision": {"label": label, "text": text},
        "category_decisions": category_decisions,
        "key_differences": [],
        "competitors_to_consider": [],
        "practical_summary": "הבחירה הסבירה יותר תלויה בשימוש, בתקציב, במצב הרכב בפועל ובבדיקה מקצועית לפני החלטה.",
    }
    for slot_key in slot_keys:
        choose_items, avoid_items = _build_slot_guidance_lists(
            slot_key,
            cars_selected_slots,
            computed_result,
            source_decision_result,
        )
        result[f"choose_{slot_key}_if"] = choose_items
        result[f"avoid_or_check_{slot_key}_if"] = avoid_items
    return result


def sanitize_decision_result(
    decision_result: Any,
    cars_selected_slots: Optional[Dict[str, Dict[str, Any]]] = None,
    computed_result: Optional[Dict[str, Any]] = None,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    slot_keys = _ordered_compare_slot_keys(
        cars_selected_slots or {},
        (computed_result or {}).get("cars") or {},
        _extract_decision_slot_keys(decision_result),
    )
    fallback = build_deterministic_decision_result(
        cars_selected_slots or {},
        computed_result or {},
        decision_result,
    )
    if not isinstance(decision_result, dict):
        return fallback
    overall = (
        decision_result.get("overall_decision")
        if isinstance(decision_result.get("overall_decision"), dict)
        else {}
    )
    overall_label = _decision_label(overall.get("label"), slot_keys)
    if overall_label == "unknown":
        overall_label = fallback["overall_decision"]["label"]
    sanitized = {
        "overall_decision": {
            "label": overall_label,
            "text": _sanitize_decision_text(
                overall.get("text"), request_id, "overall_decision.text"
            )
            or fallback["overall_decision"]["text"],
        },
        "category_decisions": [],
        "key_differences": [],
        "competitors_to_consider": [],
        "practical_summary": _sanitize_decision_text(
            decision_result.get("practical_summary"), request_id, "practical_summary"
        )
        or fallback["practical_summary"],
    }
    for slot_key in slot_keys:
        choose_key = f"choose_{slot_key}_if"
        avoid_key = f"avoid_or_check_{slot_key}_if"
        sanitized[choose_key] = _sanitize_decision_list(
            decision_result.get(choose_key), request_id, choose_key
        ) or fallback.get(choose_key, [])
        sanitized[avoid_key] = _sanitize_decision_list(
            decision_result.get(avoid_key), request_id, avoid_key
        ) or fallback.get(avoid_key, [])
    raw_categories = (
        decision_result.get("category_decisions")
        if isinstance(decision_result.get("category_decisions"), list)
        else []
    )
    by_key = {
        item.get("category_key"): item
        for item in raw_categories
        if isinstance(item, dict)
    }
    for key, name in DECISION_CATEGORY_DEFINITIONS:
        item = by_key.get(key) or {}
        sanitized["category_decisions"].append(
            {
                "category_key": key,
                "category_name_he": _sanitize_decision_text(
                    item.get("category_name_he") or name,
                    request_id,
                    f"category_decisions.{key}.name",
                )
                or name,
                "preferred": _decision_label(item.get("preferred"), slot_keys),
                "why": _sanitize_decision_text(
                    item.get("why"), request_id, f"category_decisions.{key}.why"
                )
                or "אין מספיק מידע מאומת בקטגוריה זו.",
                "important_caveat": _sanitize_optional_decision_text(
                    item.get("important_caveat"),
                    request_id,
                    f"category_decisions.{key}.important_caveat",
                ),
            }
        )
    raw_diffs = (
        decision_result.get("key_differences")
        if isinstance(decision_result.get("key_differences"), list)
        else []
    )
    for idx, item in enumerate(raw_diffs[:8]):
        if not isinstance(item, dict):
            continue
        cleaned_diff = {
            "title": _sanitize_decision_text(
                item.get("title"), request_id, f"key_differences.{idx}.title"
            ),
            "meaning_for_buyer": _sanitize_decision_text(
                item.get("meaning_for_buyer"),
                request_id,
                f"key_differences.{idx}.meaning_for_buyer",
            ),
        }
        for slot_key in slot_keys:
            cleaned_diff[slot_key] = _sanitize_decision_text(
                item.get(slot_key), request_id, f"key_differences.{idx}.{slot_key}"
            )
        sanitized["key_differences"].append(cleaned_diff)
    raw_competitors = (
        decision_result.get("competitors_to_consider")
        if isinstance(decision_result.get("competitors_to_consider"), list)
        else []
    )
    for idx, item in enumerate(raw_competitors[:5]):
        if not isinstance(item, dict):
            continue
        model = _sanitize_decision_text(
            item.get("model"), request_id, f"competitors.{idx}.model"
        )
        why = _sanitize_decision_text(
            item.get("why_consider"), request_id, f"competitors.{idx}.why_consider"
        )
        if model or why:
            sanitized["competitors_to_consider"].append(
                {"model": model, "why_consider": why}
            )
    return sanitized


def _validate_decision_writer_response(
    payload: Any,
    cars_selected_slots: Optional[Dict[str, Dict[str, Any]]] = None,
    computed_result: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if not isinstance(payload, dict):
        return None, "payload_not_object"
    if not isinstance(payload.get("decision_result"), dict):
        return None, "missing_decision_result"
    return {
        "decision_result": sanitize_decision_result(
            payload.get("decision_result"),
            cars_selected_slots or {},
            computed_result or {},
            get_request_id(),
        ),
        "checked_versions": _sanitize_checked_versions(
            payload.get("checked_versions"),
            _ordered_compare_slot_keys(
                cars_selected_slots or {},
                (computed_result or {}).get("cars") or {},
            ),
        ),
        "sources": _normalize_sources(payload.get("sources")),
    }, None


def _summarize_compare_writer_payload(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {
            "is_object": False,
            "top_level_keys": [],
        }

    decision_result = (
        payload.get("decision_result")
        if isinstance(payload.get("decision_result"), dict)
        else {}
    )
    decision_slot_keys = _extract_decision_slot_keys(decision_result)

    categories = (
        payload.get("categories") if isinstance(payload.get("categories"), list) else []
    )
    categories_with_explanations = 0
    for item in categories:
        if not isinstance(item, dict):
            continue
        explanations = item.get("explanations")
        if isinstance(explanations, dict) and any(
            str(text or "").strip() for text in explanations.values()
        ):
            categories_with_explanations += 1

    return {
        "is_object": True,
        "top_level_keys": sorted(payload.keys()),
        "has_decision_result": bool(decision_result),
        "checked_versions_count": len(payload.get("checked_versions") or {})
        if isinstance(payload.get("checked_versions"), dict)
        else 0,
        "decision_slot_keys": decision_slot_keys,
        "has_summary": bool(str(payload.get("summary") or "").strip()),
        "has_categories": isinstance(payload.get("categories"), list),
        "category_count": len(categories),
        "has_caveats": isinstance(payload.get("caveats"), list),
        "caveat_count": len(payload.get("caveats") or [])
        if isinstance(payload.get("caveats"), list)
        else None,
        "categories_with_per_car_explanations": categories_with_explanations,
    }


def _summarize_comparison_narrative_shape(narrative: Any) -> Dict[str, Any]:
    if not isinstance(narrative, dict):
        return {
            "exists": False,
            "overall_summary_exists": False,
            "category_explanations_exists": False,
            "per_car_explanations_exist": False,
            "disclaimers_exist": False,
            "category_count": 0,
        }

    categories = (
        narrative.get("category_explanations")
        if isinstance(narrative.get("category_explanations"), list)
        else []
    )
    categories_with_explanations = 0
    for item in categories:
        if not isinstance(item, dict):
            continue
        explanations = item.get("explanations")
        if isinstance(explanations, dict) and any(
            str(text or "").strip() for text in explanations.values()
        ):
            categories_with_explanations += 1

    disclaimers = (
        narrative.get("disclaimers_he")
        if isinstance(narrative.get("disclaimers_he"), list)
        else []
    )
    summary = str(narrative.get("overall_summary") or "").strip()
    return {
        "exists": True,
        "overall_summary_exists": bool(summary),
        "category_explanations_exists": bool(categories),
        "per_car_explanations_exist": categories_with_explanations > 0,
        "disclaimers_exist": any(str(item or "").strip() for item in disclaimers),
        "category_count": len(categories),
        "categories_with_per_car_explanations": categories_with_explanations,
        "partial_summary": summary.startswith(PARTIAL_COMPARISON_SUMMARY_PREFIX),
    }


def _validate_compare_writer_response(
    payload: Any,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if not isinstance(payload, dict):
        return None, "payload_not_object"
    required_keys = {"summary", "winner", "categories", "caveats"}
    if not required_keys.issubset(payload.keys()):
        return None, "missing_required_keys"

    summary = _truncate_to_word_limit(payload.get("summary"), 80)
    categories = payload.get("categories")
    caveats = payload.get("caveats")
    allowed_slot_keys = ["car_1", "car_2", "car_3"]
    winner = _normalize_compare_writer_winner(payload.get("winner"), allowed_slot_keys)
    if summary is None or winner is None:
        return None, "invalid_summary_or_winner"
    if not isinstance(categories, list) or len(categories) > 4:
        return None, "invalid_categories"
    if not isinstance(caveats, list) or len(caveats) > 3:
        return None, "invalid_caveats"

    validated_categories = []
    for item in categories:
        if not isinstance(item, dict):
            return None, "invalid_category_item"
        required_category_keys = {"name", "winner", "why", "tips"}
        if not required_category_keys.issubset(item.keys()):
            return None, "missing_category_keys"
        if item.get("name") not in COMPARE_CATEGORY_NAMES:
            return None, "invalid_category_name"
        category_winner = _normalize_compare_writer_winner(
            item.get("winner"), allowed_slot_keys
        )
        if category_winner is None:
            return None, "invalid_category_winner"
        why = _truncate_to_word_limit(item.get("why"), 60)
        if why is None:
            return None, "invalid_category_why"
        explanations = item.get("explanations")
        normalized_explanations = {}
        if explanations is not None:
            if not isinstance(explanations, dict):
                return None, "invalid_explanations"
            for slot_key in allowed_slot_keys:
                explanation_text = explanations.get(slot_key)
                if explanation_text is None:
                    continue
                explanation_clean = _truncate_to_word_limit(explanation_text, 60)
                if explanation_clean is None:
                    logger.warning(
                        "[COMPARISON] compare_writer explanation dropped slot_key=%s reason=empty_or_invalid",
                        slot_key,
                    )
                    continue
                normalized_explanations[slot_key] = explanation_clean
        tips = item.get("tips")
        if not isinstance(tips, list) or len(tips) > 3:
            return None, "invalid_tips"
        normalized_tips = []
        for tip in tips:
            tip_clean = _truncate_to_word_limit(tip, 30)
            if tip_clean is None:
                logger.warning(
                    "[COMPARISON] compare_writer tip dropped reason=empty_or_invalid"
                )
                continue
            normalized_tips.append(tip_clean)
        validated_categories.append(
            {
                "name": item.get("name"),
                "winner": category_winner,
                "why": why,
                "explanations": normalized_explanations,
                "tips": normalized_tips,
            }
        )

    normalized_caveats = []
    for caveat in caveats:
        caveat_clean = _truncate_to_word_limit(caveat, 30)
        if caveat_clean is None:
            logger.warning(
                "[COMPARISON] compare_writer caveat dropped reason=empty_or_invalid"
            )
            continue
        normalized_caveats.append(caveat_clean)

    return {
        "summary": summary,
        "winner": winner,
        "categories": validated_categories,
        "caveats": normalized_caveats,
    }, None


def validate_compare_writer_response(payload: Any) -> Optional[Dict[str, Any]]:
    validated, _reason = _validate_compare_writer_response(payload)
    return validated


def _salvage_partial_writer_output(
    stage_b_output: Any,
    cars_selected_slots: Dict,
    server_computed_result: Dict,
) -> Optional[Dict[str, Any]]:
    """Build a hybrid narrative from a partial writer response plus deterministic data."""
    if not isinstance(stage_b_output, dict):
        return None

    summary = str(stage_b_output.get("summary") or "").strip()
    if not summary:
        return None

    computed_cars = (
        (server_computed_result.get("cars") or {})
        if isinstance(server_computed_result, dict)
        else {}
    )
    car_keys = _ordered_compare_slot_keys(cars_selected_slots, computed_cars)
    category_explanations = []

    for category_key in COMPARE_CATEGORY_NAMES:
        winner = (server_computed_result.get("category_winners", {}) or {}).get(
            category_key
        ) or "tie"
        explanations = {}
        for car_key in car_keys:
            score = (
                ((computed_cars.get(car_key, {}) or {}).get("categories", {}) or {})
                .get(category_key, {})
                .get("score")
            )
            # Salvage runs on malformed/partial writer payloads, so keep category score
            # rendering defensive: booleans can pass isinstance(..., int) but are never
            # valid 0-100 comparison scores.
            if isinstance(score, (int, float)) and not isinstance(score, bool):
                explanations[car_key] = (
                    COMPARE_SCORE_EXPLANATION_TEMPLATE_HE.format(score=int(score))
                    if COMPARE_SCORE_EXPLANATION_TEMPLATE_HE
                    else ""
                )
            else:
                explanations[car_key] = ""
        category_explanations.append(
            {
                "category_key": category_key,
                "title_he": "",
                "winner": _normalize_compare_writer_winner(winner, car_keys) or "tie",
                "explanations": explanations,
                "why_it_scored_that_way": [],
            }
        )

    raw_caveats = stage_b_output.get("caveats")
    caveats = []
    if isinstance(raw_caveats, list):
        for item in raw_caveats[:3]:
            caveat = str(item or "").strip()
            if caveat:
                caveats.append(caveat)

    return {
        "overall_summary": summary,
        "category_explanations": category_explanations,
        "disclaimers_he": caveats,
    }


def build_deterministic_fallback_narrative(
    cars_selected_slots: Dict, computed_result: Dict
) -> Dict[str, Any]:
    car_keys = _ordered_compare_slot_keys(
        cars_selected_slots,
        (computed_result.get("cars") or {})
        if isinstance(computed_result, dict)
        else {},
    )
    category_explanations = []
    for cat in COMPARE_CATEGORY_NAMES:
        winner = (computed_result.get("category_winners", {}) or {}).get(cat) or "tie"
        explanations = {}
        for car_key in car_keys:
            explanations[car_key] = "מוצגת השוואה מספרית."
        category_explanations.append(
            {
                "category_key": cat,
                "title_he": "",
                "winner": _normalize_compare_writer_winner(winner, car_keys) or "tie",
                "explanations": explanations,
                "why_it_scored_that_way": [
                    "הסבר AI לא זמין כרגע; מוצגת השוואה מספרית."
                ],
            }
        )
    return {
        "overall_summary": "הסבר AI לא זמין כרגע; מוצגת השוואה מספרית.",
        "category_explanations": category_explanations,
        "disclaimers_he": ["אפשר לנסות שוב."],
    }


def mark_partial_comparison_narrative(
    narrative: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Add an explicit disclaimer when Stage A succeeded for only part of the cars."""
    if not isinstance(narrative, dict):
        return narrative
    patched = dict(narrative)
    summary = str(patched.get("overall_summary") or "").strip()
    if summary and not summary.startswith(PARTIAL_COMPARISON_SUMMARY_PREFIX):
        patched["overall_summary"] = f"{PARTIAL_COMPARISON_SUMMARY_PREFIX} {summary}"
    disclaimers = list(patched.get("disclaimers_he") or [])
    if PARTIAL_COMPARISON_DISCLAIMER not in disclaimers:
        disclaimers.append(PARTIAL_COMPARISON_DISCLAIMER)
    patched["disclaimers_he"] = disclaimers[:3]
    return patched


def _empty_stage_a_output(
    cars_selected_slots: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    cars = {}
    for slot_key, slot_data in (cars_selected_slots or {}).items():
        empty_payload = _empty_single_car_payload()
        empty_payload["car_name"] = _normalize_short_text(
            (slot_data or {}).get("display_name"), 140
        )
        cars[slot_key] = empty_payload
    return {
        "cars": cars,
        "sources": [],
    }


def _truncate_error_message(message: Any, max_len: int = 180) -> str:
    text = " ".join(str(message or "").split())
    if len(text) <= max_len:
        return text
    return f"{text[: max_len - ELLIPSIS_LEN]}..."


def _sanitize_stage_a_errors(errors: List[str], max_items: int = 5) -> List[str]:
    """Normalize Stage A slot errors into a bounded, user-safe list."""
    sanitized: List[str] = []
    for err in (errors or [])[:max_items]:
        slot_key, sep, code_or_message = str(err).partition(": ")
        if sep:
            sanitized.append(
                f"{slot_key}: {_truncate_error_message(code_or_message, 96)}"
            )
        else:
            sanitized.append(_truncate_error_message(err, 120))
    return sanitized


def _extract_stage_a_error_code(errors: List[str]) -> str:
    """Extract the first Stage A error code from '<slot>: <code>' style entries."""
    if not errors:
        return "UNKNOWN"
    first = str(errors[0])
    _, sep, code_or_message = first.partition(": ")
    if not sep:
        return _truncate_error_message(first, 96) or "UNKNOWN"
    return _truncate_error_message(code_or_message, 96) or "UNKNOWN"


def _build_stage_a_summary(computed_result: Dict[str, Any]) -> Dict[str, Any]:
    slot_keys = _ordered_compare_slot_keys(
        (computed_result.get("cars") or {}) if isinstance(computed_result, dict) else {}
    )
    category_winners = []
    for category_name, winner in (
        computed_result.get("category_winners", {}) or {}
    ).items():
        category_winners.append(
            {
                "name": category_name,
                "winner": _normalize_compare_writer_winner(winner, slot_keys) or "tie",
            }
        )
    comparison_status = computed_result.get("comparison_status", {}) or {}
    balanced = bool(comparison_status.get("balanced", True))
    return {
        "summary": "סיכום מספרי של ההשוואה."
        if balanced
        else "סיכום מספרי חלקי של ההשוואה.",
        "winner": _normalize_compare_writer_winner(
            computed_result.get("overall_winner"), slot_keys
        )
        or "tie",
        "category_winners": category_winners,
        "caveats": (
            ["המידע עשוי להשתנות."] if balanced else [PARTIAL_COMPARISON_DISCLAIMER]
        ),
        "balanced": balanced,
        "cars_with_evidence": int(comparison_status.get("cars_with_evidence", 0)),
        "requested_cars": int(comparison_status.get("requested_cars", 0)),
    }


def _build_stage_b_payload(
    narrative: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not isinstance(narrative, dict):
        return None
    return {
        "categories": narrative.get("category_explanations", []),
        "narrative": narrative.get("overall_summary"),
    }


def _has_usable_comparison_narrative(narrative: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(narrative, dict):
        return False
    if str(narrative.get("overall_summary") or "").strip():
        return True
    if any(str(item or "").strip() for item in (narrative.get("disclaimers_he") or [])):
        return True
    for category in narrative.get("category_explanations") or []:
        if not isinstance(category, dict):
            continue
        explanations = (
            category.get("explanations")
            if isinstance(category.get("explanations"), dict)
            else {}
        )
        if any(str(value or "").strip() for value in explanations.values()):
            return True
        if any(
            str(value or "").strip()
            for value in (category.get("why_it_scored_that_way") or [])
        ):
            return True
    return False


def _normalize_stage_b_category_for_narrative(
    category: Any,
) -> Optional[Dict[str, Any]]:
    if not isinstance(category, dict):
        return None
    fallback_text = str(
        category.get("why") or category.get("text") or category.get("summary") or ""
    ).strip()
    source_explanations = (
        category.get("explanations")
        if isinstance(category.get("explanations"), dict)
        else {}
    )
    explanations = {}
    for car_key in ("car_1", "car_2", "car_3"):
        text = str(source_explanations.get(car_key) or fallback_text or "").strip()
        if text:
            explanations[car_key] = text
    why_list = category.get("why_it_scored_that_way")
    if not isinstance(why_list, list):
        why_list = category.get("tips")
    if not isinstance(why_list, list):
        why_list = [fallback_text] if fallback_text else []
    return {
        "category_key": category.get("category_key") or category.get("name") or "",
        "title_he": category.get("title_he") or "",
        "winner": category.get("winner") or "",
        "explanations": explanations,
        "why_it_scored_that_way": why_list,
    }


def resolve_comparison_narrative(
    computed_result: Optional[Dict[str, Any]],
    ai_payload: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    if isinstance(computed_result, dict):
        stored_narrative = computed_result.get("narrative")
        if isinstance(stored_narrative, dict):
            candidates.append(stored_narrative)
        stored_decision_result = computed_result.get("decision_result")
        if isinstance(stored_decision_result, dict):
            candidates.append(
                convert_decision_result_to_narrative(
                    {
                        "decision_result": sanitize_decision_result(
                            stored_decision_result, {}, computed_result, None
                        )
                    },
                    {},
                )
            )
        if any(
            key in computed_result
            for key in ("overall_summary", "category_explanations", "disclaimers_he")
        ):
            candidates.append(
                {
                    "overall_summary": computed_result.get("overall_summary"),
                    "category_explanations": computed_result.get(
                        "category_explanations"
                    ),
                    "disclaimers_he": computed_result.get("disclaimers_he"),
                }
            )
        if ai_payload is None and isinstance(computed_result.get("ai"), dict):
            ai_payload = computed_result.get("ai")
    if isinstance(ai_payload, dict):
        stage_b = ai_payload.get("stage_b")
        if isinstance(stage_b, dict):
            if isinstance(stage_b.get("decision_result"), dict):
                candidates.append(
                    convert_decision_result_to_narrative(
                        {
                            "decision_result": sanitize_decision_result(
                                stage_b.get("decision_result"),
                                {},
                                computed_result or {},
                                None,
                            )
                        },
                        {},
                    )
                )
            raw_categories = stage_b.get("categories")
            if not isinstance(raw_categories, list):
                raw_categories = stage_b.get("category_explanations")
            normalized_categories = [
                item
                for item in (
                    _normalize_stage_b_category_for_narrative(category)
                    for category in (raw_categories or [])
                )
                if item
            ]
            candidates.append(
                {
                    "overall_summary": (
                        stage_b.get("narrative")
                        or stage_b.get("summary")
                        or stage_b.get("overall_summary")
                        or ""
                    ),
                    "category_explanations": normalized_categories,
                    "disclaimers_he": stage_b.get("disclaimers_he")
                    or stage_b.get("caveats")
                    or [],
                }
            )
    for candidate in candidates:
        sanitized = sanitize_comparison_narrative(candidate)
        if _has_usable_comparison_narrative(sanitized):
            return sanitized
    return None


def build_stored_comparison_ai_payload(
    computed_result: Optional[Dict[str, Any]],
    narrative: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    raw_ai = (
        computed_result.get("ai")
        if isinstance(computed_result, dict)
        and isinstance(computed_result.get("ai"), dict)
        else None
    )
    ai_payload = build_ai_payload(
        computed_result if isinstance(computed_result, dict) else {},
        narrative,
        (raw_ai or {}).get("status") or ("ok" if narrative else "fallback"),
        (raw_ai or {}).get("reason")
        if raw_ai
        else (None if narrative else "stage_b_error"),
    )
    if raw_ai and raw_ai.get("error"):
        ai_payload["error"] = raw_ai["error"]
    return ai_payload


def build_ai_payload(
    computed_result: Dict[str, Any],
    narrative: Optional[Dict[str, Any]],
    status: str,
    reason: Optional[str],
) -> Dict[str, Any]:
    stage_b_payload = _build_stage_b_payload(narrative) or {}
    decision_result = (
        computed_result.get("decision_result")
        if isinstance(computed_result.get("decision_result"), dict)
        else None
    )
    if decision_result:
        stage_b_payload["decision_result"] = decision_result
    return {
        "status": status,
        "reason": reason,
        "stage_a": _build_stage_a_summary(computed_result),
        "stage_b": stage_b_payload or None,
    }


def convert_writer_response_to_narrative(
    validated_payload: Dict[str, Any], cars_selected_slots: Dict
) -> Dict[str, Any]:
    car_keys = _ordered_compare_slot_keys(cars_selected_slots)

    category_explanations = []
    for cat in validated_payload.get("categories", []):
        explanations = {}
        source_explanations = (
            cat.get("explanations") if isinstance(cat.get("explanations"), dict) else {}
        )
        for car_key in car_keys:
            explanations[car_key] = source_explanations.get(car_key) or cat.get(
                "why", ""
            )
        category_explanations.append(
            {
                "category_key": cat.get("name"),
                "title_he": "",
                "winner": _normalize_compare_writer_winner(cat.get("winner"), car_keys)
                or "tie",
                "explanations": explanations,
                "why_it_scored_that_way": cat.get("tips", []),
            }
        )
    return {
        "overall_summary": validated_payload.get("summary", ""),
        "category_explanations": category_explanations,
        "disclaimers_he": validated_payload.get("caveats", []),
    }


def convert_decision_result_to_narrative(
    validated_payload: Dict[str, Any], cars_selected_slots: Dict
) -> Dict[str, Any]:
    decision_result = (
        validated_payload.get("decision_result")
        if isinstance(validated_payload, dict)
        else {}
    )
    if not isinstance(decision_result, dict):
        return build_deterministic_fallback_narrative(cars_selected_slots, {})

    car_keys = _ordered_compare_slot_keys(
        cars_selected_slots,
        _extract_decision_slot_keys(decision_result),
    )
    category_explanations = []
    disclaimers: List[str] = []
    for cat in (
        decision_result.get("category_decisions")
        if isinstance(decision_result.get("category_decisions"), list)
        else []
    ):
        if not isinstance(cat, dict):
            continue
        explanations = {}
        preferred = _decision_label(cat.get("preferred"), car_keys)
        for car_key in car_keys:
            choose_items = (
                decision_result.get(f"choose_{car_key}_if")
                if isinstance(decision_result.get(f"choose_{car_key}_if"), list)
                else []
            )
            avoid_items = (
                decision_result.get(f"avoid_or_check_{car_key}_if")
                if isinstance(decision_result.get(f"avoid_or_check_{car_key}_if"), list)
                else []
            )
            if preferred == car_key and choose_items:
                explanations[car_key] = str(choose_items[0]).strip()
            elif avoid_items:
                explanations[car_key] = str(avoid_items[0]).strip()
            elif str(cat.get("why") or "").strip():
                explanations[car_key] = str(cat.get("why") or "").strip()
        caveat = str(cat.get("important_caveat") or "").strip()
        if caveat:
            _append_unique_text(disclaimers, caveat, max_items=3)
        why_list = [
            text for text in (str(cat.get("why") or "").strip(), caveat) if text
        ]
        category_explanations.append(
            {
                "category_key": cat.get("category_key") or "",
                "title_he": cat.get("category_name_he") or "",
                "winner": preferred,
                "explanations": explanations,
                "why_it_scored_that_way": why_list[:2],
            }
        )
    overall = (
        decision_result.get("overall_decision")
        if isinstance(decision_result.get("overall_decision"), dict)
        else {}
    )
    summary = (
        str(decision_result.get("practical_summary") or "").strip()
        or str(overall.get("text") or "").strip()
        or DECISION_TEXT_FALLBACK_HE
    )
    return {
        "overall_summary": summary,
        "category_explanations": category_explanations,
        "disclaimers_he": disclaimers,
    }


def _safe_ai_response_snippet(exc: Exception, max_len: int = 280) -> str:
    """Extract a short, safe response snippet from provider exceptions."""
    response = getattr(exc, "response", None)
    if response is None:
        return ""
    text = ""
    try:
        text = getattr(response, "text", "") or ""
        if not text:
            content = getattr(response, "content", b"")
            if isinstance(content, bytes):
                text = content.decode("utf-8", errors="ignore")
            elif content is not None:
                text = str(content)
    except Exception:
        text = ""
    text = " ".join(str(text).split())
    return text[:max_len]


def _log_ai_client_error(
    feature: str,
    exc: Exception,
    request_id: Optional[str] = None,
    log: Optional[logging.Logger] = None,
) -> None:
    """Log enriched client error details (status/message/snippet) for diagnosis."""
    status_code = (
        getattr(exc, "status_code", None)
        or getattr(exc, "code", None)
        or getattr(getattr(exc, "response", None), "status_code", None)
    )
    message = str(exc)
    reason = "output_too_long" if _is_output_too_long_error(message) else "client_error"
    _inc_compare_metric("compare_ai_failures_total", reason=reason)
    (log or logger).error(
        "[AI] request_id=%s feature=%s model=%s error_code=%s reason=%s error_type=%s response_snippet=%s",
        request_id or "unknown",
        feature,
        COMPARISON_MODEL_ID,
        status_code,
        reason,
        type(exc).__name__,
        _safe_ai_response_snippet(exc),
    )


def call_gemini_comparison(
    prompt: str, timeout_sec: int = COMPARE_STAGE_A_TIMEOUT_SEC
) -> Tuple[Optional[Dict], Optional[str]]:
    """
    Call Gemini 3 Flash with web grounding for comparison data.
    Returns (parsed_output, error_string).
    """
    import concurrent.futures
    from app.factory import AI_EXECUTOR, AI_EXECUTOR_WORKERS

    start_time = pytime.perf_counter()
    prompt_chars = len(prompt or "")
    outcome = "ok"
    outcome_reason = None
    try:
        if extensions.ai_client is None:
            outcome = "error"
            outcome_reason = "CLIENT_NOT_INITIALIZED"
            return None, "CLIENT_NOT_INITIALIZED"

        config_kwargs = {
            "temperature": COMPARE_STAGE_A_TEMPERATURE,
            "top_p": 0.8,
            "top_k": 20,
            "max_output_tokens": COMPARE_STAGE_A_MAX_OUTPUT_TOKENS,
            "response_mime_type": "application/json",
        }
        config = genai_types.GenerateContentConfig(**config_kwargs)

        def _invoke():
            return extensions.ai_client.models.generate_content(
                model=COMPARISON_MODEL_ID,
                contents=prompt,
                config=config,
            )

        # Check executor availability
        work_queue = getattr(AI_EXECUTOR, "_work_queue", None)
        if work_queue is not None:
            queued = work_queue.qsize()
            if queued >= AI_EXECUTOR_WORKERS:
                outcome = "error"
                outcome_reason = "SERVER_BUSY"
                return None, "SERVER_BUSY"

        try:
            future = AI_EXECUTOR.submit(_invoke)
        except Exception:
            outcome = "error"
            outcome_reason = "EXECUTOR_SATURATED"
            return None, "EXECUTOR_SATURATED"

        try:
            resp = future.result(timeout=timeout_sec)
        except concurrent.futures.TimeoutError:
            future.cancel()
            _inc_compare_metric("compare_ai_failures_total", reason="timeout")
            _inc_compare_metric("compare_stage_a_timeout_total")
            outcome = "timeout"
            outcome_reason = "CALL_TIMEOUT"
            return None, "CALL_TIMEOUT"
        except Exception as e:
            _log_ai_client_error("comparison_stage_a", e)
            _inc_compare_metric("compare_stage_a_error_total")
            outcome = "error"
            outcome_reason = type(e).__name__
            if _is_output_too_long_error(str(e)):
                return None, "CALL_FAILED_OUTPUT_TOO_LONG"
            return None, f"CALL_FAILED:{type(e).__name__}"

        if resp is None:
            outcome = "error"
            outcome_reason = "CALL_FAILED_EMPTY"
            return None, "CALL_FAILED:EMPTY"

        text = (getattr(resp, "text", "") or "").strip()
        if not text:
            outcome = "error"
            outcome_reason = "EMPTY_RESPONSE"
            return None, "EMPTY_RESPONSE"

        parsed, parse_error = parse_stage_a_json(text)
        if parse_error:
            outcome = "error"
            outcome_reason = parse_error
            _inc_compare_metric("compare_stage_a_json_invalid_total")
            return None, parse_error
        return parsed, None

    finally:
        duration_ms = (pytime.perf_counter() - start_time) * 1000
        current_app.logger.info(
            "[AI] feature=comparison_stage_a model=%s duration_ms=%.2f prompt_chars=%s prompt_tokens_est=%s max_output_tokens=%s timeout_ms=%s tools_enabled=%s retry_count=%s outcome=%s reason=%s",
            COMPARISON_MODEL_ID,
            duration_ms,
            prompt_chars,
            _estimate_token_count(prompt),
            COMPARE_STAGE_A_MAX_OUTPUT_TOKENS,
            int(timeout_sec * 1000),
            False,
            0,
            outcome,
            outcome_reason,
        )


def call_gemini_single_car(
    prompt: str,
    car_label: str,
    timeout_sec: int = COMPARE_STAGE_A_TIMEOUT_SEC,
    request_id: Optional[str] = None,
    log: Optional[logging.Logger] = None,
) -> Tuple[Optional[Dict], Optional[str]]:
    """Call Gemini for a single car. Returns (parsed_dict, error_string)."""
    import concurrent.futures
    from app.factory import AI_EXECUTOR, AI_EXECUTOR_WORKERS

    start_time = pytime.perf_counter()
    prompt_chars = len(prompt or "")
    outcome = "ok"
    outcome_reason = None
    worker_logger = log or logger
    try:
        if extensions.ai_client is None:
            outcome = "error"
            outcome_reason = "CLIENT_NOT_INITIALIZED"
            return None, "CLIENT_NOT_INITIALIZED"

        config_kwargs = {
            "temperature": COMPARE_STAGE_A_TEMPERATURE,
            "top_p": 0.8,
            "top_k": 20,
            "max_output_tokens": COMPARE_STAGE_A_MAX_OUTPUT_TOKENS,
            "response_mime_type": "application/json",
        }
        config = genai_types.GenerateContentConfig(**config_kwargs)

        def _invoke():
            return extensions.ai_client.models.generate_content(
                model=COMPARISON_MODEL_ID,
                contents=prompt,
                config=config,
            )

        work_queue = getattr(AI_EXECUTOR, "_work_queue", None)
        if work_queue is not None:
            queued = work_queue.qsize()
            if queued >= AI_EXECUTOR_WORKERS:
                outcome = "error"
                outcome_reason = "SERVER_BUSY"
                return None, "SERVER_BUSY"

        try:
            future = AI_EXECUTOR.submit(_invoke)
        except Exception:
            outcome = "error"
            outcome_reason = "EXECUTOR_SATURATED"
            return None, "EXECUTOR_SATURATED"

        try:
            resp = future.result(timeout=timeout_sec)
        except concurrent.futures.TimeoutError:
            future.cancel()
            _inc_compare_metric("compare_ai_failures_total", reason="timeout")
            _inc_compare_metric("compare_stage_a_timeout_total")
            outcome = "timeout"
            outcome_reason = "CALL_TIMEOUT"
            return None, "CALL_TIMEOUT"
        except Exception as e:
            _log_ai_client_error(
                "comparison_stage_a", e, request_id=request_id, log=worker_logger
            )
            _inc_compare_metric("compare_stage_a_error_total")
            outcome = "error"
            outcome_reason = type(e).__name__
            if _is_output_too_long_error(str(e)):
                return None, "CALL_FAILED_OUTPUT_TOO_LONG"
            return None, f"CALL_FAILED:{type(e).__name__}"

        if resp is None:
            outcome = "error"
            outcome_reason = "CALL_FAILED_EMPTY"
            return None, "CALL_FAILED:EMPTY"

        text = (getattr(resp, "text", "") or "").strip()
        if not text:
            outcome = "error"
            outcome_reason = "EMPTY_RESPONSE"
            return None, "EMPTY_RESPONSE"

        parsed, parse_error = parse_single_car_json(text)
        if parse_error:
            outcome = "error"
            outcome_reason = parse_error
            _inc_compare_metric("compare_stage_a_json_invalid_total")
            return None, parse_error
        return parsed, None

    finally:
        duration_ms = (pytime.perf_counter() - start_time) * 1000
        worker_logger.info(
            "[AI] feature=comparison_stage_a_per_car model=%s car=%s duration_ms=%.2f prompt_chars=%s prompt_tokens_est=%s max_output_tokens=%s timeout_ms=%s outcome=%s reason=%s",
            COMPARISON_MODEL_ID,
            car_label,
            duration_ms,
            prompt_chars,
            _estimate_token_count(prompt),
            COMPARE_STAGE_A_MAX_OUTPUT_TOKENS,
            int(timeout_sec * 1000),
            outcome,
            outcome_reason,
        )


def call_stage_a_parallel(
    validated_cars: List[Dict], cars_selected_slots: Dict
) -> Tuple[Dict, Dict, List[str]]:
    """
    Run Stage A for each car in parallel.
    Returns (merged_model_output, sources_index, errors_list).
    """
    import concurrent.futures
    from app.factory import AI_EXECUTOR

    slot_keys = list(cars_selected_slots.keys())
    prompts = {}
    for i, car in enumerate(validated_cars):
        slot_key = slot_keys[i]
        prompts[slot_key] = build_single_car_prompt(car)

    def _retry_prompt_for(slot_key: str) -> str:
        base_prompt = prompts.get(slot_key, "")
        return (
            f"{base_prompt}\n\n"
            "FINAL JSON REMINDER:\n"
            "- Return EXACTLY one JSON object.\n"
            "- The response must start with { and end with }.\n"
            "- Do not wrap the object in an array.\n"
            "- If data is missing, keep the key and use null instead of omitting it.\n"
        )

    def _store_slot_result(slot_key: str, result: Optional[Dict[str, Any]]) -> bool:
        normalized_result = normalize_single_car_payload(
            result,
            fallback_name=(cars_selected_slots.get(slot_key, {}) or {}).get(
                "display_name"
            ),
        )
        if normalized_result is None:
            return False
        car_sources = normalized_result.get("sources", [])
        merged["cars"][slot_key] = normalized_result
        merged["sources"].extend(car_sources)
        return True

    futures = {}
    request_id = get_request_id()
    stage_a_logger = current_app.logger
    for slot_key, prompt in prompts.items():
        futures[slot_key] = AI_EXECUTOR.submit(
            call_gemini_single_car,
            prompt,
            slot_key,
            COMPARE_STAGE_A_TIMEOUT_SEC,
            request_id,
            stage_a_logger,
        )

    merged = _empty_stage_a_output(cars_selected_slots)
    errors = []
    retry_slots = {}
    for slot_key, future in futures.items():
        try:
            result, error = future.result(
                timeout=COMPARE_STAGE_A_TIMEOUT_SEC + PARALLEL_GRACE_SEC
            )
            if error:
                if error == "MODEL_JSON_INVALID":
                    retry_slots[slot_key] = _retry_prompt_for(slot_key)
                else:
                    errors.append(f"{slot_key}: {error}")
                    stage_a_logger.warning(
                        "[COMPARISON] stage_a_slot_failed request_id=%s slot_key=%s error_class=StageAError error=%s",
                        request_id,
                        slot_key,
                        _truncate_error_message(error),
                    )
            else:
                if not _store_slot_result(slot_key, result):
                    retry_slots[slot_key] = _retry_prompt_for(slot_key)
        except concurrent.futures.TimeoutError as e:
            future.cancel()
            errors.append(f"{slot_key}: CALL_TIMEOUT")
            stage_a_logger.warning(
                "[COMPARISON] stage_a_slot_failed request_id=%s slot_key=%s error_class=TimeoutError error=%s",
                request_id,
                slot_key,
                _truncate_error_message(e),
            )
        except concurrent.futures.CancelledError as e:
            errors.append(f"{slot_key}: CALL_CANCELLED")
            stage_a_logger.warning(
                "[COMPARISON] stage_a_slot_failed request_id=%s slot_key=%s error_class=CancelledError error=%s",
                request_id,
                slot_key,
                _truncate_error_message(e),
            )
        except Exception as e:
            errors.append(f"{slot_key}: CALL_FAILED:{type(e).__name__}")
            stage_a_logger.error(
                "[COMPARISON] stage_a_slot_failed request_id=%s slot_key=%s error_class=%s error=%s",
                request_id,
                slot_key,
                type(e).__name__,
                _truncate_error_message(e),
            )

    if retry_slots:
        stage_a_logger.info(
            "[COMPARISON] stage_a_retrying_json_invalid request_id=%s slot_keys=%s",
            request_id,
            sorted(retry_slots.keys()),
        )
        retry_futures = {}
        for slot_key, prompt in retry_slots.items():
            retry_futures[slot_key] = AI_EXECUTOR.submit(
                call_gemini_single_car,
                prompt,
                slot_key,
                COMPARE_STAGE_A_TIMEOUT_SEC,
                request_id,
                stage_a_logger,
            )
        for slot_key, future in retry_futures.items():
            try:
                result, error = future.result(
                    timeout=COMPARE_STAGE_A_TIMEOUT_SEC + PARALLEL_GRACE_SEC
                )
                if error or not _store_slot_result(slot_key, result):
                    final_error = error or "MODEL_JSON_INVALID"
                    errors.append(f"{slot_key}: {final_error}")
                    stage_a_logger.warning(
                        "[COMPARISON] stage_a_slot_failed request_id=%s slot_key=%s error_class=StageAError error=%s retry=1",
                        request_id,
                        slot_key,
                        _truncate_error_message(final_error),
                    )
            except concurrent.futures.TimeoutError as e:
                future.cancel()
                errors.append(f"{slot_key}: CALL_TIMEOUT")
                stage_a_logger.warning(
                    "[COMPARISON] stage_a_slot_failed request_id=%s slot_key=%s error_class=TimeoutError error=%s retry=1",
                    request_id,
                    slot_key,
                    _truncate_error_message(e),
                )
            except concurrent.futures.CancelledError as e:
                errors.append(f"{slot_key}: CALL_CANCELLED")
                stage_a_logger.warning(
                    "[COMPARISON] stage_a_slot_failed request_id=%s slot_key=%s error_class=CancelledError error=%s retry=1",
                    request_id,
                    slot_key,
                    _truncate_error_message(e),
                )
            except Exception as e:
                errors.append(f"{slot_key}: CALL_FAILED:{type(e).__name__}")
                stage_a_logger.error(
                    "[COMPARISON] stage_a_slot_failed request_id=%s slot_key=%s error_class=%s error=%s retry=1",
                    request_id,
                    slot_key,
                    type(e).__name__,
                    _truncate_error_message(e),
                )

    deduped_sources = list(dict.fromkeys(merged.get("sources", [])))
    source_limit = _MAX_STAGE_A_SOURCES * max(1, len(slot_keys))
    merged["sources"] = deduped_sources[:source_limit]
    return merged, build_sources_index_from_flat(merged), errors


def call_gemini_compare_writer(
    prompt: str, timeout_sec: int = COMPARE_WRITER_TIMEOUT_SEC
) -> Tuple[Optional[Dict], Optional[str]]:
    """Call Gemini Stage B writer WITHOUT grounding tools."""
    import concurrent.futures
    from app.factory import AI_EXECUTOR

    start_time = pytime.perf_counter()
    prompt_chars = len(prompt or "")
    is_retry_summary_only = "RETRY_MODE_SUMMARY_ONLY" in (prompt or "")
    max_output_tokens = (
        COMPARE_WRITER_RETRY_MAX_OUTPUT_TOKENS
        if is_retry_summary_only
        else COMPARE_WRITER_MAX_OUTPUT_TOKENS
    )
    outcome = "error"
    outcome_reason = None
    _inc_compare_metric("compare_ai_calls_total")

    try:
        if extensions.ai_client is None:
            outcome_reason = "CLIENT_NOT_INITIALIZED"
            return None, "CLIENT_NOT_INITIALIZED"

        config = genai_types.GenerateContentConfig(
            temperature=0.3,
            # Keep sampling conservative to reduce verbosity drift and long responses.
            top_p=0.8,
            top_k=20,
            max_output_tokens=max_output_tokens,
            response_mime_type="application/json",
        )

        def _invoke():
            return extensions.ai_client.models.generate_content(
                model=COMPARISON_MODEL_ID,
                contents=prompt,
                config=config,
            )

        try:
            future = AI_EXECUTOR.submit(_invoke)
            resp = future.result(timeout=timeout_sec)
        except concurrent.futures.TimeoutError:
            future.cancel()
            _inc_compare_metric("compare_ai_failures_total", reason="timeout")
            outcome = "timeout"
            outcome_reason = "CALL_TIMEOUT"
            return None, "CALL_TIMEOUT"
        except Exception as e:
            _log_ai_client_error("comparison_stage_b", e)
            _inc_compare_metric("compare_stage_b_error_total")
            outcome_reason = type(e).__name__
            if _is_output_too_long_error(str(e)):
                return None, "CALL_FAILED_OUTPUT_TOO_LONG"
            return None, f"CALL_FAILED:{type(e).__name__}"

        text = (getattr(resp, "text", "") or "").strip()
        if not text:
            outcome_reason = "EMPTY_RESPONSE"
            return None, "EMPTY_RESPONSE"
        COMPARE_AI_METRICS["compare_ai_output_tokens_estimate"] = _estimate_token_count(
            text
        )

        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                outcome = "ok"
                return parsed, None
        except json.JSONDecodeError:
            try:
                from json_repair import repair_json

                repaired = repair_json(text)
                parsed = json.loads(repaired)
                if isinstance(parsed, dict):
                    outcome = "ok"
                    return parsed, None
            except Exception:
                pass

        outcome_reason = "MODEL_JSON_INVALID"
        return None, "MODEL_JSON_INVALID"
    finally:
        duration_ms = (pytime.perf_counter() - start_time) * 1000
        current_app.logger.info(
            "[AI] feature=comparison_stage_b model=%s duration_ms=%.2f max_output_tokens=%s prompt_chars=%s prompt_tokens_est=%s timeout_ms=%s tools_enabled=%s retry_count=%s outcome=%s reason=%s",
            COMPARISON_MODEL_ID,
            duration_ms,
            max_output_tokens,
            prompt_chars,
            _estimate_token_count(prompt),
            int(timeout_sec * 1000),
            False,
            0,
            outcome,
            outcome_reason,
        )


def _truncate_log_payload(value: Any, limit: int = 300) -> str:
    try:
        raw = json.dumps(value, ensure_ascii=False)
    except Exception:
        raw = str(value)
    raw = " ".join(raw.split())
    return raw[:limit]


def _attempt_schema_repair(payload: Any, request_id: str) -> Optional[Dict[str, Any]]:
    repair_prompt = (
        "Return EXACTLY one JSON object with keys grounding_successful, assumptions, search_queries_used, cars. "
        "Do not return arrays at top-level and do not add markdown. "
        f"Normalize this payload into that object schema:\n{json.dumps(payload, ensure_ascii=False)}"
    )
    repaired, repair_error = call_gemini_compare_writer(repair_prompt, timeout_sec=25)
    if repair_error or not isinstance(repaired, dict):
        current_app.logger.warning(
            "[AI_SCHEMA] schema_repair_failed request_id=%s error=%s payload_sample=%s",
            request_id,
            repair_error,
            _truncate_log_payload(payload),
        )
        return None
    return repaired


def generate_narrative(
    cars_selected_slots: Dict, computed_result: Dict, timeout_sec: int = 60
) -> Optional[Dict]:
    """
    Generate short human-friendly explanations using Gemini Flash WITHOUT grounding.
    Input: only computed scores and display names (no new data retrieval).
    Returns strict JSON narrative or None on failure.
    """
    import concurrent.futures
    from app.factory import AI_EXECUTOR

    try:
        if extensions.ai_client is None:
            current_app.logger.warning("[NARRATIVE] AI client not initialized")
            return None

        # Build input context from computed results only
        car_summaries = {}
        for slot_key, slot_data in cars_selected_slots.items():
            car_computed = computed_result.get("cars", {}).get(slot_key, {})
            car_summaries[slot_key] = {
                "display_name": slot_data.get("display_name", slot_key),
                "overall_score": car_computed.get("overall_score"),
                "categories": {},
            }
            for cat_name, cat_data in car_computed.get("categories", {}).items():
                car_summaries[slot_key]["categories"][cat_name] = cat_data.get("score")

        category_winners = computed_result.get("category_winners", {})
        overall_winner = computed_result.get("overall_winner")
        top_reasons = computed_result.get("top_reasons", [])

        cat_names_he = {
            "reliability_risk": "אמינות וסיכונים",
            "ownership_cost": "עלות אחזקה",
            "practicality_comfort": "נוחות ופרקטיות",
            "driving_performance": "ביצועים ונהיגה",
        }

        slot_keys = list(cars_selected_slots.keys())
        car_explanations_template = ", ".join(
            f'"{k}": "string (1-2 sentences)"' for k in slot_keys
        )

        prompt = f"""You are a car comparison summary writer. Write SHORT, friendly, user-facing explanations in Hebrew.

INPUT DATA (already computed, DO NOT add new facts):
{json.dumps(car_summaries, ensure_ascii=False, indent=2)}

Category winners: {json.dumps(category_winners, ensure_ascii=False)}
Overall winner: {json.dumps(overall_winner, ensure_ascii=False)}
Top reasons: {json.dumps(top_reasons, ensure_ascii=False)}

RULES:
1. Do NOT add new factual claims or data not present in the input.
2. Do NOT introduce new sources or URLs.
3. Explain ONLY the scores and winners given above.
4. Use simple, friendly Hebrew. Fewer numbers, more human language.
5. When scores are very close (within {TIE_THRESHOLD} points), say "צמוד" (close race).
6. Return ONLY valid JSON. No markdown, no extra text.

Return this EXACT JSON structure:
{{{{
  "overall_summary": "string (2-4 sentences summarizing the comparison)",
  "category_explanations": [
    {{{{
      "category_key": "reliability_risk",
      "title_he": "{cat_names_he.get("reliability_risk", "")}",
      "winner": "car_1|car_2|car_3|tie",
      "explanations": {{{{ {car_explanations_template} }}}},
      "why_it_scored_that_way": ["string", "string"]
    }}}},
    {{{{
      "category_key": "ownership_cost",
      "title_he": "{cat_names_he.get("ownership_cost", "")}",
      "winner": "car_1|car_2|car_3|tie",
      "explanations": {{{{ {car_explanations_template} }}}},
      "why_it_scored_that_way": ["string", "string"]
    }}}},
    {{{{
      "category_key": "practicality_comfort",
      "title_he": "{cat_names_he.get("practicality_comfort", "")}",
      "winner": "car_1|car_2|car_3|tie",
      "explanations": {{{{ {car_explanations_template} }}}},
      "why_it_scored_that_way": ["string", "string"]
    }}}},
    {{{{
      "category_key": "driving_performance",
      "title_he": "{cat_names_he.get("driving_performance", "")}",
      "winner": "car_1|car_2|car_3|tie",
      "explanations": {{{{ {car_explanations_template} }}}},
      "why_it_scored_that_way": ["string", "string"]
    }}}}
  ],
  "disclaimers_he": ["הניקוד מבוסס על נתונים שנאספו מהאינטרנט ועשוי להשתנות", "מומלץ לבצע בדיקה מקצועית לפני רכישה"]
}}}}
"""

        config = genai_types.GenerateContentConfig(
            temperature=0.4,
            top_p=0.9,
            top_k=40,
            response_mime_type="application/json",
        )

        def _invoke():
            return extensions.ai_client.models.generate_content(
                model=COMPARISON_MODEL_ID,
                contents=prompt,
                config=config,
            )

        try:
            future = AI_EXECUTOR.submit(_invoke)
        except Exception:
            current_app.logger.warning(
                "[NARRATIVE] Executor saturated, skipping narrative"
            )
            return None

        try:
            resp = future.result(timeout=timeout_sec)
        except concurrent.futures.TimeoutError:
            future.cancel()
            current_app.logger.warning("[NARRATIVE] Timeout generating narrative")
            return None
        except Exception as e:
            current_app.logger.warning(f"[NARRATIVE] Call failed: {type(e).__name__}")
            return None

        if resp is None:
            return None

        text = (getattr(resp, "text", "") or "").strip()
        if not text:
            return None

        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            try:
                from json_repair import repair_json

                repaired = repair_json(text)
                parsed = json.loads(repaired)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass

        current_app.logger.warning("[NARRATIVE] Failed to parse narrative response")
        return None

    except Exception as e:
        current_app.logger.warning(f"[NARRATIVE] Unexpected error: {e}")
        return None


def normalize_model_output(
    parsed: Any, request_id: str
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Normalize parsed JSON into a dict.
    Handles the case where Gemini returns a JSON array (list) instead of a dict.

    Args:
        parsed: The parsed JSON output from the model (can be dict, list, or other)
        request_id: Request ID for logging purposes

    Returns:
        Tuple of (normalized_dict, error_code) - error_code is None if successful
    """
    if parsed is None:
        return None, "MODEL_SHAPE_INVALID"

    # If already a dict, return as-is
    if isinstance(parsed, dict):
        return parsed, None

    # If it's a list, try to extract the dict
    if isinstance(parsed, list):
        if len(parsed) == 1 and isinstance(parsed[0], dict):
            candidate = parsed[0]
            needs_repair = not (
                isinstance(candidate.get("cars"), dict)
                and "grounding_successful" in candidate
            )
            repaired = (
                _attempt_schema_repair(candidate, request_id) if needs_repair else None
            )
            current_app.logger.warning(
                "[AI_SCHEMA] list_single_dict_normalized request_id=%s repaired=%s payload_sample=%s",
                request_id,
                bool(repaired),
                _truncate_log_payload(parsed[0]),
            )
            return repaired or parsed[0], None
        else:
            # List with multiple elements or non-dict elements
            current_app.logger.error(
                "[AI_SCHEMA] invalid_list_shape len=%d request_id=%s payload_sample=%s",
                len(parsed),
                request_id,
                _truncate_log_payload(parsed),
            )
            return None, "MODEL_SHAPE_INVALID"

    # Any other type is invalid
    current_app.logger.error(
        "[AI_SCHEMA] unexpected_type=%s request_id=%s payload_sample=%s",
        type(parsed).__name__,
        request_id,
        _truncate_log_payload(parsed),
    )
    return None, "MODEL_SHAPE_INVALID"


# ============================================================
# REQUEST HASH FOR CACHING
# ============================================================


def compute_request_hash(
    cars: List[Dict], buyer_profile: Optional[Dict[str, Any]] = None
) -> str:
    """
    Compute a hash for caching based on selected cars and prompt version.
    Uses 32 characters (128 bits) of SHA256 for adequate collision resistance.
    Includes year, engine_type, and gearbox in hash calculation.
    """
    car_keys = []
    for c in cars:
        # Consistent year extraction: prefer year, fallback to year_start
        year_val = c.get("year")
        if year_val is None:
            year_val = c.get("year_start")
        year_str = str(year_val) if year_val is not None else ""

        key_parts = [
            c.get("make", ""),
            c.get("model", ""),
            year_str,
            c.get("engine_type", ""),
            c.get("gearbox", ""),
        ]
        car_keys.append("|".join(key_parts))

    data = {
        "cars": sorted(car_keys),
        "buyer_profile": buyer_profile,
        "prompt_version": COMPARISON_PROMPT_VERSION,
    }
    data_str = json.dumps(data, sort_keys=True)
    return hashlib.sha256(data_str.encode()).hexdigest()[:32]  # 128 bits


# ============================================================
# VALIDATION
# ============================================================


def _sanitize_buyer_number(
    value: Any, minimum: float, maximum: float
) -> Optional[float]:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < minimum or number > maximum:
        return None
    return int(number) if number.is_integer() else number


def _sanitize_buyer_string(value: Any, max_len: int = 80) -> Optional[str]:
    if value in (None, ""):
        return None
    text = escape_prompt_input(str(value).strip(), max_length=max_len)
    return text or None


def _sanitize_buyer_list(value: Any, allowed: set, max_items: int = 6) -> List[str]:
    if not isinstance(value, list):
        return []
    out = []
    for item in value[:max_items]:
        text = _sanitize_buyer_string(item, 40)
        if text and text in allowed and text not in out:
            out.append(text)
    return out


def validate_buyer_profile(
    value: Any,
) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
    """Allowlist and normalize optional compare buyer profile."""
    if value in (None, ""):
        return True, None, None
    if not isinstance(value, dict):
        return False, "פרופיל משתמש לא תקין", None

    normalized: Dict[str, Any] = {}
    number_fields = {
        "budget_min": (0, 2_000_000),
        "budget_max": (0, 2_000_000),
        "annual_km": (0, 100_000),
        "driver_age": (17, 100),
    }
    for field, bounds in number_fields.items():
        num = _sanitize_buyer_number(value.get(field), *bounds)
        if num is not None:
            normalized[field] = num

    main_use = _sanitize_buyer_string(value.get("main_use"), 40)
    if main_use:
        normalized["main_use"] = (
            main_use if main_use in BUYER_PROFILE_MAIN_USE_ALLOWED else "unknown"
        )

    safety_required = _sanitize_buyer_string(value.get("safety_required"), 10)
    if safety_required in BUYER_PROFILE_SAFETY_ALLOWED:
        normalized["safety_required"] = safety_required

    for field in ("family_size", "cargo_need", "body_style", "driving_style"):
        text = _sanitize_buyer_string(value.get(field), 80)
        if text:
            normalized[field] = text

    fuels = _sanitize_buyer_list(
        value.get("preferred_fuels"), BUYER_PROFILE_FUELS_ALLOWED
    )
    gears = _sanitize_buyer_list(
        value.get("preferred_gears"), BUYER_PROFILE_GEARS_ALLOWED
    )
    if fuels:
        normalized["preferred_fuels"] = fuels
    if gears:
        normalized["preferred_gears"] = gears

    priority_weights = (
        value.get("priority_weights")
        if isinstance(value.get("priority_weights"), dict)
        else {}
    )
    cleaned_weights = {}
    for key in BUYER_PROFILE_PRIORITY_KEYS:
        raw = priority_weights.get(key)
        if raw in (None, "") or isinstance(raw, bool):
            continue
        try:
            number = float(raw)
        except (TypeError, ValueError):
            continue
        cleaned_weights[key] = max(
            BUYER_PROFILE_PRIORITY_MIN, min(BUYER_PROFILE_PRIORITY_MAX, number)
        )
    if cleaned_weights:
        normalized["priority_weights"] = cleaned_weights

    return True, None, normalized or None


def validate_comparison_request(data: Dict) -> Tuple[bool, Optional[str], List[Dict]]:
    """
    Validate comparison request data.
    Returns (is_valid, error_message, validated_cars).
    Accepts year, engine_type, and gearbox as explicit assumptions.
    """
    cars = data.get("cars")

    if not cars:
        return False, "לא נבחרו רכבים להשוואה", []

    if not isinstance(cars, list):
        return False, "פורמט רכבים לא תקין", []

    if len(cars) < 2:
        return False, "יש לבחור לפחות 2 רכבים להשוואה", []

    if len(cars) > 3:
        return False, "ניתן להשוות עד 3 רכבים בלבד", []

    validated_cars = []
    seen_keys = set()
    for i, car in enumerate(cars):
        if not isinstance(car, dict):
            return False, f"פורמט רכב {i + 1} לא תקין", []

        make = car.get("make", "").strip()
        model = car.get("model", "").strip()

        if not make or not model:
            return False, f"רכב {i + 1}: חובה לציין יצרן ודגם", []

        # Extract year (either single year or use year_start for fallback)
        year = car.get("year")
        if year:
            try:
                year = int(year)
            except (ValueError, TypeError):
                return False, f"רכב {i + 1}: שנתון לא תקין", []
        else:
            # Fallback to year_start for consistent hashing
            year_start = car.get("year_start")
            if year_start:
                try:
                    year = int(year_start)
                except (ValueError, TypeError):
                    year = None

        engine_type = car.get("engine_type", "").strip()
        gearbox = car.get("gearbox", "").strip()

        # Check for duplicates (same make, model, year, engine, gearbox)
        # Use empty string for None year to ensure consistent comparison
        year_key = str(year) if year is not None else ""
        car_key = f"{make}|{model}|{year_key}|{engine_type}|{gearbox}"
        if car_key in seen_keys:
            return False, "לא ניתן להשוות רכבים זהים. אנא בחר רכבים שונים.", []
        seen_keys.add(car_key)

        validated_car = {
            "make": make,
            "model": model,
        }
        if year:
            validated_car["year"] = year
        if engine_type:
            validated_car["engine_type"] = engine_type
        if gearbox:
            validated_car["gearbox"] = gearbox
        # Keep year_start/year_end for backward compatibility
        if car.get("year_start"):
            validated_car["year_start"] = car.get("year_start")
        if car.get("year_end"):
            validated_car["year_end"] = car.get("year_end")

        validated_cars.append(validated_car)

    return True, None, validated_cars


def validate_grounding(model_output: Dict) -> Tuple[bool, str]:
    """
    Check if grounding was successful based on model output.
    Returns (is_valid, failure_reason).

    Enforces:
    1. grounding_successful flag must be True
    2. Car data must be present in model output
    Note: Source validation has been removed — estimates without sources are acceptable.
    """
    if not model_output:
        return False, "Empty model output"

    # Check grounding flag
    grounding = model_output.get("grounding_successful", False)
    if not grounding:
        return False, "Model reported grounding_successful=false"

    # Check that we have car data
    cars = model_output.get("cars", {})
    if not cars:
        return False, "No car data in model output"

    return True, ""


def enforce_authoritative_numbers(
    server_computed: Dict, stage_b_output: Optional[Dict], request_id: str
) -> Dict:
    """
    Server deterministic scoring is authoritative.
    Stage B may echo computed_result, but any drift is ignored and logged.
    """
    if isinstance(stage_b_output, dict) and isinstance(
        stage_b_output.get("computed_result"), dict
    ):
        if stage_b_output.get("computed_result") != server_computed:
            current_app.logger.warning(
                "[COMPARISON] stage_b attempted numeric/schema drift request_id=%s",
                request_id,
            )
    return dict(server_computed)


# ============================================================
# SOURCES INDEX BUILDER
# ============================================================


def build_sources_index(model_output: Dict) -> Dict:
    """Build an index of all sources by car, category, and metric."""
    sources_index = {}
    cars = model_output.get("cars", {})

    for car_id, car_data in cars.items():
        sources_index[car_id] = {}
        for cat_name, cat_data in car_data.items():
            if not isinstance(cat_data, dict):
                continue
            sources_index[car_id][cat_name] = {}
            for metric_name, metric_data in cat_data.items():
                if not isinstance(metric_data, dict):
                    continue
                sources = metric_data.get("sources", [])
                sources_index[car_id][cat_name][metric_name] = sources

    return sources_index


def build_sources_index_from_flat(merged_output: Dict) -> Dict:
    """Build sources index from flat sources array."""
    return {"all_sources": merged_output.get("sources", [])}


# ============================================================
# MAIN HANDLER
# ============================================================


def handle_comparison_request(
    data: Dict,
    user_id: Optional[int],
    session_id: Optional[str],
    owner_bypass: bool = False,
) -> Any:
    """
    Handle a car comparison request.
    Returns Flask response.
    """
    logger = current_app.logger
    request_id = get_request_id()
    total_start = pytime.perf_counter()
    deterministic_ms = 0
    ai_ms = 0
    db_ms = 0

    # Validate request
    is_valid, error_msg, validated_cars = validate_comparison_request(data)
    if not is_valid:
        return api_error("validation_error", error_msg, status=400)
    buyer_valid, buyer_error, buyer_profile = validate_buyer_profile(
        data.get("buyer_profile")
    )
    if not buyer_valid:
        logger.warning("[COMPARISON] invalid_buyer_profile request_id=%s", request_id)
        return api_error("validation_error", buyer_error, status=400)

    # Map cars to stable slots with display_name
    cars_selected_slots = map_cars_to_slots(validated_cars)

    # Compute request hash for caching
    request_hash = compute_request_hash(validated_cars, buyer_profile)

    # Check cache (only for logged-in users)
    if user_id:
        cached = (
            ComparisonHistory.query.filter_by(
                user_id=user_id,
                request_hash=request_hash,
            )
            .order_by(ComparisonHistory.created_at.desc())
            .first()
        )

        if cached and cached.computed_result:
            logger.info(
                f"[COMPARISON] cache hit request_id={request_id} hash={request_hash}"
            )

            # Safely parse all cached JSON fields, handling double-encoded data
            cars_selected, cars_was_double = _safe_parse_json_cached(
                cached.cars_selected, "cars_selected"
            )
            computed_result, computed_was_double = _safe_parse_json_cached(
                cached.computed_result, "computed_result"
            )
            sources_index, sources_was_double = _safe_parse_json_cached(
                cached.sources_index, "sources_index"
            )
            model_output, model_was_double = _safe_parse_json_cached(
                cached.model_json_raw, "model_json_raw"
            )

            # Validate that required fields parsed to expected types
            cache_valid = (
                isinstance(cars_selected, list)
                and len(cars_selected) >= 2
                and isinstance(computed_result, dict)
            )

            if cache_valid:
                # Extract assumptions safely (only if model_output is a dict)
                assumptions = {}
                if isinstance(model_output, dict):
                    assumptions = model_output.get("assumptions", {})

                # Self-heal: if any field was double-encoded, update the DB to store normalized JSON
                # Note: cars_selected and computed_result are required (validated above),
                # while sources_index and model_json_raw are nullable - hence the extra null checks
                needs_heal = (
                    cars_was_double
                    or computed_was_double
                    or sources_was_double
                    or model_was_double
                )
                if needs_heal:
                    try:
                        if cars_was_double:
                            cached.cars_selected = json.dumps(
                                cars_selected, ensure_ascii=False
                            )
                        if computed_was_double:
                            cached.computed_result = json.dumps(
                                computed_result, ensure_ascii=False
                            )
                        if sources_was_double and sources_index is not None:
                            cached.sources_index = json.dumps(
                                sources_index, ensure_ascii=False
                            )
                        if model_was_double and model_output is not None:
                            cached.model_json_raw = json.dumps(
                                model_output, ensure_ascii=False
                            )
                        db.session.commit()
                        logger.info(
                            f"[COMPARISON] self-healed double-encoded cache row id={cached.id}"
                        )
                    except Exception as heal_err:
                        logger.warning(
                            f"[COMPARISON] self-heal commit failed: {heal_err}"
                        )
                        db.session.rollback()

                cached_slots = (
                    map_cars_to_slots(cars_selected)
                    if isinstance(cars_selected, list)
                    else {}
                )
                if not cached_slots:
                    cached_slots = cars_selected_slots

                narrative = resolve_comparison_narrative(
                    computed_result if isinstance(computed_result, dict) else None
                )
                checked_versions = build_checked_versions(
                    cached_slots if isinstance(cached_slots, dict) else {},
                    model_output if isinstance(model_output, dict) else {},
                    computed_result.get("checked_versions")
                    if isinstance(computed_result, dict)
                    else None,
                )
                decision_result = sanitize_decision_result(
                    computed_result.get("decision_result")
                    if isinstance(computed_result, dict)
                    else None,
                    cached_slots if isinstance(cached_slots, dict) else {},
                    computed_result if isinstance(computed_result, dict) else {},
                    request_id,
                )
                if isinstance(computed_result, dict) and (
                    computed_result.get("decision_result") != decision_result
                    or computed_result.get("checked_versions") != checked_versions
                ):
                    computed_result["decision_result"] = decision_result
                    computed_result["checked_versions"] = checked_versions
                    cached.computed_result = json.dumps(
                        computed_result, ensure_ascii=False
                    )
                    try:
                        db.session.commit()
                    except Exception as heal_err:
                        logger.warning(
                            f"[COMPARISON] decision_result cache heal failed for id={cached.id}: {heal_err}"
                        )
                        db.session.rollback()
                cached_guarded, _ = apply_feature_guardrails(
                    "vehicle_comparison",
                    {"cars": cars_selected if isinstance(cars_selected, list) else []},
                    {
                        "checked_versions": checked_versions,
                        "decision_result": decision_result,
                        "narrative": narrative,
                        "computed_result": computed_result,
                        "sources_index": sources_index if sources_index else {},
                        "ai": build_stored_comparison_ai_payload(
                            computed_result if isinstance(computed_result, dict) else None,
                            narrative,
                        ),
                    },
                )
                checked_versions = cached_guarded.get("checked_versions", checked_versions)
                decision_result = cached_guarded.get("decision_result", decision_result)
                narrative = cached_guarded.get("narrative", narrative)
                ai_payload = cached_guarded.get(
                    "ai",
                    build_stored_comparison_ai_payload(
                        computed_result if isinstance(computed_result, dict) else None,
                        narrative,
                    ),
                )
                return api_ok(
                    {
                        "cached": True,
                        "comparison_id": cached.id,
                        "cars_selected": cached_slots,
                        "cars_selected_list": cars_selected
                        if isinstance(cars_selected, list)
                        else [],
                        "model_output": model_output,
                        "computed_result": computed_result,
                        "narrative": narrative,
                        "decision_result": decision_result,
                        "checked_versions": checked_versions,
                        "sources_index": sources_index if sources_index else {},
                        "assumptions": assumptions,
                        "ai": ai_payload,
                        "visible_warning": cached_guarded.get("visible_warning"),
                        "central_differences": cached_guarded.get("central_differences"),
                        "guardrail_meta": cached_guarded.get("guardrail_meta", {}),
                    }
                )
            else:
                # Cache row is corrupted (cannot parse to expected types)
                # Delete the bad row so future requests don't hit it, then proceed with fresh call
                logger.warning(
                    f"[COMPARISON] cache row {cached.id} corrupted, deleting and recomputing"
                )
                try:
                    db.session.delete(cached)
                    db.session.commit()
                except Exception as del_err:
                    logger.warning(
                        f"[COMPARISON] failed to delete corrupted cache row: {del_err}"
                    )
                    db.session.rollback()

    # Stage A: parallel per-car Gemini calls
    stage_a_start = pytime.perf_counter()
    model_output, sources_index, stage_a_errors = call_stage_a_parallel(
        validated_cars, cars_selected_slots
    )
    duration_ms = int((pytime.perf_counter() - stage_a_start) * 1000)
    ai_ms += duration_ms
    stage_a_error_code = None
    stage_a_partial = False

    if len(stage_a_errors) == len(validated_cars):
        stage_a_error_code = _extract_stage_a_error_code(stage_a_errors)
        sanitized_errors = _sanitize_stage_a_errors(stage_a_errors)
        logger.warning(
            "[COMPARISON] stage_a_all_failed request_id=%s errors=%s",
            request_id,
            sanitized_errors,
        )
        total_ms = int((pytime.perf_counter() - total_start) * 1000)
        logger.info(
            "[COMPARE_TIMING] request_id=%s total_ms=%s deterministic_ms=%s ai_ms=%s db_ms=%s",
            request_id,
            total_ms,
            deterministic_ms,
            ai_ms,
            db_ms,
        )
        return api_error(
            "comparison_ai_unavailable",
            "שירות ההשוואה אינו זמין כרגע. נסה שוב בעוד רגע.",
            status=503,
            details={
                "stage": "stage_a",
                "request_id": request_id,
                "retryable": True,
                "error_code": stage_a_error_code,
                "errors": sanitized_errors,
            },
        )
    elif stage_a_errors:
        # Partial failure — log but continue with available data
        stage_a_partial = True
        logger.warning(
            "[COMPARISON] partial_stage_a request_id=%s errors=%s",
            request_id,
            _sanitize_stage_a_errors(stage_a_errors),
        )

    # Compute scores deterministically (server-side source of truth)
    scoring_start = pytime.perf_counter()
    server_computed_result = compute_comparison_results(model_output)
    deterministic_ms = int((pytime.perf_counter() - scoring_start) * 1000)

    # Stage B: non-grounded writer call (full schema + narrative around server results)
    stage_b_output = None
    stage_b_error = None
    narrative = None
    stage_b_reason = None
    validated_decision = None
    decision_validation_reason = None
    writer_prompt = build_compare_writer_prompt(
        cars_selected_slots, server_computed_result, model_output, buyer_profile
    )
    stage_b_start = pytime.perf_counter()
    stage_b_output, stage_b_error = call_gemini_compare_writer(writer_prompt)
    ai_ms += int((pytime.perf_counter() - stage_b_start) * 1000)
    logger.info(
        "[COMPARISON] stage_b payload request_id=%s partial_stage_a=%s payload_shape=%s",
        request_id,
        stage_a_partial,
        _summarize_compare_writer_payload(stage_b_output),
    )
    if isinstance(stage_b_output, dict):
        validated_decision, decision_validation_reason = (
            _validate_decision_writer_response(
                stage_b_output,
                cars_selected_slots,
                server_computed_result,
            )
        )
    if stage_b_error:
        logger.warning(
            f"[COMPARISON] stage_b call failed request_id={request_id} error={stage_b_error}"
        )
        stage_b_reason = "stage_b_error"
        retry_prompt = build_compare_writer_retry_prompt(
            cars_selected_slots, server_computed_result
        )
        retry_output, retry_error = call_gemini_compare_writer(retry_prompt)
        logger.info(
            "[COMPARISON] stage_b retry payload request_id=%s partial_stage_a=%s payload_shape=%s",
            request_id,
            stage_a_partial,
            _summarize_compare_writer_payload(retry_output),
        )
        if retry_error:
            logger.warning(
                f"[COMPARISON] stage_b retry failed request_id={request_id} error={retry_error}"
            )
            _inc_compare_metric("compare_ai_fallback_used_total")
            narrative = build_deterministic_fallback_narrative(
                cars_selected_slots, server_computed_result
            )
        else:
            validated_retry, retry_reason = _validate_compare_writer_response(
                retry_output
            )
            if validated_retry:
                narrative = sanitize_comparison_narrative(
                    convert_writer_response_to_narrative(
                        validated_retry, cars_selected_slots
                    )
                )
                stage_b_reason = None
                logger.info(
                    "[COMPARISON] stage_b retry accepted request_id=%s narrative_shape=%s",
                    request_id,
                    _summarize_comparison_narrative_shape(narrative),
                )
            else:
                raw_retry_narrative = (
                    retry_output.get("narrative")
                    if isinstance(retry_output, dict)
                    else None
                )
                salvaged_narrative = _salvage_partial_writer_output(
                    retry_output,
                    cars_selected_slots,
                    server_computed_result,
                )
                logger.warning(
                    "[COMPARISON] stage_b retry validation failed request_id=%s reason=%s payload_shape=%s",
                    request_id,
                    retry_reason,
                    _summarize_compare_writer_payload(retry_output),
                )
                if salvaged_narrative:
                    narrative = sanitize_comparison_narrative(salvaged_narrative)
                    stage_b_reason = None
                    logger.info(
                        "[COMPARISON] narrative salvaged from partial writer output request_id=%s narrative_shape=%s",
                        request_id,
                        _summarize_comparison_narrative_shape(narrative),
                    )
                elif raw_retry_narrative:
                    narrative = sanitize_comparison_narrative(raw_retry_narrative)
                    stage_b_reason = None
                    logger.info(
                        "[COMPARISON] stage_b retry legacy narrative used request_id=%s narrative_shape=%s",
                        request_id,
                        _summarize_comparison_narrative_shape(narrative),
                    )
                else:
                    _inc_compare_metric("compare_ai_fallback_used_total")
                    narrative = build_deterministic_fallback_narrative(
                        cars_selected_slots, server_computed_result
                    )
    elif isinstance(stage_b_output, dict):
        if validated_decision:
            narrative = sanitize_comparison_narrative(
                convert_decision_result_to_narrative(
                    validated_decision, cars_selected_slots
                )
            )
            logger.info(
                "[COMPARISON] narrative generated request_id=%s narrative_shape=%s",
                request_id,
                _summarize_comparison_narrative_shape(narrative),
            )
            stage_b_reason = None
        else:
            validated_writer, validation_reason = _validate_compare_writer_response(
                stage_b_output
            )
            if validated_writer:
                narrative = sanitize_comparison_narrative(
                    convert_writer_response_to_narrative(
                        validated_writer, cars_selected_slots
                    )
                )
                logger.info(
                    "[COMPARISON] legacy narrative generated request_id=%s narrative_shape=%s",
                    request_id,
                    _summarize_comparison_narrative_shape(narrative),
                )
                stage_b_reason = None
            else:
                raw_narrative = stage_b_output.get("narrative")
                salvaged_narrative = _salvage_partial_writer_output(
                    stage_b_output,
                    cars_selected_slots,
                    server_computed_result,
                )
                logger.warning(
                    "[COMPARISON] stage_b validation failed request_id=%s reason=%s payload_shape=%s",
                    request_id,
                    validation_reason,
                    _summarize_compare_writer_payload(stage_b_output),
                )
                if salvaged_narrative:
                    narrative = sanitize_comparison_narrative(salvaged_narrative)
                    logger.info(
                        "[COMPARISON] narrative salvaged from partial writer output request_id=%s narrative_shape=%s",
                        request_id,
                        _summarize_comparison_narrative_shape(narrative),
                    )
                    stage_b_reason = None
                elif raw_narrative:
                    narrative = sanitize_comparison_narrative(raw_narrative)
                    logger.info(
                        "[COMPARISON] narrative generated request_id=%s mode=legacy_deprecated narrative_shape=%s",
                        request_id,
                        _summarize_comparison_narrative_shape(narrative),
                    )
                    stage_b_reason = None
                else:
                    stage_b_reason = "stage_b_error"
                    _inc_compare_metric("compare_ai_fallback_used_total")
                    narrative = build_deterministic_fallback_narrative(
                        cars_selected_slots, server_computed_result
                    )

    if stage_a_partial:
        narrative = mark_partial_comparison_narrative(narrative)
        logger.info(
            "[COMPARISON] partial_stage_a fallback path used request_id=%s narrative_shape=%s",
            request_id,
            _summarize_comparison_narrative_shape(narrative),
        )

    computed_result = enforce_authoritative_numbers(
        server_computed_result, stage_b_output, request_id
    )
    if validated_decision:
        decision_result = validated_decision["decision_result"]
    else:
        if decision_validation_reason:
            logger.warning(
                "[COMPARISON] decision_result fallback request_id=%s reason=%s",
                request_id,
                decision_validation_reason,
            )
        decision_result = build_deterministic_decision_result(
            cars_selected_slots, computed_result, stage_b_output
        )
    checked_versions = build_checked_versions(
        cars_selected_slots,
        model_output,
        validated_decision.get("checked_versions") if validated_decision else None,
    )
    ai_status = "ok"
    ai_reason = None
    if stage_a_partial:
        ai_status = "partial_fallback"
        ai_reason = "stage_a_partial"
    elif stage_b_reason:
        ai_status = "fallback"
        ai_reason = stage_b_reason
    ai_payload = build_ai_payload(computed_result, narrative, ai_status, ai_reason)
    comparison_guarded, _ = apply_feature_guardrails(
        "vehicle_comparison",
        {"cars": validated_cars},
        {
            "checked_versions": checked_versions,
            "decision_result": decision_result,
            "narrative": narrative,
            "computed_result": computed_result,
            "sources_index": sources_index,
            "ai": ai_payload,
        },
    )
    checked_versions = comparison_guarded.get("checked_versions", checked_versions)
    decision_result = comparison_guarded.get("decision_result", decision_result)
    narrative = comparison_guarded.get("narrative", narrative)
    ai_payload = comparison_guarded.get("ai", ai_payload)
    visible_warning = comparison_guarded.get("visible_warning")
    central_differences = comparison_guarded.get("central_differences")
    logger.info(
        "[COMPARISON] response narrative request_id=%s ai_status=%s ai_reason=%s narrative_shape=%s",
        request_id,
        ai_status,
        ai_reason,
        _summarize_comparison_narrative_shape(narrative),
    )
    if stage_a_error_code:
        ai_payload["error"] = stage_a_error_code

    # Include narrative in computed_result for storage
    stored_computed = dict(computed_result)
    stored_computed["decision_result"] = decision_result
    stored_computed["checked_versions"] = checked_versions
    if visible_warning:
        stored_computed["visible_warning"] = visible_warning
    if central_differences:
        stored_computed["central_differences"] = central_differences
    if narrative:
        stored_computed["narrative"] = narrative
    stored_computed["ai"] = ai_payload
    stored_computed["guardrail_meta"] = comparison_guarded.get("guardrail_meta", {})

    # Save to database
    try:
        db_start = pytime.perf_counter()
        comparison_record = ComparisonHistory(
            created_at=_utcnow(),
            user_id=user_id,
            session_id=session_id,
            cars_selected=json.dumps(validated_cars, ensure_ascii=False),
            model_json_raw=json.dumps(model_output, ensure_ascii=False),
            computed_result=json.dumps(stored_computed, ensure_ascii=False),
            sources_index=json.dumps(sources_index, ensure_ascii=False),
            model_name=COMPARISON_MODEL_ID,
            grounding_enabled=True,
            prompt_version=COMPARISON_PROMPT_VERSION,
            request_hash=request_hash,
            duration_ms=duration_ms,
        )
        db.session.add(comparison_record)
        db.session.commit()
        comparison_id = comparison_record.id
        db_ms = int((pytime.perf_counter() - db_start) * 1000)
        logger.info(
            f"[COMPARISON] saved request_id={request_id} comparison_id={comparison_id}"
        )
    except Exception as e:
        logger.error(f"[COMPARISON] save failed request_id={request_id} error={e}")
        db.session.rollback()
        comparison_id = None
    finally:
        total_ms = int((pytime.perf_counter() - total_start) * 1000)
        logger.info(
            "[COMPARE_TIMING] request_id=%s total_ms=%s deterministic_ms=%s ai_ms=%s db_ms=%s",
            request_id,
            total_ms,
            deterministic_ms,
            ai_ms,
            db_ms,
        )

    return api_ok(
        {
            "cached": False,
            "comparison_id": comparison_id,
            "cars_selected": cars_selected_slots,
            "cars_selected_list": validated_cars,
            "model_output": model_output,
            "computed_result": computed_result,
            "narrative": narrative,
            "decision_result": decision_result,
            "checked_versions": checked_versions,
            "sources_index": sources_index,
            "assumptions": {},
            "ai": ai_payload,
            "visible_warning": visible_warning,
            "central_differences": central_differences,
            "guardrail_meta": comparison_guarded.get("guardrail_meta", {}),
        }
    )


def get_comparison_history(user_id: int, limit: int = 10) -> List[Dict]:
    """Get comparison history for a user."""
    records = (
        ComparisonHistory.query.filter_by(user_id=user_id)
        .order_by(ComparisonHistory.created_at.desc())
        .limit(limit)
        .all()
    )

    result = []
    for record in records:
        try:
            # Robust parsing with double-encoding support
            cars = _safe_json_obj(record.cars_selected, default=[])
            if not isinstance(cars, list):
                cars = []

            computed = _safe_json_obj(record.computed_result, default={})
            if not isinstance(computed, dict):
                computed = {}

            result.append(
                {
                    "id": record.id,
                    "created_at": record.created_at.isoformat(),
                    "cars": cars,
                    "overall_winner": computed.get("overall_winner"),
                }
            )
        except (AttributeError, TypeError, ValueError) as e:
            # Log warning and skip corrupted record
            current_app.logger.warning(
                f"Skipping corrupted comparison history record id={record.id}: {e}"
            )
            continue

    return result


def get_comparison_detail(comparison_id: int, user_id: Optional[int]) -> Optional[Dict]:
    """Get details of a specific comparison."""
    query = ComparisonHistory.query.filter_by(id=comparison_id)
    if user_id:
        query = query.filter_by(user_id=user_id)

    record = query.first()
    if not record:
        return None

    try:
        # Robust parsing with double-encoding support
        cars_selected = _safe_json_obj(record.cars_selected, default=[])
        if not isinstance(cars_selected, list):
            cars_selected = []

        computed_result = _safe_json_obj(record.computed_result, default={})
        if not isinstance(computed_result, dict):
            computed_result = {}

        model_output = _safe_json_obj(record.model_json_raw, default=None)
        if model_output is not None and not isinstance(model_output, dict):
            model_output = None

        sources_index = _safe_json_obj(record.sources_index, default={})
        if not isinstance(sources_index, dict):
            sources_index = {}

        assumptions = model_output.get("assumptions", {}) if model_output else {}

        narrative = resolve_comparison_narrative(
            computed_result if isinstance(computed_result, dict) else None
        )
        cars_selected_slots = (
            map_cars_to_slots(cars_selected) if isinstance(cars_selected, list) else {}
        )
        decision_result = sanitize_decision_result(
            computed_result.get("decision_result")
            if isinstance(computed_result, dict)
            else None,
            cars_selected_slots if isinstance(cars_selected_slots, dict) else {},
            computed_result if isinstance(computed_result, dict) else {},
            get_request_id(),
        )
        if (
            isinstance(computed_result, dict)
            and computed_result.get("decision_result") != decision_result
        ):
            computed_result["decision_result"] = decision_result
        ai_payload = build_stored_comparison_ai_payload(
            computed_result if isinstance(computed_result, dict) else None,
            narrative,
        )
        if isinstance(computed_result, dict) and record.computed_result != json.dumps(
            computed_result, ensure_ascii=False
        ):
            record.computed_result = json.dumps(computed_result, ensure_ascii=False)
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
                current_app.logger.warning(
                    "Failed to self-heal comparison detail id=%s",
                    comparison_id,
                    exc_info=True,
                )

        # Reconstruct stable car slots

        return {
            "id": record.id,
            "created_at": record.created_at.isoformat(),
            "cars_selected": cars_selected_slots,
            "cars_selected_list": cars_selected
            if isinstance(cars_selected, list)
            else [],
            "model_output": model_output,
            "computed_result": computed_result,
            "narrative": narrative,
            "decision_result": decision_result,
            "ai": ai_payload,
            "sources_index": sources_index,
            "assumptions": assumptions,
            "model_name": record.model_name,
            "prompt_version": record.prompt_version,
        }
    except (AttributeError, TypeError, ValueError) as e:
        current_app.logger.warning(
            f"Failed to parse comparison detail for id={comparison_id}: {e}"
        )
        return None


def regenerate_comparison_ai(
    comparison_id: int, user_id: int
) -> Optional[Dict[str, Any]]:
    """Regenerate AI explanation without recomputing deterministic numeric scoring."""
    record = ComparisonHistory.query.filter_by(
        id=comparison_id, user_id=user_id
    ).first()
    if not record:
        return None

    cars_selected = _safe_json_obj(record.cars_selected, default=[])
    computed_result = _safe_json_obj(record.computed_result, default={})
    model_output = _safe_json_obj(record.model_json_raw, default={})
    if not isinstance(cars_selected, list) or not isinstance(computed_result, dict):
        return None
    if not isinstance(model_output, dict):
        model_output = {}

    cars_selected_slots = map_cars_to_slots(cars_selected)
    server_computed_result = dict(computed_result)
    server_computed_result.pop("narrative", None)
    server_computed_result.pop("ai", None)

    writer_prompt = build_compare_writer_prompt(
        cars_selected_slots, server_computed_result, model_output
    )
    try:
        stage_b_output, stage_b_error = call_gemini_compare_writer(writer_prompt)
    except Exception as exc:
        _inc_compare_metric("compare_ai_regenerate_error_total")
        current_app.logger.exception(
            "[COMPARISON] compare_ai_regenerate_writer_failed request_id=%s comparison_id=%s user_id=%s error_type=%s",
            get_request_id(),
            comparison_id,
            user_id,
            type(exc).__name__,
        )
        stage_b_output, stage_b_error = None, "CALL_FAILED:UNKNOWN"

    narrative = None
    reason = None
    validated_decision = None
    decision_validation_reason = None
    if stage_b_error:
        reason = "stage_b_error"
        _inc_compare_metric("compare_ai_regenerate_fallback_total")
        _inc_compare_metric("compare_ai_fallback_used_total")
        narrative = build_deterministic_fallback_narrative(
            cars_selected_slots, server_computed_result
        )
    elif isinstance(stage_b_output, dict):
        current_app.logger.info(
            "[COMPARISON] compare_ai_regenerate payload request_id=%s comparison_id=%s payload_shape=%s",
            get_request_id(),
            comparison_id,
            _summarize_compare_writer_payload(stage_b_output),
        )
        validated_decision, decision_validation_reason = (
            _validate_decision_writer_response(
                stage_b_output,
                cars_selected_slots,
                server_computed_result,
            )
        )
        if validated_decision:
            narrative = sanitize_comparison_narrative(
                convert_decision_result_to_narrative(
                    validated_decision, cars_selected_slots
                )
            )
            current_app.logger.info(
                "[COMPARISON] compare_ai_regenerate accepted request_id=%s comparison_id=%s narrative_shape=%s",
                get_request_id(),
                comparison_id,
                _summarize_comparison_narrative_shape(narrative),
            )
        else:
            validated_writer, validation_reason = _validate_compare_writer_response(
                stage_b_output
            )
            if validated_writer:
                narrative = sanitize_comparison_narrative(
                    convert_writer_response_to_narrative(
                        validated_writer, cars_selected_slots
                    )
                )
                current_app.logger.info(
                    "[COMPARISON] compare_ai_regenerate legacy narrative accepted request_id=%s comparison_id=%s narrative_shape=%s",
                    get_request_id(),
                    comparison_id,
                    _summarize_comparison_narrative_shape(narrative),
                )
            else:
                raw_narrative = stage_b_output.get("narrative")
                salvaged_narrative = _salvage_partial_writer_output(
                    stage_b_output,
                    cars_selected_slots,
                    server_computed_result,
                )
                current_app.logger.warning(
                    "[COMPARISON] compare_ai_regenerate validation failed request_id=%s comparison_id=%s reason=%s payload_shape=%s",
                    get_request_id(),
                    comparison_id,
                    validation_reason,
                    _summarize_compare_writer_payload(stage_b_output),
                )
                if salvaged_narrative:
                    narrative = sanitize_comparison_narrative(salvaged_narrative)
                    current_app.logger.info(
                        "[COMPARISON] narrative salvaged from partial writer output request_id=%s comparison_id=%s narrative_shape=%s",
                        get_request_id(),
                        comparison_id,
                        _summarize_comparison_narrative_shape(narrative),
                    )
                elif raw_narrative:
                    narrative = sanitize_comparison_narrative(raw_narrative)
                    current_app.logger.info(
                        "[COMPARISON] compare_ai_regenerate legacy narrative used request_id=%s comparison_id=%s narrative_shape=%s",
                        get_request_id(),
                        comparison_id,
                        _summarize_comparison_narrative_shape(narrative),
                    )
                else:
                    reason = "stage_b_error"
                    _inc_compare_metric("compare_ai_regenerate_fallback_total")
                    _inc_compare_metric("compare_ai_fallback_used_total")
                    narrative = build_deterministic_fallback_narrative(
                        cars_selected_slots, server_computed_result
                    )

    if not (
        (server_computed_result.get("comparison_status") or {}).get("balanced", True)
    ):
        narrative = mark_partial_comparison_narrative(narrative)
        current_app.logger.info(
            "[COMPARISON] compare_ai_regenerate partial_stage_a fallback path used request_id=%s comparison_id=%s narrative_shape=%s",
            get_request_id(),
            comparison_id,
            _summarize_comparison_narrative_shape(narrative),
        )

    if validated_decision:
        decision_result = validated_decision["decision_result"]
    else:
        if decision_validation_reason:
            current_app.logger.warning(
                "[COMPARISON] compare_ai_regenerate decision_result fallback request_id=%s comparison_id=%s reason=%s",
                get_request_id(),
                comparison_id,
                decision_validation_reason,
            )
        decision_result = build_deterministic_decision_result(
            cars_selected_slots, server_computed_result, stage_b_output
        )
    server_computed_result["decision_result"] = decision_result
    ai_payload = build_ai_payload(
        server_computed_result,
        narrative,
        "ok" if reason is None else "fallback",
        reason,
    )
    current_app.logger.info(
        "[COMPARISON] compare_ai_regenerate response request_id=%s comparison_id=%s ai_status=%s ai_reason=%s narrative_shape=%s",
        get_request_id(),
        comparison_id,
        ai_payload.get("status"),
        ai_payload.get("reason"),
        _summarize_comparison_narrative_shape(narrative),
    )
    if stage_b_error:
        ai_payload["error"] = stage_b_error
    persisted_computed = dict(server_computed_result)
    if narrative:
        persisted_computed["narrative"] = narrative
    persisted_computed["ai"] = ai_payload
    record.computed_result = json.dumps(persisted_computed, ensure_ascii=False)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        _inc_compare_metric("compare_ai_regenerate_error_total")
        current_app.logger.exception(
            "[COMPARISON] compare_ai_regenerate_commit_failed request_id=%s comparison_id=%s user_id=%s",
            get_request_id(),
            comparison_id,
            user_id,
        )
    _inc_compare_metric("compare_ai_regenerate_used_total")

    return {
        "comparison_id": comparison_id,
        "ai": ai_payload,
        "narrative": narrative,
        "decision_result": decision_result,
    }
