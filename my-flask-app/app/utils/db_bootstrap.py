import hashlib

from sqlalchemy import text


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

        preparer = engine.dialect.identifier_preparer
        with engine.connect() as conn:
            schemas = [
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT table_schema "
                        "FROM information_schema.tables "
                        "WHERE table_name='search_history' AND table_type='BASE TABLE';"
                    )
                ).all()
            ]

        if not schemas:
            logger.warning("[DB] search_history table missing; skipping cache_key ensure")
            return

        patched_schemas = []
        with engine.begin() as conn:
            for schema in schemas:
                if not schema:
                    logger.warning("[DB] skipping empty schema name from information_schema")
                    continue
                safe_schema = preparer.quote_identifier(schema)
                suffix = hashlib.sha256(schema.encode("utf-8")).hexdigest()[:12]
                index_name_raw = f"ix_search_history_user_cache_ts_{suffix}"
                index_name = preparer.quote_identifier(index_name_raw)

                conn.execute(text(f"SET LOCAL search_path={safe_schema}"))

                logger.info("[DB] ensuring cache_key on %s.search_history", schema)
                conn.execute(
                    text("ALTER TABLE search_history ADD COLUMN IF NOT EXISTS cache_key VARCHAR(64);")
                )

                logger.info("[DB] ensuring index %s on %s.search_history", index_name_raw, schema)
                conn.execute(
                    text(
                        f"CREATE INDEX IF NOT EXISTS {index_name} "
                        "ON search_history (user_id, cache_key, timestamp DESC);"
                    )
                )

                status = conn.execute(
                    text(
                        "SELECT 1 FROM information_schema.columns "
                        "WHERE table_schema=:schema AND table_name='search_history' "
                        "AND column_name='cache_key' LIMIT 1;"
                    ),
                    {"schema": schema},
                ).scalar()
                logger.info("[DB] cache_key column present in %s? %s", schema, bool(status))
                if status:
                    patched_schemas.append(schema)

            logger.info("[DB] cache_key ensure completed for schemas: %s", patched_schemas)
            try:
                alembic_version = conn.execute(
                    text("SELECT version_num FROM alembic_version LIMIT 1;")
                ).scalar()
                logger.info("[DB] Alembic current version: %s", alembic_version or "unknown")
            except Exception:
                logger.warning("[DB] Alembic version table missing or unreadable")
    except Exception as e:
        logger.exception("[DB] ensure_search_history_cache_key failed: %s", e)
