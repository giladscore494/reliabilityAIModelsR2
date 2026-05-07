# -*- coding: utf-8 -*-
"""Comparison request and model-output schema validation helpers."""

from typing import Any, Dict, List, Optional, Tuple

from app.utils.prompt_defense import escape_prompt_input


BUYER_PROFILE_MAIN_USE_ALLOWED = {
    "city",
    "highway",
    "family",
    "commuting",
    "long_trips",
    "work",
    "mixed",
    "unknown",
}


BUYER_PROFILE_SAFETY_ALLOWED = {"yes", "no"}


BUYER_PROFILE_FUELS_ALLOWED = {
    "gasoline",
    "diesel",
    "hybrid",
    "plug_in_hybrid",
    "electric",
    "lpg",
    "בנזין",
    "דיזל",
    "היברידי",
    "חשמלי",
}


BUYER_PROFILE_GEARS_ALLOWED = {
    "automatic",
    "manual",
    "robotic",
    "אוטומטית",
    "ידנית",
    "רובוטית",
}


BUYER_PROFILE_PRIORITY_KEYS = {
    "reliability",
    "fuel",
    "safety",
    "comfort",
    "performance",
    "cost",
}


BUYER_PROFILE_PRIORITY_MIN = 0


BUYER_PROFILE_PRIORITY_MAX = 10


def _sanitize_buyer_number(
    value: Any, minimum: float, maximum: float
) -> Optional[float]:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < minimum or number > maximum:
        return None
    return int(number) if number.is_integer() else number


def _sanitize_buyer_string(value: Any, max_len: int = 80) -> Optional[str]:
    if value in (None, ""):
        return None
    text = escape_prompt_input(str(value).strip(), max_length=max_len)
    return text or None


def _sanitize_buyer_list(value: Any, allowed: set, max_items: int = 6) -> List[str]:
    if not isinstance(value, list):
        return []
    out = []
    for item in value[:max_items]:
        text = _sanitize_buyer_string(item, 40)
        if text and text in allowed and text not in out:
            out.append(text)
    return out


def validate_buyer_profile(
    value: Any,
) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
    """Allowlist and normalize optional compare buyer profile."""
    if value in (None, ""):
        return True, None, None
    if not isinstance(value, dict):
        return False, "פרופיל משתמש לא תקין", None

    normalized: Dict[str, Any] = {}
    number_fields = {
        "budget_min": (0, 2_000_000),
        "budget_max": (0, 2_000_000),
        "annual_km": (0, 100_000),
        "driver_age": (17, 100),
    }
    for field, bounds in number_fields.items():
        num = _sanitize_buyer_number(value.get(field), *bounds)
        if num is not None:
            normalized[field] = num

    main_use = _sanitize_buyer_string(value.get("main_use"), 40)
    if main_use:
        normalized["main_use"] = (
            main_use if main_use in BUYER_PROFILE_MAIN_USE_ALLOWED else "unknown"
        )

    safety_required = _sanitize_buyer_string(value.get("safety_required"), 10)
    if safety_required in BUYER_PROFILE_SAFETY_ALLOWED:
        normalized["safety_required"] = safety_required

    for field in ("family_size", "cargo_need", "body_style", "driving_style"):
        text = _sanitize_buyer_string(value.get(field), 80)
        if text:
            normalized[field] = text

    fuels = _sanitize_buyer_list(
        value.get("preferred_fuels"), BUYER_PROFILE_FUELS_ALLOWED
    )
    gears = _sanitize_buyer_list(
        value.get("preferred_gears"), BUYER_PROFILE_GEARS_ALLOWED
    )
    if fuels:
        normalized["preferred_fuels"] = fuels
    if gears:
        normalized["preferred_gears"] = gears

    priority_weights = (
        value.get("priority_weights")
        if isinstance(value.get("priority_weights"), dict)
        else {}
    )
    cleaned_weights = {}
    for key in BUYER_PROFILE_PRIORITY_KEYS:
        raw = priority_weights.get(key)
        if raw in (None, "") or isinstance(raw, bool):
            continue
        try:
            number = float(raw)
        except (TypeError, ValueError):
            continue
        cleaned_weights[key] = max(
            BUYER_PROFILE_PRIORITY_MIN, min(BUYER_PROFILE_PRIORITY_MAX, number)
        )
    if cleaned_weights:
        normalized["priority_weights"] = cleaned_weights

    return True, None, normalized or None


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
            return False, f"פורמט רכב {i + 1} לא תקין", []

        make = car.get("make", "").strip()
        model = car.get("model", "").strip()

        if not make or not model:
            return False, f"רכב {i + 1}: חובה לציין יצרן ודגם", []

        # Extract year (either single year or use year_start for fallback)
        year = car.get("year")
        if year:
            try:
                year = int(year)
            except (ValueError, TypeError):
                return False, f"רכב {i + 1}: שנתון לא תקין", []
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


def validate_compare_writer_response(payload: Any) -> Optional[Dict[str, Any]]:
    from app.services.comparison.writer import _validate_compare_writer_response

    validated, _reason = _validate_compare_writer_response(payload)
    return validated


def validate_grounding(model_output: Dict) -> Tuple[bool, str]:
    """
    Check if grounding was successful based on model output.
    Returns (is_valid, failure_reason).

    Enforces:
    1. grounding_successful flag must be True
    2. Car data must be present in model output
    Note: Source validation has been removed — estimates without sources are acceptable.
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

    return True, ""
