# -*- coding: utf-8 -*-
"""Car advisor AI prompt, model call, and post-processing helpers."""

import json
import logging
import os
import time as pytime

from google.genai import types as genai_types

import app.extensions as extensions
from app.config import AI_CALL_TIMEOUT_SEC
from app.extensions import GEMINI_RECOMMENDER_MODEL_ID
from app.services.reliability_model_service import _execute_with_timeout
from app.services.gemini_grounding_client import call_grounded_model, GROUNDING_FAILED_CODE
from app.utils.prompt_defense import (
    create_data_only_instruction,
    escape_prompt_input,
    wrap_user_input_in_boundary,
)

logger = logging.getLogger(__name__)


fuel_map = {
    "בנזין": "gasoline",
    "היברידי": "hybrid",
    "דיזל היברידי": "hybrid-diesel",
    "דיזל": "diesel",
    "חשמלי": "electric",
}


gear_map = {
    "אוטומטית": "automatic",
    "ידנית": "manual",
}


turbo_map = {
    "לא משנה": "any",
    "כן": "yes",
    "לא": "no",
}


fuel_map_he = {v: k for k, v in fuel_map.items()}


gear_map_he = {v: k for k, v in gear_map.items()}


turbo_map_he = {
    "yes": "כן",
    "no": "לא",
    "any": "לא משנה",
    True: "כן",
    False: "לא",
}


def make_user_profile(
    budget_min,
    budget_max,
    years_range,
    fuels,
    gears,
    turbo_required,
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
):
    return {
        "budget_nis": [float(budget_min), float(budget_max)],
        "years": [int(years_range[0]), int(years_range[1])],
        "fuel": [f.lower() for f in fuels],
        "gear": [g.lower() for g in gears],
        "turbo_required": None if turbo_required == "any" else (turbo_required == "yes"),
        "main_use": main_use.strip(),
        "annual_km": int(annual_km),
        "driver_age": int(driver_age),
        "family_size": family_size,
        "cargo_need": cargo_need,
        "safety_required": safety_required,
        "trim_level": trim_level,
        "weights": weights,
        "body_style": body_style,
        "driving_style": driving_style,
        "excluded_colors": excluded_colors,
    }


def sanitize_profile_for_prompt(profile: dict) -> dict:
    """Recursively escape user profile fields before prompt construction."""
    if isinstance(profile, dict):
        return {k: sanitize_profile_for_prompt(v) for k, v in profile.items()}
    if isinstance(profile, list):
        return [sanitize_profile_for_prompt(v) for v in profile]
    if isinstance(profile, str):
        return escape_prompt_input(profile, max_length=300)
    return profile


def car_advisor_call_gemini_with_search(profile: dict) -> dict:
    """
    קריאה ל-Gemini 3 Pro (SDK החדש) עם Google Search ו-output כ-JSON בלבד.
    """
    start_time = pytime.perf_counter()
    try:
        if extensions.advisor_client is None:
            return {"_error": "Gemini Car Advisor client unavailable."}

        sanitized_profile = sanitize_profile_for_prompt(profile)
        bounded_profile = wrap_user_input_in_boundary(
            json.dumps(sanitized_profile, ensure_ascii=False, indent=2),
            boundary_tag="user_input"
        )
        data_instruction = create_data_only_instruction()

        prompt = f"""
{data_instruction}

Please recommend cars for an Israeli customer. Here is the user profile (JSON wrapped in <user_input>):
{bounded_profile}

You are an independent automotive data analyst for the **Israeli used car market**.
Your job is to rank cars by the customer's stated preferences and taste, not to override or re-educate the customer.

🔴 CRITICAL INSTRUCTION: USE GOOGLE SEARCH TOOL
You MUST use the Google Search tool to verify:
- that the specific model and trim are actually sold in Israel
- realistic used prices in Israel (NIS)
- realistic fuel/energy consumption values
- common issues (DSG, reliability, recalls)
- official safety ratings from official safety organizations where available
- Israeli trim levels, license fee, warranty, and competitors from grounded sources

Hard constraints:
- Return only ONE top-level JSON object.
- JSON fields: "search_performed", "search_queries", "recommended_cars".
- search_performed: ALWAYS true (boolean).
- search_queries: array of the real Hebrew queries you would run in Google (max 6).
- All numeric fields must be pure numbers (no units, no text).

recommended_cars: array of 5–10 cars. EACH car MUST include:
  - brand
  - model
  - year
  - fuel
  - gear
  - turbo
  - engine_cc
  - price_range_nis
  - avg_fuel_consumption (+ fuel_method):
      * non-EV: km per liter (number only)
      * EV: kWh per 100 km (number only)
  - annual_fee (₪/year, number only) + fee_method
  - reliability_score (1–10, number only) + reliability_method
  - maintenance_cost (₪/year, number only) + maintenance_method
  - safety_rating (1–10, number only) + safety_method
  - insurance_cost (₪/year, number only) + insurance_method
  - resale_value (1–10, number only) + resale_method
  - performance_score (1–10, number only) + performance_method
  - comfort_features (1–10, number only) + comfort_method
  - suitability (1–10, number only) + suitability_method
  - market_supply ("גבוה" / "בינוני" / "נמוך") + supply_method
  - fit_score (0–100, number only)
  - comparison_comment (Hebrew)
  - not_recommended_reason (Hebrew or null)

Where feasible, keep the current schema and ALSO add these richer fields per car:
  - trim_levels_israel: array of Israeli trim objects with sources when known
  - official_safety: {{"rating":"string|null","organization":"string|null","sources":["url"]}}
  - license_fee_israel: {{"annual_fee_ils": number|null, "method":"official|unknown", "sources":["url"]}}
  - warranty_israel: {{"vehicle_warranty":"string|null","battery_warranty":"string|null","sources":["url"]}}
  - competitors: [{{"model":"string","why_consider":"string"}}]
  - best_for: ["Hebrew string"]
  - not_ideal_for: ["Hebrew string"]
  - practical_summary: "Hebrew practical summary"

**All explanation fields (all *_method, comparison_comment, not_recommended_reason, practical_summary) MUST be in clean, easy Hebrew.**
Fit Score means preference-fit only:
- Fit Score represents how well the car matches the questionnaire preferences only.
- Fit Score is NOT a reliability score.
- Fit Score is NOT a purchase-worthiness score.
- Fit Score is NOT an approval to buy a specific vehicle.
- A car may receive a high Fit Score even if it has reliability, resale, liquidity, or ownership-cost drawbacks, as long as it strongly matches what the user asked for.
- Risks, drawbacks, and inspection points must appear separately and clearly from the preference-fit explanation.
- Never frame any result as a final approval to buy.
- Do not use first-person recommendation language such as "אני ממליץ", "הייתי קונה", "תקנה", or "אל תקנה".
- Do not call reliability_score a factual truth; treat it only as a rough maintenance-risk indicator and explain caveats.

Explanation field rules:
- comparison_comment: explain only why the car matches the user's stated preferences, priorities, budget, body style, gearbox, fuel, comfort, usage, or taste.
- not_recommended_reason: explain separately the main risks, drawbacks, ownership caveats, liquidity issues, or what to inspect before purchase, even when the car still has a high Fit Score.
- Do not use comparison_comment to warn about risks unless directly tied to preference fit.

IMPORTANT MARKET REALITY:
- לפני שאתה בוחר רכבים, תבדוק בזהירות בעזרת החיפוש שדגם כזה אכן נמכר בישראל, בתצורת מנוע וגיר שאתה מציג.
- אל תמציא דגמים או גרסאות שלא קיימים ביד 2 בישראל.
- אל תמציא ציוני בטיחות רשמיים, רמות גימור ישראליות, מחירים, אגרות, אחריות או ריקולים. אם אין מקור מאומת, החזר null/unknown והסבר קצר.
- license_fee_israel.method חייב להיות official או unknown בלבד.
- מודלים שלא נמכרו כמעט / אין להם היצע – סמן "market_supply": "נמוך" והסבר בעברית.

Return ONLY raw JSON. Do not add any backticks or explanation text.
"""

        result = call_grounded_model(
            GEMINI_RECOMMENDER_MODEL_ID,
            prompt,
            timeout_sec=AI_CALL_TIMEOUT_SEC,
        )
        err = result.get("error_code")
        if err == "EXECUTOR_SATURATED":
            return {"_error": "SERVER_BUSY"}
        if err == "CALL_TIMEOUT":
            return {"_error": "CALL_TIMEOUT"}
        if err:
            return {"_error": err}
        grounding_meta = result.get("grounding_meta") or {}
        if not grounding_meta.get("grounding_successful") and os.environ.get("ALLOW_UNGROUNDED_FALLBACK", "").lower() != "true":
            return {"_error": GROUNDING_FAILED_CODE}
        text = (result.get("text") or "").strip()
        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("[AI] Car Advisor JSON decode error, raw length=%d", len(text) if text else 0)
            return {"_error": "JSON decode error from Gemini Car Advisor"}
        if isinstance(result, dict):
            result["_grounding_meta"] = grounding_meta
            model_claimed = result.get("search_performed")
            if model_claimed and not grounding_meta.get("grounding_successful"):
                logger.warning(
                    "[AI] advisor model claims search_performed=true but no real grounding metadata found"
                )
        return result
    finally:
        duration_ms = (pytime.perf_counter() - start_time) * 1000
        logger.info(
            "[AI] feature=recommender model=%s duration_ms=%.2f",
            GEMINI_RECOMMENDER_MODEL_ID,
            duration_ms,
        )


def car_advisor_postprocess(profile: dict, parsed: dict) -> dict:
    """
    מקבל profile + פלט גולמי מג'מיני, מחשב עלויות שנתיות,
    ממפה ערכים לעברית ומחזיר אובייקט JSON מוכן ל-frontend.
    """
    recommended = parsed.get("recommended_cars") or []
    if not isinstance(recommended, list) or not recommended:
        early = {
            "search_performed": parsed.get("search_performed", False),
            "search_queries": parsed.get("search_queries", []),
            "recommended_cars": [],
        }
        grounding_meta = parsed.get("_grounding_meta")
        if isinstance(grounding_meta, dict):
            early["_grounding_meta"] = grounding_meta
            if not grounding_meta.get("grounding_successful"):
                early["grounding_confidence"] = "unverified"
        return early

    annual_km = profile.get("annual_km", 15000)
    fuel_price = profile.get("fuel_price_nis_per_liter", 7.0)
    elec_price = profile.get("electricity_price_nis_per_kwh", 0.65)

    processed = []
    for car in recommended:
        if not isinstance(car, dict):
            continue
        car = dict(car)  # copy

        fuel_val = str(car.get("fuel", "")).strip()
        gear_val = str(car.get("gear", "")).strip()
        turbo_val = car.get("turbo")

        if fuel_val in fuel_map:
            fuel_norm = fuel_map[fuel_val]
        else:
            fuel_norm = fuel_val.lower()

        if gear_val in gear_map:
            gear_norm = gear_map[gear_val]
        else:
            gear_norm = gear_val.lower()

        if isinstance(turbo_val, str):
            turbo_norm = turbo_map.get(turbo_val, turbo_val)
        else:
            turbo_norm = turbo_val

        avg_fc = car.get("avg_fuel_consumption")
        try:
            avg_fc_num = float(avg_fc)
            if avg_fc_num <= 0:
                avg_fc_num = None
        except Exception:
            avg_fc_num = None

        annual_energy_cost = None
        if avg_fc_num is not None:
            if fuel_norm == "electric":
                annual_energy_cost = (annual_km / 100.0) * avg_fc_num * elec_price
            else:
                annual_energy_cost = (annual_km / avg_fc_num) * fuel_price

        def as_float(x):
            try:
                return float(x)
            except Exception:
                return 0.0

        maintenance_cost = as_float(car.get("maintenance_cost"))
        insurance_cost = as_float(car.get("insurance_cost"))
        annual_fee = as_float(car.get("annual_fee"))

        if annual_energy_cost is not None:
            total_annual_cost = annual_energy_cost + maintenance_cost + insurance_cost + annual_fee
        else:
            total_annual_cost = None

        car["annual_energy_cost"] = round(annual_energy_cost, 0) if annual_energy_cost is not None else None
        car["annual_fuel_cost"] = car["annual_energy_cost"]
        car["maintenance_cost"] = round(maintenance_cost, 0)
        car["insurance_cost"] = round(insurance_cost, 0)
        car["annual_fee"] = round(annual_fee, 0)
        car["total_annual_cost"] = round(total_annual_cost, 0) if total_annual_cost is not None else None

        car["fuel"] = fuel_map_he.get(fuel_norm, fuel_val or fuel_norm)
        car["gear"] = gear_map_he.get(gear_norm, gear_val or gear_norm)
        car["turbo"] = turbo_map_he.get(turbo_norm, turbo_val)

        processed.append(car)

    grounding_meta = parsed.get("_grounding_meta")
    result = {
        "search_performed": parsed.get("search_performed", False),
        "search_queries": parsed.get("search_queries", []),
        "recommended_cars": processed,
    }
    if isinstance(grounding_meta, dict):
        result["_grounding_meta"] = grounding_meta
        if not grounding_meta.get("grounding_successful"):
            result["grounding_confidence"] = "unverified"
    return result
