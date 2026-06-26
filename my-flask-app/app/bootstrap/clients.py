"""External-client initialization (Gemini AI + Google OAuth).

Extracted from ``app.factory.create_app`` (Phase 3 of the maintainability
refactor). Behaviour is preserved exactly — same env var names, same fallback
to ``None`` when the API key is missing, same OAuth client metadata.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
from typing import TYPE_CHECKING

from google import genai as genai3

import app.extensions as extensions
from app.extensions import oauth
from app.services.comparison.model_config import (
    comparison_fallback_model_id,
    comparison_low_cost_model_id,
    comparison_stage_a_model_id,
    comparison_stage_a_repair_model_id,
    comparison_stage_b_model_id,
    validate_comparison_model_config,
)

if TYPE_CHECKING:
    from flask import Flask

_DEFAULT_LOGGER = logging.getLogger("app.bootstrap.clients")


def _key_fingerprint(key: str) -> str:
    """Return sha256 hex prefix of *key* — never the raw key."""
    if not key:
        return "none"
    digest = hashlib.sha256(key.encode()).hexdigest()
    return f"sha256:{digest[:16]}"


def _safe_pkg_version(pkg_name: str) -> str | None:
    try:
        import importlib.metadata
        return importlib.metadata.version(pkg_name)
    except Exception:
        return None


def _log_boot_diagnostics(log: logging.Logger) -> None:
    """Log safe boot diagnostics — never logs raw keys or secrets."""
    gemini_key = os.environ.get("GEMINI_API_KEY") or ""
    google_key = os.environ.get("GOOGLE_API_KEY") or ""

    gemini_present = bool(gemini_key)
    google_present = bool(google_key)

    if gemini_present:
        selected_source = "GEMINI_API_KEY"
        fingerprint = _key_fingerprint(gemini_key)
    elif google_present:
        selected_source = "GOOGLE_API_KEY"
        fingerprint = _key_fingerprint(google_key)
    else:
        selected_source = "none"
        fingerprint = "none"

    genai_version = _safe_pkg_version("google-genai")
    authlib_version = _safe_pkg_version("authlib")
    requests_version = _safe_pkg_version("requests")
    httpx_version = _safe_pkg_version("httpx")

    log.info(
        "[BOOT_DIAG] python=%s google_genai=%s authlib=%s requests=%s httpx=%s "
        "render_commit=%s render_service=%s "
        "gemini_api_key_present=%s google_api_key_present=%s "
        "selected_key_source=%s selected_key_fingerprint=%s",
        sys.version,
        genai_version,
        authlib_version,
        requests_version,
        httpx_version,
        os.environ.get("RENDER_GIT_COMMIT", "unknown"),
        os.environ.get("RENDER_SERVICE_NAME", "unknown"),
        gemini_present,
        google_present,
        selected_source,
        fingerprint,
    )

    log.info(
        "[BOOT_DIAG] configured_models reliability=%s recommender=%s "
        "comparison_stage_a=%s comparison_stage_a_repair=%s comparison_stage_b=%s "
        "fallback_model=%s low_cost_model=%s",
        extensions.GEMINI_RELIABILITY_MODEL_ID,
        extensions.GEMINI_RECOMMENDER_MODEL_ID,
        comparison_stage_a_model_id(),
        comparison_stage_a_repair_model_id(),
        comparison_stage_b_model_id(),
        comparison_fallback_model_id(),
        comparison_low_cost_model_id(),
    )

    if gemini_present and google_present:
        log.warning(
            "[BOOT_DIAG] HIGH: Both GEMINI_API_KEY and GOOGLE_API_KEY are set. "
            "Google SDKs may prefer GOOGLE_API_KEY unless client is initialized with explicit api_key."
        )


def init_ai_clients(app: "Flask", logger: logging.Logger | None = None) -> None:
    """Initialize the Gemini AI clients on the shared ``extensions`` module.

    Sets both ``extensions.ai_client`` and ``extensions.advisor_client`` to the
    same Gemini client (or ``None`` when ``GEMINI_API_KEY`` is missing /
    initialization fails). Mirrors the legacy in-line behaviour.
    """
    log = logger or app.logger or _DEFAULT_LOGGER
    validate_comparison_model_config(log)

    _log_boot_diagnostics(log)

    gemini_key = os.environ.get("GEMINI_API_KEY") or ""
    google_key = os.environ.get("GOOGLE_API_KEY") or ""

    if gemini_key:
        api_key = gemini_key
        key_source = "GEMINI_API_KEY"
    elif google_key:
        api_key = google_key
        key_source = "GOOGLE_API_KEY"
        log.warning("[AI] GEMINI_API_KEY absent — falling back to GOOGLE_API_KEY")
    else:
        log.warning("[AI] GEMINI_API_KEY missing")
        extensions.ai_client = None
        extensions.advisor_client = None
        return

    try:
        extensions.ai_client = genai3.Client(api_key=api_key)
        extensions.advisor_client = extensions.ai_client
        log.info("[AI] Gemini 3 client initialized key_source=%s", key_source)
    except Exception as e:  # pragma: no cover - defensive
        extensions.ai_client = None
        extensions.advisor_client = None
        log.error("[AI] Failed to init Gemini 3 client: %s", e)


def init_oauth(app: "Flask") -> None:
    """Register the Google OAuth client. Behaviour mirrors legacy create_app."""
    oauth.register(
        name="google",
        client_id=os.environ.get("GOOGLE_CLIENT_ID"),
        client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        access_token_url="https://oauth2.googleapis.com/token",
        api_base_url="https://www.googleapis.com/oauth2/v1/",
        client_kwargs={"scope": "email profile"},
    )
