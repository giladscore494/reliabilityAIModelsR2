"""Bootstrap helpers for the Flask application factory.

This package contains small, focused modules that ``app.factory.create_app``
delegates to. The goal is to keep the factory function readable and to make
each concern testable in isolation.

Currently extracted:

- :mod:`app.bootstrap.blueprints` — registers all route blueprints.
- :mod:`app.bootstrap.clients`    — initializes Gemini AI client + Google OAuth.

Planned (tracked as remaining technical debt — see PR description):

- ``app.bootstrap.request_hooks`` — ``before_request`` / ``after_request`` /
  ``teardown_request`` registration
- ``app.bootstrap.error_handlers`` — Flask error handlers
- ``app.bootstrap.security_headers`` — CSP / HSTS / cookie policy
"""
