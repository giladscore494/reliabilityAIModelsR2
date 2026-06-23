"""Regression tests for the compare_page endpoint reference fix.

Ensures that:
- The homepage renders successfully (no 500).
- The homepage contains a working link to /compare.
- No template references the invalid endpoint 'public.compare_page'.
- The comparison.compare_page endpoint resolves to /compare.
"""

import os
from pathlib import Path

import pytest
from flask import url_for


TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"


def test_homepage_returns_200(client):
    """Homepage must not return 500."""
    resp = client.get("/")
    assert resp.status_code == 200


def test_homepage_contains_compare_link(client):
    """Landing page must contain a link to /compare."""
    resp = client.get("/")
    html = resp.get_data(as_text=True)
    assert '/compare' in html


def test_no_invalid_public_compare_page_references():
    """No template should reference the invalid endpoint 'public.compare_page'."""
    invalid_refs = [
        "url_for('public.compare_page')",
        'url_for("public.compare_page")',
    ]
    for template_file in TEMPLATES_DIR.rglob("*.html"):
        content = template_file.read_text(encoding="utf-8")
        for ref in invalid_refs:
            assert ref not in content, (
                f"Found invalid endpoint reference '{ref}' in {template_file}"
            )


def test_compare_page_route_resolves(app):
    """The comparison.compare_page endpoint must resolve to /compare."""
    with app.test_request_context():
        assert url_for("comparison.compare_page") == "/compare"
