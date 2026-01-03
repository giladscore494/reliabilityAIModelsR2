"""Compact coefficients for client-side ownership simulator."""

from __future__ import annotations

from typing import Any, Dict, Mapping


def build_sim_model(
    usage_profile: Mapping[str, Any],
    micro_reliability: Mapping[str, Any],
    timeline_plan: Mapping[str, Any],
) -> Dict[str, Any]:
    defaults = {
        "annual_km": int(usage_profile.get("annual_km", 15000) or 15000),
        "city_pct": int(usage_profile.get("city_pct", 50) or 50),
        "keep_years": 3,
        "driver_style": usage_profile.get("driver_style", "normal"),
    }

    totals = timeline_plan.get("totals_by_phase") or {}
    total_min = sum((totals.get(k) or [0, 0])[0] for k in totals.keys())
    total_max = sum((totals.get(k) or [0, 0])[1] for k in totals.keys())
    projected_km = (timeline_plan.get("projected_km") or {}).get("m36", defaults["annual_km"] * 3) or (
        defaults["annual_km"] * 3
    )

    per_km_min = round(total_min / projected_km, 2) if projected_km else 0
    per_km_max = round(total_max / projected_km, 2) if projected_km else 0

    delta = abs(float(micro_reliability.get("delta", 0) or 0))
    risk_index = round(max(0.0, min(20.0, (delta * 1.5))), 2)

    cost_buckets = {
        "maintenance_per_km_ils": [per_km_min, per_km_max],
        "risk_repairs_yearly_ils": [int(300 + risk_index * 20), int(800 + risk_index * 40)],
        "tires_per_km_ils": [0.08, 0.14],
        "brakes_city_multiplier": round(1 + (defaults["city_pct"] / 100) * 0.4, 2),
        "heat_ac_multiplier": 1.2 if usage_profile.get("climate") == "south_hot" else 1.0,
    }

    notes = [
        "הטווחים הם אומדן בלבד ומשתנים לפי מחירי חלקים ועבודה.",
        "התאמות סיטי/חום יחולו רק בסימולציה בצד הלקוח.",
    ]

    return {
        "defaults": defaults,
        "cost_buckets": cost_buckets,
        "risk_index": risk_index,
        "notes": notes,
    }
