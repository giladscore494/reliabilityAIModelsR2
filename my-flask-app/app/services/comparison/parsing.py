# -*- coding: utf-8 -*-
"""Shared parsing and sanitization helpers for comparison."""

import json
import re
from typing import Any, Dict, List, Optional

from app.services.comparison.constants import (
    CAR_PROFILE_MAX_NESTING_DEPTH,
    ELLIPSIS_LEN,
    SCHEMA_ECHO_PATTERNS,
    _LABEL_VALUES,
    _MAX_STAGE_A_NOTES,
    _MAX_STAGE_A_SOURCES,
    _SINGLE_CAR_REQUIRED_CATEGORIES,
    _STAGE_A_REQUIRED_KEYS,
)

# Rich Stage A keys that can appear at top-level or inside car_profile
RICH_STAGE_A_KEYS = (
    "catalog_identity",
    "pricing",
    "trim_equipment_summary",
    "license_running_cost",
    "fuel_energy",
    "official_safety",
    "powertrain_performance",
    "reliability_risks",
    "practicality",
    "resale_market",
    "research_status",
    "uncertainties_conflicts",
)
from app.services.comparison.fallbacks import _empty_single_car_payload
from app.services.comparison.normalization import (
    _normalize_checked_version_text,
    _normalize_compare_writer_winner,
    _normalize_general_transmission_label,
    _normalize_grounded_cars_format,
    _ordered_compare_slot_keys,
    _sanitize_checked_versions,
    _segment_text_tokens,
    _infer_compare_segment_details,
)
from app.utils.sanitization import _sanitize_url


def _extract_decision_slot_keys(decision_result: Any) -> List[str]:
    from app.services.comparison.constants import _COMPARE_SLOT_RE, _DECISION_SLOT_FIELD_RE

    if not isinstance(decision_result, dict):
        return []
    extracted: List[str] = []
    for key in decision_result.keys():
        match = _DECISION_SLOT_FIELD_RE.match(str(key))
        if match:
            extracted.append(match.group(1))
    key_differences = decision_result.get("key_differences")
    for item in key_differences if isinstance(key_differences, list) else []:
        if not isinstance(item, dict):
            continue
        for key in item.keys():
            if isinstance(key, str) and _COMPARE_SLOT_RE.match(key):
                extracted.append(key)
    return _ordered_compare_slot_keys(extracted)


def _normalize_label(value: Any) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return normalized if normalized in _LABEL_VALUES else None


def _normalize_number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_short_text(value: Any, max_len: int = 120) -> Optional[str]:
    if value is None:
        return None
    text = " ".join(str(value).split()).strip()
    if not text:
        return None
    return text[:max_len]


def _normalize_sources(value: Any) -> List[str]:
    out: List[str] = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict):
            raw_url = item.get("url")
        else:
            raw_url = item
        clean_url = _sanitize_url(raw_url)
        if clean_url and clean_url not in out:
            out.append(clean_url)
        if len(out) >= _MAX_STAGE_A_SOURCES:
            break
    return out


def _normalize_car_profile(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    def _clean(obj: Any, depth: int = 0) -> Any:
        if depth > CAR_PROFILE_MAX_NESTING_DEPTH:
            return None
        if isinstance(obj, dict):
            cleaned = {}
            for key, item in obj.items():
                if isinstance(key, str) and len(key) <= 80:
                    cleaned[key] = _clean(item, depth + 1)
            return cleaned
        if isinstance(obj, list):
            return [_clean(item, depth + 1) for item in obj[:12]]
        if isinstance(obj, str):
            return " ".join(obj.split())[:500]
        if isinstance(obj, (int, float)) and not isinstance(obj, bool):
            return obj
        if obj is None or isinstance(obj, bool):
            return obj
        return None

    cleaned = _clean(value)
    return cleaned if isinstance(cleaned, dict) else {}


def normalize_single_car_payload(
    payload: Dict[str, Any], fallback_name: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return None

    normalized = _empty_single_car_payload()
    normalized["car_name"] = _normalize_short_text(
        payload.get("car_name") or fallback_name, 140
    )

    category_seen = False
    for category_name in (
        "reliability",
        "ownership_cost",
        "comfort_practicality",
        "performance_driving",
    ):
        source_category = payload.get(category_name)
        if not isinstance(source_category, dict):
            continue
        for field_name in normalized[category_name].keys():
            label = _normalize_label(source_category.get(field_name))
            normalized[category_name][field_name] = label
            category_seen = category_seen or (label is not None)

    facts = payload.get("facts")
    if isinstance(facts, dict):
        normalized["facts"]["horsepower"] = _normalize_number(facts.get("horsepower"))
        normalized["facts"]["weight_kg"] = _normalize_number(facts.get("weight_kg"))
        normalized["facts"]["body_type"] = _normalize_short_text(
            facts.get("body_type"), 60
        )
        normalized["facts"]["fuel_type"] = _normalize_short_text(
            facts.get("fuel_type"), 60
        )

    raw_notes = (
        payload.get("short_notes")
        if isinstance(payload.get("short_notes"), list)
        else []
    )
    normalized_notes: List[str] = []
    for item in raw_notes:
        note = _normalize_short_text(item, 120)
        if note:
            normalized_notes.append(note)
        if len(normalized_notes) >= _MAX_STAGE_A_NOTES:
            break
    normalized["short_notes"] = normalized_notes
    normalized["sources"] = _normalize_sources(payload.get("sources"))

    # Collect rich Stage A keys from top-level into car_profile
    rich_profile = {
        key: payload.get(key)
        for key in RICH_STAGE_A_KEYS
        if key in payload
    }

    existing_car_profile = _normalize_car_profile(payload.get("car_profile"))
    if rich_profile:
        normalized["car_profile"] = {
            **existing_car_profile,
            **_normalize_car_profile(rich_profile),
        }
    else:
        normalized["car_profile"] = existing_car_profile

    has_facts = any(value is not None for value in normalized["facts"].values())
    has_notes = bool(normalized["short_notes"])
    has_sources = bool(normalized["sources"])
    has_car_profile = bool(normalized.get("car_profile"))
    if not (category_seen or has_facts or has_notes or has_sources or has_car_profile):
        return None
    return normalized


def _strip_json_code_fences(text: str) -> str:
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", stripped, count=1)
        stripped = re.sub(r"\s*```$", "", stripped, count=1)
    return stripped.strip()


def _extract_first_json_object(text: str) -> Optional[str]:
    raw = text or ""
    start = raw.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    escaped = False
    for idx in range(start, len(raw)):
        ch = raw[idx]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return raw[start : idx + 1]
    return None


def _repair_json_once(text: str) -> str:
    repaired = (text or "").lstrip("\ufeff")
    smart_quotes = {
        "\u201c": '"',
        "\u201d": '"',
        "\u2018": "'",
        "\u2019": "'",
    }
    for src, dst in smart_quotes.items():
        repaired = repaired.replace(src, dst)
    repaired = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", repaired)
    repaired = re.sub(r",(\s*[}\]])", r"\1", repaired)
    return repaired.strip()


def _truncate_to_word_limit(value: Any, limit: int) -> Optional[str]:
    if not isinstance(value, str):
        return None
    compact = " ".join(value.split())
    if not compact:
        return None
    words = compact.split()
    if len(words) <= limit:
        return compact
    return " ".join(words[:limit])


def _truncate_error_message(message: Any, max_len: int = 180) -> str:
    text = " ".join(str(message or "").split())
    if len(text) <= max_len:
        return text
    return f"{text[: max_len - ELLIPSIS_LEN]}..."


def _sanitize_stage_a_errors(errors: List[str], max_items: int = 5) -> List[str]:
    sanitized: List[str] = []
    for err in (errors or [])[:max_items]:
        slot_key, sep, code_or_message = str(err).partition(": ")
        if sep:
            sanitized.append(
                f"{slot_key}: {_truncate_error_message(code_or_message, 96)}"
            )
        else:
            sanitized.append(_truncate_error_message(err, 120))
    return sanitized


def _extract_stage_a_error_code(errors: List[str]) -> str:
    if not errors:
        return "UNKNOWN"
    first = str(errors[0])
    _, sep, code_or_message = first.partition(": ")
    if not sep:
        return _truncate_error_message(first, 96) or "UNKNOWN"
    return _truncate_error_message(code_or_message, 96) or "UNKNOWN"


def _truncate_log_payload(value: Any, limit: int = 300) -> str:
    try:
        raw = json.dumps(value, ensure_ascii=False)
    except Exception:
        raw = str(value)
    raw = " ".join(raw.split())
    return raw[:limit]


def _safe_ai_response_snippet(exc: Exception, max_len: int = 280) -> str:
    from app.services.comparison.model_calls import _safe_ai_response_snippet as _impl

    return _impl(exc, max_len)


def _is_valid_single_car_payload(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    # Reject schema echoes, research-status-only objects, and placeholder repairs
    # before accepting the payload.
    if _is_schema_echo(payload):
        return False
    top_keys = set(payload.keys())
    if top_keys <= {"status", "checked_areas", "open_fields", "sources_found", "research_status"}:
        return False
    # Accept legacy categories
    if _SINGLE_CAR_REQUIRED_CATEGORIES.intersection(set(payload.keys())):
        return True
    # Accept non-empty car_profile dict
    car_profile = payload.get("car_profile")
    if isinstance(car_profile, dict) and car_profile:
        return True
    # Accept any rich Stage A key at top-level
    rich_keys_set = set(RICH_STAGE_A_KEYS)
    if rich_keys_set.intersection(set(payload.keys())):
        return True
    return False


def _is_schema_echo(payload: Any) -> bool:
    """Detect model outputs that are just the prompt schema or placeholders.

    Returns True if the payload looks like the model echoed the schema
    instead of providing real evidence data.
    """
    if not isinstance(payload, dict):
        return False

    raw = json.dumps(payload, ensure_ascii=False)

    # Check for known schema placeholder patterns
    import re as _re
    placeholder_hits = 0
    for pattern in SCHEMA_ECHO_PATTERNS:
        if _re.search(pattern, raw):
            placeholder_hits += 1
    if placeholder_hits >= 1:
        return True

    placeholder_values = {"string", "number", "array", "object", "high|medium|low", "unknown|low|medium|high"}
    def _walk_has_placeholder(obj: Any) -> bool:
        if isinstance(obj, dict):
            return any(_walk_has_placeholder(v) for v in obj.values())
        if isinstance(obj, list):
            return any(_walk_has_placeholder(v) for v in obj)
        return isinstance(obj, str) and obj.strip().lower() in placeholder_values
    if _walk_has_placeholder(payload):
        return True

    # Check for "string" as a literal value in important fields
    string_value_count = 0
    for key in ("car_name", "body_type", "fuel_type"):
        val = payload.get(key)
        if val == "string":
            string_value_count += 1
    car_profile = payload.get("car_profile") or {}
    if isinstance(car_profile, dict):
        cat_id = car_profile.get("catalog_identity") or {}
        if isinstance(cat_id, dict):
            for key in ("make", "model", "body_type", "fuel_type", "engine"):
                if cat_id.get(key) == "string":
                    string_value_count += 1
        # Check evidence array for placeholder area values
        evidence = car_profile.get("evidence")
        if isinstance(evidence, list):
            for item in evidence:
                if isinstance(item, dict):
                    area = item.get("area")
                    if isinstance(area, str) and "|" in area:
                        return True
    if string_value_count >= 2:
        return True

    return False


def _is_schema_echo_text(raw_text: str) -> bool:
    """Detect prose that contains the prompt schema instead of JSON data."""
    if not raw_text:
        return False
    lower = raw_text[:1000].lower()
    indicators = 0
    if "let's refine" in lower:
        indicators += 1
    if "return only valid json" in lower:
        indicators += 1
    if "the prompt requires" in lower:
        indicators += 1
    if "do not repeat or describe the schema" in lower:
        indicators += 1
    if "the required json schema" in lower:
        indicators += 1
    return indicators >= 2


def _is_valid_stage_a_payload(payload: Any) -> bool:
    return (
        isinstance(payload, dict)
        and set(payload.keys()) >= _STAGE_A_REQUIRED_KEYS
        and isinstance(payload.get("grounding_successful"), bool)
        and isinstance(payload.get("search_queries_used"), list)
        and isinstance(payload.get("assumptions"), dict)
        and isinstance(payload.get("cars"), dict)
    )
