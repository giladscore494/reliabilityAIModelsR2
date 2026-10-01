# -*- coding: utf-8 -*-
"""HardConstraintEvaluator: user-declared requirements checked in code.

Each constraint returns ``pass`` / ``fail`` / ``unknown`` / ``not_applicable``
per vehicle. Missing data is ``unknown`` — never ``fail``. JEV is never asked
whether a car fits the budget, has enough seats, can tow, has AWD or has a
required feature.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.comparison_v2.buyer_profile import FEATURE_LABELS_HE, FEATURE_SOURCES, MODE_GENERAL

PASS, FAIL, UNKNOWN, NOT_APPLICABLE = "pass", "fail", "unknown", "not_applicable"


def _official_fact(snapshot: Dict[str, Any], key: str) -> Optional[Dict[str, Any]]:
    official = snapshot.get("official_enrichment") or {}
    if key in {c.get("field") for c in official.get("conflicts") or []}:
        return None
    fact = (official.get("facts") or {}).get(key)
    if not fact or not fact.get("validated") or fact.get("variant_scope") != "variant":
        return None
    return fact


def _israeli_price(snapshot: Dict[str, Any]) -> Optional[float]:
    fact = _official_fact(snapshot, "official_price_ils")
    if not fact or fact.get("currency") != "ILS" or (fact.get("market") or fact.get("source_market")) != "IL":
        return None
    return fact.get("value")


def feature_value(snapshot: Dict[str, Any], feature: str) -> Optional[bool]:
    """Validated True/False, or None when unknown."""
    source, key = FEATURE_SOURCES[feature]
    if source == "government":
        value = snapshot["government"]["equipment"].get(key)
        return None if value is None else bool(value)
    fact = _official_fact(snapshot, key)
    if not fact or not isinstance(fact.get("value"), bool):
        return None
    return fact["value"]


def _cmp_at_least(value: Optional[float], required: float) -> str:
    if value is None:
        return UNKNOWN
    return PASS if value >= required else FAIL


class HardConstraintEvaluator:
    def evaluate(self, profile: Dict[str, Any], snapshots: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        constraints: List[Dict[str, Any]] = []
        if profile.get("mode") != MODE_GENERAL:
            budget = profile.get("budget_max_ils")
            if budget:
                per_car = {}
                for slot, snap in snapshots.items():
                    price = _israeli_price(snap)
                    per_car[slot] = {
                        "status": UNKNOWN if price is None else (PASS if price <= budget else FAIL),
                        "value": price,
                        "display": f"₪{int(price):,}" if price is not None else "אין מחיר ישראלי רשמי מאומת",
                    }
                constraints.append({"key": "budget", "label_he": "תקציב", "requirement_he": f"עד ₪{budget:,}", "per_car": per_car})

            seats_needed = profile.get("regular_passengers")
            if seats_needed:
                per_car = {}
                for slot, snap in snapshots.items():
                    seats = snap["government"]["facts"].get("seats")
                    per_car[slot] = {"status": _cmp_at_least(seats, seats_needed), "value": seats,
                                     "display": f"{seats} מושבים" if seats is not None else "מספר מושבים לא ידוע"}
                constraints.append({"key": "passengers", "label_he": "מספר נוסעים", "requirement_he": f"לפחות {seats_needed} מושבים", "per_car": per_car})

            towing_needed = profile.get("towing_braked_required_kg")
            if towing_needed:
                per_car = {}
                for slot, snap in snapshots.items():
                    tow = snap["government"]["facts"].get("towing_braked_kg")
                    per_car[slot] = {"status": _cmp_at_least(tow, towing_needed), "value": tow,
                                     "display": f"{tow:,} ק״ג" if tow is not None else "כושר גרירה לא ידוע"}
                constraints.append({"key": "towing", "label_he": "גרירה", "requirement_he": f"גרירה עם בלמים של {towing_needed:,} ק״ג לפחות", "per_car": per_car})

            if profile.get("awd_requirement") == "required":
                per_car = {}
                for slot, snap in snapshots.items():
                    drive = snap["government"]["facts"].get("drivetrain")
                    status = UNKNOWN if drive is None else (PASS if drive == "awd" else FAIL)
                    per_car[slot] = {"status": status, "value": drive,
                                     "display": {"awd": "הנעה כפולה", "two_wheel_drive": "הנעה על ציר אחד"}.get(drive, "הנעה לא ידועה")}
                constraints.append({"key": "awd", "label_he": "הנעה כפולה", "requirement_he": "חובה", "per_car": per_car})

            for feature in profile.get("must_have_features") or []:
                per_car = {}
                for slot, snap in snapshots.items():
                    value = feature_value(snap, feature)
                    status = UNKNOWN if value is None else (PASS if value else FAIL)
                    per_car[slot] = {"status": status, "value": value,
                                     "display": {True: "קיים", False: "לא קיים"}.get(value, "לא נמצא מקור רשמי מאומת")}
                constraints.append({"key": f"feature:{feature}", "label_he": FEATURE_LABELS_HE[feature], "requirement_he": "אבזור חובה", "per_car": per_car})

        failing = {slot for c in constraints for slot, r in c["per_car"].items() if r["status"] == FAIL}
        unknown = {slot for c in constraints for slot, r in c["per_car"].items() if r["status"] == UNKNOWN}
        slots = list(snapshots)
        eligible = [s for s in slots if s not in failing]
        return {
            "constraints": constraints,
            "failing_slots": sorted(failing),
            "slots_with_unknown": sorted(unknown),
            "eligible_slots": eligible,
            "per_car_status": {
                s: FAIL if s in failing else (UNKNOWN if s in unknown else (PASS if constraints else NOT_APPLICABLE))
                for s in slots
            },
        }

    @staticmethod
    def compact_for_jev(result: Dict[str, Any]) -> Dict[str, Any]:
        """Statuses only (no prices/labels) so JEV can see e.g. a seats failure as context."""
        return {c["key"]: {slot: r["status"] for slot, r in c["per_car"].items()} for c in result["constraints"]}
