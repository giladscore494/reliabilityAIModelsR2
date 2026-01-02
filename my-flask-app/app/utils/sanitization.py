"""Utility functions and allow-lists for sanitizing user/model supplied content.

This module uses strict allowlisting, escaping, and size limits to prevent
unexpected content from escaping into prompts, logs, templates, or downstream
systems.

Key principles:
- Strict allowlisting of top-level fields.
- Nested allowlisting for known structured fields.
- HTML escaping for all strings.
- Size limits (max string lengths, max list lengths, max dict keys).
- Type coercion only when safe; otherwise drop.

Note: This is not a general-purpose HTML sanitizer. It is a defensive
serialization/sanitization helper for structured JSON-like payloads.
"""

from __future__ import annotations

from html import escape
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union


# --------------------------
# Global limits / constants
# --------------------------

MAX_STRING_LENGTH = 4000
MAX_SHORT_STRING_LENGTH = 500
MAX_LIST_LENGTH = 200
MAX_DICT_KEYS = 200
MAX_RECURSION_DEPTH = 6


# --------------------------
# Allow-lists
# --------------------------

# Fields allowed in the "analyze" request payload / model output.
# Keep this list tight; add fields only with explicit sanitization rules.
ANALYZE_ALLOWED_FIELDS = {
    # existing / commonly used
    "car_details",
    "country",
    "currency",
    "language",
    "make",
    "model",
    "year",
    "trim",
    "mileage",
    "vin",
    "query",
    "analysis",
    "issues",
    "recommendations",
    "repair_costs",
    "sources",
    # newly added structured fields
    "base_score_calculated",
    "score_breakdown",
    "avg_repair_cost_ILS",
    "issues_with_costs",
    "reliability_summary",
    "reliability_summary_simple",
    "recommended_checks",
    "common_competitors_brief",
}


# --------------------------
# Primitive sanitizers
# --------------------------

def _clamp_string(s: str, max_len: int = MAX_STRING_LENGTH) -> str:
    if len(s) > max_len:
        return s[:max_len]
    return s


def sanitize_string(value: Any, *, max_len: int = MAX_STRING_LENGTH) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (int, float, bool)):
        value = str(value)
    if not isinstance(value, str):
        return None
    # escape HTML special chars; also avoids accidental template injection
    return _clamp_string(escape(value, quote=True), max_len=max_len)


def sanitize_number(value: Any) -> Optional[Union[int, float]]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    # allow numeric strings
    if isinstance(value, str):
        v = value.strip()
        if not v:
            return None
        try:
            if "." in v:
                return float(v)
            return int(v)
        except ValueError:
            return None
    return None


def sanitize_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"true", "1", "yes", "y"}:
            return True
        if v in {"false", "0", "no", "n"}:
            return False
    return None


# --------------------------
# Structured sanitizers
# --------------------------

def _sanitize_list(
    value: Any,
    item_sanitizer,
    *,
    max_len: int = MAX_LIST_LENGTH,
) -> Optional[List[Any]]:
    if value is None:
        return None
    if not isinstance(value, list):
        return None
    out: List[Any] = []
    for item in value[:max_len]:
        sanitized = item_sanitizer(item)
        if sanitized is None:
            continue
        out.append(sanitized)
    return out


def _sanitize_dict_keys(d: Mapping[str, Any]) -> List[str]:
    # Only string keys, escape and clamp; drop duplicates after sanitization
    keys: List[str] = []
    for k in d.keys():
        if not isinstance(k, str):
            continue
        ks = sanitize_string(k, max_len=MAX_SHORT_STRING_LENGTH)
        if not ks:
            continue
        if ks not in keys:
            keys.append(ks)
        if len(keys) >= MAX_DICT_KEYS:
            break
    return keys


def sanitize_freeform_dict(
    value: Any,
    *,
    depth: int = 0,
    max_keys: int = MAX_DICT_KEYS,
) -> Optional[Dict[str, Any]]:
    """Sanitize a dict with unknown schema.

    This is intentionally conservative: string values are escaped/clamped,
    numbers/bools allowed; nested lists/dicts are sanitized recursively with
    depth limit.
    """
    if value is None:
        return None
    if depth >= MAX_RECURSION_DEPTH:
        return None
    if not isinstance(value, Mapping):
        return None

    out: Dict[str, Any] = {}
    keys = _sanitize_dict_keys(value)
    for k in keys[:max_keys]:
        v = value.get(k)
        out[k] = sanitize_json_value(v, depth=depth + 1)
    return out


def sanitize_json_value(value: Any, *, depth: int = 0) -> Any:
    if value is None:
        return None
    if depth >= MAX_RECURSION_DEPTH:
        # stop recursion; stringify defensively
        return sanitize_string(value, max_len=MAX_SHORT_STRING_LENGTH)

    if isinstance(value, str):
        return sanitize_string(value)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, list):
        out: List[Any] = []
        for item in value[:MAX_LIST_LENGTH]:
            out.append(sanitize_json_value(item, depth=depth + 1))
        return out
    if isinstance(value, Mapping):
        # unknown structure: sanitize keys and values
        return sanitize_freeform_dict(value, depth=depth)

    # fallback: stringify
    return sanitize_string(value, max_len=MAX_SHORT_STRING_LENGTH)


# --------------------------
# Analyze payload sanitization
# --------------------------

# Nested allow-lists/schemas for newly added fields

# score_breakdown: list[ {category, score, notes?, weight?} ]
_SCORE_BREAKDOWN_ITEM_ALLOWED = {"category", "score", "notes", "weight"}

# issues_with_costs: list[ {issue, description?, severity?, est_cost_ILS?, cost_range_ILS?, sources?} ]
_ISSUE_WITH_COSTS_ALLOWED = {
    "issue",
    "description",
    "severity",
    "est_cost_ILS",
    "cost_range_ILS",
    "sources",
}

# recommended_checks: list[ {check, rationale?, priority?, estimated_cost_ILS?, notes?} ]
_RECOMMENDED_CHECKS_ALLOWED = {
    "check",
    "rationale",
    "priority",
    "estimated_cost_ILS",
    "notes",
}

# common_competitors_brief: list[ {make, model, years?, notes?} ]
_COMPETITOR_ALLOWED = {"make", "model", "years", "notes"}


def _sanitize_score_breakdown(value: Any) -> Optional[List[Dict[str, Any]]]:
    if not isinstance(value, list):
        return None

    out: List[Dict[str, Any]] = []
    for item in value[:MAX_LIST_LENGTH]:
        if not isinstance(item, Mapping):
            continue
        cleaned: Dict[str, Any] = {}
        for k in _SCORE_BREAKDOWN_ITEM_ALLOWED:
            if k not in item:
                continue
            if k in {"category"}:
                cleaned[k] = sanitize_string(item.get(k), max_len=MAX_SHORT_STRING_LENGTH)
            elif k in {"notes"}:
                cleaned[k] = sanitize_string(item.get(k), max_len=MAX_STRING_LENGTH)
            elif k in {"score", "weight"}:
                cleaned[k] = sanitize_number(item.get(k))
        # Require at least category or score to keep an entry
        if cleaned.get("category") or cleaned.get("score") is not None:
            out.append(cleaned)
    return out


def _sanitize_sources(value: Any) -> Optional[List[Dict[str, Any]]]:
    """Sanitize sources as list of dicts with tight schema.

    Accepts list items like:
      {"title": str, "url": str, "publisher": str, "date": str}
    Unknown keys are dropped.
    """
    if not isinstance(value, list):
        return None

    allowed = {"title", "url", "publisher", "date"}
    out: List[Dict[str, Any]] = []

    for item in value[:MAX_LIST_LENGTH]:
        if isinstance(item, str):
            # allow simple string sources too
            s = sanitize_string(item, max_len=MAX_STRING_LENGTH)
            if s:
                out.append({"title": s})
            continue
        if not isinstance(item, Mapping):
            continue

        cleaned: Dict[str, Any] = {}
        for k in allowed:
            if k not in item:
                continue
            cleaned[k] = sanitize_string(item.get(k), max_len=MAX_STRING_LENGTH)
        if cleaned:
            out.append(cleaned)

    return out


def _sanitize_issue_with_costs_list(value: Any) -> Optional[List[Dict[str, Any]]]:
    if not isinstance(value, list):
        return None

    out: List[Dict[str, Any]] = []
    for item in value[:MAX_LIST_LENGTH]:
        if not isinstance(item, Mapping):
            continue
        cleaned: Dict[str, Any] = {}
        for k in _ISSUE_WITH_COSTS_ALLOWED:
            if k not in item:
                continue
            if k in {"issue", "severity"}:
                cleaned[k] = sanitize_string(item.get(k), max_len=MAX_SHORT_STRING_LENGTH)
            elif k in {"description"}:
                cleaned[k] = sanitize_string(item.get(k), max_len=MAX_STRING_LENGTH)
            elif k in {"est_cost_ILS"}:
                cleaned[k] = sanitize_number(item.get(k))
            elif k in {"cost_range_ILS"}:
                # Accept dict with min/max
                cr = item.get(k)
                if isinstance(cr, Mapping):
                    cleaned[k] = {
                        "min": sanitize_number(cr.get("min")),
                        "max": sanitize_number(cr.get("max")),
                    }
                else:
                    cleaned[k] = None
            elif k in {"sources"}:
                cleaned[k] = _sanitize_sources(item.get(k))
        if cleaned.get("issue"):
            out.append(cleaned)
    return out


def _sanitize_recommended_checks(value: Any) -> Optional[List[Dict[str, Any]]]:
    if not isinstance(value, list):
        return None

    out: List[Dict[str, Any]] = []
    for item in value[:MAX_LIST_LENGTH]:
        if not isinstance(item, Mapping):
            continue
        cleaned: Dict[str, Any] = {}
        for k in _RECOMMENDED_CHECKS_ALLOWED:
            if k not in item:
                continue
            if k in {"check", "priority"}:
                cleaned[k] = sanitize_string(item.get(k), max_len=MAX_SHORT_STRING_LENGTH)
            elif k in {"rationale", "notes"}:
                cleaned[k] = sanitize_string(item.get(k), max_len=MAX_STRING_LENGTH)
            elif k in {"estimated_cost_ILS"}:
                cleaned[k] = sanitize_number(item.get(k))
        if cleaned.get("check"):
            out.append(cleaned)
    return out


def _sanitize_common_competitors(value: Any) -> Optional[List[Dict[str, Any]]]:
    if not isinstance(value, list):
        return None

    out: List[Dict[str, Any]] = []
    for item in value[:MAX_LIST_LENGTH]:
        if not isinstance(item, Mapping):
            continue
        cleaned: Dict[str, Any] = {}
        for k in _COMPETITOR_ALLOWED:
            if k not in item:
                continue
            if k in {"make", "model"}:
                cleaned[k] = sanitize_string(item.get(k), max_len=MAX_SHORT_STRING_LENGTH)
            elif k in {"years"}:
                if isinstance(item.get(k), list):
                    years: List[int] = []
                    for y in item.get(k)[:50]:
                        yn = sanitize_number(y)
                        if isinstance(yn, (int, float)):
                            yi = int(yn)
                            if 1900 <= yi <= 2100:
                                years.append(yi)
                    cleaned[k] = years
                else:
                    cleaned[k] = None
            elif k in {"notes"}:
                cleaned[k] = sanitize_string(item.get(k), max_len=MAX_STRING_LENGTH)
        if cleaned.get("make") and cleaned.get("model"):
            out.append(cleaned)
    return out


def sanitize_analyze_payload(payload: Any) -> Dict[str, Any]:
    """Sanitize a dict representing analyze request/model output.

    Drops any keys not in ANALYZE_ALLOWED_FIELDS.
    Applies nested sanitization for known structured fields.
    """
    if not isinstance(payload, Mapping):
        return {}

    result: Dict[str, Any] = {}

    for key in ANALYZE_ALLOWED_FIELDS:
        if key not in payload:
            continue

        value = payload.get(key)

        # Nested handling for specific fields
        if key in {"base_score_calculated", "avg_repair_cost_ILS"}:
            result[key] = sanitize_number(value)
        elif key in {"reliability_summary", "reliability_summary_simple"}:
            result[key] = sanitize_string(value, max_len=MAX_STRING_LENGTH)
        elif key == "score_breakdown":
            result[key] = _sanitize_score_breakdown(value)
        elif key == "issues_with_costs":
            result[key] = _sanitize_issue_with_costs_list(value)
        elif key == "sources":
            result[key] = _sanitize_sources(value)
        elif key == "recommended_checks":
            result[key] = _sanitize_recommended_checks(value)
        elif key == "common_competitors_brief":
            result[key] = _sanitize_common_competitors(value)
        else:
            # Default: sanitize common JSON-like types conservatively
            result[key] = sanitize_json_value(value)

    # Remove Nones to keep payload compact
    return {k: v for k, v in result.items() if v is not None}
