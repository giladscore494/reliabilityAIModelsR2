# -*- coding: utf-8 -*-
"""Offline end-to-end: Level 1.5 -> mocked Level 2 -> deterministic engine ->
mocked JEV -> summary -> API -> UI renderer. No paid calls."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.extensions import db
from app.models import ComparisonHistory

from comparison_v2_fakes import (
    AUDI_Q3,
    BMW_I4,
    HYUNDAI_TUCSON,
    XPENG_P7I,
    FakeEnrichmentProvider,
    FakeSummaryWriter,
    FakeTypeSafeSession,
    build_fake_deps,
)

ROOT = Path(__file__).resolve().parents[1]
HEADERS = {"Content-Type": "application/json", "Origin": "http://localhost"}


@pytest.fixture
def v2_client(app, logged_in_client, monkeypatch):
    monkeypatch.setenv("COMPARISON_V2_ENABLED", "true")
    client, user_id = logged_in_client
    client.post("/api/legal/accept", json={"legal_confirm": True}, headers=HEADERS)
    counters = {"provider": FakeEnrichmentProvider(), "session": FakeTypeSafeSession(), "writer": FakeSummaryWriter()}

    def fake_deps(ai_client=None):
        from app.services.comparison_v2.pipeline import ComparisonV2HistoryStore

        return build_fake_deps(
            provider=counters["provider"], session=counters["session"], writer=counters["writer"],
            history=ComparisonV2HistoryStore(),
        )

    monkeypatch.setattr("app.services.comparison_v2.pipeline.build_default_deps", fake_deps)
    return client, user_id, counters


def _post(client, keys, stream=False, **extra):
    headers = dict(HEADERS)
    if stream:
        headers["Accept"] = "application/x-ndjson"
    body = {"cars": [{"variant_identity_key": k} for k in keys], "legal_confirm": True, **extra}
    return client.post("/api/compare", json=body, headers=headers)


def test_e2e_audi_vs_tucson_json(v2_client, app):
    client, user_id, counters = v2_client
    resp = _post(client, [AUDI_Q3, HYUNDAI_TUCSON])
    assert resp.status_code == 200, resp.get_json()
    data = resp.get_json()["data"]
    assert data["engine_version"] == "comparison-v2/1"
    assert data["cars"]["car_1"]["display_name"] == "Audi Q3"
    assert data["overall"]["choice"] == "car_2"
    assert data["summary_source"] == "gemini"
    assert data["progress"][:4] == ["resolving_vehicles", "loading_government_data", "enriching_car_1", "enriching_car_2"]
    assert data["progress"][-1] == "complete"
    assert data["comparison_id"]
    meta = data["provider_meta"]
    assert (meta["remote_enrichment_calls"], meta["jev_calls"], meta["summary_calls"]) == (2, 1, 1)
    # history list + detail reload
    hist = client.get("/api/compare/history").get_json()["data"]["history"]
    assert hist[0]["cars"][0]["make"] == "Audi"
    detail = client.get(f"/api/compare/{data['comparison_id']}").get_json()["data"]
    assert detail["engine_version"] == "comparison-v2/1"
    assert detail["v2_result"]["overall"] == data["overall"]
    assert detail["v2_result"]["comparison_id"] == data["comparison_id"]


def test_e2e_bmw_vs_xpeng_stream(v2_client):
    client, _, _ = v2_client
    resp = _post(client, [BMW_I4, XPENG_P7I], stream=True)
    assert resp.status_code == 200 and resp.mimetype == "application/x-ndjson"
    # the response-logging hook must not buffer the stream into a list
    assert resp.is_streamed and not isinstance(resp.response, list)
    events = [json.loads(line) for line in resp.get_data(as_text=True).strip().split("\n")]
    stages = [e["stage"] for e in events if e["type"] == "progress"]
    assert stages == ["resolving_vehicles", "loading_government_data", "enriching_car_1", "enriching_car_2",
                      "validating_sources", "comparing_facts", "evaluating_decision", "writing_summary", "complete"]
    assert all(e["label_he"] for e in events if e["type"] == "progress")
    final = events[-1]
    assert final["type"] == "result" and final["ok"] is True
    ev = final["data"]["categories"]["electric_and_charging"]
    assert ev["status"] != "not_applicable"
    assert "dc_charging_power_kw" in ev["evidence"]["conflicted_metrics"]


def test_whole_comparison_cache_hit_is_zero_remote_calls(v2_client):
    client, _, counters = v2_client
    first = _post(client, [AUDI_Q3, HYUNDAI_TUCSON]).get_json()["data"]
    calls = (len(counters["provider"].calls), len(counters["session"].post_calls), counters["writer"].calls)
    second = _post(client, [AUDI_Q3, HYUNDAI_TUCSON]).get_json()["data"]
    assert second["cached"] is True
    assert second["comparison_id"] != first["comparison_id"]
    assert (len(counters["provider"].calls), len(counters["session"].post_calls), counters["writer"].calls) == calls


def test_v2_requires_exact_variant_and_rejects_duplicates(v2_client):
    client, _, _ = v2_client
    legacy = client.post("/api/compare", json={"cars": [{"make": "Audi", "model": "Q3"}, {"make": "BMW", "model": "i4"}], "legal_confirm": True}, headers=HEADERS)
    assert legacy.status_code == 400 and legacy.get_json()["error"]["code"] == "variant_required"
    dup = _post(client, [AUDI_Q3, AUDI_Q3])
    assert dup.status_code == 400
    unknown = _post(client, ["0" * 64, AUDI_Q3])
    assert unknown.status_code == 404, unknown.get_json()


def test_legacy_stored_comparison_still_readable(v2_client, app):
    client, user_id, _ = v2_client
    with app.app_context():
        row = ComparisonHistory(
            user_id=user_id,
            cars_selected=json.dumps([{"make": "Toyota", "model": "Corolla", "year": 2020}, {"make": "Honda", "model": "Civic", "year": 2021}]),
            computed_result=json.dumps({"decision_result": {"overall_decision": {"label": "car_1", "text": "x"}}}),
            model_json_raw=json.dumps({}),
            sources_index=json.dumps({}),
            prompt_version="v1",
        )
        db.session.add(row)
        db.session.commit()
        row_id = row.id
    detail = client.get(f"/api/compare/{row_id}").get_json()["data"]
    assert "engine_version" not in detail
    assert detail["decision_result"]["overall_decision"]["label"] == "car_1"


def test_flag_off_keeps_legacy_ui(app, logged_in_client, monkeypatch):
    monkeypatch.delenv("COMPARISON_V2_ENABLED", raising=False)
    client, _ = logged_in_client
    html = client.get("/compare").get_data(as_text=True)
    assert 'id="car_search_1"' in html and "compareV2Picker" not in html
    assert "compare_v2.js" in html  # renderer always available for V2 history rows


def test_flag_on_renders_v2_picker(v2_client):
    client, _, _ = v2_client
    html = client.get("/compare").get_data(as_text=True)
    assert 'id="compareV2Picker"' in html
    assert 'id="car_search_1"' not in html  # manual engine/gear controls removed in V2
    assert html.count('<option value="182116040879ae9539249b243610e1db750726454cbe0eb645a7b696bf96abdf">') == 3
    assert "Audi Q3 · 2024 · S LINE" in html
    assert 'id="compareV2Steps"' in html
    assert 'id="v2ReliabilityNote"' in html and "אמינות ארוכת טווח עדיין אינה נכללת" in html
    assert 'id="compareLegalConfirm"' in html  # legal consent preserved
    assert 'for="v2_variant_1"' in html and 'aria-required="true"' in html
    assert "/100" not in html


def test_template_and_js_have_no_score_language():
    js = (ROOT / "static" / "compare_v2.js").read_text(encoding="utf-8")
    html = (ROOT / "templates" / "compare.html").read_text(encoding="utf-8")
    for text in (js, html):
        assert "/100" not in text
    # the only allowed occurrence is the mandated "not a quality score" tooltip
    assert "ציון" not in js.replace("אינו ציון איכות של הרכב", "")
    for phrase in ("היתרון הכולל כרגע", "ביטחון ההכרעה", "כיסוי נתונים", "Level 1.5 — ממשלתי", "Level 2 — יצרן/יבואן רשמי",
                   "משרד התחבורה", "יבואן רשמי", "יצרן", "לא נמצא מקור רשמי מדויק לגרסה הזו",
                   "אין מספיק מידע להשוואה אמינה בתחום הזה", "נמצאה סתירה בין מקורות רשמיים ולכן הנתון לא השתתף בהכרעה",
                   "אין כרגע יתרון משמעותי", "אין מספיק מידע להכרעה כוללת",
                   "מדד ניסיוני של מנוע ההכרעה; אינו ציון איכות של הרכב."):
        assert phrase in js, phrase
    for label in ("מזהה גרסאות", "טוען נתונים רשמיים", "משלים מידע מאתרי היצרן", "מאמת את המקורות",
                  "משווה את הנתונים", "מחשב את ההכרעה", "מנסח את הסיכום"):
        assert label in js


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_renderer_on_real_pipeline_output(v2_client, tmp_path):
    client, _, _ = v2_client
    data = _post(client, [BMW_I4, XPENG_P7I]).get_json()["data"]
    (tmp_path / "result.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    script = f"""
        const api = require({json.dumps(str(ROOT / 'static' / 'compare_v2.js'))});
        const result = JSON.parse(require('fs').readFileSync({json.dumps(str(tmp_path / 'result.json'))}, 'utf8'));
        const hero = api.buildHeroHtml(result);
        const body = api.buildResultBodyHtml(result);
        process.stdout.write(JSON.stringify({{hero, body, isV2: api.isV2Result(result)}}));
    """
    out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
    hero, body = out["hero"], out["body"]
    assert out["isV2"] is True
    assert "היתרון הכולל כרגע" in hero and "XPENG P7i" in hero
    assert "ביטחון ההכרעה: <strong>88%</strong>" in hero
    assert "ממשלה:" in hero and "מקורות יצרן:" in hero
    assert "Level 1.5 — ממשלתי" in hero
    assert 'data-category="safety"' in body and 'data-category="electric_and_charging"' in body
    assert "נמצאה סתירה בין מקורות רשמיים" in body
    assert "סיכום" in body and "מקורות ופרטי אימות" in body
    assert 'href="https://www.bmw.co.il/' in body and 'rel="noopener noreferrer"' in body
    assert "/100" not in hero + body


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_renderer_escapes_untrusted_strings():
    script = f"""
        const api = require({json.dumps(str(ROOT / 'static' / 'compare_v2.js'))});
        const r = {{engine_version: 'comparison-v2/1', cars: {{car_1: {{display_name: '<img src=x onerror=alert(1)>'}}, car_2: {{display_name: 'B'}}}},
                   overall: {{choice: 'car_1', confidence: 0.5}}, coverage: {{}}, categories: {{}}, sources: {{car_1: [{{source_url: 'javascript:alert(1)', source_title: '<b>x</b>'}}]}}, diagnostics: {{}}}};
        process.stdout.write(api.buildHeroHtml(r) + api.buildResultBodyHtml(r));
    """
    html = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout
    assert "<img" not in html and "&lt;img" in html
    assert "javascript:" not in html.replace("&lt;", "")
