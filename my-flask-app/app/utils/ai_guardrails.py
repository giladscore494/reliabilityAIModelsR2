from __future__ import annotations

import copy
import json
import logging
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from flask import current_app, has_app_context

GUARDRAIL_VERSION = "2026-05-02-v2"
AI_GUARDRAIL_VERSION = GUARDRAIL_VERSION
UNSAFE_TEXT_FALLBACK = "דורש אימות"
TRUNCATED_TEXT_FALLBACK = "המידע בסעיף זה לא הושלם ולכן הוסתר עד לאימות נוסף."
RELIABILITY_FALLBACK_TEXT = (
    "המידע לגבי גרסה/מכלול זה לא אומת במלואו. יש לבדוק מול רישיון הרכב, "
    "ספר טיפולים ובדיקה מקצועית."
)
RECOMMENDATION_FALLBACK_TEXT = (
    "לא נמצאה המלצה בטוחה לפי המגבלות שהוזנו. כדאי להרחיב תקציב/שנתון/סוג רכב או לבדוק ידנית."
)
SERVICE_FALLBACK_TEXT = (
    "לא ניתן להפיק דוח בטוח מהקובץ הזה. חלק מהנתונים לא זוהו או לא עברו הסרת פרטים מזהים."
)
LEGACY_DISPLAY_NOTE = "תוצאה ישנה — ייתכן שחלק מהמידע לא עבר את שכבת האימות החדשה."
ESTIMATED_PRICE_NOTE = "טווח מחיר משוער בלבד — דורש אימות מול לוחות רכב פעילים."
LOW_SAMPLE_NOTE = "מדגם משתמשים קטן — נתון כיוון בלבד."

_LOGGER = logging.getLogger(__name__)
_HEBREW_RE = re.compile(r"[א-ת]")
_NUM_RE = re.compile(r"-?\d+(?:[.,]\d+)?")
_PII_PATTERNS = (
    re.compile(r"\b\d{2,3}[-\s]?\d{2,3}[-\s]?\d{2,3}\b"),
    re.compile(r"\b0\d{1,2}[-\s]?\d{3}[-\s]?\d{4}\b"),
    re.compile(r"\+972[-\s]?\d{1,2}[-\s]?\d{3}[-\s]?\d{4}\b"),
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
)
_LOW_CONF_STRONG_PHRASES = {
    "בוודאות": "לפי המידע הזמין",
    "מוכח": "נראה כי",
    "אין ספק": "ברמת ודאות בינונית",
    "הכי אמין": "נראה אמין יחסית",
    "הכי משתלם": "נראה משתלם יחסית",
    "ללא סיכון": "דורש אימות",
    "guaranteed": "based on available information",
    "definitely": "it appears",
    "always": "often",
    "never": "not typically",
}
_CONNECTOR_ENDINGS = {
    "and",
    "or",
    "with",
    "without",
    "because",
    "since",
    "including",
    "but",
    "if",
    "כי",
    "אבל",
    "או",
    "עם",
    "בלי",
    "לכן",
    "אם",
    "בגלל",
    "כולל",
}
_ALLOWED_SERVICE_CATEGORIES = {
    "engine",
    "brakes",
    "electrical",
    "tires",
    "ac",
    "transmission",
    "suspension",
    "diagnostic",
    "labor",
    "other",
}
_GUARDRAIL_COUNTERS: Counter[str] = Counter()
_CURRENT_YEAR = datetime.now(timezone.utc).year
_ALLOWED_SOURCE_TYPES = {
    "user_input",
    "verified_source",
    "ai_estimate",
    "internal_calc",
    "user_reported",
    "unknown",
}


def _log_event(event: str, feature: str, **payload: Any) -> None:
    logger = current_app.logger if has_app_context() else _LOGGER
    logger.info(
        json.dumps({"event": event, "feature": feature, **payload}, ensure_ascii=False)
    )


def _increment_guardrail_counter(counter_name: str) -> None:
    _GUARDRAIL_COUNTERS[counter_name] += 1


def contains_pii(text: Any) -> bool:
    return _contains_pii(text)


def redact_pii_from_text(text: Any) -> str:
    return _redact_pii(text) if isinstance(text, str) else _text(text)


def safe_json_obj(value: Any, default: Any = None) -> Any:
    fallback = {} if default is None else default
    try:
        if isinstance(value, (dict, list)):
            return value
        if not isinstance(value, str):
            return fallback
        stripped = value.strip()
        if not stripped:
            return fallback
        parsed = json.loads(stripped)
        if isinstance(parsed, str):
            parsed = json.loads(parsed)
        return parsed if isinstance(parsed, (dict, list)) else fallback
    except Exception:
        return fallback


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _norm_key(value: Any) -> str:
    return re.sub(r"\s+", " ", _text(value)).strip().lower()


def _parse_float(value: Any) -> Optional[float]:
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = _NUM_RE.search(str(value).replace("₪", "").replace("%", ""))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", "."))
    except ValueError:
        return None


def _safe_copy(value: Any) -> Any:
    try:
        return copy.deepcopy(value)
    except Exception:
        return value


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _deep_walk_strings(value: Any) -> Iterable[Tuple[List[Any], str]]:
    def visit(node: Any, path: List[Any]) -> Iterable[Tuple[List[Any], str]]:
        if isinstance(node, str):
            yield path, node
        elif isinstance(node, list):
            for index, item in enumerate(node):
                yield from visit(item, path + [index])
        elif isinstance(node, dict):
            for key, item in node.items():
                yield from visit(item, path + [key])

    yield from visit(value, [])


def _deep_set(value: Any, path: List[Any], new_value: Any) -> None:
    parent = value
    for token in path[:-1]:
        parent = parent[token]
    parent[path[-1]] = new_value


def _ensure_report(feature: str) -> Dict[str, Any]:
    return {
        "feature": feature,
        "status": "passed",
        "critical_issues": [],
        "warnings": [],
        "repair_required": False,
        "safe_to_display": True,
        "fallback_allowed": True,
        "affected_sections": [],
    }


def _add_issue(
    report: Dict[str, Any], message: str, *, critical: bool, section: Optional[str] = None
) -> None:
    bucket = "critical_issues" if critical else "warnings"
    if message not in report[bucket]:
        report[bucket].append(message)
    if section and section not in report["affected_sections"]:
        report["affected_sections"].append(section)


def _finalize_report(report: Dict[str, Any]) -> Dict[str, Any]:
    if report["critical_issues"]:
        report["status"] = "critical"
        report["repair_required"] = True
        report["safe_to_display"] = False
    elif report["warnings"]:
        report["status"] = "warnings"
    return report


def normalize_make_model(value: Any) -> str:
    text = re.sub(r"[/_]+", " ", _text(value))
    if not text:
        return ""
    if _HEBREW_RE.search(text):
        return text
    return " ".join(part.capitalize() if part.isalpha() else part for part in text.split())


def normalize_year(value: Any) -> Optional[int]:
    year = _parse_float(value)
    if year is None:
        return None
    year_int = int(round(year))
    return year_int if 1900 <= year_int <= 2100 else None


def normalize_engine_type(value: Any) -> str:
    text = _norm_key(value)
    if not text:
        return "unknown"
    if any(token in text for token in ("phev", "plug", "פלאג", "נטען")):
        return "phev"
    if any(token in text for token in ("electric", "ev", "חשמל")):
        return "electric"
    if any(token in text for token in ("hybrid", "היבריד")):
        return "hybrid"
    if any(token in text for token in ("diesel", "דיזל")):
        return "diesel"
    if any(token in text for token in ("petrol", "gasoline", "בנזין")):
        return "petrol"
    return text


def normalize_transmission_type(value: Any) -> str:
    text = _norm_key(value)
    if not text:
        return "unknown"
    if any(token in text for token in ("manual", "ידני", "ידנית")):
        return "manual"
    if any(token in text for token in ("cvt", "רציפ")):
        return "cvt"
    if any(token in text for token in ("dct", "dsg", "dual clutch", "dual-clutch", "רובוט")):
        return "robotic"
    if any(token in text for token in ("automatic", "auto", "אוטומט")):
        return "automatic"
    return text


def normalize_currency_ils(value: Any) -> Optional[int]:
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(round(float(value)))
    text = re.sub(r"[^\d,.\-]", "", str(value))
    if text.count(",") and "." not in text:
        text = text.replace(",", "")
    else:
        text = text.replace(",", "")
    try:
        return int(round(float(text)))
    except ValueError:
        amount = _parse_float(value)
        return None if amount is None else int(round(amount))


def normalize_fuel_consumption(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return f"{float(value):g} ק״מ לליטר"
    text = _text(value)
    if not text:
        return None
    numeric = _parse_float(text)
    if numeric is None:
        return None
    lowered = text.lower()
    if "l/100" in lowered or "ליטר ל-100" in lowered or "l per 100" in lowered:
        if numeric <= 0:
            return None
        numeric = round(100 / numeric, 1)
    return f"{numeric:g} ק״מ לליטר"


def normalize_percent(value: Any) -> Optional[float]:
    amount = _parse_float(value)
    return None if amount is None else round(amount, 2)


def validate_score_range(value: Any, minimum: float = 0, maximum: float = 100) -> bool:
    number = _parse_float(value)
    return number is not None and minimum <= number <= maximum


def validate_percentage_range(value: Any, minimum: float = 0, maximum: float = 100) -> bool:
    return validate_score_range(value, minimum, maximum)


def validate_price_range(min_price: Any, max_price: Any) -> bool:
    low = normalize_currency_ils(min_price)
    high = normalize_currency_ils(max_price)
    return low is not None and high is not None and low >= 0 and high >= low


def validate_year_reasonable(value: Any) -> bool:
    year = normalize_year(value)
    return year is not None and 1950 <= year <= _CURRENT_YEAR + 1


def safe_text_caveat(confidence: Any, source_type: Any) -> str:
    confidence_value = normalize_percent(confidence)
    source = _norm_key(source_type)
    if source in {"ai_estimate", "unknown"}:
        return "ברמת ודאות בינונית — דורש אימות."
    if confidence_value is not None and confidence_value < 40:
        return "ברמת ודאות בינונית — דורש אימות מול מקור רשמי."
    if source in {"user_reported", "owner_reported"}:
        return "לפי דיווח משתמשים בלבד — לא כעובדה מאומתת."
    if source in {"system_inferred", "ai_inference"}:
        return "לפי המידע הזמין — ייתכן שנדרש אימות נוסף."
    return "לפי המידע הזמין."


def downgrade_overconfident_language(text: Any, confidence: Any) -> str:
    normalized = _text(text)
    confidence_value = normalize_percent(confidence)
    if not normalized or confidence_value is None or confidence_value >= 70:
        return normalized
    softened = normalized
    for phrase, replacement in _LOW_CONF_STRONG_PHRASES.items():
        softened = re.sub(re.escape(phrase), replacement, softened, flags=re.IGNORECASE)
    return softened


def is_probably_truncated_text(text: Any) -> bool:
    value = _text(text)
    if len(value) < 12:
        return False
    if value.endswith((".", "!", "?", "…", ")", "]", "'", "\"")):
        return False
    if value.endswith((":", ",", ";", "-", "–", "—", "(", "[", "{", "\"")):
        return True
    if value.count("(") != value.count(")") or value.count("[") != value.count("]"):
        return True
    if value.count("\"") % 2 == 1:
        return True
    if re.search(r"(?:בדרך כלל|usually|typically)$", value.lower()):
        return True
    tail = value.lower().split()[-1]
    return tail in _CONNECTOR_ENDINGS


def trim_to_last_complete_sentence(text: Any) -> str:
    value = _text(text)
    matches = list(re.finditer(r"[.!?…]|[。！？]", value))
    return value[: matches[-1].end()].strip() if matches else ""


def repair_or_hide_truncated_text(result: Any, feature_key: str) -> Any:
    payload = _safe_copy(result)
    if isinstance(payload, str):
        if not is_probably_truncated_text(payload):
            return payload
        trimmed = trim_to_last_complete_sentence(payload)
        _log_event("ai_guardrail_truncated_text_repaired", feature_key)
        return trimmed or TRUNCATED_TEXT_FALLBACK
    if not isinstance(payload, (dict, list)):
        return payload
    for path, text in list(_deep_walk_strings(payload)):
        if is_probably_truncated_text(text):
            _deep_set(payload, path, trim_to_last_complete_sentence(text) or TRUNCATED_TEXT_FALLBACK)
    return payload


def _contains_pii(value: Any) -> bool:
    if isinstance(value, str):
        return any(pattern.search(value) for pattern in _PII_PATTERNS)
    if isinstance(value, list):
        return any(_contains_pii(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_pii(item) for item in value.values())
    return False


def _redact_pii(value: Any) -> Any:
    if isinstance(value, str):
        redacted = value
        for pattern in _PII_PATTERNS:
            redacted = pattern.sub("[REDACTED]", redacted)
        return redacted
    if isinstance(value, list):
        return [_redact_pii(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact_pii(item) for key, item in value.items()}
    return value


def _normalize_fuel_units_in_payload(value: Any, key_hint: str = "") -> Any:
    payload = _safe_copy(value)
    if isinstance(payload, str):
        if any(token in key_hint.lower() for token in ("fuel", "consumption", "צריכ")):
            return normalize_fuel_consumption(payload) or payload
        return payload
    if isinstance(payload, list):
        return [_normalize_fuel_units_in_payload(item, key_hint) for item in payload]
    if isinstance(payload, dict):
        for key, item in list(payload.items()):
            if "fuel" in str(key).lower() or "צריכ" in str(key):
                normalized = normalize_fuel_consumption(item)
                if normalized:
                    payload[key] = normalized
                    continue
            payload[key] = _normalize_fuel_units_in_payload(item, str(key))
    return payload


def _confidence_from_result(ai_result: Mapping[str, Any]) -> Optional[float]:
    if not isinstance(ai_result, Mapping):
        return None
    for key in ("confidence", "confidence_score", "confidence_pct"):
        value = normalize_percent(ai_result.get(key))
        if value is not None:
            return value
    note = ai_result.get("confidence_note")
    if isinstance(note, Mapping):
        return normalize_percent(note.get("confidence"))
    return None


def _soften_payload_text(value: Any, confidence: Any) -> Any:
    payload = _safe_copy(value)
    if isinstance(payload, str):
        return downgrade_overconfident_language(payload, confidence)
    if isinstance(payload, list):
        return [_soften_payload_text(item, confidence) for item in payload]
    if isinstance(payload, dict):
        for key, item in list(payload.items()):
            payload[key] = _soften_payload_text(item, confidence)
    return payload


def _find_checked_versions(ai_result: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    direct = ai_result.get("checked_versions")
    if isinstance(direct, dict):
        return direct
    computed = ai_result.get("computed_result")
    if isinstance(computed, dict) and isinstance(computed.get("checked_versions"), dict):
        return computed["checked_versions"]
    return {}


def _find_recommendations(ai_result: Mapping[str, Any]) -> List[Dict[str, Any]]:
    for key in ("recommended_cars", "recommendations", "top3", "cards"):
        value = ai_result.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    nested = ai_result.get("result")
    return _find_recommendations(nested) if isinstance(nested, dict) else []


def _classify_source_type(value: Any) -> str:
    normalized = _norm_key(value).replace(" ", "_")
    if normalized in _ALLOWED_SOURCE_TYPES:
        return normalized
    aliases = {
        "ai_inference": "ai_estimate",
        "system_inferred": "ai_estimate",
        "owner_reported": "user_reported",
        "source_verified": "verified_source",
        "calculated": "internal_calc",
        "mixed": "unknown",
    }
    return aliases.get(normalized, "unknown")


def _normalize_count(value: Any) -> Optional[int]:
    number = _parse_float(value)
    if number is None:
        return None
    count = int(round(number))
    return count if 0 <= count <= 20 else None


def _iter_text_values(value: Any) -> Iterable[str]:
    for _path, text in _deep_walk_strings(value):
        if text:
            yield text


def _json_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return _text(value)


def _contains_any(value: Any, tokens: Iterable[str]) -> bool:
    haystack = _json_text(value).lower()
    return any(token.lower() in haystack for token in tokens)


def _ensure_caveat_list(result: Dict[str, Any], message: str) -> None:
    caveats = result.get("guardrail_caveats")
    if not isinstance(caveats, list):
        caveats = []
    if message not in caveats:
        caveats.append(message)
    result["guardrail_caveats"] = caveats


def _append_inspection_caveat(result: Dict[str, Any]) -> None:
    inspection_note = "לא ניתן לאשר מצב מכני של רכב ספציפי בלי בדיקה מקצועית."
    _ensure_caveat_list(result, inspection_note)
    summary = _text(result.get("reliability_summary"))
    if summary and inspection_note not in summary:
        result["reliability_summary"] = f"{summary} {inspection_note}".strip()


def _low_data_quality(result: Mapping[str, Any]) -> bool:
    label = _norm_key(result.get("data_quality_label"))
    if label in {"חסרה", "חלקית", "low", "missing", "partial"}:
        return True
    sources = _safe_list(result.get("sources"))
    source_coverage = normalize_percent(result.get("source_coverage"))
    return len(sources) < 2 or (source_coverage is not None and source_coverage < 50)


def _comparison_sections(ai_result: Mapping[str, Any]) -> List[Dict[str, Any]]:
    decision = _safe_dict(ai_result.get("decision_result"))
    items = decision.get("category_decisions")
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    narrative = _safe_dict(ai_result.get("narrative"))
    items = narrative.get("category_explanations")
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    return []


def _build_central_differences(ai_result: Mapping[str, Any]) -> List[str]:
    differences: List[str] = []
    for item in _comparison_sections(ai_result):
        winner = item.get("winner") or item.get("preferred")
        why = _text(item.get("why") or item.get("summary") or item.get("explanation"))
        category = _text(
            item.get("category_name_he") or item.get("category") or item.get("category_key")
        )
        if winner and why:
            differences.append(f"{category}: {winner} — {why}" if category else f"{winner} — {why}")
        if len(differences) >= 8:
            break
    return differences[:8]


def _should_rebuild_central_differences(
    central_differences: Any, rebuilt_differences: List[str]
) -> bool:
    if not rebuilt_differences:
        return False
    if isinstance(central_differences, list) and not central_differences:
        return True
    return isinstance(central_differences, str) and "אין פערים" in central_differences


def validate_comparison_result(user_payload: Any, ai_result: Any) -> Dict[str, Any]:
    report = _ensure_report("vehicle_comparison")
    payload = _safe_dict(user_payload)
    result = _safe_dict(ai_result)
    cars = _safe_list(payload.get("cars") if isinstance(payload, dict) else payload)
    checked_versions = _find_checked_versions(result)
    if cars and not checked_versions:
        _add_issue(report, "missing checked-version block", critical=True, section="checked_versions")
    for index, car in enumerate(cars, start=1):
        selected = _safe_dict(car)
        slot = checked_versions.get(f"car_{index}") or checked_versions.get(str(index)) or {}
        if normalize_year(selected.get("year")) and normalize_year(slot.get("year")) not in (
            None,
            normalize_year(selected.get("year")),
        ):
            _add_issue(report, f"car_{index}: wrong year", critical=True, section="checked_versions")
        if normalize_make_model(selected.get("model")) and normalize_make_model(slot.get("model")):
            if _norm_key(selected.get("model")) != _norm_key(slot.get("model")):
                _add_issue(report, f"car_{index}: wrong model identity", critical=True, section="checked_versions")
        if normalize_engine_type(selected.get("engine_type") or selected.get("fuel_type")) == "petrol":
            if normalize_engine_type(slot.get("engine_type") or slot.get("fuel_type")) in {
                "diesel",
                "hybrid",
                "electric",
                "phev",
            }:
                _add_issue(report, f"car_{index}: petrol mismatch", critical=True, section="checked_versions")
        selected_trans = normalize_transmission_type(selected.get("transmission") or selected.get("gearbox"))
        slot_trans = normalize_transmission_type(slot.get("transmission") or slot.get("gearbox"))
        if {selected_trans, slot_trans} == {"automatic", "manual"}:
            _add_issue(report, f"car_{index}: transmission mismatch", critical=True, section="checked_versions")
        elif selected_trans == "automatic" and slot_trans in {"cvt", "robotic"}:
            _add_issue(report, f"car_{index}: automatic subtype not fully verified", critical=False, section="checked_versions")
    central = result.get("central_differences")
    rebuilt_differences = _build_central_differences(result)
    if _should_rebuild_central_differences(central, rebuilt_differences):
        _add_issue(report, "central differences missing despite category winners", critical=False, section="central_differences")
    return _finalize_report(report)


def validate_reliability_result(user_payload: Any, ai_result: Any) -> Dict[str, Any]:
    report = _ensure_report("reliability_analysis")
    payload = _safe_dict(user_payload)
    result = _safe_dict(ai_result)
    vehicle_identity = _safe_dict(
        _safe_dict(result.get("vehicle_profile")).get("vehicle_identity") or result.get("vehicle_identity")
    )
    payload_engine = normalize_engine_type(
        payload.get("engine")
        or payload.get("engine_type")
        or payload.get("fuel_type")
    )
    result_engine = normalize_engine_type(
        vehicle_identity.get("engine_type")
        or vehicle_identity.get("fuel_type")
        or _safe_dict(_safe_dict(result.get("vehicle_profile")).get("powertrain_specs")).get("engine")
    )
    payload_transmission = normalize_transmission_type(
        payload.get("transmission") or payload.get("gearbox")
    )
    result_transmission = normalize_transmission_type(
        vehicle_identity.get("transmission")
        or vehicle_identity.get("gearbox")
        or _safe_dict(_safe_dict(result.get("vehicle_profile")).get("powertrain_specs")).get("gearbox")
    )
    for field in ("make", "model"):
        if normalize_make_model(payload.get(field)) and normalize_make_model(vehicle_identity.get(field)):
            if _norm_key(payload.get(field)) != _norm_key(vehicle_identity.get(field)):
                _add_issue(report, f"wrong {field} identity", critical=True, section="vehicle_identity")
    if normalize_year(payload.get("year")) and normalize_year(vehicle_identity.get("year")) not in (
        None,
        normalize_year(payload.get("year")),
    ):
        _add_issue(report, "wrong vehicle year", critical=True, section="vehicle_identity")
    if payload_engine not in {"", "unknown"} and result_engine not in {"", "unknown"} and payload_engine != result_engine:
        _add_issue(report, "engine or fuel mismatch", critical=True, section="vehicle_identity")
    if {payload_transmission, result_transmission} == {"automatic", "manual"}:
        _add_issue(report, "transmission mismatch", critical=True, section="vehicle_identity")
    if _contains_any(result, ("mechanically sound", "תקין מכנית", "מכאנית תקינה", "safe and sound")):
        _add_issue(report, "claim about specific car mechanical condition without inspection", critical=True, section="reliability_summary")
    confidence = _confidence_from_result(result)
    if _contains_any(result, ("known defect", "defect confirmed", "תקלה ידועה בוודאות")) and not _safe_list(result.get("sources")):
        _add_issue(report, "unsupported major defect stated as fact", critical=True, section="known_risks")
    if (confidence is not None and confidence >= 70 and _low_data_quality(result)) or (
        confidence is not None
        and confidence < 70
        and _contains_any(result, ("מוכח", "בוודאות", "אין ספק", "guaranteed", "definitely"))
    ):
        _add_issue(report, "low data quality paired with high-confidence phrasing", critical=True, section="confidence_note")
    if not _safe_list(result.get("sources")):
        _add_issue(report, "source coverage limited", critical=False, section="confidence_note")
    source_type = _classify_source_type(result.get("source_type"))
    if source_type in {"ai_estimate", "user_reported", "unknown"}:
        _add_issue(report, f"source type is {source_type}", critical=False, section="confidence_note")
    if _contains_any(result, ("israeli certainty", "בישראל בוודאות", "בטוח לשוק הישראלי")) and source_type != "verified_source":
        _add_issue(report, "Israeli-market version uncertain", critical=False, section="vehicle_identity")
    if _norm_key(vehicle_identity.get("trim")) in {"לא מאומת", "unknown", "unverified"}:
        _add_issue(report, "trim not verified", critical=False, section="vehicle_identity")
    if not _safe_list(result.get("sources")):
        _add_issue(report, "source confidence medium/low", critical=False, section="confidence_note")
    if not payload.get("mileage_range") and not payload.get("mileage_km"):
        _add_issue(report, "mileage missing", critical=False, section="what_to_check")
        if _contains_any(result, ("neglect", "מוזנח", "lack of maintenance", "לא טופל")):
            _add_issue(report, "maintenance neglect inferred without mileage/history", critical=True, section="what_to_check")
    if any(key in result for key in ("score_0_100", "internal_score", "reliability_score")):
        score_value = result.get("score_0_100") or result.get("internal_score") or result.get("reliability_score")
        if not validate_score_range(score_value, 0, 100):
            _add_issue(report, "internal score out of range", critical=True, section="confidence_note")
        else:
            _add_issue(report, "internal score should not be primary user output", critical=False, section="confidence_note")
    known_risks = _safe_list(result.get("known_risks"))
    if known_risks:
        recall_like = set()
        for item in known_risks:
            item_key = _norm_key(item)
            if "recall" in item_key or "ריקול" in item_key or "campaign" in item_key:
                recall_like.add(item_key)
        if len(recall_like) != len(
            [item for item in known_risks if "recall" in _norm_key(item) or "ריקול" in _norm_key(item) or "campaign" in _norm_key(item)]
        ):
            _add_issue(report, "recall overlap repeated in conclusions", critical=False, section="known_risks")
    return _finalize_report(report)


def validate_recommendation_result(user_payload: Any, ai_result: Any) -> Dict[str, Any]:
    report = _ensure_report("recommendations")
    payload = _safe_dict(user_payload)
    recommendations = _find_recommendations(_safe_dict(ai_result))
    invalid_cards = 0
    budget_max = normalize_currency_ils(payload.get("budget_max") or payload.get("budget"))
    required_seats = _normalize_count(
        payload.get("seats")
        or payload.get("seats_choice")
        or payload.get("family_size")
    )
    preferred_trans = normalize_transmission_type(
        payload.get("transmission_preference")
        or payload.get("transmission")
        or (_safe_list(payload.get("gears")) or [None])[0]
    )
    preferred_body = _norm_key(payload.get("body_style") or payload.get("body_type"))
    preferred_fuels = {
        normalize_engine_type(item)
        for item in _safe_list(payload.get("preferred_fuels") or payload.get("fuels") or payload.get("fuels_he"))
    }
    ev_rejected = bool(payload.get("reject_ev") or payload.get("ev_rejected") or payload.get("no_ev"))
    for index, card in enumerate(recommendations, start=1):
        invalid = False
        price = normalize_currency_ils(
            card.get("price_ils")
            or card.get("list_price_ils")
            or card.get("price")
            or (_safe_list(card.get("price_range_nis")) or [None])[1]
            or card.get("price_range_nis")
        )
        stretch = bool(card.get("stretch_option") or card.get("is_stretch"))
        if budget_max is not None and price is not None and price > budget_max and not stretch:
            _add_issue(report, f"recommendation_{index}: over budget", critical=True, section="recommendations")
            invalid = True
        seats = _normalize_count(card.get("seats"))
        if required_seats and seats and seats < required_seats:
            _add_issue(report, f"recommendation_{index}: insufficient seats", critical=True, section="recommendations")
            invalid = True
        card_transmission = normalize_transmission_type(
            card.get("transmission") or card.get("gear") or card.get("gearbox")
        )
        if preferred_trans == "automatic" and card_transmission == "manual":
            _add_issue(report, f"recommendation_{index}: manual despite automatic requirement", critical=True, section="recommendations")
            invalid = True
        card_fuel = normalize_engine_type(
            card.get("fuel_type") or card.get("fuel") or card.get("powertrain")
        )
        if ev_rejected and card_fuel in {"electric", "phev"}:
            _add_issue(report, f"recommendation_{index}: EV rejected by user", critical=True, section="recommendations")
            invalid = True
        if preferred_fuels and card_fuel not in {"unknown", ""} and card_fuel not in preferred_fuels:
            _add_issue(report, f"recommendation_{index}: hard fuel constraint violated", critical=True, section="recommendations")
            invalid = True
        if preferred_body and preferred_body not in {"כללי", "unknown"}:
            card_body = _norm_key(card.get("body_style") or card.get("body_type"))
            if card_body and card_body != preferred_body:
                _add_issue(report, f"recommendation_{index}: body preference mismatch", critical=True, section="recommendations")
                invalid = True
        for key, label in (
            ("why_it_fits", "fit"),
            ("tradeoff", "tradeoff"),
            ("what_to_check", "check"),
            ("confidence", "confidence"),
            ("price_caveat", "price caveat"),
        ):
            if not card.get(key) and not (key == "why_it_fits" and card.get("reason_he")):
                severity = key in {"why_it_fits", "what_to_check", "confidence"} and index == 1
                _add_issue(
                    report,
                    f"recommendation_{index}: missing {label}",
                    critical=severity,
                    section="recommendations",
                )
                invalid = invalid or severity
        if not card.get("tradeoff"):
            _add_issue(report, f"recommendation_{index}: tradeoff missing", critical=False, section="recommendations")
        if any(token in _norm_key(card.get("availability_note")) for token in ("uncertain", "limited", "not verified")):
            _add_issue(report, f"recommendation_{index}: market availability uncertain", critical=False, section="recommendations")
        if _norm_key(card.get("trim")) in {"לא מאומת", "unknown", "unverified"}:
            _add_issue(report, f"recommendation_{index}: trim/version uncertain", critical=False, section="recommendations")
        if card.get("available_in_israel") is False:
            _add_issue(report, f"recommendation_{index}: unavailable in Israeli market", critical=True, section="recommendations")
            invalid = True
        if not card.get("price_caveat") and any(
            token in _norm_key(card.get("price_note") or card.get("price_source"))
            for token in ("estimated", "estimate", "משוער")
        ):
            _add_issue(report, f"recommendation_{index}: price estimate uncertain", critical=False, section="recommendations")
        invalid_cards += int(invalid)
    if recommendations and invalid_cards / max(len(recommendations), 1) > 0.5:
        _add_issue(report, "more than half the recommendation cards are invalid", critical=True, section="recommendations")
    if not recommendations:
        _add_issue(report, "no recommendations returned", critical=True, section="recommendations")
    return _finalize_report(report)


def validate_leasing_advisor_result(user_payload: Any, ai_result: Any, deterministic_calc: Any) -> Dict[str, Any]:
    report = _ensure_report("leasing_advisor")
    result = _safe_dict(ai_result)
    calc = _safe_dict(deterministic_calc)
    tolerance = float(calc.get("tolerance") or 1.0)
    for key in ("monthly_payment", "down_payment", "final_payment", "balloon_payment", "total_cost", "monthly_bik", "list_price_ils"):
        expected = normalize_currency_ils(calc.get(key))
        actual = normalize_currency_ils(result.get(key))
        if expected is None:
            continue
        if key in {"final_payment", "balloon_payment"} and actual is None:
            _add_issue(report, f"missing {key}", critical=True, section="numbers")
        elif actual is not None and abs(actual - expected) > tolerance:
            _add_issue(report, f"{key} differs from deterministic calculation", critical=True, section="numbers")
    top3 = _safe_list(result.get("top3"))
    candidates = {
        (
            _norm_key(item.get("make")),
            _norm_key(item.get("model")),
        ): item
        for item in _safe_list(calc.get("candidates"))
        if isinstance(item, dict)
    }
    for index, item in enumerate(top3, start=1):
        if not isinstance(item, dict):
            continue
        matched = candidates.get((_norm_key(item.get("make")), _norm_key(item.get("model"))), {})
        for key in ("monthly_bik", "list_price_ils"):
            expected = normalize_currency_ils(matched.get(key))
            actual = normalize_currency_ils(item.get(key))
            if expected is not None and actual is not None and abs(actual - expected) > tolerance:
                _add_issue(report, f"top3_{index}: {key} differs from deterministic calculation", critical=True, section="numbers")
    if any(token in json.dumps(result, ensure_ascii=False).lower() for token in ("guaranteed savings", "safe deal", "ללא סיכון", "חיסכון מובטח", "guaranteed cheaper", "best deal")):
        _add_issue(report, "unsafe guaranteed-savings language", critical=True, section="prose")
    assumptions = result.get("assumptions") or calc.get("assumptions") or calc.get("frame")
    if not assumptions:
        _add_issue(report, "missing key assumptions", critical=True, section="assumptions")
    elif not _contains_any(assumptions, ("cpi", "index", "interest", "mileage", "residual", "depreciation", "bik", "list_price", "insurance", "service")):
        _add_issue(report, "missing key assumptions", critical=True, section="assumptions")
    if not _contains_any(assumptions, ("cpi", "indexation")) and calc.get("cpi_indexed"):
        _add_issue(report, "CPI/indexation assumption missing", critical=False, section="assumptions")
    if not _contains_any(assumptions, ("mileage", "ק\"מ", "נסועה")):
        _add_issue(report, "mileage assumption missing", critical=False, section="assumptions")
    if _contains_any(result, ("residual", "future value", "ערך עתידי")):
        _add_issue(report, "residual value estimated", critical=False, section="prose")
    return _finalize_report(report)


def validate_service_prices_result(user_payload: Any, ai_result: Any) -> Dict[str, Any]:
    report = _ensure_report("service_prices")
    result = _safe_dict(ai_result)
    items = _safe_list(result.get("items") or result.get("line_items") or result.get("canonical_items"))
    subtotal = 0
    subtotal_seen = False
    for index, item in enumerate(items, start=1):
        row = _safe_dict(item)
        code = _text(row.get("canonical_code") or row.get("service_code"))
        if not code:
            _add_issue(report, f"item_{index}: canonical service code missing", critical=True, section="items")
        elif code == "unknown_requires_review":
            _add_issue(report, f"item_{index}: unknown canonical item requires review", critical=False, section="items")
        category = _norm_key(row.get("category"))
        if category and category not in _ALLOWED_SERVICE_CATEGORIES:
            _add_issue(report, f"item_{index}: invalid category", critical=True, section="items")
        price = normalize_currency_ils(row.get("price_ils") or row.get("invoice_price_ils") or row.get("price"))
        qty = _normalize_count(row.get("qty"))
        if price is None:
            _add_issue(report, f"item_{index}: non-numeric price", critical=False, section="items")
        if qty is None and row.get("qty") not in (None, "", " "):
            _add_issue(report, f"item_{index}: non-numeric quantity", critical=True, section="items")
        if price is not None:
            subtotal += price * (qty or 1)
            subtotal_seen = True
        confidence = normalize_percent(row.get("confidence"))
        if confidence is not None and confidence < 50:
            _add_issue(report, f"item_{index}: low confidence item should be marked לבדיקה", critical=False, section="items")
    sample_size = _normalize_count(result.get("sample_size") or _safe_dict(result.get("samples_meta")).get("total_cohort_n"))
    if sample_size is not None and sample_size < 10:
        _add_issue(report, "benchmark sample size is low", critical=False, section="benchmarks")
    total = normalize_currency_ils(result.get("total_price_ils") or result.get("total"))
    if subtotal_seen and total is not None and abs(subtotal - total) > 2:
        _add_issue(report, "total mismatch beyond tolerance", critical=True, section="totals")
    return _finalize_report(report)


def validate_invoice_analysis_result(user_payload: Any, ai_result: Any) -> Dict[str, Any]:
    report = _ensure_report("invoice_scanner")
    result = ai_result if isinstance(ai_result, dict) else {}
    if not isinstance(ai_result, (dict, list)):
        _add_issue(report, "report_json wrong type", critical=True, section="report_json")
    if _contains_pii(result):
        _add_issue(report, "PII detected in invoice result", critical=True, section="pii")
    if any(key in result for key in ("raw_invoice_bytes", "invoice_image_bytes", "image_bytes")):
        _add_issue(report, "raw invoice bytes scheduled for storage", critical=True, section="storage")
    totals = _safe_dict(result.get("totals"))
    items = _safe_list(result.get("items"))
    if totals and items:
        computed_total = 0
        for item in items:
            row = _safe_dict(item)
            price = normalize_currency_ils(row.get("price_ils"))
            qty = _normalize_count(row.get("qty")) or 1
            if price is None and row.get("price_ils") not in (None, ""):
                _add_issue(report, "non-numeric qty/price in arithmetic path", critical=True, section="items")
                break
            if price is not None:
                computed_total += price * qty
        expected_total = normalize_currency_ils(totals.get("total_price_ils"))
        if expected_total is not None and abs(computed_total - expected_total) > 2:
            _add_issue(report, "invoice total mismatch beyond tolerance", critical=True, section="totals")
    return _finalize_report(report)


def validate_research_submission(payload: Any) -> Dict[str, Any]:
    report = _ensure_report("research_collection")
    data = _safe_dict(payload)
    if data.get("consent_required", True) and not data.get("consent_accepted"):
        _add_issue(report, "research consent missing", critical=True, section="consent")
    vehicle = _safe_dict(data.get("vehicle") or data.get("vehicle_context"))
    if vehicle.get("year") and not validate_year_reasonable(vehicle.get("year")):
        _add_issue(report, "vehicle year is not reasonable", critical=True, section="vehicle")
    if data.get("repair_cost_ils") is not None and normalize_currency_ils(data.get("repair_cost_ils")) is None:
        _add_issue(report, "repair cost must be numeric ILS", critical=True, section="costs")
    repair_cost = normalize_currency_ils(data.get("repair_cost_ils"))
    if repair_cost is not None and repair_cost > 50000:
        _add_issue(report, "repair cost outlier flagged", critical=False, section="costs")
    if _contains_pii(_text(data.get("notes") or data.get("free_text"))):
        _add_issue(report, "free text contains PII and must be redacted", critical=True, section="notes")
    if data.get("duplicate_report"):
        _add_issue(report, "duplicate report flagged", critical=False, section="quality")
    sample_size = _normalize_count(data.get("sample_size"))
    if sample_size is not None and sample_size < 10:
        _add_issue(report, "small sample size caveat required", critical=False, section="quality")
    report["field_sources"] = {
        key: (
            "redacted"
            if key in {"notes", "free_text"} and _contains_pii(value)
            else "source_verified"
            if key in {"vehicle", "vehicle_context"}
            else "calculated"
            if key in {"sample_size", "repair_cost_ils"}
            else "user_reported"
        )
        for key, value in data.items()
    }
    return _finalize_report(report)


def validate_feature_legal_access(user: Any, feature_key: str, operation: str) -> Optional[Dict[str, Any]]:
    from app.legal import (
        INVOICE_ANON_STORAGE_KEY,
        INVOICE_ANON_STORAGE_VERSION,
        INVOICE_EXT_PROCESSING_KEY,
        INVOICE_EXT_PROCESSING_VERSION,
        PRIVACY_VERSION,
        TERMS_VERSION,
        has_accepted_feature,
    )
    from app.models import LegalAcceptance, ResearchConsent

    user_id = getattr(user, "id", user)
    terms_version = current_app.config.get("TERMS_VERSION", TERMS_VERSION) if has_app_context() else TERMS_VERSION
    privacy_version = current_app.config.get("PRIVACY_VERSION", PRIVACY_VERSION) if has_app_context() else PRIVACY_VERSION
    if not user_id:
        return {
            "error": "LEGAL_ACCEPTANCE_REQUIRED",
            "feature_key": feature_key,
            "required_terms_version": terms_version,
            "required_privacy_version": privacy_version,
        }
    accepted = LegalAcceptance.query.filter_by(
        user_id=user_id, terms_version=terms_version, privacy_version=privacy_version
    ).first()
    if not accepted:
        return {
            "error": "LEGAL_ACCEPTANCE_REQUIRED",
            "feature_key": feature_key,
            "required_terms_version": terms_version,
            "required_privacy_version": privacy_version,
        }
    if feature_key == "invoice_scanner":
        if not has_accepted_feature(user_id, INVOICE_EXT_PROCESSING_KEY, INVOICE_EXT_PROCESSING_VERSION):
            return {
                "error": "LEGAL_ACCEPTANCE_REQUIRED",
                "feature_key": feature_key,
                "required_terms_version": terms_version,
                "required_privacy_version": privacy_version,
                "required_feature_key": INVOICE_EXT_PROCESSING_KEY,
            }
        if operation in {"read", "ocr", "vision", "analyze", "write"} and not has_accepted_feature(
            user_id, INVOICE_ANON_STORAGE_KEY, INVOICE_ANON_STORAGE_VERSION
        ):
            return {
                "error": "LEGAL_ACCEPTANCE_REQUIRED",
                "feature_key": feature_key,
                "required_terms_version": terms_version,
                "required_privacy_version": privacy_version,
                "required_feature_key": INVOICE_ANON_STORAGE_KEY,
            }
    if feature_key == "research_collection":
        consent = (
            ResearchConsent.query.filter_by(
                user_id=user_id,
                terms_version=terms_version,
                privacy_version=privacy_version,
                consent_given=True,
            )
            .filter(ResearchConsent.revoked_at.is_(None))
            .first()
        )
        if not consent:
            return {
                "error": "LEGAL_ACCEPTANCE_REQUIRED",
                "feature_key": feature_key,
                "required_terms_version": terms_version,
                "required_privacy_version": privacy_version,
            }
    return None


def validate_dashboard_history_payload(payload: Any) -> Dict[str, Any]:
    report = _ensure_report("dashboard_history")
    data = _safe_dict(payload)
    meta = _safe_dict(data.get("guardrail_meta") or data.get("guardrails"))
    version = _text(meta.get("guardrail_version") or data.get("guardrail_version"))
    if version and version != GUARDRAIL_VERSION:
        _add_issue(report, "cached result uses old guardrail version", critical=False, section="guardrail_meta")
    if not version:
        _add_issue(report, "guardrail metadata missing", critical=False, section="guardrail_meta")
    if _contains_pii(data):
        _add_issue(report, "PII detected in history payload", critical=True, section="history")
    if any(key in data for key in ("prompt", "prompt_text", "debug", "debug_info", "internal_score")):
        _add_issue(report, "internal debug fields exposed", critical=True, section="history")
    for _path, text in _deep_walk_strings(data):
        if is_probably_truncated_text(text):
            _add_issue(report, "truncated text detected in history payload", critical=True, section="history")
            break
    return _finalize_report(report)


def should_repair(feature_key: str, validation_report: Mapping[str, Any]) -> bool:
    return bool(validation_report.get("critical_issues"))


def build_minimal_repair_prompt(
    feature_key: str, validation_report: Mapping[str, Any], affected_sections: Any
) -> str:
    return json.dumps(
        {
            "feature": feature_key,
            "issues": list(validation_report.get("critical_issues") or []),
            "sections": list(affected_sections or validation_report.get("affected_sections") or []),
            "instructions": [
                "Return JSON only.",
                "Patch only the listed sections.",
                "Keep deterministic values unchanged.",
            ],
        },
        ensure_ascii=False,
    )


def apply_repair_patch(original_result: Any, repair_patch: Any) -> Any:
    if not isinstance(original_result, dict) or not isinstance(repair_patch, dict):
        return original_result
    merged = _safe_copy(original_result)
    for key, value in repair_patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            nested = dict(merged[key])
            nested.update(value)
            merged[key] = nested
        else:
            merged[key] = value
    return merged


def _guardrail_meta(feature_key: str, report: Mapping[str, Any], repaired: bool) -> Dict[str, Any]:
    return {
        "guardrail_version": GUARDRAIL_VERSION,
        "validation_status": report.get("status", "passed"),
        "warnings_count": len(report.get("warnings") or []),
        "critical_count": len(report.get("critical_issues") or []),
        "repaired": repaired,
        "feature_key": feature_key,
    }


def safe_fallback_on_repair_failure(original_result: Any, validation_report: Mapping[str, Any]) -> Any:
    payload = _safe_copy(original_result)
    feature = validation_report.get("feature") or "unknown"
    affected = list(validation_report.get("affected_sections") or [])
    fallback_text = UNSAFE_TEXT_FALLBACK
    if feature == "reliability_analysis":
        fallback_text = RELIABILITY_FALLBACK_TEXT
    elif feature in {"service_prices", "invoice_scanner"}:
        fallback_text = SERVICE_FALLBACK_TEXT
    if not isinstance(payload, dict):
        return {"message": fallback_text, "guardrail_meta": _guardrail_meta(feature, validation_report, False)}
    if feature == "recommendations":
        payload["recommended_cars"] = []
        payload["recommendations"] = []
        payload["message"] = RECOMMENDATION_FALLBACK_TEXT
    elif affected:
        for section in affected:
            if section in payload:
                payload[section] = fallback_text
    else:
        payload["message"] = fallback_text
    payload["guardrail_meta"] = _guardrail_meta(feature, validation_report, False)
    return payload


def _repair_comparison_result(user_payload: Any, result: Dict[str, Any], report: Mapping[str, Any]) -> Dict[str, Any]:
    patched = _safe_copy(result)
    cars = _safe_list(_safe_dict(user_payload).get("cars") if isinstance(user_payload, dict) else user_payload)
    if not _find_checked_versions(patched):
        patched["checked_versions"] = {
            f"car_{index}": {
                "make": normalize_make_model(_safe_dict(car).get("make")),
                "model": normalize_make_model(_safe_dict(car).get("model")),
                "year": normalize_year(_safe_dict(car).get("year")),
                "engine_type": normalize_engine_type(_safe_dict(car).get("engine_type") or _safe_dict(car).get("fuel_type")),
                "transmission": normalize_transmission_type(_safe_dict(car).get("transmission") or _safe_dict(car).get("gearbox")),
                "notes": "הגרסה שנבחרה דורשת אימות מול מפרט רשמי.",
            }
            for index, car in enumerate(cars, start=1)
        }
    differences = _build_central_differences(patched)
    if differences:
        patched["central_differences"] = differences
    if report.get("critical_issues"):
        patched["visible_warning"] = "הגרסה שנבחרה דורשת אימות לפני הסתמכות על ההשוואה."
    if patched.get("prices_estimated") or any(
        token in _norm_key(patched.get("price_note"))
        for token in ("estimated", "משוער", "unverified")
    ):
        patched["price_note"] = ESTIMATED_PRICE_NOTE
    return _normalize_fuel_units_in_payload(patched)


def _repair_reliability_result(result: Dict[str, Any]) -> Dict[str, Any]:
    patched = _safe_copy(result)
    inspection_sections = {"reliability_summary", "what_to_check"}
    for key in ("known_risks", "reliability_summary", "what_to_check", "confidence_note"):
        if key in patched:
            patched[key] = RELIABILITY_FALLBACK_TEXT
            if key in inspection_sections:
                _append_inspection_caveat(patched)
    for key in ("score_0_100", "internal_score", "reliability_score"):
        patched.pop(key, None)
    vehicle_identity = _safe_dict(
        patched.get("vehicle_identity") or _safe_dict(_safe_dict(patched.get("vehicle_profile")).get("vehicle_identity"))
    )
    if vehicle_identity:
        vehicle_identity["verification_required"] = True
        if "vehicle_identity" in patched:
            patched["vehicle_identity"] = vehicle_identity
        else:
            patched.setdefault("vehicle_profile", {}).setdefault("vehicle_identity", {}).update(vehicle_identity)
    patched["source_type"] = _classify_source_type(patched.get("source_type"))
    _ensure_caveat_list(patched, safe_text_caveat(patched.get("confidence"), patched.get("source_type")))
    return patched


def _repair_recommendations_result(result: Dict[str, Any], report: Mapping[str, Any]) -> Dict[str, Any]:
    patched = _safe_copy(result)
    invalid_markers = {
        item.split(":", 1)[0]
        for item in report.get("critical_issues") or []
        if item.startswith("recommendation_")
    }
    safe_cards = []
    for index, card in enumerate(_find_recommendations(patched), start=1):
        if f"recommendation_{index}" in invalid_markers:
            continue
        fixed = dict(card)
        fixed.setdefault("why_it_fits", fixed.get("reason_he") or "לפי המידע הזמין, יש התאמה חלקית לצרכים שהוגדרו.")
        fixed.setdefault("tradeoff", "דורש אימות של רמת הגימור והזמינות בפועל.")
        fixed.setdefault("what_to_check", "בדקו זמינות, מפרט בפועל והיסטוריית טיפולים.")
        fixed.setdefault("confidence", "ברמת ודאות בינונית")
        fixed.setdefault("price_caveat", ESTIMATED_PRICE_NOTE)
        if any(
            token in _norm_key(fixed.get("price_note") or fixed.get("price_source"))
            for token in ("estimated", "estimate", "משוער")
        ):
            fixed["price_caveat"] = ESTIMATED_PRICE_NOTE
        safe_cards.append(fixed)
    if not safe_cards:
        patched["recommended_cars"] = []
        patched["recommendations"] = []
        patched["message"] = RECOMMENDATION_FALLBACK_TEXT
    else:
        for key in ("recommended_cars", "recommendations", "top3"):
            if key in patched:
                patched[key] = safe_cards if key != "top3" else safe_cards[:3]
    return patched


def _repair_leasing_result(result: Dict[str, Any], deterministic_calc: Any) -> Dict[str, Any]:
    patched = _safe_copy(result)
    calc = _safe_dict(deterministic_calc)
    for key in ("monthly_payment", "down_payment", "final_payment", "balloon_payment", "total_cost", "monthly_bik", "list_price_ils", "assumptions"):
        if key in calc:
            patched[key] = calc[key]
    if "frame" in calc and "assumptions" not in patched:
        patched["assumptions"] = calc["frame"]
    top3 = _safe_list(patched.get("top3"))
    candidate_map = {
        (_norm_key(item.get("make")), _norm_key(item.get("model"))): item
        for item in _safe_list(calc.get("candidates"))
        if isinstance(item, dict)
    }
    for item in top3:
        if not isinstance(item, dict):
            continue
        matched = candidate_map.get((_norm_key(item.get("make")), _norm_key(item.get("model"))))
        if matched:
            for key in ("monthly_bik", "list_price_ils"):
                if key in matched:
                    item[key] = matched[key]
        if any(token in _norm_key(item.get("reason_he")) for token in ("guaranteed", "safe", "מובטח")):
            item["reason_he"] = "לפי המידע הזמין, זו נראית אפשרות מתאימה."
        if _contains_any(item, ("residual", "future value", "ערך עתידי")):
            item["reason_he"] = f"{_text(item.get('reason_he'))} ערך עתידי הוא הערכה בלבד.".strip()
    return _soften_payload_text(patched, 20)


def _repair_service_result(result: Dict[str, Any]) -> Dict[str, Any]:
    patched = _redact_pii(_safe_copy(result))
    for key in ("raw_invoice_bytes", "invoice_image_bytes", "image_bytes"):
        patched.pop(key, None)
    sample_size = _normalize_count(
        patched.get("sample_size") or _safe_dict(patched.get("samples_meta")).get("total_cohort_n")
    )
    if sample_size is not None and sample_size < 10:
        patched["sample_size_caveat"] = LOW_SAMPLE_NOTE
    for item in _safe_list(patched.get("items") or patched.get("line_items") or patched.get("canonical_items")):
        if isinstance(item, dict) and normalize_percent(item.get("confidence")) is not None and normalize_percent(item.get("confidence")) < 50:
            item["review_status"] = "דורש בדיקה"
        if isinstance(item, dict) and not item.get("canonical_code"):
            item["canonical_code"] = "unknown_requires_review"
            item["review_status"] = "דורש בדיקה"
    if not isinstance(patched.get("report_json"), (dict, list)) and "report_json" in patched:
        patched["report_json"] = safe_json_obj(patched.get("report_json"), default={})
    return patched


def apply_feature_guardrails(
    feature_key: str, user_payload: Any, ai_result: Any, deterministic_calc: Any = None
) -> Tuple[Any, Dict[str, Any]]:
    request_id = None
    if has_app_context():
        try:
            from app.utils.http_helpers import get_request_id

            request_id = get_request_id()
        except Exception:
            request_id = None
    _increment_guardrail_counter("guardrail_validation_total")
    result = repair_or_hide_truncated_text(
        _normalize_fuel_units_in_payload(
            _soften_payload_text(_safe_copy(ai_result), _confidence_from_result(_safe_dict(ai_result)))
        ),
        feature_key,
    )
    if feature_key == "vehicle_comparison":
        report = validate_comparison_result(user_payload, result)
    elif feature_key == "reliability_analysis":
        report = validate_reliability_result(user_payload, result)
    elif feature_key == "recommendations":
        report = validate_recommendation_result(user_payload, result)
    elif feature_key == "leasing_advisor":
        report = validate_leasing_advisor_result(user_payload, result, deterministic_calc)
    elif feature_key == "service_prices":
        report = validate_service_prices_result(user_payload, result)
    elif feature_key == "invoice_scanner":
        report = validate_invoice_analysis_result(user_payload, result)
    elif feature_key == "research_collection":
        report = validate_research_submission(result or user_payload)
    elif feature_key == "dashboard_history":
        report = validate_dashboard_history_payload(result)
    else:
        report = _finalize_report(_ensure_report(feature_key))

    if feature_key == "vehicle_comparison" and isinstance(result, dict):
        result = _repair_comparison_result(user_payload, result, report)
    elif feature_key == "reliability_analysis" and isinstance(result, dict):
        result["source_type"] = _classify_source_type(result.get("source_type"))
        if report.get("warnings"):
            _ensure_caveat_list(result, safe_text_caveat(result.get("confidence"), result.get("source_type")))
        if any(key in result for key in ("score_0_100", "internal_score", "reliability_score")):
            for key in ("score_0_100", "internal_score", "reliability_score"):
                result.pop(key, None)
    elif feature_key == "recommendations" and isinstance(result, dict):
        if report.get("warnings"):
            normalized_cards = []
            for card in _find_recommendations(result):
                fixed = dict(card)
                fixed["why_it_fits"] = fixed.get("why_it_fits") or fixed.get("reason_he") or "לפי המידע הזמין, יש התאמה חלקית לצרכים שהוגדרו."
                fixed["tradeoff"] = fixed.get("tradeoff") or "דורש אימות של רמת הגימור והזמינות בפועל."
                fixed["what_to_check"] = fixed.get("what_to_check") or "בדקו זמינות, מפרט בפועל והיסטוריית טיפולים."
                fixed["confidence"] = fixed.get("confidence") or "ברמת ודאות בינונית"
                if not fixed.get("price_caveat") or any(
                    token in _norm_key(fixed.get("price_note") or fixed.get("price_source"))
                    for token in ("estimated", "estimate", "משוער")
                ):
                    fixed["price_caveat"] = ESTIMATED_PRICE_NOTE
                normalized_cards.append(fixed)
            for key in ("recommended_cars", "recommendations", "top3"):
                if key in result:
                    result[key] = normalized_cards if key != "top3" else normalized_cards[:3]
    elif feature_key == "leasing_advisor" and isinstance(result, dict):
        if "summary" in result and _contains_any(result.get("summary"), ("residual", "future value", "ערך עתידי")):
            result["summary"] = f"{_text(result.get('summary'))} הערך העתידי הוא הערכה בלבד.".strip()
    elif feature_key == "research_collection" and isinstance(result, dict):
        if result.get("notes"):
            result["notes"] = redact_pii_from_text(result.get("notes"))
        if result.get("free_text"):
            result["free_text"] = redact_pii_from_text(result.get("free_text"))
        if _normalize_count(result.get("sample_size")) is not None and _normalize_count(result.get("sample_size")) < 10:
            result["sample_size_caveat"] = LOW_SAMPLE_NOTE
    elif feature_key == "dashboard_history" and isinstance(result, dict):
        for key in ("prompt", "prompt_text", "debug", "debug_info", "internal_score"):
            result.pop(key, None)
        result = _redact_pii(result)
    elif feature_key in {"service_prices", "invoice_scanner"} and isinstance(result, dict):
        result = _repair_service_result(result)

    repaired = False
    if report.get("critical_issues"):
        _increment_guardrail_counter("guardrail_critical_total")
    if should_repair(feature_key, report) and isinstance(result, dict):
        if feature_key == "vehicle_comparison":
            result = _repair_comparison_result(user_payload, result, report)
            repaired = True
        elif feature_key == "reliability_analysis":
            result = _repair_reliability_result(result)
            repaired = True
        elif feature_key == "recommendations":
            result = _repair_recommendations_result(result, report)
            repaired = True
        elif feature_key == "leasing_advisor":
            result = _repair_leasing_result(result, deterministic_calc)
            repaired = True
        elif feature_key in {"service_prices", "invoice_scanner"}:
            result = _repair_service_result(result)
            repaired = True
        if repaired:
            _increment_guardrail_counter("guardrail_repair_total")
            _log_event(
                "ai_guardrail_repair_applied",
                feature_key,
                fields_repaired=report.get("affected_sections") or [],
                critical_count=len(report.get("critical_issues") or []),
            )
    if should_repair(feature_key, report) and not repaired:
        _increment_guardrail_counter("guardrail_fallback_total")
        result = safe_fallback_on_repair_failure(result, report)
    if report.get("critical_issues") and not report.get("safe_to_display", True):
        _increment_guardrail_counter("guardrail_blocked_total")
    if isinstance(result, dict):
        result["guardrail_meta"] = _guardrail_meta(feature_key, report, repaired)
        if feature_key == "dashboard_history" and any(
            warning in (report.get("warnings") or [])
            for warning in (
                "cached result uses old guardrail version",
                "guardrail metadata missing",
            )
        ):
            result.setdefault("legacy_notice", LEGACY_DISPLAY_NOTE)
    _log_event(
        "ai_guardrail_validation",
        feature_key,
        status=report.get("status"),
        critical_count=len(report.get("critical_issues") or []),
        warning_count=len(report.get("warnings") or []),
        repaired=repaired,
        request_id=request_id,
    )
    return result, report
