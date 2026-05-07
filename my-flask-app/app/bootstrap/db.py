"""DB / extension initialization helpers extracted from app.factory.

Behavior is preserved verbatim. The factory remains responsible for ordering
(it calls these helpers in the same order they used to appear inline).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Tuple
from urllib.parse import urlparse

from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect

from app.config import MAX_CONTENT_LENGTH_DEFAULT, DEFAULT_API_PAYLOAD_LIMIT_BYTES
from app.extensions import db, login_manager, migrate, oauth

if TYPE_CHECKING:
    import logging
    from flask import Flask


def configure_database(app: "Flask", logger: "logging.Logger") -> Tuple[str, bool]:
    """Resolve DATABASE_URL/SECRET_KEY env vars, set Flask config, init extensions.

    Returns ``(db_url, is_render)``.
    """
    db_url = os.environ.get("DATABASE_URL", "").strip()
    secret_key = os.environ.get("SECRET_KEY", "").strip()

    # Normalize deprecated prefix for SQLAlchemy
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)

    # If running on Render, refuse to boot without DATABASE_URL
    is_render = os.environ.get("RENDER", "").strip() != ""
    if is_render and not db_url:
        raise RuntimeError(
            "DATABASE_URL is missing on Render. "
            "Set DATABASE_URL (Internal Postgres URL) in Render Environment Variables."
        )
    if is_render and not secret_key:
        raise RuntimeError(
            "SECRET_KEY is missing on Render. "
            "Set SECRET_KEY in Render Environment Variables."
        )

    # Config
    app.config["SQLALCHEMY_DATABASE_URI"] = db_url if db_url else "sqlite:///:memory:"
    # SECURITY: Never boot without a real secret key — no fallback in any environment.
    if not secret_key:
        raise RuntimeError(
            "SECRET_KEY is required in ALL environments. "
            "Set SECRET_KEY as an environment variable."
        )
    app.config["SECRET_KEY"] = secret_key
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # ===== SECURITY: MAX_CONTENT_LENGTH (Phase 1D: DoS prevention) =====
    raw_max_content_length = os.getenv("MAX_CONTENT_LENGTH_BYTES")
    try:
        max_content_length = int(raw_max_content_length) if raw_max_content_length else MAX_CONTENT_LENGTH_DEFAULT
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Invalid MAX_CONTENT_LENGTH_BYTES; must be an integer") from exc
    app.config["MAX_CONTENT_LENGTH"] = max_content_length
    app.config["DEFAULT_API_PAYLOAD_LIMIT_BYTES"] = DEFAULT_API_PAYLOAD_LIMIT_BYTES

    # ===== SECURITY:  Session Cookie Configuration (Tier 1) =====
    app.config["SESSION_COOKIE_SECURE"] = bool(is_render)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    # ===== FIX A: SQLAlchemy Connection Pool (prevents stale connections) =====
    if db_url and "postgresql" in db_url:
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
            "pool_pre_ping": True,
            "pool_recycle": 240,
            "pool_size": 5,
            "max_overflow": 10,
            "connect_args": {"connect_timeout": 10, "sslmode": "prefer"}
        }
        logger.info("[BOOT] SQLAlchemy configured with pool_pre_ping=True, pool_recycle=240")

    if not db_url:
        logger.warning("[BOOT] DATABASE_URL not set. Using in-memory sqlite (LOCAL DEV ONLY).")

    if db_url:
        parsed_db_url = urlparse(db_url)
        safe_host = parsed_db_url.hostname or ""
        safe_port = f":{parsed_db_url.port}" if parsed_db_url.port else ""
        safe_db = (parsed_db_url.path or "").lstrip("/")
        logger.info("[DB] DATABASE host=%s%s db=%s", safe_host, safe_port, safe_db or "(default)")
    else:
        logger.info("[DB] DATABASE_URL not provided; using sqlite fallback")

    # Init
    db.init_app(app)
    login_manager.init_app(app)
    oauth.init_app(app)
    migrate.init_app(app, db)

    with app.app_context():
        try:
            with db.engine.connect() as conn:
                context = MigrationContext.configure(conn)
                current_rev = context.get_current_revision()
            has_duration_ms = False
            with db.engine.connect() as conn:
                inspector = inspect(conn)
                if inspector.has_table("search_history"):
                    cols = [col["name"] for col in inspector.get_columns("search_history")]
                    has_duration_ms = "duration_ms" in cols
            logger.info(
                "[DB] Alembic revision: %s (duration_ms column: %s)",
                current_rev or "(none)",
                "present" if has_duration_ms else "missing",
            )
        except Exception:
            logger.exception("[DB] Alembic revision check failed")

    return db_url, is_render


def log_alembic_revision(app: "Flask", logger: "logging.Logger") -> None:
    """Second-pass Alembic revision log line (preserved from create_app)."""
    with app.app_context():
        try:
            with db.engine.connect() as conn:
                context = MigrationContext.configure(conn)
                current_rev = context.get_current_revision()
            logger.info("[DB] Alembic current revision: %s", current_rev or "(none)")
        except Exception:
            logger.exception("[DB] Alembic revision check failed")
