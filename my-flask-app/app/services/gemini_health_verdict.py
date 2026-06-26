# -*- coding: utf-8 -*-
"""Deterministic Gemini health verdict.

This module turns the raw four-check Gemini health matrix into an explicit,
deterministic verdict that distinguishes between three failure classes:

* key / project basic-access problem
* code call-path problem (Interactions SDK shape, legacy grounding path, or
  product-flow prompt / schema / parsing)
* Google Search grounding permission / feature problem

The classification is a pure function (:func:`classify_gemini_health`) so it can
be unit tested without ever touching the real Gemini API. The module also holds
the last-known health root-cause class so product routes can log a
comparison-debug line that explains whether a product failure is a key problem
or a product-flow code/schema problem.

No secrets are logged or returned by anything in this module.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Stable root-cause class identifiers (deterministic verdict vocabulary).
ROOT_CAUSE_KEY_BASIC = "KEY_OR_PROJECT_BASIC_ACCESS_FAILED"
ROOT_CAUSE_INTERACTIONS = "INTERACTIONS_CALL_PATH_OR_PERMISSION_FAILED"
ROOT_CAUSE_GROUNDING = "GOOGLE_SEARCH_GROUNDING_PERMISSION_OR_FEATURE_FAILED"
ROOT_CAUSE_LEGACY_GROUNDING = "LEGACY_GROUNDING_CALL_PATH_FAILED"
ROOT_CAUSE_NO_ISSUE = "NO_GEMINI_HEALTH_ISSUE"
ROOT_CAUSE_PRODUCT_FLOW = "PRODUCT_FLOW_CODE_OR_SCHEMA_FAILED"
ROOT_CAUSE_BLOCKED_BY_GOOGLE = "ENVIRONMENT_OR_KEY_BLOCKED_BY_GOOGLE"
ROOT_CAUSE_INCONCLUSIVE = "INCONCLUSIVE"

_CHECK_KEYS = (
    "generate_content_plain",
    "interactions_plain",
    "interactions_grounded",
    "generate_content_grounded_legacy",
)

# Module-level memory of the last computed health root-cause class. Product
# routes read this to classify their own failures relative to Gemini health.
_LAST_LOCK = threading.Lock()
_last_root_cause_class: Optional[str] = None


def set_last_health_root_cause(root_cause_class: Optional[str]) -> None:
    """Record the most recent health-matrix root-cause class."""
    global _last_root_cause_class
    with _LAST_LOCK:
        _last_root_cause_class = root_cause_class


def get_last_health_root_cause() -> Optional[str]:
    """Return the most recent health-matrix root-cause class, or None."""
    with _LAST_LOCK:
        return _last_root_cause_class


def _ok(checks: Dict[str, Any], key: str) -> Optional[bool]:
    """Return the tri-state ok flag for a check (True / False / None=unknown)."""
    check = checks.get(key)
    if not isinstance(check, dict):
        return None
    if "ok" not in check:
        return None
    val = check.get("ok")
    if val is None:
        return None
    return bool(val)


def _status_code(checks: Dict[str, Any], key: str) -> Any:
    check = checks.get(key)
    if isinstance(check, dict):
        return check.get("status_code")
    return None


def _looks_like_generic_google_html(check: Any) -> bool:
    """Heuristic: a 403 whose body looks like generic Google HTML, not JSON."""
    if not isinstance(check, dict):
        return False
    if check.get("status_code") != 403:
        return False
    summary = (check.get("error_summary") or "")
    blob = summary.lower() if isinstance(summary, str) else str(summary).lower()
    return "<html" in blob or "text/html" in blob or "<!doctype html" in blob


def _all_403_generic_html(checks: Dict[str, Any]) -> bool:
    present = [checks.get(k) for k in _CHECK_KEYS if isinstance(checks.get(k), dict)]
    if len(present) < len(_CHECK_KEYS):
        return False
    if any(c.get("ok") for c in present):
        return False
    return all(_looks_like_generic_google_html(c) for c in present)


def classify_gemini_health(checks: Dict[str, Any], key_info: Dict[str, Any]) -> Dict[str, Any]:
    """Map the health matrix to a deterministic verdict.

    Pure function: depends only on ``checks`` (the four health-matrix results)
    and ``key_info`` (safe key metadata). Never calls Gemini and never reads
    secrets.

    ``checks`` keys: generate_content_plain, interactions_plain,
    interactions_grounded, generate_content_grounded_legacy. Each value is a
    dict that includes at least an ``ok`` boolean (and optionally
    ``status_code`` / ``error_summary`` for the 403-HTML rule).
    """
    gc_plain = _ok(checks, "generate_content_plain")
    int_plain = _ok(checks, "interactions_plain")
    int_grounded = _ok(checks, "interactions_grounded")
    legacy = _ok(checks, "generate_content_grounded_legacy")

    # Rule 7: every call fails with a generic Google 403 HTML body. This points
    # at an environment/key/project block rather than a single SDK call path.
    if _all_403_generic_html(checks):
        return {
            "root_cause_class": ROOT_CAUSE_BLOCKED_BY_GOOGLE,
            "is_key_problem": True,
            "is_code_call_path_problem": False,
            "is_grounding_permission_problem": False,
            "confidence": "medium",
            "explanation": (
                "All Gemini calls fail with generic Google 403 HTML. This points "
                "to key/project/environment restriction or Google-side block "
                "rather than a single SDK call path."
            ),
            "next_action": (
                "Verify key in AI Studio, remove stale GOOGLE_API_KEY, check "
                "Render fingerprint, generate a fresh Auth key, and test from "
                "local and Render with the same minimal request."
            ),
        }

    # Rule 8: any required check is unknown / not run -> cannot classify.
    if gc_plain is None or int_plain is None or int_grounded is None or legacy is None:
        return {
            "root_cause_class": ROOT_CAUSE_INCONCLUSIVE,
            "is_key_problem": None,
            "is_code_call_path_problem": None,
            "is_grounding_permission_problem": None,
            "confidence": "low",
            "explanation": (
                "The matrix produced mixed results that do not map to a known "
                "failure class."
            ),
            "next_action": (
                "Inspect individual check errors and compare "
                "endpoint/method/status/body."
            ),
        }

    # Rule 1: basic generateContent without tools fails -> key/project problem.
    if not gc_plain:
        return {
            "root_cause_class": ROOT_CAUSE_KEY_BASIC,
            "is_key_problem": True,
            "is_code_call_path_problem": False,
            "is_grounding_permission_problem": False,
            "confidence": "high",
            "explanation": (
                "Basic generateContent without tools failed, so the key/project "
                "cannot access basic Gemini from this environment. This is not a "
                "grounding-specific code path issue."
            ),
            "next_action": (
                "Replace GEMINI_API_KEY, verify selected_key_fingerprint changed "
                "after redeploy, remove GOOGLE_API_KEY, and check API key "
                "restrictions/project access."
            ),
        }

    # Rule 2: basic Gemini works, but Interactions without tools fails.
    if gc_plain and not int_plain:
        return {
            "root_cause_class": ROOT_CAUSE_INTERACTIONS,
            "is_key_problem": False,
            "is_code_call_path_problem": True,
            "is_grounding_permission_problem": False,
            "confidence": "high",
            "explanation": (
                "Basic Gemini works with this key, but the Interactions API call "
                "without tools fails. The key is valid for Gemini, but the "
                "Interactions endpoint or SDK call path is failing."
            ),
            "next_action": (
                "Inspect Interactions SDK call shape, endpoint version, "
                "google-genai version, and whether the project has Interactions "
                "access. Product code should not assume key failure."
            ),
        }

    # Rule 3: plain Gemini + plain Interactions work, grounded Interactions fails.
    if gc_plain and int_plain and not int_grounded:
        return {
            "root_cause_class": ROOT_CAUSE_GROUNDING,
            "is_key_problem": False,
            "is_code_call_path_problem": False,
            "is_grounding_permission_problem": True,
            "confidence": "high",
            "explanation": (
                "Plain Gemini and plain Interactions both work, but Interactions "
                "with google_search fails. The key works; the failure is specific "
                "to Google Search grounding permission/feature availability."
            ),
            "next_action": (
                "Do not replace the key again unless fingerprint is stale. Check "
                "Google Search grounding availability, project permissions, "
                "Gemini key type, API restrictions, and consider external search "
                "grounding fallback."
            ),
        }

    # Rule 4: Interactions grounding works but legacy generateContent grounding
    # fails -> only the legacy call path is broken.
    if gc_plain and int_plain and int_grounded and not legacy:
        return {
            "root_cause_class": ROOT_CAUSE_LEGACY_GROUNDING,
            "is_key_problem": False,
            "is_code_call_path_problem": True,
            "is_grounding_permission_problem": False,
            "confidence": "high",
            "explanation": (
                "Interactions grounding works but legacy generateContent "
                "grounding fails. The key and grounding feature work; only the "
                "legacy call path is broken."
            ),
            "next_action": (
                "Keep product flows on Interactions API and do not use legacy "
                "generateContent GoogleSearch path."
            ),
        }

    # Rule 5: everything passed.
    if gc_plain and int_plain and int_grounded and legacy:
        return {
            "root_cause_class": ROOT_CAUSE_NO_ISSUE,
            "is_key_problem": False,
            "is_code_call_path_problem": False,
            "is_grounding_permission_problem": False,
            "confidence": "high",
            "explanation": (
                "All Gemini health checks passed. If product flow still fails, "
                "the issue is in product-specific prompt/schema/parsing/business "
                "logic, not key or Gemini access."
            ),
            "next_action": (
                "Debug the product route using request_id, prompt size, schema "
                "parsing, JSON repair, and route-level error mapping."
            ),
        }

    # Defensive fallback: should be unreachable given the booleans above.
    return {
        "root_cause_class": ROOT_CAUSE_INCONCLUSIVE,
        "is_key_problem": None,
        "is_code_call_path_problem": None,
        "is_grounding_permission_problem": None,
        "confidence": "low",
        "explanation": (
            "The matrix produced mixed results that do not map to a known "
            "failure class."
        ),
        "next_action": (
            "Inspect individual check errors and compare "
            "endpoint/method/status/body."
        ),
    }


def classify_product_flow_failure(health_root_cause_class: Optional[str]) -> Dict[str, Any]:
    """Map a product-route failure relative to known Gemini health.

    If the health matrix is clean (``NO_GEMINI_HEALTH_ISSUE``) but the product
    route still fails, the failure is a product-flow code/schema problem, not a
    key failure (rule 6).
    """
    if health_root_cause_class == ROOT_CAUSE_NO_ISSUE:
        return {
            "root_cause_class": ROOT_CAUSE_PRODUCT_FLOW,
            "is_key_problem": False,
            "is_code_call_path_problem": True,
            "is_grounding_permission_problem": False,
            "confidence": "high",
            "explanation": (
                "The same Gemini model and grounding provider work in the health "
                "matrix, so the failure is in the product code path, prompt, "
                "parser, schema, timeout, or response handling."
            ),
            "next_action": (
                "Compare product call config against health call config. Log "
                "product api_method, tools, response_mime_type, prompt_chars, "
                "prompt_sha256, max_output_tokens, and parser error."
            ),
        }
    return {
        "root_cause_class": health_root_cause_class or ROOT_CAUSE_INCONCLUSIVE,
        "is_key_problem": None,
        "is_code_call_path_problem": None,
        "is_grounding_permission_problem": None,
        "confidence": "low",
        "explanation": (
            "Product route failure with no clean health baseline; consult the "
            "latest /api/admin/gemini-health verdict before drawing conclusions."
        ),
        "next_action": (
            "Run /api/admin/gemini-health and read its deterministic verdict "
            "before assuming key vs code call path."
        ),
    }


def _selected_key_fingerprint() -> str:
    """Return the safe key fingerprint for the active Gemini key (never raw)."""
    gemini_key = os.environ.get("GEMINI_API_KEY") or ""
    google_key = os.environ.get("GOOGLE_API_KEY") or ""
    key = gemini_key or google_key
    if not key:
        return "none"
    # SHA256 used as a non-reversible diagnostic fingerprint only.
    return "sha256:" + hashlib.sha256(key.encode()).hexdigest()[:16]


def log_health_verdict(
    *,
    request_id: str,
    verdict: Dict[str, Any],
    key_info: Dict[str, Any],
    checks: Dict[str, Any],
) -> None:
    """Emit the deterministic ``[AI_HEALTH_VERDICT]`` log line. No secrets."""
    log_data = {
        "request_id": request_id,
        "root_cause_class": verdict.get("root_cause_class"),
        "is_key_problem": verdict.get("is_key_problem"),
        "is_code_call_path_problem": verdict.get("is_code_call_path_problem"),
        "is_grounding_permission_problem": verdict.get("is_grounding_permission_problem"),
        "confidence": verdict.get("confidence"),
        "selected_key_source": key_info.get("selected_key_source"),
        "selected_key_fingerprint": key_info.get("selected_key_fingerprint"),
        "generate_content_plain_ok": _ok(checks, "generate_content_plain"),
        "interactions_plain_ok": _ok(checks, "interactions_plain"),
        "interactions_grounded_ok": _ok(checks, "interactions_grounded"),
        "generate_content_grounded_legacy_ok": _ok(checks, "generate_content_grounded_legacy"),
    }
    logger.info(
        "[AI_HEALTH_VERDICT] %s",
        json.dumps(log_data, ensure_ascii=False, sort_keys=True),
    )


def log_product_call_verdict_input(
    *,
    request_id: str,
    feature: str,
    model: str,
    api_method: str,
    endpoint_family: str,
    tools: List[str],
    response_mime_type: Optional[str] = None,
    prompt: Optional[str] = None,
    prompt_chars: Optional[int] = None,
) -> None:
    """Emit ``[AI_PRODUCT_CALL_VERDICT_INPUT]`` so product calls can be compared
    against the health matrix. No secrets, no full prompts."""
    if prompt is not None:
        prompt_chars = len(prompt)
        prompt_sha = hashlib.sha256(prompt.encode()).hexdigest()[:16]
    else:
        prompt_sha = None
    log_data = {
        "request_id": request_id,
        "feature": feature,
        "model": model,
        "api_method": api_method,
        "endpoint_family": endpoint_family,
        "tools": tools,
        "response_mime_type": response_mime_type,
        "prompt_chars": prompt_chars,
        "prompt_sha256_prefix": prompt_sha,
        "selected_key_fingerprint": _selected_key_fingerprint(),
        "health_last_known_root_cause_class": get_last_health_root_cause(),
    }
    logger.info(
        "[AI_PRODUCT_CALL_VERDICT_INPUT] %s",
        json.dumps(log_data, ensure_ascii=False, sort_keys=True),
    )
