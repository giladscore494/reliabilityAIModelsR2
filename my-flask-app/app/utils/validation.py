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
from typing import Any, Dict, Mapping


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


def _normalize_and_validate_text(field: str, value: Any, max_length: int) -> str:
    """
    Normalize text fields: strip control chars, collapse whitespace,
    enforce allowlist, and length limit.
    """
    if value is None:
        return ''

    text = str(value)
    text = _CONTROL_CHARS.sub('', text)
    text = re.sub(r'\s+', ' ', text).strip()

    _check_field_length(field, text, max_length)

    if text and not _ALLOWED_TEXT_PATTERN.match(text):
        raise ValidationError(field, "Field contains unsupported characters")

    return text


def validate_analyze_request(payload: Mapping[str, Any]) -> Dict[str, Any]:
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
        
        # Enforce field length limits (Phase 1D: DoS prevention) and normalize text
        for field, max_length in _FIELD_MAX_LENGTHS.items():
            if field not in validated:
                continue

            if field in _TEXT_FIELDS_TO_NORMALIZE:
                validated[field] = _normalize_and_validate_text(field, validated[field], max_length)
            else:
                _check_field_length(field, validated[field], max_length)

        return validated
    except ValidationError:
        # Preserve field/message and re-raise.
        raise
    except Exception as e:  # noqa: BLE001
        # Wrap any unexpected validation exceptions.
        raise ValidationError("payload", str(e)) from e
