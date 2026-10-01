# -*- coding: utf-8 -*-
"""V2/2 category cards: four deterministic layers, influence wording,
confidence presentation and cross-powertrain UX (offline)."""

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
PRI = dict(safety=2, performance=2, efficiency=2, practicality=2, purchase_price=2, warranty=2, equipment=2, environment=2)
GENERAL = {"mode": "general"}


def run(keys, profile=None, session=None):
    deps = build_fake_deps(session=session) if session else build_fake_deps()
    out = collect_result(run_comparison_v2({"cars": [{"variant_identity_key": k} for k in keys]}, deps,
                                           buyer_profile=profile or GENERAL))
    return out["data"]


def personalized(**kw):
    priorities = {**PRI, "ev_convenience": 2, **kw.pop("priorities", {})}
    return {"mode": "personalized", "main_use": kw.pop("main_use", "mixed"), "priorities": priorities, **kw}


@pytest.mark.parametrize("pair", [(AUDI_Q3, BMW_I4), (AUDI_Q3, HYUNDAI_TUCSON), (BMW_I4, XPENG_P7I)])
def test_every_visible_category_has_four_deterministic_layers(pair):
    data = run(list(pair))
    for key, card in data["categories"].items():
        layers = card["layers"]
        assert set(layers) == {"what", "importance", "data", "influence"}, key
        if card["evidence_status"] == "not_applicable":
            continue
        assert all(layers.values()), key
        for text in layers.values():
            assert "/100" not in text and "ציון" not in text and "%" not in text


def test_what_layer_uses_the_static_scope_texts():
    cards = run([AUDI_Q3, HYUNDAI_TUCSON])["categories"]
    assert cards["safety"]["layers"]["what"] == "בודקת דירוגי בטיחות ממשלתיים, מערכות סיוע לנהג, כריות אוויר ומערכות בטיחות זמינות."
    assert cards["towing_and_utility"]["layers"]["what"] == "בודקת את יכולת הגרירה מול צורך אמיתי שהזנת."
    assert cards["equipment_and_convenience"]["label_he"] == "אבזור ונוחות שימוש"
    assert "נוחות נסיעה" in cards["equipment_and_convenience"]["layers"]["what"]  # explicitly NOT ride comfort


def test_importance_layer_is_the_users_declared_value():
    data = run([AUDI_Q3, BMW_I4], personalized(priorities={"safety": 4, "performance": 0, "ev_convenience": 1}))
    cards = data["categories"]
    assert cards["safety"]["layers"]["importance"] == "בטיחות: קריטי"
    assert cards["performance"]["layers"]["importance"] == "ביצועים: לא חשוב"
    assert cards["electric_and_charging"]["layers"]["importance"] == "נוחות טעינה ונסיעה חשמלית: מעט חשוב"
    assert cards["official_price_and_warranty"]["layers"]["importance"] == "מחיר רכישה: בינוני · אחריות: בינוני"
    general = run([AUDI_Q3, BMW_I4])["categories"]["safety"]["layers"]["importance"]
    assert "השוואה כללית" in general and "בינוני" in general


def test_influence_names_the_favoured_car_and_the_materiality():
    data = run([AUDI_Q3, BMW_I4], personalized(priorities={"safety": 4}))
    safety = data["categories"]["safety"]
    assert safety["influence_status"] == "influenced" and safety["favoured_slot"] == "car_2"
    assert safety["layers"]["influence"].startswith("התחום השפיע לטובת BMW i4 eDrive35")
    assert "משמעות הפער לשימוש שלך: גדולה" in safety["layers"]["influence"]
    assert "עדיפות ל־BMW i4 eDrive35" in safety["layers"]["data"]


def test_zero_priority_difference_is_explained_as_no_influence():
    data = run([AUDI_Q3, BMW_I4], personalized(priorities={"performance": 0}))
    perf = data["categories"]["performance"]
    assert perf["influence_status"] == "zero_weight"
    assert perf["layers"]["influence"] == "הפער קיים, אבל התחום הוגדר אצלך כלא חשוב ולכן לא השפיע על ההכרעה הכוללת."
    assert all(pv["contribution"] is None for pv in perf["pairs"])


def test_towing_advantage_without_towing_need_has_no_influence():
    towing = run([AUDI_Q3, BMW_I4])["categories"]["towing_and_utility"]
    assert towing["influence_status"] == "no_towing_need"
    assert towing["layers"]["influence"] == "ל־Audi Q3 יתרון בגרירה, אך לא ציינת צורך בגרירה ולכן היתרון לא השפיע על ההתאמה הכוללת."


def test_important_but_missing_category_is_neutral():
    data = run([AUDI_Q3, BMW_I4], personalized(priorities={"purchase_price": 4}))
    price = data["categories"]["official_price_and_warranty"]
    assert price["influence_status"] == "insufficient_data"
    assert price["layers"]["influence"] == "התחום חשוב לך מאוד, אך אין מספיק מידע מאומת ולכן לא נתנו יתרון לאף רכב."
    for card in data["categories"].values():
        text = json.dumps(card["gaps"], ensure_ascii=False)
        assert "חסר" not in text or "אינו נחשב לחיסרון" in text


def test_jev_failure_layers_have_no_winner():
    data = run([AUDI_Q3, BMW_I4], session=FakeTypeSafeSession(fail_systemone=True))
    safety = data["categories"]["safety"]
    assert safety["influence_status"] == "judgment_unavailable"
    assert "השפיע לטובת" not in safety["layers"]["influence"]
    assert safety["favoured_slot"] is None


def test_cross_powertrain_distinguishes_not_directly_comparable_from_not_applicable():
    data = run([AUDI_Q3, BMW_I4])
    ev = data["categories"]["electric_and_charging"]
    assert ev["evidence_status"] == "not_directly_comparable"
    assert ev["layers"]["data"] == (
        "נתוני חשמל וטעינה של BMW i4 eDrive35 מוצגים משום שהם רלוונטיים לרכב החשמלי, "
        "אך אין להם נתון מקביל ב־Audi Q3 ולכן לא נבחר מנצח ישיר בתחום הזה."
    )
    shown = [r for r in ev["evidence"]["atomic_results"] if r["values"].get("car_2") is not None]
    assert shown and all(r["leader"] is None for r in shown)
    eff = data["categories"]["efficiency"]
    assert eff["evidence_status"] == "not_directly_comparable"
    assert "ביחידות שונות לפי סוג ההנעה" in eff["layers"]["data"]
    petrol = run([AUDI_Q3, HYUNDAI_TUCSON])["categories"]["electric_and_charging"]
    assert petrol["evidence_status"] == "not_applicable"


def test_equipment_counts_only_declared_features():
    data = run([AUDI_Q3, HYUNDAI_TUCSON])
    eq = data["categories"]["equipment_and_convenience"]
    assert eq["influence_status"] == "no_declared_features"
    with_nice = run([AUDI_Q3, BMW_I4], personalized(nice_to_have_features=["reverse_camera"]))
    eq2 = with_nice["categories"]["equipment_and_convenience"]
    assert eq2["influence_status"] in ("balanced", "influenced", "insufficient_data")
    assert eq2["influence_status"] != "no_declared_features"


# --------------------------------------------------------------------------
# rendered UI (real compare_v2.js under node)
# --------------------------------------------------------------------------
def render(data, tmp_path):
    (tmp_path / "r.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    script = f"""
        const api = require({json.dumps(str(ROOT / 'static' / 'compare_v2.js'))});
        const r = JSON.parse(require('fs').readFileSync({json.dumps(str(tmp_path / 'r.json'))}, 'utf8'));
        const cards = {{}};
        for (const k of r.category_order) {{ if (r.categories[k]) cards[k] = api.buildCategoryCardV22Html(r, r.categories[k]); }}
        process.stdout.write(JSON.stringify({{hero: api.buildHeroV22Html(r), body: api.buildResultBodyV22Html(r), cards}}));
    """
    return json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_card_hierarchy_four_layers_and_details(tmp_path):
    data = run([AUDI_Q3, BMW_I4], personalized(priorities={"safety": 4}))
    out = render(data, tmp_path)
    for key, card in out["cards"].items():
        if data["categories"][key]["evidence_status"] == "not_applicable":
            continue
        assert "כל הנתונים והמקורות" in card, key
        order = [card.index(m) for m in ("<h4", "data-badge", 'data-layer="what"', 'data-layer="importance"',
                                         'data-layer="data"', 'data-layer="influence"', "<details")]
        assert order == sorted(order), key
    safety = out["cards"]["safety"]
    assert 'data-badge="influenced"' in safety and "השפיע לטובת" in safety
    assert "ודאות מודל בשיפוט הזה" in safety  # raw JEV confidence only inside details, labelled
    ev = out["cards"]["electric_and_charging"]
    assert 'data-badge="not_directly_comparable"' in ev and "לא בר-השוואה ישירה" in ev
    assert "קוט״ש" in ev  # BMW battery value shown descriptively
    assert "לא רלוונטי לאף אחד" not in out["body"]
    assert "ביטחון ההכרעה" not in out["body"] + out["hero"]
    assert "/100" not in out["body"] + out["hero"]


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_tie_and_insufficient_heroes(tmp_path):
    tie = run([AUDI_Q3, HYUNDAI_TUCSON, BMW_I4])
    assert tie["recommendation"]["outcome"] == "tie"
    assert "אין כרגע יתרון משמעותי" in render(tie, tmp_path)["hero"]
    thin = run([AUDI_Q3, BMW_I4], personalized(priorities={k: 0 for k in PRI} | {"environment": 4, "ev_convenience": 0}))
    assert thin["recommendation"]["outcome"] == "insufficient_evidence"
    hero = render(thin, tmp_path)["hero"]
    assert "אין מספיק מידע מאומת להכרעה מותאמת" in hero
    assert "הסיבות המרכזיות" not in hero


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_two_petrol_cars_list_ev_as_not_relevant_to_any(tmp_path):
    body = render(run([AUDI_Q3, HYUNDAI_TUCSON]), tmp_path)["body"]
    assert "לא רלוונטי לאף אחד מהרכבים שנבחרו: חשמל וטעינה" in body
    assert "עדיין לא נכלל בגרסה הזו: אמינות ארוכת טווח" in body
