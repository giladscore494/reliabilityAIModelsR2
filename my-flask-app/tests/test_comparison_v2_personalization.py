# -*- coding: utf-8 -*-
"""V2/2: buyer-profile/2, hard constraints, JEV micro-judgments, deterministic
composition, personalization fixtures and symmetry (all offline).

Fixture outcomes come from the offline fake JEV (``comparison_v2_fakes``),
whose readings depend only on explicit profile data and validated values.
"""

import itertools
import json
import math
import re

import pytest

from app.services.comparison_v2.buyer_profile import (
    BUYER_PROFILE_VERSION,
    FEATURE_SOURCES,
    BuyerProfileError,
    balanced_profile,
    jev_buyer_context,
    normalize_buyer_profile,
)
from app.services.comparison_v2.composer import DecisionComposer
from app.services.comparison_v2.contracts import CHOICE_INSUFFICIENT, CHOICE_TIE
from app.services.comparison_v2.decision_model import (
    MIN_EFFECTIVE_WEIGHT_COVERAGE,
    PRACTICAL_TIE_MARGIN,
    TOWING_WEIGHT_WHEN_REQUIRED,
    dimension_weights,
)
from app.services.comparison_v2.demo_catalog import DemoVehicleCatalogRepository
from app.services.comparison_v2.hard_constraints import FAIL, NOT_APPLICABLE, PASS, UNKNOWN, HardConstraintEvaluator
from app.services.comparison_v2.jev_client import (
    STATUS_OK,
    STATUS_UNAVAILABLE,
    parse_answers,
    parse_choice_answer,
    parse_noul_answer,
    parse_score_answer,
)
from app.services.comparison_v2.level15 import build_level15_snapshot
from app.services.comparison_v2.pipeline import collect_result, compute_request_hash, run_comparison_v2

from comparison_v2_fakes import (
    AUDI_Q3,
    BMW_I4,
    HYUNDAI_TUCSON,
    TOYOTA_SIEENA,
    XPENG_P7I,
    FakeTypeSafeSession,
    build_fake_deps,
    score_answer,
)

PRI = dict(safety=2, performance=2, efficiency=2, practicality=2, purchase_price=2, warranty=2, equipment=2, environment=2)
PRI_EV = {**PRI, "ev_convenience": 2}
GENERAL = {"mode": "general"}


def run(keys, profile=None, session=None):
    deps = build_fake_deps(session=session) if session else build_fake_deps()
    out = collect_result(run_comparison_v2({"cars": [{"variant_identity_key": k} for k in keys]}, deps,
                                           buyer_profile=profile or GENERAL))
    assert out["type"] == "result", out
    return out["data"]


def personal(main_use="mixed", priorities=None, **kw):
    return {"mode": "personalized", "main_use": main_use, "priorities": {**PRI_EV, **(priorities or {})}, **kw}


def snapshots(*keys):
    repo = DemoVehicleCatalogRepository()
    return {f"car_{i + 1}": build_level15_snapshot(repo.get_variant(k), f"car_{i + 1}") for i, k in enumerate(keys)}


def outcome_name(data):
    rec = data["recommendation"]
    return data["cars"][rec["outcome"]]["display_name"] if rec["outcome"] in data["cars"] else rec["outcome"]


# ==========================================================================
# buyer-profile/2
# ==========================================================================
def test_mode_is_required_and_general_uses_documented_balanced_weights():
    with pytest.raises(BuyerProfileError):
        normalize_buyer_profile(None, has_plugin_vehicle=False)
    with pytest.raises(BuyerProfileError):
        normalize_buyer_profile({}, has_plugin_vehicle=False)
    general = normalize_buyer_profile({"mode": "general", "main_use": "family", "priorities": {"safety": 4}}, has_plugin_vehicle=True)
    assert general == balanced_profile(include_ev=True)
    assert general["schema"] == BUYER_PROFILE_VERSION
    assert set(general["priorities"].values()) == {2} and "ev_convenience" in general["priorities"]
    assert "ev_convenience" not in normalize_buyer_profile({"mode": "general"}, has_plugin_vehicle=False)["priorities"]


@pytest.mark.parametrize("bad", [
    {"mode": "personalized", "priorities": PRI},                                   # main_use missing
    {"mode": "personalized", "main_use": "space", "priorities": PRI},              # unknown enum
    {"mode": "personalized", "main_use": "city", "priorities": {**PRI, "safety": 5}},
    {"mode": "personalized", "main_use": "city", "priorities": {**PRI, "reliability": 3}},  # unsupported concept
    {"mode": "personalized", "main_use": "city", "priorities": {k: v for k, v in PRI.items() if k != "safety"}},
    {"mode": "personalized", "main_use": "city", "priorities": PRI, "regular_passengers": 12},
    {"mode": "personalized", "main_use": "city", "priorities": PRI, "annual_km": "a lot"},
    {"mode": "personalized", "main_use": "city", "priorities": PRI, "annual_km": float("nan")},
    {"mode": "personalized", "main_use": "city", "priorities": PRI, "cargo_need": "גדול"},  # free text
    {"mode": "personalized", "main_use": "city", "priorities": PRI, "must_have_features": ["massage_seats"]},
    {"mode": "personalized", "main_use": "city", "priorities": PRI, "awd_requirement": True},
    {"mode": "auto"},
])
def test_invalid_profiles_are_rejected(bad):
    with pytest.raises(BuyerProfileError):
        normalize_buyer_profile(bad, has_plugin_vehicle=False)


def test_normalized_profile_is_canonical_and_free_of_free_text():
    raw = personal("family", annual_km="18000", regular_passengers=4, budget_max_ils=260000, cargo_need="high",
                   parking_constraint="normal", awd_requirement="preferred", road_conditions="normal_roads",
                   charging_access="home", typical_daily_km=55, frequent_long_trip_km=300,
                   must_have_features=["surround_view_camera", "apple_carplay"],
                   nice_to_have_features=["heated_front_seats", "apple_carplay"], family_size="זוג + 2")
    p = normalize_buyer_profile(raw, has_plugin_vehicle=True)
    assert p["annual_km"] == 18000 and p["must_have_features"] == ["apple_carplay", "surround_view_camera"]
    assert p["nice_to_have_features"] == ["heated_front_seats"]  # never both required and nice-to-have
    assert "family_size" not in p
    allowed_strings = {"personalized", "family", "high", "normal", "preferred", "normal_roads", "home", BUYER_PROFILE_VERSION,
                       *FEATURE_SOURCES}

    def walk(v):
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, str):
            assert v in allowed_strings, v
    walk(p)
    # EV fields are dropped when no plug-in car is selected
    no_ev = normalize_buyer_profile(raw, has_plugin_vehicle=False)
    assert no_ev["charging_access"] is None and "ev_convenience" not in no_ev["priorities"]


def test_request_hash_includes_the_complete_normalized_profile():
    meta = {"enrichment_model": "m", "summary_model": "m", "jev_model": "j"}
    a = normalize_buyer_profile(personal("family"), has_plugin_vehicle=False)
    b = normalize_buyer_profile(personal("family", annual_km=30000), has_plugin_vehicle=False)
    a2 = normalize_buyer_profile({"priorities": PRI, "main_use": "family", "mode": "personalized"}, has_plugin_vehicle=False)
    keys = [AUDI_Q3, HYUNDAI_TUCSON]
    assert compute_request_hash(keys, a, meta) != compute_request_hash(keys, b, meta)
    assert compute_request_hash(keys, a, meta) == compute_request_hash(keys, a2, meta)
    general = normalize_buyer_profile(GENERAL, has_plugin_vehicle=False)
    assert compute_request_hash(keys, general, meta) != compute_request_hash(keys, a, meta)


def test_jev_context_never_contains_weights():
    p = normalize_buyer_profile(personal("family", priorities={"safety": 4}, annual_km=1000), has_plugin_vehicle=False)
    ctx = jev_buyer_context(p)
    assert ctx["main_use"] == "family" and "priorities" not in ctx


# ==========================================================================
# hard constraints
# ==========================================================================
def evaluate(profile, *keys, enrich=False):
    snaps = snapshots(*keys)
    if enrich:
        from app.services.comparison_v2.cache import InProcessEnrichmentCache
        from app.services.comparison_v2.official_enrichment import LiveOfficialEnrichmentRepository

        from comparison_v2_fakes import FakeEnrichmentProvider

        repo = LiveOfficialEnrichmentRepository(FakeEnrichmentProvider(), InProcessEnrichmentCache())
        for snap in snaps.values():
            snap["official_enrichment"] = repo.get_or_enrich(snap)
    p = normalize_buyer_profile(profile, has_plugin_vehicle=any(s["derived"]["powertrain_family"] in ("ev", "phev") for s in snaps.values()))
    return HardConstraintEvaluator().evaluate(p, snaps)


def statuses(result, key):
    return {slot: r["status"] for slot, r in next(c for c in result["constraints"] if c["key"] == key)["per_car"].items()}


def test_budget_pass_fail_unknown():
    r = evaluate(personal(budget_max_ils=200000), AUDI_Q3, HYUNDAI_TUCSON, enrich=True)
    assert statuses(r, "budget") == {"car_1": UNKNOWN, "car_2": FAIL}  # Audi's only price was foreign -> rejected
    r = evaluate(personal(budget_max_ils=250000), AUDI_Q3, HYUNDAI_TUCSON, enrich=True)
    assert statuses(r, "budget") == {"car_1": UNKNOWN, "car_2": PASS}
    assert r["eligible_slots"] == ["car_1", "car_2"]  # unknown is never a failure


def test_passenger_requirement():
    r = evaluate(personal(regular_passengers=6), AUDI_Q3, TOYOTA_SIEENA)
    assert statuses(r, "passengers") == {"car_1": FAIL, "car_2": PASS}
    assert r["eligible_slots"] == ["car_2"]


def test_towing_requirement():
    r = evaluate(personal(towing_braked_required_kg=1800), AUDI_Q3, BMW_I4)
    assert statuses(r, "towing") == {"car_1": PASS, "car_2": FAIL}


def test_awd_required_is_hard_and_preferred_is_soft():
    r = evaluate(personal(awd_requirement="required"), AUDI_Q3, BMW_I4)
    assert statuses(r, "awd") == {"car_1": PASS, "car_2": FAIL}
    soft = evaluate(personal(awd_requirement="preferred"), AUDI_Q3, BMW_I4)
    assert soft["constraints"] == [] and soft["eligible_slots"] == ["car_1", "car_2"]
    assert soft["per_car_status"] == {"car_1": NOT_APPLICABLE, "car_2": NOT_APPLICABLE}


def test_must_have_equipment_missing_is_unknown_never_fail():
    r = evaluate(personal(must_have_features=["apple_carplay", "reverse_camera"]), AUDI_Q3, HYUNDAI_TUCSON, enrich=True)
    assert statuses(r, "feature:apple_carplay") == {"car_1": UNKNOWN, "car_2": UNKNOWN}
    assert statuses(r, "feature:reverse_camera") == {"car_1": PASS, "car_2": PASS}  # government ADAS flag
    assert r["failing_slots"] == []


def test_general_mode_has_no_hard_constraints():
    r = evaluate(GENERAL, AUDI_Q3, BMW_I4)
    assert r["constraints"] == [] and r["eligible_slots"] == ["car_1", "car_2"]


# ==========================================================================
# composition
# ==========================================================================
def test_hard_constraint_failure_cannot_be_overridden_by_jev():
    # Performance-critical buyer: every judgment favours the BMW, but the BMW
    # definitively fails the AWD requirement -> the Audi is selected.
    always_decisive = FakeTypeSafeSession(materiality_fn=lambda group, buyer: 4)
    data = run([AUDI_Q3, BMW_I4], personal("highway", priorities={"performance": 4}, awd_requirement="required"),
               session=always_decisive)
    assert data["recommendation"]["outcome"] == "car_1"
    assert data["recommendation"]["basis"] == "hard_constraints"
    assert any(n["level"] == "fail" and n["slot"] == "car_2" for n in data["hard_constraints"]["notes"])
    # no judgments are spent on a pair that cannot change the outcome
    assert data["decision"]["question_count"] == 0


def test_all_failing_means_no_vehicle_meets_requirements():
    data = run([AUDI_Q3, BMW_I4], personal(regular_passengers=7))
    assert data["recommendation"]["outcome"] == "no_vehicle_meets_requirements"
    assert data["recommendation"]["recommended_slot"] is None


def test_zero_priority_cannot_influence_overall():
    profile = personal("mixed", priorities={"performance": 0})
    low = run([AUDI_Q3, HYUNDAI_TUCSON], profile, FakeTypeSafeSession(materiality_fn=lambda g, b: 0 if g in ("power_output", "acceleration") else 2))
    high = run([AUDI_Q3, HYUNDAI_TUCSON], profile, FakeTypeSafeSession(materiality_fn=lambda g, b: 4 if g in ("power_output", "acceleration") else 2))
    assert low["decision_trace"]["overall_composition"]["pairs"] == high["decision_trace"]["overall_composition"]["pairs"]
    perf = low["decision_trace"]["overall_composition"]["pairs"]["car_1__car_2"]["dimensions"]["performance"]
    assert perf["weight"] == 0 and perf["contribution"] is None


def test_high_priority_category_materially_changes_the_outcome():
    perf = run([AUDI_Q3, BMW_I4], personal("highway", priorities={"performance": 4}))
    tow = run([AUDI_Q3, BMW_I4], personal("work", towing_braked_required_kg=1500, priorities={"performance": 1}))
    assert outcome_name(perf) == "BMW i4 eDrive35"
    assert outcome_name(tow) == "Audi Q3"
    assert tow["recommendation"]["basis"] == "composition"  # both pass 1,500 kg; towing weighs only because it is required


def test_towing_cannot_affect_overall_when_user_does_not_tow():
    decisive_towing = FakeTypeSafeSession(materiality_fn=lambda g, b: 4 if g == "towing" else 2)
    data = run([AUDI_Q3, BMW_I4], GENERAL, session=decisive_towing)
    pair = data["decision_trace"]["overall_composition"]["pairs"]["car_1__car_2"]
    assert pair["dimensions"]["towing"]["weight"] == 0 and pair["dimensions"]["towing"]["contribution"] is None
    assert not any("towing" in q for q in data["decision_trace"]["materiality_scores"])
    p = normalize_buyer_profile(personal(towing_braked_required_kg=1000), has_plugin_vehicle=False)
    assert dimension_weights(p, ["combustion"])["towing"] == TOWING_WEIGHT_WHEN_REQUIRED


def test_conflicted_and_missing_data_are_neutral():
    data = run([BMW_I4, XPENG_P7I], personal("commuting", charging_access="home"))
    trace = data["decision_trace"]
    directions = trace["pairwise_direction"]["car_1__car_2"]
    assert directions["dc_charging"] == "none"  # XPENG's DC power is conflicted, BMW's alone is not evidence
    assert directions["electric_range"] == "none"  # WLTP vs CLTC -> not comparable
    assert not any(q.endswith(("__dc_charging", "__electric_range")) for q in trace["materiality_scores"])


def test_effective_coverage_is_separate_from_strength():
    data = run([AUDI_Q3, BMW_I4], personal(priorities={k: 0 for k in PRI} | {"environment": 4, "ev_convenience": 0}))
    rec = data["recommendation"]
    assert rec["outcome"] == CHOICE_INSUFFICIENT and rec["strength"] is None
    pair = data["decision_trace"]["overall_composition"]["pairs"]["car_1__car_2"]
    assert pair["effective_weight_coverage"] < MIN_EFFECTIVE_WEIGHT_COVERAGE
    ok = run([AUDI_Q3, BMW_I4], personal("highway", priorities={"performance": 4}))["recommendation"]
    assert ok["strength"] in ("clear", "moderate", "slight") and 0 < ok["evidence_coverage"] <= 1


def test_no_score_out_of_100_and_no_overall_confidence():
    data = run([AUDI_Q3, HYUNDAI_TUCSON], personal("family"))
    public = {k: v for k, v in data.items() if k != "decision_trace"}
    text = json.dumps(public, ensure_ascii=False)
    assert not re.search(r"/100(?!km)", text)  # only the L/100km & kWh/100km units
    assert "confidence" not in json.dumps(data["recommendation"]) and "score" not in json.dumps(data["recommendation"])
    assert data["decision_trace"]["overall_composition"]["constants"]["calibrated"] is False


class _FixedPairs(DecisionComposer):
    """Composer with injected pair outcomes (tests the selection rule only)."""

    def __init__(self, outcomes):
        super().__init__({"priorities": {}}, {s: {} for s in ("car_1", "car_2", "car_3")}, {},
                         {"eligible_slots": ["car_1", "car_2", "car_3"]}, {}, {"status": "ok"}, {"safety": 2})
        self.outcomes = outcomes

    def compose_pair(self, a, b):
        outcome, u = self.outcomes[(a, b)]
        return {"pair": [a, b], "utility": u, "effective_weight_coverage": 1.0, "outcome": outcome, "dimensions": {}}


def test_three_car_selection_requires_beating_every_other_car():
    clear = _FixedPairs({("car_1", "car_2"): ("car_1", 0.4), ("car_1", "car_3"): ("car_1", 0.2), ("car_2", "car_3"): ("car_3", -0.1)}).compose()
    assert clear["outcome"] == "car_1" and clear["strength"] == "moderate"
    unresolved = _FixedPairs({("car_1", "car_2"): ("car_1", 0.4), ("car_1", "car_3"): (CHOICE_INSUFFICIENT, 0.3),
                              ("car_2", "car_3"): (CHOICE_TIE, 0.0)}).compose()
    assert unresolved["outcome"] == CHOICE_INSUFFICIENT  # never promoted over a car it could not be compared with
    cycle = _FixedPairs({("car_1", "car_2"): ("car_1", 0.2), ("car_1", "car_3"): ("car_3", -0.2), ("car_2", "car_3"): ("car_2", 0.2)}).compose()
    assert cycle["outcome"] == CHOICE_TIE


def test_practical_tie_margin_is_named():
    assert 0 < PRACTICAL_TIE_MARGIN < 0.2


# ==========================================================================
# personalization fixtures: identical facts, different explicit needs
# ==========================================================================
@pytest.mark.parametrize("label,keys,profile,expected,reason_fragment", [
    ("performance-focused", [AUDI_Q3, BMW_I4], personal("highway", priorities={"performance": 4}), "BMW i4 eDrive35", "ביצועים — קריטי עבורך"),
    ("family/cargo + body fit", [AUDI_Q3, BMW_I4], personal("family", cargo_need="high", regular_passengers=5,
                                                             priorities={"practicality": 4, "performance": 0}), "Audi Q3", "התאמת המרכב לשימוש"),
    ("towing buyer", [AUDI_Q3, BMW_I4], personal("work", towing_braked_required_kg=1500), "Audi Q3", "גרירה"),
    ("EV buyer with home charging", [AUDI_Q3, BMW_I4], personal("commuting", annual_km=30000, charging_access="home",
                                                                 priorities={"ev_convenience": 4}), "BMW i4 eDrive35", "שגרת הטעינה"),
    ("high-mileage commuter", [AUDI_Q3, HYUNDAI_TUCSON], personal("commuting", annual_km=40000, priorities={"efficiency": 4}),
     "Hyundai Tucson Hybrid", "צריכת דלק"),
])
def test_profile_driven_outcomes(label, keys, profile, expected, reason_fragment):
    data = run(keys, profile)
    assert outcome_name(data) == expected, label
    assert any(reason_fragment in r for r in data["recommendation"]["reasons_he"]["for"]), (label, data["recommendation"]["reasons_he"])


def test_no_charging_access_removes_the_ev_advantage():
    home = run([AUDI_Q3, BMW_I4], personal("commuting", charging_access="home", priorities={"ev_convenience": 4}))
    none = run([AUDI_Q3, BMW_I4], personal("commuting", charging_access="none", priorities={"ev_convenience": 4}))
    fit_home = home["decision_trace"]["contextual_fit_scores"]["fit__car_2__charging_routine_fit"]
    fit_none = none["decision_trace"]["contextual_fit_scores"]["fit__car_2__charging_routine_fit"]
    assert fit_home > fit_none
    u_home = home["decision_trace"]["overall_composition"]["pairs"]["car_1__car_2"]["utility"]
    u_none = none["decision_trace"]["overall_composition"]["pairs"]["car_1__car_2"]["utility"]
    assert u_none > u_home  # moves toward the Audi purely because of the stated charging access
    assert none["recommendation"]["outcome"] != "car_2"


def test_contextual_fit_questions_only_when_relevant():
    general = run([AUDI_Q3, BMW_I4])
    assert not any(q.startswith("fit__") for q in general["decision_trace"]["contextual_fit_scores"])
    city = run([AUDI_Q3, BMW_I4], personal("city"))
    fits = set(city["decision_trace"]["contextual_fit_scores"])
    assert fits == {"fit__car_1__body_use_fit", "fit__car_2__body_use_fit"}  # no parking/clearance/charging context given
    rough = run([AUDI_Q3, BMW_I4], personal("city", road_conditions="rough_roads", parking_constraint="tight"))
    skipped = {s.get("fit") for s in rough["decision_trace"]["jev_skipped_questions"]}
    assert {"ground_clearance_fit", "parking_fit"} <= skipped  # no validated clearance / dimensions -> not asked


# ==========================================================================
# symmetry
# ==========================================================================
def test_two_car_swap_mirrors_everything():
    profile = personal("family", charging_access="home", regular_passengers=4, priorities={"safety": 4})
    ab = run([AUDI_Q3, BMW_I4], profile)
    ba = run([BMW_I4, AUDI_Q3], profile)
    assert outcome_name(ab) == outcome_name(ba)
    pa = ab["decision_trace"]["overall_composition"]["pairs"]["car_1__car_2"]
    pb = ba["decision_trace"]["overall_composition"]["pairs"]["car_1__car_2"]
    assert math.isclose(pa["utility"], -pb["utility"], abs_tol=1e-9)
    assert ab["decision_trace"]["materiality_scores"] == ba["decision_trace"]["materiality_scores"]
    swap = {"car_1": "car_2", "car_2": "car_1"}
    fits_ab = {q: v for q, v in ab["decision_trace"]["contextual_fit_scores"].items()}
    fits_ba = {q.replace("car_1", "X").replace("car_2", "car_1").replace("X", "car_2"): v
               for q, v in ba["decision_trace"]["contextual_fit_scores"].items()}
    assert fits_ab == fits_ba
    # the same reasons, in the same order, explain the same car
    assert ab["recommendation"]["reasons_he"] == ba["recommendation"]["reasons_he"]
    assert {swap[s] for s in ab["hard_constraints"]["eligible_slots"]} == set(ba["hard_constraints"]["eligible_slots"])


@pytest.mark.parametrize("profile", [GENERAL, personal("family", priorities={"practicality": 4}, charging_access="public_only")])
def test_three_car_permutations_preserve_semantics(profile):
    base = [AUDI_Q3, HYUNDAI_TUCSON, BMW_I4]
    results = []
    for perm in itertools.permutations(base):
        data = run(list(perm), profile)
        names = {slot: c["display_name"] for slot, c in data["cars"].items()}
        utilities = {}
        for p in data["decision_trace"]["overall_composition"]["pairs"].values():
            a, b = (names[s] for s in p["pair"])
            utilities[tuple(sorted((a, b)))] = round(p["utility"] if a < b else -p["utility"], 9)
        tied = sorted(names[s] for s in data["decision_trace"]["overall_composition"]["tied_slots"])
        results.append((outcome_name(data), tied, utilities, data["decision"]["question_count"]))
    assert all(r == results[0] for r in results), results


# ==========================================================================
# typed JEV answer parsing
# ==========================================================================
def test_score_answer_validation():
    ok = parse_score_answer("q", score_answer(3), 5)
    assert ok.status == STATUS_OK and 2.9 < ok.score < 3.1 and ok.confidence == 0.85 and ok.legend
    cases = {
        "missing_answer": None,
        "answer_not_object": [1, 2],
        "unexpected_type": {"type": "choice", "score": 2},
        "score_not_finite_number": {"type": "score", "score": float("inf")},
        "score_out_of_range": {"type": "score", "score": 4.5},
        "probability_key_unknown": {"type": "score", "score": 2, "probabilities": {"7": 1.0}},
        "probability_value_invalid": {"type": "score", "score": 2, "probabilities": {"2": float("nan")}},
        "probabilities_do_not_sum_to_1": {"type": "score", "score": 2, "probabilities": {"2": 0.5}},
        "score_inconsistent_with_distribution": {"type": "score", "score": 0.0, "probabilities": {"4": 1.0}},
        "confidence_invalid": {"type": "score", "score": 2, "confidence": 3},
    }
    for reason, answer in cases.items():
        res = parse_score_answer("q", answer, 5)
        assert res.status == STATUS_UNAVAILABLE and res.reason == reason, reason
        assert res.score is None


def test_noul_and_choice_answers():
    assert parse_noul_answer("n", {"noul": 0.3}).noul == 0.3
    assert parse_noul_answer("n", {"noul": 1.3}).status == STATUS_UNAVAILABLE
    choice = parse_choice_answer("c", {"type": "choice", "choice": "b", "confidence": 0.6, "probabilities": {"a": 0.4, "b": 0.6}}, ["a", "b"])
    assert choice.status == STATUS_OK and choice.choice == "b"
    assert parse_choice_answer("c", {"choice": "z"}, ["a", "b"]).status == STATUS_UNAVAILABLE


def test_parse_answers_is_per_question_and_ignores_unrequested_ids():
    questions = {"q1": {"type": "score", "criteria": ["a", "b", "c"]}, "q2": {"type": "score", "criteria": ["a", "b", "c"]}}
    data = {"answers": {"q1": score_answer(1, levels=3), "q2": {"type": "score", "score": 9}, "evil": {"choice": "car_1"}}}
    parsed, unexpected = parse_answers(data, questions)
    assert parsed["q1"].status == STATUS_OK and parsed["q2"].status == STATUS_UNAVAILABLE
    assert unexpected == ["evil"] and "evil" not in parsed
