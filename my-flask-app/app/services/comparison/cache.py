"""Cache-key + safe-parse-cache helpers.

Re-export façade — see :mod:`app.services.comparison` package docstring.
"""

from app.services.comparison_service import (  # noqa: F401
    compute_request_hash,
    _safe_parse_json_cached,
    _safe_json_obj,
)

__all__ = [
    "compute_request_hash",
    "_safe_parse_json_cached",
    "_safe_json_obj",
]
