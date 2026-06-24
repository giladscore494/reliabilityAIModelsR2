import logging
from types import SimpleNamespace

import pytest

from main import create_app
from app.services.comparison import grounding
from app.services.comparison.model_config import (
    DEFAULT_COMPARISON_PRO_MODEL_ID,
    InvalidComparisonModelConfig,
    validate_comparison_model_config,
)


def test_invented_gemini_31_pro_is_rejected_as_invalid_config(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("COMPARISON_STAGE_A_MODEL", "gemini-3.1-pro")

    with pytest.raises(InvalidComparisonModelConfig, match="gemini-3.1-pro"):
        create_app()


def test_valid_pro_preview_model_id_is_accepted(monkeypatch):
    monkeypatch.setenv("COMPARISON_STAGE_A_MODEL", DEFAULT_COMPARISON_PRO_MODEL_ID)
    monkeypatch.setenv("COMPARISON_STAGE_A_REPAIR_MODEL", DEFAULT_COMPARISON_PRO_MODEL_ID)
    monkeypatch.setenv("COMPARISON_STAGE_B_MODEL", DEFAULT_COMPARISON_PRO_MODEL_ID)

    validate_comparison_model_config()


def test_404_model_error_triggers_one_safe_model_fallback(app, monkeypatch):
    calls = []

    class NotFoundError(Exception):
        status_code = 404

    class _Models:
        def generate_content(self, *, model, contents, config):
            calls.append(model)
            if len(calls) == 1:
                raise NotFoundError(
                    "models/gemini-3.1-pro-preview is not found for API version v1beta, or is not supported for generateContent"
                )
            return SimpleNamespace(
                text='{"car_name":"Toyota Corolla 2020","reliability":{"overall":"high"},"ownership_cost":{},"comfort_practicality":{},"performance_driving":{},"facts":{},"short_notes":[],"sources":[]}',
                candidates=[],
            )

    monkeypatch.setenv("COMPARISON_STAGE_A_MODEL", DEFAULT_COMPARISON_PRO_MODEL_ID)
    monkeypatch.setattr(grounding.extensions, "ai_client", SimpleNamespace(models=_Models()))

    with app.app_context():
        out, err = grounding.call_gemini_single_car("{}", "car_1", timeout_sec=1)

    assert err is None
    assert out["car_name"] == "Toyota Corolla 2020"
    assert calls == [DEFAULT_COMPARISON_PRO_MODEL_ID, "gemini-3.5-flash"]


def test_fallback_logs_actual_model_used_and_reason(app, monkeypatch, caplog):
    class NotFoundError(Exception):
        status_code = 404

    class _Models:
        def __init__(self):
            self.calls = 0

        def generate_content(self, *, model, contents, config):
            self.calls += 1
            if self.calls == 1:
                raise NotFoundError("404 not found for generateContent")
            return SimpleNamespace(
                text='{"car_name":"Toyota Corolla 2020","reliability":{"overall":"high"},"ownership_cost":{},"comfort_practicality":{},"performance_driving":{},"facts":{},"short_notes":[],"sources":[]}',
                candidates=[],
            )

    monkeypatch.setattr(grounding.extensions, "ai_client", SimpleNamespace(models=_Models()))

    with app.app_context(), caplog.at_level(logging.INFO):
        out, err = grounding.call_gemini_single_car("{}", "car_1", timeout_sec=1)

    assert err is None
    assert out is not None
    log_text = caplog.text
    assert "model_fallback_due_to_404" in log_text
    assert "model=gemini-3.5-flash" in log_text
    assert "reason=model_fallback_due_to_404" in log_text
