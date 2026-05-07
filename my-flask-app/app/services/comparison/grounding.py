"""Stage A — grounded model calls and parsing.

Re-export façade — see :mod:`app.services.comparison` package docstring.
"""

from app.services.comparison_service import (  # noqa: F401
    call_gemini_comparison,
    call_gemini_single_car,
    call_stage_a_parallel,
    parse_single_car_json,
    parse_stage_a_json,
    validate_grounding,
    build_sources_index,
    build_sources_index_from_flat,
)

__all__ = [
    "call_gemini_comparison",
    "call_gemini_single_car",
    "call_stage_a_parallel",
    "parse_single_car_json",
    "parse_stage_a_json",
    "validate_grounding",
    "build_sources_index",
    "build_sources_index_from_flat",
]
