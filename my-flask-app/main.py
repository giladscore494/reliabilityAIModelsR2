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
from typing import Any, Dict, Optional


# -----------------------------
# basic coercion + escaping
# -----------------------------

_MAX_STR = 8000
_MAX_LIST = 50


def _escape(s: Any) -> str:
    if not isinstance(s, str):
        s = "" if s is None else str(s)
    if len(s) > _MAX_STR:
        s = s[:_MAX_STR]
    return html.escape(s, quote=True)


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

    # numbers
    if "base_score_calculated" in src:
        out["base_score_calculated"] = _clamp_int(src.get("base_score_calculated"), lo=0, hi=100, default=0)

    if "avg_repair_cost_ILS" in src:
        out["avg_repair_cost_ILS"] = _clamp_int(src.get("avg_repair_cost_ILS"), lo=0, hi=1_000_000, default=0)

    # strings
    for k in ("source_tag", "mileage_note", "reliability_summary", "reliability_summary_simple"):
        if k in src:
            out[k] = _escape(src.get(k))

    # lists of strings
    def _sanitize_str_list(v: Any, *, max_items: int = _MAX_LIST) -> list:
        arr = _coerce_list(v)[:max_items]
        return [_escape(x) for x in arr]

    if "common_issues" in src:
        out["common_issues"] = _sanitize_str_list(src.get("common_issues"), max_items=25)

    if "recommended_checks" in src:
        out["recommended_checks"] = _sanitize_str_list(src.get("recommended_checks"), max_items=25)

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
