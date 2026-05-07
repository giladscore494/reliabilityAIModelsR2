"""Prompt builders for the comparison Stage A / Stage B calls.

Re-export façade — see :mod:`app.services.comparison` package docstring.
"""

from app.services.comparison_service import (  # noqa: F401
    build_compare_grounding_prompt,
    build_single_car_prompt,
    build_comparison_prompt,
    build_compare_writer_prompt,
    build_compare_writer_retry_prompt,
)

__all__ = [
    "build_compare_grounding_prompt",
    "build_single_car_prompt",
    "build_comparison_prompt",
    "build_compare_writer_prompt",
    "build_compare_writer_retry_prompt",
]
