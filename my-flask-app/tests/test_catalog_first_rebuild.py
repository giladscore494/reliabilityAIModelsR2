# -*- coding: utf-8 -*-
"""Tests for the catalog-first rebuild of the review and comparison tools.

Covers PART 10 acceptance items: catalog resolver behaviour, prompt rules,
grounded model calls (3.5 fast + Google Search), identity locking, cache key
binding, Stage B non-grounding, and UI defaults.
"""

import json
import types

import pytest

import app.extensions as extensions
from app.services.vehicle_catalog_service import (
    get_catalog_hash,
    get_vehicle_catalog_ui_data,
    resolve_comparison_car,
    resolve_vehicle_selection,
)


# --------------------------------------------------------------------------
# Fake Gemini client helpers
# --------------------------------------------------------------------------
class _FakeGM:
    def __init__(self, queries, chunks):
        self.web_search_queries = queries
        self.grounding_chunks = chunks


class _FakeCandidate:
    def __init__(self, gm):
        self.grounding_metadata = gm


class _FakeResp:
    def __init__(self, text, grounded=True):
        self.text = text
        if grounded:
            self.candidates = [_FakeCandidate(_FakeGM(["query 1"], [object(), object()]))]
        else:
            self.candidates = [_FakeCandidate(None)]


class _CapturingModels:
    """Captures the GenerateContentConfig passed to generate_content."""

    def __init__(self, text, grounded=True):
        self.text = text
        self.grounded = grounded
        self.last_model = None
        self.last_config = None

    def generate_content(self, model=None, contents=None, config=None):
        self.last_model = model
        self.last_config = config
        return _FakeResp(self.text, grounded=self.grounded)


class _FakeClient:
    def __init__(self, text, grounded=True):
        self.models = _CapturingModels(text, grounded=grounded)


def _config_has_search_tool(config) -> bool:
    tools = getattr(config, "tools", None) or []
    for tool in tools:
        if getattr(tool, "google_search", None) is not None:
            return True
    return False


def _first_multi_variant_model():
    ui = get_vehicle_catalog_ui_data()
    for make, models in ui.items():
        for model, info in models.items():
            if len(info["variants"]) >= 2:
                return make, model, info["variants"]
    raise AssertionError("no multi-variant model in catalog")


def _first_single_variant_model():
    ui = get_vehicle_catalog_ui_data()
    for make, models in ui.items():
        for model, info in models.items():
            if len(info["variants"]) == 1:
                return make, model, info["variants"][0]
    raise AssertionError("no single-variant model in catalog")


# --------------------------------------------------------------------------
# 1 + 2 — Catalog resolver
# --------------------------------------------------------------------------
def test_resolver_exact_variant_id():
    make, model, variants = _first_multi_variant_model()
    vid = variants[0]["variant_id"]
    res = resolve_vehicle_selection({"make": make, "model": model, "variant_id": vid})
    assert res["resolution_status"] == "exact"
    assert res["variant_id"] == vid
    assert res["make"] == make and res["model"] == model
    assert res["profile_confidence"] == "high"
    # identity comes from the catalog variant, not user text
    assert res["fuel_type"] == variants[0]["fuel_type"]


def test_resolver_ambiguous_without_variant_id():
    make, model, variants = _first_multi_variant_model()
    res = resolve_vehicle_selection({"make": make, "model": model})
    assert res["resolution_status"] == "ambiguous"
    assert len(res["ambiguity_options"]) >= 2
    assert all(opt.get("variant_id") for opt in res["ambiguity_options"])


def test_resolver_inferred_single_variant():
    make, model, _variant = _first_single_variant_model()
    res = resolve_vehicle_selection({"make": make, "model": model})
    assert res["resolution_status"] in ("inferred", "exact")
    assert res["variant_id"]


def test_resolver_unmatched_and_bad_variant_id():
    assert resolve_vehicle_selection({"make": "Nope", "model": "Ghost", "year": 2020})["resolution_status"] == "unmatched"
    make, model, _v = _first_multi_variant_model()
    bad = resolve_vehicle_selection({"make": make, "model": model, "variant_id": "deadbeefdeadbeef"})
    assert bad["resolution_status"] == "unmatched"


def test_resolve_comparison_car_is_resolver():
    make, model, variants = _first_multi_variant_model()
    res = resolve_comparison_car({"make": make, "model": model, "variant_id": variants[0]["variant_id"]})
    assert res["resolution_status"] == "exact"


# --------------------------------------------------------------------------
# 3 — Review prompt
# --------------------------------------------------------------------------
def test_review_prompt_is_catalog_first():
    from app.services.reliability_prompt_service import build_combined_prompt

    make, model, variants = _first_multi_variant_model()
    payload = {"make": make, "model": model, "year": variants[0]["year_start"], "variant_id": variants[0]["variant_id"]}
    prompt = build_combined_prompt(payload, [])
    assert "LOCKED_CATALOG_IDENTITY" in prompt
    assert "Google Search" in prompt
    assert "JSON" in prompt
    # The model must NOT be asked to decide identity.
    assert "אל תשנה את שדות הזהות" in prompt
    # No numeric scores / no buy verdict instructions present.
    assert "אסור ציון אמינות מספרי" in prompt
    # The locked identity block carries the real catalog fuel_type.
    assert variants[0]["fuel_type"] in prompt


# --------------------------------------------------------------------------
# 4 — Review model call: 3.5 fast + Google Search
# --------------------------------------------------------------------------
def test_review_call_uses_flash_and_search(monkeypatch):
    from app.services import reliability_model_service as rms

    fake = _FakeClient(json.dumps({"ok": True, "overview": {"plain_summary": "x"}}), grounded=True)
    monkeypatch.setattr(extensions, "ai_client", fake)
    parsed, err = rms.call_gemini_grounded_once("prompt")
    assert err is None
    assert "flash" in fake.models.last_model.lower()
    assert _config_has_search_tool(fake.models.last_config)
    # response_mime_type must NOT be combined with grounding tools
    assert getattr(fake.models.last_config, "response_mime_type", None) in (None, "")
    # grounding metadata recorded honestly
    assert parsed["_grounding_meta"]["grounding_successful"] is True
    assert parsed["_grounding_meta"]["source_count"] == 2


def test_extract_grounding_meta_false_when_no_grounding():
    from app.services.reliability_model_service import extract_grounding_meta

    meta = extract_grounding_meta(_FakeResp("{}", grounded=False))
    assert meta["grounding_successful"] is False
    assert meta["source_count"] == 0


# --------------------------------------------------------------------------
# 5 + 11 — Identity locking: catalog wins over AI
# --------------------------------------------------------------------------
def test_catalog_identity_overrides_ai():
    from app.services.analyze_service import _enforce_catalog_identity

    make, model, variants = _first_multi_variant_model()
    resolution = resolve_vehicle_selection({"make": make, "model": model, "variant_id": variants[0]["variant_id"]})
    ai_output = {"identity_snapshot": {"make": "WRONG", "fuel_type": "rocket-fuel", "horsepower_hp": 99999}}
    out = _enforce_catalog_identity(ai_output, resolution, "req-1")
    snap = out["identity_snapshot"]
    assert snap["source"] == "catalog"
    assert snap["make"] == make
    assert snap["fuel_type"] == variants[0]["fuel_type"]
    assert snap["variant_id"] == variants[0]["variant_id"]
    assert out["catalog_resolution"]["resolution_status"] == "exact"


def test_research_status_is_honest():
    from app.services.analyze_service import _build_research_status

    grounded = _build_research_status({"grounding_successful": True, "source_count": 3}, {})
    assert grounded["web_search_performed"] is True
    assert grounded["grounding_successful"] is True
    assert grounded["source_count"] == 3

    degraded = _build_research_status({"grounding_successful": False, "source_count": 0}, {})
    assert degraded["web_search_performed"] is False
    assert degraded["limitations"]


# --------------------------------------------------------------------------
# 6 — Comparison Stage A: 3.5 fast + Google Search + tools_enabled
# --------------------------------------------------------------------------
def test_stage_a_uses_flash_and_search(app, monkeypatch):
    from app.services.comparison import grounding

    payload = json.dumps({"car_profile": {"pricing": {"used_price_range_ils": "x"}}, "sources": ["http://e.x"]})
    fake = _FakeClient(payload, grounded=True)
    monkeypatch.setattr(extensions, "ai_client", fake)
    with app.app_context():
        parsed, err = grounding.call_gemini_single_car("prompt", "car_1", 30, "req", None)
    assert err is None
    assert "flash" in fake.models.last_model.lower()
    assert _config_has_search_tool(fake.models.last_config)
    assert getattr(fake.models.last_config, "response_mime_type", None) in (None, "")
    assert parsed["_grounding_meta"]["grounding_successful"] is True


def test_stage_a_parallel_sets_honest_grounding(app, monkeypatch):
    from app.services.comparison import grounding

    payload = json.dumps({"car_profile": {"pricing": {"used_price_range_ils": "x"}}, "sources": ["http://e.x"]})
    fake = _FakeClient(payload, grounded=True)
    monkeypatch.setattr(extensions, "ai_client", fake)
    cars = [{"make": "Toyota", "model": "Corolla", "year": 2020}, {"make": "Mazda", "model": "3", "year": 2020}]
    slots = {"car_1": {"display_name": "Toyota Corolla 2020"}, "car_2": {"display_name": "Mazda 3 2020"}}
    with app.app_context():
        merged, _idx, errors = grounding.call_stage_a_parallel(cars, slots)
    assert merged["grounding_successful"] is True
    assert merged["research_status"]["web_search_performed"] is True


# --------------------------------------------------------------------------
# 7 — Stage B writer does NOT use Google Search
# --------------------------------------------------------------------------
def test_stage_b_writer_has_no_search_tool(app, monkeypatch):
    from app.services.comparison import writer

    fake = _FakeClient(json.dumps({"decision_result": {}}), grounded=False)
    monkeypatch.setattr(extensions, "ai_client", fake)
    with app.app_context():
        writer.call_gemini_compare_writer("prompt")
    assert not _config_has_search_tool(fake.models.last_config)


# --------------------------------------------------------------------------
# 8 — Comparison cache key binding
# --------------------------------------------------------------------------
def test_cache_key_includes_variant_and_catalog_hash(app):
    from app.services.comparison.cache import compute_request_hash

    with app.app_context():
        base = [{"make": "Toyota", "model": "Corolla", "year": 2020}, {"make": "Mazda", "model": "3", "year": 2020}]
        h_no_variant = compute_request_hash(base)
        with_variant = [dict(base[0], variant_id="abc123"), base[1]]
        h_variant = compute_request_hash(with_variant)
        assert h_no_variant != h_variant  # variant_id changes the hash
        # model id participates in the key
        assert "model_id" in _cache_data_keys()


def _cache_data_keys():
    # The cache "data" dict keys are an internal contract; assert presence via source.
    import inspect
    from app.services.comparison import cache

    return inspect.getsource(cache.compute_request_hash)


def test_cache_key_changes_with_catalog_hash(app, monkeypatch):
    from app.services.comparison import cache

    with app.app_context():
        base = [{"make": "Toyota", "model": "Corolla", "year": 2020}, {"make": "Mazda", "model": "3", "year": 2020}]
        h1 = cache.compute_request_hash(base)
        monkeypatch.setattr(
            "app.services.vehicle_catalog_service.get_catalog_generation_meta",
            lambda: {"catalog_hash": "DIFFERENT", "generated_at": "2099-01-01"},
        )
        h2 = cache.compute_request_hash(base)
        assert h1 != h2


# --------------------------------------------------------------------------
# 9 — UI: no silent gasoline/automatic defaults
# --------------------------------------------------------------------------
def test_compare_ui_has_no_silent_defaults():
    import pathlib

    html = pathlib.Path(__file__).resolve().parents[1].joinpath("templates", "compare.html").read_text(encoding="utf-8")
    assert "|| 'בנזין'" not in html
    assert "|| 'אוטומטית'" not in html
    assert '<option value="בנזין" selected>' not in html
    assert '<option value="אוטומטית" selected>' not in html


def test_reliability_ui_has_no_silent_defaults():
    import pathlib

    html = pathlib.Path(__file__).resolve().parents[1].joinpath("templates", "reliability_app.html").read_text(encoding="utf-8")
    assert "<option selected>בנזין</option>" not in html
    assert "<option selected>אוטומטית</option>" not in html


# --------------------------------------------------------------------------
# 10 — checked_versions reflect catalog identity for an exact match
# --------------------------------------------------------------------------
def test_checked_versions_use_catalog_identity():
    from app.services.comparison.normalization import build_checked_versions

    make, model, variants = _first_multi_variant_model()
    vid = variants[0]["variant_id"]
    slots = {"car_1": {"make": make, "model": model, "variant_id": vid, "display_name": f"{make} {model}"}}
    checked = build_checked_versions(slots, {"cars": {}}, None)
    cv = checked["car_1"]
    assert cv["make"] == make
    assert cv["data_basis"] == "verified_source"
    assert cv["confidence"] == "high"
