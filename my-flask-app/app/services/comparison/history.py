"""DB persistence / read helpers for comparison history."""

import json
from typing import Any, Dict, List, Optional

from flask import current_app

from app.extensions import db
from app.models import ComparisonHistory
from app.services.comparison.cache import _safe_json_obj
from app.services.comparison.fallbacks import (
    build_deterministic_fallback_narrative,
    mark_partial_comparison_narrative,
)
from app.services.comparison.normalization import map_cars_to_slots
from app.services.comparison.prompts import build_compare_writer_prompt
from app.services.comparison.decision import build_deterministic_decision_result
from app.services.comparison.metrics import _inc_compare_metric
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
from app.utils.sanitization import sanitize_comparison_narrative
from app.utils.http_helpers import get_request_id

def get_comparison_history(user_id: int, limit: int = 10) -> List[Dict]:
    """Get comparison history for a user."""
    records = (
        ComparisonHistory.query.filter_by(user_id=user_id)
        .order_by(ComparisonHistory.created_at.desc())
        .limit(limit)
        .all()
    )

    result = []
    for record in records:
        try:
            # Robust parsing with double-encoding support
            cars = _safe_json_obj(record.cars_selected, default=[])
            if not isinstance(cars, list):
                cars = []

            computed = _safe_json_obj(record.computed_result, default={})
            if not isinstance(computed, dict):
                computed = {}

            result.append(
                {
                    "id": record.id,
                    "created_at": record.created_at.isoformat(),
                    "cars": cars,
                    "overall_winner": computed.get("overall_winner"),
                }
            )
        except (AttributeError, TypeError, ValueError) as e:
            # Log warning and skip corrupted record
            current_app.logger.warning(
                f"Skipping corrupted comparison history record id={record.id}: {e}"
            )
            continue

    return result


def get_comparison_detail(comparison_id: int, user_id: Optional[int]) -> Optional[Dict]:
    """Get details of a specific comparison."""
    query = ComparisonHistory.query.filter_by(id=comparison_id)
    if user_id:
        query = query.filter_by(user_id=user_id)

    record = query.first()
    if not record:
        return None

    try:
        # Robust parsing with double-encoding support
        cars_selected = _safe_json_obj(record.cars_selected, default=[])
        if not isinstance(cars_selected, list):
            cars_selected = []

        computed_result = _safe_json_obj(record.computed_result, default={})
        if not isinstance(computed_result, dict):
            computed_result = {}

        model_output = _safe_json_obj(record.model_json_raw, default=None)
        if model_output is not None and not isinstance(model_output, dict):
            model_output = None

        sources_index = _safe_json_obj(record.sources_index, default={})
        if not isinstance(sources_index, dict):
            sources_index = {}

        assumptions = model_output.get("assumptions", {}) if model_output else {}

        narrative = resolve_comparison_narrative(
            computed_result if isinstance(computed_result, dict) else None
        )
        cars_selected_slots = (
            map_cars_to_slots(cars_selected) if isinstance(cars_selected, list) else {}
        )
        decision_result = sanitize_decision_result(
            computed_result.get("decision_result")
            if isinstance(computed_result, dict)
            else None,
            cars_selected_slots if isinstance(cars_selected_slots, dict) else {},
            computed_result if isinstance(computed_result, dict) else {},
            get_request_id(),
        )
        if (
            isinstance(computed_result, dict)
            and computed_result.get("decision_result") != decision_result
        ):
            computed_result["decision_result"] = decision_result
        ai_payload = build_stored_comparison_ai_payload(
            computed_result if isinstance(computed_result, dict) else None,
            narrative,
        )
        if isinstance(computed_result, dict) and record.computed_result != json.dumps(
            computed_result, ensure_ascii=False
        ):
            record.computed_result = json.dumps(computed_result, ensure_ascii=False)
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
                current_app.logger.warning(
                    "Failed to self-heal comparison detail id=%s",
                    comparison_id,
                    exc_info=True,
                )

        # Reconstruct stable car slots

        return {
            "id": record.id,
            "created_at": record.created_at.isoformat(),
            "cars_selected": cars_selected_slots,
            "cars_selected_list": cars_selected
            if isinstance(cars_selected, list)
            else [],
            "model_output": model_output,
            "computed_result": computed_result,
            "narrative": narrative,
            "decision_result": decision_result,
            "ai": ai_payload,
            "sources_index": sources_index,
            "assumptions": assumptions,
            "model_name": record.model_name,
            "prompt_version": record.prompt_version,
        }
    except (AttributeError, TypeError, ValueError) as e:
        current_app.logger.warning(
            f"Failed to parse comparison detail for id={comparison_id}: {e}"
        )
        return None


def regenerate_comparison_ai(
    comparison_id: int, user_id: int
) -> Optional[Dict[str, Any]]:
    """Regenerate AI explanation without recomputing deterministic numeric scoring."""
    record = ComparisonHistory.query.filter_by(
        id=comparison_id, user_id=user_id
    ).first()
    if not record:
        return None

    cars_selected = _safe_json_obj(record.cars_selected, default=[])
    computed_result = _safe_json_obj(record.computed_result, default={})
    model_output = _safe_json_obj(record.model_json_raw, default={})
    if not isinstance(cars_selected, list) or not isinstance(computed_result, dict):
        return None
    if not isinstance(model_output, dict):
        model_output = {}

    cars_selected_slots = map_cars_to_slots(cars_selected)
    server_computed_result = dict(computed_result)
    server_computed_result.pop("narrative", None)
    server_computed_result.pop("ai", None)

    writer_prompt = build_compare_writer_prompt(
        cars_selected_slots, server_computed_result, model_output
    )
    try:
        stage_b_output, stage_b_error = call_gemini_compare_writer(writer_prompt)
    except Exception as exc:
        _inc_compare_metric("compare_ai_regenerate_error_total")
        current_app.logger.exception(
            "[COMPARISON] compare_ai_regenerate_writer_failed request_id=%s comparison_id=%s user_id=%s error_type=%s",
            get_request_id(),
            comparison_id,
            user_id,
            type(exc).__name__,
        )
        stage_b_output, stage_b_error = None, "CALL_FAILED:UNKNOWN"

    narrative = None
    reason = None
    validated_decision = None
    decision_validation_reason = None
    if stage_b_error:
        reason = "stage_b_error"
        _inc_compare_metric("compare_ai_regenerate_fallback_total")
        _inc_compare_metric("compare_ai_fallback_used_total")
        narrative = build_deterministic_fallback_narrative(
            cars_selected_slots, server_computed_result
        )
    elif isinstance(stage_b_output, dict):
        current_app.logger.info(
            "[COMPARISON] compare_ai_regenerate payload request_id=%s comparison_id=%s payload_shape=%s",
            get_request_id(),
            comparison_id,
            _summarize_compare_writer_payload(stage_b_output),
        )
        validated_decision, decision_validation_reason = (
            _validate_decision_writer_response(
                stage_b_output,
                cars_selected_slots,
                server_computed_result,
            )
        )
        if validated_decision:
            narrative = sanitize_comparison_narrative(
                convert_decision_result_to_narrative(
                    validated_decision, cars_selected_slots
                )
            )
            current_app.logger.info(
                "[COMPARISON] compare_ai_regenerate accepted request_id=%s comparison_id=%s narrative_shape=%s",
                get_request_id(),
                comparison_id,
                _summarize_comparison_narrative_shape(narrative),
            )
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
                current_app.logger.info(
                    "[COMPARISON] compare_ai_regenerate legacy narrative accepted request_id=%s comparison_id=%s narrative_shape=%s",
                    get_request_id(),
                    comparison_id,
                    _summarize_comparison_narrative_shape(narrative),
                )
            else:
                raw_narrative = stage_b_output.get("narrative")
                salvaged_narrative = _salvage_partial_writer_output(
                    stage_b_output,
                    cars_selected_slots,
                    server_computed_result,
                )
                current_app.logger.warning(
                    "[COMPARISON] compare_ai_regenerate validation failed request_id=%s comparison_id=%s reason=%s payload_shape=%s",
                    get_request_id(),
                    comparison_id,
                    validation_reason,
                    _summarize_compare_writer_payload(stage_b_output),
                )
                if salvaged_narrative:
                    narrative = sanitize_comparison_narrative(salvaged_narrative)
                    current_app.logger.info(
                        "[COMPARISON] narrative salvaged from partial writer output request_id=%s comparison_id=%s narrative_shape=%s",
                        get_request_id(),
                        comparison_id,
                        _summarize_comparison_narrative_shape(narrative),
                    )
                elif raw_narrative:
                    narrative = sanitize_comparison_narrative(raw_narrative)
                    current_app.logger.info(
                        "[COMPARISON] compare_ai_regenerate legacy narrative used request_id=%s comparison_id=%s narrative_shape=%s",
                        get_request_id(),
                        comparison_id,
                        _summarize_comparison_narrative_shape(narrative),
                    )
                else:
                    reason = "stage_b_error"
                    _inc_compare_metric("compare_ai_regenerate_fallback_total")
                    _inc_compare_metric("compare_ai_fallback_used_total")
                    narrative = build_deterministic_fallback_narrative(
                        cars_selected_slots, server_computed_result
                    )

    if not (
        (server_computed_result.get("comparison_status") or {}).get("balanced", True)
    ):
        narrative = mark_partial_comparison_narrative(narrative)
        current_app.logger.info(
            "[COMPARISON] compare_ai_regenerate partial_stage_a fallback path used request_id=%s comparison_id=%s narrative_shape=%s",
            get_request_id(),
            comparison_id,
            _summarize_comparison_narrative_shape(narrative),
        )

    if validated_decision:
        decision_result = validated_decision["decision_result"]
    else:
        if decision_validation_reason:
            current_app.logger.warning(
                "[COMPARISON] compare_ai_regenerate decision_result fallback request_id=%s comparison_id=%s reason=%s",
                get_request_id(),
                comparison_id,
                decision_validation_reason,
            )
        decision_result = build_deterministic_decision_result(
            cars_selected_slots, server_computed_result, stage_b_output
        )
    server_computed_result["decision_result"] = decision_result
    ai_payload = build_ai_payload(
        server_computed_result,
        narrative,
        "ok" if reason is None else "fallback",
        reason,
    )
    current_app.logger.info(
        "[COMPARISON] compare_ai_regenerate response request_id=%s comparison_id=%s ai_status=%s ai_reason=%s narrative_shape=%s",
        get_request_id(),
        comparison_id,
        ai_payload.get("status"),
        ai_payload.get("reason"),
        _summarize_comparison_narrative_shape(narrative),
    )
    if stage_b_error:
        ai_payload["error"] = stage_b_error
    persisted_computed = dict(server_computed_result)
    if narrative:
        persisted_computed["narrative"] = narrative
    persisted_computed["ai"] = ai_payload
    record.computed_result = json.dumps(persisted_computed, ensure_ascii=False)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        _inc_compare_metric("compare_ai_regenerate_error_total")
        current_app.logger.exception(
            "[COMPARISON] compare_ai_regenerate_commit_failed request_id=%s comparison_id=%s user_id=%s",
            get_request_id(),
            comparison_id,
            user_id,
        )
    _inc_compare_metric("compare_ai_regenerate_used_total")

    return {
        "comparison_id": comparison_id,
        "ai": ai_payload,
        "narrative": narrative,
        "decision_result": decision_result,
    }


__all__ = [
    "get_comparison_history",
    "get_comparison_detail",
    "regenerate_comparison_ai",
]
