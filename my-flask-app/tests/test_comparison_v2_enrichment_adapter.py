# -*- coding: utf-8 -*-
"""Regression tests for the live Level 2 enrichment adapter (google-genai 2.x).

Covers the production failures seen with Audi Q3 vs BMW i4: INVALID_JSON with
zero grounding sources, and CALL_TIMEOUT from a shared wrapper deadline.
Responses are built through the SDK's own deserializer from REST-shaped
payloads; no network calls are made.
"""

import json
import threading
import time

import pytest
from google.genai import types as genai_types

from app.services.comparison_v2.cache import InProcessEnrichmentCache
from app.services.comparison_v2.demo_catalog import DemoVehicleCatalogRepository
from app.services.comparison_v2.level15 import build_level15_snapshot
from app.services.comparison_v2.official_enrichment import (
    ENRICHMENT_RESPONSE_SCHEMA,
    GeminiOfficialEnrichmentProvider,
    LiveOfficialEnrichmentRepository,
    enrich_many,
    extract_grounded_sources,
    extract_structured_output,
    invalid_json_diagnostics,
)

from comparison_v2_fakes import AUDI_Q3, BMW_I4

REPO = DemoVehicleCatalogRepository()
REDIRECT = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/"

BMW_CLAIM = {
    "field": "torque_nm", "value": 400, "unit": "Nm",
    "source_url": "https://www.bmw.co.il/he/all-models/i-series/i4/i4-gran-coupe-2024-g26bev-technical-data.html",
    "source_title": "BMW i4 technical data", "source_market": "IL", "source_year": 2024, "variant_scope": "variant",
    "identity_evidence": {"model": "i4 eDrive35", "trim": "Pure", "powertrain": "electric 286hp", "drivetrain": "RWD", "model_code": None},
}


def snap(key):
    return build_level15_snapshot(REPO.get_variant(key), "car_1")


def sdk_response(text_parts, chunks=(("bmw.co.il", "bmw.co.il"),), finish="STOP", queries=("bmw i4 edrive35",)):
    """A GenerateContentResponse produced by google-genai's own deserializer."""
    cand = {"content": {"role": "model", "parts": [{"text": t} for t in text_parts]}, "finishReason": finish}
    if chunks is not None:
        cand["groundingMetadata"] = {
            "webSearchQueries": list(queries),
            "groundingChunks": [{"web": {"uri": REDIRECT + str(i), "title": title, "domain": domain}} for i, (title, domain) in enumerate(chunks)],
        }
    rest = {"candidates": [cand], "modelVersion": "gemini-3.8-flash"}
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
        return self.responder(contents)


class FakeClient:
    def __init__(self, responder):
        self.models = FakeModels(responder)


# --------------------------------------------------------------------------
# request contract (SDK 2.x)
# --------------------------------------------------------------------------
def test_provider_config_one_attempt_provider_timeout_low_thinking(monkeypatch):
    for name in ("COMPARISON_ENRICHMENT_MAX_OUTPUT_TOKENS", "COMPARISON_ENRICHMENT_THINKING_LEVEL",
                 "COMPARISON_ENRICHMENT_TEMPERATURE", "COMPARISON_ENRICHMENT_URL_CONTEXT"):
        monkeypatch.delenv(name, raising=False)
    provider = GeminiOfficialEnrichmentProvider(client=None, model_id="gemini-3.1-pro-preview", timeout_sec=125)
    cfg = provider._config(("technical",), "ev")
    assert cfg.http_options.timeout == 125_000  # milliseconds, enforced by the SDK's HTTP client
    assert cfg.http_options.retry_options.attempts == 1  # no SDK retry
    assert cfg.thinking_config.thinking_level == genai_types.ThinkingLevel.LOW
    assert cfg.tools[0].google_search is not None  # Google Search grounding stays enabled
    assert cfg.tools[1].url_context is not None  # seed URLs / PDFs can actually be opened
    assert cfg.response_mime_type == "application/json"
    fields = set(cfg.response_json_schema["properties"]["claims"]["items"]["properties"]["field"]["enum"])
    assert "battery_capacity_kwh" in fields and "official_price_ils" not in fields  # task-scoped schema
    assert not fields & {"horsepower", "engine_cc", "seats", "doors"}  # no wasted cross-check searches
    assert cfg.max_output_tokens == 32768  # explicit ceiling (thinking counts toward it)
    assert cfg.automatic_function_calling.disable is True  # SDK AFC loop is irrelevant to Google Search
    assert cfg.temperature is None  # Gemini 3 default (low temperature can loop)


def test_default_timeout_is_live_window(monkeypatch):
    monkeypatch.delenv("COMPARISON_ENRICHMENT_TIMEOUT_SEC", raising=False)
    provider = GeminiOfficialEnrichmentProvider(client=None, model_id="gemini-3.8-flash")
    assert 120 <= provider.timeout_sec <= 130


# --------------------------------------------------------------------------
# structured output + grounding from SDK-shaped responses
# --------------------------------------------------------------------------
def test_native_parsed_output_is_used():
    resp = sdk_response([json.dumps({"claims": [BMW_CLAIM]})])
    assert isinstance(resp.parsed, dict)
    data, info = extract_structured_output(resp)
    assert info["source"] == "native_parsed" and data["claims"][0]["field"] == "torque_nm"


def test_text_fallback_when_parsed_missing_strict_only():
    fenced = "```json\n" + json.dumps({"claims": [BMW_CLAIM]}) + "\n```"
    resp = sdk_response([fenced])
    assert resp.parsed is None  # SDK could not json.loads the fenced text
    data, info = extract_structured_output(resp)
    assert info["source"] == "text" and data["claims"][0]["value"] == 400


def test_multiple_json_parts_use_last_complete_object():
    resp = sdk_response([json.dumps({"claims": []}), json.dumps({"claims": [BMW_CLAIM]})])
    data, info = extract_structured_output(resp)
    assert info["source"] == "text_part" and len(data["claims"]) == 1


def test_truncated_json_is_not_repaired():
    resp = sdk_response(['{"claims": [{"field": "torque_nm", "value": 4'], finish="MAX_TOKENS")
    data, info = extract_structured_output(resp)
    assert data is None and info["parser_reason"] == "JSON_DECODE_ERROR"


def test_grounding_sources_extracted_from_sdk_shape():
    resp = sdk_response(["{}"], chunks=(("bmw.co.il", "bmw.co.il"), ("www.bmw.com", None)))
    sources = extract_grounded_sources(resp)
    assert [s["domain"] for s in sources] == ["bmw.co.il", None]
    assert sources[1]["title"] == "www.bmw.com"
    assert all(s["uri"].startswith(REDIRECT) for s in sources)


def test_valid_live_shaped_response_yields_accepted_fact():
    client = FakeClient(lambda prompt: sdk_response([json.dumps({"claims": [BMW_CLAIM]})]))
    repo = LiveOfficialEnrichmentRepository(GeminiOfficialEnrichmentProvider(client, "gemini-3.8-flash", 125), InProcessEnrichmentCache())
    outcome, meta = enrich_many(repo, [snap(BMW_I4)])[0]
    assert outcome["status"] == "enriched"
    assert outcome["facts"]["torque_nm"]["value"] == 400
    assert outcome["facts"]["torque_nm"]["grounding_tier"] == "site"  # www.bmw.co.il cited, Search names bmw.co.il
    assert meta["grounded_source_count"] == 2 and meta["parse_source"] == ["native_parsed", "native_parsed"]
    assert len(client.models.calls) == 2  # technical + commercial task


# --------------------------------------------------------------------------
# INVALID_JSON diagnostics, no repair call
# --------------------------------------------------------------------------
def test_invalid_json_diagnostics_are_useful_and_safe(caplog):
    leaked = "AIzaSyD" + "x" * 30
    bad = "Here are the specs I found " + leaked + " ... not json"
    client = FakeClient(lambda prompt: sdk_response([bad], chunks=None))
    provider = GeminiOfficialEnrichmentProvider(client, "gemini-3.8-flash", 125)
    with caplog.at_level("WARNING", logger="comparison_v2"):
        result = provider.enrich(snap(AUDI_Q3), ("technical",))
    assert result["error_code"] == "INVALID_JSON"
    diag = result["diagnostics"]
    for key in ("model", "finish_reason", "candidate_count", "response_text_length", "native_parsed_present",
                "text_head", "text_tail", "grounding_metadata_present", "grounding_chunk_count", "parser_reason"):
        assert key in diag, key
    assert diag["finish_reason"] == "STOP" and diag["candidate_count"] == 1
    assert diag["grounding_metadata_present"] is False and diag["grounding_chunk_count"] == 0
    assert diag["native_parsed_present"] is False
    assert leaked not in json.dumps(diag) and "[REDACTED]" in diag["text_head"]
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "vehicle_enrichment_unusable_response" in logged and "error=INVALID_JSON" in logged
    assert leaked not in logged
    assert "ROLE: You extract" not in logged  # never the prompt


def test_invalid_json_makes_no_second_model_call():
    client = FakeClient(lambda prompt: sdk_response(["not json at all"]))
    repo = LiveOfficialEnrichmentRepository(GeminiOfficialEnrichmentProvider(client, "gemini-3.8-flash", 125), InProcessEnrichmentCache())
    results = enrich_many(repo, [snap(AUDI_Q3), snap(BMW_I4)])
    assert len(client.models.calls) == 4  # exactly one per task (2 per car), no repair call
    for outcome, _ in results:
        assert outcome["status"] == "failed" and outcome["error_code"] == "INVALID_JSON"
        assert "text_head" not in outcome["provider_diagnostics"]  # fragments stay in logs
        assert outcome["provider_diagnostics"]["parser_reason"] == "JSON_DECODE_ERROR"


def test_invalid_json_diagnostics_unit_with_block_reason():
    rest = {"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}, "modelVersion": "gemini-3.8-flash"}
    resp = genai_types.GenerateContentResponse._from_response(response=rest, kwargs={})
    diag = invalid_json_diagnostics(resp, "gemini-3.8-flash", {"native_parsed_present": False, "parser_reason": "EMPTY_TEXT"})
    assert diag["candidate_count"] == 0 and diag["prompt_block_reason"] == "SAFETY"


def test_sdk_timeout_exception_maps_to_call_timeout():
    import httpx

    def boom(prompt):
        raise httpx.ReadTimeout("timed out")

    provider = GeminiOfficialEnrichmentProvider(FakeClient(boom), "gemini-3.8-flash", 125)
    result = provider.enrich(snap(AUDI_Q3), ("technical",))
    assert result["error_code"] == "CALL_TIMEOUT"


# --------------------------------------------------------------------------
# per-car timeout windows
# --------------------------------------------------------------------------
class SleepyProvider:
    name = "fake"
    model_id = "gemini-3.8-flash"

    def __init__(self, delays, timeout_sec):
        self.delays = delays
        self.timeout_sec = timeout_sec
        self.calls = []
        self.lock = threading.Lock()

    def enrich(self, snapshot, groups):
        with self.lock:
            self.calls.append(snapshot["vehicle_id"])
        time.sleep(self.delays[snapshot["vehicle_id"]])
        return {"raw": {"claims": []}, "grounded_sources": [], "error_code": None, "model": self.model_id, "duration_ms": 1}


def _repo(provider):
    return LiveOfficialEnrichmentRepository(provider, InProcessEnrichmentCache())


def test_each_car_gets_its_own_full_window_when_started_later():
    # One worker: the second car only starts after the first finishes. With a
    # shared deadline it would inherit ~0.15s and time out; it must get 0.6s.
    provider = SleepyProvider({AUDI_Q3: 0.45, BMW_I4: 0.45}, timeout_sec=0.6)
    out = enrich_many(_repo(provider), [snap(AUDI_Q3), snap(BMW_I4)], max_workers=1, timeout_sec=0.6, poll_interval=0.02)
    assert [o["status"] for o, _ in out] == ["enriched", "enriched"]
    assert len(provider.calls) == 4  # 2 tasks per car, strictly sequential, none timed out


def test_slow_second_car_not_prematurely_timed_out():
    provider = SleepyProvider({AUDI_Q3: 0.05, BMW_I4: 0.5}, timeout_sec=0.7)
    out = enrich_many(_repo(provider), [snap(AUDI_Q3), snap(BMW_I4)], max_workers=2, timeout_sec=0.7, poll_interval=0.02)
    assert [o["status"] for o, _ in out] == ["enriched", "enriched"]


def test_timed_out_call_is_abandoned_not_awaited():
    provider = SleepyProvider({AUDI_Q3: 0.05, BMW_I4: 3.0}, timeout_sec=0.3)
    started = time.monotonic()
    out = enrich_many(_repo(provider), [snap(AUDI_Q3), snap(BMW_I4)], max_workers=2, timeout_sec=0.3, poll_interval=0.02)
    elapsed = time.monotonic() - started
    assert out[0][0]["status"] == "enriched"
    assert out[1][0]["status"] == "failed" and out[1][0]["error_code"] == "CALL_TIMEOUT"
    assert elapsed < 1.5  # did not wait for the 3s background call


def test_max_one_enrichment_call_per_car_three_cars():
    client = FakeClient(lambda prompt: sdk_response([json.dumps({"claims": []})]))
    repo = LiveOfficialEnrichmentRepository(GeminiOfficialEnrichmentProvider(client, "gemini-3.8-flash", 125), InProcessEnrichmentCache())
    from comparison_v2_fakes import HYUNDAI_TUCSON

    enrich_many(repo, [snap(AUDI_Q3), snap(BMW_I4), snap(HYUNDAI_TUCSON)])
    assert len(client.models.calls) == 6  # one technical + one commercial task per car
    prompts = [c["contents"] for c in client.models.calls]
    assert sum("Q3" in p for p in prompts) == 2 and sum("I4 EDRIVE35" in p for p in prompts) == 2
    assert all(("Q3" in p) + ("I4 EDRIVE35" in p) + ("TUCSON" in p) == 1 for p in prompts)  # one vehicle per prompt


@pytest.mark.parametrize("host", ["bmw.co.il.evil.com", "car-review.example"])
def test_grounding_does_not_weaken_domain_enforcement(host):
    claim = dict(BMW_CLAIM, source_url=f"https://{host}/i4")
    client = FakeClient(lambda prompt: sdk_response([json.dumps({"claims": [claim]})], chunks=((host, host),)))
    repo = LiveOfficialEnrichmentRepository(GeminiOfficialEnrichmentProvider(client, "gemini-3.8-flash", 125), InProcessEnrichmentCache())
    outcome, _ = enrich_many(repo, [snap(BMW_I4)])[0]
    assert outcome["facts"] == {}
    assert outcome["rejected_claims"][0]["reason"] == "SOURCE_DOMAIN_NOT_ALLOWED"
