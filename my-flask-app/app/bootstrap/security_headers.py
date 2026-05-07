"""Security-header / cache-control after_request handlers.

Extracted from app.factory as part of Phase 4 of the infra refactor. All
header values and conditions are preserved verbatim; only the call-site has
moved from inside ``create_app`` to this dedicated bootstrap module.
"""

from __future__ import annotations

import time as pytime
from typing import TYPE_CHECKING

from flask import g, request
from flask_login import current_user

from app.utils.http_helpers import get_request_id

if TYPE_CHECKING:
    import logging
    from flask import Flask


def register_security_headers(app: "Flask", *, is_render: bool, logger: "logging.Logger") -> None:
    """Register security/cache headers and structured response logging.

    Parameters
    ----------
    app : Flask
    is_render : bool
        True when running on Render (used to gate Strict-Transport-Security).
    logger : logging.Logger
    """

    @app.after_request
    def apply_security_headers(response):
        rid = get_request_id()
        response.headers.setdefault("X-Request-ID", rid)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault(
            "Permissions-Policy",
            "geolocation=(), microphone=(), camera=(), payment=()"
        )
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
        csp_nonce = getattr(g, "csp_nonce", "")
        nonce_directive = f"'nonce-{csp_nonce}'" if csp_nonce else "'unsafe-inline'"
        ph_script = app.config.get("_PH_CSP_SCRIPT", "")
        ph_connect = app.config.get("_PH_CSP_CONNECT", "")
        ph_script_src = f" https://{ph_script}" if ph_script else ""
        ph_connect_src = f" https://{ph_connect}" if ph_connect else ""
        response.headers.setdefault(
            "Content-Security-Policy",
            f"default-src 'self'; "
            f"script-src 'self' {nonce_directive} https://cdn.tailwindcss.com https://cdn.jsdelivr.net{ph_script_src}; "
            f"style-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://cdn.jsdelivr.net https://fonts.googleapis.com; "
            f"font-src 'self' https://fonts.gstatic.com; "
            f"img-src 'self' data: https://*.googleusercontent.com; "
            f"connect-src 'self' https://accounts.google.com https://www.googleapis.com https://openidconnect.googleapis.com https://generativelanguage.googleapis.com{ph_connect_src}; "
            f"frame-ancestors 'none'; "
            f"base-uri 'self'; "
            f"form-action 'self' https://accounts.google.com"
        )
        if is_render or request.is_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains; preload")

        # Structured-ish response log with duration
        try:
            duration_ms = None
            if hasattr(g, "start_time"):
                duration_ms = (pytime.perf_counter() - g.start_time) * 1000
            user_id = current_user.id if current_user.is_authenticated else "anonymous"
            if not (request.path.startswith("/static/") or request.path == "/favicon.ico"):
                logger.info(
                    f"[RESP] request_id={rid} method={request.method} path={request.path} "
                    f"status={response.status_code} duration_ms={(duration_ms or 0):.2f} user={user_id}"
                )
        except Exception:
            pass
        return response

    @app.after_request
    def apply_cache_control(response):
        path = request.path or ""
        if path.startswith("/static/") or path == "/favicon.ico":
            return response
        if path == "/dashboard" or path.startswith("/api/history/") or path.startswith("/api/account/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response
