# -*- coding: utf-8 -*-
"""Shared V2/2 decision vocabulary: dimensions, groups, constants.

All tunable numbers live here as named constants. They are PROVISIONAL and
must be calibrated on our own labeled evaluation set before anyone describes
the outcome as calibrated.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Tuple

# Decision dimensions == user priority keys (+ towing, which has no priority
# control and is weighted only when the user declares a towing need).
DIMENSIONS: Tuple[str, ...] = (
    "safety", "performance", "efficiency", "ev_convenience", "practicality",
    "towing", "environment", "purchase_price", "warranty", "equipment",
)

# Correlation group -> dimension. Correlation groups (not individual metrics)
# are the unit of evidence: one group == one signal.
GROUP_DIMENSION: Dict[str, str] = {
    "gov_safety_rating": "safety", "adas_equipment": "safety", "passive_safety": "safety", "stability_basics": "safety",
    "power_output": "performance", "acceleration": "performance", "top_speed": "performance",
    "fuel_use": "efficiency", "energy_use": "efficiency",
    "battery_size": "ev_convenience", "electric_range": "ev_convenience", "dc_charging": "ev_convenience", "ac_charging": "ev_convenience",
    "cargo": "practicality",
    "towing": "towing",
    "co2_wltp": "environment", "co2_city_highway": "environment", "pollution_class": "environment", "tailpipe_other": "environment",
    "price": "purchase_price",
    "vehicle_warranty": "warranty", "battery_warranty": "warranty",
}

# English descriptions used inside JEV questions (internal wording).
GROUP_DESCRIPTION_EN: Dict[str, str] = {
    "gov_safety_rating": "the official government safety rating and safety-equipment level",
    "adas_equipment": "the number of government-registered driver-assistance systems",
    "passive_safety": "the number of airbags",
    "stability_basics": "ABS / electronic stability control availability",
    "power_output": "power output (horsepower and, when validated, torque)",
    "acceleration": "official 0-100 km/h acceleration time",
    "top_speed": "official top speed",
    "fuel_use": "official fuel consumption (L/100km)",
    "energy_use": "official electric energy consumption (kWh/100km)",
    "battery_size": "battery capacity",
    "electric_range": "official electric range under the same measurement standard",
    "dc_charging": "DC fast-charging capability",
    "ac_charging": "AC charging power",
    "cargo": "official cargo volume",
    "towing": "official braked/unbraked towing capacity",
    "co2_wltp": "WLTP CO2 emissions",
    "co2_city_highway": "city/highway CO2 emissions",
    "pollution_class": "the government pollution group and green index",
    "tailpipe_other": "WLTP NOx emissions",
    "price": "the official Israeli list price",
    "vehicle_warranty": "the official Israeli vehicle warranty",
    "battery_warranty": "the official Israeli battery warranty",
}

GROUP_LABEL_HE: Dict[str, str] = {
    "gov_safety_rating": "דירוג הבטיחות הממשלתי", "adas_equipment": "מערכות סיוע לנהג", "passive_safety": "כריות אוויר",
    "stability_basics": "ABS ובקרת יציבות", "power_output": "הספק ומומנט", "acceleration": "תאוצה", "top_speed": "מהירות מרבית",
    "fuel_use": "צריכת דלק", "energy_use": "צריכת חשמל", "battery_size": "קיבולת סוללה", "electric_range": "טווח חשמלי",
    "dc_charging": "טעינה מהירה", "ac_charging": "טעינת AC", "cargo": "נפח תא מטען", "towing": "כושר גרירה",
    "co2_wltp": "פליטות CO₂ (WLTP)", "co2_city_highway": "פליטות CO₂ עירוני/בין-עירוני", "pollution_class": "קבוצת זיהום ומדד ירוק",
    "tailpipe_other": "פליטות NOx", "price": "מחיר רשמי", "vehicle_warranty": "אחריות לרכב", "battery_warranty": "אחריות לסוללה",
    "parking_fit": "התאמה לחניה", "body_use_fit": "התאמת המרכב לשימוש", "ground_clearance_fit": "מרווח גחון לתנאי הדרך",
    "charging_routine_fit": "התאמה לשגרת הטעינה", "awd_preference": "הנעה כפולה (רצוי)",
}

# UI category -> dimensions it reports.
CATEGORY_DIMENSIONS: Dict[str, Tuple[str, ...]] = {
    "safety": ("safety",),
    "performance": ("performance",),
    "efficiency": ("efficiency",),
    "electric_and_charging": ("ev_convenience",),
    "practicality": ("practicality",),
    "towing_and_utility": ("towing",),
    "environment": ("environment",),
    "official_price_and_warranty": ("purchase_price", "warranty"),
    "equipment_and_convenience": ("equipment",),
}

# Common 5-level materiality scale (TypeSafe Score levels must describe
# concrete situations, low -> high).
MATERIALITY_LEVELS = [
    "The difference is negligible or irrelevant for this buyer's stated use.",
    "The difference has a small practical effect and is unlikely to influence the purchase decision by itself.",
    "The difference has a noticeable practical effect and could reasonably influence the decision.",
    "The difference has a large practical effect for this buyer's stated use and should materially influence the decision.",
    "The difference directly affects whether the vehicle is suitable for an important stated need and is potentially decisive.",
]
MATERIALITY_LABEL_HE = {0: "זניחה", 1: "קטנה", 2: "מורגשת", 3: "גדולה", 4: "מכרעת"}

# Contextual fit scales (0 = clearly poor fit ... 4 = excellent fit).
FIT_LEVELS: Dict[str, list] = {
    "parking_fit": [
        "Clearly poor fit; the vehicle's dimensions likely conflict with the stated parking constraint.",
        "Workable only with meaningful inconvenience given the stated parking constraint.",
        "Acceptable / neutral for the stated parking situation.",
        "Good fit with little inconvenience for the stated parking situation.",
        "Excellent fit for the stated parking situation.",
    ],
    "body_use_fit": [
        "The body style clearly conflicts with the stated primary use.",
        "The body style is workable for the stated primary use but with clear compromises.",
        "The body style is acceptable / neutral for the stated primary use.",
        "The body style suits the stated primary use well.",
        "The body style is an excellent match for the stated primary use.",
    ],
    "ground_clearance_fit": [
        "The validated ground clearance is clearly inadequate for the stated road conditions.",
        "The ground clearance is workable for the stated road conditions only with care and frequent limits.",
        "The ground clearance is acceptable / neutral for the stated road conditions.",
        "The ground clearance suits the stated road conditions well.",
        "The ground clearance is excellent for the stated road conditions.",
    ],
    "charging_routine_fit": [
        "The validated range and charging capabilities clearly conflict with the stated charging access and driving pattern.",
        "Workable only with meaningful inconvenience (frequent planning or long charging stops) for the stated pattern.",
        "Acceptable / neutral for the stated charging access and driving pattern.",
        "Good fit; the stated daily and long-trip pattern is covered with little inconvenience.",
        "Excellent fit; range and charging comfortably cover the stated charging access and driving pattern.",
    ],
}
FIT_LABEL_HE = {0: "התאמה חלשה", 1: "התאמה עם פשרות", 2: "התאמה סבירה", 3: "התאמה טובה", 4: "התאמה מצוינת"}

# ---- provisional composition constants (calibrate on our own eval set) ----
SCORE_MAX = 4.0
# A personalized decision needs at least this share of the user's weight to
# be backed by usable evidence; otherwise the result is insufficient_evidence.
MIN_EFFECTIVE_WEIGHT_COVERAGE = 0.25
# |pairwise utility| below this margin is a practical tie.
PRACTICAL_TIE_MARGIN = 0.05
# Recommendation-strength bands for |utility| (wording only, never a score).
STRENGTH_CLEAR = 0.35
STRENGTH_MODERATE = 0.15
# Towing has no priority control: it weighs this much only when the user
# declared a towing requirement, otherwise zero.
TOWING_WEIGHT_WHEN_REQUIRED = 4
# Non-plug-in vehicles do not depend on charging: neutral fit (level 2 of 4).
NON_PLUGIN_CHARGING_FIT = 0.5
# Deterministic soft preferences.
AWD_PREFERENCE_STRENGTH = 0.5
NICE_TO_HAVE_STRENGTH = 0.5
# |category contribution to U| below this reads as "barely influenced" (wording only).
CATEGORY_NEGLIGIBLE_CONTRIBUTION = 0.01


def dimension_weights(profile: Dict[str, Any], families: Iterable[str]) -> Dict[str, int]:
    """User weight per dimension (0 == no influence).

    Priorities come straight from the validated profile. ``ev_convenience``
    applies only when a plug-in vehicle is selected; ``towing`` has no
    priority control and weighs ``TOWING_WEIGHT_WHEN_REQUIRED`` only when the
    user declared a towing requirement.
    """
    priorities = profile.get("priorities") or {}
    has_plugin = any(f in ("ev", "phev") for f in families)
    weights = {}
    for dim in DIMENSIONS:
        if dim == "towing":
            weights[dim] = TOWING_WEIGHT_WHEN_REQUIRED if profile.get("towing_braked_required_kg") else 0
        elif dim == "ev_convenience":
            weights[dim] = int(priorities.get(dim) or 0) if has_plugin else 0
        else:
            weights[dim] = int(priorities.get(dim) or 0)
    return weights
