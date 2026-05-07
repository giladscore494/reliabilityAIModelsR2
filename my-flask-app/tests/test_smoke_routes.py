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
    from main import create_app
    return create_app()


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
