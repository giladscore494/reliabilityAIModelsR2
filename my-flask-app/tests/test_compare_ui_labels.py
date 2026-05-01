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
    assert "רובוטית" in combined
    assert "רציפה" in combined
    assert "לא ידוע / לבדיקה" in combined
