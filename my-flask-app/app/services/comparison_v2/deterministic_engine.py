# -*- coding: utf-8 -*-
"""Deterministic atomic comparison engine (no global score, no /100).

Every metric is declared once in ``METRIC_REGISTRY`` with its path, category,
comparison kind, direction, tie threshold, applicability and correlation
group. The engine produces per-metric atomic results and per-category
``CategoryEvidence``. Rules that hold by construction:

* missing != zero != worse: a car without a value is excluded from that
  metric; it never loses it, and fewer than two values means
  ``insufficient_data``;
* the engine is symmetric: swapping cars swaps leaders;
* L/100km and kWh/100km are never compared with each other;
* gross permitted mass never feeds a power-to-weight ratio;
* correlated signals (e.g. government safety score and safety equipment
  level, or the 19 ADAS systems) share a correlation group so they count as
  one line of evidence, not many.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Tuple

from app.services.comparison_v2.contracts import CHOICE_TIE, NOT_APPLICABLE
from app.services.comparison_v2.field_registry import FIELD_SPECS, applies_to_family
from app.services.comparison_v2.level15 import ADAS_FIELDS, ADAS_LABELS_HE, GOVERNMENT_FACT_FIELDS

HIGHER_BETTER = "higher_better"
LOWER_BETTER = "lower_better"
PRESENCE_POSITIVE = "presence_positive"
DESCRIPTIVE = "descriptive"
CONTEXTUAL = "contextual"
NOT_CROSS_POWERTRAIN = "not_cross_powertrain_comparable"

STATUS_COMPARED = "compared"
STATUS_INSUFFICIENT = "insufficient_data"
STATUS_NOT_COMPARABLE = "not_comparable"
STATUS_DESCRIPTIVE = "descriptive"
STATUS_CONFLICT = "conflict"

# Category evidence states (not decisions).
EVIDENCE_NONE_COMPARABLE = "no_comparable_evidence"
EVIDENCE_CROSS_POWERTRAIN = "cross_powertrain_descriptive"

ALL = frozenset({"ev", "phev", "combustion", "unknown"})
PLUGIN = frozenset({"ev", "phev"})
COMBUSTION_LIKE = frozenset({"combustion", "phev"})

CATEGORIES: Tuple[str, ...] = (
    "safety",
    "performance",
    "efficiency",
    "electric_and_charging",
    "practicality",
    "towing_and_utility",
    "environment",
    "official_price_and_warranty",
    "equipment_and_convenience",
)

CATEGORY_LABELS_HE = {
    "safety": "בטיחות",
    "performance": "ביצועים",
    "efficiency": "יעילות וצריכה",
    "electric_and_charging": "חשמל וטעינה",
    "practicality": "פרקטיות ומרחב",
    "towing_and_utility": "גרירה ושימושיות",
    "environment": "סביבה ופליטות",
    "official_price_and_warranty": "מחיר ואחריות רשמיים",
    "equipment_and_convenience": "אבזור ונוחות שימוש",
    "overall": "הכרעה כוללת",
}

UNIT_LABELS_HE = {
    "hp": "כ״ס",
    "Nm": "ניוטון-מטר",
    "s": "שניות",
    "km/h": "קמ״ש",
    "L/100km": "ליטר ל־100 ק״מ",
    "kWh/100km": "קוט״ש ל־100 ק״מ",
    "kWh": "קוט״ש",
    "km": "ק״מ",
    "kW": "קילוואט",
    "min": "דקות",
    "%": "%",
    "mm": "מ״מ",
    "L": "ליטר",
    "kg": "ק״ג",
    "g/km": "גר׳ לק״מ",
    "mg/km": "מ״ג לק״מ",
    "ILS": "₪",
    "years": "שנים",
    "in": "אינץ׳",
    "count": "",
    "cc": "סמ״ק",
}


@dataclass(frozen=True)
class Metric:
    key: str
    label_he: str
    category: str
    kind: str
    source: str  # government | equipment | official | derived
    path: str
    unit: Optional[str] = None
    tie_abs: float = 0.0
    tie_rel: float = 0.0
    applies_to: FrozenSet[str] = ALL
    correlation_group: Optional[str] = None
    missing_behavior: str = "neutral"
    comparable_check: Optional[Callable[[List[Dict[str, Any]]], Optional[str]]] = None
    note_he: Optional[str] = None

    @property
    def direction(self) -> int:
        return {HIGHER_BETTER: 1, LOWER_BETTER: -1}.get(self.kind, 0)

    @property
    def applicability(self) -> List[str]:
        return sorted(self.applies_to)


def _same_range_standard(values: List[Dict[str, Any]]) -> Optional[str]:
    standards = {v.get("measurement_standard") for v in values}
    if len(standards) != 1 or None in standards:
        return "RANGE_STANDARD_MISMATCH"
    return None


def _same_charge_window(values: List[Dict[str, Any]]) -> Optional[str]:
    windows = {(v.get("dc_from"), v.get("dc_to")) for v in values}
    if len(windows) != 1 or any(None in w for w in windows) or any(v.get("comparable") is False for v in values):
        return "DC_CHARGE_WINDOW_MISMATCH"
    return None


def _israeli_current_price(values: List[Dict[str, Any]]) -> Optional[str]:
    for v in values:
        if v.get("market") != "IL" or v.get("currency") != "ILS":
            return "PRICE_MARKET_MISMATCH"
    return None


def _gov(key, label, category, kind, unit=None, **kw) -> Metric:
    return Metric(key=key, label_he=label, category=category, kind=kind, source="government",
                  path=f"government.facts.{key}", unit=unit, **kw)


def _off(key, category, kind, **kw) -> Metric:
    spec = FIELD_SPECS[key]
    kw.setdefault("applies_to", frozenset(spec.applies_to))
    return Metric(key=key, label_he=spec.label_he, category=category, kind=kind, source="official",
                  path=f"official_enrichment.facts.{key}", unit=spec.unit, **kw)


METRIC_REGISTRY: Tuple[Metric, ...] = (
    # --- safety (Level 1.5 is the base) ---
    _gov("safety_score", "ניקוד בטיחות (משרד התחבורה)", "safety", HIGHER_BETTER, tie_abs=0.5, correlation_group="gov_safety_rating"),
    _gov("safety_equipment_level", "רמת אבזור בטיחות", "safety", HIGHER_BETTER, correlation_group="gov_safety_rating"),
    Metric(key="adas_systems_count", label_he="מערכות עזר לנהג (מתוך 19)", category="safety", kind=HIGHER_BETTER,
           source="derived", path="government.equipment.*", unit="count", correlation_group="adas_equipment"),
    _gov("airbags", "כריות אוויר", "safety", HIGHER_BETTER, unit="count", correlation_group="passive_safety"),
    _gov("abs", "ABS", "safety", PRESENCE_POSITIVE, correlation_group="stability_basics"),
    _gov("esc", "בקרת יציבות (ESC)", "safety", PRESENCE_POSITIVE, correlation_group="stability_basics"),
    # --- performance ---
    _gov("horsepower", "הספק", "performance", HIGHER_BETTER, unit="hp", tie_rel=0.05, correlation_group="power_output"),
    _off("torque_nm", "performance", HIGHER_BETTER, tie_rel=0.05, correlation_group="power_output"),
    _off("acceleration_0_100_s", "performance", LOWER_BETTER, tie_abs=0.3, correlation_group="acceleration"),
    _off("top_speed_kmh", "performance", HIGHER_BETTER, tie_abs=5, correlation_group="top_speed"),
    _gov("drivetrain", "הנעה", "performance", DESCRIPTIVE),
    _off("transmission_type", "performance", DESCRIPTIVE),
    _off("transmission_gears", "performance", DESCRIPTIVE),
    # --- efficiency (never compared across units) ---
    _off("fuel_consumption_l_100km", "efficiency", LOWER_BETTER, tie_abs=0.3, correlation_group="fuel_use"),
    _off("energy_consumption_kwh_100km", "efficiency", LOWER_BETTER, tie_abs=0.5, correlation_group="energy_use"),
    # --- electric and charging ---
    _off("battery_capacity_net_kwh", "electric_and_charging", HIGHER_BETTER, tie_rel=0.03, correlation_group="battery_size"),
    _off("battery_capacity_kwh", "electric_and_charging", HIGHER_BETTER, tie_rel=0.03, correlation_group="battery_size"),
    _off("electric_range_km", "electric_and_charging", HIGHER_BETTER, tie_rel=0.03, correlation_group="electric_range",
         comparable_check=_same_range_standard),
    _off("dc_charging_power_kw", "electric_and_charging", HIGHER_BETTER, tie_rel=0.05, correlation_group="dc_charging"),
    _off("dc_charge_time_minutes", "electric_and_charging", LOWER_BETTER, tie_abs=2, correlation_group="dc_charging",
         comparable_check=_same_charge_window),
    _off("ac_charging_power_kw", "electric_and_charging", HIGHER_BETTER, tie_abs=0.5, correlation_group="ac_charging"),
    # --- practicality (bigger is not automatically better) ---
    _gov("seats", "מושבים", "practicality", CONTEXTUAL, unit="count"),
    _gov("doors", "דלתות", "practicality", CONTEXTUAL, unit="count"),
    _gov("body_style", "מרכב", "practicality", CONTEXTUAL),
    _off("cargo_volume_l", "practicality", HIGHER_BETTER, tie_rel=0.05, correlation_group="cargo"),
    _off("length_mm", "practicality", CONTEXTUAL),
    _off("width_mm", "practicality", CONTEXTUAL),
    _off("height_mm", "practicality", CONTEXTUAL),
    _off("wheelbase_mm", "practicality", CONTEXTUAL),
    _off("ground_clearance_mm", "practicality", CONTEXTUAL),
    _off("fuel_tank_l", "practicality", DESCRIPTIVE),
    # --- towing and utility ---
    _gov("towing_braked_kg", "כושר גרירה עם בלמים", "towing_and_utility", HIGHER_BETTER, unit="kg", tie_abs=50, correlation_group="towing"),
    _gov("towing_unbraked_kg", "כושר גרירה ללא בלמים", "towing_and_utility", HIGHER_BETTER, unit="kg", tie_abs=50, correlation_group="towing"),
    _gov("gross_weight_kg", "משקל כולל מותר", "towing_and_utility", DESCRIPTIVE, unit="kg",
         note_he="משקל כולל מותר (לא משקל עצמי) — אינו משמש לחישוב יחס הספק-משקל"),
    # --- environment (each measurement kept to its own standard) ---
    _gov("co2_wltp", "פליטת CO₂ (WLTP)", "environment", LOWER_BETTER, unit="g/km", tie_abs=5, correlation_group="co2_wltp"),
    _gov("co2_city", "פליטת CO₂ עירונית", "environment", LOWER_BETTER, unit="g/km", tie_abs=5, correlation_group="co2_city_highway"),
    _gov("co2_highway", "פליטת CO₂ בין-עירונית", "environment", LOWER_BETTER, unit="g/km", tie_abs=5, correlation_group="co2_city_highway"),
    _gov("pollution_group", "קבוצת זיהום", "environment", LOWER_BETTER, correlation_group="pollution_class"),
    _gov("green_index", "מדד ירוק", "environment", LOWER_BETTER, tie_abs=5, correlation_group="pollution_class"),
    _gov("nox_wltp", "פליטת NOx (WLTP)", "environment", LOWER_BETTER, unit="mg/km", tie_abs=2, correlation_group="tailpipe_other"),
    # --- official price and warranty (Israeli official only; warranty != reliability) ---
    _off("official_price_ils", "official_price_and_warranty", LOWER_BETTER, tie_rel=0.02, correlation_group="price",
         comparable_check=_israeli_current_price),
    _off("registration_fee_ils", "official_price_and_warranty", DESCRIPTIVE),
    _off("warranty_vehicle_years", "official_price_and_warranty", HIGHER_BETTER, correlation_group="vehicle_warranty",
         note_he="אחריות אינה מדד לאמינות"),
    _off("warranty_vehicle_km", "official_price_and_warranty", HIGHER_BETTER, correlation_group="vehicle_warranty"),
    _off("warranty_battery_years", "official_price_and_warranty", HIGHER_BETTER, correlation_group="battery_warranty"),
    _off("warranty_battery_km", "official_price_and_warranty", HIGHER_BETTER, correlation_group="battery_warranty"),
    # --- equipment (each feature is its own signal; it only counts when the
    #     user declared it must-have / nice-to-have — see composer) ---
    *(
        _off(key, "equipment_and_convenience", PRESENCE_POSITIVE, correlation_group=f"feature:{key}")
        for key in ("apple_carplay", "android_auto", "heated_front_seats", "ventilated_front_seats",
                    "power_front_seats", "panoramic_roof", "surround_view_camera", "premium_audio")
    ),
    _off("multimedia_screen_in", "equipment_and_convenience", DESCRIPTIVE),
    _off("wheel_size_in", "equipment_and_convenience", DESCRIPTIVE),
)

METRICS_BY_KEY = {m.key: m for m in METRIC_REGISTRY}

# Pairs that exist for one powertrain family only; across families they are
# explicitly flagged as not atomically comparable.
CROSS_POWERTRAIN_PAIRS = (("fuel_consumption_l_100km", "energy_consumption_kwh_100km"),)


class MetricRegistry:
    """Accessor so the registry can be extended/replaced without touching callers."""

    def __init__(self, metrics: Tuple[Metric, ...] = METRIC_REGISTRY):
        self.metrics = metrics

    def for_category(self, category: str) -> List[Metric]:
        return [m for m in self.metrics if m.category == category]


# ---------------------------------------------------------------------------
# value access / formatting
# ---------------------------------------------------------------------------
def _fmt_number(value: Any) -> str:
    if isinstance(value, bool):
        return "כן" if value else "לא"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        if value.is_integer():
            return f"{int(value):,}"
        return f"{value:,.1f}"
    return str(value)


DESCRIPTIVE_VALUE_LABELS_HE = {
    "awd": "הנעה כפולה (AWD)",
    "two_wheel_drive": "הנעה על ציר אחד (2WD)",
    "suv": "SUV",
    "sedan": "סדאן",
    "hatchback": "האצ'בק",
    "mpv": "רב-מושבי (MPV)",
    "coupe": "קופה",
    "automatic": "אוטומטית",
    "dual_clutch": "כפולת מצמדים",
    "cvt": "רציפה (CVT)",
    "e_cvt": "רציפה חשמלית (e-CVT)",
    "single_speed": "הילוך יחיד",
    "manual": "ידנית",
}


def format_value(metric: Metric, value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return DESCRIPTIVE_VALUE_LABELS_HE.get(value, value)
    unit = UNIT_LABELS_HE.get(metric.unit or "", metric.unit or "")
    text = _fmt_number(value)
    if metric.unit == "ILS":
        return f"₪{text}"
    return f"{text} {unit}".strip()


def _provenance(snapshot: Dict[str, Any], metric: Metric) -> Dict[str, Any]:
    if metric.source in ("government", "equipment", "derived"):
        prov = snapshot["government"]["provenance"]
        return {"source_level": prov["source_level"], "source_type": prov["source_type"], "source_title": prov["source_title"]}
    fact = (snapshot.get("official_enrichment") or {}).get("facts", {}).get(metric.key) or {}
    return {
        "source_level": fact.get("source_level"),
        "source_type": fact.get("source_type"),
        "source_title": fact.get("source_title"),
        "source_url": fact.get("source_url"),
        "source_market": fact.get("source_market"),
        "sources": fact.get("sources") or [],
        "measurement_standard": fact.get("measurement_standard"),
    }


def read_metric(snapshot: Dict[str, Any], metric: Metric) -> Dict[str, Any]:
    """Return {value, ...context}. value None == missing (never zero)."""
    family = snapshot["derived"]["powertrain_family"]
    if family not in metric.applies_to:
        return {"value": None, "applicable": False}
    if metric.source == "government":
        return {"value": snapshot["government"]["facts"].get(metric.key), "applicable": True}
    if metric.source == "derived" and metric.key == "adas_systems_count":
        equipment = snapshot["government"]["equipment"]
        values = [equipment.get(k) for k in ADAS_FIELDS]
        if any(v is None for v in values):
            return {"value": None, "applicable": True}
        return {"value": sum(1 for v in values if v), "applicable": True}
    official = snapshot.get("official_enrichment") or {}
    conflicted = {c.get("field") for c in official.get("conflicts") or []}
    if metric.key in conflicted:
        return {"value": None, "applicable": True, "conflict": True}
    fact = (official.get("facts") or {}).get(metric.key)
    if not fact or not fact.get("validated") or fact.get("variant_scope") != "variant":
        return {"value": None, "applicable": True}
    out = {
        "value": fact.get("value"),
        "applicable": True,
        "measurement_standard": fact.get("measurement_standard"),
        "market": fact.get("market") or fact.get("source_market"),
        "currency": fact.get("currency"),
        "comparable": fact.get("comparable", True),
    }
    if metric.key == "dc_charge_time_minutes":
        facts = official.get("facts") or {}
        out["dc_from"] = (facts.get("dc_charge_from_pct") or {}).get("value")
        out["dc_to"] = (facts.get("dc_charge_to_pct") or {}).get("value")
    return out


# ---------------------------------------------------------------------------
# atomic comparison
# ---------------------------------------------------------------------------
def _decide_numeric(metric: Metric, present: Dict[str, float]) -> Tuple[str, Optional[float]]:
    ordered = sorted(present.items(), key=lambda kv: kv[1] * metric.direction, reverse=True)
    (best_slot, best), (_, second) = ordered[0], ordered[1]
    margin = abs(float(best) - float(second))
    threshold = max(metric.tie_abs, metric.tie_rel * max(abs(float(best)), abs(float(second))))
    if margin <= threshold:
        return CHOICE_TIE, margin
    return best_slot, margin


def compare_metric(metric: Metric, snapshots: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    reads = {slot: read_metric(snap, metric) for slot, snap in snapshots.items()}
    values = {slot: r.get("value") for slot, r in reads.items()}
    result: Dict[str, Any] = {
        "metric": metric.key,
        "label_he": metric.label_he,
        "category": metric.category,
        "kind": metric.kind,
        "direction": metric.direction,
        "unit": metric.unit,
        "correlation_group": metric.correlation_group,
        "missing_behavior": metric.missing_behavior,
        "values": values,
        "display": {slot: format_value(metric, v) for slot, v in values.items()},
        "provenance": {slot: (_provenance(snapshots[slot], metric) if values[slot] is not None else None) for slot in snapshots},
        "missing": [slot for slot, r in reads.items() if r.get("applicable") and r.get("value") is None and not r.get("conflict")],
        "conflicted": [slot for slot, r in reads.items() if r.get("conflict")],
        "not_applicable": [slot for slot, r in reads.items() if not r.get("applicable")],
        "leader": None,
        "margin": None,
        "reason": None,
    }
    if metric.note_he:
        result["note_he"] = metric.note_he
    if metric.key == "adas_systems_count":
        result["details"] = _adas_differences(snapshots)

    applicable_slots = [s for s, r in reads.items() if r.get("applicable")]
    if metric.kind in (DESCRIPTIVE, CONTEXTUAL):
        result["status"] = STATUS_DESCRIPTIVE
        return result
    if len(applicable_slots) == 1 and len(snapshots) >= 2:
        # Applies to one powertrain family only (e.g. L/100km vs kWh/100km).
        result["status"] = STATUS_NOT_COMPARABLE
        result["kind"] = NOT_CROSS_POWERTRAIN
        result["reason"] = "NOT_CROSS_POWERTRAIN_COMPARABLE"
        return result
    if not applicable_slots:
        result["status"] = STATUS_NOT_COMPARABLE
        result["reason"] = "NOT_APPLICABLE"
        return result

    present = {s: reads[s] for s in applicable_slots if reads[s].get("value") is not None}
    if len(present) < 2:
        result["status"] = STATUS_CONFLICT if result["conflicted"] else STATUS_INSUFFICIENT
        return result
    if metric.comparable_check:
        reason = metric.comparable_check(list(present.values()))
        if reason:
            result["status"] = STATUS_NOT_COMPARABLE
            result["reason"] = reason
            return result

    if metric.kind == PRESENCE_POSITIVE:
        flags = {s: bool(r["value"]) for s, r in present.items()}
        if len(set(flags.values())) == 1:
            result["leader"] = CHOICE_TIE
        else:
            holders = [s for s, f in flags.items() if f]
            result["leader"] = holders[0] if len(holders) == 1 else CHOICE_TIE
        result["status"] = STATUS_COMPARED
        result["compared_slots"] = sorted(present)
        return result

    leader, margin = _decide_numeric(metric, {s: float(r["value"]) for s, r in present.items()})
    result["leader"] = leader
    result["margin"] = round(margin, 2) if margin is not None else None
    result["status"] = STATUS_COMPARED
    result["compared_slots"] = sorted(present)
    return result


def _adas_differences(snapshots: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    per_slot = {slot: snap["government"]["equipment"] for slot, snap in snapshots.items()}
    only: Dict[str, List[str]] = {slot: [] for slot in snapshots}
    for key in ADAS_FIELDS:
        vals = {slot: eq.get(key) for slot, eq in per_slot.items()}
        if any(v is None for v in vals.values()):
            continue
        have = [s for s, v in vals.items() if v]
        if have and len(have) < len(vals):
            for s in have:
                only[s].append(ADAS_LABELS_HE[key])
    return {"systems_only_in": only}


def build_group_results(atomic: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collapse correlated metrics into one line of evidence each."""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for res in atomic:
        if res.get("status") == STATUS_COMPARED and res.get("correlation_group"):
            groups.setdefault(res["correlation_group"], []).append(res)
    out = []
    for group, results in groups.items():
        leaders = {r["leader"] for r in results if r["leader"] and r["leader"] != CHOICE_TIE}
        if not leaders:
            lean = CHOICE_TIE
        elif len(leaders) == 1:
            lean = next(iter(leaders))
        else:
            lean = "mixed"
        out.append({"correlation_group": group, "metrics": [r["metric"] for r in results], "lean": lean})
    return out


def _slots_in_scope(metric: Metric, snapshots: Dict[str, Dict[str, Any]]) -> List[str]:
    return [slot for slot, snap in snapshots.items() if snap["derived"]["powertrain_family"] in metric.applies_to]


def _category_applicable(category: str, snapshots: Dict[str, Dict[str, Any]], metrics: List[Metric]) -> bool:
    """not_applicable only when the category is irrelevant to EVERY selected car."""
    return any(_slots_in_scope(m, snapshots) for m in metrics)


def _coverage_for(metrics: List[Metric], slot: str, snapshot: Dict[str, Any]) -> Dict[str, Any]:
    applicable = [m for m in metrics if m.kind not in (DESCRIPTIVE, CONTEXTUAL) and snapshot["derived"]["powertrain_family"] in m.applies_to]
    present = [m for m in applicable if read_metric(snapshot, m).get("value") is not None]
    ratio = (len(present) / len(applicable)) if applicable else None
    return {"available": len(present), "applicable": len(applicable), "ratio": round(ratio, 2) if ratio is not None else None}


def build_category_evidence(category: str, snapshots: Dict[str, Dict[str, Any]], registry: Optional[MetricRegistry] = None) -> Dict[str, Any]:
    registry = registry or MetricRegistry()
    metrics = registry.for_category(category)
    applicable = _category_applicable(category, snapshots, metrics)
    atomic = [compare_metric(m, snapshots) for m in metrics]
    compared = [r for r in atomic if r["status"] == STATUS_COMPARED]
    contextual = [
        {"metric": r["metric"], "label_he": r["label_he"], "display": r["display"], "values": r["values"], "note_he": r.get("note_he")}
        for r in atomic
        if r["status"] == STATUS_DESCRIPTIVE and any(v is not None for v in r["values"].values())
    ]
    not_comparable = [
        {"metric": r["metric"], "label_he": r["label_he"], "reason": r["reason"], "display": r["display"]}
        for r in atomic
        if r["status"] == STATUS_NOT_COMPARABLE and any(v is not None for v in r["values"].values())
    ]
    missing = sorted({r["metric"] for r in atomic if r["missing"] and r["status"] != STATUS_DESCRIPTIVE})
    conflicted = sorted({r["metric"] for r in atomic if r["conflicted"]})
    group_results = build_group_results(atomic)
    differing_context = [
        c for c in contextual
        if len({repr(v) for v in c["values"].values()}) > 1 and all(v is not None for v in c["values"].values())
    ]
    # Evidence state only. The engine never decides a category: every
    # applicable category goes to JEV (which can answer insufficient_evidence).
    judged = [m for m in metrics if m.kind not in (DESCRIPTIVE, CONTEXTUAL)]
    shared_metric = any(len(_slots_in_scope(m, snapshots)) >= 2 for m in judged)
    scope_slots = sorted({slot for m in judged for slot in _slots_in_scope(m, snapshots)})
    cross_powertrain = any(r.get("reason") == "NOT_CROSS_POWERTRAIN_COMPARABLE" for r in atomic)
    status = "ready"
    if not applicable:
        status = NOT_APPLICABLE
    elif not compared and not differing_context and cross_powertrain and not shared_metric:
        # Relevant to only one selected powertrain (e.g. EV charging vs a
        # petrol car): values are shown descriptively, no direct winner.
        status = EVIDENCE_CROSS_POWERTRAIN
    elif not compared and not differing_context:
        status = EVIDENCE_NONE_COMPARABLE
    return {
        "category": category,
        "label_he": CATEGORY_LABELS_HE[category],
        "applicable": applicable,
        "status": status,
        "atomic_results": atomic,
        "group_results": group_results,
        "contextual_facts": contextual,
        "not_comparable": not_comparable,
        "available_metrics": [r["metric"] for r in compared],
        "missing_metrics": missing,
        "conflicted_metrics": conflicted,
        "coverage": {slot: _coverage_for(metrics, slot, snap) for slot, snap in snapshots.items()},
        "scope_slots": scope_slots,
    }


# ---------------------------------------------------------------------------
# coverage
# ---------------------------------------------------------------------------
def coverage_label_he(ratio: Optional[float]) -> str:
    if ratio is None:
        return "לא רלוונטי"
    if ratio >= 0.75:
        return "גבוה"
    if ratio >= 0.4:
        return "בינוני"
    if ratio > 0:
        return "נמוך"
    return "אין"


def vehicle_coverage(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    gov_total = len(GOVERNMENT_FACT_FIELDS) + len(ADAS_FIELDS)
    facts = snapshot["government"]["facts"]
    equipment = snapshot["government"]["equipment"]
    gov_present = sum(1 for v in facts.values() if v is not None) + sum(1 for v in equipment.values() if v is not None)
    family = snapshot["derived"]["powertrain_family"]
    official_applicable = [k for k, s in FIELD_SPECS.items() if applies_to_family(s, family)]
    official_facts = (snapshot.get("official_enrichment") or {}).get("facts") or {}
    official_present = sum(1 for k in official_applicable if k in official_facts)
    gov_ratio = gov_present / gov_total if gov_total else None
    off_ratio = official_present / len(official_applicable) if official_applicable else None
    return {
        "government": {"present": gov_present, "total": gov_total, "ratio": round(gov_ratio, 2), "label_he": coverage_label_he(gov_ratio)},
        "official": {
            "present": official_present,
            "total": len(official_applicable),
            "ratio": round(off_ratio, 2) if off_ratio is not None else None,
            "label_he": coverage_label_he(off_ratio),
            "enrichment_status": (snapshot.get("official_enrichment") or {}).get("status"),
        },
    }


def run_deterministic_comparison(snapshots: Dict[str, Dict[str, Any]], registry: Optional[MetricRegistry] = None) -> Dict[str, Any]:
    registry = registry or MetricRegistry()
    categories = {c: build_category_evidence(c, snapshots, registry) for c in CATEGORIES}
    return {
        "categories": categories,
        "coverage": {slot: vehicle_coverage(snap) for slot, snap in snapshots.items()},
        "cross_powertrain": sorted({s["derived"]["powertrain_family"] for s in snapshots.values()}) if len({s["derived"]["powertrain_family"] for s in snapshots.values()}) > 1 else [],
    }


# ---------------------------------------------------------------------------
# pairwise evidence (V2/2): code owns the direction of every objective signal
# ---------------------------------------------------------------------------
DIRECTION_MIXED = "mixed"


def pair_key(a: str, b: str) -> str:
    return f"{a}__{b}"


def vehicle_pairs(slots: List[str]) -> List[Tuple[str, str]]:
    ordered = sorted(slots)
    return [(ordered[i], ordered[j]) for i in range(len(ordered)) for j in range(i + 1, len(ordered))]


def build_pairwise_evidence(snapshots: Dict[str, Dict[str, Any]], registry: Optional[MetricRegistry] = None) -> Dict[str, Dict[str, Any]]:
    """For every vehicle pair and correlation group: deterministic direction.

    direction: one slot (that vehicle leads), ``tie``, ``mixed`` (metrics in
    the group disagree) or ``none`` (no comparable validated value). Missing,
    conflicted and not-comparable metrics never produce a direction.
    """
    registry = registry or MetricRegistry()
    grouped: Dict[str, List[Metric]] = {}
    for metric in registry.metrics:
        if metric.correlation_group and metric.kind in (HIGHER_BETTER, LOWER_BETTER, PRESENCE_POSITIVE):
            grouped.setdefault(metric.correlation_group, []).append(metric)
    out: Dict[str, Dict[str, Any]] = {}
    for a, b in vehicle_pairs(list(snapshots)):
        sub = {a: snapshots[a], b: snapshots[b]}
        groups: Dict[str, Any] = {}
        for group, metrics in grouped.items():
            results = [compare_metric(m, sub) for m in metrics]
            compared = [r for r in results if r["status"] == STATUS_COMPARED]
            leaders = {r["leader"] for r in compared if r["leader"] not in (None, CHOICE_TIE)}
            if not compared:
                direction = "none"
            elif not leaders:
                direction = CHOICE_TIE
            elif len(leaders) == 1:
                direction = next(iter(leaders))
            else:
                direction = DIRECTION_MIXED
            groups[group] = {
                "category": metrics[0].category,
                "direction": direction,
                "metrics": [
                    {
                        "metric": r["metric"],
                        "label_he": r["label_he"],
                        "unit": r["unit"],
                        "kind": r["kind"],
                        "values": r["values"],
                        "display": r["display"],
                        "leader": r["leader"],
                        "status": r["status"],
                        "measurement_standard": {
                            s: (r["provenance"].get(s) or {}).get("measurement_standard") for s in (a, b)
                        } if r["metric"] == "electric_range_km" else None,
                    }
                    for r in results
                    if r["status"] == STATUS_COMPARED
                ],
                "excluded": [
                    {"metric": r["metric"], "status": r["status"], "reason": r.get("reason")}
                    for r in results
                    if r["status"] != STATUS_COMPARED
                ],
            }
        out[pair_key(a, b)] = {"pair": [a, b], "groups": groups}
    return out
