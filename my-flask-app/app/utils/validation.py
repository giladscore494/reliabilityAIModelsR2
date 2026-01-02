"""Utility functions for validating incoming request payloads.

This module is used by the Flask app to validate form and JSON payloads.

Notes
-----
The repository previously contained helper validation routines (e.g.
``validate_form_data``) that other modules import. When adding new helpers,
we preserve existing functions to avoid breaking callers.
"""

from __future__ import annotations

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


def validate_analyze_request(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate an /analyze request payload and return the validated payload.

    This is a small wrapper around :func:`validate_form_data` that standardizes
    error handling and ensures :class:`ValidationError` is raised with ``field``
    and ``message`` attributes.

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
        # If the app already defines required fields elsewhere, callers can
        # still pass them directly to validate_form_data; here we just validate
        # whatever payload is supplied.
        return validate_form_data(payload)
    except ValidationError:
        # Preserve field/message and re-raise.
        raise
    except Exception as e:  # noqa: BLE001
        # Wrap any unexpected validation exceptions.
        raise ValidationError("payload", str(e)) from e
