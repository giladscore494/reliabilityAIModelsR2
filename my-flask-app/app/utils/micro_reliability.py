"""Deterministic micro reliability adjustments based on usage profile."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Tuple

SUBSYSTEMS = [
    "engine_cooling",
    "engine_oil",
    "transmission",
    "suspension",
    "brakes",
    "battery_electrical",
    "ac",
    "tires",
    "hybrid_system",
]


def _extract_base_score(base_report: Mapping[str, Any]) -> float:
    try:
        val = float(base_report.get("base_score_calculated"))
        return max(0.0, min(100.0, val))
    except Exception:
        try:
            val = float(base_report.get("overall_score", 0))
            return max(0.0, min(100.0, val))
        except Exception:
            return 70.0


def _baseline_risk_vector(base_score: float) -> Dict[str, float]:
    # Higher base score = lower baseline risk factor
    base_factor = 1.0 + max(0.0, (80.0 - base_score) / 25.0)
    return {sub: base_factor for sub in SUBSYSTEMS}


def _usage_multipliers(usage_profile: Mapping[str, Any]) -> Tuple[Dict[str, float], Dict[str, str]]:
    multipliers = {sub: 1.0 for sub in SUBSYSTEMS}
    reasons: Dict[str, str] = {}

    city_pct = usage_profile.get("city_pct", 50) or 0
    if city_pct >= 70:
        for sub in ("brakes", "suspension", "battery_electrical"):
            multipliers[sub] += 0.35
            reasons[sub] = "נסיעה עירונית מרובה מגבירה שחיקה."
    elif city_pct >= 40:
        for sub in ("brakes", "suspension"):
            multipliers[sub] += 0.2
            reasons[sub] = "נסיעה עירונית מגבירה עצירות ותאוצות."

    terrain = (usage_profile.get("terrain") or "mixed").lower()
    if terrain == "hilly":
        for sub in ("brakes", "engine_cooling", "transmission"):
            multipliers[sub] += 0.3
            reasons[sub] = "נהיגה בשיפועים מעמיסה על בלמים, קירור וגיר."

    climate = (usage_profile.get("climate") or "center").lower()
    parking = (usage_profile.get("parking") or "outdoor").lower()
    if climate == "south_hot":
        for sub in ("ac", "battery_electrical", "engine_cooling"):
            multipliers[sub] += 0.25
            reasons[sub] = "חום קיצוני מגביר עומס על מזגן, מצבר וקירור."
        if parking == "outdoor":
            for sub in ("ac", "battery_electrical"):
                multipliers[sub] += 0.1
                reasons[sub] = "חניה בחוץ בשמש מחמירה בלאי."

    driver_style = (usage_profile.get("driver_style") or "normal").lower()
    if driver_style == "aggressive":
        for sub in ("tires", "brakes", "transmission"):
            multipliers[sub] += 0.3
            reasons[sub] = "נהיגה אגרסיבית מגבירה שחיקה."
    elif driver_style == "calm":
        for sub in ("tires", "brakes"):
            multipliers[sub] -= 0.05

    load = (usage_profile.get("load") or "family").lower()
    if load == "heavy":
        for sub in ("suspension", "brakes", "tires"):
            multipliers[sub] += 0.2
            reasons[sub] = "משקל גבוה מגביר בלאי מתלים/בלמים/צמיגים."

    annual_km = usage_profile.get("annual_km", 15000) or 0
    if annual_km >= 30000:
        for sub in SUBSYSTEMS:
            multipliers[sub] += 0.25
        reasons.setdefault("engine_oil", "קילומטראז׳ גבוה מצריך טיפולים תכופים יותר.")
    elif annual_km >= 20000:
        for sub in SUBSYSTEMS:
            multipliers[sub] += 0.1
        reasons.setdefault("engine_oil", "קילומטראז׳ בינוני-גבוה מגביר בלאי מצטבר.")

    return multipliers, reasons


def compute_micro_reliability(base_report: Mapping[str, Any], usage_profile: Mapping[str, Any]) -> Dict[str, Any]:
    base_score = _extract_base_score(base_report)
    baseline = _baseline_risk_vector(base_score)
    multipliers, reasons = _usage_multipliers(usage_profile)

    combined: Dict[str, float] = {}
    for sub in SUBSYSTEMS:
        combined[sub] = max(0.5, baseline.get(sub, 1.0) * multipliers.get(sub, 1.0))

    risk_index = sum(combined.values()) / float(len(SUBSYSTEMS))
    adjusted_score = max(0.0, min(100.0, base_score - (risk_index * 3.0)))
    delta = round(adjusted_score - base_score, 1)

    quick_actions = [
        "בצע בדיקות בלמים ומתלים מוקדם יותר אם יש רעידות/חריקות.",
        "בדוק מצבר ומערכת טעינה לפני קיץ חם.",
        "עקוב אחרי חום מנוע ומפלס נוזלים בנסיעות עומס.",
    ]

    top_sorted = sorted(combined.items(), key=lambda kv: kv[1], reverse=True)
    top_risks: List[Dict[str, Any]] = []
    for sub, val in top_sorted[:4]:
        level = "high" if val >= (risk_index + 0.3) else ("medium" if val >= (risk_index - 0.1) else "low")
        why = reasons.get(sub, "התאמה לפרופיל השימוש מעלה שחיקה במכלול זה.")
        mitigation = "בדיקה יזומה במוסך והקפדה על טיפולים בזמן."
        if sub in ("brakes", "tires"):
            mitigation = "בדיקת שחיקה, לחץ אוויר והחלפה מוקדמת לפי צורך."
        if sub == "ac":
            mitigation = "בדיקת לחץ גז/פילטרים לפני קיץ והימנעות מהפעלות קיצון."
        top_risks.append(
            {
                "subsystem": sub,
                "level": level,
                "why": why,
                "mitigation": mitigation,
                "score": round(val, 2),
            }
        )

    return {
        "base_score": round(base_score, 1),
        "adjusted_score": round(adjusted_score, 1),
        "delta": delta,
        "top_risks": top_risks,
        "quick_actions": quick_actions,
    }
