# -*- coding: utf-8 -*-
"""Admin-only operational health endpoints."""

import hashlib
import logging
import os
import sys
import time
import uuid

from flask import Blueprint
from flask_login import login_required

from app.extensions import GEMINI_RELIABILITY_MODEL_ID, GEMINI_RECOMMENDER_MODEL_ID
from app.services.comparison.model_config import (
    comparison_fallback_model_id,
    comparison_low_cost_model_id,
    comparison_stage_a_model_id,
    comparison_stage_a_repair_model_id,
    comparison_stage_b_model_id,
)
from app.config import ALLOW_EXTERNAL_SEARCH_GROUNDING, WEB_GROUNDING_PROVIDER
from app.services.gemini_grounding_client import (
    GROUNDING_PERMISSION_DENIED_CODE,
    GROUNDING_PERMISSION_DENIED_HE_MESSAGE,
    PROVIDER_FAILED_CODE,
    PROVIDER_HE_MESSAGE,
    _safe_gemini_error_details,
    _is_verbose,
    call_grounded_model,
    call_plain_model,
)
from app.services.gemini_health_verdict import (
    classify_gemini_health,
    log_health_verdict,
    set_last_health_root_cause,
)
from app.utils.auth_helpers import owner_required
from app.utils.http_helpers import api_ok
import app.extensions as extensions
from google import genai as genai3
from google.genai import types as genai_types

logger = logging.getLogger(__name__)

bp = Blueprint("admin", __name__)

_HEALTH_CHECK_PROMPT_PLAIN = "Return OK"
_HEALTH_CHECK_PROMPT_GROUNDED = "Search Google for the current year and return OK"


def _key_info_safe() -> dict:
    """Return safe key metadata; never exposes raw values."""
    gemini_key = os.environ.get("GEMINI_API_KEY") or ""
    google_key = os.environ.get("GOOGLE_API_KEY") or ""
    if gemini_key:
        source = "GEMINI_API_KEY"
        # SHA256 used as a non-reversible diagnostic fingerprint only — not for password storage.
        fingerprint = "sha256:" + hashlib.sha256(gemini_key.encode()).hexdigest()[:16]
    elif google_key:
        source = "GOOGLE_API_KEY"
        # SHA256 used as a non-reversible diagnostic fingerprint only — not for password storage.
        fingerprint = "sha256:" + hashlib.sha256(google_key.encode()).hexdigest()[:16]
    else:
        source = "none"
        fingerprint = "none"
    return {
        "gemini_api_key_present": bool(gemini_key),
        "google_api_key_present": bool(google_key),
        "selected_key_source": source,
        "selected_key_fingerprint": fingerprint,
    }


def _sdk_info() -> dict:
    try:
        import importlib.metadata
        genai_ver = importlib.metadata.version("google-genai")
    except Exception:
        genai_ver = "unknown"
    return {
        "google_genai_version": genai_ver,
        "python_version": sys.version,
    }


def _run_check(fn, label: str, *, api_method: str, endpoint_family: str, tools: list, diagnostic_only: bool = False) -> dict:
    """Run a single health check callable; return safe check result dict.

    Records the method/endpoint/tools used and the call duration so the matrix
    is self-describing. Never includes secrets.
    """
    meta = {
        "method_name": api_method,
        "endpoint_family": endpoint_family,
        "tools": tools,
        "diagnostic_only": diagnostic_only,
    }
    start = time.perf_counter()
    try:
        result = fn()
        err = result.get("error_code") if isinstance(result, dict) else "EMPTY_RESULT"
        text = (result.get("text") or "") if isinstance(result, dict) else ""
        ok = not err and bool(text.strip())
        details = result.get("error_details") if isinstance(result, dict) else None
        check: dict = {"ok": ok}
        check.update(meta)
        if not ok:
            status_code = None
            if isinstance(details, dict):
                status_code = details.get("response_status_code") or details.get("status_code")
            check["status_code"] = status_code
            check["error_type"] = err
            if _is_verbose() and details:
                check["error_summary"] = str(details)[:400]
            else:
                check["error_summary"] = str(err)[:200] if err else None
        else:
            check["status_code"] = None
            check["error_type"] = None
            check["error_summary"] = None
        check["duration_ms"] = int((time.perf_counter() - start) * 1000)
        return check
    except Exception as exc:
        _safe_gemini_error_details(exc)
        check = {
            "ok": False,
            "status_code": None,
            "error_type": type(exc).__name__,
            "error_summary": str(exc)[:200],
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }
        check.update(meta)
        return check


def _run_generate_content_grounded_legacy(model_id: str, prompt: str) -> dict:
    """Run legacy generateContent with GoogleSearch tool (diagnostic only, not product path)."""
    if extensions.ai_client is None:
        return {"text": "", "grounding_meta": {}, "error_code": "CLIENT_NOT_INITIALIZED", "error_details": {"type": "ClientNotInitialized"}}
    try:
        config = genai_types.GenerateContentConfig(
            tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
            temperature=0.0,
            max_output_tokens=32,
        )
        resp = extensions.ai_client.models.generate_content(
            model=model_id,
            contents=prompt,
            config=config,
        )
        text = getattr(resp, "text", "") or ""
        return {"text": str(text).strip(), "grounding_meta": {}, "error_code": None, "error_details": None}
    except Exception as exc:
        return {"text": "", "grounding_meta": {}, "error_code": "GENERATE_CONTENT_GROUNDED_LEGACY_ERROR", "error_details": _safe_gemini_error_details(exc)}


def _run_interactions_plain(model_id: str, prompt: str) -> dict:
    """Run interactions.create without grounding tools."""
    if extensions.ai_client is None:
        return {"text": "", "grounding_meta": {}, "error_code": "CLIENT_NOT_INITIALIZED", "error_details": {"type": "ClientNotInitialized"}}
    interactions = getattr(extensions.ai_client, "interactions", None)
    create = getattr(interactions, "create", None)
    if create is None:
        return {"text": "", "grounding_meta": {}, "error_code": "INTERACTIONS_UNAVAILABLE", "error_details": {"type": "InteractionsUnavailable"}}
    try:
        resp = create(model=model_id, input=prompt, store=False)
        text = getattr(resp, "output_text", None) or getattr(resp, "text", "") or ""
        return {"text": str(text).strip(), "grounding_meta": {}, "error_code": None, "error_details": None}
    except Exception as exc:
        return {"text": "", "grounding_meta": {}, "error_code": "INTERACTIONS_PLAIN_ERROR", "error_details": _safe_gemini_error_details(exc)}


def _determine_diagnosis(checks: dict) -> str:
    gc_plain = checks.get("generate_content_plain", {})
    int_plain = checks.get("interactions_plain", {})
    int_grounded = checks.get("interactions_grounded", {})
    gc_grounded_legacy = checks.get("generate_content_grounded_legacy", {})

    gc_plain_ok = gc_plain.get("ok", False)
    int_plain_ok = int_plain.get("ok", False)
    int_grounded_ok = int_grounded.get("ok", False)
    gc_grounded_legacy_ok = gc_grounded_legacy.get("ok", False)

    if not gc_plain_ok:
        return "GEMINI_KEY_OR_PROJECT_ACCESS_FAILED"
    if gc_plain_ok and not int_plain_ok:
        return "INTERACTIONS_ENDPOINT_PERMISSION_OR_SDK_ISSUE"
    if gc_plain_ok and int_plain_ok and not int_grounded_ok and not gc_grounded_legacy_ok:
        return "GOOGLE_SEARCH_GROUNDING_PERMISSION_DENIED"
    if gc_plain_ok and int_plain_ok and int_grounded_ok and not gc_grounded_legacy_ok:
        return "LEGACY_GROUNDING_PATH_FAILED_USE_INTERACTIONS"
    if gc_plain_ok and int_plain_ok and not int_grounded_ok and gc_grounded_legacy_ok:
        return "GOOGLE_SEARCH_GROUNDING_PERMISSION_DENIED"
    return "OK"


def _check_result(result, *, require_grounding: bool = False):
    err = result.get("error_code") if isinstance(result, dict) else "EMPTY_RESULT"
    text = (result.get("text") or "") if isinstance(result, dict) else ""
    meta = result.get("grounding_meta") if isinstance(result, dict) else {}
    grounding_ok = bool((meta or {}).get("grounding_successful"))
    ok = not err and bool(text.strip()) and (grounding_ok if require_grounding else True)
    error_code = err if err else (None if ok else "NO_GROUNDING_METADATA")
    return {
        "ok": ok,
        "error_code": error_code,
        "error": error_code,  # Backward-compatible alias.
        "error_details": (result.get("error_details") if isinstance(result, dict) else None),
    }


def _diagnosis(plain_check, grounded_check):
    if plain_check.get("ok") and not grounded_check.get("ok"):
        return {
            "code": GROUNDING_PERMISSION_DENIED_CODE,
            "message": GROUNDING_PERMISSION_DENIED_HE_MESSAGE,
        }
    if not plain_check.get("ok"):
        return {
            "code": PROVIDER_FAILED_CODE,
            "message": PROVIDER_HE_MESSAGE,
        }
    return {"code": None, "message": None}


@bp.route("/api/admin/gemini-health", methods=["GET"])
@login_required
@owner_required
def gemini_health():
    """Four-check Gemini health matrix: plain/grounded × generateContent/interactions."""
    model_id = GEMINI_RELIABILITY_MODEL_ID
    req_id = str(uuid.uuid4())[:8]

    # Check A: generateContent plain
    check_gc_plain = _run_check(
        lambda: call_plain_model(
            model_id,
            _HEALTH_CHECK_PROMPT_PLAIN,
            timeout_sec=15,
            max_output_tokens=16,
            temperature=0.0,
            feature="gemini_health",
            request_id=f"{req_id}-a",
        ),
        "generate_content_plain",
        api_method="generate_content_plain",
        endpoint_family="models.generateContent",
        tools=[],
    )

    # Check B: interactions plain
    check_int_plain = _run_check(
        lambda: _run_interactions_plain(model_id, _HEALTH_CHECK_PROMPT_PLAIN),
        "interactions_plain",
        api_method="interactions_plain",
        endpoint_family="interactions",
        tools=[],
    )

    # Check C: interactions grounded
    check_int_grounded = _run_check(
        lambda: call_grounded_model(
            model_id,
            _HEALTH_CHECK_PROMPT_GROUNDED,
            timeout_sec=20,
            feature="gemini_health",
            request_id=f"{req_id}-c",
        ),
        "interactions_grounded",
        api_method="interactions_grounded",
        endpoint_family="interactions",
        tools=["google_search"],
    )

    # Check D: legacy generateContent grounded (diagnostic_only, not product path)
    check_gc_grounded_legacy = _run_check(
        lambda: _run_generate_content_grounded_legacy(model_id, _HEALTH_CHECK_PROMPT_GROUNDED),
        "generate_content_grounded_legacy",
        api_method="generate_content_grounded_legacy",
        endpoint_family="models.generateContent",
        tools=["google_search"],
        diagnostic_only=True,
    )

    checks = {
        "generate_content_plain": check_gc_plain,
        "interactions_plain": check_int_plain,
        "interactions_grounded": check_int_grounded,
        "generate_content_grounded_legacy": check_gc_grounded_legacy,
    }

    overall_ok = all(c.get("ok") for c in checks.values())
    diagnosis = _determine_diagnosis(checks)

    key_info = _key_info_safe()
    verdict = classify_gemini_health(checks, key_info)
    set_last_health_root_cause(verdict.get("root_cause_class"))
    log_health_verdict(
        request_id=req_id,
        verdict=verdict,
        key_info=key_info,
        checks=checks,
    )

    return api_ok(
        {
            "ok": overall_ok,
            "key": key_info,
            "sdk": _sdk_info(),
            "models": {
                "reliability": GEMINI_RELIABILITY_MODEL_ID,
                "recommender": GEMINI_RECOMMENDER_MODEL_ID,
                "comparison_stage_a": comparison_stage_a_model_id(),
                "comparison_stage_a_repair": comparison_stage_a_repair_model_id(),
                "comparison_stage_b": comparison_stage_b_model_id(),
                "fallback_model": comparison_fallback_model_id(),
                "low_cost_model": comparison_low_cost_model_id(),
            },
            "checks": checks,
            "verdict": verdict,
            "diagnosis": diagnosis,
            "ops_note": (
                "Check Render env vars for GOOGLE_API_KEY and GEMINI_API_KEY. "
                "Prefer only GEMINI_API_KEY. "
                "Use a fresh Gemini Auth key from Google AI Studio. "
                "After redeploy, open /api/admin/gemini-health and interpret the matrix results. "
                "generate_content_plain=ok/interactions_plain=fail → INTERACTIONS_ENDPOINT_PERMISSION_OR_SDK_ISSUE. "
                "plain=ok/grounded=fail → GOOGLE_SEARCH_GROUNDING_PERMISSION_DENIED. "
                "all=fail → GEMINI_KEY_OR_PROJECT_ACCESS_FAILED. "
                "interactions_grounded=ok/legacy_grounded=fail → LEGACY_GROUNDING_PATH_FAILED_USE_INTERACTIONS."
            ),
            "configured_grounding": {
                "web_grounding_provider": WEB_GROUNDING_PROVIDER,
                "allow_external_search_grounding": ALLOW_EXTERNAL_SEARCH_GROUNDING,
            },
        }
    )
