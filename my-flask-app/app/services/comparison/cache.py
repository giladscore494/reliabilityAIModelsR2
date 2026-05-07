# -*- coding: utf-8 -*-
"""Cache-key and safe cached JSON parsing helpers."""

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

COMPARISON_PROMPT_VERSION = "v4"


def _safe_json_obj(value, default):
    """
    Safely decode a JSON value that may be None, already decoded, or double-encoded.

    Args:
        value: The value to decode (may be None, str, dict, or list)
        default: The default value to return on any error

    Returns:
        The decoded value as dict/list, or default on any failure.
        This function NEVER raises an exception.
    """
    try:
        if value is None:
            return default

        # Already decoded dict or list
        if isinstance(value, (dict, list)):
            return value

        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return default

            # First decode attempt
            result = json.loads(stripped)

            # Check if result is still a string (double-encoded)
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except (json.JSONDecodeError, TypeError, ValueError):
                    # Second decode failed, return default
                    return default

            # Verify final result is dict or list
            if isinstance(result, (dict, list)):
                return result
            return default

        # Unexpected type
        return default
    except (json.JSONDecodeError, TypeError, ValueError):
        return default


def _safe_parse_json_cached(
    raw_value: Any, field_name: str = "unknown"
) -> Tuple[Any, bool]:
    """
    Safely parse possibly double-encoded JSON from cached database rows.

    Handles the case where old cached rows stored double-encoded JSON strings,
    e.g., '"{\\\"a\\\": 1}"' which when parsed once returns a string '{"a": 1}'
    that itself needs another json.loads() call.

    Args:
        raw_value: The raw value from the database (string, dict, list, or None).
                   Can be a JSON string, already-parsed dict/list (from JSONB), or None.
        field_name: Name of the field (for logging)

    Returns:
        Tuple of (parsed_value, was_double_encoded)
        - parsed_value: The parsed dict/list, or the original value if not parseable
        - was_double_encoded: True if double-encoding was detected and unwrapped

    Never throws; returns (None, False) for truly invalid data.
    """
    if raw_value is None:
        return None, False

    if not isinstance(raw_value, str):
        # Already parsed (e.g., JSONB column returned dict/list directly)
        return raw_value, False

    try:
        # First parse attempt
        parsed = json.loads(raw_value)

        # Check if result is still a string that looks like JSON
        if isinstance(parsed, str):
            stripped = parsed.strip()
            if stripped.startswith("{") or stripped.startswith("["):
                # Attempt second parse (unwrap double-encoding)
                try:
                    parsed_inner = json.loads(parsed)
                    return parsed_inner, True  # was double-encoded
                except (json.JSONDecodeError, TypeError):
                    # Inner string wasn't valid JSON, return outer parse
                    return parsed, False

        return parsed, False
    except (json.JSONDecodeError, TypeError):
        # Could not parse at all
        return None, False


def compute_request_hash(
    cars: List[Dict], buyer_profile: Optional[Dict[str, Any]] = None
) -> str:
    """
    Compute a hash for caching based on selected cars and prompt version.
    Uses 32 characters (128 bits) of SHA256 for adequate collision resistance.
    Includes year, engine_type, and gearbox in hash calculation.
    """
    car_keys = []
    for c in cars:
        # Consistent year extraction: prefer year, fallback to year_start
        year_val = c.get("year")
        if year_val is None:
            year_val = c.get("year_start")
        year_str = str(year_val) if year_val is not None else ""

        key_parts = [
            c.get("make", ""),
            c.get("model", ""),
            year_str,
            c.get("engine_type", ""),
            c.get("gearbox", ""),
        ]
        car_keys.append("|".join(key_parts))

    data = {
        "cars": sorted(car_keys),
        "buyer_profile": buyer_profile,
        "prompt_version": COMPARISON_PROMPT_VERSION,
    }
    data_str = json.dumps(data, sort_keys=True)
    return hashlib.sha256(data_str.encode()).hexdigest()[:32]  # 128 bits
