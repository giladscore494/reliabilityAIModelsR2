from sqlalchemy import inspect, text


def ensure_search_history_cache_key(db, logger):
    """
    Runtime defensive check to guarantee search_history.cache_key exists in Postgres.
    Safe to run multiple times and under concurrency.
    """
    try:
        engine = db.engine
        dialect = engine.dialect.name if engine else ""
        if dialect != "postgresql":
            return

        insp = inspect(engine)
        if not insp.has_table("search_history"):
            logger.warning("[DB] search_history table missing; skipping cache_key ensure")
            return

        cols = {c["name"] for c in insp.get_columns("search_history")}
        with engine.begin() as conn:
            if "cache_key" not in cols:
                logger.warning("[DB] search_history.cache_key missing -> adding column")
                conn.execute(
                    text("ALTER TABLE search_history ADD COLUMN IF NOT EXISTS cache_key VARCHAR(64);")
                )
            else:
                logger.info("[DB] search_history.cache_key already present")

            logger.info("[DB] ensuring index ix_search_history_user_cache_ts exists")
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_search_history_user_cache_ts "
                    "ON search_history (user_id, cache_key, timestamp DESC);"
                )
            )

            status = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name='search_history' AND column_name='cache_key' LIMIT 1;"
                )
            ).scalar()
            logger.info("[DB] cache_key column present? %s", bool(status))
            try:
                alembic_version = conn.execute(
                    text("SELECT version_num FROM alembic_version LIMIT 1;")
                ).scalar()
                logger.info("[DB] Alembic current version: %s", alembic_version or "unknown")
            except Exception:
                logger.warning("[DB] Alembic version table missing or unreadable")
    except Exception as e:
        logger.exception("[DB] ensure_search_history_cache_key failed: %s", e)
