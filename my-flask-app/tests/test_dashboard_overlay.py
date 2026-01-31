from pathlib import Path


def test_dashboard_details_overlay_no_numeric_label():
    tpl_path = Path(__file__).resolve().parents[1] / "templates" / "dashboard.html"
    content = tpl_path.read_text(encoding="utf-8")
    assert "מתוך 100" not in content
