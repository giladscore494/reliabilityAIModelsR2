from app.utils import analytics


class KeywordClient:
    def __init__(self):
        self.calls = []
    def capture(self, *, distinct_id, event, properties=None):
        self.calls.append((distinct_id, event, properties))


class PositionalClient:
    def __init__(self):
        self.calls = []
    def capture(self, distinct_id, event, properties=None):
        self.calls.append((distinct_id, event, properties))


class KeywordRejectingClient:
    """Legacy SDK shape: keyword distinct_id/event raise TypeError."""
    def __init__(self):
        self.calls = []
    def capture(self, *args, **kwargs):
        if "distinct_id" in kwargs or "event" in kwargs:
            raise TypeError("capture() got an unexpected keyword argument 'distinct_id'")
        self.calls.append((args, kwargs))


class BrokenClient:
    def capture(self, *args, **kwargs):
        raise RuntimeError("boom")


def _enable(monkeypatch, client):
    monkeypatch.setattr(analytics, "_posthog_client", client)
    monkeypatch.setattr(analytics, "_posthog_enabled", True)


def test_track_event_current_sdk_keyword_style(monkeypatch):
    client = KeywordClient()
    _enable(monkeypatch, client)
    analytics.track_event("u1", "analyze_completed", {"request_id": "r1"})
    assert client.calls == [("u1", "analyze_completed", {"request_id": "r1"})]


def test_track_event_old_sdk_positional_style(monkeypatch):
    client = PositionalClient()
    _enable(monkeypatch, client)
    analytics.track_event("u1", "compare_completed", {"request_id": "r2"})
    assert client.calls == [("u1", "compare_completed", {"request_id": "r2"})]


def test_track_event_falls_back_to_positional_when_keywords_rejected(monkeypatch):
    client = KeywordRejectingClient()
    _enable(monkeypatch, client)
    analytics.track_event("u1", "compare_completed", {"request_id": "r3"})
    assert client.calls == [(("u1", "compare_completed"), {"properties": {"request_id": "r3"}})]


def test_track_event_never_raises(monkeypatch):
    _enable(monkeypatch, BrokenClient())
    analytics.track_event("u1", "analyze_completed", {})
    analytics.track_event("u1", "compare_completed", {})
