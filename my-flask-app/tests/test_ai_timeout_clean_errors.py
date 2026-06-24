from app.utils.production_observability import env_ms


def test_timeout_env_ms(monkeypatch):
    monkeypatch.setenv("RELIABILITY_AI_TIMEOUT_MS", "45000")
    assert env_ms("RELIABILITY_AI_TIMEOUT_MS", 1) == 45000


def test_timeout_env_ms_invalid_uses_default(monkeypatch):
    monkeypatch.setenv("COMPARISON_STAGE_A_TIMEOUT_MS", "bad")
    assert env_ms("COMPARISON_STAGE_A_TIMEOUT_MS", 123) == 123
