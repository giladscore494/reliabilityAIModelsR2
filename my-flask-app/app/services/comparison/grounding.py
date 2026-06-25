"""Stage A — grounded model calls and parsing."""

import json
import logging
import os
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
from app.services.comparison.model_config import (
    DEFAULT_COMPARISON_MODEL_ID,
    DEFAULT_COMPARISON_LOW_COST_MODEL_ID,
    comparison_safe_model_id,
    comparison_stage_a_model_id,
    comparison_stage_a_repair_model_id,
    is_model_not_found_error,
)
from app.services.comparison.parsing import (
    _extract_first_json_object,
    _is_schema_echo_text,
    _is_valid_single_car_payload,
    _is_valid_stage_a_payload,
    _repair_json_once,
    _strip_json_code_fences,
    _truncate_error_message,
    conservative_local_json_repair,
    _json_balance_state,
    normalize_single_car_payload,
)
from app.services.comparison.prompts import build_single_car_prompt
from app.services.comparison.schemas import validate_grounding
from app.services.comparison.fallbacks import _empty_stage_a_output
from app.services.gemini_grounding_client import call_grounded_model, GROUNDING_FAILED_CODE
from app.utils.http_helpers import get_request_id

logger = logging.getLogger(__name__)


def _generate_content_with_404_fallback(
    *,
    feature: str,
    model: str,
    contents: str,
    config: "genai_types.GenerateContentConfig",
    request_id: Optional[str] = None,
    log: Optional[logging.Logger] = None,
) -> Tuple[Any, str, Optional[str]]:
    """Call Gemini once, falling back once to the safe model on model 404."""
    call_log = log or logger
    try:
        resp = extensions.ai_client.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )
        return resp, model, None
    except Exception as exc:
        safe_model = comparison_safe_model_id()
        if is_model_not_found_error(exc) and model != safe_model:
            call_log.warning(
                "[AI] request_id=%s feature=%s stage=%s event=model_fallback_due_to_404 original_model=%s fallback_model=%s fallback_reason=model_404",
                request_id or "unknown",
                feature,
                feature,
                model,
                safe_model,
            )
            resp = extensions.ai_client.models.generate_content(
                model=safe_model,
                contents=contents,
                config=config,
            )
            return resp, safe_model, "model_fallback_due_to_404"
        raise


def parse_single_car_json(raw_text: str) -> Tuple[Optional[Dict], Optional[str]]:
    """Parse and validate single-car JSON response."""
    stripped = _strip_json_code_fences(raw_text)
    candidate = _extract_first_json_object(stripped)
    starts_json = (stripped or "").lstrip().startswith("{")
    if starts_json and candidate is None:
        state = _json_balance_state(stripped)
        logger.warning(
            "[COMPARISON] stage_a_json_diagnostics starts_with_json=true unbalanced=%s json_decode_error=%s extract_first_json_object_none=%s first_500=%.500s last_300=%.300s",
            state.get("unbalanced"),
            "not_attempted_no_complete_object",
            True,
            (stripped or "")[:500].replace("\n", " "),
            (stripped or "")[-300:].replace("\n", " "),
        )
    for current in (candidate, _repair_json_once(candidate) if candidate else None):
        if not current:
            continue
        try:
            parsed = json.loads(current)
        except json.JSONDecodeError as exc:
            snippet = (current or "")[:500].replace("\n", " ")
            if starts_json:
                state = _json_balance_state(current)
                logger.warning(
                    "[COMPARISON] stage_a_json_diagnostics starts_with_json=true unbalanced=%s json_decode_error=%s:%s extract_first_json_object_none=%s first_500=%.500s last_300=%.300s",
                    state.get("unbalanced"),
                    type(exc).__name__,
                    str(exc),
                    candidate is None,
                    (stripped or "")[:500].replace("\n", " "),
                    (stripped or "")[-300:].replace("\n", " "),
                )
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
    Call Gemini with web grounding for comparison data.
    Returns (parsed_output, error_string).
    """
    start_time = pytime.perf_counter()
    prompt_chars = len(prompt or "")
    outcome = "ok"
    outcome_reason = None
    model_used = comparison_stage_a_model_id()
    fallback_reason = None
    try:
        if extensions.ai_client is None:
            outcome = "error"
            outcome_reason = "CLIENT_NOT_INITIALIZED"
            return None, "CLIENT_NOT_INITIALIZED"

        result = call_grounded_model(
            comparison_stage_a_model_id(),
            prompt,
            timeout_sec=timeout_sec,
        )
        if not result or result.get("error_code"):
            outcome = "error"
            outcome_reason = (result or {}).get("error_code") or "CALL_FAILED_EMPTY"
            if outcome_reason == "CALL_TIMEOUT":
                _inc_compare_metric("compare_ai_failures_total", reason="timeout")
                _inc_compare_metric("compare_stage_a_timeout_total")
            else:
                _inc_compare_metric("compare_stage_a_error_total")
            return None, outcome_reason

        grounding_meta = result.get("grounding_meta") or {}
        if not grounding_meta.get("grounding_successful") and os.environ.get("ALLOW_UNGROUNDED_FALLBACK", "").lower() != "true":
            outcome = "error"
            outcome_reason = GROUNDING_FAILED_CODE
            return None, GROUNDING_FAILED_CODE

        text = (result.get("text") or "").strip()
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
            model_used,
            duration_ms,
            prompt_chars,
            _estimate_token_count(prompt),
            COMPARE_STAGE_A_MAX_OUTPUT_TOKENS,
            int(timeout_sec * 1000),
            True,
            0,
            outcome,
            fallback_reason or outcome_reason,
        )

def _call_gemini_single_car_raw(
    prompt: str,
    car_label: str,
    timeout_sec: int = COMPARE_STAGE_A_TIMEOUT_SEC,
    request_id: Optional[str] = None,
    log: Optional[logging.Logger] = None,
) -> Dict[str, Any]:
    """Internal helper that returns raw model text alongside parsed result.

    Returns a dict with keys:
        parsed: dict | None
        error: str | None
        raw_text: str | None
        grounding_meta: dict
        finish_reason: str | None
    """
    start_time = pytime.perf_counter()
    prompt_chars = len(prompt or "")
    outcome = "ok"
    outcome_reason = None
    model_used = comparison_stage_a_model_id()
    fallback_reason = None
    grounding_meta: Dict[str, Any] = {"grounding_successful": False, "source_count": 0}
    worker_logger = log or logger
    finish_reason = None
    raw_text = None
    try:
        if extensions.ai_client is None:
            outcome = "error"
            outcome_reason = "CLIENT_NOT_INITIALIZED"
            return {
                "parsed": None, "error": "CLIENT_NOT_INITIALIZED",
                "raw_text": None, "grounding_meta": grounding_meta,
                "finish_reason": None,
            }

        # Google Search grounding is mandatory for Stage A evidence. Grounded
        # calls use the Gemini Interactions API and intentionally do not set
        # response_mime_type; prompts require raw JSON and parsing is defensive.
        json_mime_used = False

        # Direct SDK call — no nested executor submission. This function
        # is already running inside the Stage A parallel worker thread.
        try:
            result = call_grounded_model(
                comparison_stage_a_model_id(),
                prompt,
                timeout_sec=timeout_sec,
                use_executor=False,
            )
        except Exception as e:
            elapsed = pytime.perf_counter() - start_time
            if elapsed >= timeout_sec:
                _inc_compare_metric("compare_ai_failures_total", reason="timeout")
                _inc_compare_metric("compare_stage_a_timeout_total")
                outcome = "timeout"
                outcome_reason = "CALL_TIMEOUT"
                return {
                    "parsed": None, "error": "CALL_TIMEOUT",
                    "raw_text": None, "grounding_meta": grounding_meta,
                    "finish_reason": None,
                }
            _log_ai_client_error(
                "comparison_stage_a", e, request_id=request_id, log=worker_logger
            )
            _inc_compare_metric("compare_stage_a_error_total")
            outcome = "error"
            outcome_reason = type(e).__name__
            if _is_output_too_long_error(str(e)):
                return {
                    "parsed": None, "error": "CALL_FAILED_OUTPUT_TOO_LONG",
                    "raw_text": None, "grounding_meta": grounding_meta,
                    "finish_reason": None,
                }
            return {
                "parsed": None, "error": f"CALL_FAILED:{type(e).__name__}",
                "raw_text": None, "grounding_meta": grounding_meta,
                "finish_reason": None,
            }

        elapsed = pytime.perf_counter() - start_time
        if elapsed >= timeout_sec:
            worker_logger.warning(
                "[COMPARISON] stage_a_sdk_response_after_timeout request_id=%s car=%s elapsed_sec=%.1f timeout_sec=%s",
                request_id or "unknown",
                car_label,
                elapsed,
                timeout_sec,
            )

        if not result or result.get("error_code"):
            outcome = "error"
            outcome_reason = (result or {}).get("error_code") or "CALL_FAILED_EMPTY"
            return {
                "parsed": None, "error": outcome_reason,
                "raw_text": None, "grounding_meta": grounding_meta,
                "finish_reason": None,
            }

        grounding_meta = result.get("grounding_meta") or grounding_meta
        if not grounding_meta.get("grounding_successful") and os.environ.get("ALLOW_UNGROUNDED_FALLBACK", "").lower() != "true":
            outcome = "error"
            outcome_reason = GROUNDING_FAILED_CODE
            return {
                "parsed": None, "error": GROUNDING_FAILED_CODE,
                "raw_text": None, "grounding_meta": grounding_meta,
                "finish_reason": None,
            }

        raw_text = (result.get("text") or "").strip()
        if not raw_text:
            outcome = "error"
            outcome_reason = "EMPTY_RESPONSE"
            return {
                "parsed": None, "error": "EMPTY_RESPONSE",
                "raw_text": raw_text, "grounding_meta": grounding_meta,
                "finish_reason": finish_reason,
            }

        # Check for schema echo in raw text before parsing
        if _is_schema_echo_text(raw_text):
            preview = " ".join((raw_text or "").split())[:200]
            worker_logger.warning(
                "[COMPARISON] stage_a_schema_echo_or_prompt_echo request_id=%s car=%s resp_len=%s preview=%.200s",
                request_id or "unknown",
                car_label,
                len(raw_text),
                preview,
            )

        parsed, parse_error = parse_single_car_json(raw_text)
        if parse_error:
            outcome = "error"
            outcome_reason = parse_error
            _inc_compare_metric("compare_stage_a_json_invalid_total")
            preview = " ".join((raw_text or "").split())[:200]
            # Detect schema echo / prompt echo patterns
            is_echo = (
                preview.startswith("Let's refine")
                or "Return ONLY valid JSON" in preview
                or _is_schema_echo_text(raw_text)
            )
            worker_logger.warning(
                "[COMPARISON] stage_a_model_json_invalid request_id=%s car=%s reason=%s resp_len=%s finish_reason=%s grounding_successful=%s source_count=%s schema_echo=%s preview=%.200s",
                request_id or "unknown",
                car_label,
                parse_error,
                len(raw_text or ""),
                finish_reason,
                grounding_meta.get("grounding_successful"),
                grounding_meta.get("source_count"),
                is_echo,
                preview,
            )
            return {
                "parsed": None, "error": parse_error,
                "raw_text": raw_text, "grounding_meta": grounding_meta,
                "finish_reason": finish_reason,
            }
        if isinstance(parsed, dict):
            parsed["_grounding_meta"] = grounding_meta
        return {
            "parsed": parsed, "error": None,
            "raw_text": raw_text, "grounding_meta": grounding_meta,
            "finish_reason": finish_reason,
        }

    finally:
        duration_ms = (pytime.perf_counter() - start_time) * 1000
        worker_logger.info(
            "[AI] feature=comparison_stage_a_per_car model=%s car=%s duration_ms=%.2f prompt_chars=%s prompt_tokens_est=%s max_output_tokens=%s timeout_ms=%s tools_enabled=%s json_mime=%s grounding_successful=%s source_count=%s outcome=%s reason=%s",
            model_used,
            car_label,
            duration_ms,
            prompt_chars,
            _estimate_token_count(prompt),
            COMPARE_STAGE_A_MAX_OUTPUT_TOKENS,
            int(timeout_sec * 1000),
            True,
            json_mime_used,
            grounding_meta.get("grounding_successful"),
            grounding_meta.get("source_count"),
            outcome,
            fallback_reason or outcome_reason,
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
    raw_result = _call_gemini_single_car_raw(
        prompt, car_label, timeout_sec, request_id, log
    )
    return raw_result["parsed"], raw_result["error"]


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
    model_used = comparison_stage_a_repair_model_id()
    fallback_reason = None

    try:
        if extensions.ai_client is None:
            outcome = "error"
            outcome_reason = "CLIENT_NOT_INITIALIZED"
            return None, "CLIENT_NOT_INITIALIZED"

        truncated_text = (raw_text or "")[:COMPARE_STAGE_A_REPAIR_MAX_INPUT_CHARS]
        repair_prompt = (
            "Convert the following model response into valid JSON for a single car analysis.\n"
            "Use ONLY facts present in the text. Do not add new facts.\n"
            "Do not copy schema placeholders. Do not output enum options like 'high|medium|low'.\n\n"
            "The response must start with '{'. No markdown, no code fences, no explanation.\n\n"
            "Required top-level keys for ONE car only: car_name, reliability, ownership_cost, "
            "comfort_practicality, performance_driving, facts, short_notes, sources, car_profile.\n"
            "Scoring sections must use only real low/medium/high labels supported by the text, otherwise null.\n"
            "Reject/avoid research-status-only objects and schema echoes. Use null for unknown values, [] for empty arrays.\n\n"
            f"MODEL RESPONSE:\n{truncated_text}"
        )

        config_kwargs = {
            "temperature": 0.0,
            "max_output_tokens": COMPARE_STAGE_A_REPAIR_MAX_OUTPUT_TOKENS,
            "tools": [],  # No Google Search/tools/AFC for repair
            "response_mime_type": "application/json",
        }
        try:
            config = genai_types.GenerateContentConfig(**config_kwargs)
        except TypeError:
            config_kwargs.pop("response_mime_type", None)
            config = genai_types.GenerateContentConfig(**config_kwargs)

        resp, model_used, fallback_reason = _generate_content_with_404_fallback(
            feature="comparison_stage_a_repair",
            model=comparison_stage_a_repair_model_id(),
            contents=repair_prompt,
            config=config,
            request_id=request_id,
            log=worker_logger,
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

        if not text.lstrip().startswith("{"):
            outcome = "error"
            outcome_reason = "REPAIR_MODEL_NOT_JSON_OBJECT"
            return None, "REPAIR_MODEL_JSON_INVALID"

        parsed, parse_error = parse_single_car_json(text)
        if parse_error and model_used == DEFAULT_COMPARISON_LOW_COST_MODEL_ID and DEFAULT_COMPARISON_MODEL_ID != model_used:
            worker_logger.info(
                "[COMPARISON] stage_a_repair_retry_stronger_model request_id=%s car=%s original_model=%s stronger_model=%s reason=%s",
                request_id or "unknown",
                car_label,
                model_used,
                DEFAULT_COMPARISON_MODEL_ID,
                f"REPAIR_{parse_error}",
            )
            resp = extensions.ai_client.models.generate_content(
                model=DEFAULT_COMPARISON_MODEL_ID,
                contents=repair_prompt,
                config=config,
            )
            model_used = DEFAULT_COMPARISON_MODEL_ID
            text = (getattr(resp, "text", "") or "").strip()
            if not text.lstrip().startswith("{"):
                outcome = "error"
                outcome_reason = "REPAIR_MODEL_NOT_JSON_OBJECT"
                return None, "REPAIR_MODEL_JSON_INVALID"
            parsed, parse_error = parse_single_car_json(text)
        if parse_error:
            outcome = "error"
            outcome_reason = f"REPAIR_{parse_error}"
            return None, f"REPAIR_{parse_error}"

        # Reject repair results that are just research_status-only objects
        if parsed and isinstance(parsed, dict):
            top_keys = set(parsed.keys()) - {"_grounding_meta"}
            if top_keys <= {"status", "checked_areas", "open_fields", "sources_found"}:
                outcome = "error"
                outcome_reason = "REPAIR_RESEARCH_STATUS_ONLY"
                return None, "REPAIR_RESEARCH_STATUS_ONLY"

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
            "[AI] feature=comparison_stage_a_repair model=%s car=%s duration_ms=%.2f tools_enabled=%s afc_enabled=%s response_mime_type=%s outcome=%s reason=%s",
            model_used,
            car_label,
            duration_ms,
            False,
            False,
            "application/json",
            outcome,
            fallback_reason or outcome_reason,
        )


def _looks_like_prose_without_json(raw_text: str) -> bool:
    stripped = (raw_text or "").lstrip()
    if _extract_first_json_object(_strip_json_code_fences(stripped)):
        return False
    lower = stripped[:80].lower()
    return lower.startswith(("wait", "let's", "i will", "- ", "* ", "• "))


def _build_strict_json_retry_prompt(original_prompt: str) -> str:
    return (
        "Return a single valid JSON object only. The first character must be { and the last character must be }.\n"
        "No markdown, bullets, explanation, reasoning, or prose. Use null/[] for unknowns.\n"
        "Use the same selected car and catalog context below.\n\n"
        f"{original_prompt[:6000]}"
    )


def _attempt_local_json_repair(raw_text: str, original_grounding_meta: Dict[str, Any]) -> Tuple[Optional[Dict], Optional[str]]:
    repaired = conservative_local_json_repair(raw_text)
    if not repaired:
        return None, "LOCAL_REPAIR_NO_JSON"
    parsed, error = parse_single_car_json(repaired)
    if error:
        return None, error
    if isinstance(parsed, dict) and isinstance(original_grounding_meta, dict):
        parsed["_grounding_meta"] = original_grounding_meta
    return parsed, None


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
            _call_gemini_single_car_raw,
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
            raw_result = future.result(
                timeout=COMPARE_STAGE_A_TIMEOUT_SEC + PARALLEL_GRACE_SEC
            )
            result = raw_result["parsed"]
            error = raw_result["error"]
            # Always capture raw text and grounding meta for repair
            if raw_result.get("raw_text"):
                raw_texts[slot_key] = raw_result["raw_text"]
            if raw_result.get("grounding_meta"):
                raw_grounding_metas[slot_key] = raw_result["grounding_meta"]
            if error:
                raw_for_retry = raw_result.get("raw_text") or ""
                if error == "MODEL_JSON_INVALID" and _looks_like_prose_without_json(raw_for_retry):
                    stage_a_logger.info(
                        "[COMPARISON] stage_a_strict_json_retry request_id=%s slot_key=%s original_reason=PROSE_NOT_JSON",
                        request_id,
                        slot_key,
                    )
                    retry_result = _call_gemini_single_car_raw(
                        _build_strict_json_retry_prompt(prompts[slot_key]),
                        slot_key,
                        COMPARE_STAGE_A_TIMEOUT_SEC,
                        request_id,
                        stage_a_logger,
                    )
                    if retry_result.get("raw_text"):
                        raw_texts[slot_key] = retry_result["raw_text"]
                    if retry_result.get("grounding_meta"):
                        raw_grounding_metas[slot_key] = retry_result["grounding_meta"]
                    if not retry_result.get("error") and _store_slot_result(slot_key, retry_result.get("parsed")):
                        continue
                    error = retry_result.get("error") or "MODEL_JSON_INVALID"
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
            raw_model_text = raw_texts.get(slot_key, "")
            if not raw_model_text:
                # No raw text captured — cannot repair
                errors.append(f"{slot_key}: REPAIR_NO_RAW_TEXT")
                stage_a_logger.warning(
                    "[COMPARISON] stage_a_repair_skipped request_id=%s slot_key=%s reason=REPAIR_NO_RAW_TEXT",
                    request_id,
                    slot_key,
                )
                continue
            original_gmeta = raw_grounding_metas.get(
                slot_key, {"grounding_successful": False, "source_count": 0}
            )
            local_result, local_error = _attempt_local_json_repair(raw_model_text, original_gmeta)
            if local_error is None and _store_slot_result(slot_key, local_result):
                stage_a_logger.info(
                    "[COMPARISON] stage_a_local_json_repair_succeeded request_id=%s slot_key=%s",
                    request_id,
                    slot_key,
                )
                continue
            stage_a_logger.info(
                "[COMPARISON] stage_a_local_json_repair_failed request_id=%s slot_key=%s reason=%s",
                request_id,
                slot_key,
                local_error,
            )
            repair_futures[slot_key] = AI_EXECUTOR.submit(
                _attempt_json_repair,
                raw_model_text,
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
    if not any_grounded:
        stage_a_logger.warning(
            "[COMPARISON] stage_a_grounding_absent request_id=%s tools_enabled=true grounding_successful=false source_count=%s",
            request_id,
            merged["research_status"]["source_count"],
        )
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
    "_call_gemini_single_car_raw",
    "call_stage_a_parallel",
    "_attempt_json_repair",
    "parse_single_car_json",
    "parse_stage_a_json",
    "validate_grounding",
    "build_sources_index",
    "build_sources_index_from_flat",
]
