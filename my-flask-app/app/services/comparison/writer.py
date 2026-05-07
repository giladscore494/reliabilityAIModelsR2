"""Stage B — narrative generation (writer call).

Re-export façade — see :mod:`app.services.comparison` package docstring.
"""

from app.services.comparison_service import (  # noqa: F401
    call_gemini_compare_writer,
    generate_narrative,
    validate_compare_writer_response,
    sanitize_decision_result,
)

__all__ = [
    "call_gemini_compare_writer",
    "generate_narrative",
    "validate_compare_writer_response",
    "sanitize_decision_result",
]
