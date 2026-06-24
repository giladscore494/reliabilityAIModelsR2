from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_does_not_render_internal_grounding_metadata():
    dashboard = (ROOT / "templates" / "dashboard.html").read_text(encoding="utf-8")
    assert "sourceScopeLabel" not in dashboard
    assert "weaklySourced" not in dashboard
    assert "sourceCount" not in dashboard
