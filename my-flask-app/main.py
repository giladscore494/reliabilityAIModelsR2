# -*- coding: utf-8 -*-
"""Entry point for Render/Gunicorn.

This file must be import-safe (no side effects / placeholders at top-level).
The actual Flask app factory lives in this package and is exposed via create_app().
"""

from app import create_app  # re-export for gunicorn: main:create_app
