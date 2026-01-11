# -*- coding: utf-8 -*-
"""Shared helpers for routes - provides common functionality needed by all blueprints."""

import uuid
import time as pytime
from typing import Optional, Mapping, Any, Dict
from flask import jsonify, g, current_app


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
    """Check if current user is an owner."""
    from flask_login import current_user
    if not current_user.is_authenticated:
        return False
    owner_emails = current_app.config.get('OWNER_EMAILS', set())
    return current_user.email in owner_emails


def get_redirect_uri():
    """Build OAuth redirect URI."""
    from flask import url_for, request
    from urllib.parse import urlparse, urlunparse
    # Explicitly build the full URL including scheme
    auth_url = url_for('public.auth', _external=True)
    parsed = urlparse(auth_url)
    # Force HTTPS in production
    if current_app.config.get('ENV') == 'production' or request.headers.get('X-Forwarded-Proto') == 'https':
        parsed = parsed._replace(scheme='https')
    return urlunparse(parsed)
