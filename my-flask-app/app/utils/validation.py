"""Utility functions for validating incoming request payloads.

This module is used by the Flask app to validate form and JSON payloads.

Notes
-----
The repository previously contained helper validation routines (e.g.
``validate_form_data``) that other modules import. When adding new helpers,
we preserve existing functions to avoid breaking callers.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Any, Dict, Mapping

from app.utils.http_helpers import _utcnow


class ValidationError(Exception):
    """Raised when validation of a request payload fails.

    Parameters
    ----------
    field:
        The name of the field that failed validation.
    message:
        A human readable message describing the validation error.
    """

    def __init__(self, field: str, message: str):
        self.field = field
        self.message = message
        super().__init__(f"{field}: {message}")


def validate_form_data(data: Mapping[str, Any], required_fields: Mapping[str, type] | None = None) -> Dict[str, Any]:
    """Validate form data.

    This function is intentionally kept compatible with existing callers.

    Parameters
    ----------
    data:
        Incoming payload (typically ``request.form`` or parsed JSON).
    required_fields:
        Mapping of field name -> expected type.

    Returns
    -------
    dict
        The validated payload.

    Raises
    ------
    ValidationError
        If a required field is missing or has the wrong type.
    """

    required_fields = required_fields or {}

    validated: Dict[str, Any] = {}
    for field, expected_type in required_fields.items():
        if field not in data or data[field] in (None, ""):
            raise ValidationError(field, "Field is required")
        value = data[field]
        # Type check
        if expected_type is not None and not isinstance(value, expected_type):
            raise ValidationError(field, f"Expected {expected_type.__name__}")
        validated[field] = value

    # include any additional fields as-is
    for k, v in data.items():
        if k not in validated:
            validated[k] = v

    return validated


# Field length limits for DoS prevention (Phase 1D)
_FIELD_MAX_LENGTHS = {
    'make': 80,
    'model': 80,
    'sub_model': 80,
    'mileage_range': 50,
    'fuel_type': 50,
    'transmission': 50,
    'year': 10,
    'year_min': 10,
    'year_max': 10,
    'budget_min': 20,
    'budget_max': 20,
    'annual_km': 12,
    'fuel_price': 20,
    'electricity_price': 20,
    'main_use': 300,
    'body_style': 50,
    'driving_style': 100,
    'family_size': 20,
    'cargo_need': 50,
    'safety_required': 20,
    'trim_level': 50,
    'insurance_history': 300,
    'violations': 100,
    'excluded_colors': 200,
    'driver_gender': 20,
    'seats_choice': 10,
    'research_current_vehicle': 120,
    'research_actual_consumption': 20,
    'research_sale_timeline': 40,
    'research_sale_gap': 40,
    'research_purchase_reference_type': 40,
    'research_purchase_delta_bucket': 40,
    'research_charging_cost': 20,
    'research_charging_location': 20,
}

_CONTROL_CHARS = re.compile(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]')
_ALLOWED_TEXT_PATTERN = re.compile(r"^[A-Za-z0-9א-ת\s\-.,/'\"()&:+־]+$")
_TEXT_FIELDS_TO_NORMALIZE = {
    'make',
    'model',
    'sub_model',
    'mileage_range',
    'fuel_type',
    'transmission',
    'main_use',
    'body_style',
    'driving_style',
    'family_size',
    'cargo_need',
    'safety_required',
    'trim_level',
    'insurance_history',
    'violations',
    'driver_gender',
    'seats_choice',
    'research_current_vehicle',
    'research_sale_timeline',
    'research_sale_gap',
    'research_purchase_reference_type',
    'research_purchase_delta_bucket',
    'research_charging_location',
}


def _check_field_length(field: str, value: Any, max_length: int) -> None:
    """Check if a field exceeds maximum allowed length.
    
    Parameters
    ----------
    field:
        Field name.
    value:
        Field value.
    max_length:
        Maximum allowed length.
    
    Raises
    ------
    ValidationError
        If the field exceeds the maximum length.
    """
    if value is None:
        return
    
    str_value = str(value)
    if len(str_value) > max_length:
        raise ValidationError(
            field,
            f"Field exceeds maximum length of {max_length} characters (got {len(str_value)})"
        )


def _validate_int_range(field: str, value: Any, *, min_val: int, max_val: int) -> int:
    """Validate that a field is an int within the allowed range."""
    try:
        n = int(float(value))
    except Exception:
        raise ValidationError(field, "Field must be a number")
    if n < min_val or n > max_val:
        raise ValidationError(field, f"Value must be between {min_val} and {max_val}")
    return n


_USAGE_DEFAULTS = {
    "annual_km": 15000,
    "city_pct": 50,
    "terrain": "mixed",
    "climate": "center",
    "parking": "outdoor",
    "driver_style": "normal",
    "load": "family",
}

_USAGE_ENUMS = {
    "terrain": {"flat", "mixed", "hilly"},
    "climate": {"coastal", "center", "north", "south_hot"},
    "parking": {"covered", "outdoor"},
    "driver_style": {"calm", "normal", "aggressive"},
    "load": {"light", "family", "heavy"},
}


def _normalize_enum(field: str, value: Any, allowed: set[str], default: str) -> str:
    if isinstance(value, str):
        v = value.strip().lower()
        if v in allowed:
            return v
    return default


def normalize_usage_profile(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Normalize usage profile fields with defaults and strict ranges."""
    annual_km = _validate_int_range(
        "annual_km",
        payload.get("annual_km", _USAGE_DEFAULTS["annual_km"]),
        min_val=0,
        max_val=60000,
    )
    city_pct = _validate_int_range(
        "city_pct",
        payload.get("city_pct", _USAGE_DEFAULTS["city_pct"]),
        min_val=0,
        max_val=100,
    )

    usage = {
        "annual_km": annual_km,
        "city_pct": city_pct,
    }
    for field, allowed in _USAGE_ENUMS.items():
        usage[field] = _normalize_enum(field, payload.get(field), allowed, _USAGE_DEFAULTS[field])
    return usage


def _normalize_and_validate_text(field: str, value: Any, max_length: int) -> str:
    """
    Normalize text fields: strip control chars, collapse whitespace,
    enforce allowlist, and length limit.
    """
    if value is None:
        return ''

    text = str(value)

    # Unicode-aware normalization before validation
    text = unicodedata.normalize("NFKC", text)
    translate_map = {
        ord("\u05f3"): "'",
        ord("\u05f4"): '"',
        ord("\u2013"): "-",
        ord("\u2014"): "-",
        ord("\u2212"): "-",
        ord("\u2018"): "'",
        ord("\u2019"): "'",
        ord("\u201c"): '"',
        ord("\u201d"): '"',
        ord("\u00a0"): " ",
        ord("\u200e"): None,
        ord("\u200f"): None,
        ord("\u202a"): None,
        ord("\u202b"): None,
        ord("\u202c"): None,
        ord("\u202d"): None,
        ord("\u202e"): None,
    }
    text = text.translate(translate_map)
    # Drop any remaining control/format chars
    text = ''.join(ch for ch in text if not unicodedata.category(ch).startswith('C'))
    text = _CONTROL_CHARS.sub('', text)
    text = re.sub(r'\s+', ' ', text).strip()

    _check_field_length(field, text, max_length)

    if text and not _ALLOWED_TEXT_PATTERN.match(text):
        raise ValidationError(field, "Field contains invalid characters. Use letters, numbers, spaces, and basic punctuation only.")

    return text


def _is_non_empty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _validate_string_list(field: str, value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValidationError(field, "Field must be an array")
    normalized: list[str] = []
    for item in value:
        text = _normalize_and_validate_text(field, item, 50)
        if text:
            normalized.append(text)
    return normalized


def _validate_weight_map(weights: Any) -> dict[str, int]:
    required_keys = ("reliability", "resale", "fuel", "performance", "comfort")
    if not isinstance(weights, dict):
        raise ValidationError("weights", "weights must be an object")
    normalized: dict[str, int] = {}
    for key in required_keys:
        if key not in weights:
            raise ValidationError("weights", f"weights.{key} is required")
        normalized[key] = _validate_int_range(f"weights.{key}", weights.get(key), min_val=1, max_val=5)
    return normalized


def _preferred_fuels_need_charging(payload: Mapping[str, Any]) -> bool:
    fuels = payload.get("fuels_he") or []
    if isinstance(fuels, str):
        fuels = [fuels]
    normalized = " ".join(str(item).lower() for item in fuels)
    return any(
        token in normalized
        for token in ("חשמ", "היבריד", "electric", "hybrid", "phev", "ev")
    )


def _preferred_fuels_skip_gear_requirement(payload: Mapping[str, Any]) -> bool:
    fuels = payload.get("fuels_he") or []
    if isinstance(fuels, str):
        fuels = [fuels]
    normalized = " ".join(str(item).lower() for item in fuels)
    return any(token in normalized for token in ("חשמ", "electric", "ev"))


def _require_field(payload: Mapping[str, Any], field: str, message: str) -> None:
    if not _is_non_empty(payload.get(field)):
        raise ValidationError(field, message)


def validate_analyze_request(
    payload: Mapping[str, Any],
    allowed_fields: set[str] | None = None,
    *,
    is_owner: bool = False,
) -> Dict[str, Any]:
    """Validate an /analyze request payload and return the validated payload.

    This is a small wrapper around :func:`validate_form_data` that standardizes
    error handling and ensures :class:`ValidationError` is raised with ``field``
    and ``message`` attributes.
    
    Additionally enforces per-field length limits for DoS prevention.

    Parameters
    ----------
    payload:
        Incoming payload (e.g., ``request.get_json()`` or ``request.form``).

    Returns
    -------
    dict
        The validated payload.

    Raises
    ------
    ValidationError
        If validation fails.
    """

    try:
        # Basic validation
        validated = validate_form_data(payload)

        if allowed_fields is not None:
            unexpected = {k for k in validated.keys() if k not in allowed_fields}
            if unexpected:
                raise ValidationError("payload", f"Unexpected fields: {', '.join(sorted(unexpected))}")
        
        # Enforce field length limits (Phase 1D: DoS prevention) and normalize text
        for field, max_length in _FIELD_MAX_LENGTHS.items():
            if field not in validated:
                continue

            if field in _TEXT_FIELDS_TO_NORMALIZE:
                validated[field] = _normalize_and_validate_text(field, validated[field], max_length)
            else:
                _check_field_length(field, validated[field], max_length)

        # Numeric range enforcement
        # Allow slight future buffer for upcoming model years that may appear in listings
        current_year = _utcnow().year + 2
        if "year" in validated:
            validated["year"] = _validate_int_range("year", validated["year"], min_val=1950, max_val=current_year)
        if "year_min" in validated:
            validated["year_min"] = _validate_int_range("year_min", validated["year_min"], min_val=1950, max_val=current_year)
        if "year_max" in validated:
            validated["year_max"] = _validate_int_range("year_max", validated["year_max"], min_val=1950, max_val=current_year)
        if "year_min" in validated and "year_max" in validated and validated["year_min"] > validated["year_max"]:
            raise ValidationError("year_range", "year_min cannot exceed year_max")
        if "annual_km" in validated:
            validated["annual_km"] = _validate_int_range("annual_km", validated["annual_km"], min_val=0, max_val=60000)
        if "research_actual_consumption" in validated and _is_non_empty(validated["research_actual_consumption"]):
            # Research form placeholder asks for km/L or EV-equivalent consumption, so 0-50 covers
            # realistic user-entered ranges while rejecting obviously bad values.
            validated["research_actual_consumption"] = _validate_int_range(
                "research_actual_consumption",
                validated["research_actual_consumption"],
                min_val=0,
                max_val=50,
            )
        if "research_charging_cost" in validated and _is_non_empty(validated["research_charging_cost"]):
            value = float(validated["research_charging_cost"])
            if value < 0 or value > 10:
                raise ValidationError("research_charging_cost", "Value must be between 0 and 10")
            validated["research_charging_cost"] = value

        if allowed_fields is None:
            validated["fuels_he"] = _validate_string_list("fuels_he", validated.get("fuels_he"))
            validated["gears_he"] = _validate_string_list("gears_he", validated.get("gears_he"))
            validated["weights"] = _validate_weight_map(validated.get("weights"))

            _require_field(validated, "budget_min", "budget_min is required")
            _require_field(validated, "budget_max", "budget_max is required")
            _require_field(validated, "year_min", "year_min is required")
            _require_field(validated, "year_max", "year_max is required")
            if not validated["fuels_he"]:
                raise ValidationError("fuels_he", "Select at least one preferred fuel type")
            if not validated["gears_he"] and not _preferred_fuels_skip_gear_requirement(validated):
                raise ValidationError("gears_he", "Select at least one preferred gearbox")

            if float(validated["budget_max"]) <= 0:
                raise ValidationError("budget_max", "budget_max must be greater than 0")
            if float(validated["budget_min"]) < 0:
                raise ValidationError("budget_min", "budget_min must be 0 or greater")
            if float(validated["budget_min"]) > float(validated["budget_max"]):
                raise ValidationError("budget_range", "budget_min cannot exceed budget_max")

            if not is_owner:
                _require_field(
                    validated,
                    "research_current_vehicle",
                    "research_current_vehicle is required",
                )
                _require_field(
                    validated,
                    "research_actual_consumption",
                    "research_actual_consumption is required",
                )
                if _preferred_fuels_need_charging(validated):
                    _require_field(
                        validated,
                        "research_charging_cost",
                        "research_charging_cost is required for hybrid/electric preferences",
                    )
                    _require_field(
                        validated,
                        "research_charging_location",
                        "research_charging_location is required for hybrid/electric preferences",
                    )

        # Usage profile normalization (fills defaults when missing)
        usage_profile = normalize_usage_profile(validated)
        validated.update(usage_profile)
        validated["usage_profile"] = usage_profile

        return validated
    except ValidationError:
        # Preserve field/message and re-raise.
        raise
    except Exception as e:  # noqa: BLE001
        # Wrap any unexpected validation exceptions.
        raise ValidationError("payload", str(e)) from e
