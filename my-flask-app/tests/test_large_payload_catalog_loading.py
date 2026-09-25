from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_compare_catalog_is_lazy_not_embedded(client):
    # 48c1a50 implemented the lazy, cacheable catalog endpoint for /compare,
    # which is what the old TODO comment in compare.html asked for.
    compare = (ROOT / "templates" / "compare.html").read_text(encoding="utf-8")
    assert '<script type="application/json" id="car-data">{}</script>' in compare
    assert "car_models_data | tojson" not in compare
    assert "loadCompareCatalog()" in compare

    resp = client.get("/api/compare/catalog")
    assert resp.status_code == 200
    assert "max-age=3600" in resp.headers.get("Cache-Control", "")
    assert resp.get_json()["data"]["catalog"]

    page = client.get("/compare")
    assert page.status_code == 200
    assert len(page.get_data()) < 500_000  # ~100 KB lazy vs ~2.5 MB inlined


def test_review_catalog_embed_todo_still_tracked():
    # /app still inlines the full catalog; keep the TODO until it is lazy too.
    review = (ROOT / "templates" / "reliability_app.html").read_text(encoding="utf-8")
    assert "{{ car_models_data | tojson }}" in review
    assert "move to a cacheable lazy catalog endpoint" in review
