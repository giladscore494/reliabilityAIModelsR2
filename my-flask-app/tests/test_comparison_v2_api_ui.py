# -*- coding: utf-8 -*-
"""Offline end-to-end: Level 1.5 -> mocked Level 2 -> deterministic engine ->
buyer-profile/2 -> mocked JEV micro-judgments -> composition -> summary ->
API -> UI renderer. No paid calls."""

import copy
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
V21_FIXTURE = ROOT / "tests" / "fixtures" / "comparison_v2_1_result.json"
GENERAL = {"mode": "general"}
PRI = dict(safety=2, performance=2, efficiency=2, practicality=2, purchase_price=2, warranty=2, equipment=2, environment=2)


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
    body = {"cars": [{"variant_identity_key": k} for k in keys], "legal_confirm": True, "buyer_profile": GENERAL, **extra}
    return client.post("/api/compare", json=body, headers=headers)


def test_e2e_audi_vs_tucson_json(v2_client, app):
    client, user_id, counters = v2_client
    resp = _post(client, [AUDI_Q3, HYUNDAI_TUCSON])
    assert resp.status_code == 200, resp.get_json()
    data = resp.get_json()["data"]
    assert data["engine_version"] == "comparison-v2/2"
    assert data["cars"]["car_1"]["display_name"] == "Audi Q3"
    assert data["recommendation"]["outcome"] == "car_2"
    assert data["buyer_profile"]["schema"] == "buyer-profile/2"
    assert "decision_trace" not in data  # owner/debug only
    assert data["summary_source"] == "gemini"
    assert data["progress"][:4] == ["resolving_vehicles", "loading_government_data", "enriching_car_1", "enriching_car_2"]
    assert data["progress"][-1] == "complete"
    assert data["comparison_id"]
    meta = data["provider_meta"]
    assert (meta["remote_enrichment_calls"], meta["jev_calls"], meta["summary_calls"]) == (4, 1, 1)
    # history list + detail reload
    hist = client.get("/api/compare/history").get_json()["data"]["history"]
    assert hist[0]["cars"][0]["make"] == "Audi"
    detail = client.get(f"/api/compare/{data['comparison_id']}").get_json()["data"]
    assert detail["engine_version"] == "comparison-v2/2"
    assert detail["v2_result"]["recommendation"] == data["recommendation"]
    assert detail["v2_result"]["buyer_profile"] == data["buyer_profile"]
    assert detail["v2_result"]["comparison_id"] == data["comparison_id"]
    assert "decision_trace" not in detail["v2_result"]
    # ...but the full audit trail is persisted
    with app.app_context():
        stored = json.loads(db.session.get(ComparisonHistory, data["comparison_id"]).computed_result)
    trace = stored["response"]["decision_trace"]
    for key in ("normalized_buyer_profile", "hard_constraints", "jev_question_specs", "jev_answers", "pairwise_direction",
                "materiality_scores", "contextual_fit_scores", "category_contributions", "effective_weight_coverage",
                "overall_composition", "provider_model", "usage"):
        assert key in trace, key
    assert "ts-test-secret-key" not in json.dumps(stored)


def test_owner_sees_decision_trace(v2_client, app):
    client, _, _ = v2_client
    app.config["OWNER_EMAILS"] = {"tester@example.com"}
    data = _post(client, [AUDI_Q3, HYUNDAI_TUCSON]).get_json()["data"]
    assert data["decision_trace"]["jev_question_specs"]
    detail = client.get(f"/api/compare/{data['comparison_id']}").get_json()["data"]
    assert "decision_trace" in detail["v2_result"]


def test_buyer_profile_mode_is_required_and_validated(v2_client):
    client, _, counters = v2_client
    missing = _post(client, [AUDI_Q3, HYUNDAI_TUCSON], buyer_profile=None)
    assert missing.status_code == 400
    assert missing.get_json()["error"]["code"] == "invalid_buyer_profile"
    assert "השוואה מותאמת" in missing.get_json()["error"]["message"]
    bad = _post(client, [AUDI_Q3, HYUNDAI_TUCSON], buyer_profile={"mode": "personalized", "main_use": "space", "priorities": PRI})
    assert bad.status_code == 400
    free_text = _post(client, [AUDI_Q3, HYUNDAI_TUCSON],
                      buyer_profile={"mode": "personalized", "main_use": "family", "cargo_need": "גדול מאוד", "priorities": PRI})
    assert free_text.status_code == 400
    assert counters["provider"].calls == [] and counters["session"].post_calls == []


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
    assert "decision_trace" not in final["data"]
    ev = final["data"]["categories"]["electric_and_charging"]
    assert ev["evidence_status"] != "not_applicable"
    assert "dc_charging_power_kw" in ev["evidence"]["conflicted_metrics"]


def _drop_invalid_audi_commercial_claims(provider):
    """The Audi fixture's price/warranty claims are deliberately invalid
    (foreign price, lookalike host): those groups are 'rejected', which by
    design never freezes a whole-comparison cache. Remove them so the Audi
    commercial groups are a genuinely empty (healthy) observation."""
    outputs = copy.deepcopy(provider.outputs)
    claims = outputs[AUDI_Q3]["raw"]["claims"]
    outputs[AUDI_Q3]["raw"]["claims"] = [c for c in claims if c["field"] not in ("official_price_ils", "warranty_vehicle_years")]
    provider.outputs = outputs


def test_whole_comparison_cache_hit_is_zero_remote_calls(v2_client):
    # Tucson + BMW: every Level 2 group is a healthy observation (complete /
    # partial / genuinely empty), so the whole comparison may be cached.
    client, _, counters = v2_client
    first = _post(client, [HYUNDAI_TUCSON, BMW_I4]).get_json()["data"]
    calls = (len(counters["provider"].calls), len(counters["session"].post_calls), counters["writer"].calls)
    second = _post(client, [HYUNDAI_TUCSON, BMW_I4]).get_json()["data"]
    assert second["cached"] is True
    assert second["comparison_id"] != first["comparison_id"]
    assert (len(counters["provider"].calls), len(counters["session"].post_calls), counters["writer"].calls) == calls


def test_different_profiles_never_share_a_decision_cache_entry(v2_client):
    client, _, counters = v2_client
    perf = {"mode": "personalized", "main_use": "highway", "priorities": {**PRI, "performance": 4}}
    family = {"mode": "personalized", "main_use": "family", "priorities": {**PRI, "practicality": 4}}
    _drop_invalid_audi_commercial_claims(counters["provider"])
    a = _post(client, [AUDI_Q3, HYUNDAI_TUCSON], buyer_profile=perf).get_json()["data"]
    b = _post(client, [AUDI_Q3, HYUNDAI_TUCSON], buyer_profile=family).get_json()["data"]
    assert a["cached"] is False and b["cached"] is False
    assert len(counters["session"].post_calls) == 2  # each profile got its own judgments
    # identical normalized profile (key order / omitted defaults) -> cache hit
    same = {"priorities": {**PRI, "performance": 4}, "main_use": "highway", "mode": "personalized", "must_have_features": []}
    c = _post(client, [AUDI_Q3, HYUNDAI_TUCSON], buyer_profile=same).get_json()["data"]
    assert c["cached"] is True and c["recommendation"] == a["recommendation"]


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


def test_flag_on_renders_v2_picker_and_personalization_step(v2_client):
    client, _, _ = v2_client
    html = client.get("/compare").get_data(as_text=True)
    assert 'id="compareV2Picker"' in html
    assert 'id="car_search_1"' not in html  # manual engine/gear controls removed in V2
    assert html.count('<option value="182116040879ae9539249b243610e1db750726454cbe0eb645a7b696bf96abdf">') == 3
    assert "Audi Q3 · 2024 · S LINE" in html
    assert 'id="compareV2Steps"' in html
    assert 'id="compareLegalConfirm"' in html  # legal consent preserved
    assert 'for="v2_variant_1"' in html and 'aria-required="true"' in html
    assert "/100" not in html
    # personalization step: explicit mode choice, nothing preselected
    assert 'id="v2Personalization"' in html and 'id="buyerProfilePanel"' not in html
    assert "השוואה מותאמת אליי" in html and "השוואה כללית" in html
    assert html.count('name="v2_mode"') == 2 and 'name="v2_mode" value="personalized" checked' not in html
    assert "מה השימוש העיקרי ברכב?" in html and "כמה ק״מ אתה נוסע בערך בשנה?" in html
    assert "כמה אנשים נוסעים ברכב בדרך כלל, כולל הנהג?" in html
    # 0-4 priority radios for each dimension (+ EV-only row)
    for key in list(PRI) + ["ev_convenience"]:
        assert html.count(f'name="v2_pri_{key}"') == 5, key
    assert "צרכים ודרישות נוספות" in html and "תקציב מקסימלי לרכישה" in html and "אני צריך לגרור" in html
    assert "כמה תא מטען חשוב לך בפועל?" in html and "לדוגמה:" in html
    assert 'id="v2EvSection"' in html and "טעינה ונסיעות חשמליות" in html
    assert "אבזור שחייב להיות" in html and "אבזור שהיית שמח/ה שיהיה" in html
    assert html.count("עדיין לא נכלל בגרסה הזו") == 3 and "disabled aria-disabled" in html
    for removed in ('id="buyer_family_size"', 'data-priority="reliability"', 'data-priority="cost"'):
        assert removed not in html


def test_template_and_js_have_no_score_language():
    js = (ROOT / "static" / "compare_v2.js").read_text(encoding="utf-8")
    html = (ROOT / "templates" / "compare.html").read_text(encoding="utf-8")
    for text in (js, html):
        assert "/100" not in text
    # the only allowed occurrence is the mandated "not a quality score" tooltip (V2/1 rows)
    assert "ציון" not in js.replace("אינו ציון איכות של הרכב", "")
    # V2/1 renderer (stored history) is still present
    for phrase in ("היתרון הכולל כרגע", "ביטחון ההכרעה", "כיסוי נתונים", "Level 1.5 — ממשלתי", "Level 2 — יצרן/יבואן רשמי",
                   "משרד התחבורה", "יבואן רשמי", "יצרן", "לא נמצא מקור רשמי מדויק לגרסה הזו",
                   "אין מספיק מידע להשוואה אמינה בתחום הזה", "נמצאה סתירה בין מקורות רשמיים ולכן הנתון לא השתתף בהכרעה",
                   "אין כרגע יתרון משמעותי", "אין מספיק מידע להכרעה כוללת",
                   "מדד ניסיוני של מנוע ההכרעה; אינו ציון איכות של הרכב."):
        assert phrase in js, phrase
    # V2/2 renderer vocabulary
    for phrase in ("מה הקטגוריה בודקת", "מה חשוב לך כאן", "מה הנתונים אומרים", "למה זה השפיע או לא השפיע",
                   "כל הנתונים והמקורות", "הסיבות המרכזיות", "ודאות מודל בשיפוט הזה", "זה אינו סיכוי שההמלצה נכונה"):
        assert phrase in js, phrase
    for label in ("מזהה גרסאות", "טוען נתונים רשמיים", "משלים מידע מאתרי היצרן", "מאמת את המקורות",
                  "משווה את הנתונים", "שוקל את ההבדלים לפי הצרכים שלך", "מנסח את הסיכום"):
        assert label in js


def _node(script):
    return subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_renderer_on_real_pipeline_output(v2_client, tmp_path):
    client, _, _ = v2_client
    profile = {"mode": "personalized", "main_use": "commuting", "annual_km": 30000, "regular_passengers": 7,
               "charging_access": "home", "priorities": {**PRI, "ev_convenience": 4, "performance": 0}}
    data = _post(client, [BMW_I4, XPENG_P7I], buyer_profile=profile).get_json()["data"]
    (tmp_path / "result.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    script = f"""
        const api = require({json.dumps(str(ROOT / 'static' / 'compare_v2.js'))});
        const result = JSON.parse(require('fs').readFileSync({json.dumps(str(tmp_path / 'result.json'))}, 'utf8'));
        process.stdout.write(JSON.stringify({{hero: api.buildHeroV22Html(result), body: api.buildResultBodyV22Html(result),
                                              isV2: api.isV2Result(result)}}));
    """
    out = json.loads(_node(script))
    hero, body = out["hero"], out["body"]
    assert out["isV2"] is True
    assert 'data-engine="comparison-v2/2"' in hero and "data-v2-profile-summary" in hero
    assert "נסיעות יומיות לעבודה" in hero and "ביצועים: <strong>לא חשוב</strong>" in hero
    # 7 passengers > 5 seats: a hard requirement fails and is shown explicitly
    assert "אי-עמידה בדרישות חובה" in hero and "אינו עומד בדרישת מספר נוסעים" in hero
    assert data["recommendation"]["outcome"] == "no_vehicle_meets_requirements"
    assert "אף אחד מהרכבים אינו עומד בכל דרישות החובה שהגדרת" in hero
    for layer in ("what", "importance", "data", "influence"):
        assert body.count(f'data-layer="{layer}"') >= 5, layer
    assert 'data-category="equipment_and_convenience"' in body
    assert "ביטחון ההכרעה" not in hero + body and "%" not in hero
    assert "/100" not in hero + body
    assert 'href="https://www.bmw.co.il/' in body and 'rel="noopener noreferrer"' in body


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_renders_reasons_and_model_certainty_label(v2_client, tmp_path):
    client, _, _ = v2_client
    data = _post(client, [AUDI_Q3, HYUNDAI_TUCSON],
                 buyer_profile={"mode": "personalized", "main_use": "family", "priorities": {**PRI, "safety": 4}}).get_json()["data"]
    (tmp_path / "r.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    out = json.loads(_node(f"""
        const api = require({json.dumps(str(ROOT / 'static' / 'compare_v2.js'))});
        const r = JSON.parse(require('fs').readFileSync({json.dumps(str(tmp_path / 'r.json'))}, 'utf8'));
        process.stdout.write(JSON.stringify({{hero: api.buildHeroV22Html(r), body: api.buildResultBodyV22Html(r)}}));
    """))
    assert "מתאים יותר לצרכים שהגדרת" in out["hero"] and "Hyundai Tucson Hybrid" in out["hero"]
    assert "הסיבות המרכזיות" in out["hero"] and "בטיחות — קריטי עבורך" in out["hero"]
    assert "ודאות מודל בשיפוט הזה" in out["body"] and "אינו הסתברות שההמלצה נכונה" in out["body"]
    assert "השפיע לטובת" in out["body"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_stored_v21_result_still_renders_with_its_own_contract(v2_client, app, tmp_path):
    client, user_id, _ = v2_client
    stored = json.loads(V21_FIXTURE.read_text(encoding="utf-8"))
    assert stored["engine_version"] == "comparison-v2/1"
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
    assert detail["engine_version"] == "comparison-v2/1"
    assert detail["v2_result"]["overall"] == stored["overall"]
    (tmp_path / "old.json").write_text(json.dumps(detail["v2_result"], ensure_ascii=False), encoding="utf-8")
    out = json.loads(_node(f"""
        const api = require({json.dumps(str(ROOT / 'static' / 'compare_v2.js'))});
        const r = JSON.parse(require('fs').readFileSync({json.dumps(str(tmp_path / 'old.json'))}, 'utf8'));
        const els = {{winnerDisplay: {{innerHTML: ''}}, categoriesSection: {{innerHTML: ''}}}};
        api.renderResult(r, els);
        process.stdout.write(JSON.stringify({{isV2: api.isV2Result(r), hero: els.winnerDisplay.innerHTML, body: els.categoriesSection.innerHTML}}));
    """))
    assert out["isV2"] is True
    assert "היתרון הכולל כרגע" in out["hero"] and "BMW i4 eDrive35" in out["hero"]
    assert 'data-category="safety"' in out["body"] and "data-layer" not in out["body"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_renderer_escapes_untrusted_strings():
    script = f"""
        const api = require({json.dumps(str(ROOT / 'static' / 'compare_v2.js'))});
        const r = {{engine_version: 'comparison-v2/1', cars: {{car_1: {{display_name: '<img src=x onerror=alert(1)>'}}, car_2: {{display_name: 'B'}}}},
                   overall: {{choice: 'car_1', confidence: 0.5}}, coverage: {{}}, categories: {{}}, sources: {{car_1: [{{source_url: 'javascript:alert(1)', source_title: '<b>x</b>'}}]}}, diagnostics: {{}}}};
        const r2 = Object.assign({{}}, r, {{engine_version: 'comparison-v2/2', profile_summary: {{headline: ['<script>x</script>']}},
                   recommendation: {{outcome: 'car_1', title_he: '<i>t</i>', reasons_he: {{for: ['<svg onload=1>'], against: []}}}},
                   hard_constraints: {{notes: [{{level: 'fail', text_he: '<b>n</b>'}}]}},
                   categories: {{safety: {{key: 'safety', label_he: '<u>', evidence_status: 'ready', influence_status: 'influenced',
                                          favoured_slot: 'car_1', layers: {{what: '<a>', importance: 'x', data: 'y', influence: 'z'}}, pairs: [], gaps: []}}}},
                   category_order: ['safety']}});
        process.stdout.write(api.buildHeroHtml(r) + api.buildResultBodyHtml(r) + api.buildHeroV22Html(r2) + api.buildResultBodyV22Html(r2));
    """
    html = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout
    for tag in ("<img", "<script", "<svg", "<b>n", "<i>t", "<u>", "<a>"):
        assert tag not in html, tag
    assert "&lt;img" in html
    assert "javascript:" not in html.replace("&lt;", "")
