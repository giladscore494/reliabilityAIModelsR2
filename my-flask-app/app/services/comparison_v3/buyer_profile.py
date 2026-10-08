# -*- coding: utf-8 -*-
"""BuyerPreferenceProfile ``buyer-profile/3`` (V3).

Changes from ``buyer-profile/2``:

* priorities: ``purchase_price, safety, performance, efficiency_environment, practicality`` (+ ``ev_convenience``
  when a plug-in car is selected). ``efficiency_environment`` replaces ``efficiency`` + ``environment``; a stored
  ``buyer-profile/2`` profile maps them to ``max(efficiency, environment)``. ``warranty`` and ``equipment`` are gone.
  A slider is hidden when its category has no row for the selected cars, so a missing priority is the balanced
  default (it can only weigh a dimension that has evidence);
* no ``cargo_need`` / ``road_conditions`` (no cargo volume, no ground clearance data);
* must-have / nice-to-have features are the government ADAS flags only.

Everything is an enum, a bounded number or a known key: no free text. Priorities are weights in code and are never
sent to JEV.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.services.comparison_v2.buyer_profile import (
    AWD_LABELS_HE,
    AWD_REQUIREMENT,
    BuyerProfileError,
    CHARGING_ACCESS,
    CHARGING_LABELS_HE,
    MAIN_USE,
    MAIN_USE_LABELS_HE,
    MODE_GENERAL,
    MODE_PERSONALIZED,
    MODES,
    PARKING,
    PARKING_LABELS_HE,
    PRIORITY_LABELS_HE,
    PRIORITY_MAX,
    PRIORITY_MIN,
    _enum,
    _int,
)

BUYER_PROFILE_VERSION = "buyer-profile/3"
BUYER_PROFILE_V2 = "buyer-profile/2"

PRIORITY_KEYS = ("purchase_price", "safety", "performance", "efficiency_environment", "practicality")
EV_PRIORITY_KEY = "ev_convenience"
BALANCED_PRIORITY = 2
PRIORITY_NAMES_HE = {
    "purchase_price": "מחיר", "safety": "בטיחות", "performance": "ביצועים",
    "efficiency_environment": "צריכה וסביבה", "practicality": "מידות ומרחב",
    "ev_convenience": "נוחות נסיעה חשמלית",
}

# Government ADAS flags only (source "government"; equipment_stated).
FEATURE_SOURCES: Dict[str, Tuple[str, str]] = {
    "reverse_camera": ("government", "matzlemat_reverse_ind"),
    "adaptive_cruise_control": ("government", "bakarat_shyut_adaptivit_ind"),
    "blind_spot_monitoring": ("government", "zihuy_beshetah_nistar_ind"),
    "lane_keeping_assist": ("government", "bakarat_stiya_activ_s"),
}
FEATURE_LABELS_HE = {
    "reverse_camera": "מצלמת רוורס", "adaptive_cruise_control": "בקרת שיוט אדפטיבית",
    "blind_spot_monitoring": "זיהוי רכב בשטח מת", "lane_keeping_assist": "שמירה אקטיבית על נתיב",
}
MAX_FEATURES = len(FEATURE_SOURCES)


def _features(value: Any, field: str) -> List[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise BuyerProfileError(f"{field}: must be a list")
    out: List[str] = []
    for item in value[:MAX_FEATURES * 4]:
        if not isinstance(item, str):
            raise BuyerProfileError(f"{field}: unknown feature")
        if item not in FEATURE_SOURCES:
            raise BuyerProfileError(f"{field}: unknown feature")
        if item not in out:
            out.append(item)
    return sorted(out)


def migrate_priorities(raw: Dict[str, Any], schema: Optional[str]) -> Dict[str, Any]:
    """A ``buyer-profile/2`` priority map in ``/3`` keys: efficiency / environment -> max; warranty / equipment
    dropped. A ``/3`` map is returned unchanged."""
    if not isinstance(raw, dict):
        return raw
    legacy = schema == BUYER_PROFILE_V2 or (
        ("efficiency" in raw or "environment" in raw) and "efficiency_environment" not in raw)
    if not legacy:
        return raw
    out = {k: v for k, v in raw.items() if k in PRIORITY_KEYS + (EV_PRIORITY_KEY,)}
    merged = [raw[k] for k in ("efficiency", "environment") if raw.get(k) not in (None, "")]
    if merged:
        out["efficiency_environment"] = max(_int(v, "priorities.efficiency_environment", PRIORITY_MIN, PRIORITY_MAX)
                                            for v in merged)
    return out


def _priorities(value: Any, include_ev: bool, schema: Optional[str]) -> Dict[str, int]:
    if value not in (None, "") and not isinstance(value, dict):
        raise BuyerProfileError("priorities: must be an object")
    raw = migrate_priorities(value if isinstance(value, dict) else {}, schema)
    unknown = set(raw) - set(PRIORITY_KEYS) - {EV_PRIORITY_KEY}
    if unknown:
        raise BuyerProfileError("priorities: unknown dimension")
    keys = PRIORITY_KEYS + ((EV_PRIORITY_KEY,) if include_ev else ())
    out = {}
    for key in keys:
        given = raw.get(key)
        out[key] = BALANCED_PRIORITY if given in (None, "") else _int(given, f"priorities.{key}", PRIORITY_MIN, PRIORITY_MAX)
    return out


def balanced_profile(include_ev: bool) -> Dict[str, Any]:
    priorities = {k: BALANCED_PRIORITY for k in PRIORITY_KEYS}
    if include_ev:
        priorities[EV_PRIORITY_KEY] = BALANCED_PRIORITY
    return {
        "schema": BUYER_PROFILE_VERSION, "mode": MODE_GENERAL, "main_use": None, "annual_km": None,
        "regular_passengers": None, "budget_max_ils": None, "parking_constraint": None,
        "towing_braked_required_kg": None, "awd_requirement": None, "charging_access": None,
        "typical_daily_km": None, "frequent_long_trip_km": None, "must_have_features": [],
        "nice_to_have_features": [], "priorities": priorities,
    }


def normalize_buyer_profile(value: Any, *, has_plugin_vehicle: bool) -> Dict[str, Any]:
    """Validate + canonicalize (``buyer-profile/3``; a ``/2`` profile is migrated). Raises BuyerProfileError."""
    if not isinstance(value, dict):
        raise BuyerProfileError("buyer_profile: required (choose personalized or general)")
    mode = _enum(value.get("mode"), MODES, "mode", required=True)
    if mode == MODE_GENERAL:
        return balanced_profile(has_plugin_vehicle)
    schema = value.get("schema")
    profile = balanced_profile(has_plugin_vehicle)
    profile["mode"] = MODE_PERSONALIZED
    profile["main_use"] = _enum(value.get("main_use"), MAIN_USE, "main_use", required=True)
    profile["annual_km"] = _int(value.get("annual_km"), "annual_km", 0, 150_000)
    profile["regular_passengers"] = _int(value.get("regular_passengers"), "regular_passengers", 1, 9)
    profile["budget_max_ils"] = _int(value.get("budget_max_ils"), "budget_max_ils", 10_000, 5_000_000)
    profile["parking_constraint"] = _enum(value.get("parking_constraint"), PARKING, "parking_constraint")
    profile["towing_braked_required_kg"] = _int(value.get("towing_braked_required_kg"), "towing_braked_required_kg",
                                                100, 5_000)
    profile["awd_requirement"] = _enum(value.get("awd_requirement"), AWD_REQUIREMENT, "awd_requirement")
    if has_plugin_vehicle:
        profile["charging_access"] = _enum(value.get("charging_access"), CHARGING_ACCESS, "charging_access")
        profile["typical_daily_km"] = _int(value.get("typical_daily_km"), "typical_daily_km", 0, 1_000)
        profile["frequent_long_trip_km"] = _int(value.get("frequent_long_trip_km"), "frequent_long_trip_km", 0, 3_000)
    must = _features(value.get("must_have_features"), "must_have_features")
    nice = [f for f in _features(value.get("nice_to_have_features"), "nice_to_have_features") if f not in must]
    profile["must_have_features"] = must
    profile["nice_to_have_features"] = nice
    profile["priorities"] = _priorities(value.get("priorities"), has_plugin_vehicle, schema)
    return profile


def jev_buyer_context(profile: Dict[str, Any]) -> Dict[str, Any]:
    """Context JEV may use to judge materiality / fit. Priorities are excluded (weights live in code)."""
    if profile.get("mode") == MODE_GENERAL:
        return {"mode": MODE_GENERAL, "note": "No buyer-specific needs were given; judge for a typical private buyer in Israel."}
    keys = ("main_use", "annual_km", "regular_passengers", "budget_max_ils", "parking_constraint",
            "towing_braked_required_kg", "awd_requirement", "charging_access", "typical_daily_km",
            "frequent_long_trip_km")
    ctx = {"mode": MODE_PERSONALIZED}
    ctx.update({k: profile.get(k) for k in keys if profile.get(k) is not None})
    return ctx


def profile_summary_he(profile: Dict[str, Any], dimensions: Optional[List[str]] = None) -> Dict[str, Any]:
    """Hebrew chips for the result header; only priorities whose category exists for these cars."""
    if profile.get("mode") == MODE_GENERAL:
        headline = ["השוואה כללית: משקל שווה לכל התחומים"]
    else:
        headline = [MAIN_USE_LABELS_HE.get(profile.get("main_use"), "")]
        if profile.get("annual_km"):
            headline.append(f"כ-{profile['annual_km']:,} ק״מ בשנה")
        if profile.get("regular_passengers"):
            headline.append(f"{profile['regular_passengers']} נוסעים")
        if profile.get("charging_access"):
            headline.append(CHARGING_LABELS_HE[profile["charging_access"]])
        if profile.get("towing_braked_required_kg"):
            headline.append(f"גרירה עד {profile['towing_braked_required_kg']:,} ק״ג")
        if profile.get("budget_max_ils"):
            headline.append(f"תקציב עד ₪{profile['budget_max_ils']:,}")
    priorities = [
        {"key": k, "name_he": PRIORITY_NAMES_HE[k], "value": v, "label_he": PRIORITY_LABELS_HE[v]}
        for k, v in (profile.get("priorities") or {}).items()
        if dimensions is None or k in dimensions
    ]
    priorities.sort(key=lambda p: -p["value"])
    return {"headline": [h for h in headline if h], "priorities": priorities}


def ui_options() -> Dict[str, Any]:
    """Structured choices for the /compare personalization step (labels only)."""
    return {
        "main_use": [(k, MAIN_USE_LABELS_HE[k]) for k in MAIN_USE],
        "priorities": [(k, PRIORITY_NAMES_HE[k]) for k in PRIORITY_KEYS],
        "ev_priority": (EV_PRIORITY_KEY, PRIORITY_NAMES_HE[EV_PRIORITY_KEY]),
        "priority_levels": [(v, PRIORITY_LABELS_HE[v]) for v in range(PRIORITY_MIN, PRIORITY_MAX + 1)],
        "balanced_priority": BALANCED_PRIORITY,
        "parking": [(k, PARKING_LABELS_HE[k]) for k in PARKING],
        "awd": [(k, AWD_LABELS_HE[k]) for k in AWD_REQUIREMENT],
        "charging": [(k, CHARGING_LABELS_HE[k]) for k in CHARGING_ACCESS],
        "features": [(k, FEATURE_LABELS_HE[k]) for k in FEATURE_SOURCES],
        "unsupported": [("reliability", "אמינות ארוכת טווח"), ("ownership_cost", "עלות אחזקה וביטוח"),
                        ("ride_comfort", "נוחות נסיעה")],
    }
