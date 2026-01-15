from sqlalchemy import create_engine, inspect, text

from app.utils.db_bootstrap import ensure_duration_ms_columns


def test_ensure_duration_ms_columns_adds_missing(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{db_path}")
    monkeypatch.setenv("ENABLE_RUNTIME_DB_BOOTSTRAP", "1")

    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE search_history (id INTEGER PRIMARY KEY, user_id INTEGER);"))
        conn.execute(text("CREATE TABLE advisor_history (id INTEGER PRIMARY KEY, user_id INTEGER);"))

    inspector = inspect(engine)
    assert "duration_ms" not in {c["name"] for c in inspector.get_columns("search_history")}
    assert "duration_ms" not in {c["name"] for c in inspector.get_columns("advisor_history")}

    ensure_duration_ms_columns(engine)

    inspector = inspect(engine)
    for table in ("search_history", "advisor_history"):
        cols = {c["name"] for c in inspector.get_columns(table)}
        assert "duration_ms" in cols

    engine.dispose()


def test_ensure_duration_ms_columns_idempotent(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy2.db"
    engine = create_engine(f"sqlite:///{db_path}")
    monkeypatch.setenv("ENABLE_RUNTIME_DB_BOOTSTRAP", "1")

    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE search_history (id INTEGER PRIMARY KEY, duration_ms INTEGER);"))
        conn.execute(text("CREATE TABLE advisor_history (id INTEGER PRIMARY KEY, duration_ms INTEGER);"))

    # Should not raise when columns already exist
    ensure_duration_ms_columns(engine)
    ensure_duration_ms_columns(engine)

    inspector = inspect(engine)
    assert "duration_ms" in {c["name"] for c in inspector.get_columns("search_history")}
    assert "duration_ms" in {c["name"] for c in inspector.get_columns("advisor_history")}

    engine.dispose()
