import types

import app.factory as factory
import app.extensions as extensions
from main import create_app


def test_gemini_model_routing(monkeypatch):
    calls = {}

    class FakeModels:
        def __init__(self, key):
            self.key = key

        def generate_content(self, *, model=None, contents=None, config=None):
            calls[self.key] = model
            return types.SimpleNamespace(text="{}")

    monkeypatch.setattr(factory, "_execute_with_timeout", lambda fn, _timeout: (fn(), None))
    monkeypatch.setattr(extensions, "ai_client", types.SimpleNamespace(models=FakeModels("reliability")))
    monkeypatch.setattr(extensions, "advisor_client", types.SimpleNamespace(models=FakeModels("recommender")))

    factory.call_gemini_grounded_once("prompt")
    factory.car_advisor_call_gemini_with_search({"driver_age": 30})

    assert calls["reliability"] == extensions.GEMINI_RELIABILITY_MODEL_ID
    assert calls["recommender"] == extensions.GEMINI_RECOMMENDER_MODEL_ID


def test_recommender_prompt_separates_fit_from_reliability(monkeypatch):
    captured = {}

    class FakeModels:
        def generate_content(self, *, model=None, contents=None, config=None):
            captured["contents"] = contents
            return types.SimpleNamespace(text="{}")

    monkeypatch.setattr(factory, "_execute_with_timeout", lambda fn, _timeout: (fn(), None))
    monkeypatch.setattr(extensions, "advisor_client", types.SimpleNamespace(models=FakeModels()))

    factory.car_advisor_call_gemini_with_search({"driver_age": 30})

    prompt = captured["contents"]
    assert "Fit Score represents how well the car matches the questionnaire preferences only." in prompt
    assert "Fit Score is NOT a reliability score." in prompt
    assert "Fit Score is NOT a purchase-worthiness score." in prompt
    assert "Never frame any result as a final approval to buy." in prompt


def test_owner_email_env_populates_owner_config(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-for-pytest")
    monkeypatch.setenv("OWNER_EMAIL", "owner@example.com")
    monkeypatch.delenv("OWNER_EMAILS", raising=False)
    monkeypatch.delenv("SKIP_CREATE_ALL", raising=False)

    app = create_app()

    assert app.config["OWNER_EMAILS"] == {"owner@example.com"}
