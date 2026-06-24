"""Smoke test: app imports and all active user-facing routes are registered.

Mirrors the active surface listed in the maintainability refactor task.
"""

import pytest


ACTIVE_ROUTES = [
    "/",
    "/app",
    "/compare",
    # The recommendations / "advisor" page lives at /recommendations.
    "/recommendations",
    "/dashboard",
    "/terms",
    "/privacy",
    "/healthz",
]


@pytest.fixture(scope="module")
def app():
    # Provide test-only env so create_app() can boot. This mirrors the
    # function-scoped `app` fixture in conftest.py and never weakens the
    # production requirement that SECRET_KEY must be set in real deployments.
    from main import create_app

    mp = pytest.MonkeyPatch()
    mp.setenv("SECRET_KEY", "test-secret-key-for-smoke")
    mp.setenv("DATABASE_URL", "sqlite:///:memory:")
    mp.delenv("RENDER", raising=False)
    try:
        yield create_app()
    finally:
        mp.undo()


def test_app_imports(app):
    assert app is not None


def test_active_routes_registered(app):
    """Each active user-facing route must have a registered URL rule."""
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    missing = [route for route in ACTIVE_ROUTES if route not in rules]
    assert not missing, f"Missing active routes: {missing}"


def test_legacy_routes_removed(app):
    """Removed legacy product routes must not be registered."""
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    legacy = [
        "/leasing",
        "/leasing_advisor",
        "/api/leasing/recommend",
        "/api/leasing/frame",
        "/api/leasing/history",
        "/service-prices",
        "/api/service-prices/analyze",
        "/invoice-scanner",
    ]
    leaked = [route for route in legacy if route in rules]
    assert not leaked, f"Legacy routes still registered: {leaked}"
