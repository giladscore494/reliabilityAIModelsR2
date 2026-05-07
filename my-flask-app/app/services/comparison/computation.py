# -*- coding: utf-8 -*-
"""Comparison computation and normalization helpers."""

import json
from typing import Any, Dict, List, Optional, Tuple

from flask import current_app

from app.services.comparison.constants import CATEGORY_LABELS_HE
from app.services.comparison.parsing import _truncate_log_payload
from app.services.comparison.scoring import (
    CATEGORY_SCORE_CONFIG,
    _has_any_stage_a_evidence,
    compute_category_score,
    compute_overall_score,
    determine_winner,
)
from app.services.comparison.writer import call_gemini_compare_writer


def _is_real_winner_id(winner_id: Optional[str], results: Dict[str, Any]) -> bool:
    cars = (results.get("cars") or {}) if isinstance(results, dict) else {}
    return isinstance(winner_id, str) and winner_id in cars


def _build_single_winner_top_reasons(
    results: Dict[str, Any], winner_id: str
) -> List[str]:
    winner_cats = ((results.get("cars") or {}).get(winner_id) or {}).get(
        "categories"
    ) or {}
    sorted_cats = sorted(
        [
            (cat_name, data.get("score"))
            for cat_name, data in winner_cats.items()
            if isinstance(data, dict) and data.get("score") is not None
        ],
        key=lambda x: x[1],
        reverse=True,
    )
    return [
        f"יתרון ב{CATEGORY_LABELS_HE.get(cat_name, cat_name)}"
        for cat_name, score in sorted_cats[:3]
    ]


def _build_tie_top_reasons(results: Dict[str, Any]) -> List[str]:
    cars = results.get("cars") or {}
    category_winners = results.get("category_winners") or {}
    tie_candidates = []
    for cat_name, winner in category_winners.items():
        if winner != "tie":
            continue
        scores = []
        for car_data in cars.values():
            cat_data = ((car_data or {}).get("categories") or {}).get(cat_name) or {}
            score = cat_data.get("score")
            if score is not None:
                scores.append(score)
        if not scores:
            continue
        avg_score = sum(scores) / len(scores)
        spread = max(scores) - min(scores) if len(scores) >= 2 else 0.0
        tie_candidates.append((avg_score, spread, cat_name))

    tie_candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
    reasons = [
        f"ב{CATEGORY_LABELS_HE.get(cat_name, cat_name)} הפער קטן מאוד בין הרכבים."
        for _avg_score, spread, cat_name in tie_candidates[:3]
    ]
    if reasons:
        return reasons
    return ["הציונים הכוללים קרובים מאוד, ולכן אין מנצח ברור בהשוואה."]


def _build_safe_top_reasons(results: Dict[str, Any]) -> List[str]:
    winner_id = results.get("overall_winner")
    if _is_real_winner_id(winner_id, results):
        reasons = _build_single_winner_top_reasons(results, winner_id)
        if reasons:
            return reasons
    if winner_id == "tie":
        return _build_tie_top_reasons(results)
    return ["לא ניתן לקבוע מנצח ברור על בסיס המידע הזמין."]


def _build_overall_winner_message(results: Dict[str, Any]) -> str:
    winner_id = results.get("overall_winner")
    if _is_real_winner_id(winner_id, results):
        return "נמצא יתרון כולל לאחד הרכבים."
    if winner_id == "tie":
        return "ההשוואה הכוללת צמודה ולכן הוגדרה כתיקו."
    return "לא ניתן לקבוע מנצח כולל על בסיס המידע הזמין."


def compute_comparison_results(model_output: Dict) -> Dict:
    cars_data = model_output.get("cars", {})
    requested_cars = len(cars_data)
    evidence_cars = 0
    results = {
        "cars": {},
        "category_winners": {},
        "metric_winners": {},
        "overall_winner": None,
        "overall_winner_message": "",
        "top_reasons": [],
        "comparison_status": {
            "requested_cars": requested_cars,
            "cars_with_evidence": 0,
            "balanced": True,
        },
    }

    overall_scores = {}
    for car_id, car_data in cars_data.items():
        has_evidence = _has_any_stage_a_evidence(car_data)
        if has_evidence:
            evidence_cars += 1
        car_result = {
            "categories": {},
            "overall_score": None,
            "evidence_available": has_evidence,
            "evidence_summary": {
                "source_count": len(car_data.get("sources") or []),
                "note_count": len(car_data.get("short_notes") or []),
            },
        }

        category_scores = {}
        for cat_name in CATEGORY_SCORE_CONFIG.keys():
            cat_score, metric_scores = compute_category_score(car_data, cat_name)
            car_result["categories"][cat_name] = {
                "score": cat_score,
                "metrics": metric_scores,
            }
            category_scores[cat_name] = cat_score

        car_result["overall_score"] = compute_overall_score(category_scores)
        overall_scores[car_id] = car_result["overall_score"]
        results["cars"][car_id] = car_result

    for cat_name in CATEGORY_SCORE_CONFIG.keys():
        cat_scores = {
            car_id: results["cars"][car_id]["categories"][cat_name]["score"]
            for car_id in cars_data.keys()
        }
        results["category_winners"][cat_name] = determine_winner(cat_scores)

    for cat_name, cat_def in CATEGORY_SCORE_CONFIG.items():
        results["metric_winners"][cat_name] = {}
        for metric_name in cat_def.get("subfactors", {}).keys():
            metric_scores = {}
            for car_id in cars_data.keys():
                car_metrics = (
                    results["cars"][car_id]["categories"]
                    .get(cat_name, {})
                    .get("metrics", {})
                )
                metric_scores[car_id] = car_metrics.get(metric_name)
            results["metric_winners"][cat_name][metric_name] = determine_winner(
                metric_scores
            )

    results["overall_winner"] = determine_winner(overall_scores)
    results["comparison_status"] = {
        "requested_cars": requested_cars,
        "cars_with_evidence": evidence_cars,
        "balanced": evidence_cars == requested_cars,
    }
    results["overall_winner_message"] = _build_overall_winner_message(results)
    results["top_reasons"] = _build_safe_top_reasons(results)
    return results


def _attempt_schema_repair(payload: Any, request_id: str) -> Optional[Dict[str, Any]]:
    repair_prompt = (
        "Return EXACTLY one JSON object with keys grounding_successful, assumptions, search_queries_used, cars. "
        "Do not return arrays at top-level and do not add markdown. "
        f"Normalize this payload into that object schema:\n{json.dumps(payload, ensure_ascii=False)}"
    )
    repaired, repair_error = call_gemini_compare_writer(repair_prompt, timeout_sec=25)
    if repair_error or not isinstance(repaired, dict):
        current_app.logger.warning(
            "[AI_SCHEMA] schema_repair_failed request_id=%s error=%s payload_sample=%s",
            request_id,
            repair_error,
            _truncate_log_payload(payload),
        )
        return None
    return repaired


def normalize_model_output(
    parsed: Any, request_id: str
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if parsed is None:
        return None, "MODEL_SHAPE_INVALID"
    if isinstance(parsed, dict):
        return parsed, None
    if isinstance(parsed, list):
        if len(parsed) == 1 and isinstance(parsed[0], dict):
            candidate = parsed[0]
            needs_repair = not (
                isinstance(candidate.get("cars"), dict)
                and "grounding_successful" in candidate
            )
            repaired = (
                _attempt_schema_repair(candidate, request_id) if needs_repair else None
            )
            current_app.logger.warning(
                "[AI_SCHEMA] list_single_dict_normalized request_id=%s repaired=%s payload_sample=%s",
                request_id,
                bool(repaired),
                _truncate_log_payload(parsed[0]),
            )
            return repaired or parsed[0], None
        current_app.logger.error(
            "[AI_SCHEMA] invalid_list_shape len=%d request_id=%s payload_sample=%s",
            len(parsed),
            request_id,
            _truncate_log_payload(parsed),
        )
        return None, "MODEL_SHAPE_INVALID"
    current_app.logger.error(
        "[AI_SCHEMA] unexpected_type=%s request_id=%s payload_sample=%s",
        type(parsed).__name__,
        request_id,
        _truncate_log_payload(parsed),
    )
    return None, "MODEL_SHAPE_INVALID"


def build_sources_index(model_output: Dict) -> Dict:
    from app.services.comparison.grounding import build_sources_index as _impl

    return _impl(model_output)


def build_sources_index_from_flat(merged_output: Dict) -> Dict:
    from app.services.comparison.grounding import build_sources_index_from_flat as _impl

    return _impl(merged_output)
