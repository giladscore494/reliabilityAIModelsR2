import html
from typing import Any, Dict, List, Optional


# Conservative caps to mitigate prompt injection / response bloat
_MAX_LIST_ITEMS = 50
_MAX_STRING_LEN = 5000


def _escape_string(value: Any) -> str:
    """HTML-escape and length-cap any incoming string-like value."""
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    if len(value) > _MAX_STRING_LEN:
        value = value[:_MAX_STRING_LEN]
    return html.escape(value, quote=True)


def _clamp_number(value: Any, *, min_value: float = 0.0, max_value: float = 1.0) -> float:
    """Convert to float and clamp to a safe range."""
    try:
        num = float(value)
    except (TypeError, ValueError):
        num = min_value
    if num < min_value:
        return min_value
    if num > max_value:
        return max_value
    return num


def _sanitize_list(value: Any, item_sanitizer, *, max_items: int = _MAX_LIST_ITEMS) -> List[Any]:
    if not isinstance(value, list):
        return []
    out: List[Any] = []
    for item in value[:max_items]:
        try:
            out.append(item_sanitizer(item))
        except Exception:
            # Never allow sanitizer to throw and break response piping
            continue
    return out


def _sanitize_score_breakdown(value: Any) -> Dict[str, Any]:
    """Sanitize score_breakdown object for /analyze response.

    Expected shape is a mapping of keys -> {score: number, explanation: string}
    but we handle unknown keys defensively.
    """
    if not isinstance(value, dict):
        return {}

    sanitized: Dict[str, Any] = {}

    # Limit number of categories
    for k in list(value.keys())[:_MAX_LIST_ITEMS]:
        sk = _escape_string(k)
        v = value.get(k)
        if isinstance(v, dict):
            sanitized[sk] = {
                "score": _clamp_number(v.get("score"), min_value=0.0, max_value=1.0),
                "explanation": _escape_string(v.get("explanation", "")),
            }
        else:
            # If it's not an object, treat it as explanation
            sanitized[sk] = {
                "score": 0.0,
                "explanation": _escape_string(v),
            }

    return sanitized


def sanitize_analyze_response(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Sanitize response payload for /analyze.

    Preserve existing behavior (keys and general structure) but ensure all strings
    are HTML-escaped, numbers clamped, and arrays capped.
    """
    if not isinstance(data, dict):
        return {}

    # Preserve existing top-level fields (common ones) while sanitizing.
    # We leave unknown fields out to avoid widening attack surface.
    sanitized: Dict[str, Any] = {}

    if "analysis" in data:
        sanitized["analysis"] = _escape_string(data.get("analysis"))

    if "score" in data:
        sanitized["score"] = _clamp_number(data.get("score"), min_value=0.0, max_value=1.0)

    # New: score_breakdown sanitization
    if "score_breakdown" in data:
        sanitized["score_breakdown"] = _sanitize_score_breakdown(data.get("score_breakdown"))

    # Common supporting fields
    if "warnings" in data:
        sanitized["warnings"] = _sanitize_list(data.get("warnings"), _escape_string)

    if "errors" in data:
        sanitized["errors"] = _sanitize_list(data.get("errors"), _escape_string)

    if "metadata" in data and isinstance(data.get("metadata"), dict):
        # Escape only string leaves and cap size.
        md_in: Dict[str, Any] = data.get("metadata")
        md_out: Dict[str, Any] = {}
        for k in list(md_in.keys())[:_MAX_LIST_ITEMS]:
            sk = _escape_string(k)
            v = md_in.get(k)
            if isinstance(v, str) or v is None:
                md_out[sk] = _escape_string(v)
            elif isinstance(v, (int, float)):
                # Metadata numbers are not necessarily 0..1; clamp to a broad safe range.
                md_out[sk] = _clamp_number(v, min_value=-1e9, max_value=1e9)
            elif isinstance(v, bool):
                md_out[sk] = bool(v)
            else:
                # Fallback to escaped string representation
                md_out[sk] = _escape_string(v)
        sanitized["metadata"] = md_out

    return sanitized


# --- Advisor (/advisor or similar) sanitization ---

# Field allowlist derived from recommendations.js consumption.
# Intentionally strict: drop unknown fields.
_CAR_FIELD_ALLOWLIST = {
    "id",
    "name",
    "make",
    "model",
    "trim",
    "year",
    "price",
    "mileage",
    "body_type",
    "drivetrain",
    "transmission",
    "fuel_type",
    "mpg_city",
    "mpg_highway",
    "mpg_combined",
    "range_miles",
    "horsepower",
    "torque",
    "exterior_color",
    "interior_color",
    "image_url",
    "url",
    "dealer",
    "location",
    "highlights",
    "pros",
    "cons",
    "score",
    "reason",
}


def _sanitize_car(obj: Any) -> Dict[str, Any]:
    if not isinstance(obj, dict):
        return {}

    out: Dict[str, Any] = {}
    for k in list(obj.keys())[:_MAX_LIST_ITEMS]:
        if k not in _CAR_FIELD_ALLOWLIST:
            continue
        v = obj.get(k)

        # Numbers: clamp to broad sensible ranges. Keep ints as floats? preserve type where reasonable.
        if k in {
            "year",
            "price",
            "mileage",
            "mpg_city",
            "mpg_highway",
            "mpg_combined",
            "range_miles",
            "horsepower",
            "torque",
            "score",
        }:
            # score is commonly 0..1 or 0..100; we allow 0..100 here.
            if k == "score":
                out[k] = _clamp_number(v, min_value=0.0, max_value=100.0)
            elif k == "year":
                out[k] = int(_clamp_number(v, min_value=1885, max_value=2100))
            elif k in {"price", "mileage", "range_miles", "horsepower", "torque"}:
                out[k] = _clamp_number(v, min_value=0.0, max_value=1e9)
            else:
                out[k] = _clamp_number(v, min_value=0.0, max_value=1e4)
            continue

        # Lists of strings
        if k in {"highlights", "pros", "cons"}:
            out[k] = _sanitize_list(v, _escape_string, max_items=20)
            continue

        # Nested objects: escape their string leaves shallowly, cap size.
        if isinstance(v, dict):
            nested: Dict[str, Any] = {}
            for nk in list(v.keys())[:20]:
                nested[_escape_string(nk)] = _escape_string(v.get(nk))
            out[k] = nested
            continue

        # Booleans
        if isinstance(v, bool):
            out[k] = bool(v)
            continue

        # Default: string
        out[k] = _escape_string(v)

    return out


def sanitize_advisor_response(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Sanitize advisor response with strict allowlists.

    Allow only:
      - search_performed: bool
      - search_queries: list[str]
      - recommended_cars: list[car]

    Each car is allowlisted by _CAR_FIELD_ALLOWLIST.
    All strings are HTML-escaped, numbers clamped, and lists capped.
    """
    if not isinstance(data, dict):
        return {}

    out: Dict[str, Any] = {}

    if "search_performed" in data:
        out["search_performed"] = bool(data.get("search_performed"))

    if "search_queries" in data:
        out["search_queries"] = _sanitize_list(data.get("search_queries"), _escape_string, max_items=20)

    if "recommended_cars" in data:
        out["recommended_cars"] = _sanitize_list(data.get("recommended_cars"), _sanitize_car, max_items=20)

    return out
