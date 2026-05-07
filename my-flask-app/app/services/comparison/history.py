"""DB persistence / read helpers for comparison history.

Re-export façade — see :mod:`app.services.comparison` package docstring.
"""

from app.services.comparison_service import (  # noqa: F401
    get_comparison_history,
    get_comparison_detail,
    regenerate_comparison_ai,
)

__all__ = [
    "get_comparison_history",
    "get_comparison_detail",
    "regenerate_comparison_ai",
]
