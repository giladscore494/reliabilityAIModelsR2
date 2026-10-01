# -*- coding: utf-8 -*-
"""Regression tests for the Comparison V2 Level 2 run at 3d62dda.

Production (Audi Q3 2024 S LINE / BMW i4 eDrive35 2024 PURE / Mercedes CLE300
4MATIC 2024 AMG PREMIUM):

* Audi: the official Audi technical PDF was opened (URL Context) and grounded
  (``uploads.audi-mediacenter.com``), yet all nine claims were rejected as
  VARIANT_YEAR_MISMATCH: the PDF's publication/revision year (2025) took part
  in variant matching against the Ministry model year (2024).
* BMW / Mercedes: Gemini answered STOP with JSON but ran no Search and opened
  no URL (0 queries / 0 chunks / 0 URL-context retrievals); nothing was
  retried and nothing could be accepted.

Responses are built through google-genai's own deserializer from REST-shaped
payloads; no network calls. The "official page" values are MOCKED fixtures
shaped like production, not verified specifications.
"""

import json
import time
from datetime import datetime, timedelta, timezone

import pytest
from google.genai import types as genai_types

from app.services.comparison_v2.cache import InProcessEnrichmentCache
from app.services.comparison_v2.demo_catalog import DemoVehicleCatalogRepository
from app.services.comparison_v2.field_registry import FIELD_SPECS
from app.services.comparison_v2.field_validator import FieldValidator
from app.services.comparison_v2.level15 import build_level15_snapshot
from app.services.comparison_v2.official_enrichment import (
    ENRICHMENT_RESPONSE_SCHEMA,
    GeminiOfficialEnrichmentProvider,
    LiveOfficialEnrichmentRepository,
    build_official_enrichment_prompt,
    enrich_many,
    enrichment_report,
    research_signals,
)
from app.services.comparison_v2.official_variant_matcher import explicit_model_years, match_variant
from app.services.comparison_v2.pipeline import collect_result, run_comparison_v2

from comparison_v2_fakes import AUDI_Q3, BMW_I4, MERCEDES_CLE, build_fake_deps

REPO = DemoVehicleCatalogRepository()
REDIRECT = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/"

AUDI_PDF = ("https://uploads.audi-mediacenter.com/system/production/car_motorizations/1244/file_en/"
            "ceabc2b0fd48a749c10ea1fd2c8552b20f96e0c7/eTD-Audi-Q3-40-TFSI-quattro-S_tronic-140kW_250108.pdf")
AUDI_IL_PRICE = "https://www.audi.co.il/models/q3/prices"
BMW_TECH = "https://www.bmw.co.il/he/all-models/i-series/i4/i4-gran-coupe-2024-g26bev-technical-data.html"
BMW_PRICE = "https://www.bmw.co.il/he/all-models/i-series/i4/pricelist.html"
CLE_PAGE = "https://www.mercedes-benz.co.il/models/cle-coupe/"

# What the official sources show next to the values (no Israeli trim on the
# global Audi PDF / the technical pages).
AUDI_PDF_EV = {"model": "Audi Q3", "trim": None, "powertrain": "40 TFSI quattro S tronic, 140 kW (190 PS)",
               "drivetrain": "quattro", "model_code": None}
BMW_TECH_EV = {"model": "BMW i4 eDrive35", "trim": None, "powertrain": "electric motor 210 kW (286 hp)",
               "drivetrain": "rear-wheel drive", "model_code": None, "body": "Gran Coupé", "generation": "G26"}
CLE_EV = {"model": "CLE 300 4MATIC Coupé", "trim": None,
          "powertrain": "2.0L petrol 190 kW (258 hp) + 48V mild hybrid ISG", "drivetrain": "4MATIC", "body": "Coupé"}


def snap(key, slot="car_1"):
    return build_level15_snapshot(REPO.get_variant(key), slot)


def claim(field, value, unit, url, evidence, *, market="GLOBAL", publication=None, model_year=None, scope="variant", **extra):
    return {"field": field, "value": value, "unit": unit, "source_url": url, "source_title": "official", "source_market": market,
            "source_publication_year": publication, "vehicle_model_year": model_year, "variant_scope": scope,
            "identity_evidence": evidence, **extra}


def chunk(title, i=0):
    """Gemini Developer API grounding chunk: redirect URI + title, no domain."""
    return {"uri": f"{REDIRECT}C{i}", "title": title}


def sdk_response(payload, *, chunks=(), queries=(), url_metadata=None, finish="STOP"):
    cand = {"content": {"role": "model", "parts": [{"text": json.dumps(payload)}]}, "finishReason": finish}
    if chunks or queries:
        cand["groundingMetadata"] = {"webSearchQueries": list(queries), "groundingChunks": [{"web": c} for c in chunks]}
    if url_metadata:
        cand["urlContextMetadata"] = {"urlMetadata": url_metadata}
    rest = {"candidates": [cand], "modelVersion": "gemini-3.1-pro-preview",
            "usageMetadata": {"promptTokenCount": 2000, "candidatesTokenCount": 500, "totalTokenCount": 2500}}
    return genai_types.GenerateContentResponse._from_response(
        response=rest, kwargs={"config": {"response_json_schema": ENRICHMENT_RESPONSE_SCHEMA}}
    )


def url_ok(url):
    return {"retrievedUrl": url, "urlRetrievalStatus": "URL_RETRIEVAL_STATUS_SUCCESS"}


def no_research(fields=("torque_nm",)):
    """Production BMW/Mercedes shape: STOP, valid JSON, zero Google evidence."""
    return sdk_response({"claims": [], "not_found_fields": list(fields)})


class FakeModels:
    def __init__(self, responder):
        self.responder = responder
        self.calls = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        return self.responder(contents, config, len(self.calls))


class FakeClient:
    def __init__(self, responder):
        self.models = FakeModels(responder)


def gemini(responder, **kwargs):
    kwargs.setdefault("resolve_redirects", False)
    return GeminiOfficialEnrichmentProvider(FakeClient(responder), "gemini-3.1-pro-preview", 125, **kwargs)


def is_commercial(prompt):
    return "Israeli official commercial terms only" in prompt


def is_retry(prompt):
    return "RESEARCH REQUIRED (retry)" in prompt


def validate(key, claims, sources):
    return FieldValidator().validate(snap(key), {"claims": claims}, sources)


# ===========================================================================
# Year semantics (tests 1-3)
# ===========================================================================
def test_1_publication_year_never_causes_year_mismatch():
    """Audi PDF revised in 2025, no stated model year -> powertrain facts validate."""
    claims = [claim(f, v, u, AUDI_PDF, AUDI_PDF_EV, publication=2025) for f, v, u in (
        ("torque_nm", 320, "Nm"), ("acceleration_0_100_s", 7.4, "s"), ("top_speed_kmh", 222, "km/h"))]
    out = validate(AUDI_Q3, claims, [chunk("audi-mediacenter.com")])
    assert "VARIANT_YEAR_MISMATCH" not in {r["reason"] for r in out["rejected_claims"]}
    assert set(out["facts"]) == {"torque_nm", "acceleration_0_100_s", "top_speed_kmh"}
    fact = out["facts"]["torque_nm"]
    assert fact["identity_match"] == "powertrain" and fact["identity_scope"] == "powertrain"
    assert fact["source_publication_year"] == 2025 and fact["vehicle_model_year"] is None
    assert "source_year" not in fact


def test_1b_legacy_source_year_and_dates_in_evidence_are_provenance_only():
    """The old ambiguous ``source_year`` and bare years in titles/file names
    never take part in matching."""
    legacy = dict(claim("torque_nm", 320, "Nm", AUDI_PDF, dict(AUDI_PDF_EV, powertrain=AUDI_PDF_EV["powertrain"] + " eTD 2025 (c) 2025")),
                  source_year=2025)
    legacy.pop("source_publication_year")
    out = validate(AUDI_Q3, [legacy], [chunk("audi-mediacenter.com")])
    assert out["facts"]["torque_nm"]["source_publication_year"] == 2025
    assert explicit_model_years("Audi Q3 2025 technical data, eTD-Audi-Q3_250108.pdf, (c) 2025") == []
    assert explicit_model_years("Model Year 2026") == [2026] and explicit_model_years("MY25 Q3") == [2025]
    assert explicit_model_years("שנת דגם 2024") == [2024] and explicit_model_years("Modelljahr 2026") == [2026]


def test_2_explicit_different_vehicle_model_year_is_rejected(caplog):
    out = validate(AUDI_Q3, [claim("torque_nm", 320, "Nm", AUDI_PDF, AUDI_PDF_EV, publication=2025, model_year=2026)],
                   [chunk("audi-mediacenter.com")])
    assert out["facts"] == {}
    rej = out["rejected_claims"][0]
    assert rej["reason"] == "VARIANT_YEAR_MISMATCH"
    assert rej["variant_match"]["years"] == {"government_model_year": 2024, "claimed_vehicle_model_year": 2026,
                                             "source_publication_year": 2025, "stated_model_years": [2026]}
    # stated in the evidence text instead of the claim field: same result
    in_text = claim("torque_nm", 320, "Nm", AUDI_PDF, dict(AUDI_PDF_EV, model="Audi Q3 Model Year 2026"))
    assert validate(AUDI_Q3, [in_text], [chunk("audi-mediacenter.com")])["rejected_claims"][0]["reason"] == "VARIANT_YEAR_MISMATCH"


def test_2b_year_mismatch_logs_the_three_years(caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="comparison_v2"):
        validate(AUDI_Q3, [claim("torque_nm", 320, "Nm", AUDI_PDF, AUDI_PDF_EV, publication=2025, model_year=2026)],
                 [chunk("audi-mediacenter.com")])
    line = next(r.getMessage() for r in caplog.records if "official_claim_rejected" in r.getMessage())
    assert "government_model_year=2024" in line and "claimed_vehicle_model_year=2026" in line
    assert "source_publication_year=2025" in line


def test_3_publication_2026_with_matching_vehicle_model_year_2024_is_accepted():
    out = validate(AUDI_Q3, [claim("torque_nm", 320, "Nm", AUDI_PDF, AUDI_PDF_EV, publication=2026, model_year=2024)],
                   [chunk("audi-mediacenter.com")])
    fact = out["facts"]["torque_nm"]
    assert fact["vehicle_model_year"] == 2024 and fact["source_publication_year"] == 2026


# ===========================================================================
# Identity scopes (tests 4-9)
# ===========================================================================
def test_4_torque_without_israeli_trim_accepted_at_powertrain_scope():
    ev = {"model": "Q3", "powertrain": "2.0 TFSI 190 hp", "drivetrain": "quattro"}
    out = validate(AUDI_Q3, [claim("torque_nm", 320, "Nm", AUDI_PDF, ev)], [chunk("audi-mediacenter.com")])
    assert out["facts"]["torque_nm"]["identity_match"] == "powertrain"


@pytest.mark.parametrize("evidence,reason", [
    ({"model": "Q3", "powertrain": "35 TFSI 1.5 petrol 150 hp", "drivetrain": "front-wheel drive"}, "VARIANT_DRIVETRAIN_MISMATCH"),
    ({"model": "Q3", "powertrain": "35 TFSI 150 hp", "drivetrain": "quattro"}, "VARIANT_ENGINE_MISMATCH"),
])
def test_5_different_audi_powertrain_rejected(evidence, reason):
    out = validate(AUDI_Q3, [claim("torque_nm", 250, "Nm", AUDI_PDF, evidence)], [chunk("audi-mediacenter.com")])
    assert out["facts"] == {} and out["rejected_claims"][0]["reason"] == reason


def test_6_bmw_i4_edrive35_powertrain_fields_accepted_without_pure():
    fields = (("torque_nm", 400, "Nm"), ("acceleration_0_100_s", 6.0, "s"), ("battery_capacity_net_kwh", 67.1, "kWh"),
              ("dc_charging_power_kw", 180, "kW"), ("ac_charging_power_kw", 11, "kW"))
    out = validate(BMW_I4, [claim(f, v, u, BMW_TECH, BMW_TECH_EV, market="IL") for f, v, u in fields], [chunk("bmw.co.il")])
    assert set(out["facts"]) == {f for f, _, _ in fields}
    assert {f["identity_match"] for f in out["facts"].values()} == {"powertrain"}


def test_7_bmw_i4_m50_rejected_for_edrive35():
    ev = {"model": "BMW i4 M50", "powertrain": "electric dual motor 400 kW (544 hp)", "drivetrain": "xDrive AWD"}
    out = validate(BMW_I4, [claim("torque_nm", 795, "Nm", BMW_TECH, ev, market="IL")], [chunk("bmw.co.il")])
    assert out["facts"] == {}
    assert out["rejected_claims"][0]["reason"] in ("VARIANT_MODEL_MISMATCH", "VARIANT_DRIVETRAIN_MISMATCH", "VARIANT_ENGINE_MISMATCH")
    match = match_variant(snap(BMW_I4), claim("torque_nm", 795, "Nm", BMW_TECH, ev), "powertrain")
    assert {"model", "drivetrain", "engine"} <= {k for k, v in match["checks"].items() if v is False}


def test_8_mercedes_cle300_4matic_powertrain_fields_accepted_without_amg_premium():
    fields = (("torque_nm", 400, "Nm"), ("top_speed_kmh", 250, "km/h"), ("transmission_gears", 9, "gears"), ("fuel_tank_l", 66, "L"))
    out = validate(MERCEDES_CLE, [claim(f, v, u, CLE_PAGE, CLE_EV, market="IL") for f, v, u in fields], [chunk("mercedes-benz.co.il")])
    assert set(out["facts"]) == {f for f, _, _ in fields}
    assert {f["identity_match"] for f in out["facts"].values()} == {"powertrain"}


@pytest.mark.parametrize("evidence", [
    {"model": "CLE 200 Coupé", "powertrain": "2.0L petrol 150 kW (204 hp) mild hybrid", "drivetrain": "rear-wheel drive"},
    {"model": "CLE 300", "powertrain": "2.0L petrol 258 hp mild hybrid", "drivetrain": "rear-wheel drive"},
    # self-contradictory evidence never establishes the drivetrain
    {"model": "CLE 300 4MATIC", "powertrain": "2.0L petrol 258 hp mild hybrid", "drivetrain": "rear-wheel drive"},
])
def test_9_mercedes_cle200_or_rwd_rejected(evidence):
    out = validate(MERCEDES_CLE, [claim("torque_nm", 320, "Nm", CLE_PAGE, evidence, market="IL")], [chunk("mercedes-benz.co.il")])
    assert out["facts"] == {}
    assert out["rejected_claims"][0]["reason"] in ("VARIANT_MODEL_MISMATCH", "VARIANT_DRIVETRAIN_MISMATCH", "VARIANT_SCOPE_AMBIGUOUS")


# ===========================================================================
# Exact-variant values (tests 10-12)
# ===========================================================================
def test_10_wheel_size_from_generic_q3_page_not_accepted():
    for field, value, unit in (("wheel_size_in", 19, "in"), ("tire_size", "235/50 R19", None)):
        out = validate(AUDI_Q3, [claim(field, value, unit, AUDI_PDF, AUDI_PDF_EV)], [chunk("audi-mediacenter.com")])
        assert out["facts"] == {} and out["rejected_claims"][0]["reason"] == "VARIANT_SCOPE_AMBIGUOUS", field
    # with the Israeli trim + full powertrain on an Israeli page it is exact
    il = dict(AUDI_PDF_EV, trim="S line")
    out = validate(AUDI_Q3, [claim("wheel_size_in", 19, "in", "https://www.audi.co.il/models/q3/specs", il, market="IL")],
                   [chunk("audi.co.il")])
    assert out["facts"]["wheel_size_in"]["identity_match"] == "attributes"


def test_11_israeli_price_page_for_a_different_trim_rejected():
    ev = {"model": "BMW i4 eDrive35", "trim": "M Sport", "powertrain": "electric 286 hp", "drivetrain": "RWD"}
    out = validate(BMW_I4, [claim("official_price_ils", 359000, "ILS", BMW_PRICE, ev, market="IL")], [chunk("bmw.co.il")])
    assert out["facts"] == {} and out["rejected_claims"][0]["reason"] == "VARIANT_TRIM_MISMATCH"
    # no trim at all is not enough for a price either
    out = validate(BMW_I4, [claim("official_price_ils", 339000, "ILS", BMW_PRICE, BMW_TECH_EV, market="IL")], [chunk("bmw.co.il")])
    assert out["facts"] == {} and out["rejected_claims"][0]["reason"] == "VARIANT_SCOPE_AMBIGUOUS"


def test_12_exact_model_code_is_strong_identity():
    ev = {"model": "BMW i4 eDrive35", "trim": "Pure Edition", "model_code": "41AW"}
    out = validate(BMW_I4, [claim("official_price_ils", 339000, "ILS", BMW_PRICE, ev, market="IL")], [chunk("bmw.co.il")])
    assert out["facts"]["official_price_ils"]["identity_match"] == "model_code"
    # a matching code never overrides an explicit configuration contradiction
    bad = dict(ev, drivetrain="xDrive AWD")
    out = validate(BMW_I4, [claim("official_price_ils", 339000, "ILS", BMW_PRICE, bad, market="IL")], [chunk("bmw.co.il")])
    assert out["facts"] == {} and out["rejected_claims"][0]["reason"] == "VARIANT_DRIVETRAIN_MISMATCH"
    # a different code is a mismatch; a chassis code reported as model_code is not
    other = dict(ev, model_code="41AX")
    assert validate(BMW_I4, [claim("official_price_ils", 339000, "ILS", BMW_PRICE, other, market="IL")],
                    [chunk("bmw.co.il")])["rejected_claims"][0]["reason"] == "VARIANT_MODEL_CODE_MISMATCH"
    chassis = dict(BMW_TECH_EV, model_code="G26")
    assert "torque_nm" in validate(BMW_I4, [claim("torque_nm", 400, "Nm", BMW_TECH, chassis, market="IL")], [chunk("bmw.co.il")])["facts"]


# ===========================================================================
# Per-field scope policy
# ===========================================================================
def test_field_scope_policy():
    scopes = {k: s.identity_scope for k, s in FIELD_SPECS.items()}
    for key in ("torque_nm", "acceleration_0_100_s", "top_speed_kmh", "battery_capacity_kwh", "battery_capacity_net_kwh",
                "ac_charging_power_kw", "dc_charging_power_kw", "transmission_type", "transmission_gears", "fuel_tank_l",
                "fuel_consumption_l_100km", "energy_consumption_kwh_100km", "electric_range_km"):
        assert scopes[key] == "powertrain", key
    assert scopes["cargo_volume_l"] == "powertrain_body"
    for key in ("length_mm", "width_mm", "wheelbase_mm"):
        assert scopes[key] == "model_generation", key
    for key in ("height_mm", "ground_clearance_mm", "wheel_size_in", "tire_size", "multimedia_screen_in", "panoramic_roof",
                "premium_audio", "heated_front_seats", "official_price_ils", "registration_fee_ils", "warranty_vehicle_years"):
        assert scopes[key] == "exact_variant" and FIELD_SPECS[key].trim_sensitive, key


def test_cargo_needs_the_body_stated_and_compatible():
    audi = validate(AUDI_Q3, [claim("cargo_volume_l", 530, "L", AUDI_PDF, AUDI_PDF_EV)], [chunk("audi-mediacenter.com")])
    assert audi["facts"] == {} and audi["rejected_claims"][0]["reason"] == "VARIANT_SCOPE_AMBIGUOUS"
    bmw = validate(BMW_I4, [claim("cargo_volume_l", 470, "L", BMW_TECH, BMW_TECH_EV, market="IL")], [chunk("bmw.co.il")])
    assert bmw["facts"]["cargo_volume_l"]["identity_match"] == "powertrain_body"
    seven = dict(BMW_TECH_EV, seating="7 seats")
    out = validate(BMW_I4, [claim("cargo_volume_l", 470, "L", BMW_TECH, seven, market="IL")], [chunk("bmw.co.il")])
    assert out["facts"] == {} and out["rejected_claims"][0]["reason"] == "VARIANT_SEATS_MISMATCH"


def test_dimensions_at_model_generation_scope_but_never_across_bodies():
    # same body generation (code G26) -> length accepted without the trim
    bmw = validate(BMW_I4, [claim("length_mm", 4783, "mm", BMW_TECH, {"model": "BMW i4", "generation": "G26"}, market="IL")],
                   [chunk("bmw.co.il")])
    assert bmw["facts"]["length_mm"]["identity_match"] == "model_generation"
    # a different family is never the same body generation
    out = validate(BMW_I4, [claim("length_mm", 4783, "mm", BMW_TECH, {"model": "BMW i5", "generation": "G26"}, market="IL")],
                   [chunk("bmw.co.il")])
    assert out["facts"] == {} and out["rejected_claims"][0]["reason"] == "VARIANT_MODEL_MISMATCH"
    # model name only: generation not established -> stays missing
    out = validate(BMW_I4, [claim("length_mm", 4783, "mm", BMW_TECH, {"model": "BMW i4"}, market="IL")], [chunk("bmw.co.il")])
    assert out["facts"] == {} and out["rejected_claims"][0]["reason"] == "VARIANT_SCOPE_AMBIGUOUS"
    # exact powertrain is a generation anchor; height stays exact-variant
    cle = validate(MERCEDES_CLE, [claim("length_mm", 4850, "mm", CLE_PAGE, CLE_EV, market="IL"),
                                  claim("height_mm", 1420, "mm", CLE_PAGE, CLE_EV, market="IL")], [chunk("mercedes-benz.co.il")])
    assert cle["facts"]["length_mm"]["identity_match"] == "model_generation" and "height_mm" not in cle["facts"]
    # different body / generation -> rejected
    cabrio = dict(CLE_EV, model="CLE 300 4MATIC Cabriolet", body="Cabriolet")
    out = validate(MERCEDES_CLE, [claim("length_mm", 4850, "mm", CLE_PAGE, cabrio, market="IL")], [chunk("mercedes-benz.co.il")])
    assert out["rejected_claims"][0]["reason"] == "VARIANT_BODY_MISMATCH"
    a236 = dict(CLE_EV, body=None, generation="A236")
    out = validate(MERCEDES_CLE, [claim("length_mm", 4850, "mm", CLE_PAGE, a236, market="IL")], [chunk("mercedes-benz.co.il")])
    assert out["rejected_claims"][0]["reason"] == "VARIANT_GENERATION_MISMATCH"
    sportback = dict(AUDI_PDF_EV, model="Audi Q3 Sportback")
    out = validate(AUDI_Q3, [claim("torque_nm", 320, "Nm", AUDI_PDF, sportback)], [chunk("audi-mediacenter.com")])
    assert out["rejected_claims"][0]["reason"] == "VARIANT_BODY_MISMATCH"


def test_avantgarde_is_a_line_not_an_avant_wagon():
    ev = dict(CLE_EV, trim="Avantgarde")
    match = match_variant(snap(MERCEDES_CLE), claim("torque_nm", 400, "Nm", CLE_PAGE, ev), "powertrain")
    assert match["checks"]["body"] is not False


def test_range_or_option_dependent_values_are_never_picked():
    ev = dict(BMW_TECH_EV)
    claims = [claim("energy_consumption_kwh_100km", 16.1, "kWh/100km", BMW_TECH, ev, market="IL", value_qualifier="one_of_several"),
              claim("electric_range_km", 483, "km", BMW_TECH, ev, market="IL", measurement_standard="WLTP", value_qualifier="one_of_several"),
              claim("acceleration_0_100_s", 6.0, "s", BMW_TECH, ev, market="IL", value_qualifier="approximate")]
    out = validate(BMW_I4, claims, [chunk("bmw.co.il")])
    assert out["facts"] == {} and {r["reason"] for r in out["rejected_claims"]} == {"VALUE_NOT_EXACT"}
    # two different exact values for one configuration -> conflict, never an average
    two = [claim("electric_range_km", v, "km", BMW_TECH, ev, market="IL", measurement_standard="WLTP", value_qualifier="exact")
           for v in (420, 483)]
    out = validate(BMW_I4, two, [chunk("bmw.co.il")])
    assert "electric_range_km" not in out["facts"] and out["conflicts"][0]["field"] == "electric_range_km"
    one = [claim("electric_range_km", 483, "km", BMW_TECH, ev, market="IL", measurement_standard="WLTP", value_qualifier="exact")]
    assert validate(BMW_I4, one, [chunk("bmw.co.il")])["facts"]["electric_range_km"]["identity_match"] == "powertrain"
    # a missing qualifier fails closed for consumption / range
    missing = [claim("electric_range_km", 483, "km", BMW_TECH, ev, market="IL", measurement_standard="WLTP"),
               claim("energy_consumption_kwh_100km", 16.1, "kWh/100km", BMW_TECH, ev, market="IL")]
    out = validate(BMW_I4, missing, [chunk("bmw.co.il")])
    assert out["facts"] == {} and {r["reason"] for r in out["rejected_claims"]} == {"VALUE_NOT_EXACT"}


def test_missing_model_statement_is_not_a_model_contradiction():
    ev = {"powertrain": "40 TFSI 140 kW", "drivetrain": "quattro"}
    match = match_variant(snap(AUDI_Q3), claim("torque_nm", 320, "Nm", AUDI_PDF, ev), "powertrain")
    assert match["checks"]["model"] is None and match["status"] == "VARIANT_SCOPE_AMBIGUOUS"


# ===========================================================================
# Research-required behaviour (tests 13-16)
# ===========================================================================
def _bmw_technical_grounded():
    claims = [claim("torque_nm", 400, "Nm", BMW_TECH, BMW_TECH_EV, market="IL")]
    return sdk_response({"claims": claims}, chunks=[chunk("bmw.co.il")], queries=["site:bmw.co.il i4 eDrive35"],
                        url_metadata=[url_ok(BMW_TECH)])


def test_13_14_no_research_response_gets_one_research_retry_then_validates():
    def responder(prompt, config, n):
        if is_commercial(prompt):
            return sdk_response({"claims": []}, chunks=[chunk("bmw.co.il")], queries=["bmw i4 מחיר"])
        return _bmw_technical_grounded() if is_retry(prompt) else no_research()

    provider = gemini(responder)
    repo = LiveOfficialEnrichmentRepository(provider, InProcessEnrichmentCache())
    outcome, meta = enrich_many(repo, [snap(BMW_I4)])[0]
    technical_calls = [c for c in provider.client.models.calls if not is_commercial(c["contents"])]
    assert len(technical_calls) == 2 and is_retry(technical_calls[1]["contents"]) and not is_retry(technical_calls[0]["contents"])
    retry_prompt = technical_calls[1]["contents"]
    for line in ("Your previous attempt returned without using an official source.",
                 "You MUST perform research before answering.",
                 "Do not return the final JSON until at least one official source has actually been retrieved.",
                 "return claims=[] and list the fields in not_found_fields"):
        assert line in retry_prompt
    # same model, same tools, same schema on the retry
    first_cfg, retry_cfg = technical_calls[0]["config"], technical_calls[1]["config"]
    assert technical_calls[1]["model"] == "gemini-3.1-pro-preview"
    assert [bool(t.google_search) for t in retry_cfg.tools] == [bool(t.google_search) for t in first_cfg.tools]
    assert any(t.url_context is not None for t in retry_cfg.tools)
    assert retry_cfg.response_json_schema == first_cfg.response_json_schema
    # normal validation continues on the retry's own evidence
    assert outcome["facts"]["torque_nm"]["grounding_tier"] == "url"
    task = next(t for t in outcome["tasks"] if t["task_name"] == "technical")
    assert task["attempt_count"] == 2 and task["research_retry"] is True and task["research_performed"] is True
    assert task["attempts"][0]["research_performed"] is False and task["attempts"][1]["url_context_success_count"] == 1
    report = enrichment_report(snap(BMW_I4), outcome, meta)
    assert report["research_retry"] is True and report["task_outcomes"]["technical"]["accepted"] == 1
    assert report["identity_match"] == {"powertrain": 1}


def test_15_retry_still_without_research_is_research_not_performed_and_not_cached():
    provider = gemini(lambda prompt, config, n: no_research())
    cache = InProcessEnrichmentCache()
    repo = LiveOfficialEnrichmentRepository(provider, cache)
    outcome, meta = enrich_many(repo, [snap(MERCEDES_CLE)])[0]
    assert len(provider.client.models.calls) == 4  # 2 tasks x (initial + exactly one retry), never recursive
    for group in ("technical", "price", "warranty"):
        record = outcome["group_freshness"][group]
        assert record["state"] == "research_not_performed" and record["failure_reason"] == "RESEARCH_NOT_PERFORMED"
        assert record["fresh_until"] is None
    assert outcome["cache_write"] == "skipped_no_meaningful_observation"
    assert cache.get(repo.cache_key(snap(MERCEDES_CLE))) is None
    assert set(repo.plan(snap(MERCEDES_CLE))[1]) == {"technical", "price", "warranty"}  # retryable next request
    report = enrichment_report(snap(MERCEDES_CLE), outcome, meta)
    assert report["research_performed"] is False and report["failure_reasons"] == ["RESEARCH_NOT_PERFORMED"]
    assert report["level2_health"] == "failed" and report["attempt_count"] == 4


def test_16_grounded_official_source_with_absent_data_is_bounded_empty():
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)

    def responder(prompt, config, n):
        host = "bmw.co.il"
        return sdk_response({"claims": [], "not_found_fields": ["official_price_ils", "torque_nm"]},
                            chunks=[chunk(host)], queries=["bmw i4 edrive35"], url_metadata=[url_ok(BMW_TECH)])

    provider = gemini(responder)
    repo = LiveOfficialEnrichmentRepository(provider, InProcessEnrichmentCache(), clock=lambda: now)
    outcome, _ = enrich_many(repo, [snap(BMW_I4)])[0]
    assert len(provider.client.models.calls) == 2  # researched: no retry
    tech, price = outcome["group_freshness"]["technical"], outcome["group_freshness"]["price"]
    assert tech["state"] == "empty" and datetime.fromisoformat(tech["fresh_until"]) - now == timedelta(days=3)
    assert price["state"] == "empty" and datetime.fromisoformat(price["fresh_until"]) - now == timedelta(hours=12)
    assert outcome["cache_write"] == "written"
    task = next(t for t in outcome["tasks"] if t["task_name"] == "commercial")
    assert task["not_found_trusted"] is True and task["official_source_inspected"] is True


def test_not_found_fields_without_a_relevant_official_source_never_make_empty():
    # Search ran, but only a GLOBAL page was retrieved: Israeli price/warranty
    # absence is not established.
    def responder(prompt, config, n):
        return sdk_response({"claims": [], "not_found_fields": ["official_price_ils"]},
                            chunks=[chunk("bmw.com")], queries=["bmw i4 price"])

    repo = LiveOfficialEnrichmentRepository(gemini(responder), InProcessEnrichmentCache())
    outcome, _ = enrich_many(repo, [snap(BMW_I4)])[0]
    assert outcome["group_freshness"]["technical"]["state"] == "empty"
    for group in ("price", "warranty"):
        record = outcome["group_freshness"][group]
        assert record["state"] == "grounding_unverifiable" and record["failure_reason"] == "NO_OFFICIAL_SOURCE_INSPECTED"
    task = next(t for t in outcome["tasks"] if t["task_name"] == "commercial")
    assert task["not_found_count"] == 1 and task["not_found_trusted"] is False


def test_search_without_any_retrieved_source_is_retried_but_never_empty():
    def responder(prompt, config, n):
        return sdk_response({"claims": [], "not_found_fields": ["torque_nm"]}, queries=["bmw i4 edrive35 torque"])

    provider = gemini(responder)
    repo = LiveOfficialEnrichmentRepository(provider, InProcessEnrichmentCache())
    outcome, _ = enrich_many(repo, [snap(BMW_I4)])[0]
    assert len(provider.client.models.calls) == 4
    record = outcome["group_freshness"]["technical"]
    assert record["state"] == "grounding_unverifiable" and record["failure_reason"] == "NO_OFFICIAL_SOURCE_INSPECTED"
    assert outcome["cache_write"] == "skipped_no_meaningful_observation"


def test_retry_is_skipped_when_the_deadline_leaves_no_room():
    provider = gemini(lambda prompt, config, n: no_research())
    result = provider.enrich(snap(BMW_I4), ("technical",), deadline=time.monotonic() + 20)
    assert len(provider.client.models.calls) == 1
    assert result["research_retry"] is False and result["research_retry_skipped"] == "NO_TIME_BUDGET"
    # the retry's HTTP timeout never exceeds what the deadline leaves
    provider = gemini(lambda prompt, config, n: no_research())
    provider.enrich(snap(BMW_I4), ("technical",), deadline=time.monotonic() + 60)
    timeouts = [c["config"].http_options.timeout for c in provider.client.models.calls]
    assert len(timeouts) == 2 and all(t <= 60_000 for t in timeouts)


def test_research_retry_can_be_disabled_and_failures_are_not_retried():
    provider = gemini(lambda prompt, config, n: no_research(), research_retry=False)
    assert provider.enrich(snap(BMW_I4), ("technical",))["attempt_count"] == 1
    truncated = gemini(lambda prompt, config, n: sdk_response({"claims": []}, finish="MAX_TOKENS"))
    result = truncated.enrich(snap(BMW_I4), ("technical",))
    assert result["error_code"] == "FINISH_MAX_TOKENS" and len(truncated.client.models.calls) == 1


def test_research_signals_count_only_google_evidence():
    citation = [{"kind": "citation", "uri": BMW_TECH}]
    assert research_signals({}, citation) == {"research_performed": False, "usable_evidence": False, "search_query_count": 0,
                                              "grounding_chunk_count": 0, "url_context_success_count": 0}
    failed_url = [{"kind": "url_context", "retrieved_url": BMW_TECH, "ok": False}]
    assert research_signals({"url_context_count": 1}, failed_url)["usable_evidence"] is False
    assert research_signals({"web_search_query_count": 2}, [])["research_performed"] is True
    assert research_signals({"url_context_success_count": 1}, [])["usable_evidence"] is True


# ===========================================================================
# Security (tests 17-19) — unchanged guarantees
# ===========================================================================
def test_17_allowlisted_url_without_google_grounding_is_rejected():
    out = validate(BMW_I4, [claim("torque_nm", 400, "Nm", BMW_TECH, BMW_TECH_EV, market="IL")], [])
    assert out["facts"] == {} and out["rejected_claims"][0]["reason"] == "SOURCE_NOT_GROUNDED"
    # through the provider: model-written URL, no research -> retried, then ungrounded, not cached
    resp = lambda prompt, config, n: sdk_response({"claims": [claim("torque_nm", 400, "Nm", BMW_TECH, BMW_TECH_EV, market="IL")]})  # noqa: E731
    repo = LiveOfficialEnrichmentRepository(gemini(resp), InProcessEnrichmentCache())
    outcome, _ = enrich_many(repo, [snap(BMW_I4)])[0]
    assert outcome["facts"] == {} and outcome["group_freshness"]["technical"]["state"] == "ungrounded"
    assert outcome["group_freshness"]["technical"]["failure_reason"] == "RESEARCH_NOT_PERFORMED"


@pytest.mark.parametrize("evil", ["https://bmw.co.il.evil.example/i4", "https://evilbmw.co.il/i4", "http://www.bmw.co.il/i4",
                                  "https://user@www.bmw.co.il/i4", "https://www.bmw.co.il:8443/i4"])
def test_18_lookalike_or_unsafe_urls_rejected(evil):
    sources = [chunk("bmw.co.il.evil.example"), {"kind": "url_context", "retrieved_url": evil, "ok": True}, chunk("bmw.co.il", 1)]
    out = validate(BMW_I4, [claim("torque_nm", 400, "Nm", evil, BMW_TECH_EV, market="IL")], sources)
    assert out["facts"] == {} and out["rejected_claims"][0]["reason"] in ("SOURCE_DOMAIN_NOT_ALLOWED", "SOURCE_URL_INVALID")


def test_19_different_powertrain_on_a_legitimate_grounded_domain_rejected():
    ev = {"model": "Audi Q3", "powertrain": "45 TFSI e plug-in hybrid 180 kW (245 PS)", "drivetrain": "front-wheel drive"}
    out = validate(AUDI_Q3, [claim("torque_nm", 400, "Nm", AUDI_PDF, ev)], [{"kind": "url_context", "retrieved_url": AUDI_PDF, "ok": True}])
    assert out["facts"] == {} and out["rejected_claims"][0]["reason"].startswith("VARIANT_") and "MISMATCH" in out["rejected_claims"][0]["reason"]


def test_price_still_requires_an_israeli_official_source():
    ev = {"model": "BMW i4 eDrive35", "trim": "Pure", "model_code": "41AW"}
    out = validate(BMW_I4, [claim("official_price_ils", 339000, "ILS", "https://www.bmw.com/en/i4.html", ev)], [chunk("bmw.com")])
    assert out["facts"] == {} and out["rejected_claims"][0]["reason"] == "ISRAELI_OFFICIAL_SOURCE_REQUIRED"


# ===========================================================================
# Prompt / schema contract
# ===========================================================================
def test_schema_separates_publication_year_from_vehicle_model_year():
    props = ENRICHMENT_RESPONSE_SCHEMA["properties"]["claims"]["items"]["properties"]
    assert "source_year" not in props
    assert props["source_publication_year"] == {"type": ["integer", "null"]}
    assert props["vehicle_model_year"] == {"type": ["integer", "null"]}
    assert {"body", "generation", "seating", "model_code"} <= set(props["identity_evidence"]["properties"])


@pytest.mark.parametrize("key,first", [(BMW_I4, BMW_TECH), (MERCEDES_CLE, CLE_PAGE), (AUDI_Q3, AUDI_PDF)])
def test_prompt_first_action_opens_the_most_relevant_seed(key, first):
    prompt = build_official_enrichment_prompt(snap(key), ("technical",), url_context=True)
    assert f"FIRST ACTION: open the most relevant official seed URL for this vehicle with URL Context: {first}" in prompt
    assert "vehicle_model_year = the model year" in prompt and "Do not copy the Ministry year" in prompt
    assert "Do not use the page or PDF publication date" in prompt
    assert "RESEARCH REQUIRED" not in prompt
    search_only = build_official_enrichment_prompt(snap(key), ("technical",), url_context=False)
    assert "FIRST ACTION: run Google Search restricted to the allowed official domains (site:" in search_only


def test_commercial_prompt_seeds_are_israeli_only():
    prompt = build_official_enrichment_prompt(snap(AUDI_Q3), ("price", "warranty"), url_context=True)
    assert "FIRST ACTION: open the most relevant official seed URL for this vehicle with URL Context: https://www.audi.co.il/" in prompt
    assert "audi-mediacenter" not in prompt


# ===========================================================================
# The production three-car scenario (same provider / repository / validator
# / pipeline path; fixtures shaped like the production responses)
# ===========================================================================
def _three_car_responder(prompt, config, n):
    commercial = is_commercial(prompt)
    if "Q3" in prompt and "F3BCHY" in prompt:
        if commercial:
            ev = dict(AUDI_PDF_EV, trim="S line", model_code="F3BCHY")
            return sdk_response({"claims": [claim("official_price_ils", 289900, "ILS", AUDI_IL_PRICE, ev, market="IL")]},
                                chunks=[chunk("audi.co.il")], queries=["site:audi.co.il Q3 מחירון"])
        tech = [claim(f, v, u, AUDI_PDF, AUDI_PDF_EV, publication=2025) for f, v, u in (
            ("torque_nm", 320, "Nm"), ("acceleration_0_100_s", 7.4, "s"), ("top_speed_kmh", 222, "km/h"),
            ("fuel_tank_l", 60, "L"), ("transmission_type", "dual_clutch", None), ("transmission_gears", 7, "gears"),
            ("cargo_volume_l", 530, "L"), ("tire_size", "235/50 R19", None), ("wheel_size_in", 19, "in"))]
        return sdk_response({"claims": tech}, chunks=[chunk("audi-mediacenter.com")], queries=["audi q3 40 tfsi quattro technical data"],
                            url_metadata=[url_ok(AUDI_PDF)])
    if "41AW" in prompt:
        if not is_retry(prompt):
            return no_research()  # production: STOP, 0 queries / 0 chunks / 0 URL-context
        if commercial:
            ev = {"model": "BMW i4 eDrive35", "trim": "Pure", "model_code": "41AW"}
            return sdk_response({"claims": [claim("official_price_ils", 339000, "ILS", BMW_PRICE, ev, market="IL")],
                                 "not_found_fields": ["warranty_battery_km"]},
                                chunks=[chunk("bmw.co.il")], queries=["site:bmw.co.il i4 eDrive35 מחירון"])
        tech = [claim(f, v, u, BMW_TECH, BMW_TECH_EV, market="IL", publication=2024) for f, v, u in (
            ("torque_nm", 400, "Nm"), ("acceleration_0_100_s", 6.0, "s"), ("battery_capacity_net_kwh", 67.1, "kWh"),
            ("dc_charging_power_kw", 180, "kW"), ("ac_charging_power_kw", 11, "kW"), ("cargo_volume_l", 470, "L"),
            ("length_mm", 4783, "mm"), ("height_mm", 1448, "mm"))]
        tech.append(claim("energy_consumption_kwh_100km", 16.1, "kWh/100km", BMW_TECH, BMW_TECH_EV, market="IL",
                          value_qualifier="one_of_several"))
        return sdk_response({"claims": tech}, url_metadata=[url_ok(BMW_TECH)])
    if "MJ4H" in prompt:
        if not is_retry(prompt):
            return no_research()
        if commercial:
            ev = dict(CLE_EV, trim="AMG Premium")
            return sdk_response({"claims": [claim("official_price_ils", 459000, "ILS", CLE_PAGE, ev, market="IL"),
                                            claim("warranty_vehicle_years", 3, "years", CLE_PAGE, ev, market="IL")]},
                                chunks=[chunk("mercedes-benz.co.il")], url_metadata=[url_ok(CLE_PAGE)])
        tech = [claim(f, v, u, CLE_PAGE, CLE_EV, market="IL") for f, v, u in (
            ("torque_nm", 400, "Nm"), ("top_speed_kmh", 250, "km/h"), ("transmission_type", "automatic", None),
            ("transmission_gears", 9, "gears"), ("fuel_tank_l", 66, "L"), ("cargo_volume_l", 420, "L"), ("length_mm", 4850, "mm"))]
        tech.append(claim("wheel_size_in", 19, "in", CLE_PAGE, dict(CLE_EV, trim="AMG Line"), market="IL"))
        return sdk_response({"claims": tech}, chunks=[chunk("mercedes-benz.co.il")], url_metadata=[url_ok(CLE_PAGE)])
    raise AssertionError("unexpected prompt")


def test_production_three_car_scenario_end_to_end():
    provider = gemini(_three_car_responder)
    deps = build_fake_deps(provider=provider)
    body = {"cars": [{"variant_identity_key": k} for k in (AUDI_Q3, BMW_I4, MERCEDES_CLE)]}
    data = collect_result(run_comparison_v2(body, deps, buyer_profile={"mode": "general"}))["data"]

    reports = {}
    for slot, key in zip(("car_1", "car_2", "car_3"), (AUDI_Q3, BMW_I4, MERCEDES_CLE)):
        outcome = deps.enrichment.cache.get(deps.enrichment.cache_key(snap(key)))
        reports[slot] = enrichment_report(snap(key), outcome, {"groups": ["technical", "price", "warranty"]})

    audi, bmw, cle = reports["car_1"], reports["car_2"], reports["car_3"]
    # --- Audi: the 9 production claims are no longer VARIANT_YEAR_MISMATCH
    audi_facts = data["vehicle_snapshots"]["car_1"]["official_enrichment"]["facts"]
    assert set(audi_facts) == {"torque_nm", "acceleration_0_100_s", "top_speed_kmh", "fuel_tank_l", "transmission_type",
                               "transmission_gears", "official_price_ils"}
    assert "VARIANT_YEAR_MISMATCH" not in audi["rejection_reasons"]
    assert audi["rejection_reasons"] == {"VARIANT_SCOPE_AMBIGUOUS": 3}  # cargo (no body), tyre + wheel (no trim)
    assert audi["research_performed"] and audi["research_retry"] is False
    assert set(audi["grounded_official_hosts"]) == {"audi.co.il", "audi-mediacenter.com", "uploads.audi-mediacenter.com"}
    assert audi["task_outcomes"]["technical"]["accepted"] == 6 and audi["task_outcomes"]["commercial"]["accepted"] == 1
    assert audi_facts["official_price_ils"]["identity_match"] == "model_code"
    # --- BMW: research-required retry recovers official research
    bmw_facts = data["vehicle_snapshots"]["car_2"]["official_enrichment"]["facts"]
    assert set(bmw_facts) == {"torque_nm", "acceleration_0_100_s", "battery_capacity_net_kwh", "dc_charging_power_kw",
                              "ac_charging_power_kw", "cargo_volume_l", "length_mm", "official_price_ils"}
    assert bmw["research_retry"] and bmw["research_performed"] and bmw["attempt_count"] == 4
    assert set(bmw["grounded_official_hosts"]) == {"bmw.co.il", "www.bmw.co.il"}
    assert bmw["rejection_reasons"] == {"VARIANT_SCOPE_AMBIGUOUS": 1, "VALUE_NOT_EXACT": 1}  # height, ranged consumption
    assert bmw["identity_match"] == {"powertrain": 5, "powertrain_body": 1, "model_generation": 1, "model_code": 1}
    # --- Mercedes: same recovery
    cle_facts = data["vehicle_snapshots"]["car_3"]["official_enrichment"]["facts"]
    assert set(cle_facts) == {"torque_nm", "top_speed_kmh", "transmission_type", "transmission_gears", "fuel_tank_l",
                              "cargo_volume_l", "length_mm", "official_price_ils", "warranty_vehicle_years"}
    assert cle["research_retry"] and cle["research_performed"]
    assert cle["rejection_reasons"] == {"VARIANT_TRIM_MISMATCH": 1}  # wheels stated for "AMG Line", not AMG PREMIUM
    assert cle_facts["official_price_ils"]["identity_match"] == "attributes"
    for report in reports.values():
        assert report["accepted_facts"] > 0 and report["grounded_official_hosts"]
        assert report["cache_write"] == "written"
        assert set(report["task_outcomes"]) == {"technical", "commercial"}
    # every accepted value is visible end-to-end with its official source
    rows = {r["metric"]: r for r in data["categories"]["performance"]["evidence"]["atomic_results"]}
    assert rows["torque_nm"]["values"] == {"car_1": 320, "car_2": 400, "car_3": 400}
    blob = json.dumps(reports, ensure_ascii=False)
    assert "ROLE:" not in blob and "RESEARCH REQUIRED" not in blob and "AIza" not in blob


# ===========================================================================
# Adversarial-review fixes
# ===========================================================================
def test_named_performance_variant_never_lends_its_body_dimensions():
    """CLE 53 AMG (wider body) with the right generation code: a named variant
    needs the exact powertrain for model_generation, not just C236."""
    amg = {"model": "CLE 53 AMG 4MATIC+ Coupé", "generation": "C236"}
    out = validate(MERCEDES_CLE, [claim("width_mm", 1900, "mm", CLE_PAGE, amg, market="IL")], [chunk("mercedes-benz.co.il")])
    assert out["facts"] == {} and out["rejected_claims"][0]["reason"] == "VARIANT_SCOPE_AMBIGUOUS"
    m50 = {"model": "BMW i4 M50", "generation": "G26"}
    out = validate(BMW_I4, [claim("width_mm", 1852, "mm", BMW_TECH, m50, market="IL")], [chunk("bmw.co.il")])
    assert out["facts"] == {}
    plain = {"model": "Mercedes-Benz CLE Coupé", "generation": "C236"}
    out = validate(MERCEDES_CLE, [claim("width_mm", 1860, "mm", CLE_PAGE, plain, market="IL")], [chunk("mercedes-benz.co.il")])
    assert out["facts"]["width_mm"]["identity_match"] == "model_generation"


def test_non_engine_decimals_are_not_a_displacement():
    ev = dict(AUDI_PDF_EV, powertrain="40 TFSI quattro 140 kW (190 PS), 0-100 km/h 7.4 s, 8.6 l/100 km")
    out = validate(AUDI_Q3, [claim("torque_nm", 320, "Nm", AUDI_PDF, ev)], [chunk("audi-mediacenter.com")])
    assert "torque_nm" in out["facts"]
    wrong = dict(AUDI_PDF_EV, powertrain="1.5 TFSI 110 kW (150 PS)")
    out = validate(AUDI_Q3, [claim("torque_nm", 250, "Nm", AUDI_PDF, wrong)], [chunk("audi-mediacenter.com")])
    assert out["rejected_claims"][0]["reason"] == "VARIANT_ENGINE_MISMATCH"


def test_charging_power_in_evidence_is_not_motor_output():
    ev = dict(BMW_TECH_EV, powertrain="electric 210 kW (286 hp), AC 11 kW, DC charging up to 180 kW")
    out = validate(BMW_I4, [claim("ac_charging_power_kw", 11, "kW", BMW_TECH, ev, market="IL")], [chunk("bmw.co.il")])
    assert out["facts"]["ac_charging_power_kw"]["identity_match"] == "powertrain"
    only_charging = dict(BMW_TECH_EV, powertrain="electric, AC 11 kW")
    match = match_variant(snap(BMW_I4), claim("ac_charging_power_kw", 11, "kW", BMW_TECH, only_charging), "powertrain")
    assert match["checks"]["engine"] is None  # unknown, not a contradiction


def test_body_variant_named_in_any_evidence_key_contradicts():
    """A Sportback / wagon named in the trim or powertrain text is still a
    different body (fail closed); the government trim may legitimise it."""
    for ev in (dict(AUDI_PDF_EV, powertrain="Q3 Sportback 40 TFSI quattro 140 kW (190 PS)"),
               dict(AUDI_PDF_EV, trim="Sportback S line"), dict(AUDI_PDF_EV, body="Avant"),
               dict(AUDI_PDF_EV, body="SUV-Coupé")):
        match = match_variant(snap(AUDI_Q3), claim("torque_nm", 320, "Nm", AUDI_PDF, ev), "powertrain")
        assert match["checks"]["body"] is False, ev


# ===========================================================================
# Second adversarial-review round
# ===========================================================================
def test_multi_variant_power_table_never_identifies_the_variant():
    table = dict(AUDI_PDF_EV, powertrain="35 TFSI 110 kW (150 PS) / 40 TFSI quattro 140 kW (190 PS) / 45 TFSI quattro 180 kW (245 PS)")
    for scope in ("powertrain", "exact_variant"):
        ev = dict(table, trim="S line") if scope == "exact_variant" else table
        match = match_variant(snap(AUDI_Q3), claim("torque_nm", 320, "Nm", AUDI_PDF, ev), scope)
        assert match["status"] == "VARIANT_SCOPE_AMBIGUOUS" and match["checks"]["engine"] is None, scope
    two_engines = dict(AUDI_PDF_EV, powertrain="1.5 TFSI / 2.0 TFSI petrol")
    assert match_variant(snap(AUDI_Q3), claim("torque_nm", 320, "Nm", AUDI_PDF, two_engines), "powertrain")["checks"]["engine"] is None
    # a mild-hybrid starter-generator output is auxiliary, not another variant
    assert match_variant(snap(MERCEDES_CLE), claim("torque_nm", 400, "Nm", CLE_PAGE, CLE_EV), "powertrain")["checks"]["engine"] is True


@pytest.mark.parametrize("engine", ["3.0 V6", "3.0 inline-6", "3.0 R6 petrol", "2.5 5-cylinder", "1.5 eTSI", "2,5 TFSI", "3.0 six-cylinder turbo"])
def test_other_displacement_formats_are_contradictions(engine):
    ev = dict(CLE_EV, powertrain=engine)
    assert match_variant(snap(MERCEDES_CLE), claim("torque_nm", 400, "Nm", CLE_PAGE, ev), "powertrain")["checks"]["engine"] is False


def test_amg_designation_in_powertrain_text_never_lends_dimensions():
    ev = {"model": "Mercedes-Benz CLE Coupé", "powertrain": "Mercedes-AMG CLE 53 4MATIC+ 3.0 inline-6", "drivetrain": "4MATIC+",
          "generation": "C236"}
    out = validate(MERCEDES_CLE, [claim("width_mm", 1900, "mm", CLE_PAGE, ev, market="IL")], [chunk("mercedes-benz.co.il")])
    assert out["facts"] == {}


def test_mixed_generation_codes_or_model_years_establish_nothing():
    mixed_gen = {"model": "Mercedes-Benz CLE", "generation": "C236 / A236"}
    out = validate(MERCEDES_CLE, [claim("length_mm", 4850, "mm", CLE_PAGE, mixed_gen, market="IL")], [chunk("mercedes-benz.co.il")])
    assert out["facts"] == {} and out["rejected_claims"][0]["reason"] == "VARIANT_SCOPE_AMBIGUOUS"
    mixed_year = {"model": "Mercedes-Benz CLE", "body": "Coupé MY2023/MY2024"}
    out = validate(MERCEDES_CLE, [claim("length_mm", 4850, "mm", CLE_PAGE, mixed_year, market="IL")], [chunk("mercedes-benz.co.il")])
    assert out["facts"] == {}
    only_2024 = {"model": "Mercedes-Benz CLE", "body": "Coupé"}
    out = validate(MERCEDES_CLE, [claim("length_mm", 4850, "mm", CLE_PAGE, only_2024, market="IL", model_year="2024")],
                   [chunk("mercedes-benz.co.il")])
    assert out["facts"]["length_mm"]["identity_match"] == "model_generation"  # "2024" as a string is a stated year
    wrong = claim("torque_nm", 400, "Nm", CLE_PAGE, CLE_EV, market="IL", model_year="2026")
    assert validate(MERCEDES_CLE, [wrong], [chunk("mercedes-benz.co.il")])["rejected_claims"][0]["reason"] == "VARIANT_YEAR_MISMATCH"


@pytest.mark.parametrize("trim,expected", [("S line", True), ("S line 40 TFSI quattro", True), ("Business line", False),
                                           ("S line Competition", False), ("S line Black Edition", False)])
def test_trim_is_matched_as_whole_words(trim, expected):
    ev = dict(AUDI_PDF_EV, trim=trim)
    assert match_variant(snap(AUDI_Q3), claim("wheel_size_in", 19, "in", AUDI_PDF, ev), "exact_variant")["checks"]["trim"] is expected
    cle = dict(CLE_EV, trim="AMG Line Premium Plus")
    assert match_variant(snap(MERCEDES_CLE), claim("wheel_size_in", 19, "in", CLE_PAGE, cle), "exact_variant")["checks"]["trim"] is False


def test_diesel_contradicts_a_petrol_vehicle():
    ev = dict(AUDI_PDF_EV, powertrain="40 TDI quattro 140 kW (190 PS) diesel")
    out = validate(AUDI_Q3, [claim("torque_nm", 400, "Nm", AUDI_PDF, ev)], [chunk("audi-mediacenter.com")])
    assert out["rejected_claims"][0]["reason"] == "VARIANT_PROPULSION_MISMATCH"


def test_price_rejected_without_an_israeli_source_is_not_negatively_cached():
    ev = {"model": "BMW i4 eDrive35", "trim": "Pure", "model_code": "41AW"}

    def responder(prompt, config, n):
        if is_commercial(prompt):
            return sdk_response({"claims": [claim("official_price_ils", 339000, "ILS", "https://www.bmw.com/en/i4.html", ev)]},
                                chunks=[chunk("bmw.com")], queries=["bmw i4 price"])
        return sdk_response({"claims": []}, chunks=[chunk("bmw.co.il")], queries=["bmw i4"])

    repo = LiveOfficialEnrichmentRepository(gemini(responder), InProcessEnrichmentCache())
    outcome, _ = enrich_many(repo, [snap(BMW_I4)])[0]
    price = outcome["group_freshness"]["price"]
    assert price["state"] == "grounding_unverifiable" and price["fresh_until"] is None
    assert price["failure_reason"] == "NO_OFFICIAL_SOURCE_INSPECTED"
