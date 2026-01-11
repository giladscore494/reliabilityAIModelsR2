# -*- coding: utf-8 -*-
"""Advisor routes blueprint."""

import json
import hashlib
from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_required, current_user

from app.extensions import db
from app.models import AdvisorHistory
from app.quota import check_and_increment_ip_rate_limit, get_client_ip, log_access_decision, PER_IP_PER_MIN_LIMIT
from app.utils.http_helpers import api_ok, api_error, is_owner_user, get_request_id
from app.utils.sanitization import sanitize_advisor_response
from app.utils.validation import validate_analyze_request, ValidationError
from app.factory import (
    fuel_map,
    gear_map,
    turbo_map,
    make_user_profile,
    sanitize_profile_for_prompt,
    car_advisor_call_gemini_with_search,
    car_advisor_postprocess,
)

bp = Blueprint('advisor', __name__)


@bp.route('/recommendations')
@login_required
def recommendations():
    advisor_owner_only = current_app.config.get('ADVISOR_OWNER_ONLY', False)
    if advisor_owner_only and not is_owner_user():
        flash("גישה למנוע ההמלצות זמינה לבעלי המערכת בלבד.", "error")
        return redirect(url_for('dashboard.dashboard'))
    user_email = getattr(current_user, "email", "") if current_user.is_authenticated else ""
    return render_template(
        'recommendations.html',
        user=current_user,
        user_email=user_email,
        is_owner=is_owner_user(),
    )


@bp.route('/advisor_api', methods=['POST'])
@login_required
def advisor_api():
    """
    מקבל profile מה-JS (recommendations.js),
    בונה user_profile מלא כמו ב-Car Advisor (Streamlit),
    קורא ל-Gemini 3 Pro, שומר היסטוריה ומחזיר JSON מוכן להצגה.
    """
    advisor_owner_only = current_app.config.get('ADVISOR_OWNER_ONLY', False)
    logger = current_app.logger
    per_ip_limit = current_app.config.get('PER_IP_PER_MIN_LIMIT', PER_IP_PER_MIN_LIMIT)

    if advisor_owner_only and not is_owner_user():
        log_access_decision('/advisor_api', getattr(current_user, "id", None), 'rejected', 'owner only')
        return api_error("forbidden", "גישה למנוע ההמלצות זמינה לבעלי המערכת בלבד.", status=403)
    # Log access decision
    user_id = current_user.id if current_user.is_authenticated else None
    log_access_decision('/advisor_api', user_id, 'allowed', 'authenticated user')

    client_ip = get_client_ip()
    ip_allowed, ip_count, ip_resets_at = check_and_increment_ip_rate_limit(client_ip, limit=per_ip_limit)
    if not ip_allowed:
        retry_after = max(0, int((ip_resets_at - datetime.utcnow()).total_seconds()))
        resp = api_error(
            "rate_limited",
            "חרגת ממגבלת הבקשות לדקה.",
            status=429,
            details={
                "limit": per_ip_limit,
                "used": ip_count,
                "remaining": max(0, per_ip_limit - ip_count),
                "resets_at": ip_resets_at.isoformat(),
            },
        )
        resp.headers["Retry-After"] = str(retry_after)
        return resp
    
    if not request.is_json:
        log_access_decision('/advisor_api', user_id, 'rejected', 'validation error: content-type')
        return api_error("invalid_content_type", "Content-Type חייב להיות application/json", status=415, details={"field": "payload"})

    try:
        payload = request.get_json(silent=False) or {}
    except Exception:
        log_access_decision('/advisor_api', user_id, 'rejected', 'validation error: invalid JSON')
        return api_error("invalid_json", "קלט JSON לא תקין", status=400, details={"field": "payload"})

    # Validate request before processing
    try:
        validated = validate_analyze_request(payload)
        payload = validated
    except ValidationError as e:
        log_access_decision('/advisor_api', user_id, 'rejected', f'validation error: {e.field}')
        return api_error("validation_error", e.message, status=400, details={"field": e.field})

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
        consider_market_supply = (consider_supply == "כן")

        fuel_price = float(payload.get("fuel_price", 7.0))
        electricity_price = float(payload.get("electricity_price", 0.65))

    except Exception:
        logger.exception("[ADVISOR] payload parse failed request_id=%s", get_request_id())
        log_access_decision('/advisor_api', user_id, 'rejected', 'validation error: invalid payload')
        return api_error("validation_error", "שגיאת קלט: נא לוודא שכל הנתונים הוזנו כראוי.", status=400, details={"field": "payload"})

    # --- מיפוי דלק/גיר/טורבו מהעברית לערכים לוגיים ---
    fuels = [fuel_map.get(f, "gasoline") for f in fuels_he] if fuels_he else ["gasoline"]

    if "חשמלי" in fuels_he:
        gears = ["automatic"]
    else:
        gears = [gear_map.get(g, "automatic") for g in gears_he] if gears_he else ["automatic"]

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

    profile_for_storage = sanitize_profile_for_prompt(user_profile)
    parsed = car_advisor_call_gemini_with_search(user_profile)
    if parsed.get("_error"):
        log_access_decision('/advisor_api', user_id, 'error', f'AI error: {parsed.get("_error")}')
        return api_error("advisor_ai_error", "שגיאת AI במנוע ההמלצות. נסה שוב מאוחר יותר.", status=502)

    result = car_advisor_postprocess(user_profile, parsed)
    sanitized_result = sanitize_advisor_response(result)

    # 🔴 שמירת היסטוריית המלצות למאגר
    try:
        rec_log = AdvisorHistory(
            user_id=current_user.id,
            profile_json=json.dumps(profile_for_storage, ensure_ascii=False),
            result_json=json.dumps(sanitized_result, ensure_ascii=False),
        )
        db.session.add(rec_log)
        db.session.commit()
    except Exception as e:
        print(f"[DB] ⚠️ failed to save advisor history: {e}")
        db.session.rollback()

    return api_ok(sanitized_result)
