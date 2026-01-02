"""Main Flask application.

Changes in this version:
- OAuth /login: remove explicit state=None from authorize_redirect while keeping return.
- Render-only SECRET_KEY hard-fail (Render requires explicit SECRET_KEY in env).
- SESSION_COOKIE_SECURE toggles based on Render detection.
- Minimal request logging via @app.before_request without logging secrets.
- Provide create_app() factory for deterministic gunicorn entrypoint.
"""

import os
import time
import uuid
from flask import Flask, request

# Authlib is commonly used for OAuth in Flask
from authlib.integrations.flask_client import OAuth


def _is_render() -> bool:
    """Detect Render runtime."""
    return bool(os.getenv("RENDER") or os.getenv("RENDER_SERVICE_ID") or os.getenv("RENDER_EXTERNAL_URL"))


def _required_env(name: str) -> str:
    v = os.getenv(name)
    if v is None or v == "":
        raise RuntimeError(f"Missing required environment variable: {name}")
    return v


def create_app() -> Flask:
    is_render = _is_render()

    app = Flask(__name__)

    # SECRET_KEY handling:
    # - On Render: hard-fail if not set so sessions/signing are deterministic and secure.
    # - Elsewhere (local/dev): allow empty but warn by using a stable fallback to avoid crashing.
    if is_render:
        app.secret_key = _required_env("SECRET_KEY")
    else:
        app.secret_key = os.getenv("SECRET_KEY", "dev-unsafe-secret-key")

    # Session cookie security toggle:
    # Only force secure cookies on Render (HTTPS). Local dev often uses HTTP.
    app.config["SESSION_COOKIE_SECURE"] = bool(is_render)

    # Optional: keep cookies reasonably safe by default.
    app.config.setdefault("SESSION_COOKIE_HTTPONLY", True)
    app.config.setdefault("SESSION_COOKIE_SAMESITE", "Lax")

    # Minimal request observability without secrets.
    # Do NOT log headers like Authorization/Cookie, nor query strings.
    @app.before_request
    def _log_request():
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        # stash for handlers if needed
        request.environ["request_id"] = request_id

        start = time.time()
        request.environ["_start_time"] = start

        # Minimal fields only
        app.logger.info(
            "req id=%s method=%s path=%s remote=%s ua=%s",
            request_id,
            request.method,
            request.path,
            request.headers.get("X-Forwarded-For", request.remote_addr),
            (request.user_agent.string or "")[:200],
        )

    oauth = OAuth(app)

    # OAuth client configuration (example). Adjust names to match your existing setup.
    # NOTE: This preserves existing env usage patterns while staying minimal.
    # If your repo already defined these differently, merge accordingly.
    oauth.register(
        name="auth0",
        client_id=os.getenv("AUTH0_CLIENT_ID"),
        client_secret=os.getenv("AUTH0_CLIENT_SECRET"),
        server_metadata_url=os.getenv("AUTH0_METADATA_URL"),
        client_kwargs={"scope": "openid profile email"},
    )

    @app.get("/")
    def index():
        return "OK"

    @app.get("/login")
    def login():
        # Keep return, remove explicit state=None.
        redirect_uri = os.getenv("AUTH0_CALLBACK_URL")
        return oauth.auth0.authorize_redirect(redirect_uri)

    return app


# Backwards-compatible app global for WSGI servers and local `python main.py`.
app = create_app()


if __name__ == "__main__":
    # Local dev only.
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
