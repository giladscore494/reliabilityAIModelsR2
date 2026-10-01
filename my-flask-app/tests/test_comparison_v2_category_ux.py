# -*- coding: utf-8 -*-
"""Category explanations, confidence presentation and cross-powertrain UX (offline)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.services.comparison_v2.pipeline import collect_result, run_comparison_v2

from comparison_v2_fakes import (
    AUDI_Q3,
    BMW_I4,
    HYUNDAI_TUCSON,
    XPENG_P7I,
    FakeTypeSafeSession,
    build_fake_deps,
)

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


def run(keys, session=None):
    deps = build_fake_deps(session=session) if session else build_fake_deps()
    out = collect_result(run_comparison_v2({"cars": [{"variant_identity_key": k} for k in keys]}, deps))
    return out["data"], deps


@pytest.mark.parametrize("pair", [(AUDI_Q3, BMW_I4), (AUDI_Q3, HYUNDAI_TUCSON), (BMW_I4, XPENG_P7I)])
def test_every_visible_category_has_a_deterministic_explanation(pair):
    data, _ = run(list(pair))
    for key, cat in data["categories"].items():
        if cat["status"] == "not_applicable":
            assert cat["explanation"] is None
            continue
        exp = cat["explanation"]
        assert exp and exp["what"] and exp["why"] and exp["text"], key
        assert exp["text"].startswith(exp["what"])
        assert isinstance(exp["gaps"], list)
        assert "/100" not in exp["text"] and "ציון" not in exp["text"]


def test_explanation_mirrors_the_jev_choice_and_names_the_car():
    data, _ = run([AUDI_Q3, BMW_I4])
    safety = data["categories"]["safety"]
    assert safety["decision"]["choice"] == "car_2"
    assert "מנוע ההכרעה נתן עדיפות ל־BMW i4 eDrive35" in safety["explanation"]["why"]
    assert "עדיפות ל־BMW i4 eDrive35" in safety["explanation"]["evidence"]


def test_tie_and_insufficient_wording():
    override = {
        "safety": {"type": "choice", "choice": "tie", "confidence": 0.7, "probabilities": {}},
        "performance": {"type": "choice", "choice": "insufficient_evidence", "confidence": 0.9, "probabilities": {}},
    }
    data, _ = run([AUDI_Q3, HYUNDAI_TUCSON], session=FakeTypeSafeSession(answers_override=override))
    assert "אין יתרון משמעותי" in data["categories"]["safety"]["explanation"]["why"]
    perf = data["categories"]["performance"]["explanation"]["why"]
    assert perf == "אין מספיק מידע מאומת ובר-השוואה כדי לבחור רכב בתחום זה."


def test_partial_coverage_is_named_and_missing_is_neutral():
    data, _ = run([AUDI_Q3, BMW_I4])
    perf = data["categories"]["performance"]
    assert "כיסוי חלקי" in perf["explanation"]["why"]
    assert any("נתון חסר אינו נחשב לחיסרון" in g for g in perf["explanation"]["gaps"])
    for cat in data["categories"].values():
        text = json.dumps(cat.get("explanation") or {}, ensure_ascii=False)
        assert "חסר" not in text or "אינו נחשב לחיסרון" in text


def test_jev_failure_explanation_has_no_winner():
    data, _ = run([AUDI_Q3, BMW_I4], session=FakeTypeSafeSession(fail_systemone=True))
    safety = data["categories"]["safety"]["explanation"]
    assert "מנוע ההכרעה לא היה זמין" in safety["why"]
    assert "נתן עדיפות" not in safety["text"]


def test_ev_category_is_descriptive_not_not_applicable_for_ev_vs_petrol():
    session = FakeTypeSafeSession()
    data, _ = run([AUDI_Q3, BMW_I4], session=session)
    ev = data["categories"]["electric_and_charging"]
    assert ev["status"] == "cross_powertrain_descriptive"
    assert ev["decision"]["choice"] == "not_comparable" and ev["decision"]["confidence"] is None
    assert "electric_and_charging" not in session.post_calls[0]["json"]["questions"]  # no winner asked
    # ...but its validated values are still visible to JEV's overall question as context
    assert "electric_and_charging" in session.post_calls[0]["json"]["state"]["deterministic_evidence"]
    assert ev["explanation"]["why"] == (
        "נתוני חשמל וטעינה מוצגים עבור הרכב החשמלי (BMW i4 eDrive35), אך אינם בני-השוואה ישירה מול רכב בנזין "
        "ולכן לא ניתנה הכרעה בתחום."
    )
    shown = [r for r in ev["evidence"]["atomic_results"] if r["values"].get("car_2") is not None]
    assert shown and all(r["leader"] is None for r in shown)


def test_efficiency_cross_powertrain_is_descriptive():
    data, _ = run([AUDI_Q3, BMW_I4])
    eff = data["categories"]["efficiency"]
    assert eff["status"] == "cross_powertrain_descriptive"
    assert "ביחידות שונות לפי סוג ההנעה" in eff["explanation"]["why"]


# --------------------------------------------------------------------------
# rendered UI (real compare_v2.js under node)
# --------------------------------------------------------------------------
def render(data, tmp_path):
    (tmp_path / "r.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    script = f"""
        const api = require({json.dumps(str(ROOT / 'static' / 'compare_v2.js'))});
        const r = JSON.parse(require('fs').readFileSync({json.dumps(str(tmp_path / 'r.json'))}, 'utf8'));
        const cards = {{}};
        for (const k of r.category_order) {{ if (r.categories[k]) cards[k] = api.buildCategoryCardHtml(r, r.categories[k]); }}
        process.stdout.write(JSON.stringify({{hero: api.buildHeroHtml(r), body: api.buildResultBodyHtml(r), cards}}));
    """
    return json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_insufficient_category_shows_no_winner_style_confidence(tmp_path):
    override = {"official_price_and_warranty": {"type": "choice", "choice": "insufficient_evidence", "confidence": 1.0,
                                                "probabilities": {"insufficient_evidence": 1.0}}}
    data, _ = run([AUDI_Q3, BMW_I4], session=FakeTypeSafeSession(answers_override=override))
    out = render(data, tmp_path)
    card = out["cards"]["official_price_and_warranty"]
    assert 'data-badge="insufficient"' in card and "אין מספיק מידע" in card
    assert "ביטחון" not in card and "100%" not in card
    assert "כיסוי ראיות בתחום" in card  # coverage emphasised instead
    safety = out["cards"]["safety"]
    assert 'data-badge="lead"' in safety and "ביטחון ההכרעה" in safety and "88%" in safety


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_overall_insufficient_hero_has_no_confidence(tmp_path):
    override = {"overall": {"type": "choice", "choice": "insufficient_evidence", "confidence": 0.99, "probabilities": {}}}
    data, _ = run([AUDI_Q3, BMW_I4], session=FakeTypeSafeSession(answers_override=override))
    hero = render(data, tmp_path)["hero"]
    assert "אין מספיק מידע להכרעה כוללת" in hero
    assert "ביטחון ההכרעה" not in hero and "99%" not in hero
    assert "כיסוי נתונים" in hero


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_card_hierarchy_and_cross_powertrain_rendering(tmp_path):
    data, _ = run([AUDI_Q3, BMW_I4])
    out = render(data, tmp_path)
    for key, card in out["cards"].items():
        if data["categories"][key]["status"] == "not_applicable":
            continue
        assert "data-v2-explanation" in card, key
        assert "כל הנתונים והמקורות" in card, key
        # order: title -> badge -> explanation -> key facts -> details
        assert card.index("<h4") < card.index("data-badge") < card.index("data-v2-explanation") < card.index("<details")
    ev = out["cards"]["electric_and_charging"]
    assert 'data-badge="no_direct"' in ev and "ללא הכרעה ישירה" in ev
    assert "קוט״ש" in ev  # BMW battery value shown descriptively
    assert "v2-lead-dot" not in ev and "ביטחון" not in ev
    # EV charging is relevant to the BMW, so nothing is "not relevant to any car".
    assert "לא רלוונטי לאף אחד" not in out["body"]
    assert "/100" not in out["body"] + out["hero"]


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_two_petrol_cars_list_ev_as_not_relevant_to_any(tmp_path):
    data, _ = run([AUDI_Q3, HYUNDAI_TUCSON])
    body = render(data, tmp_path)["body"]
    assert "לא רלוונטי לאף אחד מהרכבים שנבחרו: חשמל וטעינה" in body
