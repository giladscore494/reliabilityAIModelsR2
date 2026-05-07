"""Application-wide error handlers and login-manager unauthorized handler.

Extracted from app.factory as part of Phase 4. Behavior is preserved verbatim.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from flask import jsonify, redirect, request, url_for
from werkzeug.exceptions import RequestEntityTooLarge

from app.utils.http_helpers import api_error, get_request_id, log_rejection

if TYPE_CHECKING:
    from flask import Flask
    from flask_login import LoginManager


def register_error_handlers(app: "Flask", login_manager: "LoginManager") -> None:
    """Register the 413 handler and the login_manager's unauthorized response."""

    @app.errorhandler(RequestEntityTooLarge)
    def handle_request_too_large(e):
        return api_error("payload_too_large", "Payload exceeds limit", status=413, details={"field": "payload"})

    @login_manager.unauthorized_handler
    def unauthorized():
        """Return 401 for AJAX/JSON requests, otherwise redirect to login."""
        if request.is_json or request.accept_mimetypes.accept_json:
            if request.path.startswith("/api/account/delete"):
                rid = get_request_id()
                resp = jsonify({"error": "unauthorized", "message": "Login required", "request_id": rid})
                resp.status_code = 401
                resp.headers["X-Request-ID"] = rid
                return resp
            log_rejection("unauthenticated", "User not logged in, no valid session")
            return api_error("unauthenticated", "אנא התחבר כדי להשתמש בשירות זה", status=401)
        return redirect(url_for('public.login'))
