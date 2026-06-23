"""Application-wide error handlers and login-manager unauthorized handler.

Extracted from app.factory as part of Phase 4. Behavior is preserved verbatim.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from flask import jsonify, redirect, request, url_for
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from app.utils.http_helpers import api_error, get_request_id, log_rejection

if TYPE_CHECKING:
    from flask import Flask
    from flask_login import LoginManager


def register_error_handlers(app: "Flask", login_manager: "LoginManager") -> None:
    """Register the 413 handler, generic 500 handler, and the login_manager's unauthorized response."""

    @app.errorhandler(RequestEntityTooLarge)
    def handle_request_too_large(e):
        return api_error("payload_too_large", "Payload exceeds limit", status=413, details={"field": "payload"})

    @app.errorhandler(Exception)
    def handle_unhandled_exception(e):
        """Catch-all handler for unhandled exceptions.

        * Preserves normal HTTP exceptions (404, 405, etc.) — returns their
          native status code, not 500.
        * For real unhandled errors: logs traceback with request_id, path, and
          method, then returns a safe response (JSON for API/XHR callers,
          minimal Hebrew HTML otherwise).
        """
        # Let standard HTTP errors pass through with their own status code.
        if isinstance(e, HTTPException):
            return e

        rid = get_request_id()
        app.logger.exception(
            "[500] Unhandled exception request_id=%s path=%s method=%s exc_type=%s",
            rid,
            request.path,
            request.method,
            type(e).__name__,
        )

        # Determine if the caller expects JSON.
        wants_json = (
            request.is_json
            or request.headers.get("X-Requested-With", "").lower() == "xmlhttprequest"
            or "application/json" in (request.accept_mimetypes.best or "")
            or request.path.startswith("/api/")
        )

        if wants_json:
            return api_error("server_error", "שגיאת שרת", status=500, request_id=rid)

        # Safe Hebrew HTML error page — no debug info exposed.
        html = (
            '<!DOCTYPE html>'
            '<html lang="he" dir="rtl"><head><meta charset="utf-8">'
            '<title>שגיאת שרת</title>'
            '<style>body{font-family:sans-serif;text-align:center;padding:4rem 1rem;'
            'background:#eef1f6;color:#2a313b}'
            'h1{font-size:2rem;margin-bottom:1rem}'
            'p{font-size:1rem;color:#6b7480}'
            'code{font-size:0.85rem;color:#9aa3af}'
            '</style></head><body>'
            '<h1>שגיאת שרת</h1>'
            '<p>אירעה שגיאה בלתי צפויה. אנא נסה שוב מאוחר יותר.</p>'
            f'<p><code>request_id: {rid}</code></p>'
            '</body></html>'
        )
        return html, 500

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
