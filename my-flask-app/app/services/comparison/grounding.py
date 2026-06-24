"""Stage A — grounded model calls and parsing."""

import json
import logging
import time as pytime
from typing import Any, Dict, List, Optional, Tuple

import app.extensions as extensions
from flask import current_app
from google.genai import types as genai_types

from app.services.comparison.constants import (
    COMPARISON_MODEL_ID,
    COMPARE_STAGE_A_MAX_OUTPUT_TOKENS,
    COMPARE_STAGE_A_TEMPERATURE,
    COMPARE_STAGE_A_TIMEOUT_SEC,
    PARALLEL_GRACE_SEC,
    _MAX_STAGE_A_SOURCES,
)
from app.services.comparison.metrics import _inc_compare_metric
from app.services.comparison.model_calls import (
    _estimate_token_count,
    _is_output_too_long_error,
    _log_ai_client_error,
)
from app.services.comparison.parsing import (
    _extract_first_json_object,
    _is_valid_single_car_payload,
    _is_valid_stage_a_payload,
    _repair_json_once,
    _strip_json_code_fences,
    _truncate_error_message,
    normalize_single_car_payload,
)
from app.services.comparison.prompts import build_single_car_prompt
from app.services.comparison.schemas import validate_grounding
from app.services.comparison.fallbacks import _empty_stage_a_output
from app.utils.http_helpers import get_request_id

logger = logging.getLogger(__name__)

def parse_single_car_json(raw_text: str) -> Tuple[Optional[Dict], Optional[str]]:
    """Parse and validate single-car JSON response."""
    candidate = _extract_first_json_object(_strip_json_code_fences(raw_text))
    for current in (candidate, _repair_json_once(candidate) if candidate else None):
        if not current:
            continue
        try:
            parsed = json.loads(current)
        except json.JSONDecodeError:
            snippet = (current or "")[:500].replace("\n", " ")
            logger.warning(
                "[COMPARISON] stage_a_json_rejected reason=json_decode_error snippet=%.500s",
                snippet,
            )
            continue
        if _is_valid_single_car_payload(parsed):
            normalized = normalize_single_car_payload(parsed)
            if normalized is not None:
                return normalized, None
        else:
            top_keys = list(parsed.keys())[:20] if isinstance(parsed, dict) else None
            snippet = (current or "")[:500].replace("\n", " ")
            logger.warning(
                "[COMPARISON] stage_a_json_rejected reason=validation_failed top_keys=%s snippet=%.500s",
                top_keys,
                snippet,
            )
    return None, "MODEL_JSON_INVALID"


def parse_stage_a_json(raw_text: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    candidate = _extract_first_json_object(_strip_json_code_fences(raw_text))
    for current in (candidate, _repair_json_once(candidate) if candidate else None):
        if not current:
            continue
        try:
            parsed = json.loads(current)
        except json.JSONDecodeError:
            snippet = (current or "")[:500].replace("\n", " ")
            logger.warning(
                "[COMPARISON] stage_a_json_rejected reason=json_decode_error snippet=%.500s",
                snippet,
            )
            continue
        if _is_valid_stage_a_payload(parsed):
            return parsed, None
        else:
            top_keys = list(parsed.keys())[:20] if isinstance(parsed, dict) else None
            snippet = (current or "")[:500].replace("\n", " ")
            logger.warning(
                "[COMPARISON] stage_a_json_rejected reason=validation_failed top_keys=%s snippet=%.500s",
                top_keys,
                snippet,
            )
    return None, "MODEL_JSON_INVALID"


def call_gemini_comparison(
    prompt: str, timeout_sec: int = COMPARE_STAGE_A_TIMEOUT_SEC
) -> Tuple[Optional[Dict], Optional[str]]:
    """
    Call Gemini 3 Flash with web grounding for comparison data.
    Returns (parsed_output, error_string).
    """
    import concurrent.futures
    from app.factory import AI_EXECUTOR, AI_EXECUTOR_WORKERS

    start_time = pytime.perf_counter()
    prompt_chars = len(prompt or "")
    outcome = "ok"
    outcome_reason = None
    try:
        if extensions.ai_client is None:
            outcome = "error"
            outcome_reason = "CLIENT_NOT_INITIALIZED"
            return None, "CLIENT_NOT_INITIALIZED"

        config_kwargs = {
            "temperature": COMPARE_STAGE_A_TEMPERATURE,
            "top_p": 0.8,
            "top_k": 20,
            "max_output_tokens": COMPARE_STAGE_A_MAX_OUTPUT_TOKENS,
            "response_mime_type": "application/json",
        }
        config = genai_types.GenerateContentConfig(**config_kwargs)

        def _invoke():
            return extensions.ai_client.models.generate_content(
                model=COMPARISON_MODEL_ID,
                contents=prompt,
                config=config,
            )

        # Check executor availability
        work_queue = getattr(AI_EXECUTOR, "_work_queue", None)
        if work_queue is not None:
            queued = work_queue.qsize()
            if queued >= AI_EXECUTOR_WORKERS:
                outcome = "error"
                outcome_reason = "SERVER_BUSY"
                return None, "SERVER_BUSY"

        try:
            future = AI_EXECUTOR.submit(_invoke)
        except Exception:
            outcome = "error"
            outcome_reason = "EXECUTOR_SATURATED"
            return None, "EXECUTOR_SATURATED"

        try:
            resp = future.result(timeout=timeout_sec)
        except concurrent.futures.TimeoutError:
            future.cancel()
            _inc_compare_metric("compare_ai_failures_total", reason="timeout")
            _inc_compare_metric("compare_stage_a_timeout_total")
            outcome = "timeout"
            outcome_reason = "CALL_TIMEOUT"
            return None, "CALL_TIMEOUT"
        except Exception as e:
            _log_ai_client_error("comparison_stage_a", e)
            _inc_compare_metric("compare_stage_a_error_total")
            outcome = "error"
            outcome_reason = type(e).__name__
            if _is_output_too_long_error(str(e)):
                return None, "CALL_FAILED_OUTPUT_TOO_LONG"
            return None, f"CALL_FAILED:{type(e).__name__}"

        if resp is None:
            outcome = "error"
            outcome_reason = "CALL_FAILED_EMPTY"
            return None, "CALL_FAILED:EMPTY"

        text = (getattr(resp, "text", "") or "").strip()
        if not text:
            outcome = "error"
            outcome_reason = "EMPTY_RESPONSE"
            return None, "EMPTY_RESPONSE"

        parsed, parse_error = parse_stage_a_json(text)
        if parse_error:
            outcome = "error"
            outcome_reason = parse_error
            _inc_compare_metric("compare_stage_a_json_invalid_total")
            return None, parse_error
        return parsed, None

    finally:
        duration_ms = (pytime.perf_counter() - start_time) * 1000
        current_app.logger.info(
            "[AI] feature=comparison_stage_a model=%s duration_ms=%.2f prompt_chars=%s prompt_tokens_est=%s max_output_tokens=%s timeout_ms=%s tools_enabled=%s retry_count=%s outcome=%s reason=%s",
            COMPARISON_MODEL_ID,
            duration_ms,
            prompt_chars,
            _estimate_token_count(prompt),
            COMPARE_STAGE_A_MAX_OUTPUT_TOKENS,
            int(timeout_sec * 1000),
            False,
            0,
            outcome,
            outcome_reason,
        )


def call_gemini_single_car(
    prompt: str,
    car_label: str,
    timeout_sec: int = COMPARE_STAGE_A_TIMEOUT_SEC,
    request_id: Optional[str] = None,
    log: Optional[logging.Logger] = None,
) -> Tuple[Optional[Dict], Optional[str]]:
    """Call Gemini for a single car. Returns (parsed_dict, error_string)."""
    import concurrent.futures
    from app.factory import AI_EXECUTOR, AI_EXECUTOR_WORKERS

    start_time = pytime.perf_counter()
    prompt_chars = len(prompt or "")
    outcome = "ok"
    outcome_reason = None
    worker_logger = log or logger
    try:
        if extensions.ai_client is None:
            outcome = "error"
            outcome_reason = "CLIENT_NOT_INITIALIZED"
            return None, "CLIENT_NOT_INITIALIZED"

        config_kwargs = {
            "temperature": COMPARE_STAGE_A_TEMPERATURE,
            "top_p": 0.8,
            "top_k": 20,
            "max_output_tokens": COMPARE_STAGE_A_MAX_OUTPUT_TOKENS,
            "response_mime_type": "application/json",
        }
        config = genai_types.GenerateContentConfig(**config_kwargs)

        def _invoke():
            return extensions.ai_client.models.generate_content(
                model=COMPARISON_MODEL_ID,
                contents=prompt,
                config=config,
            )

        work_queue = getattr(AI_EXECUTOR, "_work_queue", None)
        if work_queue is not None:
            queued = work_queue.qsize()
            if queued >= AI_EXECUTOR_WORKERS:
                outcome = "error"
                outcome_reason = "SERVER_BUSY"
                return None, "SERVER_BUSY"

        try:
            future = AI_EXECUTOR.submit(_invoke)
        except Exception:
            outcome = "error"
            outcome_reason = "EXECUTOR_SATURATED"
            return None, "EXECUTOR_SATURATED"

        try:
            resp = future.result(timeout=timeout_sec)
        except concurrent.futures.TimeoutError:
            future.cancel()
            _inc_compare_metric("compare_ai_failures_total", reason="timeout")
            _inc_compare_metric("compare_stage_a_timeout_total")
            outcome = "timeout"
            outcome_reason = "CALL_TIMEOUT"
            return None, "CALL_TIMEOUT"
        except Exception as e:
            _log_ai_client_error(
                "comparison_stage_a", e, request_id=request_id, log=worker_logger
            )
            _inc_compare_metric("compare_stage_a_error_total")
            outcome = "error"
            outcome_reason = type(e).__name__
            if _is_output_too_long_error(str(e)):
                return None, "CALL_FAILED_OUTPUT_TOO_LONG"
            return None, f"CALL_FAILED:{type(e).__name__}"

        if resp is None:
            outcome = "error"
            outcome_reason = "CALL_FAILED_EMPTY"
            return None, "CALL_FAILED:EMPTY"

        text = (getattr(resp, "text", "") or "").strip()
        if not text:
            outcome = "error"
            outcome_reason = "EMPTY_RESPONSE"
            return None, "EMPTY_RESPONSE"

        parsed, parse_error = parse_single_car_json(text)
        if parse_error:
            outcome = "error"
            outcome_reason = parse_error
            _inc_compare_metric("compare_stage_a_json_invalid_total")
            return None, parse_error
        return parsed, None

    finally:
        duration_ms = (pytime.perf_counter() - start_time) * 1000
        worker_logger.info(
            "[AI] feature=comparison_stage_a_per_car model=%s car=%s duration_ms=%.2f prompt_chars=%s prompt_tokens_est=%s max_output_tokens=%s timeout_ms=%s outcome=%s reason=%s",
            COMPARISON_MODEL_ID,
            car_label,
            duration_ms,
            prompt_chars,
            _estimate_token_count(prompt),
            COMPARE_STAGE_A_MAX_OUTPUT_TOKENS,
            int(timeout_sec * 1000),
            outcome,
            outcome_reason,
        )


def call_stage_a_parallel(
    validated_cars: List[Dict], cars_selected_slots: Dict
) -> Tuple[Dict, Dict, List[str]]:
    """
    Run Stage A for each car in parallel.
    Returns (merged_model_output, sources_index, errors_list).
    """
    import concurrent.futures
    from app.factory import AI_EXECUTOR

    slot_keys = list(cars_selected_slots.keys())
    prompts = {}
    for i, car in enumerate(validated_cars):
        slot_key = slot_keys[i]
        prompts[slot_key] = build_single_car_prompt(car)

    def _retry_prompt_for(slot_key: str) -> str:
        base_prompt = prompts.get(slot_key, "")
        return (
            f"{base_prompt}\n\n"
            "FINAL JSON REMINDER:\n"
            "- Return EXACTLY one JSON object.\n"
            "- The response must start with { and end with }.\n"
            "- Do not wrap the object in an array.\n"
            "- If data is missing, keep the key and use null instead of omitting it.\n"
        )

    def _store_slot_result(slot_key: str, result: Optional[Dict[str, Any]]) -> bool:
        normalized_result = normalize_single_car_payload(
            result,
            fallback_name=(cars_selected_slots.get(slot_key, {}) or {}).get(
                "display_name"
            ),
        )
        if normalized_result is None:
            return False
        car_sources = normalized_result.get("sources", [])
        merged["cars"][slot_key] = normalized_result
        merged["sources"].extend(car_sources)
        return True

    futures = {}
    request_id = get_request_id()
    stage_a_logger = current_app.logger
    for slot_key, prompt in prompts.items():
        futures[slot_key] = AI_EXECUTOR.submit(
            call_gemini_single_car,
            prompt,
            slot_key,
            COMPARE_STAGE_A_TIMEOUT_SEC,
            request_id,
            stage_a_logger,
        )

    merged = _empty_stage_a_output(cars_selected_slots)
    errors = []
    retry_slots = {}
    for slot_key, future in futures.items():
        try:
            result, error = future.result(
                timeout=COMPARE_STAGE_A_TIMEOUT_SEC + PARALLEL_GRACE_SEC
            )
            if error:
                if error == "MODEL_JSON_INVALID":
                    retry_slots[slot_key] = _retry_prompt_for(slot_key)
                else:
                    errors.append(f"{slot_key}: {error}")
                    stage_a_logger.warning(
                        "[COMPARISON] stage_a_slot_failed request_id=%s slot_key=%s error_class=StageAError error=%s",
                        request_id,
                        slot_key,
                        _truncate_error_message(error),
                    )
            else:
                if not _store_slot_result(slot_key, result):
                    retry_slots[slot_key] = _retry_prompt_for(slot_key)
        except concurrent.futures.TimeoutError as e:
            future.cancel()
            errors.append(f"{slot_key}: CALL_TIMEOUT")
            stage_a_logger.warning(
                "[COMPARISON] stage_a_slot_failed request_id=%s slot_key=%s error_class=TimeoutError error=%s",
                request_id,
                slot_key,
                _truncate_error_message(e),
            )
        except concurrent.futures.CancelledError as e:
            errors.append(f"{slot_key}: CALL_CANCELLED")
            stage_a_logger.warning(
                "[COMPARISON] stage_a_slot_failed request_id=%s slot_key=%s error_class=CancelledError error=%s",
                request_id,
                slot_key,
                _truncate_error_message(e),
            )
        except Exception as e:
            errors.append(f"{slot_key}: CALL_FAILED:{type(e).__name__}")
            stage_a_logger.error(
                "[COMPARISON] stage_a_slot_failed request_id=%s slot_key=%s error_class=%s error=%s",
                request_id,
                slot_key,
                type(e).__name__,
                _truncate_error_message(e),
            )

    if retry_slots:
        stage_a_logger.info(
            "[COMPARISON] stage_a_retrying_json_invalid request_id=%s slot_keys=%s",
            request_id,
            sorted(retry_slots.keys()),
        )
        retry_futures = {}
        for slot_key, prompt in retry_slots.items():
            retry_futures[slot_key] = AI_EXECUTOR.submit(
                call_gemini_single_car,
                prompt,
                slot_key,
                COMPARE_STAGE_A_TIMEOUT_SEC,
                request_id,
                stage_a_logger,
            )
        for slot_key, future in retry_futures.items():
            try:
                result, error = future.result(
                    timeout=COMPARE_STAGE_A_TIMEOUT_SEC + PARALLEL_GRACE_SEC
                )
                if error or not _store_slot_result(slot_key, result):
                    final_error = error or "MODEL_JSON_INVALID"
                    errors.append(f"{slot_key}: {final_error}")
                    stage_a_logger.warning(
                        "[COMPARISON] stage_a_slot_failed request_id=%s slot_key=%s error_class=StageAError error=%s retry=1",
                        request_id,
                        slot_key,
                        _truncate_error_message(final_error),
                    )
            except concurrent.futures.TimeoutError as e:
                future.cancel()
                errors.append(f"{slot_key}: CALL_TIMEOUT")
                stage_a_logger.warning(
                    "[COMPARISON] stage_a_slot_failed request_id=%s slot_key=%s error_class=TimeoutError error=%s retry=1",
                    request_id,
                    slot_key,
                    _truncate_error_message(e),
                )
            except concurrent.futures.CancelledError as e:
                errors.append(f"{slot_key}: CALL_CANCELLED")
                stage_a_logger.warning(
                    "[COMPARISON] stage_a_slot_failed request_id=%s slot_key=%s error_class=CancelledError error=%s retry=1",
                    request_id,
                    slot_key,
                    _truncate_error_message(e),
                )
            except Exception as e:
                errors.append(f"{slot_key}: CALL_FAILED:{type(e).__name__}")
                stage_a_logger.error(
                    "[COMPARISON] stage_a_slot_failed request_id=%s slot_key=%s error_class=%s error=%s retry=1",
                    request_id,
                    slot_key,
                    type(e).__name__,
                    _truncate_error_message(e),
                )

    deduped_sources = list(dict.fromkeys(merged.get("sources", [])))
    source_limit = _MAX_STAGE_A_SOURCES * max(1, len(slot_keys))
    merged["sources"] = deduped_sources[:source_limit]
    return merged, build_sources_index_from_flat(merged), errors


def build_sources_index(model_output: Dict) -> Dict:
    """Build an index of all sources by car, category, and metric."""
    sources_index = {}
    cars = model_output.get("cars", {})

    for car_id, car_data in cars.items():
        sources_index[car_id] = {}
        for cat_name, cat_data in car_data.items():
            if not isinstance(cat_data, dict):
                continue
            sources_index[car_id][cat_name] = {}
            for metric_name, metric_data in cat_data.items():
                if not isinstance(metric_data, dict):
                    continue
                sources = metric_data.get("sources", [])
                sources_index[car_id][cat_name][metric_name] = sources

    return sources_index


def build_sources_index_from_flat(merged_output: Dict) -> Dict:
    """Build sources index from flat sources array."""
    return {"all_sources": merged_output.get("sources", [])}


__all__ = [
    "call_gemini_comparison",
    "call_gemini_single_car",
    "call_stage_a_parallel",
    "parse_single_car_json",
    "parse_stage_a_json",
    "validate_grounding",
    "build_sources_index",
    "build_sources_index_from_flat",
]
