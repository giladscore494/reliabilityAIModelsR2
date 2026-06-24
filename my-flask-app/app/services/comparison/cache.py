# -*- coding: utf-8 -*-
"""Cache-key and safe cached JSON parsing helpers."""

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

COMPARISON_PROMPT_VERSION = "v4"
DECISION_CATEGORY_VERSION = "decision_categories_v2_9"
SCORING_CONTRACT_VERSION = "scoring_compact_v2"


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
    """Compute a cache hash for a comparison request.

    The key binds the request to the locked catalog variant identity, the
    catalog generation hash, the comparison prompt version, and the runtime
    model id. Any of these changing produces a cache miss, so legacy entries
    created before variant_id/catalog-hash existed can never be reused
    (PART 5). Uses 32 hex chars (128 bits) of SHA256.
    """
    # Imported lazily to avoid import cycles at module load.
    from app.services.vehicle_catalog_service import (
        get_catalog_generation_meta,
        resolve_comparison_car,
    )
    from app.services.comparison.model_config import (
        comparison_stage_a_model_id,
        comparison_stage_a_repair_model_id,
        comparison_stage_b_model_id,
        comparison_fallback_model_id,
    )

    catalog_meta = get_catalog_generation_meta()

    car_keys = []
    for c in cars:
        # Consistent year extraction: prefer year, fallback to year_start
        year_val = c.get("year")
        if year_val is None:
            year_val = c.get("year_start")
        year_str = str(year_val) if year_val is not None else ""

        try:
            resolution = resolve_comparison_car(c)
        except Exception:
            resolution = {}

        key_parts = [
            c.get("make", ""),
            c.get("model", ""),
            year_str,
            c.get("engine_type", ""),
            c.get("gearbox", ""),
            str(c.get("variant_id") or resolution.get("variant_id") or ""),
            str(resolution.get("version_or_trim") or ""),
            str(resolution.get("fuel_type") or ""),
            str(resolution.get("engine") or ""),
            str(resolution.get("transmission") or ""),
            str(resolution.get("drivetrain") or ""),
            str(resolution.get("resolution_status") or ""),
        ]
        car_keys.append("|".join(key_parts))

    data = {
        "cars": sorted(car_keys),
        "buyer_profile": buyer_profile,
        "prompt_version": COMPARISON_PROMPT_VERSION,
        "catalog_hash": catalog_meta.get("catalog_hash"),
        "catalog_generated_at": catalog_meta.get("generated_at"),
        "stage_a_model_id": comparison_stage_a_model_id(),
        "stage_a_repair_model_id": comparison_stage_a_repair_model_id(),
        "stage_b_model_id": comparison_stage_b_model_id(),
        "fallback_model_id": comparison_fallback_model_id(),
        "decision_category_version": DECISION_CATEGORY_VERSION,
        "scoring_contract_version": SCORING_CONTRACT_VERSION,
    }
    data_str = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(data_str.encode("utf-8")).hexdigest()[:32]  # 128 bits
