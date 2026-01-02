"""Sanitization utilities.

This module is used to harden model/LLM output before returning it to the
front-end. It enforces *strict allowlisting* and *deep sanitization* to reduce
XSS/injection risk and to keep payload sizes bounded.

The front-end (my-flask-app/static/script.js) expects specific fields in the
/analyze response. Those fields are explicitly allowlisted in
`sanitize_analyze_response`.

Notes:
- We escape HTML in all strings.
- We clamp numeric ranges.
- We bound string lengths and list sizes.
- Unknown keys are dropped.
"""

from __future__ import annotations

from html import escape
from typing import Any, Dict, List, Optional


# -------------------------
# Generic sanitizers
# -------------------------

DEFAULT_MAX_STR_LEN = 1200
DEFAULT_MAX_LIST_ITEMS = 30
DEFAULT_MAX_NESTED_LIST_ITEMS = 50


def _to_str(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    # Avoid dumping very large objects; cast common scalar types.
    if isinstance(v, (int, float, bool)):
        return str(v)
    return str(v)


def sanitize_string(
    v: Any,
    *,
    max_len: int = DEFAULT_MAX_STR_LEN,
    allow_newlines: bool = True,
) -> str:
    """Escape and bound a string."""
    s = _to_str(v)
    if not allow_newlines:
        s = s.replace("\r", " ").replace("\n", " ")
    # Escape HTML special chars to prevent XSS.
    s = escape(s, quote=True)
    # Trim and bound.
    s = s.strip()
    if len(s) > max_len:
        s = s[:max_len]
    return s


def clamp_number(
    v: Any,
    *,
    min_value: float,
    max_value: float,
    as_int: bool = False,
    default: float = 0.0,
) -> float | int:
    try:
        n = float(v)
    except (TypeError, ValueError):
        n = float(default)
    if n < min_value:
        n = min_value
    if n > max_value:
        n = max_value
    if as_int:
        return int(round(n))
    return n


def sanitize_string_list(
    v: Any,
    *,
    max_items: int = DEFAULT_MAX_LIST_ITEMS,
    max_str_len: int = 500,
) -> List[str]:
    if not isinstance(v, list):
        return []
    out: List[str] = []
    for item in v[:max_items]:
        s = sanitize_string(item, max_len=max_str_len, allow_newlines=True)
        if s:
            out.append(s)
    return out


# -------------------------
# Deep sanitizers for known payloads
# -------------------------


def sanitize_issue_with_costs_item(v: Any) -> Dict[str, Any]:
    """Sanitize a single issues_with_costs entry.

    Expected shape:
    {
      issue: str,
      avg_cost_ILS: number,
      source: str,
      severity: str
    }
    """
    if not isinstance(v, dict):
        return {}

    # Keep strings short; these show in UI.
    issue = sanitize_string(v.get("issue", ""), max_len=250)
    source = sanitize_string(v.get("source", ""), max_len=120)
    severity = sanitize_string(v.get("severity", ""), max_len=60, allow_newlines=False)

    # Costs in ILS: clamp to a reasonable range.
    avg_cost_ils = clamp_number(v.get("avg_cost_ILS"), min_value=0, max_value=250000, default=0.0)

    out: Dict[str, Any] = {}
    if issue:
        out["issue"] = issue
    out["avg_cost_ILS"] = avg_cost_ils
    if source:
        out["source"] = source
    if severity:
        out["severity"] = severity
    return out


def sanitize_competitor_item(v: Any) -> Dict[str, Any]:
    """Sanitize a competitor brief entry.

    Expected shape:
    { model: str, brief_summary: str }
    """
    if not isinstance(v, dict):
        return {}
    model = sanitize_string(v.get("model", ""), max_len=120, allow_newlines=False)
    brief = sanitize_string(v.get("brief_summary", ""), max_len=500)

    out: Dict[str, Any] = {}
    if model:
        out["model"] = model
    if brief:
        out["brief_summary"] = brief
    return out


def sanitize_analyze_response(payload: Any) -> Dict[str, Any]:
    """Strictly allowlist and deeply sanitize the /analyze response."""
    if not isinstance(payload, dict):
        return {}

    out: Dict[str, Any] = {}

    # Simple scalar fields expected by the UI:
    out["base_score_calculated"] = clamp_number(
        payload.get("base_score_calculated"), min_value=0, max_value=100, default=0.0
    )

    source_tag = sanitize_string(payload.get("source_tag", ""), max_len=80, allow_newlines=False)
    if source_tag:
        out["source_tag"] = source_tag

    mileage_note = sanitize_string(payload.get("mileage_note", ""), max_len=300)
    if mileage_note:
        out["mileage_note"] = mileage_note

    rs = sanitize_string(payload.get("reliability_summary", ""), max_len=1200)
    if rs:
        out["reliability_summary"] = rs

    rss = sanitize_string(payload.get("reliability_summary_simple", ""), max_len=400)
    if rss:
        out["reliability_summary_simple"] = rss

    # Lists of strings:
    out["common_issues"] = sanitize_string_list(
        payload.get("common_issues"), max_items=25, max_str_len=300
    )
    out["recommended_checks"] = sanitize_string_list(
        payload.get("recommended_checks"), max_items=25, max_str_len=300
    )

    # Numeric summary:
    out["avg_repair_cost_ILS"] = clamp_number(
        payload.get("avg_repair_cost_ILS"), min_value=0, max_value=250000, default=0.0
    )

    # List of objects:
    issues = payload.get("issues_with_costs")
    issues_out: List[Dict[str, Any]] = []
    if isinstance(issues, list):
        for item in issues[:DEFAULT_MAX_NESTED_LIST_ITEMS]:
            s_item = sanitize_issue_with_costs_item(item)
            if s_item:
                issues_out.append(s_item)
    out["issues_with_costs"] = issues_out

    # Competitor briefs:
    competitors = payload.get("common_competitors_brief")
    competitors_out: List[Dict[str, Any]] = []
    if isinstance(competitors, list):
        for item in competitors[:20]:
            s_item = sanitize_competitor_item(item)
            if s_item:
                competitors_out.append(s_item)
    out["common_competitors_brief"] = competitors_out

    return out


# -------------------------
# Advisor response sanitizer
# -------------------------


def sanitize_advisor_response(payload: Any) -> Dict[str, Any]:
    """Sanitize advisor payload.

    Compatibility:
    - If this module (or an import cycle) defines a `sanitize_car_object` helper,
      delegate to it.
    - Otherwise fall back to a minimal safe allowlist to avoid leaking arbitrary
      LLM keys.

    The advisor payload is *not* the same as /analyze, but still must be
    strictly bounded and escaped.
    """

    # Delegate to existing logic if present (requested behavior).
    sco = globals().get("sanitize_car_object")
    if callable(sco):
        try:
            result = sco(payload)
            return result if isinstance(result, dict) else {}
        except Exception:
            # Fail closed.
            return {}

    if not isinstance(payload, dict):
        return {}

    # Minimal safe allowlist that covers typical advisor outputs without
    # permitting arbitrary nested structures.
    allow_scalar = {
        "title": 120,
        "summary": 1200,
        "recommendation": 800,
        "notes": 800,
        "source_tag": 80,
    }
    allow_list = {
        "recommended_checks": (25, 300),
        "warnings": (20, 250),
        "next_steps": (20, 250),
    }

    out: Dict[str, Any] = {}

    for k, max_len in allow_scalar.items():
        if k in payload:
            s = sanitize_string(payload.get(k), max_len=max_len)
            if s:
                out[k] = s

    for k, (max_items, max_str_len) in allow_list.items():
        if k in payload:
            out[k] = sanitize_string_list(payload.get(k), max_items=max_items, max_str_len=max_str_len)

    # Optional numeric confidence-like field
    if "confidence" in payload:
        out["confidence"] = clamp_number(payload.get("confidence"), min_value=0, max_value=1, default=0.0)

    return out
