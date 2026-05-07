"""Stage B — narrative generation (writer call)."""

import concurrent.futures
import json
import logging
import time as pytime
from typing import Any, Dict, List, Optional, Tuple

import app.extensions as extensions
from flask import current_app
from google.genai import types as genai_types

from app.services.comparison.fallbacks import build_deterministic_fallback_narrative
from app.services.comparison.schemas import validate_compare_writer_response
from app.services.comparison.constants import (
    COMPARISON_MODEL_ID,
    COMPARE_WRITER_MAX_OUTPUT_TOKENS,
    COMPARE_WRITER_RETRY_MAX_OUTPUT_TOKENS,
    COMPARE_WRITER_TIMEOUT_SEC,
    COMPARE_AI_METRICS,
    COMPARE_CATEGORY_NAMES,
    COMPARE_SCORE_EXPLANATION_TEMPLATE_HE,
    DECISION_TEXT_FALLBACK_HE,
    PARTIAL_COMPARISON_DISCLAIMER,
    PARTIAL_COMPARISON_SUMMARY_PREFIX,
    TIE_THRESHOLD,
)
from app.services.comparison.decision import (
    _append_unique_text,
    _decision_label,
    sanitize_decision_result,
)
from app.services.comparison.metrics import _inc_compare_metric
from app.services.comparison.model_calls import (
    _estimate_token_count,
    _is_output_too_long_error,
    _log_ai_client_error,
)
from app.services.comparison.parsing import (
    _extract_decision_slot_keys,
    _normalize_compare_writer_winner,
    _normalize_sources,
    _ordered_compare_slot_keys,
    _sanitize_checked_versions,
    _truncate_to_word_limit,
)
from app.utils.http_helpers import get_request_id
from app.utils.sanitization import sanitize_comparison_narrative

logger = logging.getLogger(__name__)


def _validate_decision_writer_response(
    payload: Any,
    cars_selected_slots: Optional[Dict[str, Dict[str, Any]]] = None,
    computed_result: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if not isinstance(payload, dict):
        return None, "payload_not_object"
    if not isinstance(payload.get("decision_result"), dict):
        return None, "missing_decision_result"
    return {
        "decision_result": sanitize_decision_result(
            payload.get("decision_result"),
            cars_selected_slots or {},
            computed_result or {},
            get_request_id(),
        ),
        "checked_versions": _sanitize_checked_versions(
            payload.get("checked_versions"),
            _ordered_compare_slot_keys(
                cars_selected_slots or {},
                (computed_result or {}).get("cars") or {},
            ),
        ),
        "sources": _normalize_sources(payload.get("sources")),
    }, None


def _summarize_compare_writer_payload(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {
            "is_object": False,
            "top_level_keys": [],
        }

    decision_result = (
        payload.get("decision_result")
        if isinstance(payload.get("decision_result"), dict)
        else {}
    )
    decision_slot_keys = _extract_decision_slot_keys(decision_result)

    categories = (
        payload.get("categories") if isinstance(payload.get("categories"), list) else []
    )
    categories_with_explanations = 0
    for item in categories:
        if not isinstance(item, dict):
            continue
        explanations = item.get("explanations")
        if isinstance(explanations, dict) and any(
            str(text or "").strip() for text in explanations.values()
        ):
            categories_with_explanations += 1

    return {
        "is_object": True,
        "top_level_keys": sorted(payload.keys()),
        "has_decision_result": bool(decision_result),
        "checked_versions_count": len(payload.get("checked_versions") or {})
        if isinstance(payload.get("checked_versions"), dict)
        else 0,
        "decision_slot_keys": decision_slot_keys,
        "has_summary": bool(str(payload.get("summary") or "").strip()),
        "has_categories": isinstance(payload.get("categories"), list),
        "category_count": len(categories),
        "has_caveats": isinstance(payload.get("caveats"), list),
        "caveat_count": len(payload.get("caveats") or [])
        if isinstance(payload.get("caveats"), list)
        else None,
        "categories_with_per_car_explanations": categories_with_explanations,
    }


def _summarize_comparison_narrative_shape(narrative: Any) -> Dict[str, Any]:
    if not isinstance(narrative, dict):
        return {
            "exists": False,
            "overall_summary_exists": False,
            "category_explanations_exists": False,
            "per_car_explanations_exist": False,
            "disclaimers_exist": False,
            "category_count": 0,
        }

    categories = (
        narrative.get("category_explanations")
        if isinstance(narrative.get("category_explanations"), list)
        else []
    )
    categories_with_explanations = 0
    for item in categories:
        if not isinstance(item, dict):
            continue
        explanations = item.get("explanations")
        if isinstance(explanations, dict) and any(
            str(text or "").strip() for text in explanations.values()
        ):
            categories_with_explanations += 1

    disclaimers = (
        narrative.get("disclaimers_he")
        if isinstance(narrative.get("disclaimers_he"), list)
        else []
    )
    summary = str(narrative.get("overall_summary") or "").strip()
    return {
        "exists": True,
        "overall_summary_exists": bool(summary),
        "category_explanations_exists": bool(categories),
        "per_car_explanations_exist": categories_with_explanations > 0,
        "disclaimers_exist": any(str(item or "").strip() for item in disclaimers),
        "category_count": len(categories),
        "categories_with_per_car_explanations": categories_with_explanations,
        "partial_summary": summary.startswith(PARTIAL_COMPARISON_SUMMARY_PREFIX),
    }


def _validate_compare_writer_response(
    payload: Any,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if not isinstance(payload, dict):
        return None, "payload_not_object"
    required_keys = {"summary", "winner", "categories", "caveats"}
    if not required_keys.issubset(payload.keys()):
        return None, "missing_required_keys"

    summary = _truncate_to_word_limit(payload.get("summary"), 80)
    categories = payload.get("categories")
    caveats = payload.get("caveats")
    allowed_slot_keys = ["car_1", "car_2", "car_3"]
    winner = _normalize_compare_writer_winner(payload.get("winner"), allowed_slot_keys)
    if summary is None or winner is None:
        return None, "invalid_summary_or_winner"
    if not isinstance(categories, list) or len(categories) > 4:
        return None, "invalid_categories"
    if not isinstance(caveats, list) or len(caveats) > 3:
        return None, "invalid_caveats"

    validated_categories = []
    for item in categories:
        if not isinstance(item, dict):
            return None, "invalid_category_item"
        required_category_keys = {"name", "winner", "why", "tips"}
        if not required_category_keys.issubset(item.keys()):
            return None, "missing_category_keys"
        if item.get("name") not in COMPARE_CATEGORY_NAMES:
            return None, "invalid_category_name"
        category_winner = _normalize_compare_writer_winner(
            item.get("winner"), allowed_slot_keys
        )
        if category_winner is None:
            return None, "invalid_category_winner"
        why = _truncate_to_word_limit(item.get("why"), 60)
        if why is None:
            return None, "invalid_category_why"
        explanations = item.get("explanations")
        normalized_explanations = {}
        if explanations is not None:
            if not isinstance(explanations, dict):
                return None, "invalid_explanations"
            for slot_key in allowed_slot_keys:
                explanation_text = explanations.get(slot_key)
                if explanation_text is None:
                    continue
                explanation_clean = _truncate_to_word_limit(explanation_text, 60)
                if explanation_clean is None:
                    logger.warning(
                        "[COMPARISON] compare_writer explanation dropped slot_key=%s reason=empty_or_invalid",
                        slot_key,
                    )
                    continue
                normalized_explanations[slot_key] = explanation_clean
        tips = item.get("tips")
        if not isinstance(tips, list) or len(tips) > 3:
            return None, "invalid_tips"
        normalized_tips = []
        for tip in tips:
            tip_clean = _truncate_to_word_limit(tip, 30)
            if tip_clean is None:
                logger.warning(
                    "[COMPARISON] compare_writer tip dropped reason=empty_or_invalid"
                )
                continue
            normalized_tips.append(tip_clean)
        validated_categories.append(
            {
                "name": item.get("name"),
                "winner": category_winner,
                "why": why,
                "explanations": normalized_explanations,
                "tips": normalized_tips,
            }
        )

    normalized_caveats = []
    for caveat in caveats:
        caveat_clean = _truncate_to_word_limit(caveat, 30)
        if caveat_clean is None:
            logger.warning(
                "[COMPARISON] compare_writer caveat dropped reason=empty_or_invalid"
            )
            continue
        normalized_caveats.append(caveat_clean)

    return {
        "summary": summary,
        "winner": winner,
        "categories": validated_categories,
        "caveats": normalized_caveats,
    }, None


def _salvage_partial_writer_output(
    stage_b_output: Any,
    cars_selected_slots: Dict,
    server_computed_result: Dict,
) -> Optional[Dict[str, Any]]:
    """Build a hybrid narrative from a partial writer response plus deterministic data."""
    if not isinstance(stage_b_output, dict):
        return None

    summary = str(stage_b_output.get("summary") or "").strip()
    if not summary:
        return None

    computed_cars = (
        (server_computed_result.get("cars") or {})
        if isinstance(server_computed_result, dict)
        else {}
    )
    car_keys = _ordered_compare_slot_keys(cars_selected_slots, computed_cars)
    category_explanations = []

    for category_key in COMPARE_CATEGORY_NAMES:
        winner = (server_computed_result.get("category_winners", {}) or {}).get(
            category_key
        ) or "tie"
        explanations = {}
        for car_key in car_keys:
            score = (
                ((computed_cars.get(car_key, {}) or {}).get("categories", {}) or {})
                .get(category_key, {})
                .get("score")
            )
            if isinstance(score, (int, float)) and not isinstance(score, bool):
                explanations[car_key] = (
                    COMPARE_SCORE_EXPLANATION_TEMPLATE_HE.format(score=int(score))
                    if COMPARE_SCORE_EXPLANATION_TEMPLATE_HE
                    else ""
                )
            else:
                explanations[car_key] = ""
        category_explanations.append(
            {
                "category_key": category_key,
                "title_he": "",
                "winner": _normalize_compare_writer_winner(winner, car_keys) or "tie",
                "explanations": explanations,
                "why_it_scored_that_way": [],
            }
        )

    raw_caveats = stage_b_output.get("caveats")
    caveats = []
    if isinstance(raw_caveats, list):
        for item in raw_caveats[:3]:
            caveat = str(item or "").strip()
            if caveat:
                caveats.append(caveat)

    return {
        "overall_summary": summary,
        "category_explanations": category_explanations,
        "disclaimers_he": caveats,
    }


def _build_stage_a_summary(computed_result: Dict[str, Any]) -> Dict[str, Any]:
    slot_keys = _ordered_compare_slot_keys(
        (computed_result.get("cars") or {}) if isinstance(computed_result, dict) else {}
    )
    category_winners = []
    for category_name, winner in (
        computed_result.get("category_winners", {}) or {}
    ).items():
        category_winners.append(
            {
                "name": category_name,
                "winner": _normalize_compare_writer_winner(winner, slot_keys) or "tie",
            }
        )
    comparison_status = computed_result.get("comparison_status", {}) or {}
    balanced = bool(comparison_status.get("balanced", True))
    return {
        "summary": "סיכום מספרי של ההשוואה."
        if balanced
        else "סיכום מספרי חלקי של ההשוואה.",
        "winner": _normalize_compare_writer_winner(
            computed_result.get("overall_winner"), slot_keys
        )
        or "tie",
        "category_winners": category_winners,
        "caveats": (
            ["המידע עשוי להשתנות."] if balanced else [PARTIAL_COMPARISON_DISCLAIMER]
        ),
        "balanced": balanced,
        "cars_with_evidence": int(comparison_status.get("cars_with_evidence", 0)),
        "requested_cars": int(comparison_status.get("requested_cars", 0)),
    }


def _build_stage_b_payload(
    narrative: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not isinstance(narrative, dict):
        return None
    return {
        "categories": narrative.get("category_explanations", []),
        "narrative": narrative.get("overall_summary"),
    }


def _has_usable_comparison_narrative(narrative: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(narrative, dict):
        return False
    if str(narrative.get("overall_summary") or "").strip():
        return True
    if any(str(item or "").strip() for item in (narrative.get("disclaimers_he") or [])):
        return True
    for category in narrative.get("category_explanations") or []:
        if not isinstance(category, dict):
            continue
        explanations = (
            category.get("explanations")
            if isinstance(category.get("explanations"), dict)
            else {}
        )
        if any(str(value or "").strip() for value in explanations.values()):
            return True
        if any(
            str(value or "").strip()
            for value in (category.get("why_it_scored_that_way") or [])
        ):
            return True
    return False


def _normalize_stage_b_category_for_narrative(
    category: Any,
) -> Optional[Dict[str, Any]]:
    if not isinstance(category, dict):
        return None
    fallback_text = str(
        category.get("why") or category.get("text") or category.get("summary") or ""
    ).strip()
    source_explanations = (
        category.get("explanations")
        if isinstance(category.get("explanations"), dict)
        else {}
    )
    explanations = {}
    for car_key in ("car_1", "car_2", "car_3"):
        text = str(source_explanations.get(car_key) or fallback_text or "").strip()
        if text:
            explanations[car_key] = text
    why_list = category.get("why_it_scored_that_way")
    if not isinstance(why_list, list):
        why_list = category.get("tips")
    if not isinstance(why_list, list):
        why_list = [fallback_text] if fallback_text else []
    return {
        "category_key": category.get("category_key") or category.get("name") or "",
        "title_he": category.get("title_he") or "",
        "winner": category.get("winner") or "",
        "explanations": explanations,
        "why_it_scored_that_way": why_list,
    }


def resolve_comparison_narrative(
    computed_result: Optional[Dict[str, Any]],
    ai_payload: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    if isinstance(computed_result, dict):
        stored_narrative = computed_result.get("narrative")
        if isinstance(stored_narrative, dict):
            candidates.append(stored_narrative)
        stored_decision_result = computed_result.get("decision_result")
        if isinstance(stored_decision_result, dict):
            candidates.append(
                convert_decision_result_to_narrative(
                    {
                        "decision_result": sanitize_decision_result(
                            stored_decision_result, {}, computed_result, None
                        )
                    },
                    {},
                )
            )
        if any(
            key in computed_result
            for key in ("overall_summary", "category_explanations", "disclaimers_he")
        ):
            candidates.append(
                {
                    "overall_summary": computed_result.get("overall_summary"),
                    "category_explanations": computed_result.get(
                        "category_explanations"
                    ),
                    "disclaimers_he": computed_result.get("disclaimers_he"),
                }
            )
        if ai_payload is None and isinstance(computed_result.get("ai"), dict):
            ai_payload = computed_result.get("ai")
    if isinstance(ai_payload, dict):
        stage_b = ai_payload.get("stage_b")
        if isinstance(stage_b, dict):
            if isinstance(stage_b.get("decision_result"), dict):
                candidates.append(
                    convert_decision_result_to_narrative(
                        {
                            "decision_result": sanitize_decision_result(
                                stage_b.get("decision_result"),
                                {},
                                computed_result or {},
                                None,
                            )
                        },
                        {},
                    )
                )
            raw_categories = stage_b.get("categories")
            if not isinstance(raw_categories, list):
                raw_categories = stage_b.get("category_explanations")
            normalized_categories = [
                item
                for item in (
                    _normalize_stage_b_category_for_narrative(category)
                    for category in (raw_categories or [])
                )
                if item
            ]
            candidates.append(
                {
                    "overall_summary": (
                        stage_b.get("narrative")
                        or stage_b.get("summary")
                        or stage_b.get("overall_summary")
                        or ""
                    ),
                    "category_explanations": normalized_categories,
                    "disclaimers_he": stage_b.get("disclaimers_he")
                    or stage_b.get("caveats")
                    or [],
                }
            )
    for candidate in candidates:
        sanitized = sanitize_comparison_narrative(candidate)
        if _has_usable_comparison_narrative(sanitized):
            return sanitized
    return None


def build_stored_comparison_ai_payload(
    computed_result: Optional[Dict[str, Any]],
    narrative: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    raw_ai = (
        computed_result.get("ai")
        if isinstance(computed_result, dict)
        and isinstance(computed_result.get("ai"), dict)
        else None
    )
    ai_payload = build_ai_payload(
        computed_result if isinstance(computed_result, dict) else {},
        narrative,
        (raw_ai or {}).get("status") or ("ok" if narrative else "fallback"),
        (raw_ai or {}).get("reason")
        if raw_ai
        else (None if narrative else "stage_b_error"),
    )
    if raw_ai and raw_ai.get("error"):
        ai_payload["error"] = raw_ai["error"]
    return ai_payload


def build_ai_payload(
    computed_result: Dict[str, Any],
    narrative: Optional[Dict[str, Any]],
    status: str,
    reason: Optional[str],
) -> Dict[str, Any]:
    stage_b_payload = _build_stage_b_payload(narrative) or {}
    decision_result = (
        computed_result.get("decision_result")
        if isinstance(computed_result.get("decision_result"), dict)
        else None
    )
    if decision_result:
        stage_b_payload["decision_result"] = decision_result
    return {
        "status": status,
        "reason": reason,
        "stage_a": _build_stage_a_summary(computed_result),
        "stage_b": stage_b_payload or None,
    }


def convert_writer_response_to_narrative(
    validated_payload: Dict[str, Any], cars_selected_slots: Dict
) -> Dict[str, Any]:
    car_keys = _ordered_compare_slot_keys(cars_selected_slots)

    category_explanations = []
    for cat in validated_payload.get("categories", []):
        explanations = {}
        source_explanations = (
            cat.get("explanations") if isinstance(cat.get("explanations"), dict) else {}
        )
        for car_key in car_keys:
            explanations[car_key] = source_explanations.get(car_key) or cat.get("why", "")
        category_explanations.append(
            {
                "category_key": cat.get("name"),
                "title_he": "",
                "winner": _normalize_compare_writer_winner(cat.get("winner"), car_keys)
                or "tie",
                "explanations": explanations,
                "why_it_scored_that_way": cat.get("tips", []),
            }
        )
    return {
        "overall_summary": validated_payload.get("summary", ""),
        "category_explanations": category_explanations,
        "disclaimers_he": validated_payload.get("caveats", []),
    }


def convert_decision_result_to_narrative(
    validated_payload: Dict[str, Any], cars_selected_slots: Dict
) -> Dict[str, Any]:
    decision_result = (
        validated_payload.get("decision_result")
        if isinstance(validated_payload, dict)
        else {}
    )
    if not isinstance(decision_result, dict):
        return build_deterministic_fallback_narrative(cars_selected_slots, {})

    car_keys = _ordered_compare_slot_keys(
        cars_selected_slots,
        _extract_decision_slot_keys(decision_result),
    )
    category_explanations = []
    disclaimers: List[str] = []
    for cat in (
        decision_result.get("category_decisions")
        if isinstance(decision_result.get("category_decisions"), list)
        else []
    ):
        if not isinstance(cat, dict):
            continue
        explanations = {}
        preferred = _decision_label(cat.get("preferred"), car_keys)
        for car_key in car_keys:
            choose_items = (
                decision_result.get(f"choose_{car_key}_if")
                if isinstance(decision_result.get(f"choose_{car_key}_if"), list)
                else []
            )
            avoid_items = (
                decision_result.get(f"avoid_or_check_{car_key}_if")
                if isinstance(decision_result.get(f"avoid_or_check_{car_key}_if"), list)
                else []
            )
            if preferred == car_key and choose_items:
                explanations[car_key] = str(choose_items[0]).strip()
            elif avoid_items:
                explanations[car_key] = str(avoid_items[0]).strip()
            elif str(cat.get("why") or "").strip():
                explanations[car_key] = str(cat.get("why") or "").strip()
        caveat = str(cat.get("important_caveat") or "").strip()
        if caveat:
            _append_unique_text(disclaimers, caveat, max_items=3)
        why_list = [text for text in (str(cat.get("why") or "").strip(), caveat) if text]
        category_explanations.append(
            {
                "category_key": cat.get("category_key") or "",
                "title_he": cat.get("category_name_he") or "",
                "winner": preferred,
                "explanations": explanations,
                "why_it_scored_that_way": why_list[:2],
            }
        )
    overall = (
        decision_result.get("overall_decision")
        if isinstance(decision_result.get("overall_decision"), dict)
        else {}
    )
    summary = (
        str(decision_result.get("practical_summary") or "").strip()
        or str(overall.get("text") or "").strip()
        or DECISION_TEXT_FALLBACK_HE
    )
    return {
        "overall_summary": summary,
        "category_explanations": category_explanations,
        "disclaimers_he": disclaimers,
    }


def call_gemini_compare_writer(
    prompt: str, timeout_sec: int = COMPARE_WRITER_TIMEOUT_SEC
) -> Tuple[Optional[Dict], Optional[str]]:
    """Call Gemini Stage B writer WITHOUT grounding tools."""
    from app.factory import AI_EXECUTOR

    start_time = pytime.perf_counter()
    prompt_chars = len(prompt or "")
    is_retry_summary_only = "RETRY_MODE_SUMMARY_ONLY" in (prompt or "")
    max_output_tokens = (
        COMPARE_WRITER_RETRY_MAX_OUTPUT_TOKENS
        if is_retry_summary_only
        else COMPARE_WRITER_MAX_OUTPUT_TOKENS
    )
    outcome = "error"
    outcome_reason = None
    _inc_compare_metric("compare_ai_calls_total")

    try:
        if extensions.ai_client is None:
            outcome_reason = "CLIENT_NOT_INITIALIZED"
            return None, "CLIENT_NOT_INITIALIZED"

        config = genai_types.GenerateContentConfig(
            temperature=0.3,
            top_p=0.8,
            top_k=20,
            max_output_tokens=max_output_tokens,
            response_mime_type="application/json",
        )

        def _invoke():
            return extensions.ai_client.models.generate_content(
                model=COMPARISON_MODEL_ID,
                contents=prompt,
                config=config,
            )

        try:
            future = AI_EXECUTOR.submit(_invoke)
            resp = future.result(timeout=timeout_sec)
        except concurrent.futures.TimeoutError:
            future.cancel()
            _inc_compare_metric("compare_ai_failures_total", reason="timeout")
            outcome = "timeout"
            outcome_reason = "CALL_TIMEOUT"
            return None, "CALL_TIMEOUT"
        except Exception as e:
            _log_ai_client_error("comparison_stage_b", e)
            _inc_compare_metric("compare_stage_b_error_total")
            outcome_reason = type(e).__name__
            if _is_output_too_long_error(str(e)):
                return None, "CALL_FAILED_OUTPUT_TOO_LONG"
            return None, f"CALL_FAILED:{type(e).__name__}"

        text = (getattr(resp, "text", "") or "").strip()
        if not text:
            outcome_reason = "EMPTY_RESPONSE"
            return None, "EMPTY_RESPONSE"
        COMPARE_AI_METRICS["compare_ai_output_tokens_estimate"] = _estimate_token_count(
            text
        )

        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                outcome = "ok"
                return parsed, None
        except json.JSONDecodeError:
            try:
                from json_repair import repair_json

                repaired = repair_json(text)
                parsed = json.loads(repaired)
                if isinstance(parsed, dict):
                    outcome = "ok"
                    return parsed, None
            except Exception:
                pass

        outcome_reason = "MODEL_JSON_INVALID"
        return None, "MODEL_JSON_INVALID"
    finally:
        duration_ms = (pytime.perf_counter() - start_time) * 1000
        current_app.logger.info(
            "[AI] feature=comparison_stage_b model=%s duration_ms=%.2f max_output_tokens=%s prompt_chars=%s prompt_tokens_est=%s timeout_ms=%s tools_enabled=%s retry_count=%s outcome=%s reason=%s",
            COMPARISON_MODEL_ID,
            duration_ms,
            max_output_tokens,
            prompt_chars,
            _estimate_token_count(prompt),
            int(timeout_sec * 1000),
            False,
            0,
            outcome,
            outcome_reason,
        )


def generate_narrative(
    cars_selected_slots: Dict, computed_result: Dict, timeout_sec: int = 60
) -> Optional[Dict]:
    """
    Generate short human-friendly explanations using Gemini Flash WITHOUT grounding.
    Input: only computed scores and display names (no new data retrieval).
    Returns strict JSON narrative or None on failure.
    """
    from app.factory import AI_EXECUTOR

    try:
        if extensions.ai_client is None:
            current_app.logger.warning("[NARRATIVE] AI client not initialized")
            return None

        car_summaries = {}
        for slot_key, slot_data in cars_selected_slots.items():
            car_computed = computed_result.get("cars", {}).get(slot_key, {})
            car_summaries[slot_key] = {
                "display_name": slot_data.get("display_name", slot_key),
                "overall_score": car_computed.get("overall_score"),
                "categories": {},
            }
            for cat_name, cat_data in car_computed.get("categories", {}).items():
                car_summaries[slot_key]["categories"][cat_name] = cat_data.get("score")

        category_winners = computed_result.get("category_winners", {})
        overall_winner = computed_result.get("overall_winner")
        top_reasons = computed_result.get("top_reasons", [])

        cat_names_he = {
            "reliability_risk": "אמינות וסיכונים",
            "ownership_cost": "עלות אחזקה",
            "practicality_comfort": "נוחות ופרקטיות",
            "driving_performance": "ביצועים ונהיגה",
        }

        slot_keys = list(cars_selected_slots.keys())
        car_explanations_template = ", ".join(
            f'"{k}": "string (1-2 sentences)"' for k in slot_keys
        )

        prompt = f"""You are a car comparison summary writer. Write SHORT, friendly, user-facing explanations in Hebrew.

INPUT DATA (already computed, DO NOT add new facts):
{json.dumps(car_summaries, ensure_ascii=False, indent=2)}

Category winners: {json.dumps(category_winners, ensure_ascii=False)}
Overall winner: {json.dumps(overall_winner, ensure_ascii=False)}
Top reasons: {json.dumps(top_reasons, ensure_ascii=False)}

RULES:
1. Do NOT add new factual claims or data not present in the input.
2. Do NOT introduce new sources or URLs.
3. Explain ONLY the scores and winners given above.
4. Use simple, friendly Hebrew. Fewer numbers, more human language.
5. When scores are very close (within {TIE_THRESHOLD} points), say "צמוד" (close race).
6. Return ONLY valid JSON. No markdown, no extra text.

Return this EXACT JSON structure:
{{{{
  "overall_summary": "string (2-4 sentences summarizing the comparison)",
  "category_explanations": [
    {{{{
      "category_key": "reliability_risk",
      "title_he": "{cat_names_he.get("reliability_risk", "")}",
      "winner": "car_1|car_2|car_3|tie",
      "explanations": {{{{ {car_explanations_template} }}}},
      "why_it_scored_that_way": ["string", "string"]
    }}}},
    {{{{
      "category_key": "ownership_cost",
      "title_he": "{cat_names_he.get("ownership_cost", "")}",
      "winner": "car_1|car_2|car_3|tie",
      "explanations": {{{{ {car_explanations_template} }}}},
      "why_it_scored_that_way": ["string", "string"]
    }}}},
    {{{{
      "category_key": "practicality_comfort",
      "title_he": "{cat_names_he.get("practicality_comfort", "")}",
      "winner": "car_1|car_2|car_3|tie",
      "explanations": {{{{ {car_explanations_template} }}}},
      "why_it_scored_that_way": ["string", "string"]
    }}}},
    {{{{
      "category_key": "driving_performance",
      "title_he": "{cat_names_he.get("driving_performance", "")}",
      "winner": "car_1|car_2|car_3|tie",
      "explanations": {{{{ {car_explanations_template} }}}},
      "why_it_scored_that_way": ["string", "string"]
    }}}}
  ],
  "disclaimers_he": ["הניקוד מבוסס על נתונים שנאספו מהאינטרנט ועשוי להשתנות", "מומלץ לבצע בדיקה מקצועית לפני רכישה"]
}}}}
"""

        config = genai_types.GenerateContentConfig(
            temperature=0.4,
            top_p=0.9,
            top_k=40,
            response_mime_type="application/json",
        )

        def _invoke():
            return extensions.ai_client.models.generate_content(
                model=COMPARISON_MODEL_ID,
                contents=prompt,
                config=config,
            )

        try:
            future = AI_EXECUTOR.submit(_invoke)
        except Exception:
            current_app.logger.warning(
                "[NARRATIVE] Executor saturated, skipping narrative"
            )
            return None

        try:
            resp = future.result(timeout=timeout_sec)
        except concurrent.futures.TimeoutError:
            future.cancel()
            current_app.logger.warning("[NARRATIVE] Timeout generating narrative")
            return None
        except Exception as e:
            current_app.logger.warning(f"[NARRATIVE] Call failed: {type(e).__name__}")
            return None

        if resp is None:
            return None

        text = (getattr(resp, "text", "") or "").strip()
        if not text:
            return None

        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            try:
                from json_repair import repair_json

                repaired = repair_json(text)
                parsed = json.loads(repaired)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass

        current_app.logger.warning("[NARRATIVE] Failed to parse narrative response")
        return None

    except Exception as e:
        current_app.logger.warning(f"[NARRATIVE] Unexpected error: {e}")
        return None


__all__ = [
    "call_gemini_compare_writer",
    "generate_narrative",
    "validate_compare_writer_response",
    "sanitize_decision_result",
    "_validate_decision_writer_response",
    "_summarize_compare_writer_payload",
    "_summarize_comparison_narrative_shape",
    "_validate_compare_writer_response",
    "_salvage_partial_writer_output",
    "_build_stage_a_summary",
    "_build_stage_b_payload",
    "_has_usable_comparison_narrative",
    "_normalize_stage_b_category_for_narrative",
    "resolve_comparison_narrative",
    "build_stored_comparison_ai_payload",
    "build_ai_payload",
    "convert_writer_response_to_narrative",
    "convert_decision_result_to_narrative",
]
