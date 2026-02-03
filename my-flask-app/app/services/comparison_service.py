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
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from flask import current_app

from app.extensions import db
from app.models import ComparisonHistory
from app.utils.http_helpers import api_ok, api_error, get_request_id
from app.utils.prompt_defense import (
    escape_prompt_input,
    wrap_user_input_in_boundary,
    create_data_only_instruction,
)
import app.extensions as extensions
from google.genai import types as genai_types


# ============================================================
# CONFIGURATION
# ============================================================

COMPARISON_PROMPT_VERSION = "v1"
COMPARISON_MODEL_ID = os.environ.get("GEMINI_COMPARISON_MODEL_ID", "gemini-3-flash-preview")
AI_CALL_TIMEOUT_SEC = int(os.environ.get("AI_CALL_TIMEOUT_SEC", "170"))

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
    "<car_identifier_1>": {{
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
    // Repeat for each car...
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


def determine_winner(scores: Dict[str, Optional[float]]) -> Optional[str]:
    """Determine winner from a dict of car_id -> score."""
    valid_scores = {k: v for k, v in scores.items() if v is not None}
    if not valid_scores:
        return None
    return max(valid_scores, key=lambda x: valid_scores[x])


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
            try:
                return api_ok({
                    "cached": True,
                    "comparison_id": cached.id,
                    "cars_selected": json.loads(cached.cars_selected),
                    "model_output": json.loads(cached.model_json_raw) if cached.model_json_raw else None,
                    "computed_result": json.loads(cached.computed_result),
                    "sources_index": json.loads(cached.sources_index) if cached.sources_index else {},
                    "assumptions": json.loads(cached.model_json_raw).get("assumptions", {}) if cached.model_json_raw else {},
                })
            except json.JSONDecodeError:
                pass  # Cache corrupted, proceed with fresh call
    
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
        else:
            return api_error("ai_call_failed", "שגיאה בתקשורת עם מנוע ה-AI. נסה שוב מאוחר יותר.", status=500)
    
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
    
    # Generate car identifiers
    car_ids = []
    for car in validated_cars:
        car_id = f"{car['make']} {car['model']}"
        car_ids.append(car_id)
    
    # Save to database
    try:
        comparison_record = ComparisonHistory(
            created_at=datetime.utcnow(),
            user_id=user_id,
            session_id=session_id,
            cars_selected=json.dumps(validated_cars, ensure_ascii=False),
            model_json_raw=json.dumps(model_output, ensure_ascii=False),
            computed_result=json.dumps(computed_result, ensure_ascii=False),
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
        "cars_selected": validated_cars,
        "model_output": model_output,
        "computed_result": computed_result,
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
            cars = json.loads(record.cars_selected)
            computed = json.loads(record.computed_result) if record.computed_result else {}
            result.append({
                "id": record.id,
                "created_at": record.created_at.isoformat(),
                "cars": cars,
                "overall_winner": computed.get("overall_winner"),
            })
        except json.JSONDecodeError:
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
        return {
            "id": record.id,
            "created_at": record.created_at.isoformat(),
            "cars_selected": json.loads(record.cars_selected),
            "model_output": json.loads(record.model_json_raw) if record.model_json_raw else None,
            "computed_result": json.loads(record.computed_result) if record.computed_result else None,
            "sources_index": json.loads(record.sources_index) if record.sources_index else {},
            "assumptions": json.loads(record.model_json_raw).get("assumptions", {}) if record.model_json_raw else {},
            "model_name": record.model_name,
            "prompt_version": record.prompt_version,
        }
    except json.JSONDecodeError:
        return None
