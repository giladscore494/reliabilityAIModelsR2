# -*- coding: utf-8 -*-
"""HTTP helper functions for routes - moved from create_app() scope."""

from typing import Optional, Mapping, Any, Dict
from flask import jsonify, g, current_app, request
from flask_login import current_user


def get_request_id() -> str:
    """Get the current request_id from Flask g object."""
    return getattr(g, 'request_id', 'unknown')


def api_ok(payload: Optional[dict] = None, status: int = 200, request_id: Optional[str] = None):
    """Standard API success response."""
    rid = request_id or get_request_id()
    resp = jsonify({"ok": True, "data": payload, "request_id": rid})
    resp.status_code = status
    resp.headers["X-Request-ID"] = rid
    return resp


def api_error(code: str, message: str, status: int = 400, details: Optional[Mapping[str, Any]] = None, request_id: Optional[str] = None):
    """Standard API error response."""
    rid = request_id or get_request_id()
    body: Dict[str, Any] = {"ok": False, "error": {"code": code, "message": message}, "request_id": rid}
    if details is not None:
        body["error"]["details"] = details
    resp = jsonify(body)
    resp.status_code = status
    resp.headers["X-Request-ID"] = rid
    return resp


def is_owner_user() -> bool:
    """Check if current user is an owner (uses app.config['OWNER_EMAILS'])."""
    if not current_user.is_authenticated:
        return False
    email = (getattr(current_user, "email", "") or "").lower()
    owner_emails = current_app.config.get('OWNER_EMAILS', set())
    return email in owner_emails


def get_redirect_uri():
    """
    Build OAuth redirect URI.
    - Always prefer apex canonical base (no www) for production.
    - For local dev (localhost/127.0.0.1), fall back to request.url_root.
    - Keeping the redirect URI stable avoids mismatches and ensures Google uses canonical_base/auth.
    """
    canonical_base = current_app.config.get('CANONICAL_BASE', '')
    host = (request.host or "").lower()
    host_only = host.split(":")[0]
    if host_only in ("localhost", "127.0.0.1"):
        uri = request.url_root.rstrip("/") + "/auth"
    else:
        uri = f"{canonical_base}/auth"
    print(f"[AUTH] Using redirect_uri={uri} (host={host})")
    return uri


def log_rejection(reason: str, details: str = "") -> None:
    """
    Safely log rejection reasons without exposing sensitive data.
    
    Args:
        reason: Short category (unauthenticated, quota, validation, server_error)
        details: Safe description of the issue (no secrets, API keys, or DB details)
    """
    user_id = current_user.id if current_user.is_authenticated else "anonymous"
    endpoint = request.endpoint or "unknown"
    request_id = get_request_id()
    current_app.logger.warning(f"[REJECT] request_id={request_id} endpoint={endpoint} user={user_id} reason={reason} details={details}")
