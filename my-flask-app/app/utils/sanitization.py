"""Utilities for sanitizing model responses.

The frontend expects stable response shapes. In the past we exposed
`sanitize_analyze_response` and `sanitize_advisor_response` helpers which
acted as backwards-compatible wrappers around the newer payload-based
sanitizers.

Some parts of the application (e.g. main.py imports) still rely on these
symbols existing.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# Existing (newer) sanitizer helpers
# ---------------------------------------------------------------------------

def _coerce_str(value: Any, *, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _coerce_dict(value: Any, *, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    return default or {}


def sanitize_analyze_payload(payload: Any) -> Dict[str, Any]:
    """Sanitize the payload returned by the "analyze" endpoint.

    This function enforces a minimal schema without being overly strict.
    """

    data = _coerce_dict(payload)

    return {
        "summary": _coerce_str(data.get("summary")),
        "reliability_score": data.get("reliability_score")
        if isinstance(data.get("reliability_score"), (int, float))
        else None,
        "issues": data.get("issues") if isinstance(data.get("issues"), list) else [],
        "recommendations": data.get("recommendations")
        if isinstance(data.get("recommendations"), list)
        else [],
        # pass-through/debug fields (safe)
        "raw": data.get("raw") if isinstance(data.get("raw"), (str, dict, list)) else None,
    }


def sanitize_advisor_payload(payload: Any) -> Dict[str, Any]:
    """Sanitize the payload returned by the "advisor" endpoint."""

    data = _coerce_dict(payload)

    return {
        "advisor_message": _coerce_str(
            data.get("advisor_message")
            or data.get("message")
            or data.get("advisor")
            or data.get("text")
        ),
        "action_items": data.get("action_items") if isinstance(data.get("action_items"), list) else [],
        "citations": data.get("citations") if isinstance(data.get("citations"), list) else [],
        "raw": data.get("raw") if isinstance(data.get("raw"), (str, dict, list)) else None,
    }


# ---------------------------------------------------------------------------
# Backwards-compatible wrapper functions (reintroduced)
# ---------------------------------------------------------------------------

def sanitize_analyze_response(response: Any) -> Dict[str, Any]:
    """Backwards-compatible wrapper for analyze responses.

    Historically, callers passed the model response directly and expected a
    dict compatible with the frontend's keys. The newer sanitizer operates on a
    payload.

    We delegate to `sanitize_analyze_payload` and then ensure the shape includes
    legacy keys that the frontend expects.
    """

    sanitized = sanitize_analyze_payload(response)

    # Ensure legacy/frontend-compatible keys exist (even if empty/None)
    # Some frontend versions used camelCase or different key names.
    summary = sanitized.get("summary", "")
    reliability_score = sanitized.get("reliability_score")
    issues = sanitized.get("issues", [])
    recommendations = sanitized.get("recommendations", [])

    return {
        # canonical keys
        "summary": summary,
        "reliability_score": reliability_score,
        "issues": issues,
        "recommendations": recommendations,
        # backwards-compatible aliases
        "reliabilityScore": reliability_score,
        "actionItems": recommendations,
        "raw": sanitized.get("raw"),
    }


def sanitize_advisor_response(response: Any) -> Dict[str, Any]:
    """Backwards-compatible wrapper for advisor responses.

    Preserve current behavior by delegating to existing sanitizer logic if
    present. If not present, apply a minimal allowlist schema.
    """

    # If there's an existing/legacy implementation in this module, prefer it.
    # (e.g. renamed helper kept for compatibility)
    legacy_impl = globals().get("_sanitize_advisor_response")
    if callable(legacy_impl):
        return legacy_impl(response)  # type: ignore[misc]

    # Otherwise delegate to the current payload-based sanitizer if available.
    if "sanitize_advisor_payload" in globals() and callable(globals()["sanitize_advisor_payload"]):
        return sanitize_advisor_payload(response)

    # Minimal allowlist fallback (similar to older versions)
    data = _coerce_dict(response)
    return {
        "advisor_message": _coerce_str(
            data.get("advisor_message")
            or data.get("message")
            or data.get("advisor")
            or data.get("text")
        ),
        "action_items": data.get("action_items") if isinstance(data.get("action_items"), list) else [],
        "citations": data.get("citations") if isinstance(data.get("citations"), list) else [],
        "raw": data.get("raw") if isinstance(data.get("raw"), (str, dict, list)) else None,
    }
