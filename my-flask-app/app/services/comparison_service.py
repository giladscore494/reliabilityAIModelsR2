# -*- coding: utf-8 -*-
"""
Comparison service logic for Car Comparison feature.
Uses Gemini 3 Flash with web grounding to retrieve car metrics.
All scoring is computed deterministically in code only.
"""

import os
import json
import hashlib
import time as pytime
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from flask import current_app

from app.extensions import db
from app.models import ComparisonHistory
from app.utils.http_helpers import api_ok, api_error, get_request_id
from app.quota import log_access_decision
from app.utils.prompt_defense import (
    escape_prompt_input,
    wrap_user_input_in_boundary,
    create_data_only_instruction,
)
import app.extensions as extensions
from google.genai import types as genai_types
from app.utils.sanitization import sanitize_comparison_narrative


# ============================================================
# JSON PARSING HELPERS
# ============================================================


def _safe_json_obj(value, default):
    """
    Safely decode a JSON value that may be None, already decoded, or double-encoded.
    
    Args:
        value: The value to decode (may be None, str, dict, or list)
        default: The default value to return on any error
        
    Returns:
        The decoded value as dict/list, or default on any failure.
        This function NEVER raises an exception.
    """
    try:
        if value is None:
            return default
        
        # Already decoded dict or list
        if isinstance(value, (dict, list)):
            return value
        
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return default
            
            # First decode attempt
            result = json.loads(stripped)
            
            # Check if result is still a string (double-encoded)
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except (json.JSONDecodeError, TypeError, ValueError):
                    # Second decode failed, return default
                    return default
            
            # Verify final result is dict or list
            if isinstance(result, (dict, list)):
                return result
            return default
        
        # Unexpected type
        return default
    except (json.JSONDecodeError, TypeError, ValueError):
        return default


def build_display_name(car: Dict[str, Any]) -> str:
    """Build a human-readable display name for a car.
    Format: "{make} {model} {year}" or "{make} {model}" if no year.
    """
    parts = [car.get("make", ""), car.get("model", "")]
    year = car.get("year")
    if year:
        parts.append(str(year))
    elif car.get("year_start") and car.get("year_end"):
        parts.append(f"{car['year_start']}-{car['year_end']}")
    return " ".join(p for p in parts if p).strip()


def map_cars_to_slots(validated_cars: List[Dict]) -> Dict[str, Dict]:
    """Map validated cars to stable slot keys: car_1, car_2, car_3.
    Each slot includes the original selection fields plus display_name.
    """
    slots = {}
    for i, car in enumerate(validated_cars):
        slot_key = f"car_{i + 1}"
        slot_data = dict(car)  # copy
        slot_data["display_name"] = build_display_name(car)
        slots[slot_key] = slot_data
    return slots


# ============================================================
# CONFIGURATION
# ============================================================

COMPARISON_PROMPT_VERSION = "v1"
COMPARISON_MODEL_ID = os.environ.get("GEMINI_COMPARISON_MODEL_ID", "gemini-3-flash-preview")
AI_CALL_TIMEOUT_SEC = int(os.environ.get("AI_CALL_TIMEOUT_SEC", "170"))
TIE_THRESHOLD = 3  # Score delta below this = "tie" (צמוד)

# Category weights for overall score calculation
CATEGORY_WEIGHTS = {
    "reliability_risk": 0.40,
    "ownership_cost": 0.25,
    "practicality_comfort": 0.20,
    "driving_performance": 0.15,
}

# Enum mappings for ordinal values (low = good score, high = bad score for risk metrics)
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

SIZE_SCORES = {
    "small": 30,
    "medium": 60,
    "large": 100,
}


# ============================================================
# METRIC DEFINITIONS
# ============================================================

METRICS_DEFINITION = {
    "reliability_risk": {
        "weight": 0.40,
        "metrics": {
            "reliability_rating": {"type": "numeric", "min": 0, "max": 100, "weight": 0.25},
            "major_failure_risk": {"type": "ordinal_negative", "weight": 0.20},
            "common_failure_patterns": {"type": "list", "weight": 0.10},  # Not scored directly
            "mileage_sensitivity": {"type": "ordinal_negative", "weight": 0.15},
            "maintenance_complexity": {"type": "ordinal_negative", "weight": 0.15},
            "expected_maintenance_cost_level": {"type": "ordinal_negative", "weight": 0.15},
        }
    },
    "ownership_cost": {
        "weight": 0.25,
        "metrics": {
            "fuel_economy_real_world": {"type": "numeric_lower_better", "min": 5, "max": 25, "weight": 0.25},
            "insurance_cost_level": {"type": "ordinal_negative", "weight": 0.20},
            "depreciation_value_retention": {"type": "ordinal_positive", "weight": 0.20},
            "parts_availability": {"type": "ordinal_positive", "weight": 0.15},
            "service_network_ease": {"type": "ordinal_positive", "weight": 0.20},
        }
    },
    "practicality_comfort": {
        "weight": 0.20,
        "metrics": {
            "cabin_space": {"type": "size", "weight": 0.15},
            "trunk_space_liters": {"type": "numeric", "min": 200, "max": 700, "weight": 0.15},
            "ride_comfort": {"type": "ordinal_positive", "weight": 0.20},
            "noise_insulation": {"type": "ordinal_positive", "weight": 0.15},
            "city_driveability": {"type": "ordinal_positive", "weight": 0.15},
            "features_value": {"type": "ordinal_positive", "weight": 0.20},
        }
    },
    "driving_performance": {
        "weight": 0.15,
        "metrics": {
            "acceleration_0_100": {"type": "numeric_lower_better", "min": 5, "max": 15, "weight": 0.20},
            "engine_power_hp": {"type": "numeric", "min": 80, "max": 300, "weight": 0.15},
            "handling_stability": {"type": "ordinal_positive", "weight": 0.25},
            "braking_performance": {"type": "ordinal_positive", "weight": 0.20},
            "highway_stability": {"type": "ordinal_positive", "weight": 0.20},
        }
    },
}


# ============================================================
# SAFE JSON CACHE PARSING
# ============================================================

def _safe_parse_json_cached(raw_value: Any, field_name: str = "unknown") -> Tuple[Any, bool]:
    """
    Safely parse possibly double-encoded JSON from cached database rows.
    
    Handles the case where old cached rows stored double-encoded JSON strings,
    e.g., '"{\\\"a\\\": 1}"' which when parsed once returns a string '{"a": 1}'
    that itself needs another json.loads() call.
    
    Args:
        raw_value: The raw value from the database (string, dict, list, or None).
                   Can be a JSON string, already-parsed dict/list (from JSONB), or None.
        field_name: Name of the field (for logging)
    
    Returns:
        Tuple of (parsed_value, was_double_encoded)
        - parsed_value: The parsed dict/list, or the original value if not parseable
        - was_double_encoded: True if double-encoding was detected and unwrapped
    
    Never throws; returns (None, False) for truly invalid data.
    """
    if raw_value is None:
        return None, False
    
    if not isinstance(raw_value, str):
        # Already parsed (e.g., JSONB column returned dict/list directly)
        return raw_value, False
    
    try:
        # First parse attempt
        parsed = json.loads(raw_value)
        
        # Check if result is still a string that looks like JSON
        if isinstance(parsed, str):
            stripped = parsed.strip()
            if stripped.startswith('{') or stripped.startswith('['):
                # Attempt second parse (unwrap double-encoding)
                try:
                    parsed_inner = json.loads(parsed)
                    return parsed_inner, True  # was double-encoded
                except (json.JSONDecodeError, TypeError):
                    # Inner string wasn't valid JSON, return outer parse
                    return parsed, False
        
        return parsed, False
    except (json.JSONDecodeError, TypeError):
        # Could not parse at all
        return None, False


# ============================================================
# PROMPT BUILDING
# ============================================================

def build_comparison_prompt(cars: List[Dict[str, str]]) -> str:
    """Build the comparison prompt for Gemini with strict JSON output."""
    
    # Sanitize car inputs including year, engine_type, and gearbox
    sanitized_cars = []
    for car in cars:
        sanitized_car = {
            "make": escape_prompt_input(car.get("make", ""), max_length=50),
            "model": escape_prompt_input(car.get("model", ""), max_length=100),
        }
        # Include explicit year if provided (single year, not a range)
        if car.get("year"):
            sanitized_car["year"] = int(car.get("year"))
        elif car.get("year_start"):
            sanitized_car["year_start"] = car.get("year_start")
            sanitized_car["year_end"] = car.get("year_end")
        # Include engine type and gearbox as explicit assumptions
        if car.get("engine_type"):
            sanitized_car["engine_type"] = escape_prompt_input(car.get("engine_type", ""), max_length=50)
        if car.get("gearbox"):
            sanitized_car["gearbox"] = escape_prompt_input(car.get("gearbox", ""), max_length=50)
        sanitized_cars.append(sanitized_car)
    
    cars_json = json.dumps(sanitized_cars, ensure_ascii=False, indent=2)
    bounded_cars = wrap_user_input_in_boundary(cars_json, boundary_tag="cars_input")
    data_instruction = create_data_only_instruction()
    
    # Build slot mapping for stable keys
    slot_mapping = {}
    for i, car in enumerate(sanitized_cars):
        slot_key = f"car_{i + 1}"
        slot_mapping[slot_key] = build_display_name(car)
    
    slot_mapping_text = "\n".join(f"  {k}: {v}" for k, v in slot_mapping.items())
    
    return f"""
{data_instruction}

You are a car comparison data analyst with access to Google Search for real-time web data.
You MUST use the Google Search tool to find factual data about each car and MUST cite sources.

🔴 CRITICAL: You are a data retrieval agent ONLY. You MUST NOT:
- Decide winners or compare scores between cars
- Compute any scores or rankings
- Make recommendations or judgments
- State which car is "better" in any way

Your ONLY job is to retrieve factual data for each metric for each car, with citations.

{bounded_cars}

IMPORTANT: Use these EXACT keys in the "cars" object:
{slot_mapping_text}

Return data for each car using the slot key (car_1, car_2, etc.) NOT the car name.

Return a SINGLE JSON object with this EXACT structure:

{{
  "grounding_successful": true,
  "search_queries_used": ["list of actual search queries you ran"],
  "assumptions": {{
    "year_assumption": "If year range wasn't clear, state what years you assumed",
    "engine_assumption": "If specific engine wasn't given, state what you assumed",
    "trim_assumption": "If specific trim wasn't given, state what you assumed"
  }},
  "cars": {{
    "car_1": {{
      "reliability_risk": {{
        "reliability_rating": {{
          "value": 0-100 or null,
          "confidence": 0.0-1.0,
          "missing_reason": "reason if null",
          "sources": [
            {{"url": "https://...", "title": "Source title", "snippet": "Brief quote (max 25 words)"}}
          ]
        }},
        "major_failure_risk": {{
          "value": "low" | "medium" | "high" | null,
          "confidence": 0.0-1.0,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "common_failure_patterns": {{
          "value": [
            {{"issue": "Issue name", "frequency": "common/rare/occasional"}}
          ] or null,
          "confidence": 0.0-1.0,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "mileage_sensitivity": {{
          "value": "low" | "medium" | "high" | null,
          "confidence": 0.0-1.0,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "maintenance_complexity": {{
          "value": "low" | "medium" | "high" | null,
          "confidence": 0.0-1.0,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "expected_maintenance_cost_level": {{
          "value": "low" | "medium" | "high" | null,
          "confidence": 0.0-1.0,
          "missing_reason": "reason if null",
          "sources": [...]
        }}
      }},
      "ownership_cost": {{
        "fuel_economy_real_world": {{
          "value": <number in L/100km> or null,
          "confidence": 0.0-1.0,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "insurance_cost_level": {{
          "value": "low" | "medium" | "high" | null,
          "confidence": 0.0-1.0,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "depreciation_value_retention": {{
          "value": "low" | "medium" | "high" | null,
          "confidence": 0.0-1.0,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "parts_availability": {{
          "value": "low" | "medium" | "high" | null,
          "confidence": 0.0-1.0,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "service_network_ease": {{
          "value": "low" | "medium" | "high" | null,
          "confidence": 0.0-1.0,
          "missing_reason": "reason if null",
          "sources": [...]
        }}
      }},
      "practicality_comfort": {{
        "cabin_space": {{
          "value": "small" | "medium" | "large" | null,
          "confidence": 0.0-1.0,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "trunk_space_liters": {{
          "value": <number in liters> or null,
          "confidence": 0.0-1.0,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "ride_comfort": {{
          "value": "low" | "medium" | "high" | null,
          "confidence": 0.0-1.0,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "noise_insulation": {{
          "value": "low" | "medium" | "high" | null,
          "confidence": 0.0-1.0,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "city_driveability": {{
          "value": "low" | "medium" | "high" | null,
          "confidence": 0.0-1.0,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "features_value": {{
          "value": "low" | "medium" | "high" | null,
          "confidence": 0.0-1.0,
          "missing_reason": "reason if null",
          "sources": [...]
        }}
      }},
      "driving_performance": {{
        "acceleration_0_100": {{
          "value": <number in seconds> or null,
          "confidence": 0.0-1.0,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "engine_power_hp": {{
          "value": <number in hp> or null,
          "confidence": 0.0-1.0,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "handling_stability": {{
          "value": "low" | "medium" | "high" | null,
          "confidence": 0.0-1.0,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "braking_performance": {{
          "value": "low" | "medium" | "high" | null,
          "confidence": 0.0-1.0,
          "missing_reason": "reason if null",
          "sources": [...]
        }},
        "highway_stability": {{
          "value": "low" | "medium" | "high" | null,
          "confidence": 0.0-1.0,
          "missing_reason": "reason if null",
          "sources": [...]
        }}
      }}
    }}
    "car_2": {{ ... }}
  }}
}}

RULES:
1. Every metric MUST have at least one source with URL, title, and snippet.
2. If data is not found, set value=null and provide a missing_reason.
3. Confidence must reflect how reliable the source data is (0.0-1.0).
4. Do NOT compare cars or state winners - only provide raw data.
5. Return ONLY valid JSON. No markdown, no explanations.
""".strip()


# ============================================================
# SCORING FUNCTIONS (DETERMINISTIC - CODE ONLY)
# ============================================================

def score_numeric(value: Optional[float], min_val: float, max_val: float, confidence: float = 1.0) -> Optional[float]:
    """Score a numeric value normalized between 0-100."""
    if value is None:
        return None
    # Clamp value to bounds
    clamped = max(min_val, min(max_val, value))
    # Normalize to 0-100
    score = ((clamped - min_val) / (max_val - min_val)) * 100
    return round(score * confidence, 1)


def score_numeric_lower_better(value: Optional[float], min_val: float, max_val: float, confidence: float = 1.0) -> Optional[float]:
    """Score a numeric value where lower is better (inverted)."""
    if value is None:
        return None
    # Clamp value to bounds
    clamped = max(min_val, min(max_val, value))
    # Invert: lower value = higher score
    score = ((max_val - clamped) / (max_val - min_val)) * 100
    return round(score * confidence, 1)


def score_ordinal_negative(value: Optional[str], confidence: float = 1.0) -> Optional[float]:
    """Score ordinal value where low is good (e.g., risk: low=good)."""
    if value is None:
        return None
    val_lower = str(value).lower().strip()
    score = ORDINAL_SCORES_NEGATIVE.get(val_lower)
    if score is None:
        return None
    return round(score * confidence, 1)


def score_ordinal_positive(value: Optional[str], confidence: float = 1.0) -> Optional[float]:
    """Score ordinal value where high is good (e.g., comfort: high=good)."""
    if value is None:
        return None
    val_lower = str(value).lower().strip()
    score = ORDINAL_SCORES_POSITIVE.get(val_lower)
    if score is None:
        return None
    return round(score * confidence, 1)


def score_size(value: Optional[str], confidence: float = 1.0) -> Optional[float]:
    """Score size value."""
    if value is None:
        return None
    val_lower = str(value).lower().strip()
    score = SIZE_SCORES.get(val_lower)
    if score is None:
        return None
    return round(score * confidence, 1)


def score_metric(metric_data: Dict, metric_def: Dict) -> Optional[float]:
    """
    Score a single metric based on its definition.
    IMPORTANT: Only scores metrics that have sources (grounding enforcement).
    """
    value = metric_data.get("value")
    confidence = float(metric_data.get("confidence", 1.0))
    metric_type = metric_def.get("type")
    
    if value is None:
        return None
    
    # Enforce source requirement: non-null values without sources are not scored
    sources = metric_data.get("sources", [])
    if not sources or len(sources) == 0:
        # Value exists but no sources - cannot score without grounding
        return None
    
    if metric_type == "numeric":
        return score_numeric(value, metric_def.get("min", 0), metric_def.get("max", 100), confidence)
    elif metric_type == "numeric_lower_better":
        return score_numeric_lower_better(value, metric_def.get("min", 0), metric_def.get("max", 100), confidence)
    elif metric_type == "ordinal_negative":
        return score_ordinal_negative(value, confidence)
    elif metric_type == "ordinal_positive":
        return score_ordinal_positive(value, confidence)
    elif metric_type == "size":
        return score_size(value, confidence)
    elif metric_type == "list":
        # Lists are not directly scored
        return None
    
    return None


def validate_metric_sources(metric_data: Dict) -> bool:
    """Check if a metric has valid sources."""
    if metric_data.get("value") is None:
        return True  # Null values don't need sources
    sources = metric_data.get("sources", [])
    return sources and len(sources) > 0


def compute_category_score(car_data: Dict, category_name: str) -> Tuple[Optional[float], Dict[str, Optional[float]]]:
    """
    Compute weighted score for a category.
    Returns (category_score, metric_scores_dict).
    """
    if category_name not in METRICS_DEFINITION:
        return None, {}
    
    cat_def = METRICS_DEFINITION[category_name]
    metrics_defs = cat_def.get("metrics", {})
    
    category_data = car_data.get(category_name, {})
    
    metric_scores = {}
    total_weighted = 0.0
    total_weights = 0.0
    
    for metric_name, metric_def in metrics_defs.items():
        metric_data = category_data.get(metric_name, {})
        score = score_metric(metric_data, metric_def)
        metric_scores[metric_name] = score
        
        if score is not None:
            weight = metric_def.get("weight", 0)
            total_weighted += score * weight
            total_weights += weight
    
    if total_weights > 0:
        category_score = round(total_weighted / total_weights, 1)
    else:
        category_score = None
    
    return category_score, metric_scores


def compute_overall_score(category_scores: Dict[str, Optional[float]]) -> Optional[float]:
    """Compute weighted overall score from category scores."""
    total_weighted = 0.0
    total_weights = 0.0
    
    for cat_name, weight in CATEGORY_WEIGHTS.items():
        score = category_scores.get(cat_name)
        if score is not None:
            total_weighted += score * weight
            total_weights += weight
    
    if total_weights > 0:
        return round(total_weighted / total_weights, 1)
    return None


def determine_winner(scores: Dict[str, Optional[float]], tie_threshold: float = TIE_THRESHOLD) -> Optional[str]:
    """Determine winner from a dict of car_id -> score. Returns 'tie' if scores are close."""
    valid_scores = {k: v for k, v in scores.items() if v is not None}
    if not valid_scores:
        return None
    if len(valid_scores) < 2:
        return next(iter(valid_scores))
    sorted_scores = sorted(valid_scores.items(), key=lambda x: x[1], reverse=True)
    top_score = sorted_scores[0][1]
    second_score = sorted_scores[1][1]
    if abs(top_score - second_score) < tie_threshold:
        return "tie"
    return sorted_scores[0][0]


def compute_comparison_results(model_output: Dict) -> Dict:
    """
    Compute all scores and determine winners based on model output.
    All scoring is done deterministically in code.
    """
    cars_data = model_output.get("cars", {})
    
    results = {
        "cars": {},
        "category_winners": {},
        "metric_winners": {},
        "overall_winner": None,
        "top_reasons": [],
    }
    
    overall_scores = {}
    
    for car_id, car_data in cars_data.items():
        car_result = {
            "categories": {},
            "overall_score": None,
        }
        
        category_scores = {}
        
        for cat_name in METRICS_DEFINITION.keys():
            cat_score, metric_scores = compute_category_score(car_data, cat_name)
            car_result["categories"][cat_name] = {
                "score": cat_score,
                "metrics": metric_scores,
            }
            category_scores[cat_name] = cat_score
        
        # Compute overall score
        car_result["overall_score"] = compute_overall_score(category_scores)
        overall_scores[car_id] = car_result["overall_score"]
        
        results["cars"][car_id] = car_result
    
    # Determine category winners
    for cat_name in METRICS_DEFINITION.keys():
        cat_scores = {car_id: results["cars"][car_id]["categories"][cat_name]["score"] 
                      for car_id in cars_data.keys()}
        results["category_winners"][cat_name] = determine_winner(cat_scores)
    
    # Determine metric winners
    for cat_name, cat_def in METRICS_DEFINITION.items():
        results["metric_winners"][cat_name] = {}
        for metric_name in cat_def.get("metrics", {}).keys():
            metric_scores = {}
            for car_id in cars_data.keys():
                car_metrics = results["cars"][car_id]["categories"].get(cat_name, {}).get("metrics", {})
                metric_scores[car_id] = car_metrics.get(metric_name)
            results["metric_winners"][cat_name][metric_name] = determine_winner(metric_scores)
    
    # Determine overall winner
    results["overall_winner"] = determine_winner(overall_scores)
    
    # Generate top 3 reasons (based on category scores)
    if results["overall_winner"]:
        winner_id = results["overall_winner"]
        winner_cats = results["cars"][winner_id]["categories"]
        
        # Sort categories by score (descending)
        sorted_cats = sorted(
            [(cat_name, data["score"]) for cat_name, data in winner_cats.items() if data["score"] is not None],
            key=lambda x: x[1],
            reverse=True
        )
        
        # Generate reasons
        cat_names_he = {
            "reliability_risk": "אמינות וסיכונים",
            "ownership_cost": "עלות אחזקה",
            "practicality_comfort": "נוחות ופרקטיות",
            "driving_performance": "ביצועים ונהיגה",
        }
        
        for cat_name, score in sorted_cats[:3]:
            reason = f"ניקוד גבוה ב{cat_names_he.get(cat_name, cat_name)}: {score:.1f}/100"
            results["top_reasons"].append(reason)
    
    return results


# ============================================================
# AI CALL FUNCTION
# ============================================================

def call_gemini_comparison(prompt: str, timeout_sec: int = AI_CALL_TIMEOUT_SEC) -> Tuple[Optional[Dict], Optional[str]]:
    """
    Call Gemini 3 Flash with web grounding for comparison data.
    Returns (parsed_output, error_string).
    """
    import concurrent.futures
    from app.factory import AI_EXECUTOR, AI_EXECUTOR_WORKERS
    
    start_time = pytime.perf_counter()
    try:
        if extensions.ai_client is None:
            return None, "CLIENT_NOT_INITIALIZED"
        
        search_tool = genai_types.Tool(google_search=genai_types.GoogleSearch())
        config = genai_types.GenerateContentConfig(
            temperature=0.3,
            top_p=0.9,
            top_k=40,
            tools=[search_tool],
            response_mime_type="application/json",
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
                return None, "SERVER_BUSY"
        
        try:
            future = AI_EXECUTOR.submit(_invoke)
        except Exception:
            return None, "EXECUTOR_SATURATED"
        
        try:
            resp = future.result(timeout=timeout_sec)
        except concurrent.futures.TimeoutError:
            future.cancel()
            return None, "CALL_TIMEOUT"
        except Exception as e:
            return None, f"CALL_FAILED:{type(e).__name__}"
        
        if resp is None:
            return None, "CALL_FAILED:EMPTY"
        
        text = (getattr(resp, "text", "") or "").strip()
        if not text:
            return None, "EMPTY_RESPONSE"
        
        try:
            parsed = json.loads(text)
            return parsed, None
        except json.JSONDecodeError:
            # Try json_repair
            try:
                from json_repair import repair_json
                repaired = repair_json(text)
                return json.loads(repaired), None
            except Exception:
                return None, "MODEL_JSON_INVALID"
    
    finally:
        duration_ms = (pytime.perf_counter() - start_time) * 1000
        current_app.logger.info(
            "[AI] feature=comparison model=%s duration_ms=%.2f",
            COMPARISON_MODEL_ID,
            duration_ms,
        )


def generate_narrative(cars_selected_slots: Dict, computed_result: Dict, timeout_sec: int = 60) -> Optional[Dict]:
    """
    Generate short human-friendly explanations using Gemini Flash WITHOUT grounding.
    Input: only computed scores and display names (no new data retrieval).
    Returns strict JSON narrative or None on failure.
    """
    import concurrent.futures
    from app.factory import AI_EXECUTOR, AI_EXECUTOR_WORKERS

    try:
        if extensions.ai_client is None:
            current_app.logger.warning("[NARRATIVE] AI client not initialized")
            return None

        # Build input context from computed results only
        car_summaries = {}
        for slot_key, slot_data in cars_selected_slots.items():
            car_computed = computed_result.get("cars", {}).get(slot_key, {})
            car_summaries[slot_key] = {
                "display_name": slot_data.get("display_name", slot_key),
                "overall_score": car_computed.get("overall_score"),
                "categories": {}
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
      "title_he": "{cat_names_he.get('reliability_risk', '')}",
      "winner": "car_1|car_2|car_3|tie",
      "explanations": {{{{ {car_explanations_template} }}}},
      "why_it_scored_that_way": ["string", "string"]
    }}}},
    {{{{
      "category_key": "ownership_cost",
      "title_he": "{cat_names_he.get('ownership_cost', '')}",
      "winner": "car_1|car_2|car_3|tie",
      "explanations": {{{{ {car_explanations_template} }}}},
      "why_it_scored_that_way": ["string", "string"]
    }}}},
    {{{{
      "category_key": "practicality_comfort",
      "title_he": "{cat_names_he.get('practicality_comfort', '')}",
      "winner": "car_1|car_2|car_3|tie",
      "explanations": {{{{ {car_explanations_template} }}}},
      "why_it_scored_that_way": ["string", "string"]
    }}}},
    {{{{
      "category_key": "driving_performance",
      "title_he": "{cat_names_he.get('driving_performance', '')}",
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
            current_app.logger.warning("[NARRATIVE] Executor saturated, skipping narrative")
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


def normalize_model_output(parsed: Any, request_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Normalize parsed JSON into a dict.
    Handles the case where Gemini returns a JSON array (list) instead of a dict.
    
    Args:
        parsed: The parsed JSON output from the model (can be dict, list, or other)
        request_id: Request ID for logging purposes
    
    Returns:
        Tuple of (normalized_dict, error_code) - error_code is None if successful
    """
    if parsed is None:
        return None, "MODEL_SHAPE_INVALID"
    
    # If already a dict, return as-is
    if isinstance(parsed, dict):
        return parsed, None
    
    # If it's a list, try to extract the dict
    if isinstance(parsed, list):
        if len(parsed) == 1 and isinstance(parsed[0], dict):
            # Single-element list containing a dict - use it with a warning
            current_app.logger.warning(
                "[COMPARISON] Model returned list with single dict, normalizing. request_id=%s",
                request_id
            )
            return parsed[0], None
        else:
            # List with multiple elements or non-dict elements
            current_app.logger.error(
                "[COMPARISON] Model returned invalid list shape (len=%d). request_id=%s",
                len(parsed),
                request_id
            )
            return None, "MODEL_SHAPE_INVALID"
    
    # Any other type is invalid
    current_app.logger.error(
        "[COMPARISON] Model returned unexpected type: %s. request_id=%s",
        type(parsed).__name__,
        request_id
    )
    return None, "MODEL_SHAPE_INVALID"


# ============================================================
# REQUEST HASH FOR CACHING
# ============================================================

def compute_request_hash(cars: List[Dict]) -> str:
    """
    Compute a hash for caching based on selected cars and prompt version.
    Uses 32 characters (128 bits) of SHA256 for adequate collision resistance.
    Includes year, engine_type, and gearbox in hash calculation.
    """
    car_keys = []
    for c in cars:
        # Consistent year extraction: prefer year, fallback to year_start
        year_val = c.get('year')
        if year_val is None:
            year_val = c.get('year_start')
        year_str = str(year_val) if year_val is not None else ''
        
        key_parts = [
            c.get('make', ''),
            c.get('model', ''),
            year_str,
            c.get('engine_type', ''),
            c.get('gearbox', ''),
        ]
        car_keys.append('|'.join(key_parts))
    
    data = {
        "cars": sorted(car_keys),
        "prompt_version": COMPARISON_PROMPT_VERSION,
    }
    data_str = json.dumps(data, sort_keys=True)
    return hashlib.sha256(data_str.encode()).hexdigest()[:32]  # 128 bits


# ============================================================
# VALIDATION
# ============================================================

def validate_comparison_request(data: Dict) -> Tuple[bool, Optional[str], List[Dict]]:
    """
    Validate comparison request data.
    Returns (is_valid, error_message, validated_cars).
    Accepts year, engine_type, and gearbox as explicit assumptions.
    """
    cars = data.get("cars")
    
    if not cars:
        return False, "לא נבחרו רכבים להשוואה", []
    
    if not isinstance(cars, list):
        return False, "פורמט רכבים לא תקין", []
    
    if len(cars) < 2:
        return False, "יש לבחור לפחות 2 רכבים להשוואה", []
    
    if len(cars) > 3:
        return False, "ניתן להשוות עד 3 רכבים בלבד", []
    
    validated_cars = []
    seen_keys = set()
    for i, car in enumerate(cars):
        if not isinstance(car, dict):
            return False, f"פורמט רכב {i+1} לא תקין", []
        
        make = car.get("make", "").strip()
        model = car.get("model", "").strip()
        
        if not make or not model:
            return False, f"רכב {i+1}: חובה לציין יצרן ודגם", []
        
        # Extract year (either single year or use year_start for fallback)
        year = car.get("year")
        if year:
            try:
                year = int(year)
            except (ValueError, TypeError):
                return False, f"רכב {i+1}: שנתון לא תקין", []
        else:
            # Fallback to year_start for consistent hashing
            year_start = car.get("year_start")
            if year_start:
                try:
                    year = int(year_start)
                except (ValueError, TypeError):
                    year = None
        
        engine_type = car.get("engine_type", "").strip()
        gearbox = car.get("gearbox", "").strip()
        
        # Check for duplicates (same make, model, year, engine, gearbox)
        # Use empty string for None year to ensure consistent comparison
        year_key = str(year) if year is not None else ""
        car_key = f"{make}|{model}|{year_key}|{engine_type}|{gearbox}"
        if car_key in seen_keys:
            return False, "לא ניתן להשוות רכבים זהים. אנא בחר רכבים שונים.", []
        seen_keys.add(car_key)
        
        validated_car = {
            "make": make,
            "model": model,
        }
        if year:
            validated_car["year"] = year
        if engine_type:
            validated_car["engine_type"] = engine_type
        if gearbox:
            validated_car["gearbox"] = gearbox
        # Keep year_start/year_end for backward compatibility
        if car.get("year_start"):
            validated_car["year_start"] = car.get("year_start")
        if car.get("year_end"):
            validated_car["year_end"] = car.get("year_end")
            
        validated_cars.append(validated_car)
    
    return True, None, validated_cars


def validate_grounding(model_output: Dict) -> Tuple[bool, str]:
    """
    Check if grounding was successful based on model output.
    Returns (is_valid, failure_reason).
    
    Enforces:
    1. grounding_successful flag must be True
    2. Each car must have at least some sourced metrics
    3. Non-null values without sources cause validation to fail
    """
    if not model_output:
        return False, "Empty model output"
    
    # Check grounding flag
    grounding = model_output.get("grounding_successful", False)
    if not grounding:
        return False, "Model reported grounding_successful=false"
    
    # Check that we have car data
    cars = model_output.get("cars", {})
    if not cars:
        return False, "No car data in model output"
    
    # Check each car has sourced data and validate source requirements
    cars_with_sources = 0
    unsourced_values = []
    
    for car_id, car_data in cars.items():
        car_has_sources = False
        for cat_name, cat_data in car_data.items():
            if not isinstance(cat_data, dict):
                continue
            for metric_name, metric_data in cat_data.items():
                if not isinstance(metric_data, dict):
                    continue
                    
                value = metric_data.get("value")
                sources = metric_data.get("sources", [])
                
                # Check if non-null value has sources
                if value is not None and (not sources or len(sources) == 0):
                    unsourced_values.append(f"{car_id}.{cat_name}.{metric_name}")
                
                if sources and len(sources) > 0:
                    car_has_sources = True
        
        if car_has_sources:
            cars_with_sources += 1
    
    # Warn about unsourced values but don't fail completely
    # (we'll skip scoring those metrics)
    if unsourced_values:
        current_app.logger.warning(
            f"[GROUNDING] Values without sources ({len(unsourced_values)} total): {', '.join(unsourced_values[:5])}..."
        )
    
    # At least half the cars should have sourced data
    min_cars_with_sources = max(1, len(cars) // 2)
    if cars_with_sources < min_cars_with_sources:
        return False, f"Only {cars_with_sources}/{len(cars)} cars have sourced data"
    
    return True, ""


# ============================================================
# SOURCES INDEX BUILDER
# ============================================================

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


# ============================================================
# MAIN HANDLER
# ============================================================

def handle_comparison_request(data: Dict, user_id: Optional[int], session_id: Optional[str]) -> Any:
    """
    Handle a car comparison request.
    Returns Flask response.
    """
    logger = current_app.logger
    request_id = get_request_id()
    
    # Validate request
    is_valid, error_msg, validated_cars = validate_comparison_request(data)
    if not is_valid:
        return api_error("validation_error", error_msg, status=400)
    
    # Map cars to stable slots with display_name
    cars_selected_slots = map_cars_to_slots(validated_cars)
    
    # Enforce per-user daily limit before any AI calls
    if user_id:
        now = datetime.utcnow()
        day_start = datetime(now.year, now.month, now.day)
        day_end = day_start + timedelta(days=1)
        today_count = (
            ComparisonHistory.query.filter(
                ComparisonHistory.user_id == user_id,
                ComparisonHistory.created_at >= day_start,
                ComparisonHistory.created_at < day_end,
            ).count()
        )
        if today_count >= 3:
            log_access_decision('/api/compare', user_id, 'rejected', 'daily limit reached')
            return api_error(
                "limit_reached",
                "הגעת למקסימום 3 השוואות היום. נסה מחר.",
                status=429,
            )
    
    # Compute request hash for caching
    request_hash = compute_request_hash(validated_cars)
    
    # Check cache (only for logged-in users)
    if user_id:
        cached = ComparisonHistory.query.filter_by(
            user_id=user_id,
            request_hash=request_hash,
        ).order_by(ComparisonHistory.created_at.desc()).first()
        
        if cached and cached.computed_result:
            logger.info(f"[COMPARISON] cache hit request_id={request_id} hash={request_hash}")
            
            # Safely parse all cached JSON fields, handling double-encoded data
            cars_selected, cars_was_double = _safe_parse_json_cached(cached.cars_selected, "cars_selected")
            computed_result, computed_was_double = _safe_parse_json_cached(cached.computed_result, "computed_result")
            sources_index, sources_was_double = _safe_parse_json_cached(cached.sources_index, "sources_index")
            model_output, model_was_double = _safe_parse_json_cached(cached.model_json_raw, "model_json_raw")
            
            # Validate that required fields parsed to expected types
            cache_valid = (
                isinstance(cars_selected, list) and
                isinstance(computed_result, dict)
            )
            
            if cache_valid:
                # Extract assumptions safely (only if model_output is a dict)
                assumptions = {}
                if isinstance(model_output, dict):
                    assumptions = model_output.get("assumptions", {})
                
                # Self-heal: if any field was double-encoded, update the DB to store normalized JSON
                # Note: cars_selected and computed_result are required (validated above),
                # while sources_index and model_json_raw are nullable - hence the extra null checks
                needs_heal = cars_was_double or computed_was_double or sources_was_double or model_was_double
                if needs_heal:
                    try:
                        if cars_was_double:
                            cached.cars_selected = json.dumps(cars_selected, ensure_ascii=False)
                        if computed_was_double:
                            cached.computed_result = json.dumps(computed_result, ensure_ascii=False)
                        if sources_was_double and sources_index is not None:
                            cached.sources_index = json.dumps(sources_index, ensure_ascii=False)
                        if model_was_double and model_output is not None:
                            cached.model_json_raw = json.dumps(model_output, ensure_ascii=False)
                        db.session.commit()
                        logger.info(f"[COMPARISON] self-healed double-encoded cache row id={cached.id}")
                    except Exception as heal_err:
                        logger.warning(f"[COMPARISON] self-heal commit failed: {heal_err}")
                        db.session.rollback()
                
                # Reconstruct slots from cached list; guard against corrupted non-list data
                if isinstance(cars_selected, list):
                    cached_slots = map_cars_to_slots(cars_selected)
                else:
                    logger.warning(f"[COMPARISON] cars_selected not a list in cache row {cached.id}, using as-is")
                    cached_slots = cars_selected if isinstance(cars_selected, dict) else {}

                return api_ok({
                    "cached": True,
                    "comparison_id": cached.id,
                    "cars_selected": cached_slots,
                    "cars_selected_list": cars_selected if isinstance(cars_selected, list) else [],
                    "model_output": model_output,
                    "computed_result": computed_result,
                    "narrative": computed_result.get("narrative") if isinstance(computed_result, dict) else None,
                    "sources_index": sources_index if sources_index else {},
                    "assumptions": assumptions,
                })
            else:
                # Cache row is corrupted (cannot parse to expected types)
                # Delete the bad row so future requests don't hit it, then proceed with fresh call
                logger.warning(f"[COMPARISON] cache row {cached.id} corrupted, deleting and recomputing")
                try:
                    db.session.delete(cached)
                    db.session.commit()
                except Exception as del_err:
                    logger.warning(f"[COMPARISON] failed to delete corrupted cache row: {del_err}")
                    db.session.rollback()
    
    # Build prompt
    prompt = build_comparison_prompt(validated_cars)
    
    # Call Gemini
    start_time = pytime.perf_counter()
    model_output, error = call_gemini_comparison(prompt)
    duration_ms = int((pytime.perf_counter() - start_time) * 1000)
    
    if error:
        logger.error(f"[COMPARISON] AI call failed request_id={request_id} error={error}")
        if error == "CALL_TIMEOUT":
            return api_error("ai_timeout", "זמן העיבוד חרג מהמותר. נסה שוב מאוחר יותר.", status=504)
        elif error == "SERVER_BUSY":
            return api_error("server_busy", "השרת עמוס כרגע. נסה שוב בעוד רגע.", status=503)
        elif error == "MODEL_JSON_INVALID":
            return api_error("model_json_invalid", "המודל החזיר פורמט לא תקין. נסה שוב.", status=502, details={"request_id": request_id})
        else:
            return api_error("ai_call_failed", "שגיאה בתקשורת עם מנוע ה-AI. נסה שוב מאוחר יותר.", status=500)
    
    # Normalize model output shape (handles list vs dict mismatch)
    model_output, shape_error = normalize_model_output(model_output, request_id)
    if shape_error:
        logger.error(f"[COMPARISON] Model output shape invalid request_id={request_id} error={shape_error}")
        return api_error(
            "model_output_invalid",
            "המודל החזיר פורמט נתונים לא צפוי. נסה שוב מאוחר יותר.",
            status=502,
            details={"request_id": request_id, "error_code": shape_error}
        )
    
    # Validate grounding
    grounding_valid, grounding_reason = validate_grounding(model_output)
    if not grounding_valid:
        logger.warning(f"[COMPARISON] grounding failed request_id={request_id} reason={grounding_reason}")
        return api_error(
            "grounding_failed",
            "לא הצלחנו לאמת את המידע ממקורות באינטרנט. נסה שוב מאוחר יותר.",
            status=502
        )
    
    # Compute scores deterministically
    computed_result = compute_comparison_results(model_output)
    
    # Build sources index
    sources_index = build_sources_index(model_output)
    
    # Generate narrative explanations (2nd LLM call, no grounding)
    narrative = None
    try:
        raw_narrative = generate_narrative(cars_selected_slots, computed_result)
        if raw_narrative:
            narrative = sanitize_comparison_narrative(raw_narrative)
            logger.info(f"[COMPARISON] narrative generated request_id={request_id}")
    except Exception as e:
        logger.warning(f"[COMPARISON] narrative generation failed: {e}")
    
    # Include narrative in computed_result for storage
    stored_computed = dict(computed_result)
    if narrative:
        stored_computed["narrative"] = narrative
    
    # Save to database
    try:
        comparison_record = ComparisonHistory(
            created_at=datetime.utcnow(),
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
        logger.info(f"[COMPARISON] saved request_id={request_id} comparison_id={comparison_id}")
    except Exception as e:
        logger.error(f"[COMPARISON] save failed request_id={request_id} error={e}")
        db.session.rollback()
        comparison_id = None
    
    return api_ok({
        "cached": False,
        "comparison_id": comparison_id,
        "cars_selected": cars_selected_slots,
        "cars_selected_list": validated_cars,
        "model_output": model_output,
        "computed_result": computed_result,
        "narrative": narrative,
        "sources_index": sources_index,
        "assumptions": model_output.get("assumptions", {}),
    })


def get_comparison_history(user_id: int, limit: int = 10) -> List[Dict]:
    """Get comparison history for a user."""
    records = (
        ComparisonHistory.query
        .filter_by(user_id=user_id)
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
            
            result.append({
                "id": record.id,
                "created_at": record.created_at.isoformat(),
                "cars": cars,
                "overall_winner": computed.get("overall_winner"),
            })
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
        
        # Extract narrative from computed_result if stored there
        narrative = computed_result.get("narrative") if isinstance(computed_result, dict) else None
        
        # Reconstruct stable car slots
        cars_selected_slots = map_cars_to_slots(cars_selected) if isinstance(cars_selected, list) else cars_selected
        
        return {
            "id": record.id,
            "created_at": record.created_at.isoformat(),
            "cars_selected": cars_selected_slots,
            "cars_selected_list": cars_selected if isinstance(cars_selected, list) else [],
            "model_output": model_output,
            "computed_result": computed_result,
            "narrative": narrative,
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
