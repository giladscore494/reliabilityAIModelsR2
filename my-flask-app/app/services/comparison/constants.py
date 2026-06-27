# -*- coding: utf-8 -*-
"""Shared constants for comparison service modules."""

import os
import re

CHECKED_VERSION_UNKNOWN_HE = "לא ידוע / לבדיקה"
CHECKED_VERSION_NOT_VERIFIED_HE = "לא מאומת"
CHECKED_VERSION_DATA_BASIS_ALLOWED = {
    "user_input",
    "verified_source",
    "ai_inference",
    "mixed",
}
CHECKED_VERSION_CONFIDENCE_ALLOWED = {"high", "medium", "low", "unverified"}

COMPARISON_PROMPT_VERSION = "v5_single_pass"
COMPARISON_MODEL_ID = (
    os.environ.get("COMPARISON_STAGE_A_MODEL")
    or os.environ.get("GEMINI_COMPARE_MODEL_ID")
    or "gemini-3.1-pro-preview"
)
AI_CALL_TIMEOUT_SEC = int(os.environ.get("AI_CALL_TIMEOUT_SEC", "170"))
COMPARE_STAGE_A_TIMEOUT_SEC = max(1, int(os.environ.get("COMPARISON_STAGE_A_TIMEOUT_MS", "60000")) // 1000) if os.environ.get("COMPARISON_STAGE_A_TIMEOUT_MS") else int(os.environ.get("COMPARE_STAGE_A_TIMEOUT_SEC", "60"))
COMPARE_SINGLE_PASS_TIMEOUT_SEC = int(os.environ.get("COMPARE_SINGLE_PASS_TIMEOUT_SEC", "120"))
COMPARE_SINGLE_PASS_MAX_REMOTE_CALLS = int(os.environ.get("COMPARE_SINGLE_PASS_MAX_REMOTE_CALLS", "8"))
COMPARE_STAGE_A_REPAIR_TIMEOUT_SEC = int(os.environ.get("COMPARE_STAGE_A_REPAIR_TIMEOUT_SEC", "15"))
COMPARE_STAGE_A_REPAIR_MAX_OUTPUT_TOKENS = int(os.environ.get("COMPARE_STAGE_A_REPAIR_MAX_OUTPUT_TOKENS", "1200"))
COMPARE_STAGE_A_REPAIR_MAX_INPUT_CHARS = int(os.environ.get("COMPARE_STAGE_A_REPAIR_MAX_INPUT_CHARS", "4000"))
COMPARE_STAGE_A_MAX_OUTPUT_TOKENS = int(
    os.environ.get("COMPARE_STAGE_A_MAX_OUTPUT_TOKENS", "8192")
)
COMPARE_STAGE_A_TEMPERATURE = float(
    os.environ.get("COMPARISON_STAGE_A_TEMPERATURE", os.environ.get("COMPARE_STAGE_A_TEMPERATURE", "0.0"))
)
COMPARE_WRITER_TIMEOUT_SEC = max(1, int(os.environ.get("COMPARISON_STAGE_B_TIMEOUT_MS", "30000")) // 1000) if os.environ.get("COMPARISON_STAGE_B_TIMEOUT_MS") else int(os.environ.get("COMPARE_WRITER_TIMEOUT_SEC", "30"))
COMPARE_WRITER_MAX_OUTPUT_TOKENS = int(
    os.environ.get("COMPARE_WRITER_MAX_OUTPUT_TOKENS", "3200")
)
COMPARE_WRITER_RETRY_MAX_OUTPUT_TOKENS = int(
    os.environ.get("COMPARE_WRITER_RETRY_MAX_OUTPUT_TOKENS", "500")
)
COMPARE_WRITER_PROMPT_CHAR_CAP = int(
    os.environ.get("COMPARE_WRITER_PROMPT_CHAR_CAP", "16000")
)
TIE_THRESHOLD = 5
PARALLEL_GRACE_SEC = 5
ELLIPSIS_LEN = 3

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

CATEGORY_LABELS_HE = {
    "reliability_risk": "אמינות וסיכונים",
    "ownership_cost": "עלות אחזקה",
    "practicality_comfort": "נוחות ופרקטיות",
    "driving_performance": "ביצועים ונהיגה",
}

_LABEL_VALUES = {"low", "medium", "high"}
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
# Clean neutral fallback used by the single-pass compare flow when the
# decision floor is not met (too little verified evidence). No invented winner.
DECISION_NEUTRAL_FALLBACK_HE = (
    "לא ניתן להשלים השוואה אמינה כרגע. אפשר לנסות שוב בעוד רגע או לדייק שנתון, מנוע ורמת גימור."
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

# Patterns that indicate a schema echo or placeholder output rather than real data
SCHEMA_ECHO_PATTERNS = [
    r'"up to \d+ urls"',
    r'"exact\|ambiguous\|unmatched"',
    r'"complete\|partial"',
    r'"unknown\|low\|medium\|high"',
    r'"catalog_exact\|catalog_ambiguous\|web_resolved\|unmatched"',
    r'"high\|medium\|low"',
    r'"pricing\|fuel\|safety\|reliability\|ownership_cost\|market\|performance\|practicality\|warranty\|recall"',
]
