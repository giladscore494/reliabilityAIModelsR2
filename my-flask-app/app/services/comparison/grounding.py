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
    COMPARE_STAGE_A_REPAIR_MAX_INPUT_CHARS,
    COMPARE_STAGE_A_REPAIR_MAX_OUTPUT_TOKENS,
    COMPARE_STAGE_A_REPAIR_TIMEOUT_SEC,
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
    _is_schema_echo_text,
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


def _google_search_tool() -> "genai_types.Tool":
    """Build the Google Search grounding tool for Stage A calls."""
    return genai_types.Tool(google_search=genai_types.GoogleSearch())


def _extract_stage_a_grounding(resp) -> Dict[str, Any]:
    """Detect real Google Search grounding signals on a Stage A response."""
    from app.services.reliability_model_service import extract_grounding_meta

    return extract_grounding_meta(resp)


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

        config = genai_types.GenerateContentConfig(
            temperature=COMPARE_STAGE_A_TEMPERATURE,
            top_p=0.8,
            top_k=20,
            max_output_tokens=COMPARE_STAGE_A_MAX_OUTPUT_TOKENS,
            tools=[_google_search_tool()],
        )

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
            True,
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
    """Call Gemini for a single car. Returns (parsed_dict, error_string).

    When called from within call_stage_a_parallel (already inside an
    AI_EXECUTOR worker), this function performs the SDK call directly
    instead of submitting a nested future to the same executor.
    """
    start_time = pytime.perf_counter()
    prompt_chars = len(prompt or "")
    outcome = "ok"
    outcome_reason = None
    grounding_meta = {"grounding_successful": False, "source_count": 0}
    worker_logger = log or logger
    finish_reason = None
    try:
        if extensions.ai_client is None:
            outcome = "error"
            outcome_reason = "CLIENT_NOT_INITIALIZED"
            return None, "CLIENT_NOT_INITIALIZED"

        # Google Search grounding is mandatory for Stage A evidence. The
        # grounding tool and a forced JSON mime type are mutually exclusive in
        # the Gemini API, so we enable the tool and parse JSON from text.
        config = genai_types.GenerateContentConfig(
            temperature=COMPARE_STAGE_A_TEMPERATURE,
            top_p=0.8,
            top_k=20,
            max_output_tokens=COMPARE_STAGE_A_MAX_OUTPUT_TOKENS,
            tools=[_google_search_tool()],
        )

        # Direct SDK call — no nested executor submission. This function
        # is already running inside the Stage A parallel worker thread.
        try:
            resp = extensions.ai_client.models.generate_content(
                model=COMPARISON_MODEL_ID,
                contents=prompt,
                config=config,
            )
        except Exception as e:
            elapsed = pytime.perf_counter() - start_time
            if elapsed >= timeout_sec:
                _inc_compare_metric("compare_ai_failures_total", reason="timeout")
                _inc_compare_metric("compare_stage_a_timeout_total")
                outcome = "timeout"
                outcome_reason = "CALL_TIMEOUT"
                return None, "CALL_TIMEOUT"
            _log_ai_client_error(
                "comparison_stage_a", e, request_id=request_id, log=worker_logger
            )
            _inc_compare_metric("compare_stage_a_error_total")
            outcome = "error"
            outcome_reason = type(e).__name__
            if _is_output_too_long_error(str(e)):
                return None, "CALL_FAILED_OUTPUT_TOO_LONG"
            return None, f"CALL_FAILED:{type(e).__name__}"

        elapsed = pytime.perf_counter() - start_time
        if elapsed >= timeout_sec:
            worker_logger.warning(
                "[COMPARISON] stage_a_sdk_response_after_timeout request_id=%s car=%s elapsed_sec=%.1f timeout_sec=%s",
                request_id or "unknown",
                car_label,
                elapsed,
                timeout_sec,
            )

        if resp is None:
            outcome = "error"
            outcome_reason = "CALL_FAILED_EMPTY"
            return None, "CALL_FAILED:EMPTY"

        # Extract finish_reason if available
        try:
            candidates = getattr(resp, "candidates", None)
            if candidates and len(candidates) > 0:
                finish_reason = getattr(candidates[0], "finish_reason", None)
        except Exception:
            pass

        grounding_meta = _extract_stage_a_grounding(resp)
        text = (getattr(resp, "text", "") or "").strip()
        if not text:
            outcome = "error"
            outcome_reason = "EMPTY_RESPONSE"
            return None, "EMPTY_RESPONSE"

        # Check for schema echo in raw text before parsing
        if _is_schema_echo_text(text):
            preview = " ".join((text or "").split())[:200]
            worker_logger.warning(
                "[COMPARISON] stage_a_schema_echo_or_prompt_echo request_id=%s car=%s resp_len=%s preview=%.200s",
                request_id or "unknown",
                car_label,
                len(text),
                preview,
            )

        parsed, parse_error = parse_single_car_json(text)
        if parse_error:
            outcome = "error"
            outcome_reason = parse_error
            _inc_compare_metric("compare_stage_a_json_invalid_total")
            preview = " ".join((text or "").split())[:200]
            # Detect schema echo / prompt echo patterns
            is_echo = (
                preview.startswith("Let's refine")
                or "Return ONLY valid JSON" in preview
                or _is_schema_echo_text(text)
            )
            worker_logger.warning(
                "[COMPARISON] stage_a_model_json_invalid request_id=%s car=%s reason=%s resp_len=%s finish_reason=%s grounding_successful=%s source_count=%s schema_echo=%s preview=%.200s",
                request_id or "unknown",
                car_label,
                parse_error,
                len(text or ""),
                finish_reason,
                grounding_meta.get("grounding_successful"),
                grounding_meta.get("source_count"),
                is_echo,
                preview,
            )
            return None, parse_error
        if isinstance(parsed, dict):
            parsed["_grounding_meta"] = grounding_meta
        return parsed, None

    finally:
        duration_ms = (pytime.perf_counter() - start_time) * 1000
        worker_logger.info(
            "[AI] feature=comparison_stage_a_per_car model=%s car=%s duration_ms=%.2f prompt_chars=%s prompt_tokens_est=%s max_output_tokens=%s timeout_ms=%s tools_enabled=%s grounding_successful=%s source_count=%s outcome=%s reason=%s",
            COMPARISON_MODEL_ID,
            car_label,
            duration_ms,
            prompt_chars,
            _estimate_token_count(prompt),
            COMPARE_STAGE_A_MAX_OUTPUT_TOKENS,
            int(timeout_sec * 1000),
            True,
            grounding_meta.get("grounding_successful"),
            grounding_meta.get("source_count"),
            outcome,
            outcome_reason,
        )


def _attempt_json_repair(
    raw_text: str,
    car_label: str,
    original_grounding_meta: Dict[str, Any],
    request_id: Optional[str] = None,
    log: Optional[logging.Logger] = None,
) -> Tuple[Optional[Dict], Optional[str]]:
    """Attempt a non-grounded JSON repair call (tools=[], no Google Search).

    Uses the raw Stage A text as input and asks the model to convert it
    into the required JSON schema without adding new facts.
    """
    worker_logger = log or logger
    start_time = pytime.perf_counter()
    outcome = "ok"
    outcome_reason = None

    try:
        if extensions.ai_client is None:
            outcome = "error"
            outcome_reason = "CLIENT_NOT_INITIALIZED"
            return None, "CLIENT_NOT_INITIALIZED"

        truncated_text = (raw_text or "")[:COMPARE_STAGE_A_REPAIR_MAX_INPUT_CHARS]
        repair_prompt = (
            "Convert the provided text into the required JSON schema using only facts "
            "present in the text. Do not add new facts. Return JSON only.\n\n"
            "The first character of the response must be '{'. "
            "No markdown, no code fences, no explanation.\n\n"
            f"TEXT:\n{truncated_text}"
        )

        config = genai_types.GenerateContentConfig(
            temperature=0.1,
            max_output_tokens=COMPARE_STAGE_A_REPAIR_MAX_OUTPUT_TOKENS,
            tools=[],  # No Google Search for repair
        )

        resp = extensions.ai_client.models.generate_content(
            model=COMPARISON_MODEL_ID,
            contents=repair_prompt,
            config=config,
        )

        if resp is None:
            outcome = "error"
            outcome_reason = "REPAIR_EMPTY"
            return None, "REPAIR_EMPTY"

        text = (getattr(resp, "text", "") or "").strip()
        if not text:
            outcome = "error"
            outcome_reason = "REPAIR_EMPTY_TEXT"
            return None, "REPAIR_EMPTY_TEXT"

        parsed, parse_error = parse_single_car_json(text)
        if parse_error:
            outcome = "error"
            outcome_reason = f"REPAIR_{parse_error}"
            return None, f"REPAIR_{parse_error}"

        # Preserve original grounding metadata if the original grounded call
        # had real grounding metadata; repair itself does not claim web_search.
        if isinstance(parsed, dict) and isinstance(original_grounding_meta, dict):
            parsed["_grounding_meta"] = original_grounding_meta

        return parsed, None

    except Exception as e:
        outcome = "error"
        outcome_reason = type(e).__name__
        worker_logger.warning(
            "[COMPARISON] stage_a_repair_failed request_id=%s car=%s error=%s",
            request_id or "unknown",
            car_label,
            str(e)[:200],
        )
        return None, f"REPAIR_FAILED:{type(e).__name__}"
    finally:
        duration_ms = (pytime.perf_counter() - start_time) * 1000
        worker_logger.info(
            "[AI] feature=comparison_stage_a_repair model=%s car=%s duration_ms=%.2f tools_enabled=%s outcome=%s reason=%s",
            COMPARISON_MODEL_ID,
            car_label,
            duration_ms,
            False,
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

    grounding_flags: Dict[str, Dict[str, Any]] = {}
    # Track raw text for potential JSON repair (indexed by slot_key)
    raw_texts: Dict[str, str] = {}
    raw_grounding_metas: Dict[str, Dict[str, Any]] = {}

    def _store_slot_result(slot_key: str, result: Optional[Dict[str, Any]]) -> bool:
        gmeta = result.get("_grounding_meta") if isinstance(result, dict) else None
        normalized_result = normalize_single_car_payload(
            result,
            fallback_name=(cars_selected_slots.get(slot_key, {}) or {}).get(
                "display_name"
            ),
        )
        if normalized_result is None:
            return False
        if isinstance(gmeta, dict):
            grounding_flags[slot_key] = gmeta
            normalized_result["research_status"] = {
                "web_search_required": True,
                "web_search_performed": bool(gmeta.get("grounding_successful")),
                "grounding_successful": bool(gmeta.get("grounding_successful")),
                "source_count": int(gmeta.get("source_count") or 0),
            }
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
    repair_slots = []  # Slots that need JSON repair (not full retry)
    for slot_key, future in futures.items():
        try:
            result, error = future.result(
                timeout=COMPARE_STAGE_A_TIMEOUT_SEC + PARALLEL_GRACE_SEC
            )
            if error:
                if error == "MODEL_JSON_INVALID":
                    repair_slots.append(slot_key)
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
                    repair_slots.append(slot_key)
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

    # JSON repair: use a short non-grounded call (tools=[]) instead of
    # rerunnning full Google Search. This avoids doubling latency and failure risk.
    if repair_slots:
        stage_a_logger.info(
            "[COMPARISON] stage_a_json_repair request_id=%s slot_keys=%s strategy=non_grounded_repair",
            request_id,
            sorted(repair_slots),
        )
        repair_futures = {}
        for slot_key in repair_slots:
            # Re-run the original prompt as a repair source text
            original_prompt = prompts.get(slot_key, "")
            original_gmeta = raw_grounding_metas.get(
                slot_key, {"grounding_successful": False, "source_count": 0}
            )
            repair_futures[slot_key] = AI_EXECUTOR.submit(
                _attempt_json_repair,
                original_prompt,
                slot_key,
                original_gmeta,
                request_id,
                stage_a_logger,
            )
        for slot_key, future in repair_futures.items():
            try:
                result, error = future.result(
                    timeout=COMPARE_STAGE_A_REPAIR_TIMEOUT_SEC + PARALLEL_GRACE_SEC
                )
                if error or not _store_slot_result(slot_key, result):
                    final_error = error or "MODEL_JSON_INVALID"
                    errors.append(f"{slot_key}: {final_error}")
                    stage_a_logger.warning(
                        "[COMPARISON] stage_a_slot_failed request_id=%s slot_key=%s error_class=StageAError error=%s repair=1",
                        request_id,
                        slot_key,
                        _truncate_error_message(final_error),
                    )
            except concurrent.futures.TimeoutError as e:
                future.cancel()
                errors.append(f"{slot_key}: CALL_TIMEOUT")
                stage_a_logger.warning(
                    "[COMPARISON] stage_a_slot_failed request_id=%s slot_key=%s error_class=TimeoutError error=%s repair=1",
                    request_id,
                    slot_key,
                    _truncate_error_message(e),
                )
            except concurrent.futures.CancelledError as e:
                errors.append(f"{slot_key}: CALL_CANCELLED")
                stage_a_logger.warning(
                    "[COMPARISON] stage_a_slot_failed request_id=%s slot_key=%s error_class=CancelledError error=%s repair=1",
                    request_id,
                    slot_key,
                    _truncate_error_message(e),
                )
            except Exception as e:
                errors.append(f"{slot_key}: CALL_FAILED:{type(e).__name__}")
                stage_a_logger.error(
                    "[COMPARISON] stage_a_slot_failed request_id=%s slot_key=%s error_class=%s error=%s repair=1",
                    request_id,
                    slot_key,
                    type(e).__name__,
                    _truncate_error_message(e),
                )

    deduped_sources = list(dict.fromkeys(merged.get("sources", [])))
    source_limit = _MAX_STAGE_A_SOURCES * max(1, len(slot_keys))
    merged["sources"] = deduped_sources[:source_limit]
    # Honest, server-owned grounding flag: only true if at least one Stage A
    # per-car call actually triggered Google Search (never model-asserted).
    any_grounded = any(
        bool(g.get("grounding_successful")) for g in grounding_flags.values()
    )
    merged["grounding_successful"] = any_grounded
    merged["research_status"] = {
        "web_search_required": True,
        "web_search_performed": any_grounded,
        "grounding_successful": any_grounded,
        "grounded_car_count": sum(
            1 for g in grounding_flags.values() if g.get("grounding_successful")
        ),
        "source_count": sum(int(g.get("source_count") or 0) for g in grounding_flags.values()),
    }
    stage_a_logger.info(
        "[COMPARISON] stage_a_grounding request_id=%s tools_enabled=true grounding_successful=%s grounded_cars=%s",
        request_id,
        any_grounded,
        merged["research_status"]["grounded_car_count"],
    )
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
    "_attempt_json_repair",
    "parse_single_car_json",
    "parse_stage_a_json",
    "validate_grounding",
    "build_sources_index",
    "build_sources_index_from_flat",
]
