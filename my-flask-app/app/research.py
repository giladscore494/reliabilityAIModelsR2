import hashlib
import json
import os
import secrets
from typing import Any

from app.utils.validation import ValidationError

RESEARCH_CONSENT_TYPE = os.environ.get("RESEARCH_CONSENT_TYPE", "research_questions")
RESEARCH_NOTICE_VERSION = os.environ.get("RESEARCH_NOTICE_VERSION", "2026-04-03")
RESEARCH_QUESTION_VERSION = os.environ.get(
    "RESEARCH_QUESTION_VERSION", "after_value_v2_2026_04_25"
)
RESEARCH_CONSENT_VERSION = "2026-04-25"  # Alias for new refactor, backward-compatible

RESEARCH_FLOW_TYPES = {"reliability", "compare", "advisor", "owner_profile"}
OWNER_PROFILE_FLOW = "owner_profile"

# Source of truth for which fields are required for the core service versus optional
# product enrichment/research. This should prevent research fields from drifting back
# into pre-result service forms.
FIELD_CLASSIFICATION = {
    "make": "service_required",
    "model": "service_required",
    "year": "service_required",
    "mileage": "service_required",
    "fuel_type": "service_required",
    "transmission": "service_required",
    "budget_min": "service_required",
    "budget_max": "service_required",
    "main_use": "service_required",
    "annual_km": "service_required",
    "family_size": "service_required",
    "cargo_need": "service_optional",
    "current_vehicle": "research_optional",
    "ownership_duration": "research_optional",
    "mileage_bucket": "research_optional",
    "had_major_faults": "research_optional",
    "major_fault_type": "research_optional",
    "maintenance_cost_bucket": "research_optional",
    "actual_fuel_consumption": "research_optional",
    "satisfaction_score": "research_optional",
    "would_buy_again": "research_optional",
}

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
    "advisor.ownership_duration": {
        "less_than_6_months",
        "6_12_months",
        "1_2_years",
        "2_4_years",
        "4_plus_years",
    },
    "advisor.mileage_bucket": {
        "0-50k",
        "50k-100k",
        "100k-150k",
        "150k-200k",
        "200k+",
        "unknown",
    },
    "advisor.major_fault_type": {
        "engine",
        "gearbox",
        "hybrid_battery",
        "electrical",
        "suspension",
        "ac",
        "other",
    },
    "advisor.maintenance_cost_bucket": {
        "0-2000",
        "2000-4000",
        "4000-7000",
        "7000-10000",
        "10000+",
        "unknown",
    },
    "advisor.would_buy_again": {"yes", "no", "not_sure"},
    "owner_profile.mileage_bucket": {
        "0-50k",
        "50k-100k",
        "100k-150k",
        "150k-200k",
        "200k+",
        "unknown",
    },
    "owner_profile.ownership_duration_bucket": {
        "less_than_6_months",
        "6_12_months",
        "1_2_years",
        "2_4_years",
        "4_plus_years",
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


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(_has_value(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_has_value(item) for item in value)
    return True


def _response_object(question_code: str, item: dict[str, Any]) -> dict[str, Any] | None:
    response = item.get("response")
    if response is None:
        return None
    if not isinstance(response, dict):
        raise ValidationError(question_code, "response must be an object")
    if not _has_value(response):
        return None
    return response


def _optional_question(
    *,
    question_code: str,
    response_json: dict[str, Any],
    answer_type: str,
) -> dict[str, Any]:
    return {
        "question_code": question_code,
        "response_json": json.dumps(response_json, ensure_ascii=False),
        "is_required": False,
        "answer_type": answer_type,
    }


def _validate_optional_questions(
    responses: dict[str, dict[str, Any]],
    validators: dict[str, Any],
) -> list[dict[str, Any]]:
    unknown_codes = sorted(set(responses.keys()) - set(validators.keys()))
    if unknown_codes:
        raise ValidationError(
            "question_code",
            f"Unknown research question_code: {', '.join(unknown_codes)}",
        )

    validated: list[dict[str, Any]] = []
    for question_code, item in responses.items():
        response = _response_object(question_code, item)
        if response is None:
            continue
        validated.append(validators[question_code](response))

    if not validated:
        raise ValidationError(
            "responses", "At least one valid research answer is required"
        )
    return validated


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
    return _validate_optional_questions(
        responses,
        {
            "ownership_status": lambda response: _optional_question(
                question_code="ownership_status",
                response_json={
                    "ownership_status": _require_enum(
                        "ownership_status.ownership_status",
                        response.get("ownership_status"),
                        _ENUMS["reliability.ownership_status"],
                    )
                },
                answer_type="bucket",
            ),
            "maintenance_profile": lambda response: _optional_question(
                question_code="maintenance_profile",
                response_json={
                    "garage_type": _require_enum(
                        "maintenance_profile.garage_type",
                        response.get("garage_type"),
                        _ENUMS["reliability.garage_type"],
                    ),
                    "last_service_cost_ils": _require_int(
                        "maintenance_profile.last_service_cost_ils",
                        response.get("last_service_cost_ils"),
                        min_value=0,
                        max_value=100000,
                    ),
                },
                answer_type="number",
            ),
            "first_test_pass": lambda response: _optional_question(
                question_code="first_test_pass",
                response_json={
                    "first_test_pass": _require_bool(
                        "first_test_pass.first_test_pass",
                        response.get("first_test_pass"),
                    )
                },
                answer_type="boolean",
            ),
            "out_of_warranty_repairs": lambda response: _optional_question(
                question_code="out_of_warranty_repairs",
                response_json={
                    "out_of_warranty_repairs": _require_bool(
                        "out_of_warranty_repairs.out_of_warranty_repairs",
                        response.get("out_of_warranty_repairs"),
                    )
                },
                answer_type="boolean",
            ),
        },
    )


def _validate_compare(responses: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return _validate_optional_questions(
        responses,
        {
            "subject_vehicle": lambda response: _optional_question(
                question_code="subject_vehicle",
                response_json={
                    "subject_vehicle_slot": _require_enum(
                        "subject_vehicle.subject_vehicle_slot",
                        response.get("subject_vehicle_slot"),
                        _ENUMS["compare.subject_vehicle_slot"],
                    )
                },
                answer_type="bucket",
            ),
            "annual_insurance": lambda response: _optional_question(
                question_code="annual_insurance",
                response_json={
                    "annual_insurance_ils": _require_int(
                        "annual_insurance.annual_insurance_ils",
                        response.get("annual_insurance_ils"),
                        min_value=0,
                        max_value=50000,
                    )
                },
                answer_type="number",
            ),
            "annual_total_cost": lambda response: _optional_question(
                question_code="annual_total_cost",
                response_json={
                    "annual_total_cost_ils": _require_int(
                        "annual_total_cost.annual_total_cost_ils",
                        response.get("annual_total_cost_ils"),
                        min_value=0,
                        max_value=200000,
                    )
                },
                answer_type="number",
            ),
            "owner_satisfaction": lambda response: _optional_question(
                question_code="owner_satisfaction",
                response_json={
                    "satisfaction_score": _require_int(
                        "owner_satisfaction.satisfaction_score",
                        response.get("satisfaction_score"),
                        min_value=1,
                        max_value=10,
                    ),
                    "would_buy_again": _require_bool(
                        "owner_satisfaction.would_buy_again",
                        response.get("would_buy_again"),
                    ),
                },
                answer_type="rating",
            ),
        },
    )


def _validate_advisor(
    responses: dict[str, dict[str, Any]],
    vehicle_context: dict[str, Any],
) -> list[dict[str, Any]]:
    validators = {
        "current_vehicle": lambda response: _optional_question(
            question_code="current_vehicle",
            response_json={
                "current_vehicle": _require_text(
                    "current_vehicle.current_vehicle",
                    response.get("current_vehicle"),
                    max_length=120,
                )
            },
            answer_type="text",
        ),
        "ownership_duration": lambda response: _optional_question(
            question_code="ownership_duration",
            response_json={
                "ownership_duration": _require_enum(
                    "ownership_duration.ownership_duration",
                    response.get("ownership_duration"),
                    _ENUMS["advisor.ownership_duration"],
                )
            },
            answer_type="bucket",
        ),
        "mileage_bucket": lambda response: _optional_question(
            question_code="mileage_bucket",
            response_json={
                "mileage_bucket": _require_enum(
                    "mileage_bucket.mileage_bucket",
                    response.get("mileage_bucket"),
                    _ENUMS["advisor.mileage_bucket"],
                )
            },
            answer_type="bucket",
        ),
        "had_major_faults": lambda response: _optional_question(
            question_code="had_major_faults",
            response_json={
                "had_major_faults": _require_bool(
                    "had_major_faults.had_major_faults",
                    response.get("had_major_faults"),
                )
            },
            answer_type="boolean",
        ),
        "major_fault_type": lambda response: _optional_question(
            question_code="major_fault_type",
            response_json={
                "major_fault_type": _require_enum(
                    "major_fault_type.major_fault_type",
                    response.get("major_fault_type"),
                    _ENUMS["advisor.major_fault_type"],
                )
            },
            answer_type="bucket",
        ),
        "maintenance_cost_bucket": lambda response: _optional_question(
            question_code="maintenance_cost_bucket",
            response_json={
                "maintenance_cost_bucket": _require_enum(
                    "maintenance_cost_bucket.maintenance_cost_bucket",
                    response.get("maintenance_cost_bucket"),
                    _ENUMS["advisor.maintenance_cost_bucket"],
                )
            },
            answer_type="bucket",
        ),
        "actual_fuel_consumption": lambda response: _optional_question(
            question_code="actual_fuel_consumption",
            response_json={
                "actual_consumption": _require_float(
                    "actual_fuel_consumption.actual_consumption",
                    response.get("actual_consumption"),
                    min_value=0,
                    max_value=50,
                )
            },
            answer_type="number",
        ),
        "satisfaction_score": lambda response: _optional_question(
            question_code="satisfaction_score",
            response_json={
                "satisfaction_score": _require_int(
                    "satisfaction_score.satisfaction_score",
                    response.get("satisfaction_score"),
                    min_value=1,
                    max_value=5,
                )
            },
            answer_type="rating",
        ),
        "would_buy_again": lambda response: _optional_question(
            question_code="would_buy_again",
            response_json={
                "would_buy_again": _require_enum(
                    "would_buy_again.would_buy_again",
                    response.get("would_buy_again"),
                    _ENUMS["advisor.would_buy_again"],
                )
            },
            answer_type="bucket",
        ),
    }

    if charging_question_required(vehicle_context):
        validators["charging_profile"] = lambda response: _optional_question(
            question_code="charging_profile",
            response_json={
                "charging_cost_ils_per_kwh": _require_float(
                    "charging_profile.charging_cost_ils_per_kwh",
                    response.get("charging_cost_ils_per_kwh"),
                    min_value=0,
                    max_value=10,
                ),
                "charging_location": _require_enum(
                    "charging_profile.charging_location",
                    response.get("charging_location"),
                    _ENUMS["advisor.charging_location"],
                ),
            },
            answer_type="number",
        )

    return _validate_optional_questions(responses, validators)
