# -*- coding: utf-8 -*-
"""Level 2 field contracts: which official fields may be extracted and how.

Each field declares its canonical unit, the deterministic unit conversions
that are allowed, a plausible range, which powertrains it applies to, whether
it must come from an Israeli official source, and its freshness group. A
measurement *standard* (WLTP/EPA/...) is never converted — only units are.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, FrozenSet, Optional, Tuple

ALL_FAMILIES: FrozenSet[str] = frozenset({"ev", "phev", "combustion", "unknown"})
PLUGIN: FrozenSet[str] = frozenset({"ev", "phev"})
COMBUSTION_LIKE: FrozenSet[str] = frozenset({"combustion", "phev"})

FRESHNESS_TECHNICAL = "technical"
FRESHNESS_PRICE = "price"
FRESHNESS_WARRANTY = "warranty"

# Initial TTLs per freshness group (seconds).
FRESHNESS_TTL_SECONDS = {
    FRESHNESS_TECHNICAL: 30 * 24 * 3600,
    FRESHNESS_PRICE: 24 * 3600,
    FRESHNESS_WARRANTY: 7 * 24 * 3600,
}

RANGE_STANDARDS = ("WLTP", "EPA", "NEDC", "CLTC", "unknown")
TRANSMISSION_TYPES = ("automatic", "manual", "dual_clutch", "cvt", "e_cvt", "single_speed")


def _factor(f: float) -> Callable[[float], float]:
    return lambda v: v * f


def _inverse(k: float) -> Callable[[float], float]:
    return lambda v: k / v


@dataclass(frozen=True)
class FieldSpec:
    key: str
    group: str
    label_he: str
    value_type: str  # number | int | bool | str | enum
    unit: Optional[str] = None
    units: Dict[str, Callable[[float], float]] = field(default_factory=dict)
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    applies_to: FrozenSet[str] = ALL_FAMILIES
    israeli_only: bool = False
    local_authority: bool = False  # Israeli source outranks a global one
    freshness: str = FRESHNESS_TECHNICAL
    conflict_rel: float = 0.02
    conflict_abs: float = 0.0
    enum_values: Tuple[str, ...] = ()
    pattern: Optional[str] = None
    requires_standard: bool = False
    # How much vehicle identity a source must establish before a value of
    # this field may be attributed to the Level 1.5 variant (see
    # ``IDENTITY_SCOPES`` and ``official_variant_matcher``).
    identity_scope: str = "exact_variant"

    @property
    def trim_sensitive(self) -> bool:
        """True when the Israeli trim (or the exact model code) is required."""
        return self.identity_scope == SCOPE_EXACT_VARIANT


def _num(key, group, label, unit, units, lo, hi, **kw) -> FieldSpec:
    return FieldSpec(key=key, group=group, label_he=label, value_type=kw.pop("value_type", "number"),
                     unit=unit, units=units, min_value=lo, max_value=hi, **kw)


def _flag(key, label) -> FieldSpec:
    return FieldSpec(key=key, group="equipment", label_he=label, value_type="bool", local_authority=True)


_LENGTH_UNITS = {"mm": _factor(1), "cm": _factor(10), "m": _factor(1000), "in": _factor(25.4), "inch": _factor(25.4), "inches": _factor(25.4)}

FIELD_SPECS: Dict[str, FieldSpec] = {
    spec.key: spec
    for spec in (
        # --- performance ---
        _num("torque_nm", "performance", "מומנט", "Nm",
             {"nm": _factor(1), "lbft": _factor(1.3558179483), "kgm": _factor(9.80665), "kgfm": _factor(9.80665)},
             50, 2500),
        _num("acceleration_0_100_s", "performance", "תאוצה 0–100 קמ״ש", "s",
             {"s": _factor(1), "sec": _factor(1), "seconds": _factor(1)}, 1.5, 25, conflict_abs=0.2),
        _num("top_speed_kmh", "performance", "מהירות מרבית", "km/h",
             {"kmh": _factor(1), "kph": _factor(1), "mph": _factor(1.609344)}, 80, 420, conflict_abs=3),
        _num("fuel_consumption_l_100km", "performance", "צריכת דלק", "L/100km",
             {"l100km": _factor(1), "kml": _inverse(100.0)}, 0.5, 30, applies_to=COMBUSTION_LIKE, conflict_abs=0.2),
        _num("energy_consumption_kwh_100km", "performance", "צריכת אנרגיה", "kWh/100km",
             {"kwh100km": _factor(1), "whkm": _factor(0.1)}, 8, 60, applies_to=PLUGIN, conflict_abs=0.3),
        # --- battery / EV ---
        _num("battery_capacity_kwh", "battery", "קיבולת סוללה (ברוטו)", "kWh", {"kwh": _factor(1)}, 1, 250, applies_to=PLUGIN),
        _num("battery_capacity_net_kwh", "battery", "קיבולת סוללה (נטו)", "kWh", {"kwh": _factor(1)}, 1, 250, applies_to=PLUGIN),
        _num("electric_range_km", "battery", "טווח חשמלי", "km",
             {"km": _factor(1), "mi": _factor(1.609344), "miles": _factor(1.609344)}, 10, 1200,
             applies_to=PLUGIN, requires_standard=True),
        FieldSpec(key="electric_range_standard", group="battery", label_he="תקן מדידת טווח", value_type="enum",
                  enum_values=RANGE_STANDARDS, applies_to=PLUGIN),
        _num("ac_charging_power_kw", "battery", "טעינת AC מרבית", "kW", {"kw": _factor(1)}, 1, 50, applies_to=PLUGIN),
        _num("dc_charging_power_kw", "battery", "טעינת DC מרבית", "kW", {"kw": _factor(1)}, 10, 500, applies_to=PLUGIN),
        _num("dc_charge_time_minutes", "battery", "זמן טעינה מהירה", "min",
             {"min": _factor(1), "mins": _factor(1), "minutes": _factor(1), "h": _factor(60), "hours": _factor(60)},
             5, 180, applies_to=PLUGIN, conflict_abs=2),
        _num("dc_charge_from_pct", "battery", "טעינה מהירה — מ־%", "%", {"%": _factor(1), "pct": _factor(1), "percent": _factor(1)},
             0, 99, applies_to=PLUGIN, conflict_rel=0.0),
        _num("dc_charge_to_pct", "battery", "טעינה מהירה — עד %", "%", {"%": _factor(1), "pct": _factor(1), "percent": _factor(1)},
             1, 100, applies_to=PLUGIN, conflict_rel=0.0),
        # --- dimensions / packaging ---
        _num("length_mm", "dimensions", "אורך", "mm", _LENGTH_UNITS, 2500, 6500, conflict_abs=10),
        _num("width_mm", "dimensions", "רוחב", "mm", _LENGTH_UNITS, 1400, 2700, conflict_abs=10),
        _num("height_mm", "dimensions", "גובה", "mm", _LENGTH_UNITS, 1000, 2500, conflict_abs=10),
        _num("wheelbase_mm", "dimensions", "בסיס גלגלים", "mm", _LENGTH_UNITS, 1800, 4000, conflict_abs=10),
        _num("ground_clearance_mm", "dimensions", "מרווח גחון", "mm", _LENGTH_UNITS, 80, 400, conflict_abs=5),
        _num("cargo_volume_l", "dimensions", "נפח תא מטען", "L",
             {"l": _factor(1), "liters": _factor(1), "litres": _factor(1), "ft3": _factor(28.3168466), "cuft": _factor(28.3168466)},
             50, 4000, conflict_rel=0.03),
        _num("fuel_tank_l", "dimensions", "מיכל דלק", "L",
             {"l": _factor(1), "liters": _factor(1), "litres": _factor(1), "gal": _factor(3.785411784), "usgal": _factor(3.785411784)},
             20, 200, applies_to=COMBUSTION_LIKE, conflict_abs=1),
        # --- transmission ---
        FieldSpec(key="transmission_type", group="transmission", label_he="סוג תיבת הילוכים", value_type="enum",
                  enum_values=TRANSMISSION_TYPES, local_authority=True),
        _num("transmission_gears", "transmission", "מספר הילוכים", None, {"": _factor(1), "gears": _factor(1), "speed": _factor(1)},
             1, 12, value_type="int", local_authority=True, conflict_rel=0.0),
        # --- equipment ---
        _num("wheel_size_in", "equipment", "קוטר חישוקים", "in", {"in": _factor(1), "inch": _factor(1), "inches": _factor(1), '"': _factor(1)},
             13, 26, local_authority=True, conflict_rel=0.0),
        FieldSpec(key="tire_size", group="equipment", label_he="מידות צמיגים", value_type="str",
                  pattern=r"^\d{3}/\d{2}\s?Z?R\s?\d{2}$", local_authority=True),
        _num("multimedia_screen_in", "equipment", "מסך מולטימדיה", "in", {"in": _factor(1), "inch": _factor(1), "inches": _factor(1), '"': _factor(1)},
             5, 60, local_authority=True, conflict_abs=0.3),
        _flag("apple_carplay", "Apple CarPlay"),
        _flag("android_auto", "Android Auto"),
        _flag("heated_front_seats", "חימום מושבים קדמיים"),
        _flag("ventilated_front_seats", "אוורור מושבים קדמיים"),
        _flag("power_front_seats", "כוונון חשמלי למושבים קדמיים"),
        _flag("panoramic_roof", "גג פנורמי"),
        _flag("surround_view_camera", "מצלמות 360°"),
        _flag("premium_audio", "מערכת שמע פרימיום"),
        # --- commercial (Israeli official source only) ---
        _num("official_price_ils", "commercial", "מחיר מחירון רשמי", "ILS", {"ils": _factor(1), "nis": _factor(1), "₪": _factor(1)},
             30_000, 3_000_000, israeli_only=True, local_authority=True, freshness=FRESHNESS_PRICE, conflict_rel=0.005),
        _num("registration_fee_ils", "commercial", "אגרת רישום", "ILS", {"ils": _factor(1), "nis": _factor(1), "₪": _factor(1)},
             0, 200_000, israeli_only=True, local_authority=True, freshness=FRESHNESS_PRICE, conflict_rel=0.005),
        _num("warranty_vehicle_years", "commercial", "אחריות לרכב (שנים)", "years", {"years": _factor(1), "year": _factor(1), "y": _factor(1)},
             1, 15, israeli_only=True, local_authority=True, freshness=FRESHNESS_WARRANTY, conflict_rel=0.0),
        _num("warranty_vehicle_km", "commercial", "אחריות לרכב (ק״מ)", "km", {"km": _factor(1)},
             10_000, 1_000_000, israeli_only=True, local_authority=True, freshness=FRESHNESS_WARRANTY, conflict_rel=0.0),
        _num("warranty_battery_years", "commercial", "אחריות לסוללה (שנים)", "years", {"years": _factor(1), "year": _factor(1), "y": _factor(1)},
             1, 15, israeli_only=True, local_authority=True, freshness=FRESHNESS_WARRANTY, conflict_rel=0.0),
        _num("warranty_battery_km", "commercial", "אחריות לסוללה (ק״מ)", "km", {"km": _factor(1)},
             10_000, 1_000_000, israeli_only=True, local_authority=True, freshness=FRESHNESS_WARRANTY, conflict_rel=0.0),
    )
}

# ---------------------------------------------------------------------------
# identity scopes — what determines each value
# ---------------------------------------------------------------------------
# Every scope rejects an EXPLICIT contradiction (model, body, generation,
# stated vehicle model year, propulsion, drivetrain, engine/motor, model
# code). They differ only in which identity elements must be POSITIVELY
# established when the source does not name the Israeli trim:
#
# * exact_variant — Israeli trim (with model + full powertrain) or the exact
#   official model code. Price, fees, warranty, equipment, wheels/tyres,
#   height and ground clearance (suspension / wheel packages change them).
# * powertrain — model + propulsion + drivetrain + engine/motor
#   configuration. Values the manufacturer defines per mechanical
#   configuration: performance, battery, charging, gearbox, fuel tank and the
#   homologated consumption / range of that configuration (a value the source
#   itself gives as a range or per wheel/option is rejected as not exact, and
#   different values for one configuration become a conflict — never an
#   average and never the best case).
# * powertrain_body — powertrain + the body explicitly stated and compatible
#   (cargo volume differs between body styles, drivetrains and batteries).
# * model_generation — same model, no body / generation / year / powertrain
#   contradiction, plus a positive generation anchor (generation code, the
#   exact powertrain, or the stated model year). Exterior length, width,
#   wheelbase — invariant for one body generation.
SCOPE_EXACT_VARIANT = "exact_variant"
SCOPE_POWERTRAIN = "powertrain"
SCOPE_POWERTRAIN_BODY = "powertrain_body"
SCOPE_MODEL_GENERATION = "model_generation"
IDENTITY_SCOPES = (SCOPE_EXACT_VARIANT, SCOPE_POWERTRAIN, SCOPE_POWERTRAIN_BODY, SCOPE_MODEL_GENERATION)

FIELD_IDENTITY_SCOPES: Dict[str, str] = {
    **{key: SCOPE_POWERTRAIN for key in (
        "torque_nm", "acceleration_0_100_s", "top_speed_kmh",
        "fuel_consumption_l_100km", "energy_consumption_kwh_100km",
        "battery_capacity_kwh", "battery_capacity_net_kwh",
        "electric_range_km", "electric_range_standard",
        "ac_charging_power_kw", "dc_charging_power_kw",
        "dc_charge_time_minutes", "dc_charge_from_pct", "dc_charge_to_pct",
        "transmission_type", "transmission_gears", "fuel_tank_l",
    )},
    "cargo_volume_l": SCOPE_POWERTRAIN_BODY,
    **{key: SCOPE_MODEL_GENERATION for key in ("length_mm", "width_mm", "wheelbase_mm")},
    # everything else (height, ground clearance, equipment, wheels/tyres,
    # commercial) stays exact_variant
}
# Backward-compatible name: fields that do not need the Israeli trim.
POWERTRAIN_LEVEL_FIELDS = tuple(k for k, v in FIELD_IDENTITY_SCOPES.items() if v == SCOPE_POWERTRAIN)
FIELD_SPECS = {
    key: replace(spec, identity_scope=FIELD_IDENTITY_SCOPES.get(key, SCOPE_EXACT_VARIANT))
    for key, spec in FIELD_SPECS.items()
}

FIELD_GROUPS = ("performance", "battery", "dimensions", "transmission", "equipment", "commercial")

# Fields that exist in Level 1.5. The model may report them only as a
# cross-check; they are NEVER merged into the snapshot. A disagreement is a
# government conflict shown in diagnostics only.
GOVERNMENT_CROSSCHECK_SPECS: Dict[str, FieldSpec] = {
    "horsepower": _num("horsepower", "crosscheck", "כוח סוס", "hp",
                       {"hp": _factor(1), "ps": _factor(0.98632), "cv": _factor(0.98632), "kw": _factor(1.341022)}, 40, 2000),
    "engine_cc": _num("engine_cc", "crosscheck", "נפח מנוע", "cc", {"cc": _factor(1), "cm3": _factor(1), "l": _factor(1000)},
                      0, 9000, conflict_abs=60),
    "seats": _num("seats", "crosscheck", "מושבים", None, {"": _factor(1), "seats": _factor(1)}, 1, 9, value_type="int", conflict_rel=0.0),
    "doors": _num("doors", "crosscheck", "דלתות", None, {"": _factor(1), "doors": _factor(1)}, 1, 6, value_type="int", conflict_rel=0.0),
}

FRESHNESS_GROUP_FIELDS: Dict[str, Tuple[str, ...]] = {
    group: tuple(k for k, s in FIELD_SPECS.items() if s.freshness == group)
    for group in (FRESHNESS_TECHNICAL, FRESHNESS_PRICE, FRESHNESS_WARRANTY)
}


def normalize_unit_token(unit: Any) -> str:
    if unit is None:
        return ""
    text = str(unit).strip().lower()
    text = text.replace("·", "").replace("-", "").replace(" ", "").replace("‏", "")
    text = text.replace("km/h", "kmh").replace("kmph", "kmh").replace("l/100km", "l100km").replace("l/100", "l100km")
    text = text.replace("kwh/100km", "kwh100km").replace("wh/km", "whkm").replace("km/l", "kml")
    text = text.replace("lb.ft", "lbft").replace("lbft", "lbft").replace("lb/ft", "lbft").replace("ftlb", "lbft")
    text = text.replace("cu.ft", "cuft").replace("ft³", "ft3").replace("ליטר", "l").replace("שניות", "s")
    text = text.replace("kg·m", "kgm").replace("kgf·m", "kgfm").replace("n.m", "nm").replace("n·m", "nm")
    return text


def applies_to_family(spec: FieldSpec, family: str) -> bool:
    return family in spec.applies_to


def pattern_ok(spec: FieldSpec, value: str) -> bool:
    return not spec.pattern or bool(re.match(spec.pattern, value.strip(), flags=re.IGNORECASE))


def hp_from_kw(kw: float) -> float:
    """Display-only helper (kW -> hp)."""
    return kw * 1.341022


def field_label_he(key: str) -> str:
    spec = FIELD_SPECS.get(key) or GOVERNMENT_CROSSCHECK_SPECS.get(key)
    return spec.label_he if spec else key


def field_catalog_for_prompt(family: str, groups: Optional[Tuple[str, ...]] = None) -> Dict[str, Dict[str, Any]]:
    """Field list (applicable to this powertrain) rendered into the prompt."""
    out: Dict[str, Dict[str, Any]] = {}
    for key, spec in FIELD_SPECS.items():
        if not applies_to_family(spec, family):
            continue
        if groups and spec.freshness not in groups:
            continue
        item: Dict[str, Any] = {"group": spec.group, "type": spec.value_type}
        if spec.unit:
            item["unit"] = spec.unit
        if spec.enum_values:
            item["allowed_values"] = list(spec.enum_values)
        if spec.israeli_only:
            item["israeli_official_source_only"] = True
        if spec.requires_standard:
            item["must_state_measurement_standard"] = True
        out[key] = item
    return out
