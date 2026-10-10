# -*- coding: utf-8 -*-
"""Comparison V3 live fixes, from the first live comparison (2026-10-10: BMW 530E M SPORT 2020 plug-in, Audi A7
SPORTBACK 2020 plug-in, Alfa Romeo GIULIA MILANO 2020 petrol; MILO 22082 / 13323 / 17715):

* L1 one row set for the table and the decision: a row missing for any car is absent from every weight, score,
  JEV question and text (the safety score of the Alfa is null);
* L2 N-car reasons: an advantage is stated only for the row leader over ALL cars, ties name the cars at the top,
  no "X מול Y" text, and the summary guard rejects a sentence crediting a car with a row it does not lead;
* L3 the body-style / use fit is not weighted (it was a JEV judgement without a deterministic rule or a row);
* L4 the official CO2 / green index / pollution group rows are compared across propulsion families with one note;
* L5 TRIPY timeouts (catalog 15 s with one retry, facts 20 s) and the picker's loading / error / retry states.
"""

import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.services.comparison_v3 import tripy
from app.services.comparison_v3.explanations import summary_credit_violation
from app.services.comparison_v3.judgments import FIT_DIMENSION
from app.services.comparison_v3.metrics import comparable_rows
from app.services.comparison_v3.pipeline import collect_result, run_comparison_v3
from app.services.comparison_v3.tripy import TripyCatalogRepository, TripyClient, TripyFactsRepository, TripyUnavailable

from comparison_v2_fakes import FakeSummaryWriter, FakeTypeSafeSession
from comparison_v3_fakes import KEYS, LIVE_TRIO, OCTAVIA, FakeTripySession, build_v3_deps, eea, gov, snapshots_for, vkey

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "static" / "compare_v3.js"
node_required = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

GENERAL = {"mode": "general"}
PERSONALIZED = {"mode": "personalized", "main_use": "family", "regular_passengers": 4,
                "priorities": {"purchase_price": 2, "safety": 2, "performance": 2, "efficiency_environment": 2,
                               "practicality": 2, "ev_convenience": 2}}
BMW, AUDI, ALFA = "BMW 530E", "Audi A7 SPORTBACK", "Alfa Romeo GIULIA"
HIDDEN = ("safety_score", "safety_equipment_level", "ניקוד בטיחות", "רמת אבזור בטיחותי", "דירוג הבטיחות",
          "safety score", "safety-equipment level")


def _trio(profile, writer=None):
    session = FakeTypeSafeSession()
    deps = build_v3_deps(session=session, writer=writer)
    body = {"cars": [{"variant_identity_key": KEYS[k]} for k in ("live_bmw", "live_audi", "live_alfa")],
            "buyer_profile": profile}
    result = collect_result(run_comparison_v3(body, deps))
    assert result["type"] == "result", result
    return result["data"], session


def _user_texts(data):
    """Every deterministic text the user reads (hero reasons, section headers, summary, row notes)."""
    rec = data["recommendation"]
    texts = list(rec["reasons_he"]["for"]) + list(rec["reasons_he"]["against"]) + [data["summary"]]
    for section in data["table"]["sections"]:
        texts.append(section["influence_he"])
        texts += [r.get("note_he") or "" for r in section["rows"]]
    return [t for t in texts if t]


def _row(data, row_id):
    return next((r for s in data["table"]["sections"] for r in s["rows"] if r["row_id"] == row_id), None)


# ---------------------------------------------------------------------------
# L1: the hidden safety score is nowhere
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("profile", [PERSONALIZED, GENERAL])
def test_trio_safety_score_is_absent_from_the_table_and_from_every_weight_score_and_reason(profile):
    data, session = _trio(profile)
    for row_id in ("safety_score", "safety_equipment_level"):
        assert _row(data, row_id) is None
    whole = json.dumps(data, ensure_ascii=False)                  # table, reasons, summary AND the decision trace
    jev_body = json.dumps(session.post_calls, ensure_ascii=False)  # the JEV state and every question text
    for word in HIDDEN:
        assert word not in whole, word
        assert word not in jev_body, word
    trace = data["decision_trace"]
    assert {r["row_id"] for r in trace["rows"]} == {r["row_id"] for s in data["table"]["sections"] for r in s["rows"]}
    factors = trace["jev_state"]["pairwise_objective_evidence"]["factor_values"]
    assert set(factors["gov_safety_rating"]) == {"adas_systems_count"}
    for pair in trace["overall_composition"]["pairs"].values():
        for dim in pair["dimensions"].values():
            for signal in dim["signals"]:
                assert set(signal.get("metrics") or []) <= {r["row_id"] for r in trace["rows"]}
    # the safety reason names the row that exists
    assert any("יתרון במערכות סיוע לנהג" in t for t in data["recommendation"]["reasons_he"]["for"])


# ---------------------------------------------------------------------------
# L2: N-car reasons
# ---------------------------------------------------------------------------
def test_trio_reasons_credit_only_the_row_leader_and_show_ties():
    data, _ = _trio(PERSONALIZED)
    assert data["recommendation"]["outcome"] == "car_2"
    assert data["recommendation"]["reasons_he"] == {
        "for": [
            f"ביצועים: בחשיבות בינונית עבורך. ל־{AUDI} יתרון בהספק.",
            f"בטיחות: בחשיבות בינונית עבורך. ל־{AUDI} יתרון במערכות סיוע לנהג.",
            f"צריכה וסביבה: בחשיבות בינונית עבורך. פליטת CO₂ (WLTP): {BMW}; מדד ירוק: תיקו בין {BMW} ל־{AUDI}; "
            f"קבוצת זיהום: תיקו בין {BMW} ל־{AUDI}.",
        ],
        "against": [],
    }
    rows = {r["row_id"]: r for s in data["table"]["sections"] for r in s["rows"]}
    assert rows["green_index"]["leader"] == "tie" and rows["pollution_group"]["leader"] == "tie"
    env = next(s for s in data["table"]["sections"] if s["key"] == "efficiency_environment")
    assert f"קבוצת זיהום: תיקו בין {BMW} ל־{AUDI}" in env["influence_he"]
    texts = _user_texts(data)
    for text in texts:
        assert "מול" not in text, text                                # no pairwise sentence
        assert not (AUDI in text and "יתרון במדד ירוק" in text), text  # Audi never credited with the green index


@pytest.mark.parametrize("profile", [PERSONALIZED, GENERAL])
def test_trio_every_user_text_passes_the_leader_guard(profile):
    data, _ = _trio(profile)
    rows = comparable_rows(snapshots_for(*LIVE_TRIO))
    for text in _user_texts(data):
        assert summary_credit_violation(text, rows, data["cars"]) is None, text


def test_summary_guard_rejects_a_non_leader_credit():
    rows = comparable_rows(snapshots_for(*LIVE_TRIO))
    cars = {"car_1": {"display_name": BMW, "make": "BMW", "model": "530E"},
            "car_2": {"display_name": AUDI, "make": "Audi", "model": "A7 SPORTBACK"},
            "car_3": {"display_name": ALFA, "make": "Alfa Romeo", "model": "GIULIA"}}
    bad = [f"ל־{AUDI} יתרון במדד ירוק וקבוצת זיהום.",             # a tie with the BMW, never Audi's advantage
           f"ל־{AUDI} יתרון בדירוג הבטיחות של משרד התחבורה.",       # a hidden row
           f"ל־{BMW} ול־{AUDI} יתרון בקבוצת זיהום.",                # a tie is not an advantage
           "ל־Audi יתרון בפליטת CO₂ (WLTP).",                       # the BMW leads; the brand alone names the car
           f"{BMW} מול {AUDI}: התחום השפיע לטובת {AUDI}."]           # a pairwise sentence
    for text in bad:
        assert summary_credit_violation(text, rows, cars), text
    good = [f"ל־{AUDI} יתרון בהספק.", f"ל־{BMW} יתרון בפליטת CO₂ (WLTP).",
            f"לפי הנתונים הזמינים והעדיפויות שהגדרת, {AUDI} מתאים יותר.",
            f"מדד ירוק: תיקו בין {BMW} ל־{AUDI}."]
    for text in good:
        assert summary_credit_violation(text, rows, cars) is None, text


def test_summary_crediting_a_non_leader_falls_back_to_the_deterministic_text():
    def output(credit):
        return lambda payload: {"stated_outcome": payload["outcome"], "summary_he":
                                f"לפי הנתונים הזמינים והעדיפויות שהגדרת, {AUDI} מתאים יותר. {credit}"}

    data, _ = _trio(PERSONALIZED, writer=FakeSummaryWriter(output_fn=output(f"ל־{AUDI} יתרון במדד ירוק.")))
    assert data["summary_source"] == "deterministic_fallback"
    assert "מדד ירוק" not in data["summary"] or "תיקו" in data["summary"]
    data, _ = _trio(PERSONALIZED, writer=FakeSummaryWriter(output_fn=output(f"ל־{AUDI} יתרון בהספק.")))
    assert data["summary_source"] == "gemini"


def test_summary_payload_carries_the_row_leaders():
    writer = FakeSummaryWriter()
    _trio(PERSONALIZED, writer=writer)
    leaders = {x["row"]: x["leader"] for x in writer.payloads[0]["row_leaders"]}
    assert leaders["קבוצת זיהום"] == f"תיקו בין {BMW} ל־{AUDI}"
    assert leaders["פליטת CO₂ (WLTP)"] == BMW
    assert leaders["כריות אוויר"] == "תיקו בין כל הרכבים"


# ---------------------------------------------------------------------------
# two cars, one car leads every row: the reasons are the ones before the fix (captured from main)
# ---------------------------------------------------------------------------
WEAK = copy.deepcopy(OCTAVIA)
WEAK["variant_identity_key"] = vkey("weak-octavia")
WEAK["identity"].update({"model": "WEAKCAR", "manufacturer": "פיאט"})
WEAK["facts"].update({
    "horsepower": gov(110, "hp"), "safety_score": gov(3), "safety_equipment_level": gov(2), "airbags": gov(4),
    "green_index": gov(160), "pollution_group": gov(12), "co2_wltp": gov(170, "g/km", standard="WLTP"),
    "fuel_consumption_combined_l_100km": eea(7.5, "L/100km", standard="WLTP"), "wheelbase_mm": eea(2500, "mm"),
    "towing_braked_kg": gov(1000, "kg"), "towing_unbraked_kg": gov(500, "kg"),
    "curb_weight_kg": eea(1450, "kg", definition="eu_running_order"),
    "adas.reverse_camera": gov(False), "adas.adaptive_cruise_control": gov(False)})
OCT = "Skoda OCTAVIA"
BEFORE_GENERAL = [
    f"בהשוואה כללית למחיר משקל שווה לשאר התחומים. ל־{OCT} יתרון במחיר, ומשמעות הפער לשימוש שהגדרת הוערכה כמורגשת.",
    f"בהשוואה כללית לביצועים משקל שווה לשאר התחומים. ל־{OCT} יתרון בהספק, ומשמעות הפער לשימוש שהגדרת הוערכה כמורגשת.",
    f"בהשוואה כללית למידות ומרחב משקל שווה לשאר התחומים. ל־{OCT} יתרון בבסיס גלגלים, ומשמעות הפער לשימוש שהגדרת "
    "הוערכה כמורגשת.",
    f"בהשוואה כללית לבטיחות משקל שווה לשאר התחומים. ל־{OCT} יתרון בדירוג הבטיחות של משרד התחבורה, ומשמעות הפער "
    "לשימוש שהגדרת הוערכה כגדולה.",
]
BEFORE_PERSONALIZED = [
    f"מחיר: חשוב לך. ל־{OCT} יתרון במחיר, ומשמעות הפער לשימוש שהגדרת הוערכה כמורגשת.",
    f"בטיחות: קריטי עבורך. ל־{OCT} יתרון בדירוג הבטיחות של משרד התחבורה, ומשמעות הפער לשימוש שהגדרת הוערכה כגדולה.",
    f"בטיחות: קריטי עבורך. ל־{OCT} יתרון בכריות אוויר, ומשמעות הפער לשימוש שהגדרת הוערכה כמורגשת.",
    f"מידות ומרחב: בחשיבות בינונית עבורך. ל־{OCT} יתרון בבסיס גלגלים, ומשמעות הפער לשימוש שהגדרת הוערכה כמורגשת.",
]
BEFORE_INFLUENCE = {
    "price": f"התחום השפיע לטובת {OCT} בזכות מחיר; משמעות הפער לשימוש שלך: מורגשת.",
    "safety": f"התחום השפיע לטובת {OCT} בזכות דירוג הבטיחות של משרד התחבורה וכריות אוויר; משמעות הפער לשימוש שלך: גדולה.",
    "performance": f"התחום השפיע לטובת {OCT} בזכות הספק; משמעות הפער לשימוש שלך: מורגשת.",
    "efficiency_environment": f"התחום השפיע לטובת {OCT} בזכות צריכה ופליטת CO₂ ומדד ירוק וקבוצת זיהום; משמעות הפער "
                              "לשימוש שלך: מורגשת.",
    "practicality": f"התחום השפיע לטובת {OCT} בזכות בסיס גלגלים; משמעות הפער לשימוש שלך: מורגשת.",
}


@pytest.mark.parametrize("profile, expected", [
    (GENERAL, BEFORE_GENERAL),
    ({"mode": "personalized", "main_use": "family", "parking_constraint": "tight",
      "priorities": {"purchase_price": 3, "safety": 4, "performance": 1, "efficiency_environment": 2, "practicality": 2}},
     BEFORE_PERSONALIZED),
])
def test_two_cars_one_leads_every_row_reasons_unchanged(profile, expected):
    body = {"cars": [{"variant_identity_key": OCTAVIA["variant_identity_key"], "asking_price_ils": 100000},
                     {"variant_identity_key": WEAK["variant_identity_key"], "asking_price_ils": 130000}],
            "buyer_profile": profile}
    data = collect_result(run_comparison_v3(body, build_v3_deps(records=[OCTAVIA, WEAK])))["data"]
    scored = [r for s in data["table"]["sections"] for r in s["rows"] if not r["display_only"]]
    assert scored and all(r["leader"] == "car_1" for r in scored)
    assert data["recommendation"]["reasons_he"] == {"for": expected, "against": []}
    influence = {s["key"]: s["influence_he"] for s in data["table"]["sections"]}
    for key, text in BEFORE_INFLUENCE.items():
        assert influence[key] == text


# ---------------------------------------------------------------------------
# L3: no body-fit weight
# ---------------------------------------------------------------------------
def test_body_use_fit_is_not_weighted_or_shown():
    assert set(FIT_DIMENSION) == {"parking_fit", "charging_routine_fit"}
    data, session = _trio(PERSONALIZED)              # sedan / hatchback / sedan with a main use: asked before the fix
    questions = session.post_calls[-1]["json"]["questions"]
    assert not [q for q in questions if "body_use_fit" in q]
    whole = json.dumps(data, ensure_ascii=False)
    assert "body_use_fit" not in whole and "התאמת המרכב לשימוש" not in whole
    practicality = next(s for s in data["table"]["sections"] if s["key"] == "practicality")
    assert practicality["influence_status"] == "display_only"


# ---------------------------------------------------------------------------
# L4: the CO2 row of a plug-in / petrol trio
# ---------------------------------------------------------------------------
def test_trio_shows_co2_with_the_same_note_as_the_green_index_and_pollution_group():
    data, _ = _trio(PERSONALIZED)
    co2 = _row(data, "co2_wltp")
    assert [co2["cells"][s]["text"] for s in ("car_1", "car_2", "car_3")] == ["34 גר׳ לק״מ", "40 גר׳ לק״מ", "162 גר׳ לק״מ"]
    assert co2["leader"] == "car_1" and co2["standard"] == "WLTP"
    notes = {_row(data, rid)["note_he"] for rid in ("co2_wltp", "green_index", "pollution_group")}
    assert len(notes) == 1 and "פלאג-אין" in next(iter(notes))
    for rid in ("fuel_consumption_l_100km", "energy_consumption_kwh_100km"):
        assert _row(data, rid) is None                   # measured consumption stays within one propulsion family
    # the deterministic explanation carries the note too
    assert next(iter(notes)) in co2["explanation_he"] or co2["explanation_source"] == "gemini"
    # two plug-ins: no note
    rows = {r["row_id"]: r for r in comparable_rows(snapshots_for(LIVE_TRIO[0], LIVE_TRIO[1]))}
    assert "co2_wltp" in rows and "note_he" not in rows["co2_wltp"] and "note_he" not in rows["green_index"]


@node_required
def test_the_row_note_is_rendered_under_the_label(tmp_path):
    data, _ = _trio(GENERAL)
    (tmp_path / "r.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    html = subprocess.run(["node", "-e", f"""
        const api = require({json.dumps(str(JS))});
        const r = JSON.parse(require('fs').readFileSync({json.dumps(str(tmp_path / 'r.json'))}, 'utf8'));
        process.stdout.write(api.buildTableHtml(r));"""], capture_output=True, text=True, check=True).stdout
    co2 = html.split('data-row="co2_wltp"', 1)[1].split('class="v3-cell', 1)[0]
    assert "data-v3-row-note" in co2 and "פלאג-אין" in co2
    assert html.count("data-v3-row-note") == 3


def test_the_rules_version_is_part_of_the_request_hash(monkeypatch):
    """A comparison cached under the old rules is never served after the fix (the owner reruns the same trio)."""
    from app.services.comparison_v3 import pipeline
    from app.services.comparison_v3.contracts import DECISION_RULES_VERSION

    assert DECISION_RULES_VERSION == "v3-rules/2"
    cars = [(KEYS["live_bmw"], None), (KEYS["live_audi"], None)]
    before = pipeline.compute_request_hash(cars, GENERAL, {}, {})
    monkeypatch.setattr(pipeline, "DECISION_RULES_VERSION", "v3-rules/1")
    assert pipeline.compute_request_hash(cars, GENERAL, {}, {}) != before


# ---------------------------------------------------------------------------
# L5: TRIPY timeouts and the catalog retry
# ---------------------------------------------------------------------------
def test_tripy_timeout_configuration():
    assert tripy.CATALOG_TIMEOUT_SEC == 15
    assert tripy.FACTS_TIMEOUT_SEC == 20
    assert tripy.CATALOG_RETRY_DELAY_SEC == 1
    assert tripy.RETRY_STATUSES == (502, 503, 504)


def _client(session, sleeps):
    return TripyClient(base_url="https://tripy.test", token="t", session=session, sleep=sleeps.append)


class _FlakyCatalog(FakeTripySession):
    """The first GET fails with ``first`` (an exception), the next ones answer."""

    def __init__(self, first):
        super().__init__()
        self.first = first

    def get(self, url, params=None, headers=None, timeout=None):
        if self.first is not None:
            self.calls.append({"method": "GET", "url": url, "timeout": timeout})
            exc, self.first = self.first, None
            raise exc
        return super().get(url, params=params, headers=headers, timeout=timeout)


def test_catalog_timeout_and_one_retry_after_one_second():
    for statuses in ([502], [503], [504]):
        sleeps = []
        session = FakeTripySession(statuses=statuses)
        items = TripyCatalogRepository(_client(session, sleeps)).manufacturers()
        assert items and len(session.calls) == 2 and sleeps == [1.0]
        assert all(c["timeout"] == 15 for c in session.calls)
    sleeps = []
    session = _FlakyCatalog(ConnectionError("reset"))
    assert TripyCatalogRepository(_client(session, sleeps)).manufacturers()
    assert len(session.calls) == 2 and sleeps == [1.0]
    sleeps = []
    session = _FlakyCatalog(TimeoutError("read timeout"))
    assert TripyCatalogRepository(_client(session, sleeps)).manufacturers()
    assert len(session.calls) == 2
    # one retry only; never on another status
    for statuses, calls in (([503, 503], 2), ([500], 1), ([401], 1), ([429], 1)):
        sleeps = []
        session = FakeTripySession(statuses=statuses)
        with pytest.raises(TripyUnavailable):
            TripyCatalogRepository(_client(session, sleeps)).manufacturers()
        assert len(session.calls) == calls, statuses


def test_facts_timeout_and_no_retry():
    sleeps = []
    session = FakeTripySession(statuses=[503])
    with pytest.raises(TripyUnavailable):
        TripyFactsRepository(_client(session, sleeps)).get_records([KEYS["octavia"]])
    assert len(session.calls) == 1 and sleeps == []
    session = FakeTripySession(raise_exc=ConnectionError("reset"))
    with pytest.raises(TripyUnavailable):
        TripyFactsRepository(_client(session, sleeps)).get_records([KEYS["octavia"]])
    assert len(session.calls) == 1
    session = FakeTripySession()
    TripyFactsRepository(_client(session, sleeps)).get_records([KEYS["octavia"]])
    assert session.calls[0]["timeout"] == 20


# ---------------------------------------------------------------------------
# L5: the picker states (loading, error + retry, empty list explained)
# ---------------------------------------------------------------------------
_DOM = """
class ClassList { constructor() { this.s = new Set(['hidden']); }
  toggle(c, on) { if (on) this.s.add(c); else this.s.delete(c); } add(c) { this.s.add(c); } contains(c) { return this.s.has(c); } }
class El { constructor(id) { this.id = id; this.innerHTML = ''; this.disabled = false; this.children = []; this._text = '';
    this.classList = new ClassList(); this.attrs = {}; this.listeners = {}; }
  set textContent(v) { this._text = v; this.children = []; } get textContent() { return this._text; }
  appendChild(n) { this.children.push(n); } setAttribute(k, v) { this.attrs[k] = v; }
  addEventListener(t, fn) { this.listeners[t] = fn; } click() { return this.listeners.click(); } }
const els = {};
global.document = {
  getElementById: (id) => (/^v3_(make|model|picker_error)_/.test(id) ? (els[id] = els[id] || new El(id)) : null),
  createElement: () => new El('button'), createTextNode: (t) => ({ text: t }),
  querySelector: () => null, querySelectorAll: () => [] };
const queue = [];
global.fetch = () => new Promise((resolve, reject) => queue.push({ resolve, reject }));
const tick = () => new Promise((r) => setTimeout(r, 0));
const ok = (data) => ({ ok: true, json: async () => ({ ok: true, data }) });
const fail = () => ({ ok: false, json: async () => ({ ok: false, error: { message: 'x' } }) });
"""


@node_required
def test_picker_loading_error_retry_and_empty_list():
    script = _DOM + f"""
    const api = require({json.dumps(str(JS))});
    (async () => {{
      const out = {{}};
      let done = api.loadManufacturers();
      out.loading = els.v3_make_1.innerHTML;
      out.loadingDisabled = els.v3_make_1.disabled;
      queue.shift().resolve(fail()); await done; await tick();
      const box = els.v3_picker_error_1;
      out.error = box.textContent; out.errorShown = !box.classList.contains('hidden');
      const button = box.children.find((c) => c.textContent === 'נסו שוב');
      out.retryButton = !!button && button.attrs['data-v3-retry'] === '';
      button.click(); await tick();
      out.retryLoading = els.v3_make_1.innerHTML;
      queue.shift().resolve(ok({{ manufacturers: [{{ manufacturer: 'סקודה', display: 'Skoda' }}] }})); await tick(); await tick();
      out.filled = els.v3_make_1.innerHTML; out.enabled = !els.v3_make_1.disabled;
      out.errorCleared = els.v3_picker_error_1.classList.contains('hidden');
      done = api.loadList(1, document.getElementById('v3_model_1'), 'models', {{ manufacturer: 'סקודה' }}, 'model', (m) => m.model, 'בחרו דגם...');
      out.modelsLoading = document.getElementById('v3_model_1').innerHTML;
      queue.shift().resolve(ok({{ models: [] }})); await done;
      out.empty = els.v3_picker_error_1.textContent;
      out.emptyRetry = els.v3_picker_error_1.children.some((c) => c.textContent === 'נסו שוב');
      out.emptyShown = !els.v3_picker_error_1.classList.contains('hidden');
      done = api.loadList(1, document.getElementById('v3_model_1'), 'models', {{ manufacturer: 'סקודה' }}, 'model', (m) => m.model, 'בחרו דגם...');
      queue.shift().reject(new TypeError('network')); await done;
      out.modelsError = els.v3_picker_error_1.textContent;
      process.stdout.write(JSON.stringify(out));
    }})();
    """
    out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
    assert "טוען…" in out["loading"] and out["loadingDisabled"] is True
    assert out["error"] == "לא הצלחנו לטעון את רשימת היצרנים. נסו שוב." and out["errorShown"] and out["retryButton"]
    assert "טוען…" in out["retryLoading"]
    assert "Skoda (סקודה)" in out["filled"] and out["enabled"] and out["errorCleared"]
    assert "טוען…" in out["modelsLoading"]
    assert out["empty"] == "לא נמצאו דגמים ליצרן הזה בקטלוג." and out["emptyRetry"] and out["emptyShown"]
    assert out["modelsError"] == "לא הצלחנו לטעון את רשימת הדגמים. נסו שוב."
