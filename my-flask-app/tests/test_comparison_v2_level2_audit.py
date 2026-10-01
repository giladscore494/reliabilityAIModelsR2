# -*- coding: utf-8 -*-
"""Regression tests for the Comparison V2 Level 2 production audit.

Production (d7935fa): Gemini returned HTTP 200 with grounded JSON, yet claims
from ``uploads.audi-mediacenter.com`` were rejected as SOURCE_NOT_GROUNDED,
the all-rejected result was cached as ``enriched`` with every freshness group
fresh (30 d technical), and the next runs never searched again.

Every response below is built through google-genai's own deserializer from a
REST-shaped payload shaped like the Gemini Developer API's (``domain`` is
absent from grounding chunks — the SDK documents it as unsupported there).
No network calls are made.
"""

import json
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from app.services.comparison_v2.cache import InProcessEnrichmentCache, build_cache_key
from app.services.comparison_v2.contracts import ENRICHMENT_CONTRACT_VERSION
from app.services.comparison_v2.demo_catalog import DemoVehicleCatalogRepository
from app.services.comparison_v2.field_validator import FIELD_VALIDATOR_VERSION, FieldValidator
from app.services.comparison_v2.grounding import (
    GroundingIndex,
    bare_hostname,
    is_grounding_redirect,
    resolve_grounding_redirects,
)
from app.services.comparison_v2.level15 import build_level15_snapshot
from app.services.comparison_v2.official_enrichment import (
    ENRICHMENT_RESPONSE_SCHEMA,
    GeminiOfficialEnrichmentProvider,
    LiveOfficialEnrichmentRepository,
    enrich_many,
    enrich_many_iter,
    enrichment_report,
    plan_tasks,
)
from app.services.comparison_v2.pipeline import (
    collect_result,
    comparison_cacheability,
    run_comparison_v2,
    server_timeout_sec,
)
from app.services.comparison_v2.source_registry import SOURCE_REGISTRY_VERSION, registry_site

from comparison_v2_fakes import AUDI_Q3, BMW_I4, HYUNDAI_TUCSON, FakeEnrichmentProvider, build_fake_deps

ROOT = Path(__file__).resolve().parents[1]
REPO = DemoVehicleCatalogRepository()
REDIRECT = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/"
AUDI = "אאודי"
PDF = ("https://uploads.audi-mediacenter.com/system/production/car_motorizations/1244/file_en/"
       "ceabc2b0fd48a749c10ea1fd2c8552b20f96e0c7/eTD-Audi-Q3-40-TFSI-quattro-S_tronic-140kW_250108.pdf")
# Exactly what a global technical-data PDF shows: model, powertrain,
# drivetrain — and no Israeli trim name.
AUDI_PDF_EVIDENCE = {"model": "Q3", "trim": None, "powertrain": "40 TFSI 2.0 petrol 190hp", "drivetrain": "quattro", "model_code": None}
AUDI_IL_EVIDENCE = {"model": "Q3", "trim": "S line", "powertrain": "40 TFSI 2.0 petrol 190hp", "drivetrain": "quattro", "model_code": None}


def snap(key, slot="car_1"):
    return build_level15_snapshot(REPO.get_variant(key), slot)


def claim(field, value, unit, url, *, market="GLOBAL", evidence=AUDI_PDF_EVIDENCE, scope="variant", year=2024, **extra):
    return {"field": field, "value": value, "unit": unit, "source_url": url, "source_title": "Audi", "source_market": market,
            "vehicle_model_year": year, "value_qualifier": "exact", "variant_scope": scope, "identity_evidence": evidence, **extra}


def chunk(title, i=0):
    """A Gemini Developer API grounding chunk: redirect URI + title, no domain."""
    return {"uri": f"{REDIRECT}AUZIYQ{i}", "title": title}


def sdk_response(payload, chunks=(), queries=("audi q3 40 tfsi technical data",), finish="STOP", url_metadata=None,
                 usage=None, text=None):
    cand = {"content": {"role": "model", "parts": [{"text": text if text is not None else json.dumps(payload)}]},
            "finishReason": finish}
    if chunks is not None and (chunks or queries):
        cand["groundingMetadata"] = {"webSearchQueries": list(queries), "groundingChunks": [{"web": c} for c in chunks]}
    if url_metadata:
        cand["urlContextMetadata"] = {"urlMetadata": url_metadata}
    rest = {"candidates": [cand], "modelVersion": "gemini-3.1-pro-preview",
            "usageMetadata": usage or {"promptTokenCount": 2100, "candidatesTokenCount": 900, "thoughtsTokenCount": 400,
                                       "toolUsePromptTokenCount": 5000, "totalTokenCount": 8400}}
    return genai_types.GenerateContentResponse._from_response(
        response=rest, kwargs={"config": {"response_json_schema": ENRICHMENT_RESPONSE_SCHEMA}}
    )


class FakeModels:
    def __init__(self, responder):
        self.responder = responder
        self.calls = []
        self.lock = threading.Lock()

    def generate_content(self, *, model, contents, config):
        with self.lock:
            self.calls.append({"model": model, "contents": contents, "config": config})
        return self.responder(contents, config)


class FakeClient:
    def __init__(self, responder):
        self.models = FakeModels(responder)


def technical_only(response_for_technical, commercial=None):
    """Responder: technical prompt -> the given response, commercial -> empty grounded JSON."""
    def responder(prompt, config):
        if "Israeli official commercial terms only" in prompt:
            return commercial or sdk_response({"claims": [], "not_found_fields": ["official_price_ils"]}, chunks=[chunk("audi.co.il")])
        return response_for_technical() if callable(response_for_technical) else response_for_technical
    return responder


def live_repo(responder, cache=None, clock=None, resolver=None, model="gemini-3.1-pro-preview"):
    provider = GeminiOfficialEnrichmentProvider(FakeClient(responder), model, 125,
                                                redirect_resolver=resolver, resolve_redirects=resolver is not None)
    kwargs = {"clock": clock} if clock else {}
    return LiveOfficialEnrichmentRepository(provider, cache or InProcessEnrichmentCache(), **kwargs), provider


# ===========================================================================
# Grounding correlation
# ===========================================================================
def test_reproduces_production_audi_mediacenter_claim_now_grounded():
    """Test 1: redirect URI + site title, no ``domain`` -> accepted at site tier."""
    outcome = FieldValidator().validate(
        snap(AUDI_Q3), {"claims": [claim("torque_nm", 320, "Nm", PDF)]}, [chunk("audi-mediacenter.com")]
    )
    assert outcome["rejected_claims"] == []
    fact = outcome["facts"]["torque_nm"]
    assert fact["value"] == 320 and fact["source_url"] == PDF
    assert fact["grounding_tier"] == "site" and fact["identity_match"] == "powertrain"
    assert outcome["grounded_official_hosts"] == ["audi-mediacenter.com"]


def test_resolved_redirect_gives_exact_url_tier_even_with_a_page_title():
    """Human-readable page title: correlation comes from the resolved redirect."""
    sources = [chunk("Audi Q3 40 TFSI quattro – technical data", 7)]
    resolve_grounding_redirects(sources, fetch_location=lambda uri, timeout: PDF)
    out = FieldValidator().validate(snap(AUDI_Q3), {"claims": [claim("torque_nm", 320, "Nm", PDF)]}, sources)
    assert out["facts"]["torque_nm"]["grounding_tier"] == "url"


def test_page_title_without_resolution_fails_closed():
    out = FieldValidator().validate(
        snap(AUDI_Q3), {"claims": [claim("torque_nm", 320, "Nm", PDF)]}, [chunk("Audi Q3 technical data")]
    )
    assert out["facts"] == {} and out["rejected_claims"][0]["reason"] == "SOURCE_NOT_GROUNDED"


def test_allowlisted_url_not_supported_by_grounding_is_rejected():
    """Test 2: an allowlisted site that Search never touched is still rejected."""
    out = FieldValidator().validate(
        snap(AUDI_Q3),
        {"claims": [claim("torque_nm", 320, "Nm", "https://www.audi.com/en/models/q3/specs.html")]},
        [chunk("audi.co.il"), chunk("audi-mediacenter.com", 1)],
    )
    assert out["facts"] == {}
    assert out["rejected_claims"][0]["reason"] == "SOURCE_NOT_GROUNDED"


@pytest.mark.parametrize("evil", [
    "https://uploads.audi-mediacenter.com.evil.example/x.pdf",
    "https://evil-uploads.audi-mediacenter.com.example/x.pdf",
    "https://audi-mediacenter.com.evil.example/x.pdf",
])
def test_lookalike_host_always_rejected_even_if_grounded(evil):
    """Test 3: a lookalike host fails the allowlist before grounding is consulted."""
    host = evil.split("/")[2]
    sources = [chunk(host), {"uri": f"{REDIRECT}x", "title": host, "retrieved_url": evil}]
    out = FieldValidator().validate(snap(AUDI_Q3), {"claims": [claim("torque_nm", 320, "Nm", evil)]}, sources)
    assert out["facts"] == {}
    assert out["rejected_claims"][0]["reason"] == "SOURCE_DOMAIN_NOT_ALLOWED"
    assert out["grounded_official_hosts"] == []


def test_redirect_handling_never_widens_the_allowlist():
    """Test 4: redirect evidence only correlates; it never admits a host."""
    # Only Google's exact redirect endpoint is ever requested.
    assert is_grounding_redirect(f"{REDIRECT}abc")
    for bad in (f"http://vertexaisearch.cloud.google.com/grounding-api-redirect/a",
                "https://vertexaisearch.cloud.google.com.evil.com/grounding-api-redirect/a",
                "https://vertexaisearch.cloud.google.com:8443/grounding-api-redirect/a",
                "https://user@vertexaisearch.cloud.google.com/grounding-api-redirect/a",
                "https://vertexaisearch.cloud.google.com/other/a",
                "https://uploads.audi-mediacenter.com/x.pdf"):
        assert not is_grounding_redirect(bad), bad
    requested = []
    sources = [{"kind": "grounding_chunk", "uri": "https://evil.example/r", "title": "x"},
               {"kind": "grounding_chunk", "uri": f"{REDIRECT}1", "title": "y"}]
    resolve_grounding_redirects(sources, fetch_location=lambda uri, timeout: requested.append(uri) or "https://www.carzone.co.il/q3")
    assert requested == [f"{REDIRECT}1"]
    # A redirect to a third-party site is ignored evidence, not official evidence.
    index = GroundingIndex(AUDI, sources)
    assert index.official_hosts == [] and "www.carzone.co.il" in index.ignored
    # A redirect to the Israeli importer does not ground a global audi.com claim.
    il = [{"kind": "grounding_chunk", "uri": f"{REDIRECT}2", "title": "", "retrieved_url": "https://www.audi.co.il/q3"}]
    assert GroundingIndex(AUDI, il).correlate("https://www.audi.com/en/q3") == (None, None)
    # Non-https or malformed Location headers are discarded.
    bad = [{"kind": "grounding_chunk", "uri": f"{REDIRECT}3", "title": ""}]
    stats = resolve_grounding_redirects(bad, fetch_location=lambda uri, timeout: "http://www.audi.com/q3")
    assert stats["redirects_resolved"] == 0 and "retrieved_url" not in bad[0]


def test_registry_site_and_bare_hostname_rules():
    assert registry_site(AUDI, "uploads.audi-mediacenter.com") == "audi-mediacenter.com"
    assert registry_site(AUDI, "campaign.audi.co.il") == "audi.co.il"
    assert registry_site(AUDI, "media.audi.com") == "audi.com"
    assert registry_site("ב מ וו", "bmw.scene7.com") == "bmw.scene7.com"  # exact-only host is its own site
    assert registry_site(AUDI, "uploads.audi-mediacenter.com.evil.example") is None
    assert bare_hostname("audi-mediacenter.com") == "audi-mediacenter.com"
    for not_host in ("Audi Q3 technical data", "audi", "https://audi.com/x", "", None, "a..b.com"):
        assert bare_hostname(not_host) is None, not_host


def test_citations_are_not_search_evidence_but_url_context_success_is():
    citation = [{"kind": "citation", "uri": "https://www.audi.com/en/q3", "title": "audi.com"}]
    assert GroundingIndex(AUDI, citation).official_hosts == []
    ok = [{"kind": "url_context", "retrieved_url": PDF, "status": "URL_RETRIEVAL_STATUS_SUCCESS", "ok": True}]
    failed = [{"kind": "url_context", "retrieved_url": PDF, "status": "URL_RETRIEVAL_STATUS_ERROR", "ok": False}]
    assert GroundingIndex(AUDI, ok).correlate(PDF) == ("url", "uploads.audi-mediacenter.com")
    assert GroundingIndex(AUDI, failed).correlate(PDF) == (None, None)


def test_min_tier_can_be_tightened(monkeypatch):
    monkeypatch.setenv("COMPARISON_GROUNDING_MIN_TIER", "host")
    out = FieldValidator().validate(snap(AUDI_Q3), {"claims": [claim("torque_nm", 320, "Nm", PDF)]}, [chunk("audi-mediacenter.com")])
    assert out["facts"] == {} and out["rejected_claims"][0]["reason"] == "SOURCE_NOT_GROUNDED"


# ===========================================================================
# Variant identity (powertrain-level for trim-independent technical fields)
# ===========================================================================
def test_trim_independent_field_accepted_without_trim_but_trim_dependent_is_not():
    out = FieldValidator().validate(
        snap(AUDI_Q3),
        {"claims": [claim("torque_nm", 320, "Nm", PDF), claim("cargo_volume_l", 530, "L", PDF)]},
        [chunk("audi-mediacenter.com")],
    )
    assert "torque_nm" in out["facts"]
    assert "cargo_volume_l" not in out["facts"]
    assert out["rejected_claims"][0]["reason"] == "VARIANT_SCOPE_AMBIGUOUS"


def test_trim_contradiction_and_missing_drivetrain_still_rejected():
    wrong_trim = dict(AUDI_PDF_EVIDENCE, trim="Advanced")
    no_drive = dict(AUDI_PDF_EVIDENCE, drivetrain=None)
    out = FieldValidator().validate(
        snap(AUDI_Q3),
        {"claims": [claim("torque_nm", 320, "Nm", PDF, evidence=wrong_trim), claim("top_speed_kmh", 222, "km/h", PDF, evidence=no_drive)]},
        [chunk("audi-mediacenter.com")],
    )
    assert out["facts"] == {}
    assert {r["reason"] for r in out["rejected_claims"]} == {"VARIANT_TRIM_MISMATCH", "VARIANT_SCOPE_AMBIGUOUS"}


# ===========================================================================
# Freshness / per-car cache
# ===========================================================================
def test_all_claims_rejected_by_grounding_infrastructure_is_not_cached():
    """Test 5 (the production run): JSON fine, every claim SOURCE_NOT_GROUNDED
    because no official host could be correlated -> no 30-day technical cache."""
    resp = lambda: sdk_response({"claims": [claim("torque_nm", 320, "Nm", PDF)]}, chunks=[chunk("Audi Q3 technical data")])  # noqa: E731
    cache = InProcessEnrichmentCache()
    repo, provider = live_repo(technical_only(resp), cache)
    outcome, meta = enrich_many(repo, [snap(AUDI_Q3)])[0]
    assert outcome["facts"] == {}
    assert outcome["group_freshness"]["technical"]["state"] == "grounding_unverifiable"
    assert outcome["group_freshness"]["technical"]["fresh_until"] is None
    assert outcome["observed_at"].get("technical") is None
    assert outcome["level2_health"] == "degraded"
    # commercial task genuinely found nothing -> bounded negative cache only
    assert outcome["group_freshness"]["price"]["state"] == "empty"
    _, groups = repo.plan(snap(AUDI_Q3))
    assert groups == ("technical",)  # technical is searched again next time
    enrich_many(repo, [snap(AUDI_Q3)])
    assert sum("Israeli official commercial terms only" not in c["contents"] for c in provider.client.models.calls) == 2


def test_all_claims_rejected_by_validator_gets_short_ttl_not_full():
    resp = lambda: sdk_response(  # noqa: E731
        {"claims": [claim("torque_nm", 320, "Nm", PDF, evidence=dict(AUDI_PDF_EVIDENCE, drivetrain="FWD"))]},
        chunks=[chunk("audi-mediacenter.com")],
    )
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    repo, _ = live_repo(technical_only(resp), clock=lambda: now)
    outcome, _ = enrich_many(repo, [snap(AUDI_Q3)])[0]
    tech = outcome["group_freshness"]["technical"]
    assert tech["state"] == "rejected"
    assert datetime.fromisoformat(tech["fresh_until"]) - now == timedelta(hours=12)
    assert outcome["level2_health"] == "degraded"  # never frozen into a whole-comparison cache


def test_partial_enrichment_caches_observed_groups_and_keeps_values_on_refresh():
    """Test 6: partial technical -> short TTL; facts survive a weaker re-search."""
    first = sdk_response({"claims": [claim("torque_nm", 320, "Nm", PDF), claim("top_speed_kmh", 222, "km/h", PDF)]},
                         chunks=[chunk("audi-mediacenter.com")])
    second = sdk_response({"claims": [claim("acceleration_0_100_s", 7.4, "s", PDF)]}, chunks=[chunk("audi-mediacenter.com")])
    responses = iter([first, second])
    now = [datetime(2026, 10, 1, tzinfo=timezone.utc)]
    repo, _ = live_repo(technical_only(lambda: next(responses)), clock=lambda: now[0])
    out1, _ = enrich_many(repo, [snap(AUDI_Q3)])[0]
    tech = out1["group_freshness"]["technical"]
    assert tech["state"] == "partial" and tech["accepted"] == 2 and tech["requested"] == 24
    assert datetime.fromisoformat(tech["fresh_until"]) - now[0] == timedelta(days=3)
    assert out1["observed_at"]["technical"] and out1["level2_health"] == "partial"
    assert out1["cache_write"] == "written"
    now[0] += timedelta(days=3, hours=1)
    out2, meta = enrich_many(repo, [snap(AUDI_Q3)])[0]
    assert "technical" in meta["groups"]  # partial TTL (3 d) expired; commercial negative TTLs too
    assert set(out2["facts"]) == {"torque_nm", "top_speed_kmh", "acceleration_0_100_s"}
    assert out2["facts"]["torque_nm"]["carried_over"] is True


@pytest.mark.parametrize("failure", ["timeout", "provider_error", "invalid_json", "max_tokens", "safety"])
def test_provider_failures_never_make_groups_fresh(failure):
    """Tests 7, 8, 19: timeout / error / INVALID_JSON / truncation -> no cache."""
    import httpx

    def responder(prompt, config):
        if failure == "timeout":
            raise httpx.ReadTimeout("timed out")
        if failure == "provider_error":
            raise genai_errors.ServerError(503, {"error": {"code": 503, "message": "unavailable", "status": "UNAVAILABLE"}})
        if failure == "invalid_json":
            return sdk_response(None, chunks=[chunk("audi-mediacenter.com")], text="Here are the specs: torque 320 Nm")
        if failure == "max_tokens":
            return sdk_response(None, chunks=[chunk("audi-mediacenter.com")], finish="MAX_TOKENS",
                                text='{"claims": [{"field": "torque_nm", "value": 320')
        return sdk_response({"claims": [claim("torque_nm", 320, "Nm", PDF)]}, chunks=[chunk("audi-mediacenter.com")], finish="SAFETY")

    cache = InProcessEnrichmentCache()
    repo, _ = live_repo(responder, cache)
    outcome, _ = enrich_many(repo, [snap(AUDI_Q3)])[0]
    expected = {"timeout": "CALL_TIMEOUT", "provider_error": "PROVIDER_ERROR:ServerError", "invalid_json": "INVALID_JSON",
                "max_tokens": "FINISH_MAX_TOKENS", "safety": "FINISH_SAFETY"}[failure]
    assert outcome["status"] == "failed" and outcome["error_code"] == expected
    assert outcome["facts"] == {} and outcome["observed_at"] == {}
    assert outcome["cache_write"] == "skipped_no_meaningful_observation"
    assert cache.get(repo.cache_key(snap(AUDI_Q3))) is None
    assert set(repo.plan(snap(AUDI_Q3))[1]) == {"technical", "price", "warranty"}
    if failure in ("invalid_json", "max_tokens", "safety"):
        diag = outcome["provider_diagnostics"]
        assert diag["finish_reason"] == {"invalid_json": "STOP", "max_tokens": "MAX_TOKENS", "safety": "SAFETY"}[failure]
        assert "text_head" not in diag and diag["usage"]["total_token_count"] == 8400


def test_flash_and_pro_enrichment_use_different_cache_keys():
    """Test 9: a Flash cache entry never masquerades as a Pro result."""
    cache = InProcessEnrichmentCache()
    flash_provider = FakeEnrichmentProvider()
    flash_provider.model_id = "gemini-3.8-flash"
    flash = LiveOfficialEnrichmentRepository(flash_provider, cache)
    flash.get_or_enrich(snap(HYUNDAI_TUCSON))
    pro_provider = FakeEnrichmentProvider()
    pro_provider.model_id = "gemini-3.1-pro-preview"
    pro = LiveOfficialEnrichmentRepository(pro_provider, cache)
    assert flash.cache_key(snap(HYUNDAI_TUCSON)) != pro.cache_key(snap(HYUNDAI_TUCSON))
    cached, groups = pro.plan(snap(HYUNDAI_TUCSON))
    assert cached is None and set(groups) == {"technical", "price", "warranty"}
    pro.get_or_enrich(snap(HYUNDAI_TUCSON))
    assert len(pro_provider.calls) == 2


def test_contract_registry_and_validator_versions_are_part_of_the_key():
    """Test 10: semantics changes invalidate old cache automatically."""
    base = build_cache_key("v", ENRICHMENT_CONTRACT_VERSION, SOURCE_REGISTRY_VERSION, "m", FIELD_VALIDATOR_VERSION)
    assert base != build_cache_key("v", "official-enrichment/1", SOURCE_REGISTRY_VERSION, "m", FIELD_VALIDATOR_VERSION)
    assert base != build_cache_key("v", ENRICHMENT_CONTRACT_VERSION, "official-source-registry/2", "m", FIELD_VALIDATOR_VERSION)
    assert base != build_cache_key("v", ENRICHMENT_CONTRACT_VERSION, SOURCE_REGISTRY_VERSION, "m", "field-validator/1")
    # The poisoned production rows were written under /1 without a validator version.
    assert base != build_cache_key("v", "official-enrichment/1", SOURCE_REGISTRY_VERSION, "m")
    # /3 (publication year vs vehicle model year, identity scopes, research
    # retry): rows written under /2 — e.g. Audi technical "rejected" for
    # VARIANT_YEAR_MISMATCH — are never read again.
    assert ENRICHMENT_CONTRACT_VERSION == "official-enrichment/3" and FIELD_VALIDATOR_VERSION == "field-validator/3"
    assert base != build_cache_key("v", "official-enrichment/2", SOURCE_REGISTRY_VERSION, "m", "field-validator/2")
    assert base != build_cache_key("v", ENRICHMENT_CONTRACT_VERSION, SOURCE_REGISTRY_VERSION, "m", "field-validator/2")


def test_legacy_poisoned_payload_shape_would_not_be_trusted_even_under_new_key():
    """A payload without group_freshness falls back to observed_at only."""
    cache = InProcessEnrichmentCache()
    provider = FakeEnrichmentProvider()
    repo = LiveOfficialEnrichmentRepository(provider, cache)
    cache.set(repo.cache_key(snap(AUDI_Q3)), AUDI_Q3, {"status": "enriched", "facts": {}, "observed_at": {}}, "m")
    _, groups = repo.plan(snap(AUDI_Q3))
    assert set(groups) == {"technical", "price", "warranty"}


# ===========================================================================
# Model configuration
# ===========================================================================
def test_enrichment_model_defaults_to_pro_and_summary_stays_independent(monkeypatch):
    """Tests 14, 15."""
    from app.services.comparison.model_config import (
        comparison_enrichment_model_id,
        comparison_stage_a_model_id,
        comparison_summary_model_id,
        validate_comparison_model_config,
    )

    for name in ("COMPARISON_ENRICHMENT_MODEL", "COMPARISON_SUMMARY_MODEL", "COMPARISON_STAGE_A_MODEL", "GEMINI_COMPARE_MODEL_ID"):
        monkeypatch.delenv(name, raising=False)
    assert comparison_enrichment_model_id() == "gemini-3.1-pro-preview"
    assert comparison_summary_model_id() == "gemini-3.8-flash"
    validate_comparison_model_config()
    monkeypatch.setenv("COMPARISON_ENRICHMENT_MODEL", "gemini-3.8-flash")
    assert comparison_enrichment_model_id() == "gemini-3.8-flash" and comparison_summary_model_id() == "gemini-3.8-flash"
    monkeypatch.delenv("COMPARISON_ENRICHMENT_MODEL")
    monkeypatch.setenv("COMPARISON_SUMMARY_MODEL", "gemini-3.5-flash")
    assert comparison_enrichment_model_id() == "gemini-3.1-pro-preview" and comparison_summary_model_id() == "gemini-3.5-flash"
    monkeypatch.setenv("COMPARISON_STAGE_A_MODEL", "gemini-3.5-flash")
    assert comparison_enrichment_model_id() == "gemini-3.1-pro-preview"  # legacy Stage A is unrelated


def test_default_deps_wire_pro_enrichment_with_google_search(monkeypatch):
    """Tests 14, 16 through the production wiring."""
    from app.services.comparison_v2.pipeline import build_default_deps

    for name in ("COMPARISON_ENRICHMENT_MODEL", "COMPARISON_SUMMARY_MODEL", "COMPARISON_V2_OFFLINE_MODE"):
        monkeypatch.delenv(name, raising=False)
    deps = build_default_deps(ai_client=object())
    provider = deps.enrichment.provider
    assert provider.model_id == "gemini-3.1-pro-preview"
    assert deps.summary_writer.model_id == "gemini-3.8-flash"
    assert deps.provider_meta["enrichment_model"] == "gemini-3.1-pro-preview"
    cfg = provider._config(("technical",), "combustion")
    assert any(t.google_search is not None for t in cfg.tools)


# ===========================================================================
# Provider request / response handling
# ===========================================================================
def test_grounded_structured_response_parsed_with_full_observability(caplog):
    """Test 18 + observability: JSON + grounding + usage + finish reason."""
    resp = sdk_response({"claims": [claim("torque_nm", 320, "Nm", PDF)], "not_found_fields": ["cargo_volume_l"]},
                        chunks=[chunk("audi-mediacenter.com"), chunk("audi.com", 1)],
                        queries=("audi q3 40 tfsi torque", "audi q3 technical data pdf"),
                        url_metadata=[{"retrievedUrl": PDF, "urlRetrievalStatus": "URL_RETRIEVAL_STATUS_SUCCESS"}])
    repo, _ = live_repo(technical_only(resp))
    outcome, meta = enrich_many(repo, [snap(AUDI_Q3)])[0]
    assert outcome["facts"]["torque_nm"]["grounding_tier"] == "url"  # URL-context retrieval of the exact PDF
    report = enrichment_report(snap(AUDI_Q3), outcome, meta)
    for key in ("vehicle", "requested_groups", "requested_field_count", "model", "duration_ms", "provider_status",
                "finish_reasons", "usage", "grounding_metadata_present", "search_query_count", "grounding_chunk_count",
                "grounded_source_count", "accepted_facts", "rejected_claims", "model_generic_claims", "conflicts",
                "missing", "rejection_reasons", "cache_write", "fresh_groups_marked"):
        assert key in report, key
    assert report["model"] == "gemini-3.1-pro-preview" and report["finish_reasons"] == ["STOP", "STOP"]
    assert report["search_query_count"] == 3 and report["grounding_chunk_count"] == 3
    assert report["usage"]["prompt_token_count"] == 4200 and report["usage"]["total_token_count"] == 16800
    assert report["requested_field_count"] == 30 and report["accepted_facts"] == 1
    assert report["cache_write"] == "written" and set(report["fresh_groups_marked"]) == {"technical", "price", "warranty"}
    blob = json.dumps(report, ensure_ascii=False)
    assert "ROLE:" not in blob and "AIza" not in blob


def test_slow_call_inside_window_is_accepted():
    """Test 17."""
    def slow():
        time.sleep(0.3)
        return sdk_response({"claims": [claim("torque_nm", 320, "Nm", PDF)]}, chunks=[chunk("audi-mediacenter.com")])

    repo, _ = live_repo(technical_only(slow))
    outcome, _ = enrich_many(repo, [snap(AUDI_Q3)], timeout_sec=1.0, poll_interval=0.02)[0]
    assert outcome["status"] == "enriched" and "torque_nm" in outcome["facts"]


def test_url_context_rejection_falls_back_once_to_search_only():
    seen = []

    def responder(prompt, config):
        seen.append([("url_context" if t.url_context is not None else "google_search") for t in config.tools])
        if len(seen) == 1:
            raise genai_errors.ClientError(400, {"error": {"code": 400, "message": "url_context unsupported", "status": "INVALID_ARGUMENT"}})
        return sdk_response({"claims": [claim("torque_nm", 320, "Nm", PDF)]}, chunks=[chunk("audi-mediacenter.com")])

    provider = GeminiOfficialEnrichmentProvider(FakeClient(responder), "gemini-3.1-pro-preview", 125, url_context=True, resolve_redirects=False)
    result = provider.enrich(snap(AUDI_Q3), ("technical",))
    assert seen == [["google_search", "url_context"], ["google_search"]]
    assert result["error_code"] is None and result["url_context_fallback"] is True


def test_split_plan_and_prompt_scoping(monkeypatch):
    assert plan_tasks(("technical", "price", "warranty")) == [("technical", ("technical",)), ("commercial", ("price", "warranty"))]
    assert plan_tasks(("price",)) == [("commercial", ("price",))]
    monkeypatch.setenv("COMPARISON_ENRICHMENT_SPLIT", "false")
    assert plan_tasks(("technical", "price")) == [("all", ("technical", "price"))]
    from app.services.comparison_v2.official_enrichment import build_official_enrichment_prompt

    commercial = build_official_enrichment_prompt(snap(AUDI_Q3), ("price", "warranty"))
    assert "audi.co.il" in commercial and "audi-mediacenter" not in commercial and "torque_nm" not in commercial
    technical = build_official_enrichment_prompt(snap(AUDI_Q3), ("technical",))
    assert "uploads.audi-mediacenter.com" in technical and "official_price_ils" not in technical
    assert "engine_cc" not in technical.split("FIELDS TO LOOK FOR")[1]  # no cross-check search budget


def test_task_deadline_is_enforced_without_waiting():
    def slow(prompt, config):
        time.sleep(2.0)
        return sdk_response({"claims": []}, chunks=[chunk("audi.co.il")])

    repo, _ = live_repo(slow)
    started = time.monotonic()
    outcome, _ = enrich_many(repo, [snap(AUDI_Q3)], deadline=time.monotonic() + 0.3, poll_interval=0.02)[0]
    assert time.monotonic() - started < 1.5
    assert outcome["status"] == "failed" and outcome["error_code"] == "DEADLINE_EXCEEDED"


def test_enrich_many_iter_emits_heartbeats_while_waiting():
    def slow(prompt, config):
        time.sleep(0.35)
        return sdk_response({"claims": []}, chunks=[chunk("audi.co.il")])

    repo, _ = live_repo(slow)
    gen = enrich_many_iter(repo, [snap(AUDI_Q3)], poll_interval=0.02, heartbeat_sec=0.1)
    beats = 0
    while True:
        try:
            next(gen)
            beats += 1
        except StopIteration as stop:
            results = stop.value
            break
    assert beats >= 2 and len(results) == 1


# ===========================================================================
# Pipeline: fact -> deterministic evidence -> API -> UI
# ===========================================================================
def _audi_pdf_provider():
    provider = FakeEnrichmentProvider()
    provider.outputs = dict(provider.outputs)
    provider.outputs[AUDI_Q3] = {
        "raw": {"claims": [claim("torque_nm", 320, "Nm", PDF), claim("acceleration_0_100_s", 7.4, "s", PDF)]},
        "grounded_sources": [chunk("audi-mediacenter.com")],  # production shape: no domain
    }
    return provider


def test_accepted_level2_fact_reaches_deterministic_evidence_and_api():
    """Tests 11, 12 (backend half)."""
    data = collect_result(run_comparison_v2(
        {"cars": [{"variant_identity_key": AUDI_Q3}, {"variant_identity_key": HYUNDAI_TUCSON}]},
        build_fake_deps(provider=_audi_pdf_provider()), buyer_profile={"mode": "general"},
    ))["data"]
    fact = data["vehicle_snapshots"]["car_1"]["official_enrichment"]["facts"]["torque_nm"]
    assert fact["value"] == 320 and fact["grounding_tier"] == "site"
    rows = {r["metric"]: r for r in data["categories"]["performance"]["evidence"]["atomic_results"]}
    torque = rows["torque_nm"]
    assert torque["values"] == {"car_1": 320, "car_2": 350} and torque["status"] == "compared"
    assert torque["provenance"]["car_1"]["source_url"] == PDF
    assert torque["provenance"]["car_1"]["sources"][0]["grounding_tier"] == "site"
    assert any(s["source_url"] == PDF and "torque_nm" in s["fields"] for s in data["sources"]["car_1"])
    diag = data["diagnostics"]["car_1"]
    assert diag["level2_health"] in ("partial", "degraded") and diag["grounded_official_hosts"] == ["audi-mediacenter.com"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_accepted_level2_fact_is_rendered_by_the_v22_frontend():
    """Test 12 (UI half): the real compare_v2.js renders the official value + source."""
    data = collect_result(run_comparison_v2(
        {"cars": [{"variant_identity_key": AUDI_Q3}, {"variant_identity_key": HYUNDAI_TUCSON}]},
        build_fake_deps(provider=_audi_pdf_provider()), buyer_profile={"mode": "general"},
    ))["data"]
    torque = next(r for r in data["categories"]["performance"]["evidence"]["atomic_results"] if r["metric"] == "torque_nm")
    script = f"""
        const api = require({json.dumps(str(ROOT / 'static' / 'compare_v2.js'))});
        const r = {json.dumps(data, ensure_ascii=False)};
        process.stdout.write(api.buildResultBodyV22Html(r));
    """
    html = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout
    assert torque["display"]["car_1"] in html
    assert "uploads.audi-mediacenter.com" in html  # the official source link is shown


def test_whole_comparison_cache_never_freezes_failed_enrichment(app):
    """Test 13: ungrounded/failed Level 2 -> not cached; recomputed next time."""
    from app.services.comparison_v2.pipeline import ComparisonV2HistoryStore

    provider = FakeEnrichmentProvider()
    provider.outputs = {k: {"raw": {"claims": v["raw"]["claims"]}, "grounded_sources": []} for k, v in provider.outputs.items()}
    body = {"cars": [{"variant_identity_key": HYUNDAI_TUCSON}, {"variant_identity_key": BMW_I4}]}
    with app.app_context():
        deps = build_fake_deps(provider=provider, history=ComparisonV2HistoryStore())
        first = collect_result(run_comparison_v2(body, deps, buyer_profile={"mode": "general"}))["data"]
        assert first["vehicle_snapshots"]["car_1"]["official_enrichment"]["facts"] == {}
        assert first["vehicle_snapshots"]["car_1"]["official_enrichment"]["group_states"]["technical"] == "ungrounded"
        second = collect_result(run_comparison_v2(body, deps, buyer_profile={"mode": "general"}))["data"]
    assert second["cached"] is False
    assert len(provider.calls) == 8  # every task searched again: nothing poisoned


def test_whole_comparison_cache_hit_requires_healthy_level2_and_expires_with_it(app):
    from app.services.comparison_v2.pipeline import ComparisonV2HistoryStore

    provider = FakeEnrichmentProvider()
    body = {"cars": [{"variant_identity_key": HYUNDAI_TUCSON}, {"variant_identity_key": BMW_I4}]}
    now = [datetime.now(timezone.utc).replace(tzinfo=None)]
    with app.app_context():
        history = ComparisonV2HistoryStore(now=lambda: now[0])
        deps = build_fake_deps(provider=provider, history=history)
        collect_result(run_comparison_v2(body, deps, buyer_profile={"mode": "general"}))
        hit = collect_result(run_comparison_v2(body, deps, buyer_profile={"mode": "general"}))["data"]
        assert hit["cached"] is True
        # Copies of a cache hit are history only: they never extend the cache.
        now[0] += timedelta(hours=13)  # past the empty-price negative TTL (12 h) of the cars
        miss = collect_result(run_comparison_v2(body, deps, buyer_profile={"mode": "general"}))["data"]
    assert miss["cached"] is False


def test_comparison_cacheability_rules():
    ok = {"status": "enriched", "cache_valid_until": "2026-10-02T00:00:00+00:00",
          "group_freshness": {g: {"state": "complete"} for g in ("technical", "price", "warranty")}}
    later = dict(ok, cache_valid_until="2026-10-09T00:00:00+00:00")
    assert comparison_cacheability({"outcome": "car_1"}, [ok, later]) == (True, "2026-10-02T00:00:00+00:00", None)
    for state in ("rejected", "ungrounded", "grounding_unverifiable", "failed"):
        bad = dict(ok, group_freshness={**ok["group_freshness"], "technical": {"state": state}})
        assert comparison_cacheability({"outcome": "car_1"}, [ok, bad])[0] is False, state
    assert comparison_cacheability({"outcome": "decision_unavailable"}, [ok, ok])[0] is False
    assert comparison_cacheability({"outcome": "car_1"}, [ok, dict(ok, status="failed")])[0] is False


# ===========================================================================
# Wall-clock budget
# ===========================================================================
def test_server_timeout_detection(monkeypatch):
    import sys

    monkeypatch.delenv("COMPARISON_V2_SERVER_TIMEOUT_SEC", raising=False)
    monkeypatch.delenv("GUNICORN_CMD_ARGS", raising=False)
    monkeypatch.setattr(sys, "argv", ["/usr/bin/gunicorn", "main:create_app()", "--timeout", "180", "--workers", "2"])
    assert server_timeout_sec() == 180
    monkeypatch.setattr(sys, "argv", ["/usr/bin/gunicorn", "main:create_app()", "--timeout=240"])
    assert server_timeout_sec() == 240
    monkeypatch.setattr(sys, "argv", ["pytest"])
    assert server_timeout_sec() is None
    monkeypatch.setenv("GUNICORN_CMD_ARGS", "--workers 2 -t 90")
    assert server_timeout_sec() == 90
    monkeypatch.setenv("COMPARISON_V2_SERVER_TIMEOUT_SEC", "200")
    assert server_timeout_sec() == 200


def test_tight_budget_skips_remote_work_instead_of_being_killed(monkeypatch):
    """Enrichment/JEV never run past what the server timeout leaves."""
    monkeypatch.setenv("COMPARISON_V2_SERVER_TIMEOUT_SEC", "20")  # budget 8s < 30s post-enrichment reserve
    provider = FakeEnrichmentProvider()
    deps = build_fake_deps(provider=provider)
    out = collect_result(run_comparison_v2(
        {"cars": [{"variant_identity_key": AUDI_Q3}, {"variant_identity_key": HYUNDAI_TUCSON}]}, deps, buyer_profile={"mode": "general"},
    ))
    data = out["data"]
    assert provider.calls == []  # no task started without its window
    assert data["vehicle_snapshots"]["car_1"]["official_enrichment"]["status"] == "failed"
    assert data["diagnostics"]["car_1"]["error_code"] == "DEADLINE_EXCEEDED"
    assert data["decision"]["reason"] == "deadline_exceeded"
    assert out["type"] == "result"


# ===========================================================================
# Range standards (a valid range must not be hidden)
# ===========================================================================
def _ev_claims(range_claims):
    ev = {"model": "i4 eDrive35", "trim": "Pure", "powertrain": "electric 286hp", "drivetrain": "RWD"}
    url = "https://www.bmw.co.il/he/all-models/i4/technical-data.html"
    return [dict(claim("electric_range_km", v, "km", url, market="IL", evidence=ev), measurement_standard=std) for v, std in range_claims]


def test_conflict_in_another_standard_does_not_hide_selected_range():
    out = FieldValidator().validate(snap(BMW_I4), {"claims": _ev_claims([(483, "WLTP"), (400, "EPA"), (430, "EPA")])},
                                    [chunk("bmw.co.il")])
    assert out["facts"]["electric_range_km"]["value"] == 483
    assert out["facts"]["electric_range_km"]["conflicting_standards"] == ["EPA"]
    assert [c for c in out["conflicts"] if c["field"] == "electric_range_km"] == []
    assert out["range_standard_conflicts"][0]["measurement_standard"] == "EPA"


def test_ranges_compared_on_a_common_published_standard():
    from app.services.comparison_v2.deterministic_engine import METRIC_REGISTRY, compare_metric

    metric = next(m for m in METRIC_REGISTRY if m.key == "electric_range_km")

    def ev_snap(slot, primary, alternates):
        s = snap(BMW_I4, slot)
        s["official_enrichment"]["facts"]["electric_range_km"] = {
            "value": primary[0], "measurement_standard": primary[1], "validated": True, "variant_scope": "variant",
            "alternate_standards": {std: {"value": v, "sources": []} for std, v in alternates.items()},
            "source_url": "https://www.bmw.co.il/x", "sources": [],
        }
        return s

    res = compare_metric(metric, {"car_1": ev_snap("car_1", (483, "WLTP"), {"EPA": 430}), "car_2": ev_snap("car_2", (400, "EPA"), {})})
    assert res["status"] == "compared" and res["measurement_standard"] == "EPA"
    assert res["values"] == {"car_1": 430, "car_2": 400} and res["display"]["car_1"].endswith("(EPA)")
    res2 = compare_metric(metric, {"car_1": ev_snap("car_1", (483, "WLTP"), {}), "car_2": ev_snap("car_2", (400, "EPA"), {})})
    assert res2["status"] == "not_comparable" and res2["reason"] == "RANGE_STANDARD_MISMATCH"


def test_diagnostic_report_uses_production_report_and_is_safe():
    from scripts.enrichment_diagnostic import build_report

    resp = sdk_response({"claims": [claim("torque_nm", 320, "Nm", PDF), claim("cargo_volume_l", 530, "L", PDF)]},
                        chunks=[chunk("audi-mediacenter.com")])
    repo, provider = live_repo(technical_only(resp))
    outcome, meta = enrich_many(repo, [snap(AUDI_Q3)])[0]
    report = build_report(snap(AUDI_Q3), outcome, meta, provider)
    assert report["model"] == "gemini-3.1-pro-preview"
    assert report["accepted"]["torque_nm"]["grounding_tier"] == "site"
    assert report["rejected_by_reason"]["VARIANT_SCOPE_AMBIGUOUS"][0]["field"] == "cargo_volume_l"
    assert report["cache_decision"]["cache_write"] == "written"
    assert len(report["requested_fields"]) == 30 and report["summary"]["finish_reasons"] == ["STOP", "STOP"]
    assert "ROLE:" not in json.dumps(report, ensure_ascii=False)
