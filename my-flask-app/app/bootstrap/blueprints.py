"""Blueprint registration for the Flask application.

Extracted from ``app.factory.create_app`` (Phase 3 of the maintainability
refactor). The set and order of blueprints is preserved exactly to avoid any
behavioural change in route resolution / URL precedence.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flask import Flask


def register_blueprints(app: "Flask") -> None:
    """Register all route blueprints on ``app``.

    The import-then-register order matches the historical
    ``create_app`` body. Do not reorder without verifying that no two
    blueprints share a URL rule.
    """
    from app.routes.public_routes import bp as public_bp
    from app.routes.analyze_routes import bp as analyze_bp
    from app.routes.advisor_routes import bp as advisor_bp
    from app.routes.dashboard_routes import bp as dashboard_bp
    from app.routes.legal_routes import bp as legal_bp
    from app.routes.comparison_routes import bp as comparison_bp
    from app.routes.public_examples_routes import bp as examples_bp
    from app.routes.feedback_routes import bp as feedback_bp
    from app.routes.owner_routes import bp as owner_bp
    from app.routes.owner_profile_routes import owner_profile_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(analyze_bp)
    app.register_blueprint(advisor_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(legal_bp)
    app.register_blueprint(comparison_bp)
    app.register_blueprint(examples_bp)
    app.register_blueprint(feedback_bp)
    app.register_blueprint(owner_bp)
    app.register_blueprint(owner_profile_bp)
