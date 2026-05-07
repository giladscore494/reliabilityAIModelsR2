"""Request lifecycle hooks: before_request, after_request, teardown_request,
and the Jinja context_processor.

Extracted from app.factory as part of Phase 4. Each hook's logic is preserved
verbatim. Closures over locals in ``create_app`` (canonical_host,
allowed_hosts, is_host_allowed) are passed in explicitly here.
"""

from __future__ import annotations

import secrets
import time as pytime
import uuid
from typing import TYPE_CHECKING, Callable, Set
from urllib.parse import urlparse

from flask import g, jsonify, redirect, request, session
from flask_login import current_user
from werkzeug.exceptions import RequestEntityTooLarge

from app.config import (
    DEFAULT_API_PAYLOAD_LIMIT_BYTES,
    PER_IP_PER_MIN_LIMIT,
)
from app.extensions import db
from app.legal import (
    CONTACT_EMAIL,
    PRIVACY_VERSION,
    TERMS_VERSION,
    parse_legal_confirm,
)
from app.models import LegalAcceptance, ResearchConsent
from app.research import (
    RESEARCH_CONSENT_TYPE,
    RESEARCH_NOTICE_VERSION,
    ensure_anon_id,
)
from app.utils.auth_helpers import is_owner
from app.utils.http_helpers import api_error, get_request_id

if TYPE_CHECKING:
    import logging
    from flask import Flask


def register_request_hooks(
    app: "Flask",
    *,
    canonical_host: str,
    allowed_hosts: Set[str],
    is_host_allowed: Callable[[str], bool],
    get_client_ip: Callable[[], str],
    check_and_increment_ip_rate_limit: Callable[..., object],
    logger: "logging.Logger",
) -> None:
    """Register every request-lifecycle hook used by create_app."""

    @app.before_request
    def ensure_yrc_anon_cookie():
        """Set a stable anonymous cookie for PostHog distinct_id on anonymous users."""
        if hasattr(request, 'cookies') and not request.cookies.get('yrc_anon'):
            g._set_yrc_anon = uuid.uuid4().hex
        else:
            g._set_yrc_anon = None

    @app.after_request
    def set_yrc_anon_cookie(response):
        """Attach the yrc_anon cookie if it was flagged for creation."""
        anon_val = getattr(g, '_set_yrc_anon', None)
        if anon_val:
            response.set_cookie(
                'yrc_anon',
                anon_val,
                max_age=365 * 24 * 60 * 60,
                httponly=True,
                secure=True,
                samesite='Lax',
            )
        return response

    @app.before_request
    def assign_request_id_and_redirect():
        """
        Assign a request_id + start_time early and enforce canonical host redirect.
        """
        if not getattr(g, "request_id", None):
            g.request_id = str(uuid.uuid4())
        g.start_time = pytime.perf_counter()
        g.csp_nonce = secrets.token_urlsafe(16)
        ensure_anon_id(session)

        host = (request.host or "").lower()
        # Preserve port if present (e.g., local dev)
        host_parts = host.split(":")
        hostname_only = host_parts[0]
        port_part = f":{host_parts[1]}" if len(host_parts) > 1 else ""
        if canonical_host and hostname_only == f"www.{canonical_host}":
            target_host = canonical_host + port_part
            parsed = urlparse(request.url)
            redirect_url = parsed._replace(netloc=target_host).geturl()
            return redirect(redirect_url, code=301)

    @app.context_processor
    def inject_template_globals():
        legal_accepted = False
        research_consent_accepted = False
        posthog_key = app.config.get("POSTHOG_API_KEY", "")
        posthog_host = app.config.get("POSTHOG_HOST", "https://us.i.posthog.com")
        terms_version = app.config.get("TERMS_VERSION", TERMS_VERSION)
        privacy_version = app.config.get("PRIVACY_VERSION", PRIVACY_VERSION)
        research_notice_version = app.config.get("RESEARCH_NOTICE_VERSION", RESEARCH_NOTICE_VERSION)
        if current_user.is_authenticated:
            try:
                legal_accepted = LegalAcceptance.query.filter_by(
                    user_id=current_user.id,
                    terms_version=terms_version,
                    privacy_version=privacy_version,
                ).first() is not None
                research_consent_accepted = ResearchConsent.query.filter_by(
                    user_id=current_user.id,
                    consent_type=RESEARCH_CONSENT_TYPE,
                    terms_version=terms_version,
                    privacy_version=privacy_version,
                    research_notice_version=research_notice_version,
                ).first() is not None
            except Exception:
                legal_accepted = False
                research_consent_accepted = False
        else:
            try:
                research_consent_accepted = ResearchConsent.query.filter_by(
                    anon_id=session.get("anon_id"),
                    consent_type=RESEARCH_CONSENT_TYPE,
                    terms_version=terms_version,
                    privacy_version=privacy_version,
                    research_notice_version=research_notice_version,
                ).first() is not None
            except Exception:
                research_consent_accepted = False
        app.logger.info(
            "[POSTHOG] template config injected=%s path=%s host=%s",
            bool(posthog_key),
            request.path,
            posthog_host if posthog_key else "",
        )
        return {
            "is_logged_in": current_user.is_authenticated,
            "current_user": current_user,
            "is_owner": is_owner(),
            "contact_email": app.config.get("CONTACT_EMAIL", CONTACT_EMAIL),
            "legal_accepted": legal_accepted,
            "research_consent_accepted": research_consent_accepted,
            "research_consent_type": app.config.get("RESEARCH_CONSENT_TYPE", RESEARCH_CONSENT_TYPE),
            "research_notice_version": research_notice_version,
            "terms_version": terms_version,
            "privacy_version": privacy_version,
            "csp_nonce": getattr(g, "csp_nonce", ""),
            "csrf_token": session.get("csrf_token", ""),
            "posthog_key": posthog_key,
            "posthog_host": posthog_host,
            "is_authenticated": current_user.is_authenticated,
        }

    @app.before_request
    def validate_host_header():
        """Validate the Host header to prevent host header injection attacks."""
        host = request.host
        if not is_host_allowed(host):
            logger.warning(f"[SECURITY] Invalid host header: {host}")
            # For API routes, return JSON error
            if request.is_json or request.accept_mimetypes.accept_json or request.path.startswith(('/analyze', '/advisor_api', '/search-details')):
                return api_error("invalid_host", "Invalid host header", status=400)
            return "Invalid host header", 400

    @app.before_request
    def block_security_scan_paths():
        path = (request.path or "").lower()
        blocked_prefixes = (
            "/.env", "/.git", "/wp-admin", "/config", "/phpinfo",
            "/phpmyadmin", "/server-status",
        )
        if not any(path == p or path.startswith(f"{p}/") for p in blocked_prefixes):
            return None
        client_ip = get_client_ip()
        try:
            check_and_increment_ip_rate_limit(client_ip, limit=PER_IP_PER_MIN_LIMIT)
        except Exception:
            pass
        logger.warning(
            "[SECURITY_SCAN] method=%s path=%s ip=%s ua=%s request_id=%s",
            request.method,
            request.path,
            client_ip,
            (request.headers.get("User-Agent") or "")[:120],
            get_request_id(),
        )
        return ("", 404)

    @app.before_request
    def enforce_route_specific_payload_limits():
        if request.method not in ("POST", "PUT", "PATCH"):
            return None
        if request.content_length is None:
            if (request.headers.get("Transfer-Encoding") or "").lower() == "chunked" or request.content_type:
                raise RequestEntityTooLarge()
            content_length = 0
        else:
            content_length = request.content_length
        path = request.path or ""
        limit = DEFAULT_API_PAYLOAD_LIMIT_BYTES
        # Tight guard for all write routes to preserve DoS safety.
        if content_length > limit:
            raise RequestEntityTooLarge()
        return None

    # Phase 2H: Origin/Referer protection for session-auth POST endpoints (CSRF-safe without tokens)
    @app.before_request
    def ensure_csrf_token():
        """Ensure a CSRF token exists in the session for Double Submit Cookie pattern."""
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_hex(32)

    @app.before_request
    def check_origin_referer_for_posts():
        """
        Validate Origin or Referer header for session-based POST endpoints.
        This provides CSRF protection without requiring CSRF tokens in fetch() calls.
        """
        # Only check POST requests to session-authenticated endpoints
        if request.method != 'POST':
            return None

        # Only check specific endpoints (not login/auth which may come from external OAuth flow)
        protected_paths = ['/analyze', '/advisor_api', '/api/account/delete', '/api/compare']
        if not any(request.path.startswith(p) for p in protected_paths):
            return None

        def _forbidden_response():
            if request.path.startswith("/api/account/delete"):
                rid = get_request_id()
                resp = jsonify({"error": "forbidden", "request_id": rid})
                resp.status_code = 403
                resp.headers["X-Request-ID"] = rid
                return resp
            return api_error("forbidden_origin", "Request from unauthorized origin", status=403)

        # Get Origin or Referer header
        origin = request.headers.get('Origin')
        referer = request.headers.get('Referer')

        # Extract host from origin or referer
        if origin:
            # Origin format: https://example.com or https://example.com:port
            try:
                parsed = urlparse(origin)
                origin_host = parsed.netloc or parsed.hostname
            except Exception:
                origin_host = None
        else:
            origin_host = None

        if referer and not origin_host:
            # Referer format: https://example.com/path
            try:
                parsed = urlparse(referer)
                origin_host = parsed.netloc or parsed.hostname
            except Exception:
                origin_host = None

        if not origin_host:
            # Fallback: Double Submit Cookie check
            csrf_header = request.headers.get("X-CSRF-Token", "")
            csrf_session = session.get("csrf_token", "")
            if csrf_header and csrf_session and len(csrf_header) == 64 and csrf_header == csrf_session:
                return None  # CSRF token valid, allow request
            logger.warning(f"[CSRF] POST to {request.path} with no Origin/Referer and invalid/missing CSRF token")
            return _forbidden_response()

        host_no_port = origin_host.split(':')[0].lower() if ':' in origin_host else origin_host.lower()
        if host_no_port not in allowed_hosts:
            logger.warning(f"[CSRF] Blocked POST to {request.path} from disallowed origin: {origin_host}")
            return _forbidden_response()

        return None

    @app.before_request
    def enforce_legal_acceptance():
        """
        Centralized legal enforcement to avoid missing endpoints.
        ProxyFix normalizes request.remote_addr; only allowlisted paths bypass acceptance.
        """
        path = request.path or ""
        allowlist = {
            "/",
            "/terms",
            "/privacy",
            "/api/legal/accept",
            "/api/legal/status",
            "/login",
            "/logout",
            "/auth",
            "/healthz",
            "/recommendations",
            "/compare",
            "/api/examples",
            "/owner/examples",
            "/owner/examples/update",
        }
        if path in allowlist or path.startswith(("/static/", "/assets/", "/example/")) or path == "/favicon.ico":
            return None
        if not current_user.is_authenticated:
            return None
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return None

        def _legal_error(code: str, message: str):
            rid = get_request_id()
            resp = jsonify({"error": code, "message": message, "request_id": rid})
            resp.status_code = 403
            resp.headers["X-Request-ID"] = rid
            return resp

        is_ai_write = request.method in ("POST", "PUT", "PATCH", "DELETE") and path.startswith(
            ("/analyze", "/advisor_api", "/api/compare")
        )
        if not is_ai_write:
            return None

        payload = request.get_json(silent=True) if request.is_json else request.form
        if not parse_legal_confirm((payload or {}).get("legal_confirm")):
            return _legal_error("TERMS_NOT_ACCEPTED", "Please accept Terms & Privacy to continue.")

        terms_version = app.config.get("TERMS_VERSION")
        privacy_version = app.config.get("PRIVACY_VERSION")
        current_acceptance = LegalAcceptance.query.filter_by(
            user_id=current_user.id,
            terms_version=terms_version,
            privacy_version=privacy_version,
        ).first()
        if current_acceptance:
            return None
        previous_acceptance = LegalAcceptance.query.filter_by(user_id=current_user.id).first()
        if previous_acceptance:
            return _legal_error("TERMS_VERSION_MISMATCH", "Updated Terms & Privacy require re-acceptance.")
        return _legal_error("TERMS_NOT_ACCEPTED", "Please accept Terms & Privacy to continue.")

    @app.before_request
    def log_request_metadata():
        """
        Phase 2K: Log request metadata (request_id assigned earlier).
        """
        request_id = getattr(g, "request_id", str(uuid.uuid4()))
        g.request_id = request_id

        xfp = request.headers.get("X-Forwarded-Proto", "")
        xff = request.headers.get("X-Forwarded-For", "")
        auth_state = current_user.is_authenticated
        path = request.path or ""

        if not (path.startswith("/static/") or path == "/favicon.ico"):
            # Phase 2K: Use logger instead of print
            logger.info(
                f"[REQ] request_id={request_id} {request.method} {path} "
                f"host={request.host} scheme={request.scheme} xfp={xfp} xff={xff} auth={auth_state}"
            )

    @app.teardown_request
    def teardown_request_handler(exc):
        try:
            db.session.rollback()
        except Exception:
            logger.exception("[DB] teardown rollback failed")
        finally:
            db.session.remove()
