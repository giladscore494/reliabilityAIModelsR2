"""Vehicle / source / label normalization helpers.

Re-export façade — see :mod:`app.services.comparison` package docstring.
"""

from app.services.comparison_service import (  # noqa: F401
    build_display_name,
    build_checked_versions,
    map_cars_to_slots,
    normalize_single_car_payload,
    normalize_model_output,
    infer_compare_segment,
)

__all__ = [
    "build_display_name",
    "build_checked_versions",
    "map_cars_to_slots",
    "normalize_single_car_payload",
    "normalize_model_output",
    "infer_compare_segment",
]
