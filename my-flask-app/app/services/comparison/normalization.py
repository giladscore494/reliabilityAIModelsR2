# -*- coding: utf-8 -*-
"""Vehicle, source, and comparison-slot normalization helpers."""

import re
from typing import Any, Dict, List, Optional, Tuple


def build_display_name(car: Dict[str, Any]) -> str:
    """Build a human-readable display name for a car.
    Format: "{make} {model} {year}" or "{make} {model}" if no year.
    """
    parts = [car.get("make", ""), car.get("model", "")]
    year = car.get("year")
    if year:
        parts.append(str(year))
    elif car.get("year_start") and car.get("year_end"):
        parts.append(f"{car['year_start']}-{car['year_end']}")
    return " ".join(p for p in parts if p).strip()


CHECKED_VERSION_UNKNOWN_HE = "לא ידוע / לבדיקה"
CHECKED_VERSION_NOT_VERIFIED_HE = "לא מאומת"
CHECKED_VERSION_DATA_BASIS_ALLOWED = {
    "user_input",
    "verified_source",
    "ai_inference",
    "mixed",
}
CHECKED_VERSION_CONFIDENCE_ALLOWED = {"high", "medium", "low", "unverified"}


def _normalize_general_transmission_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return CHECKED_VERSION_UNKNOWN_HE

    lowered = text.lower()
    if any(
        token in lowered
        for token in ("dsg", "dct", "dual clutch", "dual-clutch", "robot", "רובוט")
    ):
        return "רובוטית"
    if any(token in lowered for token in ("cvt", "רציפ", "continuously variable")):
        return "רציפה"
    if any(token in lowered for token in ("manual", "ידני", "ידנית")):
        return "ידנית"
    if any(
        token in lowered
        for token in (
            "unknown",
            "not verified",
            "needs verification",
            "לא ידוע",
            "לבדיקה",
            "לא מאומת",
        )
    ):
        return CHECKED_VERSION_UNKNOWN_HE
    if any(
        token in lowered
        for token in ("automatic", "auto", "אוטומט", "planetary", "פלנטר")
    ):
        return "אוטומטית"
    return text[:80]


def _normalize_checked_version_text(value: Any, default: str = "") -> str:
    text = " ".join(str(value or "").split()).strip()
    return text[:180] if text else default


def _sanitize_checked_versions(
    payload: Any, slot_keys: List[str]
) -> Dict[str, Dict[str, str]]:
    if not isinstance(payload, dict):
        return {}

    sanitized: Dict[str, Dict[str, str]] = {}
    for slot_key in slot_keys:
        raw = payload.get(slot_key)
        if not isinstance(raw, dict):
            continue
        data_basis = raw.get("data_basis")
        confidence = raw.get("confidence")
        sanitized[slot_key] = {
            "make": _normalize_checked_version_text(raw.get("make")),
            "model": _normalize_checked_version_text(raw.get("model")),
            "year": _normalize_checked_version_text(raw.get("year")),
            "trim": _normalize_checked_version_text(raw.get("trim")),
            "engine_type": _normalize_checked_version_text(raw.get("engine_type")),
            "transmission": _normalize_general_transmission_label(
                raw.get("transmission")
            ),
            "drivetrain": _normalize_checked_version_text(raw.get("drivetrain")),
            "seats": _normalize_checked_version_text(raw.get("seats")),
            "data_basis": data_basis
            if data_basis in CHECKED_VERSION_DATA_BASIS_ALLOWED
            else "ai_inference",
            "confidence": confidence
            if confidence in CHECKED_VERSION_CONFIDENCE_ALLOWED
            else "low",
            "notes": _normalize_checked_version_text(raw.get("notes")),
        }
    return sanitized


def _normalize_grounded_cars_format(grounded_output: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    grounded_cars_raw = (
        ((grounded_output or {}).get("cars") or {})
        if isinstance(grounded_output, dict)
        else {}
    )
    if isinstance(grounded_cars_raw, list):
        return {
            f"car_{index + 1}": item
            for index, item in enumerate(grounded_cars_raw)
            if isinstance(item, dict)
        }
    return grounded_cars_raw if isinstance(grounded_cars_raw, dict) else {}


_COMPARE_SLOT_RE = re.compile(r"^car_(\d+)$")


def build_checked_versions(
    cars_selected_slots: Dict[str, Dict[str, Any]],
    grounded_output: Optional[Dict[str, Any]],
    ai_checked_versions: Optional[Dict[str, Dict[str, str]]] = None,
) -> Dict[str, Dict[str, str]]:
    grounded_cars = _normalize_grounded_cars_format(grounded_output)
    slot_keys = ordered_compare_slot_keys(cars_selected_slots or {}, grounded_cars, ai_checked_versions or {})
    ai_checked_versions = _sanitize_checked_versions(ai_checked_versions, slot_keys)
    result: Dict[str, Dict[str, str]] = {}

    for slot_key in slot_keys:
        selection = (cars_selected_slots or {}).get(slot_key, {}) or {}
        grounded_car = (
            grounded_cars.get(slot_key, {})
            if isinstance(grounded_cars.get(slot_key, {}), dict)
            else {}
        )
        profile = (
            grounded_car.get("car_profile")
            if isinstance(grounded_car.get("car_profile"), dict)
            else {}
        )
        identity = (
            profile.get("vehicle_identity")
            if isinstance(profile.get("vehicle_identity"), dict)
            else {}
        )
        powertrain = (
            profile.get("powertrain_specs")
            if isinstance(profile.get("powertrain_specs"), dict)
            else {}
        )
        recommended_trim = (
            profile.get("recommended_trim")
            if isinstance(profile.get("recommended_trim"), dict)
            else {}
        )
        trims = (
            profile.get("trim_levels_israel")
            if isinstance(profile.get("trim_levels_israel"), list)
            else []
        )

        make = _normalize_checked_version_text(
            identity.get("make")
        ) or _normalize_checked_version_text(selection.get("make"))
        model = _normalize_checked_version_text(
            identity.get("model")
        ) or _normalize_checked_version_text(selection.get("model"))
        year = (
            _normalize_checked_version_text(identity.get("year"))
            or _normalize_checked_version_text(selection.get("year"))
            or _normalize_checked_version_text(selection.get("year_start"))
        )

        trim_confidence = str(recommended_trim.get("confidence") or "").strip().lower()
        trim = _normalize_checked_version_text(recommended_trim.get("trim_name"))
        if not trim and len(trims) == 1 and isinstance(trims[0], dict):
            trim = _normalize_checked_version_text(trims[0].get("trim_name"))
        if not trim or trim_confidence == "low":
            trim = CHECKED_VERSION_NOT_VERIFIED_HE

        engine_type = (
            _normalize_checked_version_text(powertrain.get("engine"))
            or _normalize_checked_version_text(selection.get("engine_type"))
            or _normalize_checked_version_text(
                (grounded_car.get("facts") or {}).get("fuel_type")
            )
            or CHECKED_VERSION_UNKNOWN_HE
        )
        transmission = _normalize_general_transmission_label(
            powertrain.get("gearbox") or selection.get("gearbox")
        )
        drivetrain = _normalize_checked_version_text(
            powertrain.get("drivetrain"),
            CHECKED_VERSION_NOT_VERIFIED_HE,
        )
        seats_value = powertrain.get("seats")
        seats = (
            _normalize_checked_version_text(seats_value)
            if seats_value not in (None, "")
            else CHECKED_VERSION_NOT_VERIFIED_HE
        )

        has_profile = bool(profile)
        has_sources = bool(
            powertrain.get("sources")
            or profile.get("sources")
            or grounded_car.get("sources")
            or (
                trims[0].get("source") if trims and isinstance(trims[0], dict) else None
            )
        )
        has_user_specific = bool(
            selection.get("year")
            or selection.get("engine_type")
            or selection.get("gearbox")
        )

        if has_sources and has_user_specific:
            data_basis = "mixed"
        elif has_sources:
            data_basis = "verified_source"
        elif has_profile and has_user_specific:
            data_basis = "mixed"
        elif has_profile:
            data_basis = "ai_inference"
        else:
            data_basis = "user_input"

        if has_sources and year and trim != CHECKED_VERSION_NOT_VERIFIED_HE:
            confidence = "high"
        elif has_sources and year:
            confidence = "medium"
        elif has_profile or has_user_specific:
            confidence = "low"
        else:
            confidence = "unverified"

        note_parts: List[str] = []
        if has_user_specific and (
            selection.get("engine_type") or selection.get("gearbox")
        ):
            note_parts.append(
                "סוג המנוע או התיבה נבחרו כערכים כלליים בטופס "
                "ויש לאמת מול מפרט היבואן או רישיון הרכב."
            )
        if trim == CHECKED_VERSION_NOT_VERIFIED_HE:
            note_parts.append("רמת הגימור לא אומתה במידע הזמין.")
        if not year:
            note_parts.append("השנתון המדויק לא אומת.")
        if data_basis in {"mixed", "ai_inference"}:
            note_parts.append(
                "ההשוואה מתייחסת לגרסה מייצגת לפי המידע הזמין "
                "וייתכנו הבדלים בין רמות גימור, מנועים ותיבות הילוכים."
            )
        if not note_parts:
            note_parts.append("יש לאמת את המפרט מול מקור רשמי לפני החלטת רכישה.")

        fallback = {
            "make": make,
            "model": model,
            "year": year or CHECKED_VERSION_NOT_VERIFIED_HE,
            "trim": trim,
            "engine_type": engine_type,
            "transmission": transmission,
            "drivetrain": drivetrain,
            "seats": seats,
            "data_basis": data_basis,
            "confidence": confidence,
            "notes": " ".join(note_parts[:2]),
        }

        merged = dict(fallback)
        ai_version = ai_checked_versions.get(slot_key) or {}
        for key, value in ai_version.items():
            if value:
                merged[key] = value
        if not merged.get("transmission"):
            merged["transmission"] = CHECKED_VERSION_UNKNOWN_HE
        merged["transmission"] = _normalize_general_transmission_label(
            merged.get("transmission")
        )
        result[slot_key] = merged

    return result


def map_cars_to_slots(validated_cars: List[Dict]) -> Dict[str, Dict]:
    """Map validated cars to stable slot keys: car_1, car_2, car_3.
    Each slot includes the original selection fields plus display_name.
    """
    slots = {}
    for i, car in enumerate(validated_cars):
        slot_key = f"car_{i + 1}"
        slot_data = dict(car)  # copy
        slot_data["display_name"] = build_display_name(car)
        slots[slot_key] = slot_data
    return slots


def ordered_compare_slot_keys(*sources: Any) -> List[str]:
    seen = set()
    ordered: List[str] = []
    for source in sources:
        if isinstance(source, dict):
            keys = source.keys()
        else:
            keys = source or []
        for key in keys:
            if isinstance(key, str) and _COMPARE_SLOT_RE.match(key) and key not in seen:
                seen.add(key)
                ordered.append(key)
    return sorted(
        ordered, key=lambda value: int(_COMPARE_SLOT_RE.match(value).group(1))
    )


def normalize_compare_writer_winner(
    value: Any, allowed_slot_keys: List[str]
) -> Optional[str]:
    if value == "tie":
        return "tie"
    if not isinstance(value, str):
        return None
    if value in allowed_slot_keys:
        return value
    legacy_map = {
        "carA": "car_1",
        "carB": "car_2",
        "carC": "car_3",
    }
    normalized = legacy_map.get(value)
    if normalized in allowed_slot_keys:
        return normalized
    return None

_ordered_compare_slot_keys = ordered_compare_slot_keys
_normalize_compare_writer_winner = normalize_compare_writer_winner


def _segment_text_tokens(
    car_slot: Optional[Dict[str, Any]], grounded_car_data: Optional[Dict[str, Any]]
) -> str:
    car_slot = car_slot or {}
    grounded_car_data = grounded_car_data or {}
    facts = (
        (grounded_car_data.get("facts") or {})
        if isinstance(grounded_car_data, dict)
        else {}
    )
    text_parts = [
        car_slot.get("make"),
        car_slot.get("model"),
        car_slot.get("trim"),
        car_slot.get("display_name"),
        car_slot.get("engine_type"),
        car_slot.get("gearbox"),
        grounded_car_data.get("car_name")
        if isinstance(grounded_car_data, dict)
        else None,
        facts.get("body_type"),
        facts.get("fuel_type"),
        " ".join((grounded_car_data.get("short_notes") or [])[:4])
        if isinstance(grounded_car_data, dict)
        else None,
    ]
    return " ".join(str(part).lower() for part in text_parts if part)


def infer_compare_segment_details(
    car_slot: Optional[Dict[str, Any]],
    grounded_car_data: Optional[Dict[str, Any]],
) -> Tuple[str, List[str]]:
    text = _segment_text_tokens(car_slot, grounded_car_data)
    facts = (
        ((grounded_car_data or {}).get("facts") or {})
        if isinstance(grounded_car_data, dict)
        else {}
    )
    body_type = str(facts.get("body_type") or "").lower()

    def _matches(*keywords: str) -> List[str]:
        return [keyword for keyword in keywords if keyword in text]

    def _body_matches(*keywords: str) -> List[str]:
        return [keyword for keyword in keywords if keyword in body_type]

    pickup_hits = _matches(
        "pickup",
        "pick-up",
        "truck",
        "ute",
        "hilux",
        "ranger",
        "navara",
        "amarok",
        "d-max",
        "l200",
        "triton",
        "ram ",
        "f-150",
        "silverado",
    ) + _body_matches("pickup", "truck")
    if pickup_hits:
        return "pickup_truck", pickup_hits[:3]

    mpv_hits = _matches(
        "minivan",
        "mpv",
        "people carrier",
        "grand c4 spacetourer",
        "touran",
        "carens",
        "s-max",
        "galaxy",
        "berlingo",
        "doblo",
        "caddy",
    ) + _body_matches("minivan", "mpv", "van")
    if mpv_hits:
        return "minivan_mpv", mpv_hits[:3]

    offroad_hits = _matches(
        "land cruiser",
        "prado",
        "wrangler",
        "defender",
        "jimny",
        "pajero",
        "patrol",
        "grenadier",
        "g-class",
        "g wagon",
        "g-wagon",
        "4runner",
    ) + _body_matches("4x4", "off-road", "off road")
    if offroad_hits:
        return "hardcore_4x4", offroad_hits[:3]

    family_3row_hits = _matches(
        "7 seat",
        "7-seat",
        "7 seater",
        "seven seat",
        "third row",
        "3 row",
        "3-row",
        "highlander",
        "pilot",
        "sorento",
        "palisade",
        "telluride",
        "pathfinder",
        "xc90",
        "explorer",
        "everest",
        "kodiaq",
    )
    if family_3row_hits:
        return "three_row_family_suv", family_3row_hits[:3]

    sporty_hits = _matches(
        "gti",
        "type r",
        "type-r",
        "sti",
        "gr86",
        "86",
        "brz",
        "mx-5",
        "miata",
        "cupra",
        "amg",
        "m sport",
        " m ",
        "rs ",
        "n line",
        "n ",
        "vrs",
        "track",
        "sportback performance",
        "hot hatch",
        "roadster",
        "coupe",
    )
    if sporty_hits:
        return "sporty_dynamic", sporty_hits[:3]

    executive_hits = _matches(
        "executive",
        "luxury",
        "premium",
        "5 series",
        "7 series",
        "a6",
        "a8",
        "e-class",
        "s-class",
        "es ",
        "gs ",
        "ls ",
        "g80",
        "g90",
        "s90",
        "xf",
        "xj",
    )
    if executive_hits:
        return "executive_luxury", executive_hits[:3]

    city_hits = _matches(
        "city",
        "mini",
        "aygo",
        "i10",
        "picanto",
        "up!",
        "up ",
        "c1",
        "108",
        "spark",
        "alto",
        "mii",
        "ka ",
        "twingo",
    )
    if city_hits:
        return "city_mini", city_hits[:3]

    supermini_hits = _matches(
        "supermini",
        "polo",
        "ibiza",
        "fiesta",
        "yaris",
        "clio",
        "corsa",
        "jazz",
        "fit",
        "i20",
        "rio",
        "208",
        "mazda2",
        "swift",
        "fabia",
    )
    if supermini_hits:
        return "supermini_hatch", supermini_hits[:3]

    crossover_hits = _matches(
        "crossover",
        "cross",
        "cuv",
        "suv",
        "sportage",
        "qashqai",
        "cx-5",
        "cx5",
        "tucson",
        "rav4",
        "cr-v",
        "crv",
        "x-trail",
        "xtrail",
        "kadjar",
        "3008",
    ) + _body_matches("suv", "crossover", "cuv")
    if crossover_hits:
        return "crossover_soft_suv", crossover_hits[:3]

    family_body_hits = _matches(
        "sedan",
        "saloon",
        "hatch",
        "hatchback",
        "wagon",
        "estate",
        "tourer",
        "fastback",
        "liftback",
    ) + _body_matches(
        "sedan", "saloon", "hatch", "wagon", "estate", "fastback", "liftback"
    )
    if family_body_hits:
        return "family_sedan_hatch_wagon", family_body_hits[:3]

    return "general_private_car", ["default_private_car"]

_infer_compare_segment_details = infer_compare_segment_details


def infer_compare_segment(
    car_slot: Optional[Dict[str, Any]], grounded_car_data: Optional[Dict[str, Any]]
) -> str:
    """Infer a lightweight compare segment without relying on a missing taxonomy field."""
    segment_key, _signals = infer_compare_segment_details(car_slot, grounded_car_data)
    return segment_key
