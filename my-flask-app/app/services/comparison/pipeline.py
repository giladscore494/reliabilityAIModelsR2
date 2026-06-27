"""Pipeline orchestration for the comparison feature."""

import json
import time as pytime
from typing import Any, Dict, Optional

from flask import current_app

from app.extensions import db
from app.models import ComparisonHistory
from app.services.comparison.cache import compute_request_hash, _safe_parse_json_cached
from app.services.comparison.grounding import call_gemini_single_pass_compare
from app.services.comparison.normalization import build_checked_versions, map_cars_to_slots
from app.services.comparison.prompts import build_single_pass_compare_prompt
from app.services.comparison.schemas import validate_buyer_profile, validate_comparison_request
from app.services.comparison.constants import COMPARISON_PROMPT_VERSION
from app.services.comparison.model_config import (
    comparison_stage_a_model_id,
    comparison_stage_a_repair_model_id,
    comparison_stage_b_model_id,
    comparison_fallback_model_id,
)
from app.services.comparison.decision import build_deterministic_decision_result
from app.services.comparison.metrics import _inc_compare_metric
from app.services.comparison.parsing import _extract_stage_a_error_code, _sanitize_stage_a_errors
from app.services.comparison.writer import (
    _summarize_comparison_narrative_shape,
    _validate_decision_writer_response,
    build_ai_payload,
    build_stored_comparison_ai_payload,
    convert_decision_result_to_narrative,
    resolve_comparison_narrative,
    sanitize_decision_result,
)
from app.utils.ai_guardrails import apply_feature_guardrails
from app.utils.http_helpers import _utcnow, api_error, api_ok, get_request_id
from app.services.gemini_grounding_client import (
    GROUNDING_FAILED_CODE,
    GROUNDING_HE_MESSAGE,
    GROUNDING_PERMISSION_DENIED_CODE,
    GROUNDING_PERMISSION_DENIED_HE_MESSAGE,
)
from app.utils.production_observability import log_slow_operation
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

    # Single grounded pass: collect source-verified evidence AND decide in ONE
    # Google-grounded Pro call. There is no scoring engine — the model's
    # reasoning IS the decision (decision_result schema).
    single_pass_start = pytime.perf_counter()
    single_pass_prompt = build_single_pass_compare_prompt(validated_cars, buyer_profile)
    parsed_output, single_pass_error, grounding_meta = call_gemini_single_pass_compare(
        single_pass_prompt
    )
    duration_ms = int((pytime.perf_counter() - single_pass_start) * 1000)
    ai_ms += duration_ms
    logger.info(
        "[COMPARE_TIMING] request_id=%s stage=single_pass duration_ms=%s", request_id, duration_ms
    )
    log_slow_operation(
        logger, feature="vehicle_comparison", stage="single_pass", duration_ms=duration_ms, request_id=request_id
    )

    # Total AI failure: the single grounded call produced no decision at all.
    # There is no scoring fallback to lean on, so return a clean retryable
    # error (mirrors the previous all-failed → 503 behavior). Internal error
    # codes are never exposed to users.
    if single_pass_error or not isinstance(parsed_output, dict):
        logger.warning(
            "[COMPARISON] single_pass_unavailable request_id=%s error=%s",
            request_id,
            single_pass_error,
        )
        total_ms = int((pytime.perf_counter() - total_start) * 1000)
        logger.info(
            "[COMPARE_TIMING] request_id=%s total_ms=%s deterministic_ms=%s ai_ms=%s db_ms=%s single_pass_ms=%s",
            request_id,
            total_ms,
            deterministic_ms,
            ai_ms,
            db_ms,
            duration_ms,
        )
        log_slow_operation(logger, feature="vehicle_comparison", stage="total", duration_ms=total_ms, request_id=request_id)
        if GROUNDING_PERMISSION_DENIED_CODE in (single_pass_error or ""):
            return api_error(
                GROUNDING_PERMISSION_DENIED_CODE,
                GROUNDING_PERMISSION_DENIED_HE_MESSAGE,
                status=503,
                request_id=request_id,
            )
        if GROUNDING_FAILED_CODE in (single_pass_error or ""):
            return api_error(GROUNDING_FAILED_CODE, GROUNDING_HE_MESSAGE, status=503)
        return api_error(
            "comparison_ai_unavailable",
            "לא ניתן להשלים השוואה אמינה כרגע. אפשר לנסות שוב בעוד רגע או לדייק שנתון, מנוע ורמת גימור.",
            status=503,
            details={
                "stage": "single_pass",
                "error_code": "single_pass_unavailable",
                "retryable": True,
            },
        )

    grounding_successful = bool(grounding_meta.get("grounding_successful"))
    source_count = int(grounding_meta.get("source_count") or 0)
    sources_list = (
        parsed_output.get("sources")
        if isinstance(parsed_output, dict) and isinstance(parsed_output.get("sources"), list)
        else []
    )
    sources_list = [s for s in sources_list if isinstance(s, str) and s.strip()]

    if not grounding_successful and not sources_list and isinstance(parsed_output, dict):
        decision = parsed_output.get("decision_result")
        if isinstance(decision, dict):
            overall = decision.get("overall_decision")
            if not isinstance(overall, dict):
                overall = {}
                decision["overall_decision"] = overall
            if overall.get("label") in {"car_1", "car_2", "car_3"}:
                logger.warning(
                    "[COMPARISON] single_pass_confident_decision_blocked request_id=%s reason=ungrounded_empty_sources original_label=%s",
                    request_id,
                    overall.get("label"),
                )
            overall["label"] = "unknown"
            overall["text"] = "לא ניתן להשלים השוואה אמינה כרגע. אפשר לנסות שוב בעוד רגע או לדייק שנתון, מנוע ורמת גימור."
            decision["category_decisions"] = []
            decision["key_differences"] = []
            decision["competitors_to_consider"] = []
            decision["practical_summary"] = ""

    # Minimal, scoreless model_output for storage and source indexing.
    model_output = {
        "cars": {slot_key: {} for slot_key in cars_selected_slots},
        "sources": sources_list,
        "assumptions": {},
        "grounding_successful": grounding_successful,
        "research_status": {
            "web_search_required": True,
            "web_search_performed": grounding_successful,
            "grounding_successful": grounding_successful,
            "source_count": source_count,
        },
    }
    sources_index = {"all_sources": sources_list}

    validated_decision = None
    decision_validation_reason = None
    if isinstance(parsed_output, dict):
        validated_decision, decision_validation_reason = (
            _validate_decision_writer_response(
                parsed_output, cars_selected_slots, {}
            )
        )

    stage_a_error_code = None
    ai_status = "ok"
    ai_reason = None
    if validated_decision:
        decision_result = validated_decision["decision_result"]
        ai_checked_versions = validated_decision.get("checked_versions")
    else:
        ai_reason = single_pass_error or decision_validation_reason or "single_pass_no_decision"
        logger.warning(
            "[COMPARISON] single_pass_no_usable_decision request_id=%s reason=%s",
            request_id,
            ai_reason,
        )
        _inc_compare_metric("compare_ai_fallback_used_total")
        return api_error(
            "comparison_ai_unavailable",
            "לא ניתן להשלים השוואה אמינה כרגע. אפשר לנסות שוב בעוד רגע או לדייק שנתון, מנוע ורמת גימור.",
            status=503,
            details={"stage": "single_pass", "error_code": "single_pass_no_decision", "retryable": True},
        )

    # computed_result is now a thin, scoreless container: the decision IS the result.
    computed_result = {
        "cars": model_output["cars"],
        "decision_result": decision_result,
        "comparison_status": {
            "requested_cars": len(validated_cars),
            "cars_with_evidence": len(validated_cars) if grounding_successful else 0,
            "balanced": True,
        },
        "sources": sources_list,
    }

    narrative = sanitize_comparison_narrative(
        convert_decision_result_to_narrative(
            {"decision_result": decision_result}, cars_selected_slots
        )
    )
    logger.info(
        "[COMPARISON] single_pass narrative request_id=%s grounding_successful=%s source_count=%s narrative_shape=%s",
        request_id,
        grounding_successful,
        source_count,
        _summarize_comparison_narrative_shape(narrative),
    )

    checked_versions = build_checked_versions(
        cars_selected_slots,
        model_output,
        ai_checked_versions,
    )
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
    stored_computed["model_ids"] = {
        "stage_a": comparison_stage_a_model_id(),
        "stage_a_repair": comparison_stage_a_repair_model_id(),
        "stage_b": comparison_stage_b_model_id(),
        "fallback": comparison_fallback_model_id(),
    }
    stored_computed["guardrail_meta"] = comparison_guarded.get("guardrail_meta", {})

    # Save to database
    total_ms = int((pytime.perf_counter() - total_start) * 1000)
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
            model_name=", ".join([
                comparison_stage_a_model_id(),
                comparison_stage_a_repair_model_id(),
                comparison_stage_b_model_id(),
                comparison_fallback_model_id(),
            ])[:64],
            grounding_enabled=bool(model_output.get("grounding_successful")),
            prompt_version=COMPARISON_PROMPT_VERSION,
            request_hash=request_hash,
            duration_ms=total_ms,
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
            "[COMPARE_TIMING] request_id=%s total_ms=%s deterministic_ms=%s ai_ms=%s db_ms=%s single_pass_ms=%s",
            request_id,
            total_ms,
            deterministic_ms,
            ai_ms,
            db_ms,
            duration_ms,
        )
        log_slow_operation(logger, feature="vehicle_comparison", stage="total", duration_ms=total_ms, request_id=request_id)

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
            "model_ids": stored_computed.get("model_ids"),
            "research_status": model_output.get("research_status")
            or {"grounding_successful": False, "web_search_performed": False},
        }
    )


__all__ = [
    "handle_comparison_request",
    "build_ai_payload",
    "build_stored_comparison_ai_payload",
    "convert_decision_result_to_narrative",
    "resolve_comparison_narrative",
    "enforce_authoritative_numbers",
]
