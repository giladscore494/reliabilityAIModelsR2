import hashlib
import json
import os
import secrets
from typing import Any

from app.utils.validation import ValidationError

RESEARCH_CONSENT_TYPE = os.environ.get("RESEARCH_CONSENT_TYPE", "research_questions")
RESEARCH_NOTICE_VERSION = os.environ.get("RESEARCH_NOTICE_VERSION", "2026-04-03")
RESEARCH_QUESTION_VERSION = os.environ.get("RESEARCH_QUESTION_VERSION", "2026-04-03")
RESEARCH_CONSENT_VERSION = "2026-04-25"  # Alias for new refactor, backward-compatible

RESEARCH_FLOW_TYPES = {"reliability", "compare", "advisor", "owner_profile"}
OWNER_PROFILE_FLOW = "owner_profile"

_ENUMS = {
    "reliability.ownership_status": {"owner", "pre_purchase_research"},
    "reliability.garage_type": {"authorized", "independent", "both"},
    "compare.subject_vehicle_slot": {"car_1", "car_2", "car_3", "unknown"},
    "advisor.sale_timeline_bucket": {
        "under_14_days",
        "14_to_30_days",
        "31_to_60_days",
        "over_60_days",
        "not_sold",
    },
    "advisor.ask_to_sale_gap_bucket": {
        "under_5_pct",
        "5_to_10_pct",
        "10_to_15_pct",
        "over_15_pct",
        "not_sold",
    },
    "advisor.purchase_reference_type": {"price_list", "published_ad"},
    "advisor.purchase_delta_bucket": {
        "below_5_pct",
        "within_5_pct",
        "5_to_10_pct",
        "over_10_pct",
        "unknown",
    },
    "advisor.charging_location": {"home", "work", "public", "mixed"},
    "owner_profile.mileage_bucket": {
        "0-50000",
        "50000-100000",
        "100000-150000",
        "150000-200000",
        "200000+",
    },
    "owner_profile.ownership_duration_bucket": {
        "0-1_years",
        "1-3_years",
        "3-5_years",
        "5-10_years",
        "10+_years",
    },
    "owner_profile.annual_km_bucket": {
        "0-10000",
        "10000-15000",
        "15000-20000",
        "20000-30000",
        "30000+",
    },
    "owner_profile.fuel_consumption_bucket": {
        "very_low",
        "low",
        "average",
        "high",
        "very_high",
    },
}


def generate_anon_id() -> str:
    raw = secrets.token_urlsafe(32)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def ensure_anon_id(session_obj) -> str:
    anon_id = (session_obj.get("anon_id") or "").strip()
    if anon_id:
        return anon_id
    anon_id = generate_anon_id()
    session_obj["anon_id"] = anon_id
    return anon_id


def _require_bool(field: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    raise ValidationError(field, "Field must be true or false")


def _require_int(field: str, value: Any, *, min_value: int, max_value: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValidationError(field, "Field must be a whole number")
    if number < min_value or number > max_value:
        raise ValidationError(
            field, f"Field must be between {min_value} and {max_value}"
        )
    return number


def _require_float(
    field: str, value: Any, *, min_value: float, max_value: float
) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValidationError(field, "Field must be a number")
    if number < min_value or number > max_value:
        raise ValidationError(
            field, f"Field must be between {min_value} and {max_value}"
        )
    return round(number, 2)


def _require_enum(field: str, value: Any, allowed: set[str]) -> str:
    if not isinstance(value, str):
        raise ValidationError(field, "Field must be a string")
    normalized = value.strip()
    if normalized not in allowed:
        raise ValidationError(
            field, f"Field must be one of: {', '.join(sorted(allowed))}"
        )
    return normalized


def _require_text(field: str, value: Any, *, max_length: int) -> str:
    if value is None:
        raise ValidationError(field, "Field is required")
    text = " ".join(str(value).split()).strip()
    if not text:
        raise ValidationError(field, "Field is required")
    if len(text) > max_length:
        raise ValidationError(field, f"Field must be at most {max_length} characters")
    return text


def _vehicle_context_json(vehicle_context: Any) -> str:
    if vehicle_context is None:
        return json.dumps({}, ensure_ascii=False)
    if isinstance(vehicle_context, str):
        try:
            json.loads(vehicle_context)
            return vehicle_context
        except json.JSONDecodeError as exc:
            raise ValidationError(
                "vehicle_context", "vehicle_context must be valid JSON"
            ) from exc
    if not isinstance(vehicle_context, dict):
        raise ValidationError("vehicle_context", "vehicle_context must be an object")
    return json.dumps(vehicle_context, ensure_ascii=False)


def normalize_vehicle_context(vehicle_context: Any) -> tuple[dict[str, Any], str]:
    context_json = _vehicle_context_json(vehicle_context)
    return json.loads(context_json), context_json


def charging_question_required(vehicle_context: dict[str, Any]) -> bool:
    preferred_fuels = vehicle_context.get("preferred_fuels") or []
    if isinstance(preferred_fuels, str):
        preferred_fuels = [preferred_fuels]
    normalized = " ".join(str(item).lower() for item in preferred_fuels)
    return any(
        token in normalized
        for token in ("חשמ", "היבריד", "electric", "hybrid", "phev", "ev")
    )


def validate_research_payload(
    flow_type: str, responses: Any, vehicle_context: Any
) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
    normalized_flow = (flow_type or "").strip().lower()
    if normalized_flow not in RESEARCH_FLOW_TYPES:
        raise ValidationError("flow_type", "Unsupported research flow")
    if not isinstance(responses, list) or not responses:
        raise ValidationError("responses", "responses must be a non-empty list")

    context_obj, context_json = normalize_vehicle_context(vehicle_context)
    response_map: dict[str, dict[str, Any]] = {}
    for item in responses:
        if not isinstance(item, dict):
            raise ValidationError("responses", "Each response must be an object")
        question_code = (item.get("question_code") or "").strip()
        if not question_code:
            raise ValidationError("question_code", "question_code is required")
        if question_code in response_map:
            raise ValidationError(
                "question_code", f"Duplicate question_code: {question_code}"
            )
        response_map[question_code] = item

    if normalized_flow == "reliability":
        validated = _validate_reliability(response_map)
    elif normalized_flow == "compare":
        validated = _validate_compare(response_map)
    else:
        validated = _validate_advisor(response_map, context_obj)

    return context_obj, context_json, validated


def _validate_reliability(responses: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    required_codes = {
        "ownership_status",
        "maintenance_profile",
        "first_test_pass",
        "out_of_warranty_repairs",
    }
    missing = required_codes - set(responses.keys())
    if missing:
        raise ValidationError(
            "responses",
            f"Missing required research answers: {', '.join(sorted(missing))}",
        )

    ownership_value = responses["ownership_status"].get("response")
    ownership_status = _require_enum(
        "ownership_status",
        (ownership_value or {}).get("ownership_status"),
        _ENUMS["reliability.ownership_status"],
    )

    maintenance_value = responses["maintenance_profile"].get("response") or {}
    garage_type = _require_enum(
        "maintenance_profile.garage_type",
        maintenance_value.get("garage_type"),
        _ENUMS["reliability.garage_type"],
    )
    last_service_cost_ils = _require_int(
        "maintenance_profile.last_service_cost_ils",
        maintenance_value.get("last_service_cost_ils"),
        min_value=0,
        max_value=100000,
    )

    first_test_pass = _require_bool(
        "first_test_pass",
        (responses["first_test_pass"].get("response") or {}).get("first_test_pass"),
    )
    out_of_warranty_repairs = _require_bool(
        "out_of_warranty_repairs",
        (responses["out_of_warranty_repairs"].get("response") or {}).get(
            "out_of_warranty_repairs"
        ),
    )

    return [
        {
            "question_code": "ownership_status",
            "response_json": json.dumps(
                {"ownership_status": ownership_status}, ensure_ascii=False
            ),
            "is_required": True,
        },
        {
            "question_code": "maintenance_profile",
            "response_json": json.dumps(
                {
                    "garage_type": garage_type,
                    "last_service_cost_ils": last_service_cost_ils,
                },
                ensure_ascii=False,
            ),
            "is_required": True,
        },
        {
            "question_code": "first_test_pass",
            "response_json": json.dumps(
                {"first_test_pass": first_test_pass}, ensure_ascii=False
            ),
            "is_required": True,
        },
        {
            "question_code": "out_of_warranty_repairs",
            "response_json": json.dumps(
                {"out_of_warranty_repairs": out_of_warranty_repairs}, ensure_ascii=False
            ),
            "is_required": True,
        },
    ]


def _validate_compare(responses: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    required_codes = {
        "subject_vehicle",
        "annual_insurance",
        "annual_total_cost",
        "owner_satisfaction",
    }
    missing = required_codes - set(responses.keys())
    if missing:
        raise ValidationError(
            "responses",
            f"Missing required research answers: {', '.join(sorted(missing))}",
        )

    subject_vehicle = _require_enum(
        "subject_vehicle.subject_vehicle_slot",
        (responses["subject_vehicle"].get("response") or {}).get(
            "subject_vehicle_slot"
        ),
        _ENUMS["compare.subject_vehicle_slot"],
    )
    annual_insurance_ils = _require_int(
        "annual_insurance.annual_insurance_ils",
        (responses["annual_insurance"].get("response") or {}).get(
            "annual_insurance_ils"
        ),
        min_value=0,
        max_value=50000,
    )
    annual_total_cost_ils = _require_int(
        "annual_total_cost.annual_total_cost_ils",
        (responses["annual_total_cost"].get("response") or {}).get(
            "annual_total_cost_ils"
        ),
        min_value=0,
        max_value=200000,
    )
    owner_satisfaction = responses["owner_satisfaction"].get("response") or {}
    satisfaction_score = _require_int(
        "owner_satisfaction.satisfaction_score",
        owner_satisfaction.get("satisfaction_score"),
        min_value=1,
        max_value=10,
    )
    would_buy_again = _require_bool(
        "owner_satisfaction.would_buy_again",
        owner_satisfaction.get("would_buy_again"),
    )

    return [
        {
            "question_code": "subject_vehicle",
            "response_json": json.dumps(
                {"subject_vehicle_slot": subject_vehicle}, ensure_ascii=False
            ),
            "is_required": True,
        },
        {
            "question_code": "annual_insurance",
            "response_json": json.dumps(
                {"annual_insurance_ils": annual_insurance_ils}, ensure_ascii=False
            ),
            "is_required": True,
        },
        {
            "question_code": "annual_total_cost",
            "response_json": json.dumps(
                {"annual_total_cost_ils": annual_total_cost_ils}, ensure_ascii=False
            ),
            "is_required": True,
        },
        {
            "question_code": "owner_satisfaction",
            "response_json": json.dumps(
                {
                    "satisfaction_score": satisfaction_score,
                    "would_buy_again": would_buy_again,
                },
                ensure_ascii=False,
            ),
            "is_required": True,
        },
    ]


def _validate_advisor(
    responses: dict[str, dict[str, Any]],
    vehicle_context: dict[str, Any],
) -> list[dict[str, Any]]:
    required_codes = {
        "current_vehicle",
        "sale_experience",
        "purchase_reference",
        "actual_fuel_consumption",
    }
    if charging_question_required(vehicle_context):
        required_codes.add("charging_profile")
    missing = required_codes - set(responses.keys())
    if missing:
        raise ValidationError(
            "responses",
            f"Missing required research answers: {', '.join(sorted(missing))}",
        )

    current_vehicle = _require_text(
        "current_vehicle.current_vehicle",
        (responses["current_vehicle"].get("response") or {}).get("current_vehicle"),
        max_length=120,
    )

    sale_experience = responses["sale_experience"].get("response") or {}
    sale_timeline_bucket = _require_enum(
        "sale_experience.sale_timeline_bucket",
        sale_experience.get("sale_timeline_bucket"),
        _ENUMS["advisor.sale_timeline_bucket"],
    )
    ask_to_sale_gap_bucket = _require_enum(
        "sale_experience.ask_to_sale_gap_bucket",
        sale_experience.get("ask_to_sale_gap_bucket"),
        _ENUMS["advisor.ask_to_sale_gap_bucket"],
    )

    purchase_reference = responses["purchase_reference"].get("response") or {}
    purchase_reference_type = _require_enum(
        "purchase_reference.purchase_reference_type",
        purchase_reference.get("purchase_reference_type"),
        _ENUMS["advisor.purchase_reference_type"],
    )
    purchase_delta_bucket = _require_enum(
        "purchase_reference.purchase_delta_bucket",
        purchase_reference.get("purchase_delta_bucket"),
        _ENUMS["advisor.purchase_delta_bucket"],
    )

    actual_fuel_consumption = _require_float(
        "actual_fuel_consumption.actual_consumption",
        (responses["actual_fuel_consumption"].get("response") or {}).get(
            "actual_consumption"
        ),
        min_value=0,
        max_value=50,
    )

    validated = [
        {
            "question_code": "current_vehicle",
            "response_json": json.dumps(
                {"current_vehicle": current_vehicle}, ensure_ascii=False
            ),
            "is_required": True,
        },
        {
            "question_code": "sale_experience",
            "response_json": json.dumps(
                {
                    "sale_timeline_bucket": sale_timeline_bucket,
                    "ask_to_sale_gap_bucket": ask_to_sale_gap_bucket,
                },
                ensure_ascii=False,
            ),
            "is_required": True,
        },
        {
            "question_code": "purchase_reference",
            "response_json": json.dumps(
                {
                    "purchase_reference_type": purchase_reference_type,
                    "purchase_delta_bucket": purchase_delta_bucket,
                },
                ensure_ascii=False,
            ),
            "is_required": True,
        },
        {
            "question_code": "actual_fuel_consumption",
            "response_json": json.dumps(
                {"actual_consumption": actual_fuel_consumption}, ensure_ascii=False
            ),
            "is_required": True,
        },
    ]

    if charging_question_required(vehicle_context):
        charging_profile = responses["charging_profile"].get("response") or {}
        charging_cost = _require_float(
            "charging_profile.charging_cost_ils_per_kwh",
            charging_profile.get("charging_cost_ils_per_kwh"),
            min_value=0,
            max_value=10,
        )
        charging_location = _require_enum(
            "charging_profile.charging_location",
            charging_profile.get("charging_location"),
            _ENUMS["advisor.charging_location"],
        )
        validated.append(
            {
                "question_code": "charging_profile",
                "response_json": json.dumps(
                    {
                        "charging_cost_ils_per_kwh": charging_cost,
                        "charging_location": charging_location,
                    },
                    ensure_ascii=False,
                ),
                "is_required": True,
            }
        )

    return validated
