# -*- coding: utf-8 -*-
"""Advisor service logic."""

import json
import logging
import time

from flask import current_app

from app.extensions import db
from app.models import AdvisorHistory
from app.quota import log_access_decision
from app.utils.http_helpers import api_ok, api_error, get_request_id
from app.utils.sanitization import sanitize_advisor_response
from app.factory import (
    fuel_map,
    gear_map,
    turbo_map,
    make_user_profile,
    sanitize_profile_for_prompt,
    car_advisor_call_gemini_with_search,
    car_advisor_postprocess,
)

logger = logging.getLogger(__name__)


def handle_advisor_logic(payload, user, user_id):
    """
    Process advisor payload and return Flask response.
    """
    logger = current_app.logger
    model_duration_ms = 0
    try:
        # ---- שלב 1: בסיסי ----
        budget_min = float(payload.get("budget_min", 0))
        budget_max = float(payload.get("budget_max", 0))
        year_min = int(payload.get("year_min", 2000))
        year_max = int(payload.get("year_max", 2025))

        fuels_he = payload.get("fuels_he") or []
        gears_he = payload.get("gears_he") or []
        turbo_choice_he = payload.get("turbo_choice_he", "לא משנה")

        # ---- שלב 2: שימוש וסגנון ----
        main_use = (payload.get("main_use") or "").strip()
        annual_km = int(payload.get("annual_km", 15000))
        driver_age = int(payload.get("driver_age", 21))

        license_years = int(payload.get("license_years", 0))
        driver_gender = payload.get("driver_gender", "זכר") or "זכר"

        body_style = payload.get("body_style", "כללי") or "כללי"
        driving_style = payload.get("driving_style", "רגוע ונינוח") or "רגוע ונינוח"
        seats_choice = payload.get("seats_choice", "5") or "5"

        excluded_colors = payload.get("excluded_colors") or []
        if isinstance(excluded_colors, str):
            excluded_colors = [
                s.strip() for s in excluded_colors.split(",") if s.strip()
            ]

        # ---- שלב 3: סדר עדיפויות ----
        weights = payload.get("weights") or {
            "reliability": 5,
            "resale": 3,
            "fuel": 4,
            "performance": 2,
            "comfort": 3,
        }

        # ---- שלב 4: פרטים נוספים ----
        insurance_history = payload.get("insurance_history", "") or ""
        violations = payload.get("violations", "אין") or "אין"

        family_size = payload.get("family_size", "1-2") or "1-2"
        cargo_need = payload.get("cargo_need", "בינוני") or "בינוני"

        safety_required = payload.get("safety_required")
        if not safety_required:
            safety_required = payload.get("safety_required_radio", "כן")
        if not safety_required:
            safety_required = "כן"

        trim_level = payload.get("trim_level", "סטנדרטי") or "סטנדרטי"

        consider_supply = payload.get("consider_supply", "כן") or "כן"
        consider_market_supply = consider_supply == "כן"

        fuel_price = float(payload.get("fuel_price", 7.0))
        electricity_price = float(payload.get("electricity_price", 0.65))

    except Exception:
        logger.exception(
            "[ADVISOR] payload parse failed request_id=%s", get_request_id()
        )
        log_access_decision(
            "/advisor_api", user_id, "rejected", "validation error: invalid payload"
        )
        return api_error(
            "validation_error",
            "שגיאת קלט: נא לוודא שכל הנתונים הוזנו כראוי.",
            status=400,
            details={"field": "payload"},
        )

    # --- מיפוי דלק/גיר/טורבו מהעברית לערכים לוגיים ---
    fuels = (
        [fuel_map.get(f, "gasoline") for f in fuels_he] if fuels_he else ["gasoline"]
    )

    if "חשמלי" in fuels_he:
        gears = ["automatic"]
    else:
        gears = (
            [gear_map.get(g, "automatic") for g in gears_he]
            if gears_he
            else ["automatic"]
        )

    turbo_choice = turbo_map.get(turbo_choice_he, "any")

    # --- בניית user_profile כמו ב-Car Advisor (Streamlit) ---
    user_profile = make_user_profile(
        budget_min,
        budget_max,
        [year_min, year_max],
        fuels,
        gears,
        turbo_choice,
        main_use,
        annual_km,
        driver_age,
        family_size,
        cargo_need,
        safety_required,
        trim_level,
        weights,
        body_style,
        driving_style,
        excluded_colors,
    )

    # שדות נוספים
    user_profile["license_years"] = license_years
    user_profile["driver_gender"] = driver_gender
    user_profile["insurance_history"] = insurance_history
    user_profile["violations"] = violations
    user_profile["consider_market_supply"] = consider_market_supply
    user_profile["fuel_price_nis_per_liter"] = fuel_price
    user_profile["electricity_price_nis_per_kwh"] = electricity_price
    user_profile["seats"] = seats_choice
    user_profile["market_research_context"] = {
        "current_vehicle": payload.get("research_current_vehicle") or "",
        "actual_consumption": payload.get("research_actual_consumption") or "",
        "sale_timeline_bucket": payload.get("research_sale_timeline") or "",
        "ask_to_sale_gap_bucket": payload.get("research_sale_gap") or "",
        "purchase_reference_type": payload.get("research_purchase_reference_type")
        or "",
        "purchase_delta_bucket": payload.get("research_purchase_delta_bucket") or "",
        "charging_cost_ils_per_kwh": payload.get("research_charging_cost") or "",
        "charging_location": payload.get("research_charging_location") or "",
    }

    profile_for_storage = sanitize_profile_for_prompt(user_profile)
    start_time = time.perf_counter()
    parsed = car_advisor_call_gemini_with_search(user_profile)
    model_duration_ms = int((time.perf_counter() - start_time) * 1000)
    if parsed.get("_error"):
        error_reason = parsed.get("_error")
        log_access_decision(
            "/advisor_api", user_id, "error", f"AI error: {error_reason}"
        )
        if error_reason == "CALL_TIMEOUT":
            return api_error(
                "advisor_timeout",
                "זמן העיבוד חרג מהמותר. נסה שוב מאוחר יותר.",
                status=504,
            )
        return api_error(
            "advisor_ai_error",
            "שגיאת AI במנוע ההמלצות. נסה שוב מאוחר יותר.",
            status=502,
        )

    result = car_advisor_postprocess(user_profile, parsed)
    sanitized_result = sanitize_advisor_response(result)
    history_id = None

    # 🔴 שמירת היסטוריית המלצות למאגר
    try:
        rec_log = AdvisorHistory(
            user_id=user.id,
            profile_json=json.dumps(profile_for_storage, ensure_ascii=False),
            result_json=json.dumps(sanitized_result, ensure_ascii=False),
            duration_ms=model_duration_ms,
        )
        db.session.add(rec_log)
        db.session.commit()
        history_id = rec_log.id
    except Exception as e:
        logger.warning("[DB] failed to save advisor history: %s", e)
        db.session.rollback()

    response_payload = dict(sanitized_result)
    response_payload["history_id"] = history_id
    return api_ok(response_payload)
