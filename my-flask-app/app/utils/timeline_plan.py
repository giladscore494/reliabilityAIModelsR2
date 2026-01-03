"""Deterministic 36-month maintenance timeline."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Mapping, Tuple

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def _load_json(filename: str) -> list | dict:
    path = os.path.join(DATA_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


_MAINTENANCE_SCHEDULE = _load_json("maintenance_schedule.json")
_COST_RANGES = _load_json("cost_ranges_il.json")


def _estimate_current_km(mileage_range: str | None) -> int:
    if not mileage_range:
        return 0
    digits = [int(x.replace(",", "").replace(" ", "")) for x in mileage_range.split() if x.replace(",", "").replace(" ", "").isdigit()]
    if not digits and "-" in mileage_range:
        parts = mileage_range.replace("ק\"מ", "").replace(",", "").split("-")
        try:
            nums = [int(p.strip()) for p in parts if p.strip()]
            if len(nums) == 2:
                return int(sum(nums) / 2)
        except Exception:
            return 0
    if digits:
        return max(digits)
    return 0


def _project_km(current_km: int, annual_km: int, month: int) -> int:
    return int(current_km + (annual_km * (month / 12.0)))


def _risk_level_map(top_risks: List[Mapping[str, Any]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for r in top_risks:
        sub = str(r.get("subsystem") or "").strip().lower()
        level = str(r.get("level") or "").strip().lower()
        if sub:
            out[sub] = level
    return out


def _phase_for_month(month: int) -> Tuple[List[int], str]:
    if month <= 3:
        return [0, 3], "Do now"
    if month <= 12:
        return [3, 12], "Watch"
    return [12, 36], "Plan ahead"


def _cost_for_task(task_name: str) -> Tuple[int, int]:
    rng = _COST_RANGES.get(task_name) or [0, 0]
    if isinstance(rng, list) and len(rng) >= 2:
        try:
            return int(rng[0]), int(rng[1])
        except Exception:
            return 0, 0
    return 0, 0


def build_timeline_plan(
    usage_profile: Mapping[str, Any],
    micro_reliability: Mapping[str, Any],
    base_report: Mapping[str, Any],
) -> Dict[str, Any]:
    annual_km = int(usage_profile.get("annual_km", 15000) or 0)
    mileage_range = base_report.get("mileage_range") or base_report.get("mileage_km") or ""
    current_km = _estimate_current_km(str(mileage_range))
    risk_levels = _risk_level_map(micro_reliability.get("top_risks") or [])

    actions_by_phase: Dict[str, List[Dict[str, Any]]] = {"0_3": [], "3_12": [], "12_36": []}

    for task in _MAINTENANCE_SCHEDULE:
        name = task.get("name", "")
        subsystem = task.get("subsystem") or ""
        interval_months = task.get("interval_months")
        interval_km = task.get("interval_km")

        due_months: List[int] = []
        if interval_months:
            try:
                interval_months = int(interval_months)
                if interval_months > 0:
                    due_months.append(interval_months)
            except Exception:
                pass
        if interval_km:
            try:
                interval_km_val = int(interval_km)
                if annual_km > 0 and interval_km_val > current_km:
                    months_by_km = max(0, int(((interval_km_val - current_km) / annual_km) * 12))
                    due_months.append(months_by_km)
            except Exception:
                pass

        if not due_months:
            continue
        due = min(max(due_months), 36)

        # Risk-based pull-in
        level = risk_levels.get(subsystem)
        if level == "high":
            due = max(0, due - 3)
        elif level == "medium":
            due = max(0, due - 1)

        phase_range, phase_title = _phase_for_month(due)
        key = "0_3" if phase_range == [0, 3] else ("3_12" if phase_range == [3, 12] else "12_36")
        min_cost, max_cost = _cost_for_task(name)
        reason = task.get("notes") or "תזמון מחזורי לפי ק״מ/זמן."
        if level == "high":
            reason = f"{reason} (מוקדם בגלל סיכון גבוה במכלול {subsystem})."
        action = {
            "name": name,
            "subsystem": subsystem,
            "phase_title": phase_title,
            "month_target": due,
            "reason": reason,
            "cost_ils": [min_cost, max_cost],
        }
        actions_by_phase[key].append(action)

    totals_by_phase: Dict[str, List[int]] = {}
    for key, actions in actions_by_phase.items():
        total_min = sum(int(a.get("cost_ils", [0, 0])[0]) for a in actions)
        total_max = sum(int(a.get("cost_ils", [0, 0])[1]) for a in actions)
        totals_by_phase[key] = [total_min, total_max]

    phases = []
    for key, title in (("0_3", "Do now"), ("3_12", "Watch"), ("12_36", "Plan ahead")):
        start, end = (0, 3) if key == "0_3" else ((3, 12) if key == "3_12" else (12, 36))
        phases.append(
            {
                "month_range": [start, end],
                "title": title,
                "actions": actions_by_phase.get(key, []),
                "total_ils": totals_by_phase.get(key, [0, 0]),
            }
        )

    return {
        "horizon_months": 36,
        "phases": phases,
        "totals_by_phase": totals_by_phase,
        "projected_km": {
            "current": current_km,
            "m3": _project_km(current_km, annual_km, 3),
            "m12": _project_km(current_km, annual_km, 12),
            "m24": _project_km(current_km, annual_km, 24),
            "m36": _project_km(current_km, annual_km, 36),
        },
    }
