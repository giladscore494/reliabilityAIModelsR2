# -*- coding: utf-8 -*-
"""Compatibility layer for comparison service.

Real implementation lives under app.services.comparison.*
"""

import app.extensions as extensions
from google.genai import types as genai_types

from app.utils.ai_guardrails import apply_feature_guardrails
from app.utils.http_helpers import _utcnow, api_error, api_ok, get_request_id

from app.services.comparison.cache import _safe_json_obj, _safe_parse_json_cached, compute_request_hash
from app.services.comparison.computation import (
    build_sources_index,
    build_sources_index_from_flat,
    compute_comparison_results,
    normalize_model_output,
)
from app.services.comparison.constants import *  # noqa: F403
from app.services.comparison.decision import *  # noqa: F403
from app.services.comparison.fallbacks import *  # noqa: F403
from app.services.comparison.fallbacks import _empty_stage_a_output
from app.services.comparison.grounding import (
    build_sources_index as _build_sources_index_impl,
    build_sources_index_from_flat as _build_sources_index_from_flat_impl,
    call_gemini_comparison as _ground_call_gemini_comparison,
    call_gemini_single_car as _ground_call_gemini_single_car,
    call_stage_a_parallel as _call_stage_a_parallel_impl,
    parse_single_car_json,
    parse_stage_a_json,
)
from app.services.comparison import history as _history
from app.services.comparison import pipeline as _pipeline
from app.services.comparison.metrics import *  # noqa: F403
from app.services.comparison.model_calls import *  # noqa: F403
from app.services.comparison.normalization import *  # noqa: F403
from app.services.comparison.parsing import *  # noqa: F403
from app.services.comparison.pipeline import enforce_authoritative_numbers
from app.services.comparison.prompts import *  # noqa: F403
from app.services.comparison.schemas import *  # noqa: F403
from app.services.comparison.scoring import *  # noqa: F403
from app.services.comparison.writer import *  # noqa: F403


def build_sources_index(model_output):
    return _build_sources_index_impl(model_output)


def build_sources_index_from_flat(merged_output):
    return _build_sources_index_from_flat_impl(merged_output)


def call_gemini_comparison(prompt, timeout_sec=COMPARE_STAGE_A_TIMEOUT_SEC):  # noqa: F405
    return _ground_call_gemini_comparison(prompt, timeout_sec)


def call_gemini_single_car(
    prompt,
    car_label,
    timeout_sec=COMPARE_STAGE_A_TIMEOUT_SEC,  # noqa: F405
    request_id=None,
    log=None,
):
    return _ground_call_gemini_single_car(prompt, car_label, timeout_sec, request_id, log)


def call_stage_a_parallel(validated_cars, cars_selected_slots):
    """Compatibility wrapper to preserve monkeypatch behavior in legacy tests."""
    from app.services.comparison import grounding

    original = grounding.call_gemini_single_car
    if call_gemini_single_car is original:
        return _call_stage_a_parallel_impl(validated_cars, cars_selected_slots)
    grounding.call_gemini_single_car = call_gemini_single_car
    try:
        return _call_stage_a_parallel_impl(validated_cars, cars_selected_slots)
    finally:
        grounding.call_gemini_single_car = original


def handle_comparison_request(data, user_id, session_id, owner_bypass=False):
    original_stage_a = _pipeline.call_stage_a_parallel
    original_stage_b = _pipeline.call_gemini_compare_writer
    _pipeline.call_stage_a_parallel = call_stage_a_parallel
    _pipeline.call_gemini_compare_writer = call_gemini_compare_writer
    try:
        return _pipeline.handle_comparison_request(data, user_id, session_id, owner_bypass)
    finally:
        _pipeline.call_stage_a_parallel = original_stage_a
        _pipeline.call_gemini_compare_writer = original_stage_b


def get_comparison_history(user_id, limit=10):
    return _history.get_comparison_history(user_id, limit)


def get_comparison_detail(comparison_id, user_id):
    return _history.get_comparison_detail(comparison_id, user_id)


def regenerate_comparison_ai(comparison_id, user_id):
    original_stage_b = _history.call_gemini_compare_writer
    _history.call_gemini_compare_writer = call_gemini_compare_writer
    try:
        return _history.regenerate_comparison_ai(comparison_id, user_id)
    finally:
        _history.call_gemini_compare_writer = original_stage_b
