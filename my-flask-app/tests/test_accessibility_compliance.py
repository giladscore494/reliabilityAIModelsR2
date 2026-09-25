from pathlib import Path


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


def test_shared_accessibility_contracts_are_present():
    navbar = (TEMPLATES / "_navbar.html").read_text()
    footer = (TEMPLATES / "_footer.html").read_text()
    assert "דלג לתוכן הראשי" in navbar
    assert 'aria-controls="navbar-drawer"' in navbar
    assert 'aria-expanded="false"' in navbar
    assert 'role="dialog"' in navbar and 'aria-modal="true"' in navbar
    assert "public.accessibility" in footer
    for filename in PUBLIC_PAGES:
        page = (TEMPLATES / filename).read_text()
        assert 'lang="he"' in page
        assert 'dir="rtl"' in page
        assert 'id="main-content"' in page


def test_dialog_keyboard_contracts_are_implemented():
    navbar_js = (TEMPLATES.parent / "static" / "navbar.js").read_text()
    research_js = (TEMPLATES.parent / "static" / "research.js").read_text()
    login = (TEMPLATES / "_login_modal.html").read_text()
    assert "e.key === 'Escape'" in navbar_js
    assert "lastFocused.focus" in navbar_js
    assert "event.key === 'Escape'" in research_js
    assert "previousFocus?.focus" in research_js
    assert 'aria-labelledby="login-modal-title"' in login
    assert "previous?.focus" in login


def test_posthog_requires_choice_and_disables_invasive_features():
    snippet = (TEMPLATES / "_posthog_snippet.html").read_text()
    assert "yeda_analytics_choice" in snippet
    assert "autocapture:false" in snippet
    assert "disable_session_recording:true" in snippet
    assert "capture_pageview:false" in snippet
    assert "person_profiles:'never'" in snippet
