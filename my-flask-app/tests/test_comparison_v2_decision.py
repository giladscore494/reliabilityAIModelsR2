# -*- coding: utf-8 -*-
"""JEV System One request/answers, summary writer and pipeline behaviour (all offline)."""

import copy
import json
import re
from datetime import datetime, timedelta, timezone

import pytest

from app.services.comparison_v2.cache import InProcessEnrichmentCache
from app.services.comparison_v2.decision_model import FIT_LEVELS, MATERIALITY_LEVELS
from app.services.comparison_v2.jev_client import (
    extract_model_ids,
    redacted_request_shape,
    reset_model_verification_cache,
)
from app.services.comparison_v2.official_enrichment import (
    ENRICHMENT_RESPONSE_SCHEMA,
    LiveOfficialEnrichmentRepository,
    build_official_enrichment_prompt,
)
from app.services.comparison_v2.pipeline import collect_result, run_comparison_v2
from app.services.comparison_v2.summary_writer import validate_summary

from comparison_v2_fakes import (
    AUDI_Q3,
    BMW_I4,
    CADILLAC_ESCALADE_IQ,
    HYUNDAI_TUCSON,
    TOYOTA_SIEENA,
    XPENG_P7I,
    FakeEnrichmentProvider,
    FakeSummaryWriter,
    FakeTypeSafeSession,
    build_fake_deps,
)


GENERAL = {"mode": "general"}
PRI = dict(safety=2, performance=2, efficiency=2, practicality=2, purchase_price=2, warranty=2, equipment=2, environment=2)
QID = re.compile(r"^(materiality__car_[1-3]__car_[1-3]__[a-z0-9_]+|fit__car_[1-3]__[a-z_]+)$")
HEBREW = re.compile(r"[\u0590-\u05ff]")


def run(keys, deps, profile=None):
    data = {"cars": [{"variant_identity_key": k} for k in keys]}
    return collect_result(run_comparison_v2(data, deps, buyer_profile=profile or GENERAL))


def flip(choice):
    return {"car_1": "car_2", "car_2": "car_1"}.get(choice, choice)


def resolve(state, path):
    node = state
    for part in path.split("."):
        assert isinstance(node, dict) and part in node, path
        node = node[part]
    return node


# --------------------------------------------------------------------------
# JEV System One request contract (V2/2)
# --------------------------------------------------------------------------
def test_one_systemone_call_with_many_narrow_score_questions():
    session = FakeTypeSafeSession()
    out = run([AUDI_Q3, HYUNDAI_TUCSON], build_fake_deps(session=session))
    assert out["type"] == "result"
    assert len(session.post_calls) == 1
    call = session.post_calls[0]
    assert call["url"] == "https://jev.test/v1/systemone"
    assert call["headers"]["Authorization"] == "Bearer ts-test-secret-key"
    body = call["json"]
    assert body["model"] == "jev-1.13.0"
    assert set(body["state"]) == {"buyer_profile", "pairwise_objective_evidence", "contextual_vehicle_evidence", "hard_constraint_results"}
    questions = body["questions"]
    assert len(questions) >= 5
    assert "overall" not in questions
    for qid, q in questions.items():
        assert QID.match(qid), qid
        assert q["type"] == "score"
        assert q["criteria"] in (MATERIALITY_LEVELS, *FIT_LEVELS.values())
    # no broad category-winner questions and no car_N/tie criteria anywhere
    from app.services.comparison_v2.deterministic_engine import CATEGORIES
    assert not set(questions) & set(CATEGORIES)
    text = json.dumps(questions)
    assert "insufficient_evidence" not in text and "Which car" not in text and "better overall?" not in text
    assert out["data"]["decision"]["primitives"] == ["score"]
    assert out["data"]["decision"]["question_count"] == len(questions)


def test_materiality_questions_only_for_code_determined_differences():
    session = FakeTypeSafeSession()
    out = run([AUDI_Q3, HYUNDAI_TUCSON], build_fake_deps(session=session))["data"]
    directions = out["decision_trace"]["pairwise_direction"]["car_1__car_2"]
    asked = {qid.split("__")[3] for qid in session.post_calls[0]["json"]["questions"] if qid.startswith("materiality__")}
    weights = out["decision_trace"]["overall_composition"]["weights"]
    from app.services.comparison_v2.decision_model import GROUP_DIMENSION
    assert asked == {g for g, d in directions.items()
                     if d in ("car_1", "car_2") and g in GROUP_DIMENSION and weights[GROUP_DIMENSION[g]] > 0}
    assert "towing" not in asked  # no towing requirement -> towing is never weighed
    # missing / tie / not comparable groups are never turned into a question
    assert all(directions[g] in ("car_1", "car_2") for g in asked)


def test_three_cars_one_call_with_pairwise_questions():
    session = FakeTypeSafeSession()
    out = run([AUDI_Q3, HYUNDAI_TUCSON, BMW_I4], build_fake_deps(session=session))["data"]
    assert len(session.post_calls) == 1 and out["provider_meta"]["jev_calls"] == 1
    pairs = {"__".join(q.split("__")[1:3]) for q in session.post_calls[0]["json"]["questions"] if q.startswith("materiality__")}
    assert pairs == {"car_1__car_2", "car_1__car_3", "car_2__car_3"}


def test_state_contains_only_compact_validated_data_in_english():
    session = FakeTypeSafeSession()
    run([AUDI_Q3, HYUNDAI_TUCSON], build_fake_deps(session=session),
        profile={"mode": "personalized", "main_use": "family", "parking_constraint": "tight", "priorities": PRI})
    body = session.post_calls[0]["json"]
    text = json.dumps(body, ensure_ascii=False)
    # rejected / model-generic / third-party values never reach JEV
    for banned in ("carzone", "evil.com", "299000", "1650", "Bose", "http", "<", "source_url", "source_title",
                   "ts-test-secret-key", "rejected", "Audi", "Hyundai", "Tucson", "Q3"):
        assert banned not in text, banned
    assert not HEBREW.search(text)  # English canonical internal wording
    assert "193" not in json.dumps(body["state"]["pairwise_objective_evidence"]["factor_values"].get("top_speed", {}))
    torque = body["state"]["pairwise_objective_evidence"]["factor_values"]["power_output"]["torque_nm"]
    assert torque["car_2"] == 350


def test_no_value_is_duplicated_between_state_subtrees():
    session = FakeTypeSafeSession()
    run([BMW_I4, AUDI_Q3], build_fake_deps(session=session),
        profile={"mode": "personalized", "main_use": "commuting", "charging_access": "home", "typical_daily_km": 50,
                 "priorities": {**PRI, "ev_convenience": 3}})
    state = session.post_calls[0]["json"]["state"]
    factor_keys = {(metric, slot) for g in state["pairwise_objective_evidence"]["factor_values"].values()
                   for metric, vals in g.items() for slot in vals if slot.startswith("car_")}
    ctx_keys = {(k, slot) for slot, vals in state["contextual_vehicle_evidence"].items() for k in vals}
    assert not factor_keys & ctx_keys
    assert any(q.startswith("fit__") and q.endswith("charging_routine_fit") for q in session.post_calls[0]["json"]["questions"])


def test_every_question_references_existing_state_paths():
    session = FakeTypeSafeSession()
    out = run([BMW_I4, AUDI_Q3, HYUNDAI_TUCSON], build_fake_deps(session=session),
              profile={"mode": "personalized", "main_use": "family", "charging_access": "public_only",
                       "road_conditions": "rough_roads", "priorities": {**PRI, "ev_convenience": 2}})["data"]
    body = session.post_calls[0]["json"]
    specs = out["decision_trace"]["jev_question_specs"]
    assert {s["question_id"] for s in specs} == set(body["questions"])
    for spec in specs:
        for path in spec["state_paths"]:
            resolve(body["state"], path)
            assert path in body["questions"][spec["question_id"]]["instructions"], (spec["question_id"], path)


def test_priorities_stay_in_code_and_do_not_reach_jev():
    session = FakeTypeSafeSession()
    run([AUDI_Q3, HYUNDAI_TUCSON], build_fake_deps(session=session),
        profile={"mode": "personalized", "main_use": "family", "annual_km": 18000, "priorities": {**PRI, "safety": 4}})
    buyer = session.post_calls[0]["json"]["state"]["buyer_profile"]
    assert buyer["main_use"] == "family" and buyer["annual_km"] == 18000
    assert "priorities" not in buyer and "weights" not in json.dumps(buyer)


def test_zero_weight_dimension_is_not_asked():
    session = FakeTypeSafeSession()
    run([AUDI_Q3, HYUNDAI_TUCSON], build_fake_deps(session=session),
        profile={"mode": "personalized", "main_use": "city", "priorities": {**PRI, "performance": 0}})
    asked = {q.split("__")[3] for q in session.post_calls[0]["json"]["questions"] if q.startswith("materiality__")}
    assert not asked & {"power_output", "acceleration", "top_speed"}


def test_ab_swap_flips_outcome_and_preserves_judgments():
    ab = run([AUDI_Q3, HYUNDAI_TUCSON], build_fake_deps())["data"]
    ba = run([HYUNDAI_TUCSON, AUDI_Q3], build_fake_deps())["data"]
    assert ab["recommendation"]["outcome"] == "car_2"
    assert ba["recommendation"]["outcome"] == flip(ab["recommendation"]["outcome"])
    pa = ab["decision_trace"]["overall_composition"]["pairs"]["car_1__car_2"]
    pb = ba["decision_trace"]["overall_composition"]["pairs"]["car_1__car_2"]
    assert pa["utility"] == -pb["utility"]
    assert pa["effective_weight_coverage"] == pb["effective_weight_coverage"]
    # same questions, same materiality readings, inverted code directions
    assert ab["decision_trace"]["materiality_scores"] == ba["decision_trace"]["materiality_scores"]
    da = ab["decision_trace"]["pairwise_direction"]["car_1__car_2"]
    db = ba["decision_trace"]["pairwise_direction"]["car_1__car_2"]
    assert {g: flip(d) for g, d in da.items()} == db
    assert [r.replace("Hyundai Tucson Hybrid", "X") for r in ab["recommendation"]["reasons_he"]["for"]] == \
        [r.replace("Hyundai Tucson Hybrid", "X") for r in ba["recommendation"]["reasons_he"]["for"]]


def test_missing_data_gives_no_advantage_to_other_side():
    # Toyota's official claim is rejected -> no Level 2 at all for car_1.
    session = FakeTypeSafeSession()
    out = run([TOYOTA_SIEENA, HYUNDAI_TUCSON], build_fake_deps(session=session))["data"]
    perf = out["categories"]["performance"]["evidence"]["atomic_results"]
    torque = next(r for r in perf if r["metric"] == "torque_nm")
    assert torque["status"] == "insufficient_data" and torque["leader"] is None
    eff = out["categories"]["efficiency"]
    assert eff["evidence_status"] == "insufficient_data"
    assert eff["influence_status"] == "insufficient_data"
    assert not any("fuel_use" in q for q in session.post_calls[0]["json"]["questions"])
    assert out["coverage"]["car_2"]["official"]["present"] > out["coverage"]["car_1"]["official"]["present"]
    for cat in out["categories"].values():
        for r in cat["evidence"]["atomic_results"]:
            if "car_1" in r["missing"]:
                assert r["leader"] is None, r["metric"]


def test_malformed_answer_only_drops_that_question():
    probe = run([AUDI_Q3, HYUNDAI_TUCSON], build_fake_deps())["data"]
    qid = next(q for q in probe["decision_trace"]["materiality_scores"])
    bad = {qid: {"type": "score", "score": 7, "confidence": 0.9, "probabilities": {"0": 1.0}}}
    out = run([AUDI_Q3, HYUNDAI_TUCSON], build_fake_deps(session=FakeTypeSafeSession(answers_override=bad)))["data"]
    answers = out["decision_trace"]["jev_answers"]
    assert answers[qid]["status"] == "judgment_unavailable"
    assert sum(a["status"] == "ok" for a in answers.values()) == len(answers) - 1
    assert out["decision"]["status"] == "ok"
    assert out["recommendation"]["outcome"] != "decision_unavailable"


def test_score_answer_distribution_model_and_usage_stored_verbatim():
    probe = run([AUDI_Q3, HYUNDAI_TUCSON], build_fake_deps())["data"]
    qid = next(q for q in probe["decision_trace"]["materiality_scores"])
    probs = {"0": 0.05, "1": 0.15, "2": 0.5, "3": 0.25, "4": 0.05}
    override = {qid: {"type": "score", "score": 2.1, "confidence": 0.42, "probabilities": probs,
                      "legend": {str(i): MATERIALITY_LEVELS[i] for i in range(5)}}}
    session = FakeTypeSafeSession(answers_override=override, response_model="jev-1.13.0-20260915")
    out = run([AUDI_Q3, HYUNDAI_TUCSON], build_fake_deps(session=session, jev_model="jev-latest"))["data"]
    ans = out["decision_trace"]["jev_answers"][qid]
    assert (ans["score"], ans["confidence"], ans["probabilities"]) == (2.1, 0.42, probs)
    assert ans["legend"]["2"] == MATERIALITY_LEVELS[2]
    assert out["decision"]["requested_model"] == "jev-latest"
    assert out["decision"]["response_model"] == "jev-1.13.0-20260915"
    assert out["decision"]["usage"] == {"input_tokens": 1234, "output_tokens": 0}
    # no overall confidence is manufactured
    assert "confidence" not in json.dumps(out["recommendation"])


def test_confidence_is_never_averaged_into_the_decision():
    def with_confidence(conf):
        probe = run([AUDI_Q3, HYUNDAI_TUCSON], build_fake_deps())["data"]
        override = {}
        for qid, ans in probe["decision_trace"]["jev_answers"].items():
            override[qid] = {k: ans[k] for k in ("type", "score", "probabilities")}
            override[qid]["confidence"] = conf
        return run([AUDI_Q3, HYUNDAI_TUCSON], build_fake_deps(session=FakeTypeSafeSession(answers_override=override)))["data"]

    low, high = with_confidence(0.2), with_confidence(0.99)
    assert low["decision_trace"]["overall_composition"]["pairs"] == high["decision_trace"]["overall_composition"]["pairs"]
    assert low["recommendation"] == high["recommendation"]
    assert _no_time(low["vehicle_snapshots"]) == _no_time(high["vehicle_snapshots"])


def test_jev_failure_keeps_deterministic_facts_and_never_asks_gemini_for_a_winner():
    writer = FakeSummaryWriter()
    out = run([AUDI_Q3, HYUNDAI_TUCSON], build_fake_deps(session=FakeTypeSafeSession(fail_systemone=True), writer=writer))
    data = out["data"]
    assert out["type"] == "result"
    assert data["recommendation"]["outcome"] == "decision_unavailable"
    assert data["recommendation"]["recommended_slot"] is None
    assert writer.calls == 0
    assert data["summary_source"] == "deterministic_fallback"
    safety = data["categories"]["safety"]
    assert safety["influence_status"] == "judgment_unavailable"
    assert any(r["status"] == "compared" for r in safety["evidence"]["atomic_results"])


def _no_time(obj):
    if isinstance(obj, dict):
        return {k: _no_time(v) for k, v in obj.items() if k != "observed_at"}
    if isinstance(obj, list):
        return [_no_time(v) for v in obj]
    return obj


def test_unverified_jev_model_is_never_used():
    session = FakeTypeSafeSession(models={"data": [{"id": "jev-1.13.0"}]})
    out = run([AUDI_Q3, HYUNDAI_TUCSON], build_fake_deps(session=session, jev_model="jev-latest"))["data"]
    assert session.post_calls == []
    assert out["recommendation"]["outcome"] == "decision_unavailable"
    assert out["decision"]["reason"] == "jev_model_not_in_account_models"


def test_model_list_parsing_handles_ids_and_aliases():
    ids = extract_model_ids({"data": [{"id": "jev-1.13.0", "aliases": ["jev-latest", "jev-preview"]}]})
    assert ids == ["jev-1.13.0", "jev-latest", "jev-preview"]
    assert extract_model_ids({"models": ["a", {"name": "b"}]}) == ["a", "b"]


def test_redacted_request_shape_hides_key():
    reset_model_verification_cache()
    deps = build_fake_deps()
    out = run([AUDI_Q3, HYUNDAI_TUCSON], deps)
    shape = redacted_request_shape(deps.jev_client.session.post_calls[0]["json"])
    assert shape["headers"]["Authorization"] == "Bearer [REDACTED]"
    assert "ts-test-secret-key" not in json.dumps(shape)
    assert out["type"] == "result"


def test_response_never_contains_secrets():
    out = run([AUDI_Q3, HYUNDAI_TUCSON], build_fake_deps())["data"]
    text = json.dumps(out, ensure_ascii=False)
    assert "ts-test-secret-key" not in text and "Authorization" not in text and "api_key" not in text.lower()


# --------------------------------------------------------------------------
# summary
# --------------------------------------------------------------------------
def test_summary_cannot_change_decision():
    def contrarian(payload):
        return {"stated_outcome": "car_1", "summary_he": "לפי הנתונים הזמינים והעדיפויות שהגדרת, Audi Q3 מתאים יותר. זה הכל."}

    writer = FakeSummaryWriter(output_fn=contrarian)
    out = run([AUDI_Q3, HYUNDAI_TUCSON], build_fake_deps(writer=writer))["data"]
    assert out["recommendation"]["outcome"] == "car_2"
    assert out["summary_source"] == "deterministic_fallback"
    assert writer.calls == 1  # no repair call
    assert "Hyundai Tucson Hybrid" in out["summary"]


def test_summary_payload_is_the_immutable_composed_result():
    writer = FakeSummaryWriter()
    out = run([AUDI_Q3, HYUNDAI_TUCSON], build_fake_deps(writer=writer),
              profile={"mode": "personalized", "main_use": "family", "priorities": {**PRI, "safety": 4}})["data"]
    payload = writer.payloads[0]
    assert payload["outcome"] == out["recommendation"]["outcome"]
    assert payload["recommended_name"] == "Hyundai Tucson Hybrid"
    assert payload["reasons_for"] == out["recommendation"]["reasons_he"]["for"]
    assert "בטיחות: קריטי" in payload["buyer_profile"]["priorities"]
    text = json.dumps(payload, ensure_ascii=False)
    assert "http" not in text and "probabilities" not in text and "confidence" not in text


def test_summary_with_matching_label_but_wrong_car_named_is_rejected():
    def sneaky(payload):
        return {"stated_outcome": payload["outcome"],
                "summary_he": "לפי הנתונים הזמינים והעדיפויות שהגדרת, Audi Q3 מתאים יותר. ההכרעה מבוססת על הנתונים הזמינים."}

    out = run([AUDI_Q3, HYUNDAI_TUCSON], build_fake_deps(writer=FakeSummaryWriter(output_fn=sneaky)))["data"]
    assert out["summary_source"] == "deterministic_fallback"


@pytest.mark.parametrize("text", [
    "לפי הנתונים הזמינים והעדיפויות שהגדרת, Hyundai Tucson Hybrid מתאים יותר. ללא ספק זו הבחירה.",
    "לפי הנתונים הזמינים והעדיפויות שהגדרת, Hyundai Tucson Hybrid מתאים יותר. הוא מקבל ציון גבוה.",
    "לפי הנתונים הזמינים והעדיפויות שהגדרת, Hyundai Tucson Hybrid מתאים יותר. הצריכה שלו 4.1 ליטר.",
    "לפי הנתונים הזמינים והעדיפויות שהגדרת, Hyundai Tucson Hybrid מתאים יותר. ההמלצה נכונה ב-95% מהמקרים.",
    "משפט אחד בלבד.",
])
def test_summary_guardrails_reject(text):
    out = run([AUDI_Q3, HYUNDAI_TUCSON], build_fake_deps(
        writer=FakeSummaryWriter(output_fn=lambda p: {"stated_outcome": p["outcome"], "summary_he": text})))["data"]
    assert out["summary_source"] == "deterministic_fallback"


def test_valid_summary_accepted():
    out = run([AUDI_Q3, HYUNDAI_TUCSON], build_fake_deps())["data"]
    assert out["summary_source"] == "gemini"
    assert out["summary"].startswith("לפי הנתונים הזמינים והעדיפויות שהגדרת")


def test_summary_failure_does_not_fail_request():
    out = run([AUDI_Q3, HYUNDAI_TUCSON], build_fake_deps(writer=FakeSummaryWriter(error="SUMMARY_ERROR:Timeout")))
    assert out["type"] == "result"
    assert out["data"]["summary_source"] == "deterministic_fallback"
    assert out["data"]["summary"]


def test_validate_summary_unit():
    payload = {"outcome": "tie", "recommended_name": None, "cars": {}, "categories": []}
    cars = {"car_1": {"display_name": "A"}, "car_2": {"display_name": "B"}}
    assert validate_summary({"stated_outcome": "tie", "summary_he": "אין כרגע יתרון משמעותי. הנתונים מאוזנים."}, payload, cars)
    assert not validate_summary({"stated_outcome": "tie", "summary_he": "A מתאים יותר. הנתונים מאוזנים."}, payload, cars)


# --------------------------------------------------------------------------
# enrichment / cost behaviour
# --------------------------------------------------------------------------
def test_cache_miss_two_cars_is_four_remote_calls():
    provider = FakeEnrichmentProvider()
    session = FakeTypeSafeSession()
    writer = FakeSummaryWriter()
    out = run([AUDI_Q3, HYUNDAI_TUCSON], build_fake_deps(provider=provider, session=session, writer=writer))["data"]
    assert len(provider.calls) == 2
    assert {c["vehicle_id"] for c in provider.calls} == {AUDI_Q3, HYUNDAI_TUCSON}  # one call per car, each its own
    assert len(session.post_calls) == 1 and writer.calls == 1
    meta = out["provider_meta"]
    assert (meta["remote_enrichment_calls"], meta["jev_calls"], meta["summary_calls"]) == (2, 1, 1)


def test_per_car_cache_reused_across_different_pairs():
    provider = FakeEnrichmentProvider()
    deps = build_fake_deps(provider=provider)
    run([AUDI_Q3, HYUNDAI_TUCSON], deps)
    reset_model_verification_cache()
    out = run([AUDI_Q3, BMW_I4], deps)["data"]
    assert [c["vehicle_id"] for c in provider.calls] == [AUDI_Q3, HYUNDAI_TUCSON, BMW_I4] or \
        sorted(c["vehicle_id"] for c in provider.calls[:2]) == sorted([AUDI_Q3, HYUNDAI_TUCSON]) and provider.calls[2]["vehicle_id"] == BMW_I4
    assert out["provider_meta"]["remote_enrichment_calls"] == 1
    assert out["vehicle_snapshots"]["car_1"]["official_enrichment"]["status"] == "cache_hit"


def test_enrichment_failure_for_one_car_continues_with_level15():
    provider = FakeEnrichmentProvider(fail_for={HYUNDAI_TUCSON})
    out = run([AUDI_Q3, HYUNDAI_TUCSON], build_fake_deps(provider=provider))
    data = out["data"]
    assert out["type"] == "result"
    assert data["vehicle_snapshots"]["car_2"]["official_enrichment"]["status"] == "failed"
    assert data["vehicle_snapshots"]["car_2"]["government"]["facts"]["horsepower"] == 230
    assert len(provider.calls) == 2  # no retry


def test_stale_price_refreshes_only_price_group():
    provider = FakeEnrichmentProvider()
    cache = InProcessEnrichmentCache()
    now = [datetime(2026, 10, 1, tzinfo=timezone.utc)]
    repo = LiveOfficialEnrichmentRepository(provider, cache, clock=lambda: now[0])
    from app.services.comparison_v2.demo_catalog import DemoVehicleCatalogRepository
    from app.services.comparison_v2.level15 import build_level15_snapshot

    snap = build_level15_snapshot(DemoVehicleCatalogRepository().get_variant(HYUNDAI_TUCSON), "car_1")
    first = repo.get_or_enrich(snap)
    assert "official_price_ils" in first["facts"] and "torque_nm" in first["facts"]
    now[0] = now[0] + timedelta(hours=30)
    second = repo.get_or_enrich(snap)
    assert provider.calls[1]["groups"] == ("price",)
    assert second["status"] == "refreshed"
    assert "torque_nm" in second["facts"]  # technical data kept from cache
    now[0] = now[0] + timedelta(hours=1)
    repo.get_or_enrich(snap)
    assert len(provider.calls) == 2  # fresh again -> no call


def test_enrichment_prompt_is_single_vehicle_and_lists_registry_domains():
    from app.services.comparison_v2.demo_catalog import DemoVehicleCatalogRepository
    from app.services.comparison_v2.level15 import build_level15_snapshot

    snap = build_level15_snapshot(DemoVehicleCatalogRepository().get_variant(HYUNDAI_TUCSON), "car_1")
    prompt = build_official_enrichment_prompt(snap)
    assert "TUCSON HYBRID" in prompt and "JADD1" in prompt and "hyundaimotors.co.il" in prompt
    assert "Q3" not in prompt and "bmw" not in prompt.lower()
    assert "never judge which car is better" in prompt
    assert "Never infer" in prompt
    assert "untrusted DATA" in prompt
    fields = ENRICHMENT_RESPONSE_SCHEMA["properties"]["claims"]["items"]["properties"]
    assert fields["variant_scope"]["enum"] == ["variant", "model_generic"]


def test_legacy_single_pass_prompt_not_used_by_v2():
    import inspect

    import app.services.comparison_v2.official_enrichment as enr
    import app.services.comparison_v2.pipeline as pipe

    for module in (enr, pipe):
        src = inspect.getsource(module)
        assert "build_single_pass_compare_prompt" not in src
        assert "call_gemini_single_pass_compare" not in src


def test_ev_scenarios_b_and_d():
    b = run([BMW_I4, XPENG_P7I], build_fake_deps())["data"]
    ev = b["categories"]["electric_and_charging"]
    assert ev["evidence_status"] != "not_applicable"
    rng = next(r for r in ev["evidence"]["atomic_results"] if r["metric"] == "electric_range_km")
    assert rng["status"] == "not_comparable" and rng["reason"] == "RANGE_STANDARD_MISMATCH"
    assert "dc_charging_power_kw" in ev["evidence"]["conflicted_metrics"]
    d = run([CADILLAC_ESCALADE_IQ, XPENG_P7I], build_fake_deps())["data"]
    assert d["cars"]["car_1"]["seats"] == 7
    rejected = d["diagnostics"]["car_1"]["rejected_claims"]
    assert any(r["field"] == "official_price_ils" for r in rejected)


def test_offline_mode_makes_zero_remote_calls(monkeypatch):
    monkeypatch.setenv("COMPARISON_V2_OFFLINE_MODE", "true")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    from app.services.comparison_v2 import pipeline as pipe

    deps = pipe.build_default_deps(ai_client=object())
    deps.history = None
    out = run([AUDI_Q3, HYUNDAI_TUCSON], deps)["data"]
    meta = out["provider_meta"]
    assert (meta["remote_enrichment_calls"], meta["jev_calls"], meta["summary_calls"]) == (0, 0, 0)
    assert out["recommendation"]["outcome"] == "decision_unavailable"
    assert out["summary_source"] == "deterministic_fallback"


def test_level2_cache_hit_is_one_jev_and_one_summary():
    provider = FakeEnrichmentProvider()
    session = FakeTypeSafeSession()
    writer = FakeSummaryWriter()
    deps = build_fake_deps(provider=provider, session=session, writer=writer)
    run([AUDI_Q3, HYUNDAI_TUCSON], deps)
    reset_model_verification_cache()
    out = run([AUDI_Q3, HYUNDAI_TUCSON], deps)["data"]
    meta = out["provider_meta"]
    assert (meta["remote_enrichment_calls"], meta["jev_calls"], meta["summary_calls"]) == (0, 1, 1)
    assert len(provider.calls) == 2 and len(session.post_calls) == 2 and writer.calls == 2


def test_prompt_carries_only_this_brands_seed_urls_and_rules():
    from app.services.comparison_v2.demo_catalog import DemoVehicleCatalogRepository
    from app.services.comparison_v2.level15 import build_level15_snapshot

    repo = DemoVehicleCatalogRepository()
    tucson = build_official_enrichment_prompt(build_level15_snapshot(repo.get_variant(HYUNDAI_TUCSON), "car_1"))
    assert "https://campaigns.hyundaimotors.co.il/catalogue/tucson_hybrid_catalogue.pdf" in tucson
    assert "audi.co" not in tucson and "audi.com" not in tucson and "cadillac" not in tucson.lower()
    assert "not proof" in tucson
    assert "ONLY inside the allowed domains" in tucson
    sienna = build_official_enrichment_prompt(build_level15_snapshot(repo.get_variant(TOYOTA_SIEENA), "car_1"))
    assert "2023_Fleet_Guide.pdf" in sienna and "Do not report an Israeli price" in sienna


def test_seed_url_is_not_proof_of_variant():
    from app.services.comparison_v2.demo_catalog import DemoVehicleCatalogRepository
    from app.services.comparison_v2.field_validator import FieldValidator
    from app.services.comparison_v2.level15 import build_level15_snapshot

    snap = build_level15_snapshot(DemoVehicleCatalogRepository().get_variant(HYUNDAI_TUCSON), "car_1")
    seed = "https://www.hyundaimotors.co.il/models/tucson-hybrid"
    claim = {"field": "torque_nm", "value": 350, "unit": "Nm", "source_url": seed, "source_market": "IL",
             "variant_scope": "variant", "identity_evidence": {"model": "Tucson Hybrid"}}
    out = FieldValidator().validate(snap, {"claims": [claim]}, [{"domain": "hyundaimotors.co.il"}])
    assert out["facts"] == {} and out["rejected_claims"][0]["reason"] == "VARIANT_SCOPE_AMBIGUOUS"
    # an allowed seed host that Search never returned is still rejected
    full = dict(claim, identity_evidence={"model_code": "JADD1"})
    out = FieldValidator().validate(snap, {"claims": [full]}, [])
    assert out["rejected_claims"][0]["reason"] == "SOURCE_NOT_GROUNDED"


def test_sienna_israeli_price_from_toyota_usa_rejected():
    from app.services.comparison_v2.demo_catalog import DemoVehicleCatalogRepository
    from app.services.comparison_v2.field_validator import FieldValidator
    from app.services.comparison_v2.level15 import build_level15_snapshot

    snap = build_level15_snapshot(DemoVehicleCatalogRepository().get_variant(TOYOTA_SIEENA), "car_1")
    claim = {"field": "official_price_ils", "value": 250000, "unit": "ILS", "source_url": "https://www.toyota.com/sienna",
             "source_market": "IL", "variant_scope": "variant", "identity_evidence": {"model_code": "AXLH40L-PPXEHA"}}
    out = FieldValidator().validate(snap, {"claims": [claim]}, [{"domain": "toyota.com"}])
    assert out["facts"] == {} and out["rejected_claims"][0]["reason"] == "ISRAELI_OFFICIAL_SOURCE_REQUIRED"
