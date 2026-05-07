"""Emergency / dev-only DB bootstrap helpers.

This module was previously imported from ``app/factory.py`` and gated behind
``ENABLE_RUNTIME_DB_BOOTSTRAP``. It has been moved out of the application
runtime path entirely. Schema changes are managed by Alembic / Flask-Migrate
(see ``docs/DB_DEPLOY_CHECKLIST.md``). This file is retained as an
emergency-recovery tool only and **must not** be imported by app startup.

To run manually (only in an emergency, e.g. recovering a DB whose Alembic
state is broken):

    cd my-flask-app
    ENABLE_RUNTIME_DB_BOOTSTRAP=1 python -c "
    from main import create_app
    from app.extensions import db
    from scripts.dev.emergency_db_bootstrap import (
        ensure_search_history_cache_key,
        ensure_duration_ms_columns,
    )
    app = create_app()
    with app.app_context():
        ensure_search_history_cache_key(app, db)
        ensure_duration_ms_columns(db.engine)
    "
"""

import os
from sqlalchemy import inspect, text


def ensure_search_history_cache_key(app, db, logger=None):
    """
    DEPRECATED runtime ALTER TABLE helper.

    Schema changes are managed by Alembic/Flask-Migrate. This function only runs
    when ``ENABLE_RUNTIME_DB_BOOTSTRAP`` is explicitly set to a truthy value
    (intended for emergency-recovery / dev-only use).
    """
    log = logger or getattr(app, "logger", None)
    if os.environ.get("ENABLE_RUNTIME_DB_BOOTSTRAP", "").lower() not in ("1", "true", "yes"):
        if log:
            log.info("[DB] Runtime bootstrap disabled; skipping cache_key ensure")
        return
    try:
        engine = db.engine
        dialect = engine.dialect.name if engine else ""
        if dialect != "postgresql":
            return

        inspector = inspect(engine)
        if not inspector.has_table("search_history"):
            if log:
                log.warning("[DB] search_history table missing; skipping cache_key ensure")
            return

        columns = {col["name"] for col in inspector.get_columns("search_history")}
        has_cache_key = "cache_key" in columns
        if not has_cache_key:
            try:
                db.session.execute(
                    text(
                        "ALTER TABLE search_history ADD COLUMN IF NOT EXISTS cache_key VARCHAR(64);"
                    )
                )
                db.session.commit()
                has_cache_key = True
            except Exception as col_exc:
                db.session.rollback()
                if log:
                    log.warning("[DB] cache_key add failed: %s", col_exc)
                return

        if has_cache_key:
            try:
                db.session.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_search_history_user_cache_ts "
                        "ON search_history (user_id, cache_key, timestamp DESC);"
                    )
                )
                db.session.commit()
            except Exception as idx_exc:
                db.session.rollback()
                if log:
                    log.warning("[DB] index ensure skipped: %s", idx_exc)

        if log:
            log.info("[DB] cache_key ensured on search_history (added if missing)")
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        if log:
            log.exception("[DB] ensure_search_history_cache_key failed: %s", e)


def ensure_duration_ms_columns(engine, logger=None):
    """
    DEPRECATED runtime ALTER TABLE helper.

    Schema changes are managed by Alembic/Flask-Migrate. This function only runs
    when ``ENABLE_RUNTIME_DB_BOOTSTRAP`` is explicitly set to a truthy value
    (intended for emergency-recovery / dev-only use).
    """
    log = logger
    if os.environ.get("ENABLE_RUNTIME_DB_BOOTSTRAP", "").lower() not in ("1", "true", "yes"):
        if log:
            log.info("[DB] Runtime bootstrap disabled; skipping duration_ms ensure")
        return
    try:
        dialect = getattr(engine, "dialect", None)
        dialect_name = dialect.name if dialect else ""
    except Exception as e:
        if log:
            log.warning("[DB] duration_ms dialect detection failed: %s", e)
        return
    targets = ("search_history", "advisor_history")
    pg_statements = {
        "search_history": text("ALTER TABLE search_history ADD COLUMN IF NOT EXISTS duration_ms INTEGER;"),
        "advisor_history": text("ALTER TABLE advisor_history ADD COLUMN IF NOT EXISTS duration_ms INTEGER;"),
    }
    sqlite_statements = {
        "search_history": text("ALTER TABLE search_history ADD COLUMN duration_ms INTEGER;"),
        "advisor_history": text("ALTER TABLE advisor_history ADD COLUMN duration_ms INTEGER;"),
    }

    if dialect_name == "postgresql":
        statements = pg_statements
    elif dialect_name == "sqlite":
        statements = sqlite_statements
    else:
        if log:
            log.warning("[DB] duration_ms ensure skipped: unsupported dialect %s", dialect_name)
        return

    for table_name in targets:
        try:
            inspector = inspect(engine)
            if not inspector.has_table(table_name):
                continue
            cols = {col["name"] for col in inspector.get_columns(table_name)}
            if "duration_ms" in cols:
                continue

            stmt = statements.get(table_name)
            if stmt is None:
                continue

            with engine.begin() as conn:
                conn.execute(stmt)
        except Exception as e:
            if log:
                log.warning("[DB] duration_ms ensure unexpected error for %s: %s", table_name, e)
