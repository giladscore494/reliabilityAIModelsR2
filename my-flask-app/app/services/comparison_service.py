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

from app.services.comparison.cache import (
    _safe_json_obj,
    _safe_parse_json_cached,
    compute_request_hash,
)
from app.services.comparison.fallbacks import (
    _empty_single_car_payload,
    _empty_stage_a_output,
    build_deterministic_fallback_narrative,
    mark_partial_comparison_narrative,
)
from app.services.comparison.normalization import (
    build_checked_versions,
    build_display_name,
    infer_compare_segment,
    map_cars_to_slots,
)
from app.services.comparison.scoring import (
    CATEGORY_WEIGHTS,
    ORDINAL_SCORES_NEGATIVE,
    ORDINAL_SCORES_POSITIVE,
    compute_category_score,
    compute_overall_score,
    determine_winner,
    score_ordinal_negative,
    score_ordinal_positive,
)
from app.services.comparison.prompts import (
    build_compare_grounding_prompt,
    build_single_car_prompt,
    build_comparison_prompt,
    build_compare_writer_prompt,
    build_compare_writer_retry_prompt,
)
from app.services.comparison.schemas import (
    validate_buyer_profile,
    validate_comparison_request,
    validate_compare_writer_response,
    validate_grounding,
)


# ============================================================
# JSON PARSING HELPERS
# ============================================================


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


# ============================================================
# SCORING FUNCTIONS (DETERMINISTIC - CODE ONLY)
# ============================================================


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
    from app.services.comparison.grounding import parse_single_car_json as _impl

    return _impl(raw_text)


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
    from app.services.comparison.grounding import parse_stage_a_json as _impl

    return _impl(raw_text)


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
    from app.services.comparison.writer import _validate_decision_writer_response as _impl

    return _impl(payload, cars_selected_slots, computed_result)


def _summarize_compare_writer_payload(payload: Any) -> Dict[str, Any]:
    from app.services.comparison.writer import _summarize_compare_writer_payload as _impl

    return _impl(payload)


def _summarize_comparison_narrative_shape(narrative: Any) -> Dict[str, Any]:
    from app.services.comparison.writer import (
        _summarize_comparison_narrative_shape as _impl,
    )

    return _impl(narrative)


def _validate_compare_writer_response(
    payload: Any,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    from app.services.comparison.writer import _validate_compare_writer_response as _impl

    return _impl(payload)


def _salvage_partial_writer_output(
    stage_b_output: Any,
    cars_selected_slots: Dict,
    server_computed_result: Dict,
) -> Optional[Dict[str, Any]]:
    from app.services.comparison.writer import _salvage_partial_writer_output as _impl

    return _impl(stage_b_output, cars_selected_slots, server_computed_result)


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
    from app.services.comparison.writer import _build_stage_a_summary as _impl

    return _impl(computed_result)


def _build_stage_b_payload(
    narrative: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    from app.services.comparison.writer import _build_stage_b_payload as _impl

    return _impl(narrative)


def _has_usable_comparison_narrative(narrative: Optional[Dict[str, Any]]) -> bool:
    from app.services.comparison.writer import _has_usable_comparison_narrative as _impl

    return _impl(narrative)


def _normalize_stage_b_category_for_narrative(
    category: Any,
) -> Optional[Dict[str, Any]]:
    from app.services.comparison.writer import (
        _normalize_stage_b_category_for_narrative as _impl,
    )

    return _impl(category)


def resolve_comparison_narrative(
    computed_result: Optional[Dict[str, Any]],
    ai_payload: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    from app.services.comparison.writer import resolve_comparison_narrative as _impl

    return _impl(computed_result, ai_payload)


def build_stored_comparison_ai_payload(
    computed_result: Optional[Dict[str, Any]],
    narrative: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    from app.services.comparison.writer import build_stored_comparison_ai_payload as _impl

    return _impl(computed_result, narrative)


def build_ai_payload(
    computed_result: Dict[str, Any],
    narrative: Optional[Dict[str, Any]],
    status: str,
    reason: Optional[str],
) -> Dict[str, Any]:
    from app.services.comparison.writer import build_ai_payload as _impl

    return _impl(computed_result, narrative, status, reason)


def convert_writer_response_to_narrative(
    validated_payload: Dict[str, Any], cars_selected_slots: Dict
) -> Dict[str, Any]:
    from app.services.comparison.writer import convert_writer_response_to_narrative as _impl

    return _impl(validated_payload, cars_selected_slots)


def convert_decision_result_to_narrative(
    validated_payload: Dict[str, Any], cars_selected_slots: Dict
) -> Dict[str, Any]:
    from app.services.comparison.writer import (
        convert_decision_result_to_narrative as _impl,
    )

    return _impl(validated_payload, cars_selected_slots)


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
    from app.services.comparison.grounding import call_gemini_comparison as _impl

    return _impl(prompt, timeout_sec)


def call_gemini_single_car(
    prompt: str,
    car_label: str,
    timeout_sec: int = COMPARE_STAGE_A_TIMEOUT_SEC,
    request_id: Optional[str] = None,
    log: Optional[logging.Logger] = None,
) -> Tuple[Optional[Dict], Optional[str]]:
    from app.services.comparison.grounding import call_gemini_single_car as _impl

    return _impl(prompt, car_label, timeout_sec, request_id, log)


def call_stage_a_parallel(
    validated_cars: List[Dict], cars_selected_slots: Dict
) -> Tuple[Dict, Dict, List[str]]:
    from app.services.comparison.grounding import call_stage_a_parallel as _impl

    return _impl(validated_cars, cars_selected_slots)


def call_gemini_compare_writer(
    prompt: str, timeout_sec: int = COMPARE_WRITER_TIMEOUT_SEC
) -> Tuple[Optional[Dict], Optional[str]]:
    from app.services.comparison.writer import call_gemini_compare_writer as _impl

    return _impl(prompt, timeout_sec)


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
    from app.services.comparison.writer import generate_narrative as _impl

    return _impl(cars_selected_slots, computed_result, timeout_sec)


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


def enforce_authoritative_numbers(
    server_computed: Dict, stage_b_output: Optional[Dict], request_id: str
) -> Dict:
    from app.services.comparison.pipeline import enforce_authoritative_numbers as _impl

    return _impl(server_computed, stage_b_output, request_id)


# ============================================================
# SOURCES INDEX BUILDER
# ============================================================


def build_sources_index(model_output: Dict) -> Dict:
    from app.services.comparison.grounding import build_sources_index as _impl

    return _impl(model_output)


def build_sources_index_from_flat(merged_output: Dict) -> Dict:
    from app.services.comparison.grounding import build_sources_index_from_flat as _impl

    return _impl(merged_output)


# ============================================================
# MAIN HANDLER
# ============================================================


def handle_comparison_request(
    data: Dict,
    user_id: Optional[int],
    session_id: Optional[str],
    owner_bypass: bool = False,
) -> Any:
    from app.services.comparison.pipeline import handle_comparison_request as _impl

    return _impl(data, user_id, session_id, owner_bypass)


def get_comparison_history(user_id: int, limit: int = 10) -> List[Dict]:
    from app.services.comparison.history import get_comparison_history as _impl

    return _impl(user_id, limit)


def get_comparison_detail(comparison_id: int, user_id: Optional[int]) -> Optional[Dict]:
    from app.services.comparison.history import get_comparison_detail as _impl

    return _impl(comparison_id, user_id)


def regenerate_comparison_ai(
    comparison_id: int, user_id: int
) -> Optional[Dict[str, Any]]:
    from app.services.comparison.history import regenerate_comparison_ai as _impl

    return _impl(comparison_id, user_id)
