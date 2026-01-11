from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError


def ensure_search_history_cache_key(app, db, logger=None):
    """
    Runtime defensive check to guarantee search_history.cache_key exists in Postgres.
    Safe to run multiple times and under concurrency.
    """
    log = logger or getattr(app, "logger", None)
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
    Ensure duration_ms column exists on search_history and advisor_history tables.
    Idempotent and best-effort: logs warnings on failure without raising.
    """
    log = logger
    try:
        inspector = inspect(engine)
    except Exception as e:
        if log:
            log.warning("[DB] duration_ms inspector init failed: %s", e)
        return

    dialect = getattr(engine, "dialect", None)
    dialect_name = dialect.name if dialect else ""
    targets = ("search_history", "advisor_history")

    for table_name in targets:
        try:
            if not inspector.has_table(table_name):
                continue
            cols = {col["name"] for col in inspector.get_columns(table_name)}
            if "duration_ms" in cols:
                continue

            if dialect_name == "postgresql":
                stmt = text(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS duration_ms INTEGER;")
            elif dialect_name == "sqlite":
                stmt = text(f"ALTER TABLE {table_name} ADD COLUMN duration_ms INTEGER;")
            else:
                if log:
                    log.warning("[DB] duration_ms ensure skipped: unsupported dialect %s", dialect_name)
                continue

            with engine.begin() as conn:
                conn.execute(stmt)
        except SQLAlchemyError as e:
            if log:
                log.warning("[DB] duration_ms add failed for %s: %s", table_name, e)
        except Exception as e:
            if log:
                log.warning("[DB] duration_ms ensure unexpected error for %s: %s", table_name, e)
