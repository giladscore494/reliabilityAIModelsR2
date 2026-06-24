import logging

from app import legal


def test_production_missing_salt_logs_render_env_name(monkeypatch, caplog):
    monkeypatch.setattr(legal, "LEGAL_IP_HASH_SALT", "")
    with caplog.at_level(logging.ERROR):
        assert legal.validate_legal_ip_hash_salt_config("production") is False
    assert "LEGAL_IP_HASH_SALT" in caplog.text
    assert "Render env var LEGAL_IP_HASH_SALT" in caplog.text


def test_dev_missing_salt_non_blocking(monkeypatch):
    monkeypatch.setattr(legal, "LEGAL_IP_HASH_SALT", "")
    assert legal.validate_legal_ip_hash_salt_config("development") is False


def test_hashing_does_not_return_raw_ip(monkeypatch):
    monkeypatch.setattr(legal, "LEGAL_IP_HASH_SALT", "salt")
    assert legal.normalize_legal_ip("203.0.113.17") != "203.0.113.17"
