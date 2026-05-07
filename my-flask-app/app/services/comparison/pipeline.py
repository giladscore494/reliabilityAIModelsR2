"""Pipeline orchestration for the comparison feature."""

import json
import time as pytime
from typing import Any, Dict, Optional

from flask import current_app

from app.extensions import db
from app.models import ComparisonHistory
from app.services.comparison.cache import compute_request_hash, _safe_parse_json_cached
from app.services.comparison.fallbacks import (
    build_deterministic_fallback_narrative,
    mark_partial_comparison_narrative,
)
from app.services.comparison.grounding import call_stage_a_parallel
from app.services.comparison.normalization import build_checked_versions, map_cars_to_slots
from app.services.comparison.prompts import build_compare_writer_prompt, build_compare_writer_retry_prompt
from app.services.comparison.schemas import validate_buyer_profile, validate_comparison_request
from app.services.comparison.computation import compute_comparison_results
from app.services.comparison.constants import COMPARISON_MODEL_ID, COMPARISON_PROMPT_VERSION
from app.services.comparison.decision import build_deterministic_decision_result
from app.services.comparison.metrics import _inc_compare_metric
from app.services.comparison.parsing import _extract_stage_a_error_code, _sanitize_stage_a_errors
from app.services.comparison.writer import (
    _salvage_partial_writer_output,
    _summarize_compare_writer_payload,
    _summarize_comparison_narrative_shape,
    _validate_compare_writer_response,
    _validate_decision_writer_response,
    build_ai_payload,
    build_stored_comparison_ai_payload,
    call_gemini_compare_writer,
    convert_decision_result_to_narrative,
    convert_writer_response_to_narrative,
    resolve_comparison_narrative,
    sanitize_decision_result,
)
from app.utils.ai_guardrails import apply_feature_guardrails
from app.utils.http_helpers import _utcnow, api_error, api_ok, get_request_id
from app.utils.sanitization import sanitize_comparison_narrative

def enforce_authoritative_numbers(
    server_computed: Dict, stage_b_output: Optional[Dict], request_id: str
) -> Dict:
    """
    Server deterministic scoring is authoritative.
    Stage B may echo computed_result, but any drift is ignored and logged.
    """
    if isinstance(stage_b_output, dict) and isinstance(
        stage_b_output.get("computed_result"), dict
    ):
        if stage_b_output.get("computed_result") != server_computed:
            current_app.logger.warning(
                "[COMPARISON] stage_b attempted numeric/schema drift request_id=%s",
                request_id,
            )
    return dict(server_computed)


def handle_comparison_request(
    data: Dict,
    user_id: Optional[int],
    session_id: Optional[str],
    owner_bypass: bool = False,
) -> Any:
    """
    Handle a car comparison request.
    Returns Flask response.
    """
    logger = current_app.logger
    request_id = get_request_id()
    total_start = pytime.perf_counter()
    deterministic_ms = 0
    ai_ms = 0
    db_ms = 0

    # Validate request
    is_valid, error_msg, validated_cars = validate_comparison_request(data)
    if not is_valid:
        return api_error("validation_error", error_msg, status=400)
    buyer_valid, buyer_error, buyer_profile = validate_buyer_profile(
        data.get("buyer_profile")
    )
    if not buyer_valid:
        logger.warning("[COMPARISON] invalid_buyer_profile request_id=%s", request_id)
        return api_error("validation_error", buyer_error, status=400)

    # Map cars to stable slots with display_name
    cars_selected_slots = map_cars_to_slots(validated_cars)

    # Compute request hash for caching
    request_hash = compute_request_hash(validated_cars, buyer_profile)

    # Check cache (only for logged-in users)
    if user_id:
        cached = (
            ComparisonHistory.query.filter_by(
                user_id=user_id,
                request_hash=request_hash,
            )
            .order_by(ComparisonHistory.created_at.desc())
            .first()
        )

        if cached and cached.computed_result:
            logger.info(
                f"[COMPARISON] cache hit request_id={request_id} hash={request_hash}"
            )

            # Safely parse all cached JSON fields, handling double-encoded data
            cars_selected, cars_was_double = _safe_parse_json_cached(
                cached.cars_selected, "cars_selected"
            )
            computed_result, computed_was_double = _safe_parse_json_cached(
                cached.computed_result, "computed_result"
            )
            sources_index, sources_was_double = _safe_parse_json_cached(
                cached.sources_index, "sources_index"
            )
            model_output, model_was_double = _safe_parse_json_cached(
                cached.model_json_raw, "model_json_raw"
            )

            # Validate that required fields parsed to expected types
            cache_valid = (
                isinstance(cars_selected, list)
                and len(cars_selected) >= 2
                and isinstance(computed_result, dict)
            )

            if cache_valid:
                # Extract assumptions safely (only if model_output is a dict)
                assumptions = {}
                if isinstance(model_output, dict):
                    assumptions = model_output.get("assumptions", {})

                # Self-heal: if any field was double-encoded, update the DB to store normalized JSON
                # Note: cars_selected and computed_result are required (validated above),
                # while sources_index and model_json_raw are nullable - hence the extra null checks
                needs_heal = (
                    cars_was_double
                    or computed_was_double
                    or sources_was_double
                    or model_was_double
                )
                if needs_heal:
                    try:
                        if cars_was_double:
                            cached.cars_selected = json.dumps(
                                cars_selected, ensure_ascii=False
                            )
                        if computed_was_double:
                            cached.computed_result = json.dumps(
                                computed_result, ensure_ascii=False
                            )
                        if sources_was_double and sources_index is not None:
                            cached.sources_index = json.dumps(
                                sources_index, ensure_ascii=False
                            )
                        if model_was_double and model_output is not None:
                            cached.model_json_raw = json.dumps(
                                model_output, ensure_ascii=False
                            )
                        db.session.commit()
                        logger.info(
                            f"[COMPARISON] self-healed double-encoded cache row id={cached.id}"
                        )
                    except Exception as heal_err:
                        logger.warning(
                            f"[COMPARISON] self-heal commit failed: {heal_err}"
                        )
                        db.session.rollback()

                cached_slots = (
                    map_cars_to_slots(cars_selected)
                    if isinstance(cars_selected, list)
                    else {}
                )
                if not cached_slots:
                    cached_slots = cars_selected_slots

                narrative = resolve_comparison_narrative(
                    computed_result if isinstance(computed_result, dict) else None
                )
                checked_versions = build_checked_versions(
                    cached_slots if isinstance(cached_slots, dict) else {},
                    model_output if isinstance(model_output, dict) else {},
                    computed_result.get("checked_versions")
                    if isinstance(computed_result, dict)
                    else None,
                )
                decision_result = sanitize_decision_result(
                    computed_result.get("decision_result")
                    if isinstance(computed_result, dict)
                    else None,
                    cached_slots if isinstance(cached_slots, dict) else {},
                    computed_result if isinstance(computed_result, dict) else {},
                    request_id,
                )
                if isinstance(computed_result, dict) and (
                    computed_result.get("decision_result") != decision_result
                    or computed_result.get("checked_versions") != checked_versions
                ):
                    computed_result["decision_result"] = decision_result
                    computed_result["checked_versions"] = checked_versions
                    cached.computed_result = json.dumps(
                        computed_result, ensure_ascii=False
                    )
                    try:
                        db.session.commit()
                    except Exception as heal_err:
                        logger.warning(
                            f"[COMPARISON] decision_result cache heal failed for id={cached.id}: {heal_err}"
                        )
                        db.session.rollback()
                cached_guarded, _ = apply_feature_guardrails(
                    "vehicle_comparison",
                    {"cars": cars_selected if isinstance(cars_selected, list) else []},
                    {
                        "checked_versions": checked_versions,
                        "decision_result": decision_result,
                        "narrative": narrative,
                        "computed_result": computed_result,
                        "sources_index": sources_index if sources_index else {},
                        "ai": build_stored_comparison_ai_payload(
                            computed_result if isinstance(computed_result, dict) else None,
                            narrative,
                        ),
                    },
                )
                checked_versions = cached_guarded.get("checked_versions", checked_versions)
                decision_result = cached_guarded.get("decision_result", decision_result)
                narrative = cached_guarded.get("narrative", narrative)
                ai_payload = cached_guarded.get(
                    "ai",
                    build_stored_comparison_ai_payload(
                        computed_result if isinstance(computed_result, dict) else None,
                        narrative,
                    ),
                )
                return api_ok(
                    {
                        "cached": True,
                        "comparison_id": cached.id,
                        "cars_selected": cached_slots,
                        "cars_selected_list": cars_selected
                        if isinstance(cars_selected, list)
                        else [],
                        "model_output": model_output,
                        "computed_result": computed_result,
                        "narrative": narrative,
                        "decision_result": decision_result,
                        "checked_versions": checked_versions,
                        "sources_index": sources_index if sources_index else {},
                        "assumptions": assumptions,
                        "ai": ai_payload,
                        "visible_warning": cached_guarded.get("visible_warning"),
                        "central_differences": cached_guarded.get("central_differences"),
                        "guardrail_meta": cached_guarded.get("guardrail_meta", {}),
                    }
                )
            else:
                # Cache row is corrupted (cannot parse to expected types)
                # Delete the bad row so future requests don't hit it, then proceed with fresh call
                logger.warning(
                    f"[COMPARISON] cache row {cached.id} corrupted, deleting and recomputing"
                )
                try:
                    db.session.delete(cached)
                    db.session.commit()
                except Exception as del_err:
                    logger.warning(
                        f"[COMPARISON] failed to delete corrupted cache row: {del_err}"
                    )
                    db.session.rollback()

    # Stage A: parallel per-car Gemini calls
    stage_a_start = pytime.perf_counter()
    model_output, sources_index, stage_a_errors = call_stage_a_parallel(
        validated_cars, cars_selected_slots
    )
    duration_ms = int((pytime.perf_counter() - stage_a_start) * 1000)
    ai_ms += duration_ms
    stage_a_error_code = None
    stage_a_partial = False

    if len(stage_a_errors) == len(validated_cars):
        stage_a_error_code = _extract_stage_a_error_code(stage_a_errors)
        sanitized_errors = _sanitize_stage_a_errors(stage_a_errors)
        logger.warning(
            "[COMPARISON] stage_a_all_failed request_id=%s errors=%s",
            request_id,
            sanitized_errors,
        )
        total_ms = int((pytime.perf_counter() - total_start) * 1000)
        logger.info(
            "[COMPARE_TIMING] request_id=%s total_ms=%s deterministic_ms=%s ai_ms=%s db_ms=%s",
            request_id,
            total_ms,
            deterministic_ms,
            ai_ms,
            db_ms,
        )
        return api_error(
            "comparison_ai_unavailable",
            "שירות ההשוואה אינו זמין כרגע. נסה שוב בעוד רגע.",
            status=503,
            details={
                "stage": "stage_a",
                "request_id": request_id,
                "retryable": True,
                "error_code": stage_a_error_code,
                "errors": sanitized_errors,
            },
        )
    elif stage_a_errors:
        # Partial failure — log but continue with available data
        stage_a_partial = True
        logger.warning(
            "[COMPARISON] partial_stage_a request_id=%s errors=%s",
            request_id,
            _sanitize_stage_a_errors(stage_a_errors),
        )

    # Compute scores deterministically (server-side source of truth)
    scoring_start = pytime.perf_counter()
    server_computed_result = compute_comparison_results(model_output)
    deterministic_ms = int((pytime.perf_counter() - scoring_start) * 1000)

    # Stage B: non-grounded writer call (full schema + narrative around server results)
    stage_b_output = None
    stage_b_error = None
    narrative = None
    stage_b_reason = None
    validated_decision = None
    decision_validation_reason = None
    writer_prompt = build_compare_writer_prompt(
        cars_selected_slots, server_computed_result, model_output, buyer_profile
    )
    stage_b_start = pytime.perf_counter()
    stage_b_output, stage_b_error = call_gemini_compare_writer(writer_prompt)
    ai_ms += int((pytime.perf_counter() - stage_b_start) * 1000)
    logger.info(
        "[COMPARISON] stage_b payload request_id=%s partial_stage_a=%s payload_shape=%s",
        request_id,
        stage_a_partial,
        _summarize_compare_writer_payload(stage_b_output),
    )
    if isinstance(stage_b_output, dict):
        validated_decision, decision_validation_reason = (
            _validate_decision_writer_response(
                stage_b_output,
                cars_selected_slots,
                server_computed_result,
            )
        )
    if stage_b_error:
        logger.warning(
            f"[COMPARISON] stage_b call failed request_id={request_id} error={stage_b_error}"
        )
        stage_b_reason = "stage_b_error"
        retry_prompt = build_compare_writer_retry_prompt(
            cars_selected_slots, server_computed_result
        )
        retry_output, retry_error = call_gemini_compare_writer(retry_prompt)
        logger.info(
            "[COMPARISON] stage_b retry payload request_id=%s partial_stage_a=%s payload_shape=%s",
            request_id,
            stage_a_partial,
            _summarize_compare_writer_payload(retry_output),
        )
        if retry_error:
            logger.warning(
                f"[COMPARISON] stage_b retry failed request_id={request_id} error={retry_error}"
            )
            _inc_compare_metric("compare_ai_fallback_used_total")
            narrative = build_deterministic_fallback_narrative(
                cars_selected_slots, server_computed_result
            )
        else:
            validated_retry, retry_reason = _validate_compare_writer_response(
                retry_output
            )
            if validated_retry:
                narrative = sanitize_comparison_narrative(
                    convert_writer_response_to_narrative(
                        validated_retry, cars_selected_slots
                    )
                )
                stage_b_reason = None
                logger.info(
                    "[COMPARISON] stage_b retry accepted request_id=%s narrative_shape=%s",
                    request_id,
                    _summarize_comparison_narrative_shape(narrative),
                )
            else:
                raw_retry_narrative = (
                    retry_output.get("narrative")
                    if isinstance(retry_output, dict)
                    else None
                )
                salvaged_narrative = _salvage_partial_writer_output(
                    retry_output,
                    cars_selected_slots,
                    server_computed_result,
                )
                logger.warning(
                    "[COMPARISON] stage_b retry validation failed request_id=%s reason=%s payload_shape=%s",
                    request_id,
                    retry_reason,
                    _summarize_compare_writer_payload(retry_output),
                )
                if salvaged_narrative:
                    narrative = sanitize_comparison_narrative(salvaged_narrative)
                    stage_b_reason = None
                    logger.info(
                        "[COMPARISON] narrative salvaged from partial writer output request_id=%s narrative_shape=%s",
                        request_id,
                        _summarize_comparison_narrative_shape(narrative),
                    )
                elif raw_retry_narrative:
                    narrative = sanitize_comparison_narrative(raw_retry_narrative)
                    stage_b_reason = None
                    logger.info(
                        "[COMPARISON] stage_b retry legacy narrative used request_id=%s narrative_shape=%s",
                        request_id,
                        _summarize_comparison_narrative_shape(narrative),
                    )
                else:
                    _inc_compare_metric("compare_ai_fallback_used_total")
                    narrative = build_deterministic_fallback_narrative(
                        cars_selected_slots, server_computed_result
                    )
    elif isinstance(stage_b_output, dict):
        if validated_decision:
            narrative = sanitize_comparison_narrative(
                convert_decision_result_to_narrative(
                    validated_decision, cars_selected_slots
                )
            )
            logger.info(
                "[COMPARISON] narrative generated request_id=%s narrative_shape=%s",
                request_id,
                _summarize_comparison_narrative_shape(narrative),
            )
            stage_b_reason = None
        else:
            validated_writer, validation_reason = _validate_compare_writer_response(
                stage_b_output
            )
            if validated_writer:
                narrative = sanitize_comparison_narrative(
                    convert_writer_response_to_narrative(
                        validated_writer, cars_selected_slots
                    )
                )
                logger.info(
                    "[COMPARISON] legacy narrative generated request_id=%s narrative_shape=%s",
                    request_id,
                    _summarize_comparison_narrative_shape(narrative),
                )
                stage_b_reason = None
            else:
                raw_narrative = stage_b_output.get("narrative")
                salvaged_narrative = _salvage_partial_writer_output(
                    stage_b_output,
                    cars_selected_slots,
                    server_computed_result,
                )
                logger.warning(
                    "[COMPARISON] stage_b validation failed request_id=%s reason=%s payload_shape=%s",
                    request_id,
                    validation_reason,
                    _summarize_compare_writer_payload(stage_b_output),
                )
                if salvaged_narrative:
                    narrative = sanitize_comparison_narrative(salvaged_narrative)
                    logger.info(
                        "[COMPARISON] narrative salvaged from partial writer output request_id=%s narrative_shape=%s",
                        request_id,
                        _summarize_comparison_narrative_shape(narrative),
                    )
                    stage_b_reason = None
                elif raw_narrative:
                    narrative = sanitize_comparison_narrative(raw_narrative)
                    logger.info(
                        "[COMPARISON] narrative generated request_id=%s mode=legacy_deprecated narrative_shape=%s",
                        request_id,
                        _summarize_comparison_narrative_shape(narrative),
                    )
                    stage_b_reason = None
                else:
                    stage_b_reason = "stage_b_error"
                    _inc_compare_metric("compare_ai_fallback_used_total")
                    narrative = build_deterministic_fallback_narrative(
                        cars_selected_slots, server_computed_result
                    )

    if stage_a_partial:
        narrative = mark_partial_comparison_narrative(narrative)
        logger.info(
            "[COMPARISON] partial_stage_a fallback path used request_id=%s narrative_shape=%s",
            request_id,
            _summarize_comparison_narrative_shape(narrative),
        )

    computed_result = enforce_authoritative_numbers(
        server_computed_result, stage_b_output, request_id
    )
    if validated_decision:
        decision_result = validated_decision["decision_result"]
    else:
        if decision_validation_reason:
            logger.warning(
                "[COMPARISON] decision_result fallback request_id=%s reason=%s",
                request_id,
                decision_validation_reason,
            )
        decision_result = build_deterministic_decision_result(
            cars_selected_slots, computed_result, stage_b_output
        )
    checked_versions = build_checked_versions(
        cars_selected_slots,
        model_output,
        validated_decision.get("checked_versions") if validated_decision else None,
    )
    ai_status = "ok"
    ai_reason = None
    if stage_a_partial:
        ai_status = "partial_fallback"
        ai_reason = "stage_a_partial"
    elif stage_b_reason:
        ai_status = "fallback"
        ai_reason = stage_b_reason
    ai_payload = build_ai_payload(computed_result, narrative, ai_status, ai_reason)
    comparison_guarded, _ = apply_feature_guardrails(
        "vehicle_comparison",
        {"cars": validated_cars},
        {
            "checked_versions": checked_versions,
            "decision_result": decision_result,
            "narrative": narrative,
            "computed_result": computed_result,
            "sources_index": sources_index,
            "ai": ai_payload,
        },
    )
    checked_versions = comparison_guarded.get("checked_versions", checked_versions)
    decision_result = comparison_guarded.get("decision_result", decision_result)
    narrative = comparison_guarded.get("narrative", narrative)
    ai_payload = comparison_guarded.get("ai", ai_payload)
    visible_warning = comparison_guarded.get("visible_warning")
    central_differences = comparison_guarded.get("central_differences")
    logger.info(
        "[COMPARISON] response narrative request_id=%s ai_status=%s ai_reason=%s narrative_shape=%s",
        request_id,
        ai_status,
        ai_reason,
        _summarize_comparison_narrative_shape(narrative),
    )
    if stage_a_error_code:
        ai_payload["error"] = stage_a_error_code

    # Include narrative in computed_result for storage
    stored_computed = dict(computed_result)
    stored_computed["decision_result"] = decision_result
    stored_computed["checked_versions"] = checked_versions
    if visible_warning:
        stored_computed["visible_warning"] = visible_warning
    if central_differences:
        stored_computed["central_differences"] = central_differences
    if narrative:
        stored_computed["narrative"] = narrative
    stored_computed["ai"] = ai_payload
    stored_computed["guardrail_meta"] = comparison_guarded.get("guardrail_meta", {})

    # Save to database
    try:
        db_start = pytime.perf_counter()
        comparison_record = ComparisonHistory(
            created_at=_utcnow(),
            user_id=user_id,
            session_id=session_id,
            cars_selected=json.dumps(validated_cars, ensure_ascii=False),
            model_json_raw=json.dumps(model_output, ensure_ascii=False),
            computed_result=json.dumps(stored_computed, ensure_ascii=False),
            sources_index=json.dumps(sources_index, ensure_ascii=False),
            model_name=COMPARISON_MODEL_ID,
            grounding_enabled=True,
            prompt_version=COMPARISON_PROMPT_VERSION,
            request_hash=request_hash,
            duration_ms=duration_ms,
        )
        db.session.add(comparison_record)
        db.session.commit()
        comparison_id = comparison_record.id
        db_ms = int((pytime.perf_counter() - db_start) * 1000)
        logger.info(
            f"[COMPARISON] saved request_id={request_id} comparison_id={comparison_id}"
        )
    except Exception as e:
        logger.error(f"[COMPARISON] save failed request_id={request_id} error={e}")
        db.session.rollback()
        comparison_id = None
    finally:
        total_ms = int((pytime.perf_counter() - total_start) * 1000)
        logger.info(
            "[COMPARE_TIMING] request_id=%s total_ms=%s deterministic_ms=%s ai_ms=%s db_ms=%s",
            request_id,
            total_ms,
            deterministic_ms,
            ai_ms,
            db_ms,
        )

    return api_ok(
        {
            "cached": False,
            "comparison_id": comparison_id,
            "cars_selected": cars_selected_slots,
            "cars_selected_list": validated_cars,
            "model_output": model_output,
            "computed_result": computed_result,
            "narrative": narrative,
            "decision_result": decision_result,
            "checked_versions": checked_versions,
            "sources_index": sources_index,
            "assumptions": {},
            "ai": ai_payload,
            "visible_warning": visible_warning,
            "central_differences": central_differences,
            "guardrail_meta": comparison_guarded.get("guardrail_meta", {}),
        }
    )


__all__ = [
    "handle_comparison_request",
    "build_ai_payload",
    "build_stored_comparison_ai_payload",
    "convert_writer_response_to_narrative",
    "convert_decision_result_to_narrative",
    "resolve_comparison_narrative",
    "enforce_authoritative_numbers",
]
