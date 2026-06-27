# -*- coding: utf-8 -*-
"""Decision result sanitization and deterministic fallback helpers."""

import logging
from typing import Any, Dict, List, Optional, Tuple

from flask import current_app, has_app_context

from app.services.comparison.constants import (
    CATEGORY_LABELS_HE,
    DECISION_CATEGORY_DEFINITIONS,
    DECISION_FORBIDDEN_TEXT_RE,
    DECISION_NEUTRAL_FALLBACK_HE,
    DECISION_TEXT_FALLBACK_HE,
)
from app.services.comparison.parsing import (
    _extract_decision_slot_keys,
    _normalize_compare_writer_winner,
    _ordered_compare_slot_keys,
)


logger = logging.getLogger(__name__)


def _decision_label(value: Any, allowed_slots: Optional[List[str]] = None) -> str:
    if isinstance(value, str):
        normalized = value.strip()
        if normalized in {"tie", "depends", "unknown"}:
            return normalized
        if allowed_slots and normalized in allowed_slots:
            return normalized
        if normalized in {"car_1", "car_2", "car_3"}:
            return normalized
    return "unknown"


def _is_forbidden_decision_text(value: Any) -> bool:
    return isinstance(value, str) and bool(DECISION_FORBIDDEN_TEXT_RE.search(value))


def _sanitize_decision_text(
    value: Any, request_id: Optional[str], field_path: str
) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return ""
    if _is_forbidden_decision_text(text):
        active_logger = current_app.logger if has_app_context() else logger
        active_logger.warning(
            "[COMPARISON] decision_result text sanitized request_id=%s field=%s",
            request_id or "unknown",
            field_path,
        )
        return ""
    return text[:700]


def _sanitize_optional_decision_text(
    value: Any, request_id: Optional[str], field_path: str
) -> Optional[str]:
    if value is None:
        return None
    text = _sanitize_decision_text(value, request_id, field_path)
    return text or None


def _sanitize_decision_list(
    value: Any, request_id: Optional[str], field_path: str, max_items: int = 6
) -> List[str]:
    if not isinstance(value, list):
        return []
    out = []
    for idx, item in enumerate(value[:max_items]):
        text = _sanitize_decision_text(item, request_id, f"{field_path}.{idx}")
        if text:
            out.append(text)
    return out


def _decision_category_name_he(category_key: Any) -> str:
    normalized_key = str(category_key or "").strip()
    for key, name in DECISION_CATEGORY_DEFINITIONS:
        if key == normalized_key:
            return name
    return (
        CATEGORY_LABELS_HE.get(normalized_key)
        or normalized_key.replace("_", " ").strip()
    )


def _append_unique_text(target: List[str], text: str, max_items: int = 2) -> None:
    normalized = " ".join(str(text or "").split()).strip()
    if not normalized or normalized in target or len(target) >= max_items:
        return
    target.append(normalized)


def _join_hebrew_labels(labels: List[str]) -> str:
    normalized = [
        str(label or "").strip() for label in labels if str(label or "").strip()
    ]
    if not normalized:
        return ""
    if len(normalized) == 1:
        return normalized[0]
    if len(normalized) == 2:
        return f"{normalized[0]} ו{normalized[1]}"
    return f"{', '.join(normalized[:-1])} ו{normalized[-1]}"


def _build_slot_guidance_lists(
    slot_key: str,
    cars_selected_slots: Dict[str, Dict[str, Any]],
    computed_result: Dict[str, Any],
    source_decision_result: Any,
) -> Tuple[List[str], List[str]]:
    category_items = (
        source_decision_result.get("category_decisions")
        if isinstance(source_decision_result, dict)
        and isinstance(source_decision_result.get("category_decisions"), list)
        else []
    )
    key_differences = (
        source_decision_result.get("key_differences")
        if isinstance(source_decision_result, dict)
        and isinstance(source_decision_result.get("key_differences"), list)
        else []
    )
    overall = (
        source_decision_result.get("overall_decision")
        if isinstance(source_decision_result, dict)
        and isinstance(source_decision_result.get("overall_decision"), dict)
        else {}
    )
    practical_summary = _sanitize_decision_text(
        source_decision_result.get("practical_summary")
        if isinstance(source_decision_result, dict)
        else None,
        None,
        f"{slot_key}.practical_summary",
    )
    slot_keys = _ordered_compare_slot_keys(
        cars_selected_slots,
        computed_result.get("cars") or {},
        _extract_decision_slot_keys(source_decision_result),
    )
    car_label = (
        (cars_selected_slots.get(slot_key) or {}).get("display_name") or slot_key
    ).strip()
    category_advantages: List[str] = []
    category_tradeoffs: List[str] = []
    shared_caveats: List[str] = []

    for item in category_items:
        if not isinstance(item, dict):
            continue
        category_name = _decision_category_name_he(
            item.get("category_key") or item.get("category_name_he")
        )
        preferred = _decision_label(item.get("preferred"), slot_keys)
        if preferred == slot_key:
            _append_unique_text(category_advantages, category_name)
        elif preferred not in {"unknown", "depends", "tie"}:
            _append_unique_text(category_tradeoffs, category_name)
        caveat = _sanitize_optional_decision_text(
            item.get("important_caveat"),
            None,
            f"{slot_key}.important_caveat",
        )
        if caveat:
            _append_unique_text(shared_caveats, caveat, max_items=1)

    diff_insight = ""
    for item in key_differences:
        if not isinstance(item, dict):
            continue
        title = _sanitize_decision_text(
            item.get("title"), None, f"{slot_key}.diff_title"
        )
        detail = _sanitize_decision_text(
            item.get(slot_key), None, f"{slot_key}.diff_value"
        )
        if title and detail:
            diff_insight = f"{title}: {detail}"
            break

    choose_items: List[str] = []
    avoid_items: List[str] = []
    if category_advantages:
        _append_unique_text(
            choose_items,
            f"אם חשובים לך במיוחד {_join_hebrew_labels(category_advantages[:2])}.",
        )
    if _decision_label(overall.get("label"), slot_keys) == slot_key:
        _append_unique_text(
            choose_items,
            "אם בתמונה הכוללת זו נראית הבחירה הסבירה יותר עבורך, בכפוף למצב הרכב בפועל.",
        )
    elif diff_insight:
        _append_unique_text(
            choose_items, f"אם הפער הבא מתאים לשימוש שלך: {diff_insight}"
        )
    elif practical_summary:
        _append_unique_text(
            choose_items, f"אם הכיוון הכללי של ההשוואה מתאים לך: {practical_summary}"
        )

    if category_tradeoffs:
        _append_unique_text(
            avoid_items,
            f"בדוק אם הפשרה ב{_join_hebrew_labels(category_tradeoffs[:2])} מקובלת עליך מול החלופות.",
        )
    if shared_caveats:
        _append_unique_text(avoid_items, shared_caveats[0])
    _append_unique_text(
        avoid_items,
        "בדוק היסטוריית טיפולים, תאונות, אחריות ועלויות אחזקה לפני החלטה.",
    )

    if not choose_items:
        _append_unique_text(
            choose_items,
            f"אם {car_label} מתאים לצרכים שלך אחרי בדיקת מצב, היסטוריה ועלויות צפויות.",
        )
    return choose_items[:2], avoid_items[:2]


def build_deterministic_decision_result(
    cars_selected_slots: Dict[str, Dict[str, Any]],
    computed_result: Optional[Dict[str, Any]] = None,
    source_decision_result: Any = None,
) -> Dict[str, Any]:
    computed_result = computed_result if isinstance(computed_result, dict) else {}
    slot_keys = _ordered_compare_slot_keys(
        cars_selected_slots,
        computed_result.get("cars") or {},
        _extract_decision_slot_keys(source_decision_result),
    )
    # Single-pass design: there is no scoring engine and therefore no
    # overall_winner to lean on. When the grounded model output cannot be
    # parsed, we never manufacture a winner — we return a clean neutral
    # "unknown" decision and let the model's own reasoning (when present)
    # drive the result via sanitize_decision_result.
    label = "unknown"
    text = DECISION_NEUTRAL_FALLBACK_HE

    category_decisions = []

    result: Dict[str, Any] = {
        "overall_decision": {"label": label, "text": text},
        "category_decisions": category_decisions,
        "key_differences": [],
        "competitors_to_consider": [],
        "practical_summary": "הבחירה הסבירה יותר תלויה בשימוש, בתקציב, במצב הרכב בפועל ובבדיקה מקצועית לפני החלטה.",
    }
    for slot_key in slot_keys:
        choose_items, avoid_items = _build_slot_guidance_lists(
            slot_key,
            cars_selected_slots,
            computed_result,
            source_decision_result,
        )
        result[f"choose_{slot_key}_if"] = choose_items
        result[f"avoid_or_check_{slot_key}_if"] = avoid_items
    return result


def sanitize_decision_result(
    decision_result: Any,
    cars_selected_slots: Optional[Dict[str, Dict[str, Any]]] = None,
    computed_result: Optional[Dict[str, Any]] = None,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    slot_keys = _ordered_compare_slot_keys(
        cars_selected_slots or {},
        (computed_result or {}).get("cars") or {},
        _extract_decision_slot_keys(decision_result),
    )
    fallback = build_deterministic_decision_result(
        cars_selected_slots or {},
        computed_result or {},
        decision_result,
    )
    if not isinstance(decision_result, dict):
        return fallback
    overall = (
        decision_result.get("overall_decision")
        if isinstance(decision_result.get("overall_decision"), dict)
        else {}
    )
    overall_label = _decision_label(overall.get("label"), slot_keys)
    sanitized = {
        "overall_decision": {
            "label": overall_label,
            "text": _sanitize_decision_text(
                overall.get("text"), request_id, "overall_decision.text"
            )
            or fallback["overall_decision"]["text"],
        },
        "category_decisions": [],
        "key_differences": [],
        "competitors_to_consider": [],
        "practical_summary": _sanitize_decision_text(
            decision_result.get("practical_summary"), request_id, "practical_summary"
        )
        or "",
    }
    for slot_key in slot_keys:
        choose_key = f"choose_{slot_key}_if"
        avoid_key = f"avoid_or_check_{slot_key}_if"
        sanitized[choose_key] = _sanitize_decision_list(
            decision_result.get(choose_key), request_id, choose_key
        ) or []
        sanitized[avoid_key] = _sanitize_decision_list(
            decision_result.get(avoid_key), request_id, avoid_key
        ) or []
    raw_categories = (
        decision_result.get("category_decisions")
        if isinstance(decision_result.get("category_decisions"), list)
        else []
    )
    allowed_names = dict(DECISION_CATEGORY_DEFINITIONS)
    for item in raw_categories:
        if not isinstance(item, dict):
            continue
        key = str(item.get("category_key") or "").strip()
        if not key:
            continue
        name = allowed_names.get(key, item.get("category_name_he") or key)
        why = _sanitize_decision_text(item.get("why"), request_id, f"category_decisions.{key}.why")
        if not why:
            continue
        sanitized["category_decisions"].append(
            {
                "category_key": key,
                "category_name_he": _sanitize_decision_text(
                    item.get("category_name_he") or name,
                    request_id,
                    f"category_decisions.{key}.name",
                )
                or name,
                "preferred": _decision_label(item.get("preferred"), slot_keys),
                "why": why[:160],
            }
        )
    raw_diffs = (
        decision_result.get("key_differences")
        if isinstance(decision_result.get("key_differences"), list)
        else []
    )
    for idx, item in enumerate(raw_diffs[:4]):
        if not isinstance(item, dict):
            continue
        cleaned_diff = {
            "title": _sanitize_decision_text(
                item.get("title"), request_id, f"key_differences.{idx}.title"
            ),
            "meaning_for_buyer": _sanitize_decision_text(
                item.get("meaning_for_buyer"),
                request_id,
                f"key_differences.{idx}.meaning_for_buyer",
            ),
        }
        for slot_key in slot_keys:
            cleaned_diff[slot_key] = _sanitize_decision_text(
                item.get(slot_key), request_id, f"key_differences.{idx}.{slot_key}"
            )
        if cleaned_diff.get("title") and cleaned_diff.get("meaning_for_buyer") and any(cleaned_diff.get(slot_key) for slot_key in slot_keys):
            sanitized["key_differences"].append(cleaned_diff)
    raw_competitors = (
        decision_result.get("competitors_to_consider")
        if isinstance(decision_result.get("competitors_to_consider"), list)
        else []
    )
    for idx, item in enumerate(raw_competitors[:3]):
        if not isinstance(item, dict):
            continue
        model = _sanitize_decision_text(
            item.get("model"), request_id, f"competitors.{idx}.model"
        )
        why = _sanitize_decision_text(
            item.get("why_consider"), request_id, f"competitors.{idx}.why_consider"
        )
        if model or why:
            sanitized["competitors_to_consider"].append(
                {"model": model, "why_consider": why, "confidence": item.get("confidence") if item.get("confidence") in {"high", "medium", "low"} else "medium"}
            )
    overall_text = sanitized.get("overall_decision", {}).get("text")
    if sanitized.get("practical_summary") == overall_text:
        sanitized["practical_summary"] = ""
    return sanitized
