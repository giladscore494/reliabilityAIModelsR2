"""Utilities for sanitizing inbound/outbound payloads.

This module centralizes defensive parsing of API responses so the rest of the
application can assume well-formed structures.

Changes made:
- Add strict sanitizer for score_breakdown with 6 expected keys and clamping
  values to the 1-10 range.
- Ensure sanitize_analyze_response uses the strict score_breakdown sanitizer.
- Allow advisor API responses to include search_performed and search_queries.
- Add backwards-compatible sanitize_advisor_response alias.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping, Optional


# -----------------------------
# Generic helpers
# -----------------------------

def _to_int(value: Any, default: int = 0) -> int:
    """Best-effort convert to int."""
    try:
        if isinstance(value, bool):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp(n: int, low: int, high: int) -> int:
    return max(low, min(high, n))


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _pick_allowed(src: Mapping[str, Any], allowed: Iterable[str]) -> Dict[str, Any]:
    return {k: src[k] for k in allowed if k in src}


# -----------------------------
# Score breakdown sanitization
# -----------------------------

SCORE_BREAKDOWN_KEYS = (
    "accuracy",
    "reliability",
    "safety",
    "clarity",
    "completeness",
    "helpfulness",
)


def sanitize_score_breakdown(value: Any) -> Dict[str, int]:
    """Strictly sanitize score_breakdown.

    - Always returns a dict with exactly the 6 expected keys.
    - Coerces values to int when possible.
    - Clamps each value to the 1-10 range.
    """

    src = _as_dict(value)
    out: Dict[str, int] = {}
    for key in SCORE_BREAKDOWN_KEYS:
        n = _to_int(src.get(key), default=1)
        out[key] = _clamp(n, 1, 10)
    return out


# -----------------------------
# Analyze response sanitization
# -----------------------------

def sanitize_analyze_response(payload: Any) -> Dict[str, Any]:
    """Sanitize the model analyze response."""

    src = _as_dict(payload)

    # Preserve existing behavior where possible but ensure score_breakdown is strict.
    out: Dict[str, Any] = {
        "overall_score": _clamp(_to_int(src.get("overall_score"), default=1), 1, 10),
        "explanation": src.get("explanation") if isinstance(src.get("explanation"), str) else "",
        "score_breakdown": sanitize_score_breakdown(src.get("score_breakdown")),
    }

    # Optional/extra fields (kept if present)
    if isinstance(src.get("flags"), list):
        out["flags"] = src["flags"]
    if isinstance(src.get("raw"), dict):
        out["raw"] = src["raw"]

    return out


# -----------------------------
# Advisor API response sanitization
# -----------------------------

def sanitize_advisor_api_response(payload: Any) -> Dict[str, Any]:
    """Sanitize responses returned from the advisor API."""

    src = _as_dict(payload)

    # These are the only top-level keys permitted to flow through.
    # NOTE: search_performed/search_queries added for client features.
    top_allowed = {
        "answer",
        "citations",
        "confidence",
        "metadata",
        "search_performed",
        "search_queries",
    }

    out: Dict[str, Any] = _pick_allowed(src, top_allowed)

    # Normalize common shapes
    if "answer" in out and not isinstance(out["answer"], str):
        out["answer"] = ""

    if "citations" in out and not isinstance(out["citations"], list):
        out["citations"] = []

    if "confidence" in out:
        # confidence is expected to be 0-1; clamp conservatively.
        try:
            conf = float(out["confidence"])
        except (TypeError, ValueError):
            conf = 0.0
        out["confidence"] = max(0.0, min(1.0, conf))

    if "search_performed" in out and not isinstance(out["search_performed"], bool):
        # Best-effort coercion
        sp = out["search_performed"]
        out["search_performed"] = bool(sp) if sp is not None else False

    if "search_queries" in out and not isinstance(out["search_queries"], list):
        out["search_queries"] = []

    if "metadata" in out and not isinstance(out["metadata"], dict):
        out["metadata"] = {}

    return out


# Backwards-compatible alias

def sanitize_advisor_response(payload: Any) -> Dict[str, Any]:
    """Backward-compatible wrapper for older call sites."""

    return sanitize_advisor_api_response(payload)
