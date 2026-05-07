"""Pipeline orchestration for the comparison feature.

Re-export façade. Behavior lives (for now) in
:mod:`app.services.comparison_service`. A future refactor will physically
move the orchestration logic here without changing the public API.
"""

from app.services.comparison_service import (  # noqa: F401
    handle_comparison_request,
    build_ai_payload,
    build_stored_comparison_ai_payload,
    convert_writer_response_to_narrative,
    convert_decision_result_to_narrative,
    resolve_comparison_narrative,
    enforce_authoritative_numbers,
)

__all__ = [
    "handle_comparison_request",
    "build_ai_payload",
    "build_stored_comparison_ai_payload",
    "convert_writer_response_to_narrative",
    "convert_decision_result_to_narrative",
    "resolve_comparison_narrative",
    "enforce_authoritative_numbers",
]
