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

    if "avg_repair_cost_ILS" in src:
        out["avg_repair_cost_ILS"] = _clamp_int(src.get("avg_repair_cost_ILS"), lo=0, hi=1_000_000, default=0)

    # strings
    for k in ("source_tag", "mileage_note", "reliability_summary", "reliability_summary_simple"):
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
                        "url": _escape(item.get("url")),
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
# Reliability report (strict JSON spec)
# -----------------------------

_CONFIDENCE_ALLOWED = {"high", "medium", "low"}
_LEVEL_ALLOWED = {"low", "medium", "high"}


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


def _normalize_confidence(value: Any, missing_info: Sequence[str]) -> str:
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _CONFIDENCE_ALLOWED:
            return lowered
    if len(missing_info) >= 4:
        return "low"
    if missing_info:
        return "medium"
    return "high"


def _sanitize_top_risks(value: Any) -> list:
    risks_out = []
    allowed_values = {"low", "medium", "high"}
    for row in _coerce_list(value)[:6]:
        if not isinstance(row, dict):
            continue
        sev = str(row.get("severity", "")).strip().lower()
        impact = str(row.get("cost_impact", "")).strip().lower()
        risks_out.append(
            {
                "risk_title": _escape(row.get("risk_title")),
                "why_it_matters": _escape(row.get("why_it_matters")),
                "how_to_check": _escape(row.get("how_to_check")),
                "severity": sev if sev in allowed_values else "medium",
                "cost_impact": impact if impact in allowed_values else "medium",
            }
        )
    if len(risks_out) < 3:
        defaults = [
            {
                "risk_title": "היסטוריית טיפולים לא מלאה",
                "why_it_matters": "טיפולים שלא בוצעו בזמן מגדילים סיכון לתקלות במנוע ובגיר.",
                "how_to_check": "בקש חשבוניות טיפולים ומספר בעלים קודמים; ודא טיפול גדול אחרון.",
                "severity": "medium",
                "cost_impact": "medium",
            },
            {
                "risk_title": "מצב גיר ומנוע",
                "why_it_matters": "תקלות בגיר/מנוע הן היקרות ביותר ומורידות ערך רכב.",
                "how_to_check": "בבדיקת מוסך: סריקת מחשב, רעידות, החלקות הילוכים, הדלקת נורות.",
                "severity": "high",
                "cost_impact": "high",
            },
            {
                "risk_title": "שחיקת מתלים ובלמים",
                "why_it_matters": "שחיקה מתקדמת פוגעת בבטיחות וגורמת להוצאות מיידיות.",
                "how_to_check": "בדוק רעשים, זליגות, רפידות וצלחות; סיבוב גלגלים ובדיקה במוסך.",
                "severity": "medium",
                "cost_impact": "medium",
            },
        ]
        for item in defaults:
            if len(risks_out) >= 3:
                break
            risks_out.append(item)
    return risks_out[:6]


def _sanitize_str_list(value: Any, *, max_items: int = 10) -> list:
    return [_escape(v) for v in _coerce_list(value)[:max_items]]


def _sanitize_mileage_changes(value: Any) -> list:
    items = []
    for row in _coerce_list(value)[:5]:
        if not isinstance(row, dict):
            continue
        items.append(
            {
                "mileage_band": _escape(row.get("mileage_band")),
                "what_to_expect": _escape(row.get("what_to_expect")),
            }
        )
    if not items:
        items = [
            {"mileage_band": "עד 120k", "what_to_expect": "בצע בדיקת מוסך מלאה; לוודא טיפולים בזמן ורצועת תזמון אם רלוונטי."},
            {"mileage_band": "120k–180k", "what_to_expect": "לשים דגש על גיר, מערכת קירור ומתלים; לבדוק נזילות וצריכת שמן."},
            {"mileage_band": "מעל 180k", "what_to_expect": "לתמחר הוצאות מתלים/בלמים/גומיות; להימנע מרכב ללא היסטוריית טיפולים מוכחת."},
        ]
    return items[:5]


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
    out["overall_score"] = _clamp_int(
        src.get("overall_score", src.get("base_score_calculated")),
        lo=0,
        hi=100,
        default=0,
    )
    out["confidence"] = _normalize_confidence(src.get("confidence"), inferred_missing)
    out["one_sentence_verdict"] = _escape(src.get("one_sentence_verdict") or "")
    out["top_risks"] = _sanitize_top_risks(src.get("top_risks"))

    expected_cost_src = _coerce_dict(src.get("expected_ownership_cost"))
    out["expected_ownership_cost"] = {
        "maintenance_level": _normalize_level(expected_cost_src.get("maintenance_level")),
        "typical_yearly_range_ils": _escape(
            expected_cost_src.get("typical_yearly_range_ils") or "לא ידוע"
        ),
        "notes": _escape(
            expected_cost_src.get("notes")
            or "הערכה מבוססת מידע חלקי; ודא הצעת מחיר במוסך לפני קנייה."
        ),
    }

    buyer_src = _coerce_dict(src.get("buyer_checklist"))
    out["buyer_checklist"] = {
        "ask_seller": _sanitize_str_list(buyer_src.get("ask_seller"), max_items=10)
        or [
            "בקש היסטוריית טיפולים מלאה (חשבוניות ומוסכים)",
            "כמה בעלים היו ולמה נמכר",
            "האם הרכב עבר תאונות או תיקוני שלדה",
            "מתי הוחלפו בלמים, צמיגים ורצועת תזמון/שרשרת",
            "האם יש רעידות, נזילות או תקלות ידועות",
        ],
        "inspection_focus": _sanitize_str_list(buyer_src.get("inspection_focus"), max_items=10)
        or [
            "סריקת מחשב לאיתור תקלות בגיר/מנוע",
            "בדיקת נזילות שמן/מים ומערכת קירור",
            "בדיקת מתלים, בושינגים ובלמים תחת עומס",
            "בדיקת תיבת הילוכים בנסיעת מבחן בכל הילוך",
            "מדידת עובי צמיגים ובדיקת ייצור/סדקים",
        ],
        "walk_away_signs": _sanitize_str_list(buyer_src.get("walk_away_signs"), max_items=6)
        or [
            "אין היסטוריית טיפולים או סירוב להציג חשבוניות",
            "רעידות/החלקות בגיר או נורת אזהרה דולקת",
            "נזילות שמן/מים משמעותיות מתחת לרכב",
            "תיקוני שלדה או פגיעות בטיחות שלא דווחו",
        ],
    }

    out["what_changes_with_mileage"] = _sanitize_mileage_changes(src.get("what_changes_with_mileage"))
    out["recommended_next_step"] = {
        "action": _escape(
            _coerce_dict(src.get("recommended_next_step")).get("action")
            or "קבע בדיקת מוסך מלאה ודוח מחשב לפני התחייבות."
        ),
        "reason": _escape(
            _coerce_dict(src.get("recommended_next_step")).get("reason")
            or "הבדיקה תאמת מצב גיר/מנוע ותיתן הערכת עלויות ריאלית."
        ),
    }

    out["missing_info"] = inferred_missing

    return out
