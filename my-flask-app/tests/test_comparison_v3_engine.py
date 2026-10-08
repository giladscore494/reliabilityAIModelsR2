# -*- coding: utf-8 -*-
"""Comparison V3 (``comparison-v3/1``) offline: the row rule R, categories, price / budget, safety as one signal,
hp per tonne, buyer-profile/3 migration, row explanations, TRIPY access, attribution, the no-import rule."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.services.comparison.model_config import DEFAULT_COMPARISON_V2_MODEL_ID
from app.services.comparison_v3 import contracts
from app.services.comparison_v3.buyer_profile import (
    BUYER_PROFILE_VERSION,
    BuyerProfileError,
    migrate_priorities,
    normalize_buyer_profile,
)
from app.services.comparison_v3.engine import build_pairwise_evidence
from app.services.comparison_v3.metrics import (
    CATEGORIES,
    METRICS,
    attribution,
    available_dimensions,
    comparable_rows,
)
from app.services.comparison_v3.pipeline import collect_result, compute_request_hash, run_comparison_v3
from app.services.comparison_v3.row_explanations import (
    GeminiRowExplanationWriter,
    fallback_text,
    produce_row_explanations,
    row_payload,
    validate_row_text,
)
from app.services.comparison_v3.tripy import TripyClient, TripyFactsRepository, TripyUnavailable

from comparison_v3_fakes import (
    BMW_530E,
    CIVIC_NO_WHEELBASE,
    ESCAPE_US,
    FOCUS_NEDC,
    GOLF,
    KEYS,
    OCTAVIA,
    OUTLANDER_PHEV,
    FakeExplanationWriter,
    FakeTripySession,
    build_v3_deps,
    snapshots_for,
)

ROOT = Path(__file__).resolve().parents[1]
GENERAL = {"mode": "general"}


def _rows(*recs):
    return comparable_rows(snapshots_for(*recs))


def _ids(rows):
    return [r["row_id"] for r in rows]


def _run(cars, profile=None, deps=None):
    body = {"cars": cars, "buyer_profile": profile or GENERAL}
    return collect_result(run_comparison_v3(body, deps or build_v3_deps()))


def _cars(*names, prices=None):
    prices = prices or [None] * len(names)
    out = []
    for name, price in zip(names, prices):
        car = {"variant_identity_key": KEYS[name]}
        if price is not None:
            car["asking_price_ils"] = price
        out.append(car)
    return out


# ---------------------------------------------------------------------------
# versions / categories
# ---------------------------------------------------------------------------
def test_versions_and_category_order():
    assert contracts.ENGINE_VERSION == "comparison-v3/1"
    assert contracts.SNAPSHOT_CONTRACT_VERSION == "canonical-vehicle-snapshot/2"
    assert BUYER_PROFILE_VERSION == "buyer-profile/3"
    assert [c["key"] for c in CATEGORIES] == ["variant_details", "price", "safety", "performance",
                                              "efficiency_environment", "practicality", "history", "ev", "towing"]
    assert [c["dimension"] for c in CATEGORIES] == [None, "purchase_price", "safety", "performance",
                                                    "efficiency_environment", "practicality", None, "ev_convenience",
                                                    "towing"]
    # wheelbase is the only scored practicality metric; removed dimensions / metrics are gone
    assert [m.key for m in METRICS if m.category == "practicality" and m.scored] == ["wheelbase_mm"]
    keys = {m.key for m in METRICS}
    for removed in ("warranty_years", "nox_mg_km", "ground_clearance_mm", "cargo_volume_l", "abs", "esc",
                    "fuel_consumption_combined_l_100km"):
        assert removed not in keys
    dims = {m.category for m in METRICS}
    assert "warranty" not in dims and "equipment" not in dims


# ---------------------------------------------------------------------------
# row rule R
# ---------------------------------------------------------------------------
def test_row_present_for_two_of_three_cars_is_absent_everywhere():
    two = _rows(OCTAVIA, GOLF)
    three = _rows(OCTAVIA, GOLF, CIVIC_NO_WHEELBASE)
    assert "wheelbase_mm" in _ids(two)
    assert "wheelbase_mm" not in _ids(three)          # the Civic has no wheelbase -> no row for anyone
    # practicality had only wheelbase as a scored row: its slider is hidden
    assert "practicality" in available_dimensions(two)
    assert "practicality" not in available_dimensions(three)
    # the evidence the engine / JEV see is built from the same rows
    pairwise = build_pairwise_evidence(three, ["car_1", "car_2", "car_3"])
    for pair in pairwise.values():
        assert "wheelbase" not in pair["groups"]


def test_wltp_is_never_compared_with_nedc():
    rows = _rows(OCTAVIA, FOCUS_NEDC)
    ids = _ids(rows)
    assert "fuel_consumption_l_100km" not in ids      # 6.1 WLTP vs 5.1 NEDC
    assert "co2_wltp" not in ids and "co2_nedc_g_km" not in ids
    same = _rows(OCTAVIA, GOLF)
    row = next(r for r in same if r["row_id"] == "fuel_consumption_l_100km")
    assert row["standard"] == "WLTP" and row["leader"] == "car_2"


def test_phev_consumption_is_never_compared_with_petrol():
    ids = _ids(_rows(OCTAVIA, BMW_530E))
    for rid in ("fuel_consumption_l_100km", "co2_wltp", "energy_consumption_kwh_100km", "electric_range_km"):
        assert rid not in ids
    # two PHEVs: consumption and the conditional EV category exist
    phev = _rows(BMW_530E, OUTLANDER_PHEV)
    assert {"fuel_consumption_l_100km", "electric_range_km"} <= set(_ids(phev))
    assert "ev_convenience" in available_dimensions(phev)


def test_hp_per_tonne_only_with_the_same_mass_definition():
    eu = _rows(OCTAVIA, GOLF)
    row = next(r for r in eu if r["row_id"] == "hp_per_tonne")
    assert row["standard"] == "eu_running_order"
    assert row["cells"]["car_1"]["text"] == "107.9 כ״ס לטון"
    mass = next(r for r in eu if r["row_id"] == "curb_weight_kg")
    assert mass["label_he"] == "משקל במצב נסיעה (כולל נהג)"
    mixed = _ids(_rows(OCTAVIA, ESCAPE_US))           # eu_running_order (EEA) vs na_curb (Transport Canada)
    assert "hp_per_tonne" not in mixed and "curb_weight_kg" not in mixed
    assert "horsepower" in mixed


def test_safety_is_one_signal_and_abs_esc_never_appear():
    rows = _rows(OCTAVIA, GOLF)
    safety = [r for r in rows if r["category"] == "safety"]
    groups = {r["row_id"]: r["group"] for r in safety}
    assert groups == {"safety_score": "gov_safety_rating", "safety_equipment_level": "gov_safety_rating",
                      "adas_systems_count": "gov_safety_rating", "airbags": "passive_safety"}
    pair = build_pairwise_evidence(rows, ["car_1", "car_2"])["car_1__car_2"]
    assert sorted(g for g, v in pair["groups"].items() if v["dimension"] == "safety") == ["gov_safety_rating",
                                                                                          "passive_safety"]
    text = json.dumps(rows, ensure_ascii=False).lower()
    for word in ("abs", "esc", "בלימה נגד נעילה", "בקרת יציבות"):
        assert word not in text


def test_absent_rows_never_leave_placeholders():
    for recs in ((OCTAVIA, GOLF, CIVIC_NO_WHEELBASE), (OCTAVIA, ESCAPE_US), (OCTAVIA, BMW_530E)):
        for row in _rows(*recs):
            assert len(row["cells"]) == len(recs)
            for cell in row["cells"].values():
                assert cell["text"] and cell["text"].strip()
                for bad in ("אין מידע", "—", "None", "null", "nan", "undefined"):
                    assert bad not in cell["text"], (row["row_id"], cell["text"])


def test_history_rows_original_price_recalls_and_road_survival():
    rows = {r["row_id"]: r for r in _rows((OCTAVIA, 120000), (GOLF, 115000))}
    price = rows["original_new_price_ils"]
    assert price["display_only"] and price["history"]
    assert price["cells"]["car_1"]["text"] == "₪160,000"
    assert price["cells"]["car_2"]["text"] == "₪150,000–₪175,000 (4 מחירים לגרסאות הדגם)"
    # depreciation needs a single original price for every car (the Golf has a range) -> absent
    assert "depreciation_from_new" not in rows
    single = {r["row_id"]: r for r in _rows((OCTAVIA, 120000), (OCTAVIA | {"variant_identity_key": "x" * 64}, 140000))}
    assert single["depreciation_from_new"]["cells"]["car_1"]["text"] == "25%"
    assert single["depreciation_from_new"]["display_only"]
    recalls = rows["recalls"]
    assert [recalls["cells"][s]["text"] for s in ("car_1", "car_2")] == ["1", "2"]
    assert recalls["cells"]["car_2"]["details"][1] == {"year": 2023, "system": "כריות אוויר", "repair": "החלפת מודול"}
    survival = rows["road_survival"]
    assert survival["survival_age"] == 3 and survival["label_he"] == "ירידה מהכביש עד גיל 3"
    assert survival["cells"]["car_1"]["text"] == "1% מהרכבים"
    for rid in ("original_new_price_ils", "recalls", "road_survival"):
        assert rows[rid]["leader"] is None
    # never weighted: no pairwise group, no JEV question, no dimension
    pair = build_pairwise_evidence(list(rows.values()), ["car_1", "car_2"])["car_1__car_2"]
    assert not {"recalls", "road_survival", "original_new_price_ils"} & set(pair["groups"])
    fallback = fallback_text(survival)
    assert "אינה ידועה" in fallback and "אמין" not in fallback


# ---------------------------------------------------------------------------
# asking price / budget
# ---------------------------------------------------------------------------
def test_asking_price_row_only_with_every_price():
    assert "asking_price_ils" not in _ids(_rows(OCTAVIA, GOLF))
    assert "asking_price_ils" not in _ids(_rows((OCTAVIA, 120000), GOLF))
    rows = _rows((OCTAVIA, 120000), (GOLF, 117000))
    row = next(r for r in rows if r["row_id"] == "asking_price_ils")
    assert row["leader"] == "car_2"                    # 3,000 of 120,000 is more than the 2% tie margin
    rows = _rows((OCTAVIA, 120000), (GOLF, 119000))
    row = next(r for r in rows if r["row_id"] == "asking_price_ils")
    assert row["leader"] == "tie"                      # within 2%
    row = next(r for r in _rows((OCTAVIA, 120000), (GOLF, 100000)) if r["row_id"] == "asking_price_ils")
    assert row["leader"] == "car_2" and row["cells"]["car_1"]["source"] == "user_supplied"


def test_budget_with_a_missing_price_is_a_400():
    result = _run(_cars("octavia", "golf", prices=[120000, None]),
                  {"mode": "personalized", "main_use": "family", "budget_max_ils": 130000})
    assert result["type"] == "error" and result["status"] == 400
    assert result["code"] == "invalid_buyer_profile"
    assert result["message"] == "כדי לבדוק תקציב יש להזין מחיר לכל רכב"
    assert result["field"] == "budget_max_ils"


def test_budget_is_evaluated_with_every_price():
    result = _run(_cars("octavia", "golf", prices=[140000, 118000]),
                  {"mode": "personalized", "main_use": "family", "budget_max_ils": 130000})
    assert result["type"] == "result"
    data = result["data"]
    budget = next(c for c in data["hard_constraints"]["constraints"] if c["key"] == "budget")
    assert {s: r["status"] for s, r in budget["per_car"].items()} == {"car_1": "fail", "car_2": "pass"}
    assert data["recommendation"]["outcome"] == "car_2"
    price_section = next(s for s in data["table"]["sections"] if s["key"] == "price")
    assert price_section["rows"][0]["row_id"] == "asking_price_ils"
    assert data["cars"]["car_1"]["asking_price_text"] == "₪140,000"


@pytest.mark.parametrize("price", [999, 3000001, 1500.5, "abc", True])
def test_invalid_asking_price_is_rejected(price):
    result = _run([{"variant_identity_key": KEYS["octavia"], "asking_price_ils": price},
                   {"variant_identity_key": KEYS["golf"]}])
    assert result["status"] == 400 and result["code"] == "invalid_asking_price"
    assert result["field"] == "cars.0.asking_price_ils"


def test_asking_prices_are_part_of_the_request_hash():
    versions = {"contract": "vehicle-facts/1", "admission": "a", "snapshots_sha": "s", "matcher": "m",
                "zero_semantics": "z"}
    base = compute_request_hash([("k1", 100000), ("k2", None)], {"mode": "general"}, versions, {})
    assert base != compute_request_hash([("k1", 100001), ("k2", None)], {"mode": "general"}, versions, {})
    assert base != compute_request_hash([("k1", 100000), ("k2", None)], {"mode": "general"},
                                        {**versions, "snapshots_sha": "s2"}, {})


# ---------------------------------------------------------------------------
# buyer-profile/3
# ---------------------------------------------------------------------------
def test_v2_profile_migration_merges_efficiency_and_environment():
    migrated = migrate_priorities({"efficiency": 4, "environment": 1, "safety": 3, "warranty": 4, "equipment": 2,
                                   "performance": 0, "practicality": 2, "purchase_price": 1}, "buyer-profile/2")
    assert migrated["efficiency_environment"] == 4
    assert "warranty" not in migrated and "equipment" not in migrated
    profile = normalize_buyer_profile({"schema": "buyer-profile/2", "mode": "personalized", "main_use": "city",
                                       "priorities": {"efficiency": 1, "environment": 3}}, has_plugin_vehicle=False)
    assert profile["schema"] == "buyer-profile/3"
    assert profile["priorities"]["efficiency_environment"] == 3
    assert profile["priorities"]["safety"] == 2            # a missing slider defaults to 2
    assert "ev_convenience" not in profile["priorities"]
    with pytest.raises(BuyerProfileError):
        normalize_buyer_profile({"mode": "personalized", "main_use": "city", "priorities": {"warranty": 2}},
                                has_plugin_vehicle=False)


# ---------------------------------------------------------------------------
# row explanations (E)
# ---------------------------------------------------------------------------
def _payloads():
    rows = _rows((OCTAVIA, 120000), (GOLF, 100000))
    names = {"car_1": "Skoda OCTAVIA", "car_2": "Volkswagen GOLF"}
    return rows, names


def test_row_explanation_with_a_foreign_number_falls_back():
    rows, names = _payloads()

    def output(payloads):
        out = {p["row_id"]: f"השורה מציגה את {p['label_he']}. ההבדל משמעותי לשימוש שלך." for p in payloads}
        out["horsepower"] = "להספק של 999 כוחות סוס יש משמעות. Skoda OCTAVIA עדיף בשורה הזו."
        out["asking_price_ils"] = "המחיר נמדד לפי מה שהזנת. Skoda OCTAVIA עדיף כאן במחיר."   # not the leader
        out["safety_score"] = "הניקוד הוא 6 ו-5. הוא מבטא 50% יותר בטיחות."                 # percentage
        return out

    writer = FakeExplanationWriter(output_fn=output)
    result = produce_row_explanations(writer, rows, names, ["משפחתי"])
    assert writer.calls == 1 and result["calls"] == 1
    for rid in ("horsepower", "asking_price_ils", "safety_score"):
        assert result["sources"][rid] == "fallback" and rid in result["rejected"]
        assert result["texts"][rid] == fallback_text(next(r for r in rows if r["row_id"] == rid))
    assert result["sources"]["wheelbase_mm"] == "gemini"


def test_row_explanation_validator_rules():
    rows, names = _payloads()
    by_id = {r["row_id"]: r for r in rows}
    labels = [r["label_he"] for r in rows]
    hp = row_payload(by_id["horsepower"], names)
    ok = "ההספק נמדד בכוחות סוס לפי רישום משרד התחבורה. ל-Skoda OCTAVIA יש 150 כ״ס ול-Volkswagen GOLF יש 130 כ״ס."
    assert validate_row_text(ok, hp, [lbl for lbl in labels if lbl != hp["label_he"]], names) == ok
    assert validate_row_text("משפט אחד בלבד.", hp, [], names) is None                     # 2-4 sentences
    assert validate_row_text("א. " * 300, hp, [], names) is None                              # > 450 chars
    other = "ההספק נמדד בכוחות סוס. הוא קשור גם לצריכת דלק משולבת של הרכב."
    assert validate_row_text(other, hp, ["צריכת דלק משולבת"], names) is None                # another row's data
    survival = row_payload(by_id["road_survival"], names)
    assert validate_row_text("השורה מציגה ירידה מהכביש. זה מדד אמינות.", survival, [], names) is None
    assert validate_row_text("השורה מציגה ירידה מהכביש עד גיל 3. הנתון אינו מבחין בין סיבות.", survival, [], names) is None
    good = "השורה מציגה ירידה מהכביש עד גיל 3. סיבת הירידה מהכביש אינה ידועה."
    assert validate_row_text(good, survival, [], names) == good
    assert "http" not in json.dumps(hp) and "row_ids" not in json.dumps(hp)


def test_explanation_writer_model_and_config():
    writer = GeminiRowExplanationWriter(client=None)
    assert writer.model_id == DEFAULT_COMPARISON_V2_MODEL_ID == "gemini-3.8-flash"
    pytest.importorskip("google.genai")
    from google.genai import types as genai_types

    cfg = writer._config(["horsepower"])
    assert cfg.temperature == 0.0
    assert cfg.thinking_config.thinking_level == genai_types.ThinkingLevel.LOW
    assert not cfg.tools
    assert writer.write([], [])["error_code"] == "CLIENT_NOT_INITIALIZED" and writer.calls == 0


def test_explanations_stage_runs_before_the_summary_and_fills_every_row():
    deps = build_v3_deps()
    events = list(run_comparison_v3({"cars": _cars("octavia", "golf", prices=[120000, 115000]),
                                     "buyer_profile": GENERAL}, deps))
    stages = [e["stage"] for e in events if e["type"] == "progress"]
    assert stages == ["resolving_vehicles", "loading_facts", "comparing_facts", "evaluating_decision",
                      "writing_explanations", "writing_summary", "complete"]
    data = events[-1]["data"]
    assert deps.explanation_writer.calls == 1 and data["provider_meta"]["explanation_calls"] == 1
    for section in data["table"]["sections"]:
        for row in section["rows"]:
            assert row["explanation_he"] and row["explanation_source"] in ("gemini", "fallback")


def test_explanation_failure_uses_fallbacks_and_is_not_cached():
    deps = build_v3_deps(explanation_writer=FakeExplanationWriter(error="EXPLANATIONS_ERROR:Timeout"))
    result = _run(_cars("octavia", "golf"), deps=deps)
    data = result["data"]
    assert data["explanations"]["reason"] == "EXPLANATIONS_ERROR:Timeout"
    assert all(r["explanation_source"] == "fallback" for s in data["table"]["sections"] for r in s["rows"])


# ---------------------------------------------------------------------------
# TRIPY access
# ---------------------------------------------------------------------------
def _repo(session):
    return TripyFactsRepository(TripyClient(base_url="https://tripy.test", token="facts-secret", session=session))


def test_tripy_request_shape_bearer_and_timeout():
    session = FakeTripySession()
    data = _repo(session).get_records([KEYS["octavia"], KEYS["golf"]])
    call = session.calls[0]
    assert call["url"] == "https://tripy.test/api/facts/v1/vehicles"
    assert call["json"] == {"variant_identity_keys": [KEYS["octavia"], KEYS["golf"]]}
    assert call["headers"]["Authorization"] == "Bearer facts-secret"
    assert call["timeout"] == 5
    assert data["versions"]["contract"] == "vehicle-facts/1"


def test_tripy_retries_once_on_5xx_only():
    session = FakeTripySession(statuses=[502])
    _repo(session).get_records([KEYS["octavia"]])
    assert len(session.calls) == 2
    session = FakeTripySession(statuses=[503, 503])
    with pytest.raises(TripyUnavailable):
        _repo(session).get_records([KEYS["octavia"]])
    assert len(session.calls) == 2
    session = FakeTripySession(statuses=[401])
    with pytest.raises(TripyUnavailable):
        _repo(session).get_records([KEYS["octavia"]])
    assert len(session.calls) == 1
    with pytest.raises(TripyUnavailable):
        _repo(FakeTripySession(raise_exc=TimeoutError("read timeout"))).get_records([KEYS["octavia"]])
    with pytest.raises(TripyUnavailable):
        TripyFactsRepository(TripyClient(base_url="", token="", session=FakeTripySession())).get_records(["k"])


def test_tripy_down_is_facts_unavailable_never_demo_data():
    session = FakeTripySession(statuses=[500, 500])
    deps = build_v3_deps(facts=_repo(session))
    result = _run(_cars("octavia", "golf"), deps=deps)
    assert result["type"] == "error" and result["status"] == 503
    assert result["code"] == "facts_unavailable"
    assert result["message"] == "ההשוואה לא זמינה כרגע, נסו שוב בעוד כמה דקות"
    assert deps.explanation_writer.calls == 0 and deps.summary_writer.calls == 0


def test_tripy_contract_mismatch_is_unavailable():
    class Wrong(FakeTripySession):
        def post(self, url, json=None, headers=None, timeout=None):  # noqa: A002
            resp = super().post(url, json=json, headers=headers, timeout=timeout)
            resp._body["contract"] = "vehicle-facts/2"
            return resp

    with pytest.raises(TripyUnavailable):
        _repo(Wrong()).get_records([KEYS["octavia"]])


# ---------------------------------------------------------------------------
# attribution
# ---------------------------------------------------------------------------
def test_attribution_lists_exactly_the_sources_used():
    assert attribution(_rows(OCTAVIA, GOLF)) == ["משרד התחבורה", "EEA (CC BY 4.0)"]
    assert attribution(_rows(OCTAVIA, ESCAPE_US)) == ["משרד התחבורה", "EEA (CC BY 4.0)", "Transport Canada", "EPA"]
    gov_only = [r for r in _rows(OCTAVIA, GOLF) if all(c.get("source") == "government" for c in r["cells"].values())]
    assert attribution(gov_only) == ["משרד התחבורה"]
    data = _run(_cars("octavia", "golf", prices=[120000, 110000]))["data"]
    assert data["table"]["attribution_he"] == "מקורות: משרד התחבורה; EEA (CC BY 4.0)"


# ---------------------------------------------------------------------------
# no-import rule
# ---------------------------------------------------------------------------
def test_v3_pipeline_does_not_import_enrichment_grounding_or_source_registry():
    code = ("import sys, json; import app.services.comparison_v3.pipeline; "
            "print(json.dumps(sorted(m for m in sys.modules if m.startswith('app.'))))")
    env = {**os.environ, "DATABASE_URL": "sqlite:///:memory:", "SECRET_KEY": "x"}
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True, env=env)
    modules = json.loads(out.stdout.strip().splitlines()[-1])
    for banned in ("official_enrichment", "grounding", "source_registry"):
        assert not [m for m in modules if m.rsplit(".", 1)[-1] == banned], banned
    assert "app.services.comparison_v3.pipeline" in modules
