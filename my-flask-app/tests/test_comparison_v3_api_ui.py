# -*- coding: utf-8 -*-
"""Comparison V3 offline end-to-end: fake TRIPY facts -> rows -> mocked JEV -> composition -> row explanations ->
summary -> API -> /compare template -> compare_v3.js renderer. No paid calls, no network."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.extensions import db
from app.models import ComparisonHistory

from comparison_v2_fakes import FakeSummaryWriter, FakeTypeSafeSession
from comparison_v3_fakes import ALL_RECORDS, KEYS, FakeExplanationWriter, FakeTripySession, build_v3_deps

ROOT = Path(__file__).resolve().parents[1]
HEADERS = {"Content-Type": "application/json", "Origin": "http://localhost"}
V21_FIXTURE = ROOT / "tests" / "fixtures" / "comparison_v2_1_result.json"
GENERAL = {"mode": "general"}
JS = ROOT / "static" / "compare_v3.js"
CSS = ROOT / "static" / "compare_v3.css"
node_required = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


@pytest.fixture
def v3_client(app, logged_in_client, monkeypatch):
    """V3 is the code default for /compare: no engine fixture, no env flag."""
    from app.services.comparison_v3.pipeline import ComparisonV3HistoryStore
    from app.services.comparison_v3.tripy import DemoCatalogRepository, DemoFactsRepository

    client, user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True}, headers=HEADERS)
    counters = {"session": FakeTypeSafeSession(), "writer": FakeSummaryWriter(), "explainer": FakeExplanationWriter(),
                "facts": DemoFactsRepository(records=ALL_RECORDS)}

    def fake_deps(ai_client=None):
        return build_v3_deps(session=counters["session"], writer=counters["writer"],
                             explanation_writer=counters["explainer"], facts=counters["facts"],
                             history=ComparisonV3HistoryStore())

    monkeypatch.setattr("app.services.comparison_v3.pipeline.build_default_deps", fake_deps)
    monkeypatch.setattr("app.services.comparison_v3.pipeline.build_default_catalog",
                        lambda: DemoCatalogRepository(DemoFactsRepository(records=ALL_RECORDS)))
    return client, user_id, counters


def _post(client, cars, stream=False, profile=None):
    headers = dict(HEADERS)
    if stream:
        headers["Accept"] = "application/x-ndjson"
    body = {"cars": cars, "legal_confirm": True, "buyer_profile": profile or GENERAL}
    return client.post("/api/compare", json=body, headers=headers)


def _cars(*names, prices=None):
    prices = prices or [None] * len(names)
    return [{"variant_identity_key": KEYS[n], **({"asking_price_ils": p} if p is not None else {})}
            for n, p in zip(names, prices)]


def _node(script):
    return subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout


def _render(tmp_path, result):
    (tmp_path / "r.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return json.loads(_node(f"""
        const api = require({json.dumps(str(JS))});
        const r = JSON.parse(require('fs').readFileSync({json.dumps(str(tmp_path / 'r.json'))}, 'utf8'));
        process.stdout.write(JSON.stringify({{isV3: api.isV3Result(r), hero: api.buildHeroHtml(r), table: api.buildTableHtml(r)}}));
    """))


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
def test_v3_is_the_default_engine_json(v3_client, app):
    client, user_id, counters = v3_client
    resp = _post(client, _cars("octavia", "golf", prices=[120000, 110000]))
    assert resp.status_code == 200, resp.get_json()
    data = resp.get_json()["data"]
    assert data["engine_version"] == "comparison-v3/1"
    assert data["buyer_profile"]["schema"] == "buyer-profile/3"
    assert data["progress"] == ["resolving_vehicles", "loading_facts", "comparing_facts", "evaluating_decision",
                                "writing_explanations", "writing_summary", "complete"]
    assert "decision_trace" not in data
    meta = data["provider_meta"]
    assert (meta["facts_provider"], meta["jev_calls"], meta["explanation_calls"], meta["summary_calls"]) == ("tripy", 1, 1, 1)
    assert "remote_enrichment_calls" not in meta
    assert [s["key"] for s in data["table"]["sections"]] == ["variant_details", "price", "safety", "performance",
                                                             "efficiency_environment", "practicality", "history",
                                                             "towing"]
    assert data["tripy_versions"]["contract"] == "vehicle-facts/1"
    with app.app_context():
        row = ComparisonHistory.query.get(data["comparison_id"])
        assert row.prompt_version == "comparison-v3/1"
        stored = json.loads(row.computed_result)["response"]
        assert stored["table"]["sections"][0]["rows"][0]["explanation_he"]


def test_cached_comparison_makes_no_model_calls(v3_client):
    client, _, counters = v3_client
    cars = _cars("octavia", "golf", prices=[120000, 110000])
    first = _post(client, cars).get_json()["data"]
    assert counters["explainer"].calls == 1 and counters["writer"].calls == 1
    second = _post(client, cars).get_json()["data"]
    assert second["cached"] is True and second["comparison_id"] != first["comparison_id"]
    assert counters["explainer"].calls == 1 and counters["writer"].calls == 1
    assert len(counters["session"].post_calls) == 1
    assert second["table"] == first["table"]
    # a different asking price is a different comparison
    _post(client, _cars("octavia", "golf", prices=[121000, 110000]))
    assert counters["explainer"].calls == 2


def test_stream_progress_and_result(v3_client):
    client, _, _ = v3_client
    resp = _post(client, _cars("bmw_530e", "outlander_phev"), stream=True)
    assert resp.mimetype == "application/x-ndjson"
    lines = [json.loads(line) for line in resp.get_data(as_text=True).splitlines() if line.strip()]
    stages = [line["stage"] for line in lines if line["type"] == "progress"]
    assert stages.index("writing_explanations") < stages.index("writing_summary")
    result = lines[-1]
    assert result["type"] == "result" and result["ok"] is True
    assert "ev" in [s["key"] for s in result["data"]["table"]["sections"]]


def test_budget_without_every_price_is_400(v3_client):
    client, _, counters = v3_client
    profile = {"mode": "personalized", "main_use": "family", "budget_max_ils": 150000}
    resp = _post(client, _cars("octavia", "golf", prices=[120000, None]), profile=profile)
    assert resp.status_code == 400
    err = resp.get_json()["error"]
    assert err["code"] == "invalid_buyer_profile"
    assert err["message"] == "כדי לבדוק תקציב יש להזין מחיר לכל רכב"
    assert counters["explainer"].calls == 0
    streamed = _post(client, _cars("octavia", "golf", prices=[120000, None]), stream=True, profile=profile)
    last = json.loads(streamed.get_data(as_text=True).strip().splitlines()[-1])
    assert last["type"] == "error" and last["error"]["field"] == "budget_max_ils"


def test_tripy_down_returns_facts_unavailable(v3_client, monkeypatch):
    from app.services.comparison_v3.tripy import TripyClient, TripyFactsRepository

    client, _, counters = v3_client
    counters["facts"] = TripyFactsRepository(TripyClient(base_url="https://tripy.test", token="t",
                                                         session=FakeTripySession(statuses=[503, 503])))
    resp = _post(client, _cars("octavia", "golf"))
    assert resp.status_code == 503
    assert resp.get_json()["error"] == {"code": "facts_unavailable",
                                        "message": "ההשוואה לא זמינה כרגע, נסו שוב בעוד כמה דקות"}
    assert counters["writer"].calls == 0 and counters["explainer"].calls == 0


def test_availability_hides_sliders_without_rows(v3_client):
    client, _, _ = v3_client
    two = client.post("/api/compare/v3/availability", json={"cars": _cars("octavia", "golf")}, headers=HEADERS)
    assert two.status_code == 200
    assert "practicality" in two.get_json()["data"]["available_dimensions"]
    assert "purchase_price" not in two.get_json()["data"]["available_dimensions"]      # no prices entered
    three = client.post("/api/compare/v3/availability", json={"cars": _cars("octavia", "golf", "civic")},
                        headers=HEADERS).get_json()["data"]
    assert "practicality" not in three["available_dimensions"] and three["plugin_selected"] is False
    priced = client.post("/api/compare/v3/availability",
                         json={"cars": _cars("bmw_530e", "outlander_phev", prices=[250000, 200000])},
                         headers=HEADERS).get_json()["data"]
    assert {"purchase_price", "ev_convenience"} <= set(priced["available_dimensions"])
    assert priced["plugin_selected"] is True


def test_catalog_cascade_through_the_server(v3_client):
    client, _, _ = v3_client
    makers = client.get("/api/compare/v3/catalog/manufacturers").get_json()["data"]["manufacturers"]
    skoda = next(m for m in makers if m["manufacturer"] == "סקודה")
    assert skoda["display"] == "Skoda"
    models = client.get("/api/compare/v3/catalog/models", query_string={"manufacturer": "סקודה"}).get_json()["data"]
    assert [m["model"] for m in models["models"]] == ["OCTAVIA"]
    trims = client.get("/api/compare/v3/catalog/trims",
                       query_string={"manufacturer": "סקודה", "model": "OCTAVIA", "year": 2023}).get_json()["data"]
    assert trims["trims"][0]["variant_identity_key"] == KEYS["octavia"]
    assert client.get("/api/compare/v3/catalog/bogus").status_code == 404
    assert client.get("/api/compare/v3/catalog/models").status_code == 400


# ---------------------------------------------------------------------------
# template
# ---------------------------------------------------------------------------
def test_compare_page_renders_the_v3_picker_and_personalization(v3_client):
    client, _, _ = v3_client
    html = client.get("/compare").get_data(as_text=True)
    assert 'data-engine="comparison-v3/1"' in html
    assert 'id="compareV2Picker"' not in html and 'id="car_search_1"' not in html
    for n in (1, 2, 3):
        for part in ("make", "model", "year", "trim"):
            assert f'id="v3_{part}_{n}"' in html
        assert f'id="v3_price_{n}"' in html
    assert html.count("מחיר מבוקש (₪)") == 3 and 'min="1000" max="3000000"' in html
    assert 'id="v3Personalization"' in html and 'id="v2Personalization"' not in html
    for key in ("purchase_price", "safety", "performance", "efficiency_environment", "practicality", "ev_convenience"):
        assert f'data-v3-priority-key="{key}"' in html
        assert html.count(f'name="v3_pri_{key}"') == 5
    for removed in ("v3_pri_warranty", "v3_pri_equipment", "v3_pri_efficiency\"", "v3_pri_environment",
                    "v3_cargo_need", "v3_road_conditions"):
        assert removed not in html
    assert "compare_v3.js" in html and "compare_v3.css" in html and "compare_v2.js" in html
    assert 'id="compareLegalConfirm"' in html and 'id="compareV2Steps"' in html


def test_template_and_js_have_no_placeholder_or_score_language():
    js = JS.read_text(encoding="utf-8")
    for bad in ("אין מידע", "/100", "ציון", "אמינות"):
        assert bad not in js, bad
    assert "'—'" not in js and '"—"' not in js


# ---------------------------------------------------------------------------
# JS renderer
# ---------------------------------------------------------------------------
@node_required
def test_js_table_three_columns_keyboard_chevrons_no_placeholders(v3_client, tmp_path):
    client, _, _ = v3_client
    data = _post(client, _cars("octavia", "golf", "civic", prices=[120000, 110000, 125000])).get_json()["data"]
    out = _render(tmp_path, data)
    assert out["isV3"] is True
    table, hero = out["table"], out["hero"]
    assert "--v3-cols:3" in table
    assert table.count('class="v3-cell v3-car" role="columnheader"') == 3
    assert "₪120,000" in table and "2023" in table
    # every chevron is a real button (Enter / Space work natively) controlling an existing hidden panel
    buttons = re.findall(r'<button type="button" class="v3-chevron[^"]*" aria-expanded="false" aria-controls="([^"]+)" data-v3-toggle>', table)
    assert len(buttons) == table.count("data-v3-toggle") and len(buttons) > 10
    for panel in buttons:
        assert f'id="{panel}" class="v3-panel"' in table
        assert re.search(f'id="{panel}" class="v3-panel"[^>]*hidden', table)
    assert 'data-row="wheelbase_mm"' not in table               # the Civic has no wheelbase
    for bad in ("אין מידע", "—", "null", "undefined", ">None<", "NaN"):
        assert bad not in table + hero, bad
    # leader cells are bold + marked; display-only rows never are
    assert "v3-lead" in table and "●" in table
    row = re.search(r'data-row="seats">(.*?)</div></div>', table)
    assert row and "v3-lead" not in row.group(1)
    assert "מקורות: משרד התחבורה; EEA (CC BY 4.0)" in table


@node_required
def test_js_recall_details_render_the_tripy_keys(v3_client, tmp_path):
    client, _, _ = v3_client
    data = _post(client, _cars("octavia", "golf", prices=[120000, 110000])).get_json()["data"]
    table = _render(tmp_path, data)["table"]
    assert "<li>2023 · בלמים · דליפה בצינור בלם · החלפת צינור בלם · ייצור 2022-01–2023-06</li>" in table
    assert "₪150,000–₪175,000 (4 מחירים במחירון לשנה זו)" in table
    assert "משרד התחבורה — מאגר data.gov.il" in table


@node_required
def test_js_renderer_caps_columns_at_three_and_escapes(tmp_path):
    result = {"engine_version": "comparison-v3/1", "recommendation": {"title_he": "<img src=x>"},
              "table": {"slots": ["car_1", "car_2", "car_3", "car_4"],
                        "cars": {s: {"display_name": f"<b>{s}</b>"} for s in ("car_1", "car_2", "car_3", "car_4")},
                        "sections": [{"key": "safety", "label_he": "בטיחות", "influence_he": "x", "rows": [
                            {"row_id": "airbags", "label_he": "כריות אוויר", "display_only": False,
                             "explanation_he": "<script>alert(1)</script>",
                             "cells": {s: {"text": "7", "leader": s == "car_1"} for s in ("car_1", "car_2", "car_3", "car_4")}}]}]}}
    out = _render(tmp_path, result)
    assert "--v3-cols:3" in out["table"] and 'data-slot="car_4"' not in out["table"]
    assert "<script>" not in out["table"] and "<img" not in out["hero"] and "&lt;b&gt;car_1" in out["table"]


@node_required
def test_js_toggle_opens_the_explanation_panel(tmp_path):
    script = f"""
        const api = require({json.dumps(str(JS))});
        const panels = {{p1: {{hidden: true}}}};
        global.document = {{getElementById: (id) => panels[id]}};
        const attrs = {{'aria-expanded': 'false', 'aria-controls': 'p1'}};
        const button = {{getAttribute: (k) => attrs[k], setAttribute: (k, v) => {{ attrs[k] = v; }}}};
        let handler = null;
        const container = {{addEventListener: (type, fn) => {{ handler = fn; }}}};
        api.bindToggles(container);
        const event = {{target: {{closest: () => button}}}};
        handler(event);
        const opened = [attrs['aria-expanded'], panels.p1.hidden];
        handler(event);
        process.stdout.write(JSON.stringify({{opened, closed: [attrs['aria-expanded'], panels.p1.hidden]}}));
    """
    out = json.loads(_node(script))
    assert out == {"opened": ["true", False], "closed": ["false", True]}


# ---------------------------------------------------------------------------
# CSS at 375px
# ---------------------------------------------------------------------------
MOBILE_SNAPSHOT = """@media (max-width: 639px) {
    .v3-row { grid-template-columns: repeat(var(--v3-cols), minmax(0, 1fr)); }
    .v3-row > .v3-label { grid-column: 1 / -1; padding-bottom: .1rem; font-weight: 700; }
    .v3-head > .v3-label { display: none; }
    .v3-label, .v3-cell { padding-inline: .5rem; font-size: .84rem; }
}"""


def test_css_mobile_snapshot_label_above_values_no_fixed_widths():
    css = CSS.read_text(encoding="utf-8")
    assert MOBILE_SNAPSHOT in css
    assert "grid-template-columns: minmax(7rem, 1.2fr) repeat(var(--v3-cols), minmax(0, 1fr))" in css
    layout = re.sub(r"\.v3-table \.sr-only \{[^}]*\}", "", css)       # the visually-hidden helper is 1px by design
    assert not re.search(r"(?<![-\w])width:\s*\d+(px|rem)", layout)   # no fixed widths anywhere
    assert "overflow-wrap: anywhere" in css and "nowrap" not in css


def test_css_has_no_horizontal_scroll_at_375px_in_a_browser(v3_client, tmp_path):
    sync_api = pytest.importorskip("playwright.sync_api")
    client, _, _ = v3_client
    data = _post(client, _cars("octavia", "golf", "civic", prices=[1200000, 1100000, 1250000])).get_json()["data"]
    page_html = f"""<!doctype html><html dir="rtl"><head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1"><style>{CSS.read_text(encoding='utf-8')}
        body {{ margin: 0; padding: 0 16px; font-family: sans-serif; }} .sr-only {{ position: absolute; width: 1px;
        height: 1px; overflow: hidden; clip: rect(0 0 0 0); }}</style></head><body><div id="winnerDisplay"></div>
        <div id="categoriesSection"></div><script>{JS.read_text(encoding='utf-8')}</script>
        <script>window.YedaCompareV3.renderResult({json.dumps(data, ensure_ascii=False)});
        document.querySelectorAll('[data-v3-toggle]').forEach(b => b.click());</script></body></html>"""
    (tmp_path / "page.html").write_text(page_html, encoding="utf-8")
    try:
        with sync_api.sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception:  # noqa: BLE001 - a preinstalled Chromium under PLAYWRIGHT_BROWSERS_PATH
                found = sorted(Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/nonexistent")).glob("chromium-*/chrome-linux/chrome"))
                if not found:
                    raise
                browser = p.chromium.launch(executable_path=str(found[-1]))
            page = browser.new_page(viewport={"width": 375, "height": 800})
            page.goto((tmp_path / "page.html").as_uri())
            metrics = page.evaluate("""() => ({scroll: document.documentElement.scrollWidth,
                labelTop: document.querySelector('[data-row="horsepower"] .v3-label').getBoundingClientRect().bottom,
                cellTop: document.querySelector('[data-row="horsepower"] .v3-cell').getBoundingClientRect().top,
                open: document.querySelectorAll('.v3-panel:not([hidden])').length})""")
            browser.close()
    except Exception as exc:  # noqa: BLE001 - no browser in this environment
        pytest.skip(f"chromium unavailable: {exc}")
    assert metrics["scroll"] <= 375
    assert metrics["labelTop"] <= metrics["cellTop"]            # the label sits above the values
    assert metrics["open"] > 10


# ---------------------------------------------------------------------------
# stored history of older engines
# ---------------------------------------------------------------------------
@node_required
def test_stored_v2_result_still_renders_under_the_v3_default(v3_client, app, tmp_path):
    client, user_id, _ = v3_client
    stored = json.loads(V21_FIXTURE.read_text(encoding="utf-8"))
    with app.app_context():
        row = ComparisonHistory(
            user_id=user_id, cars_selected=json.dumps(stored["cars_selected_list"]),
            computed_result=json.dumps({"engine_version": "comparison-v2/1", "cacheable": True, "response": stored}),
            model_json_raw=json.dumps({"engine_version": "comparison-v2/1"}), sources_index=json.dumps({}),
            prompt_version="comparison-v2/1",
        )
        db.session.add(row)
        db.session.commit()
        row_id = row.id
    detail = client.get(f"/api/compare/{row_id}").get_json()["data"]
    (tmp_path / "old.json").write_text(json.dumps(detail["v2_result"], ensure_ascii=False), encoding="utf-8")
    out = json.loads(_node(f"""
        const v2 = require({json.dumps(str(ROOT / 'static' / 'compare_v2.js'))});
        const v3 = require({json.dumps(str(JS))});
        const r = JSON.parse(require('fs').readFileSync({json.dumps(str(tmp_path / 'old.json'))}, 'utf8'));
        const els = {{winnerDisplay: {{innerHTML: ''}}, categoriesSection: {{innerHTML: ''}}}};
        v2.renderResult(r, els);
        process.stdout.write(JSON.stringify({{isV3: v3.isV3Result(r), isV2: v2.isV2Result(r), hero: els.winnerDisplay.innerHTML}}));
    """))
    assert out["isV3"] is False and out["isV2"] is True
    assert "BMW i4 eDrive35" in out["hero"]


def test_stored_v3_result_reloads_from_history(v3_client):
    client, _, _ = v3_client
    data = _post(client, _cars("octavia", "golf")).get_json()["data"]
    detail = client.get(f"/api/compare/{data['comparison_id']}").get_json()["data"]
    assert detail["engine_version"] == "comparison-v3/1"
    assert detail["v2_result"]["table"]["sections"] == data["table"]["sections"]
