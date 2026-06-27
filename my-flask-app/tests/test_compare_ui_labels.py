# -*- coding: utf-8 -*-
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_compare_page_uses_general_transmission_labels():
    compare_html = (ROOT / "templates" / "compare.html").read_text(encoding="utf-8")
    reliability_html = (ROOT / "templates" / "reliability_app.html").read_text(
        encoding="utf-8"
    )
    combined = f"{compare_html}\n{reliability_html}"

    assert "רובוטית (DSG)" not in combined
    assert "רובוטית (כפולת מצמדים)" not in combined
    assert "אוטומטית (פלנטרית/רציפה)" not in combined
    assert '<option value="רובוטית">רובוטית</option>' in compare_html
    assert '<option value="רציפה">רציפה</option>' in compare_html
    assert '<option value="לא ידוע / לבדיקה">לא ידוע / לבדיקה</option>' in compare_html
    assert "<option>רובוטית</option>" in reliability_html
    assert "<option>רציפה</option>" in reliability_html
    assert "<option>לא ידוע / לבדיקה</option>" in reliability_html


def test_compare_ui_hides_request_ids_and_confidence_labels():
    compare_html = (ROOT / "templates" / "compare.html").read_text(encoding="utf-8")
    result_block = compare_html[
        compare_html.index("function renderCheckedVersions"):
        compare_html.index("function renderComparePartialResearch")
    ]
    visible_forbidden = ["רמת ודאות", "לא מאומת", "מחקר חלקי", "request_id"]
    for term in visible_forbidden:
        assert term not in result_block
    assert "checkedVersionConfidenceLabel" not in compare_html


def test_compare_ui_renders_canonical_categories_and_clean_alternatives():
    compare_html = (ROOT / "templates" / "compare.html").read_text(encoding="utf-8")
    for key in [
        "pricing_and_value",
        "fuel_consumption",
        "official_safety",
        "powertrain_and_performance",
        "reliability_and_risk",
        "family_daily_use",
        "resale_and_market_confidence",
        "ownership_cost",
        "comfort_practicality",
    ]:
        assert key in compare_html or key in (ROOT / "app" / "services" / "comparison" / "prompts.py").read_text(encoding="utf-8")
    assert "חלופות שכדאי לשקול" in compare_html
    assert "why_consider" in compare_html
