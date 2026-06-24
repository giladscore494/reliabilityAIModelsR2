# -*- coding: utf-8 -*-
"""Deterministic comparison scoring helpers."""

from typing import Any, Dict, List, Optional, Tuple

from app.services.comparison.constants import (
    HORSEPOWER_HIGH_THRESHOLD,
    HORSEPOWER_MEDIUM_THRESHOLD,
    HP_PER_TON_HIGH_THRESHOLD,
    HP_PER_TON_MEDIUM_THRESHOLD,
    KILOGRAMS_PER_TON,
    MIN_REASONABLE_VEHICLE_WEIGHT_KG,
    TIE_THRESHOLD,
)


CATEGORY_WEIGHTS = {
    "reliability_risk": 40,
    "ownership_cost": 25,
    "practicality_comfort": 20,
    "driving_performance": 15,
}


ORDINAL_SCORES_NEGATIVE = {
    "low": 100,
    "medium": 60,
    "high": 20,
}


ORDINAL_SCORES_POSITIVE = {
    "low": 20,
    "medium": 60,
    "high": 100,
}


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


CATEGORY_SCORE_CONFIG = {
    "reliability_risk": {
        "stage_a_key": "reliability",
        "weight": 40,
        "subfactors": {
            "reliability_reputation": {
                "weight": 12,
                "kind": "positive_label",
                "field": "overall",
            },
            "issue_frequency": {
                "weight": 8,
                "kind": "negative_label",
                "field": "issue_frequency",
            },
            "issue_severity": {
                "weight": 8,
                "kind": "negative_label",
                "field": "issue_severity",
            },
            "repair_cost_risk": {
                "weight": 5,
                "kind": "negative_label",
                "field": "repair_cost_risk",
            },
            "recall_risk": {
                "weight": 4,
                "kind": "negative_label",
                "field": "recall_risk",
            },
            "parts_complexity": {
                "weight": 3,
                "kind": "negative_label",
                "field": "parts_complexity",
            },
        },
    },
    "ownership_cost": {
        "stage_a_key": "ownership_cost",
        "weight": 25,
        "subfactors": {
            "fuel_cost": {"weight": 8, "kind": "negative_label", "field": "fuel_cost"},
            "routine_maintenance": {
                "weight": 6,
                "kind": "negative_label",
                "field": "routine_maintenance",
            },
            "repair_burden": {
                "weight": 5,
                "kind": "negative_label",
                "field": "repair_burden",
            },
            "insurance_burden": {
                "weight": 3,
                "kind": "negative_label",
                "field": "insurance_burden",
            },
            "depreciation_risk": {
                "weight": 3,
                "kind": "negative_label",
                "field": "depreciation_risk",
            },
        },
    },
    "practicality_comfort": {
        "stage_a_key": "comfort_practicality",
        "weight": 20,
        "subfactors": {
            "space": {"weight": 7, "kind": "positive_label", "field": "space"},
            "ride_comfort": {
                "weight": 5,
                "kind": "positive_label",
                "field": "ride_comfort",
            },
            "trunk_usefulness": {
                "weight": 4,
                "kind": "positive_label",
                "field": "trunk_usefulness",
            },
            "daily_usability": {
                "weight": 4,
                "kind": "positive_label",
                "field": "daily_usability",
            },
        },
    },
    "driving_performance": {
        "stage_a_key": "performance_driving",
        "weight": 15,
        "subfactors": {
            "power_capability": {"weight": 6, "kind": "power_capability"},
            "braking_confidence": {
                "weight": 3,
                "kind": "positive_label",
                "field": "braking_confidence",
            },
            "handling_agility": {
                "weight": 4,
                "kind": "positive_label",
                "field": "handling_agility",
            },
            "fun_to_drive": {
                "weight": 2,
                "kind": "positive_label",
                "field": "fun_to_drive",
            },
        },
    },
}


_LABEL_VALUES = {"low", "medium", "high"}


def _normalize_label(value: Any) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return normalized if normalized in _LABEL_VALUES else None


def _normalize_number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def score_ordinal_negative(
    value: Optional[str], confidence: float = 1.0
) -> Optional[float]:
    """Score ordinal value where low is good (e.g., risk: low=good)."""
    normalized = _normalize_label(value)
    score = ORDINAL_SCORES_NEGATIVE.get(normalized) if normalized else None
    if score is None:
        return None
    return round(score * confidence, 1)


def score_ordinal_positive(
    value: Optional[str], confidence: float = 1.0
) -> Optional[float]:
    """Score ordinal value where high is good (e.g., comfort: high=good)."""
    normalized = _normalize_label(value)
    score = ORDINAL_SCORES_POSITIVE.get(normalized) if normalized else None
    if score is None:
        return None
    return round(score * confidence, 1)


def _average_scores(scores: List[Optional[float]]) -> Optional[float]:
    valid = [score for score in scores if score is not None]
    if not valid:
        return None
    return round(sum(valid) / len(valid), 1)


def _derive_power_to_weight_label(car_data: Dict[str, Any]) -> Optional[str]:
    performance = car_data.get("performance_driving", {})
    explicit = _normalize_label((performance or {}).get("power_to_weight"))
    if explicit:
        return explicit

    facts = car_data.get("facts", {})
    horsepower = _normalize_number((facts or {}).get("horsepower"))
    weight_kg = _normalize_number((facts or {}).get("weight_kg"))
    if (
        horsepower is None
        or weight_kg is None
        or weight_kg < MIN_REASONABLE_VEHICLE_WEIGHT_KG
    ):
        return None

    hp_per_ton = (horsepower * KILOGRAMS_PER_TON) / weight_kg
    if hp_per_ton >= HP_PER_TON_HIGH_THRESHOLD:
        return "high"
    if hp_per_ton >= HP_PER_TON_MEDIUM_THRESHOLD:
        return "medium"
    return "low"


def _derive_horsepower_label(car_data: Dict[str, Any]) -> Optional[str]:
    horsepower = _normalize_number(((car_data.get("facts") or {}).get("horsepower")))
    if horsepower is None:
        return None
    if horsepower >= HORSEPOWER_HIGH_THRESHOLD:
        return "high"
    if horsepower >= HORSEPOWER_MEDIUM_THRESHOLD:
        return "medium"
    return "low"


def _score_power_capability(car_data: Dict[str, Any]) -> Optional[float]:
    performance = car_data.get("performance_driving", {})
    return _average_scores(
        [
            score_ordinal_positive((performance or {}).get("power_feel")),
            score_ordinal_positive(
                _derive_power_to_weight_label(car_data)
                or _derive_horsepower_label(car_data)
            ),
        ]
    )


def _score_subfactor(
    car_data: Dict[str, Any], category_name: str, subfactor_def: Dict[str, Any]
) -> Optional[float]:
    stage_a_key = CATEGORY_SCORE_CONFIG[category_name]["stage_a_key"]
    section = car_data.get(stage_a_key, {})
    if subfactor_def.get("kind") == "positive_label":
        return score_ordinal_positive((section or {}).get(subfactor_def.get("field")))
    if subfactor_def.get("kind") == "negative_label":
        return score_ordinal_negative((section or {}).get(subfactor_def.get("field")))
    if subfactor_def.get("kind") == "power_capability":
        return _score_power_capability(car_data)
    return None


def _has_any_stage_a_evidence(car_data: Dict[str, Any]) -> bool:
    if not isinstance(car_data, dict):
        return False
    for category_name in SINGLE_CAR_CATEGORY_TEMPLATE.keys():
        for value in (car_data.get(category_name) or {}).values():
            if value is not None:
                return True
    if any(value is not None for value in (car_data.get("facts") or {}).values()):
        return True
    if car_data.get("short_notes"):
        return True
    if car_data.get("sources"):
        return True
    if car_data.get("car_profile"):
        return True
    return False


def compute_category_score(
    car_data: Dict, category_name: str
) -> Tuple[Optional[float], Dict[str, Optional[float]]]:
    """Compute deterministic weighted score for a category from simplified Stage A evidence."""
    category_def = CATEGORY_SCORE_CONFIG.get(category_name)
    if not category_def:
        return None, {}

    metric_scores = {}
    total_weighted = 0.0
    total_weights = 0.0
    for metric_name, metric_def in category_def.get("subfactors", {}).items():
        score = _score_subfactor(car_data, category_name, metric_def)
        metric_scores[metric_name] = score
        if score is None:
            continue
        weight = metric_def.get("weight", 0)
        total_weighted += score * weight
        total_weights += weight

    if total_weights == 0:
        return None, metric_scores
    return round(total_weighted / total_weights, 1), metric_scores


def compute_overall_score(
    category_scores: Dict[str, Optional[float]],
) -> Optional[float]:
    """Compute weighted overall score from category scores."""
    total_weighted = 0.0
    total_weights = 0.0
    for cat_name, weight in CATEGORY_WEIGHTS.items():
        score = category_scores.get(cat_name)
        if score is None:
            continue
        total_weighted += score * weight
        total_weights += weight
    if total_weights == 0:
        return None
    return round(total_weighted / total_weights, 1)


def determine_winner(
    scores: Dict[str, Optional[float]], tie_threshold: float = TIE_THRESHOLD
) -> Optional[str]:
    """Determine winner from a dict of car_id -> score. Returns 'tie' if scores are close."""
    valid_scores = {k: v for k, v in scores.items() if v is not None}
    if len(valid_scores) < 2:
        return None
    sorted_scores = sorted(valid_scores.items(), key=lambda x: x[1], reverse=True)
    top_score = sorted_scores[0][1]
    second_score = sorted_scores[1][1]
    if abs(top_score - second_score) < tie_threshold:
        return "tie"
    return sorted_scores[0][0]
