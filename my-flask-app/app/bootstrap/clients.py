"""External-client initialization (Gemini AI + Google OAuth).

Extracted from ``app.factory.create_app`` (Phase 3 of the maintainability
refactor). Behaviour is preserved exactly — same env var names, same fallback
to ``None`` when the API key is missing, same OAuth client metadata.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from google import genai as genai3

import app.extensions as extensions
from app.extensions import oauth
from app.services.comparison.model_config import validate_comparison_model_config

if TYPE_CHECKING:
    from flask import Flask

_DEFAULT_LOGGER = logging.getLogger("app.bootstrap.clients")


def init_ai_clients(app: "Flask", logger: logging.Logger | None = None) -> None:
    """Initialize the Gemini AI clients on the shared ``extensions`` module.

    Sets both ``extensions.ai_client`` and ``extensions.advisor_client`` to the
    same Gemini client (or ``None`` when ``GEMINI_API_KEY`` is missing /
    initialization fails). Mirrors the legacy in-line behaviour.
    """
    log = logger or app.logger or _DEFAULT_LOGGER
    validate_comparison_model_config(log)
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        log.warning("[AI] GEMINI_API_KEY missing")
        extensions.ai_client = None
        extensions.advisor_client = None
        return

    try:
        extensions.ai_client = genai3.Client(api_key=api_key)
        extensions.advisor_client = extensions.ai_client
        log.info("[AI] Gemini 3 client initialized")
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
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
        api_base_url="https://www.googleapis.com/oauth2/v1/",
        userinfo_endpoint="https://openidconnect.googleapis.com/v1/userinfo",
        claims_options={
            "iss": {"values": ["https://accounts.google.com", "accounts.google.com"]}
        },
    )
