# -*- coding: utf-8 -*-
"""Fallback narratives and empty comparison payload helpers."""

import re
from typing import Any, Dict, List, Optional

from app.services.comparison.constants import (
    COMPARE_CATEGORY_NAMES,
    PARTIAL_COMPARISON_DISCLAIMER,
    PARTIAL_COMPARISON_SUMMARY_PREFIX,
)
from app.services.comparison.normalization import normalize_compare_writer_winner, ordered_compare_slot_keys


_COMPARE_SLOT_RE = re.compile(r"^car_(\d+)$")


SINGLE_CAR_CATEGORY_TEMPLATE = {
    "reliability": {
        "overall": None,
        "issue_frequency": None,
        "issue_severity": None,
        "repair_cost_risk": None,
        "recall_risk": None,
        "parts_complexity": None,
    },
    "ownership_cost": {
        "fuel_cost": None,
        "routine_maintenance": None,
        "repair_burden": None,
        "insurance_burden": None,
        "depreciation_risk": None,
    },
    "comfort_practicality": {
        "space": None,
        "ride_comfort": None,
        "trunk_usefulness": None,
        "daily_usability": None,
    },
    "performance_driving": {
        "power_feel": None,
        "power_to_weight": None,
        "braking_confidence": None,
        "handling_agility": None,
        "fun_to_drive": None,
    },
}

SINGLE_CAR_FACTS_TEMPLATE = {
    "horsepower": None,
    "weight_kg": None,
    "body_type": None,
    "fuel_type": None,
}


def _normalize_short_text(value: Any, max_len: int = 120) -> Optional[str]:
    if value is None:
        return None
    text = " ".join(str(value).split()).strip()
    if not text:
        return None
    return text[:max_len]






def _empty_single_car_payload() -> Dict[str, Any]:
    payload = {
        "car_name": None,
        "facts": dict(SINGLE_CAR_FACTS_TEMPLATE),
        "short_notes": [],
        "sources": [],
        "car_profile": {},
    }
    for category_name, template in SINGLE_CAR_CATEGORY_TEMPLATE.items():
        payload[category_name] = dict(template)
    return payload


def build_deterministic_fallback_narrative(
    cars_selected_slots: Dict, computed_result: Dict
) -> Dict[str, Any]:
    car_keys = ordered_compare_slot_keys(
        cars_selected_slots,
        (computed_result.get("cars") or {})
        if isinstance(computed_result, dict)
        else {},
    )
    category_explanations = []
    for cat in COMPARE_CATEGORY_NAMES:
        winner = (computed_result.get("category_winners", {}) or {}).get(cat) or "tie"
        explanations = {}
        for car_key in car_keys:
            explanations[car_key] = "מוצגת השוואה מספרית."
        category_explanations.append(
            {
                "category_key": cat,
                "title_he": "",
                "winner": normalize_compare_writer_winner(winner, car_keys) or "tie",
                "explanations": explanations,
                "why_it_scored_that_way": [
                    "הסבר AI לא זמין כרגע; מוצגת השוואה מספרית."
                ],
            }
        )
    return {
        "overall_summary": "הסבר AI לא זמין כרגע; מוצגת השוואה מספרית.",
        "category_explanations": category_explanations,
        "disclaimers_he": ["אפשר לנסות שוב."],
    }


def mark_partial_comparison_narrative(
    narrative: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Add an explicit disclaimer when Stage A succeeded for only part of the cars."""
    if not isinstance(narrative, dict):
        return narrative
    patched = dict(narrative)
    summary = str(patched.get("overall_summary") or "").strip()
    if summary and not summary.startswith(PARTIAL_COMPARISON_SUMMARY_PREFIX):
        patched["overall_summary"] = f"{PARTIAL_COMPARISON_SUMMARY_PREFIX} {summary}"
    disclaimers = list(patched.get("disclaimers_he") or [])
    if PARTIAL_COMPARISON_DISCLAIMER not in disclaimers:
        disclaimers.append(PARTIAL_COMPARISON_DISCLAIMER)
    patched["disclaimers_he"] = disclaimers[:3]
    return patched


def _empty_stage_a_output(
    cars_selected_slots: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    cars = {}
    for slot_key, slot_data in (cars_selected_slots or {}).items():
        empty_payload = _empty_single_car_payload()
        empty_payload["car_name"] = _normalize_short_text(
            (slot_data or {}).get("display_name"), 140
        )
        cars[slot_key] = empty_payload
    return {
        "cars": cars,
        "sources": [],
    }
