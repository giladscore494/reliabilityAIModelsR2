"""Internal validation / typed-shape helpers (request + response).

Re-export façade — see :mod:`app.services.comparison` package docstring.
"""

from app.services.comparison_service import (  # noqa: F401
    validate_buyer_profile,
    validate_comparison_request,
    validate_grounding,
    validate_compare_writer_response,
)

__all__ = [
    "validate_buyer_profile",
    "validate_comparison_request",
    "validate_grounding",
    "validate_compare_writer_response",
]
