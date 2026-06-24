from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_catalog_embed_todo_present_for_review_and_compare():
    review = (ROOT / "templates" / "reliability_app.html").read_text(encoding="utf-8")
    compare = (ROOT / "templates" / "compare.html").read_text(encoding="utf-8")
    assert "move to a cacheable lazy catalog endpoint" in review
    assert "move to a cacheable lazy catalog endpoint" in compare
