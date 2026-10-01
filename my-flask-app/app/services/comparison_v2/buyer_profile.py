# -*- coding: utf-8 -*-
"""BuyerPreferenceProfile (``buyer-profile/2``): structured user needs.

Everything the decision uses is a validated enum / number — no free text.
Priorities (0–4) are the user's declared weights and stay under
deterministic code control; ``main_use`` and the other context fields only
help JEV judge how much a *factual* difference matters, they never silently
change a weight.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

BUYER_PROFILE_VERSION = "buyer-profile/2"

MODE_PERSONALIZED = "personalized"
MODE_GENERAL = "general"
MODES = (MODE_PERSONALIZED, MODE_GENERAL)

MAIN_USE = ("city", "mixed", "highway", "long_trips", "family", "commuting", "work")
CARGO_NEED = ("low", "medium", "high")
PARKING = ("tight", "normal", "no_constraint")
AWD_REQUIREMENT = ("required", "preferred", "not_important")
ROAD_CONDITIONS = ("normal_roads", "rough_roads", "off_road")
CHARGING_ACCESS = ("home", "work", "home_and_work", "public_only", "none", "unknown")

# Priority dimensions the user can weigh (0 = not important ... 4 = critical).
PRIORITY_KEYS = (
    "safety",
    "performance",
    "efficiency",
    "practicality",
    "purchase_price",
    "warranty",
    "equipment",
    "environment",
)
EV_PRIORITY_KEY = "ev_convenience"
PRIORITY_MIN, PRIORITY_MAX = 0, 4

# Documented balanced default for a general comparison: every applicable
# dimension gets the same middle weight. ev_convenience applies only when an
# EV/PHEV is selected. Towing has no declared priority; it is weighted only
# when the user states a towing requirement (see composer.TOWING_WEIGHT).
BALANCED_PRIORITY = 2

PRIORITY_LABELS_HE = {0: "לא חשוב", 1: "מעט חשוב", 2: "בינוני", 3: "חשוב", 4: "קריטי"}
MAIN_USE_LABELS_HE = {
    "city": "עירוני", "mixed": "מעורב", "highway": "בין-עירוני", "long_trips": "נסיעות ארוכות",
    "family": "משפחה", "commuting": "נסיעות יומיות לעבודה", "work": "עבודה",
}
CARGO_LABELS_HE = {"low": "תא מטען קטן מספיק", "medium": "תא מטען בינוני", "high": "צורך בתא מטען גדול"}
PARKING_LABELS_HE = {"tight": "חניה צפופה", "normal": "חניה רגילה", "no_constraint": "ללא מגבלת חניה"}
AWD_LABELS_HE = {"required": "הנעה כפולה — חובה", "preferred": "הנעה כפולה — רצוי", "not_important": "הנעה כפולה — לא חשוב"}
ROAD_LABELS_HE = {"normal_roads": "כבישים רגילים", "rough_roads": "דרכים משובשות", "off_road": "שטח"}
CHARGING_LABELS_HE = {
    "home": "טעינה בבית", "work": "טעינה בעבודה", "home_and_work": "טעינה בבית ובעבודה",
    "public_only": "טעינה ציבורית בלבד", "none": "ללא גישה לטעינה", "unknown": "גישה לטעינה לא ידועה",
}
PRIORITY_NAMES_HE = {
    "safety": "בטיחות", "performance": "ביצועים", "efficiency": "חיסכון בצריכה", "practicality": "פרקטיות",
    "purchase_price": "מחיר רכישה", "warranty": "אחריות", "equipment": "אבזור", "environment": "סביבה",
    "ev_convenience": "נוחות טעינה ונסיעה חשמלית",
}

# Equipment the user may require / prefer. Only canonical fields we actually
# extract (official Level 2) or unambiguous government ADAS flags.
FEATURE_SOURCES: Dict[str, Tuple[str, str]] = {
    "apple_carplay": ("official", "apple_carplay"),
    "android_auto": ("official", "android_auto"),
    "heated_front_seats": ("official", "heated_front_seats"),
    "ventilated_front_seats": ("official", "ventilated_front_seats"),
    "power_front_seats": ("official", "power_front_seats"),
    "panoramic_roof": ("official", "panoramic_roof"),
    "surround_view_camera": ("official", "surround_view_camera"),
    "premium_audio": ("official", "premium_audio"),
    "reverse_camera": ("government", "matzlemat_reverse_ind"),
    "adaptive_cruise_control": ("government", "bakarat_shyut_adaptivit_ind"),
    "blind_spot_monitoring": ("government", "zihuy_beshetah_nistar_ind"),
    "lane_keeping_assist": ("government", "bakarat_stiya_activ_s"),
}
FEATURE_LABELS_HE = {
    "apple_carplay": "Apple CarPlay", "android_auto": "Android Auto", "heated_front_seats": "חימום מושבים קדמיים",
    "ventilated_front_seats": "אוורור מושבים קדמיים", "power_front_seats": "כוונון חשמלי למושבים",
    "panoramic_roof": "גג פנורמי", "surround_view_camera": "מצלמות 360°", "premium_audio": "מערכת שמע פרימיום",
    "reverse_camera": "מצלמת רוורס", "adaptive_cruise_control": "בקרת שיוט אדפטיבית",
    "blind_spot_monitoring": "זיהוי רכב בשטח מת", "lane_keeping_assist": "שמירה אקטיבית על נתיב",
}

# Concepts users ask about that this version cannot evaluate. They never
# influence the result; the UI shows them disabled.
UNSUPPORTED_CONCEPTS = ("reliability", "ownership_cost", "ride_comfort")

MAX_FEATURES = 12


class BuyerProfileError(ValueError):
    pass


def _enum(value: Any, allowed: Tuple[str, ...], field: str, required: bool = False) -> Optional[str]:
    if value in (None, ""):
        if required:
            raise BuyerProfileError(f"{field}: required")
        return None
    if not isinstance(value, str) or value not in allowed:
        raise BuyerProfileError(f"{field}: invalid value")
    return value


def _int(value: Any, field: str, lo: int, hi: int) -> Optional[int]:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise BuyerProfileError(f"{field}: invalid number")
    try:
        num = float(value)
    except (TypeError, ValueError):
        raise BuyerProfileError(f"{field}: invalid number") from None
    if num != num or not (lo <= num <= hi):  # NaN or out of range
        raise BuyerProfileError(f"{field}: out of range")
    return int(round(num))


def _features(value: Any, field: str) -> List[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise BuyerProfileError(f"{field}: must be a list")
    out: List[str] = []
    for item in value[:MAX_FEATURES]:
        if not isinstance(item, str) or item not in FEATURE_SOURCES:
            raise BuyerProfileError(f"{field}: unknown feature")
        if item not in out:
            out.append(item)
    return sorted(out)


def _priorities(value: Any, include_ev: bool) -> Dict[str, int]:
    raw = value if isinstance(value, dict) else {}
    if value not in (None, "") and not isinstance(value, dict):
        raise BuyerProfileError("priorities: must be an object")
    keys = PRIORITY_KEYS + ((EV_PRIORITY_KEY,) if include_ev else ())
    unknown = set(raw) - set(PRIORITY_KEYS) - {EV_PRIORITY_KEY}
    if unknown:
        raise BuyerProfileError("priorities: unknown dimension")
    out = {}
    for key in keys:
        if raw.get(key) in (None, ""):
            raise BuyerProfileError(f"priorities.{key}: required")
        out[key] = _int(raw.get(key), f"priorities.{key}", PRIORITY_MIN, PRIORITY_MAX)
    return out


def balanced_profile(include_ev: bool) -> Dict[str, Any]:
    priorities = {k: BALANCED_PRIORITY for k in PRIORITY_KEYS}
    if include_ev:
        priorities[EV_PRIORITY_KEY] = BALANCED_PRIORITY
    return {
        "schema": BUYER_PROFILE_VERSION,
        "mode": MODE_GENERAL,
        "main_use": None,
        "annual_km": None,
        "regular_passengers": None,
        "budget_max_ils": None,
        "cargo_need": None,
        "parking_constraint": None,
        "towing_braked_required_kg": None,
        "awd_requirement": None,
        "road_conditions": None,
        "charging_access": None,
        "typical_daily_km": None,
        "frequent_long_trip_km": None,
        "must_have_features": [],
        "nice_to_have_features": [],
        "priorities": priorities,
    }


def normalize_buyer_profile(value: Any, *, has_plugin_vehicle: bool) -> Dict[str, Any]:
    """Validate + canonicalize. Raises BuyerProfileError with a field-level reason."""
    if not isinstance(value, dict):
        raise BuyerProfileError("buyer_profile: required (choose personalized or general)")
    mode = _enum(value.get("mode"), MODES, "mode", required=True)
    if mode == MODE_GENERAL:
        return balanced_profile(has_plugin_vehicle)

    profile = balanced_profile(has_plugin_vehicle)
    profile["mode"] = MODE_PERSONALIZED
    profile["main_use"] = _enum(value.get("main_use"), MAIN_USE, "main_use", required=True)
    profile["annual_km"] = _int(value.get("annual_km"), "annual_km", 0, 150_000)
    profile["regular_passengers"] = _int(value.get("regular_passengers"), "regular_passengers", 1, 9)
    profile["budget_max_ils"] = _int(value.get("budget_max_ils"), "budget_max_ils", 10_000, 5_000_000)
    profile["cargo_need"] = _enum(value.get("cargo_need"), CARGO_NEED, "cargo_need")
    profile["parking_constraint"] = _enum(value.get("parking_constraint"), PARKING, "parking_constraint")
    profile["towing_braked_required_kg"] = _int(value.get("towing_braked_required_kg"), "towing_braked_required_kg", 100, 5_000)
    profile["awd_requirement"] = _enum(value.get("awd_requirement"), AWD_REQUIREMENT, "awd_requirement")
    profile["road_conditions"] = _enum(value.get("road_conditions"), ROAD_CONDITIONS, "road_conditions")
    if has_plugin_vehicle:
        profile["charging_access"] = _enum(value.get("charging_access"), CHARGING_ACCESS, "charging_access")
        profile["typical_daily_km"] = _int(value.get("typical_daily_km"), "typical_daily_km", 0, 1_000)
        profile["frequent_long_trip_km"] = _int(value.get("frequent_long_trip_km"), "frequent_long_trip_km", 0, 3_000)
    must = _features(value.get("must_have_features"), "must_have_features")
    nice = [f for f in _features(value.get("nice_to_have_features"), "nice_to_have_features") if f not in must]
    profile["must_have_features"] = must
    profile["nice_to_have_features"] = nice
    profile["priorities"] = _priorities(value.get("priorities"), has_plugin_vehicle)
    return profile


def jev_buyer_context(profile: Dict[str, Any]) -> Dict[str, Any]:
    """Context JEV may use to judge materiality/fit.

    Priorities are deliberately excluded: they are applied as weights in code
    and must not be double-counted (or re-interpreted) by the model.
    """
    if profile.get("mode") == MODE_GENERAL:
        return {"mode": MODE_GENERAL, "note": "No buyer-specific needs were given; judge for a typical private buyer in Israel."}
    keys = ("main_use", "annual_km", "regular_passengers", "budget_max_ils", "cargo_need", "parking_constraint",
            "towing_braked_required_kg", "awd_requirement", "road_conditions", "charging_access",
            "typical_daily_km", "frequent_long_trip_km")
    ctx = {"mode": MODE_PERSONALIZED}
    ctx.update({k: profile.get(k) for k in keys if profile.get(k) is not None})
    return ctx


def profile_summary_he(profile: Dict[str, Any]) -> Dict[str, Any]:
    """Hebrew chips for the result header (presentation only)."""
    if profile.get("mode") == MODE_GENERAL:
        headline = ["השוואה כללית — משקל שווה לכל התחומים"]
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
    ]
    priorities.sort(key=lambda p: -p["value"])
    return {"headline": [h for h in headline if h], "priorities": priorities}


CARGO_EXAMPLES_HE = {
    "low": "קניות שבועיות ותיק או שניים",
    "medium": "עגלת תינוק או מזוודות לחופשה משפחתית",
    "high": "ציוד ספורט, מזוודות לכל המשפחה או עבודה עם ציוד",
}
UNSUPPORTED_LABELS_HE = {
    "reliability": "אמינות ארוכת טווח",
    "ownership_cost": "עלות אחזקה, ביטוח וירידת ערך",
    "ride_comfort": "נוחות נסיעה",
}


def ui_options() -> Dict[str, Any]:
    """Structured choices for the /compare personalization step (labels only)."""
    return {
        "main_use": [(k, MAIN_USE_LABELS_HE[k]) for k in MAIN_USE],
        "priorities": [(k, PRIORITY_NAMES_HE[k]) for k in PRIORITY_KEYS],
        "ev_priority": (EV_PRIORITY_KEY, PRIORITY_NAMES_HE[EV_PRIORITY_KEY]),
        "priority_levels": [(v, PRIORITY_LABELS_HE[v]) for v in range(PRIORITY_MIN, PRIORITY_MAX + 1)],
        "balanced_priority": BALANCED_PRIORITY,
        "cargo": [(k, CARGO_LABELS_HE[k], CARGO_EXAMPLES_HE[k]) for k in CARGO_NEED],
        "parking": [(k, PARKING_LABELS_HE[k]) for k in PARKING],
        "awd": [(k, AWD_LABELS_HE[k]) for k in AWD_REQUIREMENT],
        "road": [(k, ROAD_LABELS_HE[k]) for k in ROAD_CONDITIONS],
        "charging": [(k, CHARGING_LABELS_HE[k]) for k in CHARGING_ACCESS],
        "features": [(k, FEATURE_LABELS_HE[k]) for k in FEATURE_SOURCES],
        "unsupported": [(k, UNSUPPORTED_LABELS_HE[k]) for k in UNSUPPORTED_CONCEPTS],
    }
