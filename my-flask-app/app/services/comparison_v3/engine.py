# -*- coding: utf-8 -*-
"""V3 deterministic engine: pairwise evidence from the rows, hard constraints and dimension weights.

Everything reads the rows of ``metrics.comparable_rows`` (the row rule): a metric that is not a row for every
selected car never produces a direction, a constraint value or a JEV state value. Display-only and history rows never
produce a direction.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

from app.services.comparison_v3.buyer_profile import FEATURE_LABELS_HE, FEATURE_SOURCES, MODE_GENERAL
from app.services.comparison_v3.contracts import CHOICE_TIE
from app.services.comparison_v3.metrics import GROUP_DIMENSION, METRICS_BY_KEY, leader_of
from app.services.comparison_v3.snapshot import fact_value

DIRECTION_MIXED = "mixed"
PASS, FAIL, UNKNOWN, NOT_APPLICABLE = "pass", "fail", "unknown", "not_applicable"

# ---- provisional composition constants (uncalibrated; see docs/COMPARISON_V3.md) ----
SCORE_MAX = 4.0
MIN_EFFECTIVE_WEIGHT_COVERAGE = 0.25
PRACTICAL_TIE_MARGIN = 0.05
STRENGTH_CLEAR = 0.35
STRENGTH_MODERATE = 0.15
TOWING_WEIGHT_WHEN_REQUIRED = 4
NON_PLUGIN_CHARGING_FIT = 0.5
AWD_PREFERENCE_STRENGTH = 0.5
NICE_TO_HAVE_STRENGTH = 0.5
CATEGORY_NEGLIGIBLE_CONTRIBUTION = 0.01

DIMENSIONS: Tuple[str, ...] = ("purchase_price", "safety", "performance", "efficiency_environment", "practicality",
                               "ev_convenience", "towing")


def pair_key(a: str, b: str) -> str:
    return f"{a}__{b}"


def vehicle_pairs(slots: Iterable[str]) -> List[Tuple[str, str]]:
    ordered = sorted(slots)
    return [(ordered[i], ordered[j]) for i in range(len(ordered)) for j in range(i + 1, len(ordered))]


def dimension_weights(profile: Dict[str, Any], families: Iterable[str]) -> Dict[str, int]:
    """User weight per dimension. ev_convenience only with a plug-in car; towing only with a towing requirement."""
    priorities = profile.get("priorities") or {}
    has_plugin = any(f in ("ev", "phev") for f in families)
    out = {}
    for dim in DIMENSIONS:
        if dim == "towing":
            out[dim] = TOWING_WEIGHT_WHEN_REQUIRED if profile.get("towing_braked_required_kg") else 0
        elif dim == "ev_convenience":
            out[dim] = int(priorities.get(dim) or 0) if has_plugin else 0
        else:
            out[dim] = int(priorities.get(dim) or 0)
    return out


def build_pairwise_evidence(rows: List[Dict[str, Any]], slots: List[str]) -> Dict[str, Dict[str, Any]]:
    """Per vehicle pair and correlation group: the code direction (slot / tie / mixed) from the scored rows only."""
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        if not row["display_only"] and row.get("group") in GROUP_DIMENSION:
            grouped.setdefault(row["group"], []).append(row)
    out: Dict[str, Dict[str, Any]] = {}
    for a, b in vehicle_pairs(slots):
        groups: Dict[str, Any] = {}
        for group, group_rows in grouped.items():
            metrics = []
            for row in group_rows:
                values = {a: row["values"][a], b: row["values"][b]}
                metrics.append({"metric": row["row_id"], "label_he": row["label_he"], "unit": row["unit"],
                                "kind": row["kind"], "standard": row.get("standard"), "values": values,
                                "leader": leader_of(METRICS_BY_KEY[row["row_id"]], values)})
            leaders = {m["leader"] for m in metrics if m["leader"] not in (None, CHOICE_TIE)}
            direction = CHOICE_TIE if not leaders else (next(iter(leaders)) if len(leaders) == 1 else DIRECTION_MIXED)
            groups[group] = {"category": group_rows[0]["category"], "dimension": GROUP_DIMENSION[group],
                             "direction": direction, "metrics": metrics}
        out[pair_key(a, b)] = {"pair": [a, b], "groups": groups}
    return out


# ---------------------------------------------------------------------------
# hard constraints (pass / fail / unknown; missing data is unknown, never fail)
# ---------------------------------------------------------------------------
def feature_value(snapshot: Dict[str, Any], feature: str):
    _, flag = FEATURE_SOURCES[feature]
    value = (snapshot.get("equipment") or {}).get(flag)
    return None if value is None else bool(value)


def _at_least(value, required) -> str:
    if value is None:
        return UNKNOWN
    return PASS if value >= required else FAIL


def evaluate_constraints(profile: Dict[str, Any], snapshots: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    constraints: List[Dict[str, Any]] = []
    if profile.get("mode") != MODE_GENERAL:
        budget = profile.get("budget_max_ils")
        if budget:
            per_car = {}
            for slot, snap in snapshots.items():
                price = fact_value(snap, "asking_price_ils")    # required for every car (validated earlier)
                per_car[slot] = {"status": UNKNOWN if price is None else (PASS if price <= budget else FAIL),
                                 "value": price, "display": f"₪{int(price):,}" if price is not None else "מחיר לא הוזן"}
            constraints.append({"key": "budget", "label_he": "תקציב", "requirement_he": f"עד ₪{budget:,}",
                                "per_car": per_car})
        seats_needed = profile.get("regular_passengers")
        if seats_needed:
            per_car = {}
            for slot, snap in snapshots.items():
                seats = fact_value(snap, "seats")
                per_car[slot] = {"status": _at_least(seats, seats_needed), "value": seats,
                                 "display": f"{seats} מושבים" if seats is not None else "מספר מושבים לא ידוע"}
            constraints.append({"key": "passengers", "label_he": "מספר נוסעים",
                                "requirement_he": f"לפחות {seats_needed} מושבים", "per_car": per_car})
        towing_needed = profile.get("towing_braked_required_kg")
        if towing_needed:
            per_car = {}
            for slot, snap in snapshots.items():
                tow = fact_value(snap, "towing_braked_kg")
                per_car[slot] = {"status": _at_least(tow, towing_needed), "value": tow,
                                 "display": f"{tow:,} ק״ג" if tow is not None else "כושר גרירה לא ידוע"}
            constraints.append({"key": "towing", "label_he": "גרירה",
                                "requirement_he": f"גרירה עם בלמים של {towing_needed:,} ק״ג לפחות", "per_car": per_car})
        if profile.get("awd_requirement") == "required":
            per_car = {}
            for slot, snap in snapshots.items():
                drive = fact_value(snap, "drivetrain")
                awd = drive in ("awd", "four_wheel_drive")
                per_car[slot] = {"status": UNKNOWN if drive is None else (PASS if awd else FAIL), "value": drive,
                                 "display": "הנעה כפולה" if awd else ("הנעה על ציר אחד" if drive else "הנעה לא ידועה")}
            constraints.append({"key": "awd", "label_he": "הנעה כפולה", "requirement_he": "חובה", "per_car": per_car})
        for feature in profile.get("must_have_features") or []:
            per_car = {}
            for slot, snap in snapshots.items():
                value = feature_value(snap, feature)
                per_car[slot] = {"status": UNKNOWN if value is None else (PASS if value else FAIL), "value": value,
                                 "display": {True: "קיים", False: "לא קיים"}.get(value, "לא דווח למשרד התחבורה")}
            constraints.append({"key": f"feature:{feature}", "label_he": FEATURE_LABELS_HE[feature],
                                "requirement_he": "אבזור חובה", "per_car": per_car})
    failing = {s for c in constraints for s, r in c["per_car"].items() if r["status"] == FAIL}
    unknown = {s for c in constraints for s, r in c["per_car"].items() if r["status"] == UNKNOWN}
    slots = list(snapshots)
    return {
        "constraints": constraints,
        "failing_slots": sorted(failing),
        "slots_with_unknown": sorted(unknown),
        "eligible_slots": [s for s in slots if s not in failing],
        "per_car_status": {s: FAIL if s in failing else (UNKNOWN if s in unknown else (PASS if constraints else NOT_APPLICABLE))
                           for s in slots},
    }


def compact_constraints_for_jev(result: Dict[str, Any]) -> Dict[str, Any]:
    return {c["key"]: {slot: r["status"] for slot, r in c["per_car"].items()} for c in result["constraints"]}
