# -*- coding: utf-8 -*-
"""JEV client, summary writer and pipeline behaviour (all offline)."""

import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.services.comparison_v2.cache import InProcessEnrichmentCache
from app.services.comparison_v2.jev_client import (
    TypeSafeJevClient,
    build_jev_request,
    evaluate_with_jev,
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


def run(keys, deps, profile=None):
    data = {"cars": [{"variant_identity_key": k} for k in keys]}
    return collect_result(run_comparison_v2(data, deps, buyer_profile=profile))


def flip(choice):
    return {"car_1": "car_2", "car_2": "car_1"}.get(choice, choice)


# --------------------------------------------------------------------------
# JEV request contract
# --------------------------------------------------------------------------
def test_one_jev_call_with_all_categories_and_real_choice_options():
    session = FakeTypeSafeSession()
    deps = build_fake_deps(session=session)
    out = run([AUDI_Q3, HYUNDAI_TUCSON], deps)
    assert out["type"] == "result"
    assert len(session.post_calls) == 1
    call = session.post_calls[0]
    assert call["url"] == "https://jev.test/v1/systemone"
    assert call["headers"]["Authorization"] == "Bearer ts-test-secret-key"
    body = call["json"]
    assert body["model"] == "jev-1.13.0"
    assert set(body["state"]) == {"car_1", "car_2", "deterministic_evidence", "coverage", "buyer_profile"}
    applicable = [k for k, c in out["data"]["categories"].items() if c["status"] != "not_applicable"]
    # every applicable category (even one with no comparable metric) is a JEV question
    assert set(body["questions"]) == set(applicable) | {"overall"}
    assert "official_price_and_warranty" in body["questions"]
    for k in applicable:
        assert out["data"]["categories"][k]["decision"]["decision_source"] == "jev"
    assert "electric_and_charging" not in body["questions"]  # not applicable to ICE/hybrid pair
    for q in body["questions"].values():
        assert q["type"] == "choice"
        assert list(q["criteria"]) == ["car_1", "car_2", "tie", "insufficient_evidence"]
        assert q["criteria"]["insufficient_evidence"] == "Available evidence is insufficient"
        assert q["criteria"]["tie"] == "Differences are balanced or not meaningful"


def test_three_cars_still_one_call_with_car_3_choice():
    session = FakeTypeSafeSession()
    deps = build_fake_deps(session=session)
    out = run([AUDI_Q3, HYUNDAI_TUCSON, BMW_I4], deps)
    assert len(session.post_calls) == 1
    for q in session.post_calls[0]["json"]["questions"].values():
        assert list(q["criteria"]) == ["car_1", "car_2", "car_3", "tie", "insufficient_evidence"]
    assert out["data"]["provider_meta"]["jev_calls"] == 1


def test_state_contains_only_validated_data():
    session = FakeTypeSafeSession()
    run([AUDI_Q3, HYUNDAI_TUCSON], build_fake_deps(session=session))
    body = session.post_calls[0]["json"]
    text = json.dumps(body, ensure_ascii=False)
    # rejected / model-generic / third-party values never reach JEV
    assert "carzone" not in text
    assert "evil.com" not in text
    assert "299000" not in text  # foreign price claim
    assert "193" not in json.dumps(body["state"]["car_2"]["official_level_2"])  # 2WD top-speed claim
    assert "1650" not in text  # model_generic height
    assert "Bose" not in text  # extra equipment is never weighted
    # no URLs, page titles, HTML or prose
    assert "http" not in text and "<" not in text
    assert "source_url" not in text and "source_title" not in text
    assert "ts-test-secret-key" not in text
    car2_official = body["state"]["car_2"]["official_level_2"]["facts"]
    assert car2_official["torque_nm"]["value"] == 350


def test_buyer_profile_passed_to_jev():
    session = FakeTypeSafeSession()
    run([AUDI_Q3, HYUNDAI_TUCSON], build_fake_deps(session=session),
        profile={"family_size": "זוג + 3", "priority_weights": {"safety": 10}})
    body = session.post_calls[0]["json"]
    assert body["state"]["buyer_profile"]["family_size"] == "זוג + 3"
    assert "safety" in body["state"]["buyer_profile"]["emphasis"]
    assert "preferences" in body["questions"]["overall"]["instructions"]


def test_ab_swap_flips_decisions():
    ab = run([AUDI_Q3, HYUNDAI_TUCSON], build_fake_deps())["data"]
    ba = run([HYUNDAI_TUCSON, AUDI_Q3], build_fake_deps())["data"]
    assert ab["overall"]["choice"] == "car_2"
    assert ba["overall"]["choice"] == flip(ab["overall"]["choice"])
    for cat in ab["categories"]:
        assert ba["categories"][cat]["decision"]["choice"] == flip(ab["categories"][cat]["decision"]["choice"]), cat


def test_missing_data_gives_no_advantage_to_other_side():
    # Toyota's official claim is rejected -> no Level 2 at all for car_1.
    out = run([TOYOTA_SIEENA, HYUNDAI_TUCSON], build_fake_deps())["data"]
    perf = out["categories"]["performance"]["evidence"]["atomic_results"]
    torque = next(r for r in perf if r["metric"] == "torque_nm")
    assert torque["status"] == "insufficient_data" and torque["leader"] is None
    eff = out["categories"]["efficiency"]
    assert eff["evidence"]["status"] == "no_comparable_evidence"
    assert eff["decision"]["choice"] == "insufficient_evidence"  # JEV's answer, not a local fallback
    assert eff["decision"]["decision_source"] == "jev"
    # Tucson's extra Level 2 coverage does not turn into wins
    assert out["coverage"]["car_2"]["official"]["present"] > out["coverage"]["car_1"]["official"]["present"]
    for cat in out["categories"].values():
        for r in cat["evidence"]["atomic_results"]:
            if "car_1" in r["missing"]:
                assert r["leader"] in (None,), r["metric"]


def test_insufficient_evidence_from_jev_is_preserved():
    override = {"overall": {"type": "choice", "choice": "insufficient_evidence", "confidence": 0.71,
                            "probabilities": {"car_1": 0.1, "car_2": 0.15, "tie": 0.04, "insufficient_evidence": 0.71}}}
    session = FakeTypeSafeSession(answers_override=override)
    writer = FakeSummaryWriter()
    out = run([AUDI_Q3, HYUNDAI_TUCSON], build_fake_deps(session=session, writer=writer))["data"]
    assert out["overall"]["choice"] == "insufficient_evidence"
    assert out["overall"]["confidence"] == 0.71
    assert writer.payloads[0]["overall"]["choice"] == "insufficient_evidence"
    assert "יתרון כולל" not in out["summary"] or "אין" in out["summary"]


def test_probabilities_confidence_model_and_usage_stored_verbatim():
    probs = {"car_1": 0.08, "car_2": 0.86, "tie": 0.04, "insufficient_evidence": 0.02}
    override = {"overall": {"type": "choice", "choice": "car_2", "confidence": 0.86, "probabilities": probs},
                "safety": {"type": "choice", "choice": "car_2", "confidence": 0.94,
                           "probabilities": {"car_1": 0.01, "car_2": 0.94, "tie": 0.03, "insufficient_evidence": 0.02}}}
    session = FakeTypeSafeSession(answers_override=override, response_model="jev-1.13.0-20260915")
    out = run([AUDI_Q3, HYUNDAI_TUCSON], build_fake_deps(session=session, jev_model="jev-latest"))["data"]
    assert out["overall"]["choice"] == "car_2"
    assert out["overall"]["confidence"] == 0.86
    assert out["overall"]["probabilities"] == probs
    assert out["categories"]["safety"]["decision"]["confidence"] == 0.94
    assert out["decision"]["requested_model"] == "jev-latest"
    assert out["decision"]["response_model"] == "jev-1.13.0-20260915"
    assert out["decision"]["usage"] == {"input_tokens": 1234, "output_tokens": 0}
    # confidence is never turned into a car score
    assert "score" not in json.dumps(out["overall"])


def test_confidence_not_recomputed_from_probabilities():
    override = {"overall": {"type": "choice", "choice": "car_1", "confidence": 0.5,
                            "probabilities": {"car_1": 0.9, "car_2": 0.05, "tie": 0.03, "insufficient_evidence": 0.02}}}
    out = run([AUDI_Q3, HYUNDAI_TUCSON], build_fake_deps(session=FakeTypeSafeSession(answers_override=override)))["data"]
    assert out["overall"]["confidence"] == 0.5


def test_jev_choice_outside_vocabulary_becomes_unavailable_not_a_winner():
    override = {"overall": {"type": "choice", "choice": "car_9", "confidence": 0.99, "probabilities": {}}}
    out = run([AUDI_Q3, HYUNDAI_TUCSON], build_fake_deps(session=FakeTypeSafeSession(answers_override=override)))["data"]
    assert out["overall"]["choice"] == "decision_unavailable"


def test_jev_confidence_does_not_change_facts():
    low = {"overall": {"type": "choice", "choice": "car_2", "confidence": 0.31, "probabilities": {}}}
    high = {"overall": {"type": "choice", "choice": "car_2", "confidence": 0.97, "probabilities": {}}}
    a = run([AUDI_Q3, HYUNDAI_TUCSON], build_fake_deps(session=FakeTypeSafeSession(answers_override=low)))["data"]
    b = run([AUDI_Q3, HYUNDAI_TUCSON], build_fake_deps(session=FakeTypeSafeSession(answers_override=high)))["data"]
    assert _no_time(a["vehicle_snapshots"]) == _no_time(b["vehicle_snapshots"])
    for cat in a["categories"]:
        assert _no_time(a["categories"][cat]["evidence"]) == _no_time(b["categories"][cat]["evidence"])


def _no_time(obj):
    if isinstance(obj, dict):
        return {k: _no_time(v) for k, v in obj.items() if k != "observed_at"}
    if isinstance(obj, list):
        return [_no_time(v) for v in obj]
    return obj


def test_jev_failure_keeps_deterministic_facts_and_never_asks_gemini_for_a_winner():
    writer = FakeSummaryWriter()
    out = run([AUDI_Q3, HYUNDAI_TUCSON], build_fake_deps(session=FakeTypeSafeSession(fail_systemone=True), writer=writer))
    data = out["data"]
    assert out["type"] == "result"
    assert data["overall"]["choice"] == "decision_unavailable"
    assert writer.calls == 0
    assert data["summary_source"] == "deterministic_fallback"
    safety = data["categories"]["safety"]
    assert safety["status"] == "decision_unavailable"
    assert any(r["status"] == "compared" for r in safety["evidence"]["atomic_results"])


def test_unverified_jev_model_is_never_used():
    session = FakeTypeSafeSession(models={"data": [{"id": "jev-1.13.0"}]})
    out = run([AUDI_Q3, HYUNDAI_TUCSON], build_fake_deps(session=session, jev_model="jev-latest"))["data"]
    assert session.post_calls == []
    assert out["overall"]["choice"] == "decision_unavailable"
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
        return {"stated_overall_choice": "car_1", "summary_he": "לפי הנתונים הזמינים כרגע, ל־Audi Q3 יש יתרון כולל. זה הכל."}

    writer = FakeSummaryWriter(output_fn=contrarian)
    out = run([AUDI_Q3, HYUNDAI_TUCSON], build_fake_deps(writer=writer))["data"]
    assert out["overall"]["choice"] == "car_2"
    assert out["summary_source"] == "deterministic_fallback"
    assert writer.calls == 1  # no repair call
    assert "Hyundai Tucson Hybrid" in out["summary"]


def test_summary_with_matching_label_but_wrong_car_named_is_rejected():
    def sneaky(payload):
        return {"stated_overall_choice": payload["overall"]["choice"],
                "summary_he": "לפי הנתונים הזמינים כרגע, ל־Audi Q3 יש יתרון כולל. ההכרעה מבוססת על הנתונים הזמינים."}

    out = run([AUDI_Q3, HYUNDAI_TUCSON], build_fake_deps(writer=FakeSummaryWriter(output_fn=sneaky)))["data"]
    assert out["summary_source"] == "deterministic_fallback"


@pytest.mark.parametrize("text", [
    "לפי הנתונים הזמינים כרגע, ל־Hyundai Tucson Hybrid יש יתרון כולל. ללא ספק זו הבחירה.",
    "לפי הנתונים הזמינים כרגע, ל־Hyundai Tucson Hybrid יש יתרון כולל. הוא מקבל ציון גבוה.",
    "לפי הנתונים הזמינים כרגע, ל־Hyundai Tucson Hybrid יש יתרון כולל. הצריכה שלו 4.1 ליטר.",
    "משפט אחד בלבד.",
])
def test_summary_guardrails_reject(text):
    out = run([AUDI_Q3, HYUNDAI_TUCSON], build_fake_deps(
        writer=FakeSummaryWriter(output_fn=lambda p: {"stated_overall_choice": p["overall"]["choice"], "summary_he": text})))["data"]
    assert out["summary_source"] == "deterministic_fallback"


def test_valid_summary_accepted():
    out = run([AUDI_Q3, HYUNDAI_TUCSON], build_fake_deps())["data"]
    assert out["summary_source"] == "gemini"
    assert out["summary"].startswith("לפי הנתונים הזמינים כרגע")


def test_summary_failure_does_not_fail_request():
    out = run([AUDI_Q3, HYUNDAI_TUCSON], build_fake_deps(writer=FakeSummaryWriter(error="SUMMARY_ERROR:Timeout")))
    assert out["type"] == "result"
    assert out["data"]["summary_source"] == "deterministic_fallback"
    assert out["data"]["summary"]


def test_validate_summary_unit():
    payload = {"overall": {"choice": "tie", "choice_name": None}, "cars": {}, "categories": []}
    cars = {"car_1": {"display_name": "A"}, "car_2": {"display_name": "B"}}
    assert validate_summary({"stated_overall_choice": "tie", "summary_he": "אין כרגע יתרון כולל משמעותי. הנתונים מאוזנים."}, payload, cars)
    assert not validate_summary({"stated_overall_choice": "tie", "summary_he": "ל־A יש יתרון כולל. הנתונים מאוזנים."}, payload, cars)


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
    assert ev["status"] != "not_applicable"
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
    assert out["overall"]["choice"] == "decision_unavailable"
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
