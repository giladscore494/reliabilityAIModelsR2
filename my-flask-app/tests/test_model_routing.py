import types

import app.factory as factory
import app.extensions as extensions


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
