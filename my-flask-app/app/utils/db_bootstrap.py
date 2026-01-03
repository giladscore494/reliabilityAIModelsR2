from sqlalchemy import inspect, text


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
        if "cache_key" not in columns:
            try:
                db.session.execute(
                    text(
                        "ALTER TABLE search_history ADD COLUMN IF NOT EXISTS cache_key VARCHAR(64);"
                    )
                )
                db.session.commit()
            except Exception as col_exc:
                db.session.rollback()
                if log:
                    log.warning("[DB] cache_key add failed: %s", col_exc)
                return

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
