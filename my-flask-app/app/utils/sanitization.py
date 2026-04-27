"""Utilities for sanitizing model responses.

The frontend expects stable response shapes. Some parts of the application
(e.g. main.py imports) rely on these symbols existing.

Security goals:
- allowlist-only for known response shapes
- HTML-escape all strings (XSS defense)
- size caps for lists / strings
"""

from __future__ import annotations

import html
from typing import Any, Dict, Mapping, Optional, Sequence
import re


# -----------------------------
# basic coercion + escaping
# -----------------------------

_MAX_STR = 8000
_MAX_LIST = 50
_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2066-\u2069]")
_SAFE_URL_RE = re.compile(r'^https?://', re.IGNORECASE)


def _normalize_text(raw: str) -> str:
    """Iteratively unescape and clean control characters/whitespace."""
    s = raw
    for _ in range(3):
        unescaped = html.unescape(s)
        if unescaped == s:
            break
        s = unescaped
    s = _ZERO_WIDTH_RE.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _escape(s: Any) -> str:
    if not isinstance(s, str):
        s = "" if s is None else str(s)
    if len(s) > _MAX_STR:
        s = s[:_MAX_STR]
    s = _normalize_text(s)
    return html.escape(s, quote=False)


def _sanitize_url(raw: Any) -> str:
    """Sanitize a URL: escape HTML entities and enforce http/https protocol."""
    url = _escape(raw)
    if url and not _SAFE_URL_RE.match(url):
        return ""
    return url


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _coerce_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _clamp_int(value: Any, *, lo: int, hi: int, default: int = 0) -> int:
    try:
        if isinstance(value, bool):
            return default
        n = int(float(value))
    except Exception:
        return default
    return max(lo, min(hi, n))



def _sanitize_str_list(value: Any, *, max_items: int = _MAX_LIST) -> list:
    arr = _coerce_list(value)[:max_items]
    return [_escape(v) for v in arr]


# -----------------------------
# /analyze sanitization
# -----------------------------

_SCORE_BREAKDOWN_KEYS = (
    "engine_transmission_score",
    "electrical_score",
    "suspension_brakes_score",
    "maintenance_cost_score",
    "satisfaction_score",
    "recalls_score",
)


def _sanitize_score_breakdown(value: Any) -> Dict[str, int]:
    """Strict 6-key score_breakdown, values clamped to 1..10."""
    src = _coerce_dict(value)
    out: Dict[str, int] = {}
    for k in _SCORE_BREAKDOWN_KEYS:
        out[k] = _clamp_int(src.get(k), lo=1, hi=10, default=1)
    return out


# --- risk_signals sanitization helpers ---

_SYSTEM_ALLOWED = {"engine", "transmission", "electrical", "cooling", "brakes", "suspension", "other"}
_SEVERITY_RS_ALLOWED = {"low", "medium", "high"}
_FREQ_ALLOWED = {"rare", "sometimes", "common"}
_EVIDENCE_ALLOWED = {"weak", "medium", "strong"}
_TRANS_TYPE_ALLOWED = {"automatic", "manual", "cvt", "dct", "other", "unknown"}
_MCP_LEVEL_ALLOWED = {"low", "medium", "high", "unknown"}
_SQ_ALLOWED = {"low", "medium", "high"}
_OVERALL_RELIABILITY_ALLOWED = {"high", "medium", "low"}
_MODEL_JSON_BIAS_ALLOWED = {"strong", "neutral", "weak"}
_MODEL_JSON_SENSITIVITY_ALLOWED = {"low", "normal", "high"}
_CALIBRATION_SOURCE_ALLOWED = {"model_json", "none"}


def _clamp_float(value: Any, lo: float = 0.0, hi: float = 1.0, default: float = 0.0) -> float:
    try:
        if isinstance(value, bool):
            return default
        f = float(value)
    except Exception:
        return default
    return max(lo, min(hi, round(f, 4)))


def _normalize_enum(value: Any, allowed: set, default: str) -> str:
    if isinstance(value, str):
        v = value.strip().lower()
        if v in allowed:
            return v
    return default


def _normalize_optional_enum(value: Any, allowed: set) -> Optional[str]:
    if isinstance(value, str):
        v = value.strip().lower()
        if v in allowed:
            return v
    return None


def _sanitize_risk_signals(value: Any) -> Dict[str, Any]:
    """Sanitize risk_signals dict with strict schema enforcement."""
    src = _coerce_dict(value)
    out: Dict[str, Any] = {}

    # vehicle_resolution
    vr = _coerce_dict(src.get("vehicle_resolution"))
    out["vehicle_resolution"] = {
        "generation": _escape(vr.get("generation") or ""),
        "engine_family": _escape(vr.get("engine_family") or ""),
        "transmission_type": _normalize_enum(vr.get("transmission_type"), _TRANS_TYPE_ALLOWED, "unknown"),
        "confidence": _clamp_float(vr.get("confidence")),
    }

    # recalls
    rc = _coerce_dict(src.get("recalls"))
    rc_count = _clamp_int(rc.get("count"), lo=0, hi=1000, default=0)
    rc_high = _clamp_int(rc.get("high_severity_count"), lo=0, hi=1000, default=0)
    out["recalls"] = {
        "count": rc_count,
        "high_severity_count": min(rc_high, rc_count) if rc_count > 0 else rc_high,
        "notes": _escape(rc.get("notes") or ""),
    }

    # systemic_issue_signals
    raw_signals = _coerce_list(src.get("systemic_issue_signals"))[:_MAX_LIST]
    signals_out = []
    for item in raw_signals:
        if not isinstance(item, dict):
            continue
        signals_out.append({
            "system": _normalize_enum(item.get("system"), _SYSTEM_ALLOWED, "other"),
            "issue": _escape(item.get("issue") or ""),
            "severity": _normalize_enum(item.get("severity"), _SEVERITY_RS_ALLOWED, "medium"),
            "repeat_frequency": _normalize_enum(item.get("repeat_frequency"), _FREQ_ALLOWED, "rare"),
            "typical_timing": _escape(item.get("typical_timing") or ""),
            "evidence_text": _escape(item.get("evidence_text") or ""),
            "evidence_strength": _normalize_enum(item.get("evidence_strength"), _EVIDENCE_ALLOWED, "medium"),
        })
    out["systemic_issue_signals"] = signals_out

    # maintenance_cost_pressure
    mcp = _coerce_dict(src.get("maintenance_cost_pressure"))
    out["maintenance_cost_pressure"] = {
        "level": _normalize_enum(mcp.get("level"), _MCP_LEVEL_ALLOWED, "unknown"),
        "drivers": [_escape(d) for d in _coerce_list(mcp.get("drivers"))[:_MAX_LIST]],
        "evidence_strength": _normalize_enum(mcp.get("evidence_strength"), _EVIDENCE_ALLOWED, "medium"),
    }

    # confidence_meta
    cm = _coerce_dict(src.get("confidence_meta"))
    out["confidence_meta"] = {
        "data_completeness": _clamp_float(cm.get("data_completeness")),
        "source_quality": _normalize_enum(cm.get("source_quality"), _SQ_ALLOWED, "medium"),
        "notes": _escape(cm.get("notes") or ""),
    }

    return out




def sanitize_analyze_response(response: Any) -> Dict[str, Any]:
    """Sanitize /analyze response to match static/script.js expectations."""
    src = _coerce_dict(response)

    # allowlist fields used by the frontend
    out: Dict[str, Any] = {}
    out["ok"] = bool(src.get("ok", True))
    if "error" in src:
        out["error"] = _escape(src.get("error"))

    # numbers
    if "base_score_calculated" in src:
        out["base_score_calculated"] = _clamp_int(src.get("base_score_calculated"), lo=0, hi=100, default=0)

    for score_key in ("model_reliability_score", "deal_risk_score"):
        if score_key in src:
            out[score_key] = _clamp_int(src.get(score_key), lo=0, hi=100, default=0)

    if "avg_repair_cost_ILS" in src:
        out["avg_repair_cost_ILS"] = _clamp_int(src.get("avg_repair_cost_ILS"), lo=0, hi=1_000_000, default=0)

    # strings
    for k in (
        "source_tag",
        "mileage_note",
        "reliability_summary",
        "reliability_summary_simple",
        "overall_reliability_reasoning",
        "reliability_factors_summary",
    ):
        if k in src:
            out[k] = _escape(src.get(k))

    # boolean
    if "km_warn" in src:
        out["km_warn"] = bool(src.get("km_warn"))

    def _sanitize_sources(v: Any) -> list:
        items = _coerce_list(v)[:_MAX_LIST]
        out_list = []
        for item in items:
            if isinstance(item, dict):
                out_list.append(
                    {
                        "title": _escape(item.get("title")),
                        "url": _sanitize_url(item.get("url")),
                        "domain": _escape(item.get("domain")),
                    }
                )
            else:
                out_list.append(_escape(item))
        return out_list

    if "common_issues" in src:
        out["common_issues"] = _sanitize_str_list(src.get("common_issues"), max_items=25)

    if "recommended_checks" in src:
        out["recommended_checks"] = _sanitize_str_list(src.get("recommended_checks"), max_items=25)

    if "search_queries" in src:
        out["search_queries"] = _sanitize_str_list(src.get("search_queries"), max_items=10)
    if "search_performed" in src:
        out["search_performed"] = bool(src.get("search_performed"))
    if "sources" in src:
        out["sources"] = _sanitize_sources(src.get("sources"))

    # issues_with_costs: list[dict]
    issues_with_costs_out = []
    for row in _coerce_list(src.get("issues_with_costs"))[:25]:
        if not isinstance(row, dict):
            continue
        issues_with_costs_out.append(
            {
                "issue": _escape(row.get("issue")),
                "avg_cost_ILS": _clamp_int(row.get("avg_cost_ILS"), lo=0, hi=1_000_000, default=0),
                "source": _escape(row.get("source")),
                "severity": _escape(row.get("severity")),
            }
        )
    if issues_with_costs_out:
        out["issues_with_costs"] = issues_with_costs_out

    # competitors: list[dict]
    competitors_out = []
    for row in _coerce_list(src.get("common_competitors_brief"))[:20]:
        if not isinstance(row, dict):
            continue
        competitors_out.append(
            {
                "model": _escape(row.get("model")),
                "brief_summary": _escape(row.get("brief_summary")),
            }
        )
    if competitors_out:
        out["common_competitors_brief"] = competitors_out

    # strict score_breakdown
    if "score_breakdown" in src:
        out["score_breakdown"] = _sanitize_score_breakdown(src.get("score_breakdown"))

    if "reliability_report" in src:
        out["reliability_report"] = sanitize_reliability_report_response(src.get("reliability_report"))

    if "estimated_reliability" in src:
        out["estimated_reliability"] = _escape(src.get("estimated_reliability"))

    for label_key in ("model_reliability_label", "deal_risk_label"):
        if label_key in src:
            out[label_key] = _escape(src.get(label_key))

    if "calibration_applied" in src:
        out["calibration_applied"] = bool(src.get("calibration_applied"))

    if "calibration_source" in src:
        out["calibration_source"] = _normalize_enum(
            src.get("calibration_source"),
            _CALIBRATION_SOURCE_ALLOWED,
            "none",
        )

    optional_calibration_fields = {
        "reliability_bias": _MODEL_JSON_BIAS_ALLOWED,
        "recall_penalty_sensitivity": _MODEL_JSON_SENSITIVITY_ALLOWED,
        "maintenance_penalty_sensitivity": _MODEL_JSON_SENSITIVITY_ALLOWED,
        "systemic_penalty_sensitivity": _MODEL_JSON_SENSITIVITY_ALLOWED,
        "calibration_confidence": _SQ_ALLOWED,
    }
    for field_name, allowed_values in optional_calibration_fields.items():
        if field_name in src:
            out[field_name] = _normalize_optional_enum(src.get(field_name), allowed_values)

    if "soft_floor_if_no_major_systemic" in src:
        raw_soft_floor = src.get("soft_floor_if_no_major_systemic")
        out["soft_floor_if_no_major_systemic"] = (
            _clamp_int(raw_soft_floor, lo=0, hi=100, default=0)
            if raw_soft_floor is not None
            else None
        )

    if "overall_reliability_estimate" in src:
        out["overall_reliability_estimate"] = _normalize_enum(
            src.get("overall_reliability_estimate"),
            _OVERALL_RELIABILITY_ALLOWED,
            "medium",
        )

    if "risk_signals" in src:
        out["risk_signals"] = _sanitize_risk_signals(src.get("risk_signals"))

    # Log dropped keys (only key names, no PII)
    dropped_keys = set(src.keys()) - set(out.keys())
    if dropped_keys:
        import logging
        logger = logging.getLogger(__name__)
        # Get request_id from context if available
        try:
            from flask import has_request_context, g
            if has_request_context() and hasattr(g, 'request_id'):
                request_id = g.request_id
            else:
                request_id = "unknown"
        except Exception:
            request_id = "unknown"
        logger.info(f"[SANITIZATION] Dropped keys in analyze response: {sorted(dropped_keys)} (request_id: {request_id})")

    return out


# -----------------------------
# /advisor_api sanitization
# -----------------------------

_RECOMMENDED_CAR_ALLOWED = {
    "brand",
    "model",
    "year",
    "fuel",
    "gear",
    "turbo",
    "engine_cc",
    "price_range_nis",
    "avg_fuel_consumption",
    "annual_fee",
    "reliability_score",
    "maintenance_cost",
    "safety_rating",
    "insurance_cost",
    "resale_value",
    "performance_score",
    "comfort_features",
    "suitability",
    "market_supply",
    "fit_score",
    "comparison_comment",
    "not_recommended_reason",
    "annual_energy_cost",
    "annual_fuel_cost",
    "total_annual_cost",
}


def _is_method_field(k: str) -> bool:
    return k.endswith("_method")


def _sanitize_recommended_car(item: Any) -> Dict[str, Any]:
    if not isinstance(item, dict):
        return {}

    allowed = set(_RECOMMENDED_CAR_ALLOWED)
    for k in item.keys():
        if isinstance(k, str) and _is_method_field(k):
            allowed.add(k)

    out: Dict[str, Any] = {}
    for k in allowed:
        if k not in item:
            continue
        v = item.get(k)

        # numbers
        if k in {
            "year",
            "engine_cc",
            "avg_fuel_consumption",
            "annual_fee",
            "reliability_score",
            "maintenance_cost",
            "safety_rating",
            "insurance_cost",
            "resale_value",
            "performance_score",
            "comfort_features",
            "suitability",
            "fit_score",
            "annual_energy_cost",
            "annual_fuel_cost",
            "total_annual_cost",
        }:
            if k == "year":
                out[k] = _clamp_int(v, lo=1990, hi=2100, default=0)
            elif k == "fit_score":
                out[k] = _clamp_int(v, lo=0, hi=100, default=0)
            elif "score" in k or "rating" in k or k in {"resale_value", "comfort_features", "suitability"}:
                out[k] = _clamp_int(v, lo=0, hi=10, default=0)
            else:
                out[k] = _clamp_int(v, lo=0, hi=10_000_000, default=0)
            continue

        # price_range_nis can be list [min,max] or number
        if k == "price_range_nis":
            if isinstance(v, list):
                vv = v[:2]
                out[k] = [_clamp_int(x, lo=0, hi=10_000_000, default=0) for x in vv]
            else:
                out[k] = _clamp_int(v, lo=0, hi=10_000_000, default=0)
            continue

        # strings (incl methods and comments)
        out[k] = _escape(v)

    return out


def sanitize_advisor_api_response(payload: Any) -> Dict[str, Any]:
    """Sanitize /advisor_api response to match static/recommendations.js expectations."""
    src = _coerce_dict(payload)
    out: Dict[str, Any] = {}

    # keep these top-level fields (recommendations.js uses them)
    out["search_performed"] = bool(src.get("search_performed", False))

    queries = _coerce_list(src.get("search_queries"))[:6]
    out["search_queries"] = [_escape(q) for q in queries]

    cars = _coerce_list(src.get("recommended_cars"))[:10]
    out["recommended_cars"] = [_sanitize_recommended_car(c) for c in cars if isinstance(c, dict)]

    return out


# Backwards-compatible name used by main.py
def sanitize_advisor_response(payload: Any) -> Dict[str, Any]:
    return sanitize_advisor_api_response(payload)


# -----------------------------
# /compare narrative sanitization
# -----------------------------

_CATEGORY_KEY_ALLOWED = {
    "reliability_risk", "ownership_cost", "practicality_comfort",
    "driving_performance", "safety",
}
_WINNER_ALLOWED = {"car_1", "car_2", "car_3", "tie"}


def sanitize_comparison_narrative(narrative: Any) -> Optional[Dict[str, Any]]:
    """Sanitize narrative output from the 2nd LLM call.
    Enforces allowlist keys, HTML-escapes strings, caps sizes.
    Returns None if input is not a dict.
    """
    if not isinstance(narrative, dict):
        return None

    out: Dict[str, Any] = {}

    # overall_summary
    if "overall_summary" in narrative:
        out["overall_summary"] = _escape(narrative["overall_summary"])

    # category_explanations
    raw_cats = _coerce_list(narrative.get("category_explanations"))[:10]
    cats_out = []
    for cat in raw_cats:
        if not isinstance(cat, dict):
            continue
        cat_key = str(cat.get("category_key", "")).strip()
        if cat_key not in _CATEGORY_KEY_ALLOWED:
            continue
        winner = str(cat.get("winner", "")).strip()
        if winner not in _WINNER_ALLOWED:
            winner = ""

        explanations_raw = _coerce_dict(cat.get("explanations"))
        explanations_out = {}
        for k in ("car_1", "car_2", "car_3"):
            if k in explanations_raw:
                explanations_out[k] = _escape(explanations_raw[k])

        why_list = _coerce_list(cat.get("why_it_scored_that_way"))[:3]
        why_out = [_escape(w) for w in why_list]

        cats_out.append({
            "category_key": cat_key,
            "title_he": _escape(cat.get("title_he", "")),
            "winner": winner,
            "explanations": explanations_out,
            "why_it_scored_that_way": why_out,
        })
    out["category_explanations"] = cats_out

    # disclaimers
    raw_disclaimers = _coerce_list(narrative.get("disclaimers_he"))[:5]
    out["disclaimers_he"] = [_escape(d) for d in raw_disclaimers]

    return out


# -----------------------------
# Reliability report (strict JSON spec)
# -----------------------------

_LEVEL_ALLOWED = {"low", "medium", "high"}
_RELIABILITY_REPORT_FINAL_LINE = (
    "This information highlights areas to verify and is not a substitute for a professional inspection."
)
_DEFAULT_RISK_AREAS = [
    {
        "risk_area": "מנוע, גיר ומערכת קירור",
        "why_to_check": "מערכות אלו יוצרות בדרך כלל את החשיפה הכספית הגבוהה ביותר אם קיימת תקלה חבויה.",
    },
    {
        "risk_area": "היסטוריית טיפולים ועדכוני יצרן",
        "why_to_check": "תיעוד חסר או לא עקבי מקשה להבין אם טיפולים, קמפיינים ועדכוני תוכנה בוצעו בזמן.",
    },
    {
        "risk_area": "קילומטראז׳ ביחס לגיל הרכב ולאופי השימוש",
        "why_to_check": "פער בין הגיל, הקילומטראז׳ והשימוש בפועל עשוי לשנות את רמת השחיקה וההוצאות האפשריות.",
    },
    {
        "risk_area": "פגיעות עבר, תיקוני שלדה ודפוסי בעלות",
        "why_to_check": "ריבוי בעלים, תאונות קודמות או תיקונים מהותיים יכולים להשפיע על הסיכון המכני והכלכלי.",
    },
]
_DEFAULT_DECISION_CHECKLIST = {
    "mechanical_inspection_points": [
        "סריקת מחשב מלאה למנוע, גיר, מערכות בטיחות ומערכות עזר.",
        "בדיקת נזילות שמן/נוזל קירור, מצב מערכת הקירור וסימני התחממות.",
        "נסיעת מבחן לבדיקת רעידות, החלקות גיר, רעשים ממתלים ובלמים.",
        "בדיקת צמיגים, בלמים, בולמים ובלאי לא אחיד שמעיד על בעיית שלדה או כיוון.",
    ],
    "documents_to_verify": [
        "ספר טיפולים, חשבוניות ומועדי טיפולים בפועל.",
        "אישור על קריאות שירות, קמפיינים ועדכוני תוכנה שבוצעו אם קיימים.",
        "דוח בעלויות קודמות, שעבודים או מגבלות רישום אם רלוונטי.",
        "דוחות בדיקה קודמים או תיעוד תאונה/תיקון אם קיים.",
    ],
    "questions_to_ask_seller": [
        "למה הרכב נמכר עכשיו וכמה זמן הוא בבעלות המוכר הנוכחי?",
        "האם היו תקלות חוזרות, תיקוני גיר/מנוע, או תקלות חשמל משמעותיות?",
        "איפה בוצעו הטיפולים והאם יש רצף חשבוניות מלא?",
        "האם בוצעו תאונות, תיקוני שלדה, צביעה רחבה או החלפת מכלולים מרכזיים?",
    ],
    "red_flags_to_look_for": [
        "סירוב לשתף מסמכים או פערים מהותיים בהיסטוריית הטיפולים.",
        "נורות אזהרה פעילות, רעידות, החלקות גיר או התחממות בנסיעת מבחן.",
        "סימני נזילה, תיקוני פח חריגים, ריתוכים או חוסר התאמה בין חלקי מרכב.",
        "פער לא מוסבר בין מצב הרכב, הקילומטראז׳, מספר הבעלים והתיאור של המוכר.",
    ],
}
_DEFAULT_KNOWN_UNCERTAINTIES = [
    "המצב המכני בפועל של הרכב הספציפי.",
    "רציפות ואיכות היסטוריית הטיפולים והחשבוניות.",
    "נזק חבוי משלדה, תאונה, הצפה או תיקון לא מתועד.",
    "אופן הנהיגה והשימוש של הבעלים הקודמים.",
]
_DEFAULT_COST_SENSITIVITY = [
    "עלויות אפשריות עשויות להשתנות משמעותית לפי מצב הרכב בפועל (למשל כ-₪1,500–₪4,000 אם מתגלה צורך בתיקוני בלאי בינוניים).",
    "אם קיימת תקלה מהותית במנוע, גיר, קירור או מערכת חשמל מרכזית, החשיפה עשויה לעלות לטווח רחב יותר כגון כ-₪4,000–₪15,000+.",
]


def _derive_missing_info(payload: Optional[Mapping[str, Any]]) -> list:
    """Infer missing info items from the incoming payload."""
    if not payload:
        return []

    labels = {
        "make": "יצרן",
        "model": "דגם",
        "sub_model": "תת-דגם/תצורה",
        "year": "שנת ייצור",
        "trim": "רמת גימור/מנוע",
        "engine": "מנוע/נפח",
        "mileage_km": "קילומטראז׳ מדויק",
        "mileage_range": "טווח קילומטראז׳",
        "ownership_history": "היסטוריית בעלויות",
        "usage_city_pct": "אחוז נסיעה עירונית",
        "budget": "תקציב רכישה",
        "budget_min": "תקציב מינימלי",
        "budget_max": "תקציב מקסימלי",
    }
    missing = []
    for field, label in labels.items():
        if not payload.get(field):
            missing.append(label)
    return missing


def derive_missing_info(payload: Optional[Mapping[str, Any]]) -> list:
    """Public helper to infer missing info items from request payloads."""
    return _derive_missing_info(payload)


def _normalize_level(value: Any, default: str = "medium") -> str:
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _LEVEL_ALLOWED:
            return lowered
    return default


def _sanitize_str_list(value: Any, *, max_items: int = 10) -> list:
    return [_escape(v) for v in _coerce_list(value)[:max_items]]


def _sanitize_based_on_available_information(
    value: Any, missing_info: Sequence[str]
) -> str:
    if isinstance(value, list):
        parts = [_escape(v) for v in value[:2] if _escape(v)]
        if parts:
            return " ".join(parts)
    text = _escape(value) if value is not None else ""
    if text:
        return text
    if missing_info:
        missing_preview = ", ".join(missing_info[:3])
        return (
            "הניתוח מבוסס על מידע חלקי, ציבורי וכללי בלבד לגבי הדגם/השנה. "
            f"חסרים גם פרטים מהותיים כגון {missing_preview}, ולכן אי אפשר להסיק ממנו תמונה מלאה על הרכב הספציפי."
        )
    return (
        "הניתוח מבוסס על מידע חלקי, ציבורי וכללי בלבד לגבי הדגם/השנה. "
        "ללא בדיקה פיזית, תיעוד מלא והיסטוריית שימוש, אי אפשר להסיק ממנו תמונה מלאה על הרכב הספציפי."
    )


def _sanitize_key_risk_areas(value: Any) -> list:
    items = []
    for row in _coerce_list(value)[:6]:
        if isinstance(row, dict):
            risk_area = _escape(row.get("risk_area") or row.get("risk_title") or "")
            why_to_check = _escape(
                row.get("why_to_check")
                or row.get("why_it_matters")
                or row.get("how_to_check")
                or ""
            )
        else:
            risk_area = _escape(row)
            why_to_check = ""
        if risk_area:
            items.append({"risk_area": risk_area, "why_to_check": why_to_check})
    for default in _DEFAULT_RISK_AREAS:
        if len(items) >= 4:
            break
        items.append(default)
    return items[:6]


def _sanitize_decision_checklist(value: Any) -> Dict[str, Any]:
    src = _coerce_dict(value)
    legacy_src = _coerce_dict(src.get("buyer_checklist"))
    active_src = src or legacy_src
    return {
        "mechanical_inspection_points": _sanitize_str_list(
            active_src.get("mechanical_inspection_points")
            or active_src.get("inspection_focus"),
            max_items=10,
        )
        or list(_DEFAULT_DECISION_CHECKLIST["mechanical_inspection_points"]),
        "documents_to_verify": _sanitize_str_list(
            active_src.get("documents_to_verify"),
            max_items=10,
        )
        or list(_DEFAULT_DECISION_CHECKLIST["documents_to_verify"]),
        "questions_to_ask_seller": _sanitize_str_list(
            active_src.get("questions_to_ask_seller")
            or active_src.get("ask_seller"),
            max_items=10,
        )
        or list(_DEFAULT_DECISION_CHECKLIST["questions_to_ask_seller"]),
        "red_flags_to_look_for": _sanitize_str_list(
            active_src.get("red_flags_to_look_for")
            or active_src.get("walk_away_signs"),
            max_items=10,
        )
        or list(_DEFAULT_DECISION_CHECKLIST["red_flags_to_look_for"]),
    }


def _sanitize_known_uncertainties(value: Any, missing_info: Sequence[str]) -> list:
    items = _sanitize_str_list(value, max_items=10)
    if not items:
        items = [f"לא ידוע: {item}" for item in missing_info[:4]]
    if not items:
        items = list(_DEFAULT_KNOWN_UNCERTAINTIES)
    for default in _DEFAULT_KNOWN_UNCERTAINTIES:
        if len(items) >= 4:
            break
        items.append(default)
    return items[:8]


def _sanitize_estimated_cost_sensitivity(value: Any, src: Mapping[str, Any]) -> list:
    items = _sanitize_str_list(value, max_items=6)
    if not items:
        legacy_cost = _coerce_dict(src.get("expected_ownership_cost"))
        legacy_range = _escape(legacy_cost.get("typical_yearly_range_ils") or "")
        legacy_notes = _escape(legacy_cost.get("notes") or "")
        if legacy_range:
            items.append(
                f"פוטנציאל העלויות עשוי להשתנות משמעותית לפי מצב הרכב בפועל (למשל {legacy_range} אם ההערכה הישנה עדיין רלוונטית לרכב שנבדק)."
            )
        if legacy_notes:
            items.append(legacy_notes)
    if not items:
        items = list(_DEFAULT_COST_SENSITIVITY)
    return items[:6]


def sanitize_reliability_report_response(
    response: Any,
    missing_info: Optional[Sequence[str]] = None,
    payload: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Sanitize AI response for the vehicle reliability report (strict JSON schema).
    """
    src = _coerce_dict(response)
    inferred_missing = list(missing_info) if missing_info else _derive_missing_info(payload)
    inferred_missing = [_escape(m) for m in inferred_missing][:10]

    out: Dict[str, Any] = {}
    if isinstance(src.get("available"), bool):
        out["available"] = bool(src.get("available"))
        if src.get("available") is False:
            if "reason" in src:
                out["reason"] = _escape(src.get("reason"))
            return out
    out["based_on_available_information"] = _sanitize_based_on_available_information(
        src.get("based_on_available_information"),
        inferred_missing,
    )
    out["key_risk_areas_to_examine"] = _sanitize_key_risk_areas(
        src.get("key_risk_areas_to_examine") or src.get("top_risks")
    )
    out["what_must_be_checked_before_a_decision"] = _sanitize_decision_checklist(
        src.get("what_must_be_checked_before_a_decision") or src.get("buyer_checklist")
    )
    out["missing_info"] = inferred_missing
    out["known_uncertainties"] = _sanitize_known_uncertainties(
        src.get("known_uncertainties") or src.get("missing_info"),
        inferred_missing,
    )
    out["estimated_cost_sensitivity"] = _sanitize_estimated_cost_sensitivity(
        src.get("estimated_cost_sensitivity"),
        src,
    )
    out["final_line"] = _RELIABILITY_REPORT_FINAL_LINE

    return out


# -----------------------------
# Research refactor 2026-04-25
# -----------------------------

import re as _re_mod

_PII_PATTERNS = [
    _re_mod.compile(r'\b\d{2,3}-?\d{2,3}-?\d{2,3}\b'),  # Israeli license plate
    _re_mod.compile(r'\b0\d{1,2}-?\d{7}\b'),  # Israeli phone
    _re_mod.compile(r'\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b'),  # email
]


def _contains_pii_strings(s: str) -> bool:
    """Check if string contains PII patterns (license plates, phones, emails)."""
    if not isinstance(s, str):
        return False
    for pattern in _PII_PATTERNS:
        if pattern.search(s):
            return True
    return False


def sanitize_profile_for_storage(profile_json: dict) -> dict:
    """
    Sanitize advisor profile for storage by removing PII and sensitive fields.
    Returns a new dict with only allowed fields.
    """
    allowed_keys = {
        "budget", "min_year", "max_year", "fuel_preference", "transmission_preference",
        "main_use", "annual_km_bucket", "body_style", "family_size_bucket",
        "cargo_need", "maintenance_sensitivity", "comfort_importance",
        "performance_importance", "reliability_importance", "safety_importance",
        "age_bucket", "license_years_bucket",
    }
    
    result = {}
    
    for key in allowed_keys:
        if key in profile_json:
            result[key] = profile_json[key]
    
    # Convert exact age to bucket
    if "driver_age" in profile_json or "age" in profile_json:
        age = profile_json.get("driver_age") or profile_json.get("age")
        if age is not None:
            try:
                age_int = int(age)
                if age_int < 25:
                    result["age_bucket"] = "17-24"
                elif age_int < 35:
                    result["age_bucket"] = "25-34"
                elif age_int < 45:
                    result["age_bucket"] = "35-44"
                elif age_int < 55:
                    result["age_bucket"] = "45-54"
                else:
                    result["age_bucket"] = "55+"
            except (ValueError, TypeError):
                pass
    
    # Convert exact license_years to bucket
    if "license_years" in profile_json:
        lic_years = profile_json["license_years"]
        if lic_years is not None:
            try:
                lic_int = int(lic_years)
                if lic_int <= 1:
                    result["license_years_bucket"] = "0-1"
                elif lic_int <= 5:
                    result["license_years_bucket"] = "2-5"
                elif lic_int <= 10:
                    result["license_years_bucket"] = "6-10"
                else:
                    result["license_years_bucket"] = "10+"
            except (ValueError, TypeError):
                pass
    
    return result


def sanitize_context_for_ai(context: dict) -> dict:
    """
    Sanitize context for AI reasoning. Keeps only reasoning-relevant keys.
    """
    allowed_keys = {
        "current_or_previous_vehicle", "ownership_duration_bucket", "annual_km_bucket",
        "main_use", "maintenance_sensitivity", "had_major_faults", "satisfaction_score",
        "would_buy_again", "actual_fuel_consumption_bucket", "family_size_bucket",
        "cargo_need",
    }
    
    result = {}
    for key in allowed_keys:
        if key in context:
            val = context[key]
            # Truncate strings to 64 chars
            if isinstance(val, str):
                val = val[:64]
            result[key] = val
    
    return result


def sanitize_research_answer(question_key: str, answer):
    """
    Validate and sanitize a research answer based on its question key.
    Raises ValidationError if invalid.
    """
    from app.utils.validation import ValidationError
    
    # Define all allowed question keys
    allowed_keys = {
        # Owner profile flow
        "has_current_vehicle", "make", "model", "year", "fuel_type", "transmission",
        "mileage_bucket", "ownership_duration_bucket", "had_major_faults",
        "satisfaction_score", "would_buy_again", "actual_fuel_consumption_bucket",
        "main_use", "annual_km_bucket", "notes",
        # Reliability flow
        "ownership_status", "garage_type",
        # Compare flow
        "subject_vehicle_slot",
        # Advisor flow
        "sale_timeline_bucket", "ask_to_sale_gap_bucket", "purchase_reference_type",
        "purchase_delta_bucket", "charging_location",
    }
    
    if question_key not in allowed_keys:
        raise ValidationError(question_key, f"Unknown question key: {question_key}")
    
    # Enum validation
    enum_map = {
        "ownership_status": {"owner", "pre_purchase_research"},
        "garage_type": {"authorized", "independent", "both"},
        "subject_vehicle_slot": {"car_1", "car_2", "car_3", "unknown"},
        "sale_timeline_bucket": {"under_14_days", "14_to_30_days", "31_to_60_days", "over_60_days", "not_sold"},
        "ask_to_sale_gap_bucket": {"under_5_pct", "5_to_10_pct", "10_to_15_pct", "over_15_pct", "not_sold"},
        "purchase_reference_type": {"price_list", "published_ad"},
        "purchase_delta_bucket": {"below_5_pct", "within_5_pct", "5_to_10_pct", "over_10_pct", "unknown"},
        "charging_location": {"home", "work", "public", "mixed"},
        "mileage_bucket": {"0-50k", "50k-100k", "100k-150k", "150k-200k", "200k+", "unknown"},
        "ownership_duration_bucket": {"less_than_6_months", "6_12_months", "1_2_years", "2_4_years", "4_plus_years"},
        "annual_km_bucket": {"0-10000", "10000-15000", "15000-20000", "20000-30000", "30000+"},
        "actual_fuel_consumption_bucket": {"very_low", "low", "average", "high", "very_high"},
        "fuel_type": {"gasoline", "diesel", "hybrid", "electric", "lpg", "other"},
        "transmission": {"manual", "automatic", "cvt", "dual_clutch", "other"},
        "main_use": {"city", "highway", "mixed", "other"},
    }
    
    if question_key in enum_map:
        if not isinstance(answer, str):
            raise ValidationError(question_key, "Expected string value")
        if answer not in enum_map[question_key]:
            raise ValidationError(question_key, f"Invalid value: {answer}")
        return answer
    
    # Boolean validation
    bool_keys = {"has_current_vehicle", "had_major_faults", "would_buy_again"}
    if question_key in bool_keys:
        if not isinstance(answer, bool):
            raise ValidationError(question_key, "Expected boolean value")
        return answer
    
    # Integer validation (satisfaction_score, year)
    if question_key == "satisfaction_score":
        try:
            val = int(answer)
            if val < 1 or val > 10:
                raise ValidationError(question_key, "satisfaction_score must be 1-10")
            return val
        except (ValueError, TypeError):
            raise ValidationError(question_key, "satisfaction_score must be an integer")
    
    if question_key == "year":
        try:
            val = int(answer)
            if val < 1900 or val > 2030:
                raise ValidationError(question_key, "year must be between 1900 and 2030")
            return val
        except (ValueError, TypeError):
            raise ValidationError(question_key, "year must be an integer")
    
    # String validation (make, model, notes)
    if question_key in {"make", "model"}:
        if not isinstance(answer, str):
            raise ValidationError(question_key, "Expected string value")
        if len(answer) > 100:
            raise ValidationError(question_key, "Value too long (max 100 chars)")
        if _contains_pii_strings(answer):
            raise ValidationError(question_key, "Contains prohibited information")
        return answer
    
    # Free text (notes) - max 200 chars and PII check
    if question_key == "notes":
        if not isinstance(answer, str):
            raise ValidationError(question_key, "Expected string value")
        if len(answer) > 200:
            raise ValidationError(question_key, "Notes too long (max 200 chars)")
        if _contains_pii_strings(answer):
            raise ValidationError(question_key, "Notes contain prohibited information (license plates, phone numbers, emails)")
        return answer
    
    # Default: accept as-is for unknown keys (shouldn't reach here due to allowlist)
    return answer
