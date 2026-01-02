# -*- coding: utf-8 -*-
"""
Output sanitization module for AI-generated responses.
Implements strict whitelisting, type conversion, HTML escaping, and size limits.
Commercial-grade: treats all AI output as hostile.
"""

import html
from datetime import datetime
from typing import Any, Dict, List, Optional

# Strict allowlist for /analyze response fields
ANALYZE_ALLOWED_FIELDS = {
    "reliability_score",
    "fuel_efficiency_score", 
    "performance_score",
    "comfort_score",
    "safety_score",
    "resale_value_score",
    "overall_score",
    "strengths",
    "weaknesses",
    "common_issues",
    "maintenance_cost_estimate",
    "rating_explanation",
    "source_tag",
    "mileage_note",
    "km_warn"
}

ADVISOR_ALLOWED_FIELDS = {
    "search_performed",
    "search_queries",
    "recommended_cars"
}

CAR_FIELDS = {
    "brand", "model", "year", "fuel", "gear", "turbo", "engine_cc",
    "price_range_nis", "avg_fuel_consumption", "fuel_method",
    "annual_fee", "fee_method", "reliability_score", "reliability_method",
    "maintenance_cost", "maintenance_method", "safety_rating", "safety_method",
    "insurance_cost", "insurance_method", "resale_value", "resale_method",
    "performance_score", "performance_method", "comfort_features", "comfort_method",
    "suitability", "suitability_method", "market_supply", "supply_method",
    "fit_score", "comparison_comment", "not_recommended_reason",
    "annual_energy_cost", "annual_fuel_cost", "total_annual_cost"
}

def escape_html(value: str) -> str:
    """Escape HTML special characters in a string."""
    if not isinstance(value, str):
        return str(value)
    return html.escape(value, quote=True)

def sanitize_string(value: Any, max_length: int = 1000) -> Optional[str]:
    """
    Sanitize a string field.
    - Converts to string
    - Escapes HTML
    - Enforces max length
    """
    if value is None:
        return None
    s = str(value).strip()
    if len(s) > max_length:
        s = s[:max_length]
    return escape_html(s)

def sanitize_number(value: Any, min_val: float = None, max_val: float = None) -> Optional[float]:
    """Sanitize numeric field with range checks."""
    if value is None:
        return None
    try:
        num = float(value)
        if min_val is not None and num < min_val:
            return min_val
        if max_val is not None and num > max_val:
            return max_val
        return num
    except (TypeError, ValueError):
        return None

def sanitize_boolean(value: Any) -> bool:
    """Sanitize boolean field."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "yes", "1", "on")
    return bool(value)

def sanitize_list(value: Any, max_items: int = 100, item_sanitizer=None) -> List:
    """
    Sanitize a list field.
    - Enforces max item count
    - Applies item sanitizer to each element
    """
    if not isinstance(value, list):
        return []
    if len(value) > max_items:
        value = value[:max_items]
    if item_sanitizer:
        return [item_sanitizer(item) for item in value if item is not None]
    return [str(item).strip() for item in value if item]

def sanitize_analyze_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Sanitize /analyze endpoint response.
    Strict allowlist: only known fields are returned.
    """
    if not isinstance(data, dict):
        return {}
    
    sanitized = {}
    
    for field, value in data.items():
        if field not in ANALYZE_ALLOWED_FIELDS:
            continue  # Drop unknown fields
        
        # Type-specific sanitization
        if field in ("reliability_score", "fuel_efficiency_score", "performance_score",
                     "comfort_score", "safety_score", "resale_value_score", "overall_score"):
            sanitized[field] = sanitize_number(value, min_val=0, max_val=10)
        
        elif field == "strengths":
            sanitized[field] = sanitize_list(value, max_items=10, 
                                             item_sanitizer=lambda x: sanitize_string(x, 200))
        
        elif field == "weaknesses":
            sanitized[field] = sanitize_list(value, max_items=10,
                                             item_sanitizer=lambda x: sanitize_string(x, 200))
        
        elif field == "common_issues":
            sanitized[field] = sanitize_list(value, max_items=15,
                                             item_sanitizer=lambda x: sanitize_string(x, 300))
        
        elif field in ("maintenance_cost_estimate", "rating_explanation", "source_tag", "mileage_note"):
            sanitized[field] = sanitize_string(value, max_length=500)
        
        elif field == "km_warn":
            sanitized[field] = sanitize_boolean(value)
    
    return sanitized

def sanitize_advisor_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Sanitize /advisor_api endpoint response.
    Whitelists top-level fields and recursively sanitizes car recommendations.
    """
    if not isinstance(data, dict):
        return {}
    
    sanitized = {}
    
    for field, value in data.items():
        if field not in ADVISOR_ALLOWED_FIELDS:
            continue  # Drop unknown fields
        
        if field == "search_performed":
            sanitized[field] = sanitize_boolean(value)
        
        elif field == "search_queries":
            sanitized[field] = sanitize_list(value, max_items=6,
                                             item_sanitizer=lambda x: sanitize_string(x, 200))
        
        elif field == "recommended_cars":
            sanitized[field] = [sanitize_car_object(car) for car in value if isinstance(car, dict)]
            if len(sanitized[field]) > 10:
                sanitized[field] = sanitized[field][:10]
    
    return sanitized

def sanitize_car_object(car: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitize a single car recommendation object."""
    if not isinstance(car, dict):
        return {}
    
    sanitized = {}
    
    for field, value in car.items():
        if field not in CAR_FIELDS:
            continue  # Drop unknown fields
        
        # Numeric fields (scores, prices, consumption)
        if field in ("year", "engine_cc", "price_range_nis", "avg_fuel_consumption",
                     "annual_fee", "reliability_score", "maintenance_cost", "safety_rating",
                     "insurance_cost", "resale_value", "performance_score", "comfort_features",
                     "suitability", "fit_score", "annual_energy_cost", "annual_fuel_cost",
                     "total_annual_cost"):
            if field == "year":
                # Dynamic max year: current year + 1 (to allow next year models)
                max_year = datetime.now().year + 1
                sanitized[field] = sanitize_number(value, min_val=1990, max_val=max_year)
            elif field == "fit_score":
                sanitized[field] = sanitize_number(value, min_val=0, max_val=100)
            elif "score" in field or "rating" in field:
                sanitized[field] = sanitize_number(value, min_val=1, max_val=10)
            else:
                sanitized[field] = sanitize_number(value, min_val=0)
        
        # String fields (brand, model, fuel, gear, etc.)
        elif field in ("brand", "model", "fuel", "gear", "turbo", "market_supply"):
            sanitized[field] = sanitize_string(value, max_length=100)
        
        # Text explanation fields
        elif field in ("comparison_comment", "not_recommended_reason", "fuel_method",
                       "fee_method", "reliability_method", "maintenance_method",
                       "safety_method", "insurance_method", "resale_method",
                       "performance_method", "comfort_method", "suitability_method",
                       "supply_method"):
            sanitized[field] = sanitize_string(value, max_length=500)
        
        elif field == "turbo":
            # Boolean or string
            if isinstance(value, bool):
                sanitized[field] = value
            else:
                sanitized[field] = sanitize_string(value, max_length=50)
    
    return sanitized
