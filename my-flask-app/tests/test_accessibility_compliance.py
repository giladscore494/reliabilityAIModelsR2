from pathlib import Path
import re
import shutil
import subprocess

import pytest


TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
PUBLIC_PAGES = (
    "landing.html",
    "reliability_app.html",
    "compare.html",
    "recommendations.html",
    "dashboard.html",
    "terms.html",
    "privacy.html",
    "accessibility.html",
)


def test_accessibility_statement_and_legal_routes(client):
    for route in ("/accessibility", "/terms", "/privacy"):
        response = client.get(route)
        assert response.status_code == 200
    page = client.get("/accessibility").get_data(as_text=True)
    assert "הצהרת נגישות" in page
    assert 'lang="he"' in page and 'dir="rtl"' in page
    assert 'id="main-content"' in page


def test_configured_contacts_render_escaped(app, client):
    app.config["ACCESSIBILITY_CONTACT_EMAIL"] = 'access+test@example.com"><script>'
    app.config["LEGAL_CONTACT_EMAIL"] = "legal@example.com"
    page = client.get("/accessibility").get_data(as_text=True)
    assert "access+test@example.com" in page
    assert "<script>" not in page
    assert "legal@example.com" in client.get("/privacy").get_data(as_text=True)


def test_every_ordinary_footer_page_gets_shared_analytics_controller(app, client):
    app.config["POSTHOG_API_KEY"] = "ph_test"
    for route in ("/", "/app", "/compare", "/recommendations", "/terms", "/privacy", "/accessibility"):
        response = client.get(route)
        assert response.status_code == 200
        page = response.get_data(as_text=True)
        assert 'id="analytics-privacy-settings"' in page
        assert 'static/analytics_privacy.js' in page
        assert "onclick=" not in re.search(
            r'<button id="analytics-privacy-settings"[^>]*>', page
        ).group(0)


def test_public_pages_do_not_disable_browser_zoom():
    for filename in PUBLIC_PAGES:
        page = (TEMPLATES / filename).read_text()
        assert "user-scalable=no" not in page
        assert "maximum-scale=1.0" not in page


def test_shared_accessibility_contracts_are_present():
    navbar = (TEMPLATES / "_navbar.html").read_text()
    footer = (TEMPLATES / "_footer.html").read_text()
    assert "דלג לתוכן הראשי" in navbar
    assert 'aria-controls="navbar-drawer"' in navbar
    assert 'aria-expanded="false"' in navbar
    assert 'role="dialog"' in navbar and 'aria-modal="true"' in navbar
    assert "public.accessibility" in footer
    assert "onclick=" not in footer
    assert "analytics-privacy-settings" in footer
    assert "{% include '_posthog_snippet.html' %}" in footer
    for filename in PUBLIC_PAGES:
        page = (TEMPLATES / filename).read_text()
        assert 'lang="he"' in page
        assert 'dir="rtl"' in page
        assert 'id="main-content"' in page


def test_dialog_keyboard_contracts_are_implemented():
    navbar = (TEMPLATES / "_navbar.html").read_text()
    navbar_js = (TEMPLATES.parent / "static" / "navbar.js").read_text()
    research_js = (TEMPLATES.parent / "static" / "research.js").read_text()
    login = (TEMPLATES / "_login_modal.html").read_text()
    assert "e.key === 'Escape'" in navbar_js
    assert "lastFocused.focus" in navbar_js
    assert "event.key === 'Escape'" in research_js
    assert "previousFocus?.focus" in research_js
    assert 'aria-labelledby="login-modal-title"' in login
    assert "previous?.focus" in login

    # Every static ARIA relationship in the representative shared dialogs resolves.
    combined = navbar + login + (TEMPLATES / "_research_consent_modal.html").read_text()
    ids = set(re.findall(r'\bid="([^"{]+)"', combined))
    references = re.findall(r'\baria-(?:controls|labelledby|describedby)="([^"{]+)"', combined)
    assert all(ref in ids for value in references for ref in value.split())


def test_legacy_posthog_cookie_is_not_created():
    hooks = (TEMPLATES.parent / "app" / "bootstrap" / "request_hooks.py").read_text()
    assert "yrc_anon" not in hooks


def test_posthog_requires_choice_and_disables_invasive_features():
    snippet = (TEMPLATES / "_posthog_snippet.html").read_text()
    controller = (TEMPLATES.parent / "static" / "analytics_privacy.js").read_text()
    assert "analytics_privacy.js" in snippet
    assert "onclick=" not in (TEMPLATES / "_footer.html").read_text()
    assert "autocapture: false" in controller
    assert "disable_session_recording: true" in controller
    assert "capture_pageview: false" in controller
    assert "person_profiles: 'never'" in controller


def test_representative_labels_and_decorative_icons():
    reliability = (TEMPLATES / "reliability_app.html").read_text()
    advisor = (TEMPLATES / "recommendations.html").read_text()
    compare = (TEMPLATES / "compare.html").read_text()
    for control in ("mileage_range", "fuel_type", "annual_km", "city_pct", "driver_style"):
        assert f'for="{control}"' in reliability
    for control in ("w_reliability", "w_resale", "w_fuel", "w_performance", "w_comfort"):
        assert f'for="{control}"' in advisor
    assert 'role="combobox"' in compare
    assert 'role="listbox"' in compare
    assert 'role="option"' in compare
    for page in (reliability, advisor, compare, (TEMPLATES / "landing.html").read_text()):
        assert not re.search(r'<svg(?![^>]*aria-hidden="true")', page)


def test_analytics_choice_behavior_with_javascript_controller():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the analytics controller behavior test")
    test_file = Path(__file__).resolve().parent / "js" / "analytics_privacy.test.js"
    subprocess.run([node, str(test_file)], check=True)
