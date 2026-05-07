"""Fallback narratives + empty payload helpers.

Re-export façade — see :mod:`app.services.comparison` package docstring.

Includes ``build_deterministic_decision_result`` because it produces a fully
deterministic decision payload used as a fallback when AI output is missing
or fails validation.
"""

from app.services.comparison_service import (  # noqa: F401
    build_deterministic_fallback_narrative,
    build_deterministic_decision_result,
    mark_partial_comparison_narrative,
    _empty_single_car_payload,
    _empty_stage_a_output,
)

__all__ = [
    "build_deterministic_fallback_narrative",
    "build_deterministic_decision_result",
    "mark_partial_comparison_narrative",
    "_empty_single_car_payload",
    "_empty_stage_a_output",
]
