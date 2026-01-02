"""Sanitization utilities.

This module provides strict, allowlist-based sanitization for JSON responses returned
by the API. It is designed to be in sync with the frontend expectations in
`my-flask-app/static/script.js`.

Key principles:
- Strict allowlists per endpoint/schema to avoid leaking unexpected fields.
- HTML escaping for all string values to reduce XSS risk when rendering in the UI.
- Size and depth limits to mitigate large payload / abuse.

NOTE: When updating this file, keep the allowlists aligned with the frontend and
API contracts.
"""

from __future__ import annotations

import html
from typing import Any, Dict, Iterable, List, Optional, Set, Union


# ---------------------------
# Limits / configuration
# ---------------------------

# Conservative payload limits to prevent excessively large responses.
_MAX_STRING_LENGTH = 10_000
_MAX_LIST_LENGTH = 200
_MAX_DICT_KEYS = 200
_MAX_DEPTH = 10


def _escape_string(value: str) -> str:
    """Escape HTML and apply a maximum string length."""
    if value is None:
        return value
    if not isinstance(value, str):
        value = str(value)
    if len(value) > _MAX_STRING_LENGTH:
        value = value[:_MAX_STRING_LENGTH]
    return html.escape(value, quote=True)


def _clamp_list(value: List[Any]) -> List[Any]:
    if len(value) > _MAX_LIST_LENGTH:
        return value[:_MAX_LIST_LENGTH]
    return value


def _clamp_dict(value: Dict[str, Any]) -> Dict[str, Any]:
    if len(value) > _MAX_DICT_KEYS:
        # Deterministic truncation
        keys = list(value.keys())[:_MAX_DICT_KEYS]
        return {k: value[k] for k in keys}
    return value


def _sanitize_any(
    value: Any,
    *,
    allowed_keys: Optional[Set[str]] = None,
    depth: int = 0,
) -> Any:
    """Recursively sanitize any JSON-serializable value.

    - Strings are HTML-escaped and length-limited.
    - Lists are length-limited; each element sanitized.
    - Dicts are key-allowlisted (if provided), key-count limited, and values sanitized.
    - Numbers/bools/None pass through.

    This is intentionally conservative.
    """
    if depth > _MAX_DEPTH:
        # Stop recursion; return a safe representation.
        return None

    if value is None:
        return None

    if isinstance(value, str):
        return _escape_string(value)

    if isinstance(value, (int, float, bool)):
        return value

    if isinstance(value, list):
        value = _clamp_list(value)
        return [_sanitize_any(v, allowed_keys=None, depth=depth + 1) for v in value]

    if isinstance(value, dict):
        value = _clamp_dict(value)
        out: Dict[str, Any] = {}
        for k, v in value.items():
            if not isinstance(k, str):
                k = str(k)
            # Escape keys too (very rare, but safe)
            k_esc = _escape_string(k)
            if allowed_keys is not None and k_esc not in allowed_keys:
                continue
            out[k_esc] = _sanitize_any(v, allowed_keys=None, depth=depth + 1)
        return out

    # Fallback: string representation (escaped)
    return _escape_string(str(value))


# ---------------------------
# /analyze response schema
# ---------------------------

# These fields are referenced by `my-flask-app/static/script.js` and must be present
# (if provided by the model/backend) after sanitization.
_ANALYZE_ALLOWED_FIELDS: Set[str] = {
    "base_score_calculated",
    "source_tag",
    "mileage_note",
    "reliability_summary",
    "reliability_summary_simple",
    "common_issues",
    "recommended_checks",
    "avg_repair_cost_ILS",
    "issues_with_costs",
    "common_competitors_brief",
    "score_breakdown",
}


def sanitize_analyze_response(payload: Any) -> Any:
    """Sanitize the /analyze API response.

    Accepts a dict-like payload; returns a dict containing only allowlisted fields.
    """
    if not isinstance(payload, dict):
        return {}
    return _sanitize_any(payload, allowed_keys=_ANALYZE_ALLOWED_FIELDS)


# ---------------------------
# /advisor_api response schema
# ---------------------------

# Recommended cars schema allowlist.
# Keep this aligned with any frontend consumption.
_RECOMMENDED_CAR_ALLOWED_FIELDS: Set[str] = {
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


def _is_method_field(field_name: str) -> bool:
    return field_name.endswith("_method")


def sanitize_recommended_car(item: Any) -> Dict[str, Any]:
    """Sanitize a single recommended car item.

    In addition to the explicit allowlist, permits "*_method" fields to allow the
    backend to describe calculation/estimation methods.
    """
    if not isinstance(item, dict):
        return {}

    # Filter keys first; allow *_method fields in addition to the allowlist.
    allowed = set(_RECOMMENDED_CAR_ALLOWED_FIELDS)
    for k in item.keys():
        if isinstance(k, str) and _is_method_field(k):
            allowed.add(k)

    return _sanitize_any(item, allowed_keys=allowed)


def sanitize_advisor_api_response(payload: Any) -> Any:
    """Sanitize the /advisor_api response.

    Expected shapes:
    - { "recommended_cars": [ { ... }, ... ], ... }

    We keep the top-level shape conservative: only preserve "recommended_cars"
    plus a minimal set of optional status/message fields if present.
    """
    if not isinstance(payload, dict):
        return {}

    top_allowed = {"recommended_cars", "message", "status", "error"}
    out: Dict[str, Any] = _sanitize_any(payload, allowed_keys=top_allowed)

    # Re-sanitize recommended_cars with item-level schema (because _sanitize_any
    # would sanitize nested dicts without an allowlist).
    rec = payload.get("recommended_cars")
    if isinstance(rec, list):
        rec = _clamp_list(rec)
        out["recommended_cars"] = [sanitize_recommended_car(x) for x in rec]
    elif rec is not None:
        out["recommended_cars"] = []

    return out
