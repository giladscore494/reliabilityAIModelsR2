# -*- coding: utf-8 -*-
"""
scoring_baseline.py
===================
Deterministic reliability baseline for the scoring engine ONLY.
This is a SEPARATE dictionary from car_models_dict.py — it does NOT replace it.

car_models_dict.py = validation, UI, autocomplete (untouched)
scoring_baseline.py = deterministic base scores for reliability calculation

Data sources:
  - JD Power VDS 2025/2026 (PP100, 34K owners)
  - Consumer Reports 2025 (380K vehicles, 0-100)
  - RepairPal (annual repair costs, 4.0/5.0 scale)
  - What Car? UK reliability 2024
  - Israeli market context (heat, salt, parts availability)

Usage in scoring engine:
    from app.services.scoring_baseline import get_make_profile, get_model_override, MAKE_DEFAULT

    make_profile = get_make_profile("toyota")
    model_override = get_model_override("toyota", "corolla")

    base = 62 + make_profile["base_modifier"]
    if model_override:
        base += model_override["model_modifier"]
"""

from typing import Any, Dict, Optional, Tuple

# ═══════════════════════════════════════════════════════════════════
# MAKE-LEVEL PROFILES
# ═══════════════════════════════════════════════════════════════════
#
# base_modifier: added to 62 base score (-10 to +10)
# recall_multiplier: scales recall penalty (>1 = brand known for recalls)
# mcp_multiplier: scales maintenance cost penalty
# bonus_eligible: can earn up to +8 bonus when LLM signals are clean
# notes: Hebrew explanation for debug/output
# ═══════════════════════════════════════════════════════════════════

MAKE_PROFILES: Dict[str, Dict[str, Any]] = {

    # ── יפן ─────────────────────────────────────────────────
    # Toyota: JDP 162 PP100 (#3), CR 66/100 (#1), RepairPal 4.0/5 $429/yr
    "toyota":        {"base_modifier": +8,  "recall_multiplier": 0.7,  "mcp_multiplier": 0.7,  "bonus_eligible": True,  "notes": "Toyota — JD Power #3 (162 PP100), CR #1 (66/100)"},
    # Lexus: JDP 140 PP100 (#1 3yr), CR 60/100 (#3), RepairPal $551/yr
    "lexus":         {"base_modifier": +10, "recall_multiplier": 0.6,  "mcp_multiplier": 0.85, "bonus_eligible": True,  "notes": "Lexus — #1 JD Power שלוש שנים (140 PP100)"},
    # Honda: JDP 196, CR 59/100 (#4), RepairPal 4.0/5 $428/yr
    "honda":         {"base_modifier": +6,  "recall_multiplier": 0.8,  "mcp_multiplier": 0.75, "bonus_eligible": True,  "notes": "Honda — CR #4 (59/100), JDP 196 PP100"},
    # Mazda: JDP 161 (#2 mass market), CR 39 (dropped, CX-70/90), used-car CR #3
    "mazda":         {"base_modifier": +5,  "recall_multiplier": 0.8,  "mcp_multiplier": 0.8,  "bonus_eligible": True,  "notes": "Mazda — JDP #2 (161 PP100), דגמי ליבה מצוינים"},
    # Subaru: JDP 210, CR 63/100 (#2), RepairPal 3.5/5 $617/yr
    "subaru":        {"base_modifier": +4,  "recall_multiplier": 0.85, "mcp_multiplier": 0.9,  "bonus_eligible": True,  "notes": "Subaru — CR #2 (63/100), Best Overall Brand 2025"},
    # Nissan: JDP 209, CR 57/100 (#6), RepairPal 4.0/5 $500/yr
    "nissan":        {"base_modifier": +1,  "recall_multiplier": 0.95, "mcp_multiplier": 0.95, "bonus_eligible": False, "notes": "Nissan — CR #6 (57/100), CVT Jatco בעייתי בדגמים ישנים"},
    # Suzuki: no US data, UK strong
    "suzuki":        {"base_modifier": +3,  "recall_multiplier": 0.9,  "mcp_multiplier": 0.75, "bonus_eligible": True,  "notes": "Suzuki — אמין, עלויות נמוכות"},
    # Mitsubishi: JDP 256 PP100
    "mitsubishi":    {"base_modifier": -1,  "recall_multiplier": 1.1,  "mcp_multiplier": 1.0,  "bonus_eligible": False, "notes": "Mitsubishi — JDP 256 PP100, CVT בעייתי"},
    "daihatsu":      {"base_modifier": +2,  "recall_multiplier": 0.9,  "mcp_multiplier": 0.7,  "bonus_eligible": False, "notes": "Daihatsu — אמין, הופסק"},
    "isuzu":         {"base_modifier": +2,  "recall_multiplier": 0.9,  "mcp_multiplier": 0.85, "bonus_eligible": False, "notes": "Isuzu — דיזל אמין"},
    "infiniti":      {"base_modifier": -2,  "recall_multiplier": 1.0,  "mcp_multiplier": 1.2,  "bonus_eligible": False, "notes": "Infiniti — אמינות בינונית-נמוכה"},

    # ── קוריאה ───────────────────────────────────────────────
    # Hyundai: JDP 199, CR 47/100
    "hyundai":       {"base_modifier": +2,  "recall_multiplier": 0.9,  "mcp_multiplier": 0.85, "bonus_eligible": False, "notes": "Hyundai — CR 47/100, JDP 199 PP100"},
    # Kia: JDP 206, CR 49/100 (#10)
    "kia":           {"base_modifier": +2,  "recall_multiplier": 0.9,  "mcp_multiplier": 0.85, "bonus_eligible": False, "notes": "Kia — CR #10 (49/100), JDP 206 PP100"},
    "genesis":       {"base_modifier": +1,  "recall_multiplier": 0.9,  "mcp_multiplier": 1.0,  "bonus_eligible": False, "notes": "Genesis — CR 44/100, פרימיום קוריאני"},
    "kgm / ssangyong": {"base_modifier": -2, "recall_multiplier": 1.1, "mcp_multiplier": 1.0, "bonus_eligible": False, "notes": "KGM/SsangYong — בינוני"},
    "ssangyong":     {"base_modifier": -2,  "recall_multiplier": 1.1,  "mcp_multiplier": 1.0,  "bonus_eligible": False, "notes": "SsangYong — בינוני"},
    "daewoo":        {"base_modifier": -3,  "recall_multiplier": 1.1,  "mcp_multiplier": 1.0,  "bonus_eligible": False, "notes": "Daewoo — הופסק, חלפים נדירים"},

    # ── גרמניה ───────────────────────────────────────────────
    # VW: JDP 285 PP100 (LAST), CR 44/100, RepairPal 3.5/5 $676/yr
    "volkswagen":    {"base_modifier": -5,  "recall_multiplier": 1.3,  "mcp_multiplier": 1.3,  "bonus_eligible": False, "notes": "VW — אחרון JDP (285 PP100), CR 44/100"},
    # Audi: JDP 273, CR 43/100 (dropped 10), RepairPal 3.0/5 $987/yr
    "audi":          {"base_modifier": -5,  "recall_multiplier": 1.2,  "mcp_multiplier": 1.5,  "bonus_eligible": False, "notes": "Audi — JDP 273 PP100, CR 43/100"},
    # BMW: JDP 189 (top 10!), CR 58/100 (#5), RepairPal 2.5/5 $968/yr
    "bmw":           {"base_modifier": -2,  "recall_multiplier": 1.1,  "mcp_multiplier": 1.4,  "bonus_eligible": False, "notes": "BMW — JDP #9 (189 PP100), CR #5 (58/100)"},
    # Mercedes: JDP 243, CR 42/100, RepairPal 2.5/5 $908/yr
    "mercedes-benz": {"base_modifier": -4,  "recall_multiplier": 1.1,  "mcp_multiplier": 1.5,  "bonus_eligible": False, "notes": "Mercedes — JDP 243 PP100, CR 42/100"},
    "mercedes":      {"base_modifier": -4,  "recall_multiplier": 1.1,  "mcp_multiplier": 1.5,  "bonus_eligible": False, "notes": "Mercedes — alias"},
    # Porsche: JDP 186 (#3 premium)
    "porsche":       {"base_modifier": +1,  "recall_multiplier": 0.9,  "mcp_multiplier": 1.5,  "bonus_eligible": False, "notes": "Porsche — JDP #3 premium (186 PP100)"},
    "opel":          {"base_modifier": -2,  "recall_multiplier": 1.1,  "mcp_multiplier": 1.1,  "bonus_eligible": False, "notes": "Opel — Stellantis, בינוני-נמוך"},
    "seat":          {"base_modifier": -4,  "recall_multiplier": 1.2,  "mcp_multiplier": 1.2,  "bonus_eligible": False, "notes": "SEAT — VW platform"},
    "cupra":         {"base_modifier": -4,  "recall_multiplier": 1.2,  "mcp_multiplier": 1.2,  "bonus_eligible": False, "notes": "Cupra — VW/SEAT ספורטיבי"},
    "skoda":         {"base_modifier": -3,  "recall_multiplier": 1.1,  "mcp_multiplier": 1.1,  "bonus_eligible": False, "notes": "Škoda — VW Group"},
    # MINI: JDP 190→168 (2026 #2)
    "mini":          {"base_modifier": -2,  "recall_multiplier": 1.1,  "mcp_multiplier": 1.3,  "bonus_eligible": False, "notes": "MINI — JDP 168 PP100 (2026 #2)"},
    "smart":         {"base_modifier": -3,  "recall_multiplier": 1.1,  "mcp_multiplier": 1.2,  "bonus_eligible": False, "notes": "Smart — Geely platform חדש"},

    # ── צרפת ──────────────────────────────────────────────────
    "renault":       {"base_modifier": -4,  "recall_multiplier": 1.2,  "mcp_multiplier": 1.2,  "bonus_eligible": False, "notes": "Renault — אמינות בינונית-נמוכה"},
    "peugeot":       {"base_modifier": -4,  "recall_multiplier": 1.2,  "mcp_multiplier": 1.2,  "bonus_eligible": False, "notes": "Peugeot — חשמל בדגמים ישנים"},
    "citroen":       {"base_modifier": -5,  "recall_multiplier": 1.3,  "mcp_multiplier": 1.2,  "bonus_eligible": False, "notes": "Citroën — אמינות נמוכה"},
    "ds":            {"base_modifier": -5,  "recall_multiplier": 1.2,  "mcp_multiplier": 1.3,  "bonus_eligible": False, "notes": "DS — חשמל, תחזוקה יקרה"},
    "ds automobiles": {"base_modifier": -5, "recall_multiplier": 1.2,  "mcp_multiplier": 1.3,  "bonus_eligible": False, "notes": "DS Automobiles — alias"},
    "alpine":        {"base_modifier": -3,  "recall_multiplier": 1.1,  "mcp_multiplier": 1.3,  "bonus_eligible": False, "notes": "Alpine — רנו ספורטיבית"},
    "dacia":         {"base_modifier": -2,  "recall_multiplier": 1.1,  "mcp_multiplier": 0.9,  "bonus_eligible": False, "notes": "Dacia — פשוט, פחות תקלות"},

    # ── איטליה / בריטניה / שוודיה ─────────────────────────────
    "fiat":          {"base_modifier": -5,  "recall_multiplier": 1.3,  "mcp_multiplier": 1.1,  "bonus_eligible": False, "notes": "Fiat — אמינות נמוכה"},
    "alfa romeo":    {"base_modifier": -7,  "recall_multiplier": 1.3,  "mcp_multiplier": 1.5,  "bonus_eligible": False, "notes": "Alfa Romeo — אמינות נמוכה מאוד"},
    "abarth":        {"base_modifier": -5,  "recall_multiplier": 1.2,  "mcp_multiplier": 1.2,  "bonus_eligible": False, "notes": "Abarth — Fiat ספורטיבי"},
    "lancia":        {"base_modifier": -6,  "recall_multiplier": 1.3,  "mcp_multiplier": 1.3,  "bonus_eligible": False, "notes": "Lancia — כמעט הופסק"},
    "maserati":      {"base_modifier": -6,  "recall_multiplier": 1.3,  "mcp_multiplier": 1.6,  "bonus_eligible": False, "notes": "Maserati — אמינות נמוכה"},
    "ferrari":       {"base_modifier": -3,  "recall_multiplier": 1.0,  "mcp_multiplier": 2.0,  "bonus_eligible": False, "notes": "Ferrari — תחזוקה אסטרונומית"},
    "lamborghini":   {"base_modifier": -4,  "recall_multiplier": 1.1,  "mcp_multiplier": 2.0,  "bonus_eligible": False, "notes": "Lamborghini — VW Group"},
    # Land Rover: JDP 270 PP100
    "land rover":    {"base_modifier": -8,  "recall_multiplier": 1.4,  "mcp_multiplier": 1.6,  "bonus_eligible": False, "notes": "Land Rover — JDP 270 PP100"},
    "jaguar":        {"base_modifier": -6,  "recall_multiplier": 1.3,  "mcp_multiplier": 1.5,  "bonus_eligible": False, "notes": "Jaguar — בינוני-נמוך"},
    "bentley":       {"base_modifier": -4,  "recall_multiplier": 1.1,  "mcp_multiplier": 2.0,  "bonus_eligible": False, "notes": "Bentley — VW Group"},
    "rolls-royce":   {"base_modifier": -3,  "recall_multiplier": 1.0,  "mcp_multiplier": 2.0,  "bonus_eligible": False, "notes": "Rolls-Royce — BMW platform"},
    "mclaren":       {"base_modifier": -5,  "recall_multiplier": 1.2,  "mcp_multiplier": 2.0,  "bonus_eligible": False, "notes": "McLaren — אמינות נמוכה"},
    "aston martin":  {"base_modifier": -6,  "recall_multiplier": 1.3,  "mcp_multiplier": 2.0,  "bonus_eligible": False, "notes": "Aston Martin"},
    "lotus":         {"base_modifier": -4,  "recall_multiplier": 1.2,  "mcp_multiplier": 1.5,  "bonus_eligible": False, "notes": "Lotus — נישה"},
    # Volvo: JDP 242, RepairPal 3.0/5 $769/yr
    "volvo":         {"base_modifier": -3,  "recall_multiplier": 1.1,  "mcp_multiplier": 1.3,  "bonus_eligible": False, "notes": "Volvo — JDP 242, בטיחות מעולה, אמינות ירדה"},
    "saab":          {"base_modifier": -4,  "recall_multiplier": 1.2,  "mcp_multiplier": 1.3,  "bonus_eligible": False, "notes": "Saab — הופסק"},

    # ── ארה״ב ─────────────────────────────────────────────────
    # Ford: JDP 216, CR 38/100
    "ford":          {"base_modifier": -2,  "recall_multiplier": 1.1,  "mcp_multiplier": 1.1,  "bonus_eligible": False, "notes": "Ford — JDP 216, CR 38/100"},
    # Chevrolet: JDP 169 (#6)
    "chevrolet":     {"base_modifier": -1,  "recall_multiplier": 1.0,  "mcp_multiplier": 1.0,  "bonus_eligible": False, "notes": "Chevrolet — JDP #6 (169 PP100)"},
    # Jeep: JDP 275, CR 28/100 (#24)
    "jeep":          {"base_modifier": -5,  "recall_multiplier": 1.3,  "mcp_multiplier": 1.3,  "bonus_eligible": False, "notes": "Jeep — JDP 275, CR 28/100"},
    "dodge":         {"base_modifier": -4,  "recall_multiplier": 1.2,  "mcp_multiplier": 1.2,  "bonus_eligible": False, "notes": "Dodge — Stellantis"},
    "ram":           {"base_modifier": -4,  "recall_multiplier": 1.2,  "mcp_multiplier": 1.2,  "bonus_eligible": False, "notes": "RAM — JDP 242, CR 26/100"},
    "chrysler":      {"base_modifier": -5,  "recall_multiplier": 1.3,  "mcp_multiplier": 1.3,  "bonus_eligible": False, "notes": "Chrysler — JDP 282, CR 34/100"},
    "cadillac":      {"base_modifier": 0,   "recall_multiplier": 1.0,  "mcp_multiplier": 1.3,  "bonus_eligible": False, "notes": "Cadillac — JDP #2 premium (169)"},
    "lincoln":       {"base_modifier": -2,  "recall_multiplier": 1.1,  "mcp_multiplier": 1.2,  "bonus_eligible": False, "notes": "Lincoln — CR 40/100"},
    # Tesla: CR 50/100 (#9, +8 spots)
    "tesla":         {"base_modifier": -1,  "recall_multiplier": 1.0,  "mcp_multiplier": 0.8,  "bonus_eligible": False, "notes": "Tesla — CR #9 (50/100)"},
    "gmc":           {"base_modifier": -2,  "recall_multiplier": 1.1,  "mcp_multiplier": 1.1,  "bonus_eligible": False, "notes": "GMC — JDP 181, CR 31/100"},
    "hummer":        {"base_modifier": -3,  "recall_multiplier": 1.2,  "mcp_multiplier": 1.3,  "bonus_eligible": False, "notes": "Hummer — תחזוקה יקרה"},

    # ── סין ───────────────────────────────────────────────────
    "byd":           {"base_modifier": 0,   "recall_multiplier": 1.0,  "mcp_multiplier": 0.8,  "bonus_eligible": False, "notes": "BYD — חשמלי, היסטוריה קצרה"},
    "mg":            {"base_modifier": -1,  "recall_multiplier": 1.0,  "mcp_multiplier": 0.9,  "bonus_eligible": False, "notes": "MG — SAIC, חדש"},
    "geely":         {"base_modifier": -2,  "recall_multiplier": 1.0,  "mcp_multiplier": 0.95, "bonus_eligible": False, "notes": "Geely — בינוני"},
    "chery":         {"base_modifier": -3,  "recall_multiplier": 1.1,  "mcp_multiplier": 1.0,  "bonus_eligible": False, "notes": "Chery — לא מוכח"},
    "omoda":         {"base_modifier": -3,  "recall_multiplier": 1.1,  "mcp_multiplier": 1.0,  "bonus_eligible": False, "notes": "Omoda — Chery, חדש"},
    "jaecoo":        {"base_modifier": -3,  "recall_multiplier": 1.1,  "mcp_multiplier": 1.0,  "bonus_eligible": False, "notes": "Jaecoo — Chery, חדש"},
    "zeekr":         {"base_modifier": -1,  "recall_multiplier": 1.0,  "mcp_multiplier": 0.9,  "bonus_eligible": False, "notes": "Zeekr — Geely premium EV"},
    "xpeng":         {"base_modifier": -2,  "recall_multiplier": 1.0,  "mcp_multiplier": 0.9,  "bonus_eligible": False, "notes": "XPeng — חדש"},
    "nio":           {"base_modifier": -2,  "recall_multiplier": 1.0,  "mcp_multiplier": 0.9,  "bonus_eligible": False, "notes": "NIO — חדש"},
    "lynk & co":     {"base_modifier": -2,  "recall_multiplier": 1.0,  "mcp_multiplier": 0.95, "bonus_eligible": False, "notes": "Lynk & Co — Geely/Volvo"},
    "polestar":      {"base_modifier": -2,  "recall_multiplier": 1.1,  "mcp_multiplier": 1.1,  "bonus_eligible": False, "notes": "Polestar — Volvo/Geely EV"},
    "seres":         {"base_modifier": -3,  "recall_multiplier": 1.1,  "mcp_multiplier": 1.0,  "bonus_eligible": False, "notes": "Seres — חדש"},
    "voyah":         {"base_modifier": -3,  "recall_multiplier": 1.0,  "mcp_multiplier": 1.0,  "bonus_eligible": False, "notes": "Voyah — Dongfeng premium"},
    "gac / aion":    {"base_modifier": -3,  "recall_multiplier": 1.0,  "mcp_multiplier": 0.9,  "bonus_eligible": False, "notes": "GAC/Aion — חדש"},
    "leapmotor":     {"base_modifier": -3,  "recall_multiplier": 1.1,  "mcp_multiplier": 0.9,  "bonus_eligible": False, "notes": "Leapmotor — חדש"},
    "skywell":       {"base_modifier": -4,  "recall_multiplier": 1.1,  "mcp_multiplier": 1.0,  "bonus_eligible": False, "notes": "Skywell — נישה"},
    "maxus":         {"base_modifier": -2,  "recall_multiplier": 1.0,  "mcp_multiplier": 0.9,  "bonus_eligible": False, "notes": "Maxus — SAIC commercial"},
    "ora":           {"base_modifier": -3,  "recall_multiplier": 1.0,  "mcp_multiplier": 0.9,  "bonus_eligible": False, "notes": "Ora — GWM EV"},
    "wey":           {"base_modifier": -3,  "recall_multiplier": 1.0,  "mcp_multiplier": 1.0,  "bonus_eligible": False, "notes": "Wey — GWM premium"},
    "haval":         {"base_modifier": -3,  "recall_multiplier": 1.0,  "mcp_multiplier": 0.9,  "bonus_eligible": False, "notes": "Haval — GWM"},
    "hongqi":        {"base_modifier": -3,  "recall_multiplier": 1.0,  "mcp_multiplier": 1.0,  "bonus_eligible": False, "notes": "Hongqi — FAW luxury"},
    "dongfeng":      {"base_modifier": -3,  "recall_multiplier": 1.0,  "mcp_multiplier": 0.9,  "bonus_eligible": False, "notes": "Dongfeng — חדש"},
    "aiways":        {"base_modifier": -4,  "recall_multiplier": 1.2,  "mcp_multiplier": 1.0,  "bonus_eligible": False, "notes": "Aiways — הפסיק פעילות"},

    # ── אחרים ─────────────────────────────────────────────────
    "tata":          {"base_modifier": -4,  "recall_multiplier": 1.1,  "mcp_multiplier": 1.0,  "bonus_eligible": False, "notes": "Tata — הופסק"},
    "mahindra":      {"base_modifier": -3,  "recall_multiplier": 1.1,  "mcp_multiplier": 1.0,  "bonus_eligible": False, "notes": "Mahindra — נישה"},
    "proton":        {"base_modifier": -5,  "recall_multiplier": 1.2,  "mcp_multiplier": 1.1,  "bonus_eligible": False, "notes": "Proton — הופסק"},
    "rover":         {"base_modifier": -6,  "recall_multiplier": 1.3,  "mcp_multiplier": 1.3,  "bonus_eligible": False, "notes": "Rover — נסגר 2005"},
    "mg (british era)": {"base_modifier": -6, "recall_multiplier": 1.3, "mcp_multiplier": 1.3, "bonus_eligible": False, "notes": "MG British — נסגר 2005"},
}

MAKE_DEFAULT: Dict[str, Any] = {
    "base_modifier": 0, "recall_multiplier": 1.0, "mcp_multiplier": 1.0,
    "bonus_eligible": False, "notes": "יצרן לא ידוע",
}


# ═══════════════════════════════════════════════════════════════════
# MODEL-LEVEL OVERRIDES — all 980 Israeli-market vehicles
# ═══════════════════════════════════════════════════════════════════
#
# model_modifier: additional points on top of make (-5 to +5)
# confidence_boost: added to data confidence (0.0 to 0.15)
# transmission_default: fallback if AI can't identify ("automatic"/"manual"/"cvt"/"dct"/"other")
# known_issues: list of Hebrew strings for debug/output
#
# Auto-generated from:
#   - 289 manually-researched models (JDP, CR, RepairPal data)
#   - 691 heuristic-scored models (EV/PHEV/sport/discontinued/new rules)
# ═══════════════════════════════════════════════════════════════════



MODEL_OVERRIDES: Dict[str, Dict[str, Dict[str, Any]]] = {

    # ── Abarth (5 models) ──
    "abarth": {
        "124 spider": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["ספורטיבי"]},
        "500 / 595 / 695": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "500e": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "600e": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["חדש מאוד"]},
        "punto": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Aiways (2 models) ──
    "aiways": {
        "u5": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "u6": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Alfa Romeo (21 models) ──
    "alfa romeo": {
        "145": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "146": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "147": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "155": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "156": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "159": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "164": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "166": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "33": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "4c": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "75": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "brera": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "giulia": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "giulietta": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "gt": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "gtv": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "junior / milano": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["חדש מאוד"]},
        "mito": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "spider": {"model_modifier": -3, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים", "ספורטיבי"]},
        "stelvio": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "tonale": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Alpine (2 models) ──
    "alpine": {
        "a110": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "a290": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["חדש מאוד"]},
    },

    # ── Aston Martin (7 models) ──
    "aston martin": {
        "db11": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "db12": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "db9": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "dbs superleggera": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "dbx": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "rapide": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "vantage": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Audi (31 models) ──
    "audi": {
        "100": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "80": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "a1": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": []},
        "a3": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": []},
        "a4": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "dct", "known_issues": []},
        "a5": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": []},
        "a6": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": []},
        "a7": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": []},
        "a8": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "e-tron gt": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": []},
        "q2": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": []},
        "q3": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": []},
        "q4 e-tron": {"model_modifier": -3, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": []},
        "q5": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "q6 e-tron": {"model_modifier": -3, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה", "חדש מאוד"]},
        "q7": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "q8": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "q8 e-tron / e-tron": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה"]},
        "r8": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "rs3": {"model_modifier": +0, "confidence_boost": 0.1, "transmission_default": "dct", "known_issues": ["ספורטיבי"]},
        "rs4": {"model_modifier": +0, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["ספורטיבי"]},
        "rs5": {"model_modifier": +0, "confidence_boost": 0.1, "transmission_default": "dct", "known_issues": ["ספורטיבי"]},
        "rs6": {"model_modifier": +0, "confidence_boost": 0.1, "transmission_default": "dct", "known_issues": ["ספורטיבי"]},
        "rs7": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": ["ספורטיבי"]},
        "s3": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "s4": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "s5": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "sq5": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": []},
        "sq7": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": []},
        "sq8": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": []},
        "tt": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "dct", "known_issues": []},
    },

    # ── Bentley (3 models) ──
    "bentley": {
        "bentayga": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "continental gt / gtc": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "flying spur": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Bmw (113 models) ──
    "bmw": {
        "116i": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "118d": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "118i": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "120i": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "125i": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "128ti": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "218i": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "218i gran coupe": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "220i": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "225xe": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "230e": {"model_modifier": -3, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["PHEV — בעייתי סטטיסטית"]},
        "316i": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "318d / 320d": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "318i": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "320e": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "320i": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "323i": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "325i": {"model_modifier": +0, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "328i": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "330e": {"model_modifier": -3, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["PHEV — בעייתי סטטיסטית"]},
        "330i": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "335i": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "340i / m340i": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "420i": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "428i": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "430i": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "435i": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "440i / m440i": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "518i": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "520d / 530d": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "520i": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "523i": {"model_modifier": +0, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "525i": {"model_modifier": +0, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "528i": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "530e": {"model_modifier": -3, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["PHEV — בעייתי סטטיסטית"]},
        "530i": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "535i": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "540i": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "545e / 550e": {"model_modifier": -3, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["PHEV — בעייתי סטטיסטית"]},
        "550i / m550i": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "630i": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "630i gt": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "640i": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "640i gt": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "650i": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "728i": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "730i / 730li": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "735i": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "740e / 745e / 750e": {"model_modifier": -3, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["PHEV — בעייתי סטטיסטית"]},
        "740i / 740li": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "745i": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "750i / 750li": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "760i / 760li": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "840i": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "850i / m850i": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "i3": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "other", "known_issues": []},
        "i3s": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה"]},
        "i4 edrive35 / edrive40 / m50": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה"]},
        "i5 edrive40 / m60": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה"]},
        "i7 xdrive60 / m70": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה"]},
        "i8": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "ix xdrive40 / xdrive50 / m60": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה"]},
        "ix1 edrive20 / xdrive30": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה"]},
        "ix2 edrive20 / xdrive30": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה"]},
        "ix3": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה"]},
        "m135i / m140i": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "m2": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "m235i / m240i": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "m3": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "m4": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "m5": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "m6": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "m760li": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "m8": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "x1 sdrive18i": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "x1 sdrive20i": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "x1 xdrive25e / 30e": {"model_modifier": -3, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["PHEV — בעייתי סטטיסטית"]},
        "x2 m35i": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "x2 sdrive18i": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "x2 sdrive20i": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "x3 2.0i / xdrive20i": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "x3 2.5i / 3.0i": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "x3 m": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "x3 m40i": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "x3 xdrive20d / 30d": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "x3 xdrive30e": {"model_modifier": -3, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["PHEV — בעייתי סטטיסטית"]},
        "x3 xdrive30i": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "x4 m40i": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "x4 xdrive20i": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "x4 xdrive30i": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "x5 3.0d / xdrive30d": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "x5 3.0i / xdrive30i": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "x5 4.4i / xdrive50i": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "x5 m": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "x5 m50i / m60i": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "x5 xdrive40e": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "x5 xdrive40i": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "x5 xdrive45e": {"model_modifier": -3, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["PHEV — בעייתי סטטיסטית"]},
        "x5 xdrive50e": {"model_modifier": -3, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["PHEV — בעייתי סטטיסטית"]},
        "x6 m": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "x6 xdrive30d": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "x6 xdrive35i / 40i": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "x6 xdrive50i / m50i / m60i": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "x7 m50i / m60i": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "x7 xdrive30d / 40d": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "x7 xdrive40i": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "xm": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "z3 1.8 / 1.9": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "z3 2.8 / 3.0": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "z3 m roadster": {"model_modifier": -3, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים", "ספורטיבי"]},
        "z4 2.0i / 2.5i / 3.0i": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "z4 sdrive20i / 23i / 28i / 35i": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "z4 sdrive20i / 30i / m40i": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Byd (8 models) ──
    "byd": {
        "atto 3": {"model_modifier": +1, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": []},
        "dolphin": {"model_modifier": +1, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": []},
        "han": {"model_modifier": +0, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": []},
        "seal": {"model_modifier": +0, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": []},
        "seal u": {"model_modifier": +0, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": []},
        "sealion 7": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["חדש מאוד"]},
        "song plus": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "tang": {"model_modifier": +0, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": []},
    },

    # ── Cadillac (8 models) ──
    "cadillac": {
        "ats": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "cts": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "escalade": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "lyriq": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "srx": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "xt4": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "xt5": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "xt6": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Chery (5 models) ──
    "chery": {
        "arrizo 8": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["חדש מאוד"]},
        "tiggo 4 pro": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "tiggo 7 pro": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "tiggo 8 pro": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "tiggo 9": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["חדש מאוד"]},
    },

    # ── Chevrolet (17 models) ──
    "chevrolet": {
        "aveo / sonic": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "blazer ev": {"model_modifier": -3, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה", "חדש מאוד"]},
        "bolt ev / euv": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה"]},
        "camaro": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["ספורטיבי"]},
        "captiva": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "corvette": {"model_modifier": +0, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["ספורטיבי"]},
        "cruze": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "equinox": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "equinox ev": {"model_modifier": -3, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה", "חדש מאוד"]},
        "malibu": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "orlando": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "silverado": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["מסחרי — עמידות"]},
        "spark": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "tahoe": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "traverse": {"model_modifier": +0, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["ספורטיבי"]},
        "trax": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "volt": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Chrysler (6 models) ──
    "chrysler": {
        "300c": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "crossfire": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "pacifica": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "pt cruiser": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "sebring": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "voyager / grand voyager": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Citroen (30 models) ──
    "citroen": {
        "ami": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "berlingo": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["מסחרי — עמידות"]},
        "c-elysee": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "c1": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "c2": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "c3": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "c3 aircross": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "c3 picasso": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "c4": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "c4 picasso / spacetourer": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "c4 x": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "c5": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "c5 aircross": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "c5 x": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "c6": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "c8": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "ds3": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "ds4": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "ds5": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "grand c4 picasso / spacetourer": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "jumper": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["מסחרי — עמידות"]},
        "jumpy": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["מסחרי — עמידות"]},
        "saxo": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "spacetourer": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "xsara": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "xsara picasso": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "zx": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "ë-berlingo": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה", "מסחרי — עמידות"]},
        "ë-c3": {"model_modifier": -3, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה", "חדש מאוד"]},
        "ë-c4": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה"]},
    },

    # ── Cupra (5 models) ──
    "cupra": {
        "ateca": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": []},
        "born": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה"]},
        "formentor": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": []},
        "leon": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": []},
        "tavascan": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "dct", "known_issues": ["חדש מאוד"]},
    },

    # ── Dacia (8 models) ──
    "dacia": {
        "bigster": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["חדש מאוד"]},
        "dokker": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "duster": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "jogger": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "lodgy": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "logan": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "sandero": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "spring": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה"]},
    },

    # ── Daewoo (10 models) ──
    "daewoo": {
        "cielo / nexia": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "espero": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "kalos": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "lacetti": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "lanos": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "leganza": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "matiz": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "nubira": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "tacuma / rezzo": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "tico": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
    },

    # ── Daihatsu (9 models) ──
    "daihatsu": {
        "charade": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "copen": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "cuore / mira": {"model_modifier": +0, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "gran move": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "materia": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "move": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "sirion": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "terios": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "yrv": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
    },

    # ── Dodge (7 models) ──
    "dodge": {
        "caliber": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "challenger": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "charger": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "durango": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "journey": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "nitro": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "ram": {"model_modifier": -1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
    },

    # ── Dongfeng (1 models) ──
    "dongfeng": {
        "box": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["חדש מאוד"]},
    },

    # ── Ds Automobiles (5 models) ──
    "ds automobiles": {
        "ds 3": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "ds 3 crossback": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "ds 4": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "ds 7": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "ds 9": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Ferrari (11 models) ──
    "ferrari": {
        "296 gtb / gts": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "458 italia / spider": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["ספורטיבי"]},
        "488 gtb / spider": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["ספורטיבי"]},
        "812 superfast / gts": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "california / t": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "f8 tributo / spider": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["ספורטיבי"]},
        "gtc4lusso": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "portofino": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "purosangue": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "roma": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "sf90 stradale / spider": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["ספורטיבי"]},
    },

    # ── Fiat (25 models) ──
    "fiat": {
        "500": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "500e": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "500l": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "500x": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "600e": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "bravo / brava": {"model_modifier": +0, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "croma": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "doblo": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["מסחרי — עמידות"]},
        "ducato": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["מסחרי — עמידות"]},
        "fiorino": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["מסחרי — עמידות"]},
        "freemont": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "fullback": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "linea": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "marea": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "multipla": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "panda": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "punto / grande punto": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "qubo": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "scudo": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["מסחרי — עמידות"]},
        "stilo": {"model_modifier": -3, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים", "ספורטיבי"]},
        "tempra": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "tipo": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "topolino": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["חדש מאוד"]},
        "ulysse": {"model_modifier": +0, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "uno": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
    },

    # ── Ford (19 models) ──
    "ford": {
        "bronco": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "ecosport": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["ספורטיבי"]},
        "explorer": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "f-150": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["מסחרי — עמידות"]},
        "fiesta": {"model_modifier": -1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["PowerShift DCT"]},
        "focus": {"model_modifier": -1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["PowerShift DCT"]},
        "galaxy": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "kuga / escape": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "maverick": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "mondeo / fusion": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "mustang": {"model_modifier": +0, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["ספורטיבי"]},
        "mustang mach-e": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": []},
        "puma": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "ranger": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "s-max": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "tourneo connect / courier": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "transit": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "manual", "known_issues": []},
        "transit connect": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["מסחרי — עמידות"]},
        "transit custom": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["מסחרי — עמידות"]},
    },

    # ── Gac / Aion (2 models) ──
    "gac / aion": {
        "aion v": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["חדש מאוד"]},
        "aion y": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Geely (4 models) ──
    "geely": {
        "coolray": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "geometry c": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "monjaro": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "starray": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["חדש מאוד"]},
    },

    # ── Genesis (6 models) ──
    "genesis": {
        "g70": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "g80": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "g90": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "gv60": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "gv70": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "gv80": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Gmc (3 models) ──
    "gmc": {
        "hummer ev": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה"]},
        "sierra": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["מסחרי — עמידות"]},
        "yukon": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Haval (2 models) ──
    "haval": {
        "h6": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "jolion": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Honda (15 models) ──
    "honda": {
        "accord": {"model_modifier": +3, "confidence_boost": 0.15, "transmission_default": "automatic", "known_issues": []},
        "civic": {"model_modifier": +3, "confidence_boost": 0.15, "transmission_default": "automatic", "known_issues": []},
        "civic type r": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "manual", "known_issues": []},
        "cr-v": {"model_modifier": +3, "confidence_boost": 0.15, "transmission_default": "automatic", "known_issues": []},
        "cr-z": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "cvt", "known_issues": []},
        "e:ny1": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": []},
        "fr-v": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "hr-v": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "insight": {"model_modifier": +3, "confidence_boost": 0.1, "transmission_default": "cvt", "known_issues": []},
        "jazz / fit": {"model_modifier": +4, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "legend": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "odyssey": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "prelude": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "manual", "known_issues": []},
        "stream": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "zr-v": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Hongqi (1 models) ──
    "hongqi": {
        "e-hs9": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה"]},
    },

    # ── Hummer (2 models) ──
    "hummer": {
        "h2": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "h3": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
    },

    # ── Hyundai (33 models) ──
    "hyundai": {
        "atos": {"model_modifier": +0, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "bayon": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "casper": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "coupe / tiburon": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "creta": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "elantra": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "excel": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "getz": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "grandeur / azera": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "h1 / starex": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "i10": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "i20": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "i25 / accent": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "i30": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "i40": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "ioniq": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "ioniq 5": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["ICCU"]},
        "ioniq 5 n": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["חדש מאוד"]},
        "ioniq 6": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": []},
        "ix35": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה"]},
        "kona": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "kona n": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "matrix": {"model_modifier": -3, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה", "הופסק לפני 2015"]},
        "nexo": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "palisade": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "santa fe": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "sonata": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "staria": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "terracan": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "trajet": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "tucson": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "veloster": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "venue": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Infiniti (10 models) ──
    "infiniti": {
        "g series": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "q30": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "q50": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "q60": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "q70": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "qx30": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "qx50": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "qx60": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "qx70 / fx": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "qx80 / qx56": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Isuzu (4 models) ──
    "isuzu": {
        "d-max": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["מסחרי — עמידות"]},
        "mu-x": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "rodeo": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "trooper": {"model_modifier": -1, "confidence_boost": 0.1, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
    },

    # ── Jaecoo (2 models) ──
    "jaecoo": {
        "j7": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["חדש מאוד"]},
        "j8": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["חדש מאוד"]},
    },

    # ── Jaguar (9 models) ──
    "jaguar": {
        "e-pace": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה"]},
        "f-pace": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "f-type": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "i-pace": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "s-type": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "x-type": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "xe": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "xf": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "xj": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Jeep (8 models) ──
    "jeep": {
        "avenger": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "cherokee": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "commander": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "compass": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "gladiator": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["מסחרי — עמידות"]},
        "grand cherokee": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "renegade": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "wrangler": {"model_modifier": +0, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Kgm / Ssangyong (9 models) ──
    "kgm / ssangyong": {
        "actyon": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "korando": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "kyron": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "musso": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "rexton": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "rodius / stavic": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "tivoli": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "torres": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "torres evx": {"model_modifier": -3, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה", "חדש מאוד"]},
    },

    # ── Kia (28 models) ──
    "kia": {
        "carens": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "carnival": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "ceed": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "ceed sw": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "ev3": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": []},
        "ev6": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": []},
        "ev9": {"model_modifier": -3, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["CR worst Kia"]},
        "forte / cerato": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "magentis": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "mohave / borrego": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "niro": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "niro plus": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "opirus / amanti": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "optima / k5": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "picanto": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "pride": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "proceed": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "rio": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "seltos": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "sephia": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "shuma": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "sorento": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "soul": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "sportage": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "stinger": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["ספורטיבי"]},
        "stonic": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "venga": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "xceed": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Lamborghini (5 models) ──
    "lamborghini": {
        "aventador": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "gallardo": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "huracan": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "revuelto": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה"]},
        "urus": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Lancia (9 models) ──
    "lancia": {
        "dedra": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "delta": {"model_modifier": +0, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "kappa": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "lybra": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "musa": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "phedra": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "thema": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "thesis": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "y / ypsilon": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Land Rover (8 models) ──
    "land rover": {
        "defender": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "discovery": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "discovery sport": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "freelander": {"model_modifier": -1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "range rover": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "range rover evoque": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "range rover sport": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "range rover velar": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Leapmotor (2 models) ──
    "leapmotor": {
        "c10": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["חדש מאוד"]},
        "t03": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Lexus (16 models) ──
    "lexus": {
        "ct 200h": {"model_modifier": +3, "confidence_boost": 0.1, "transmission_default": "cvt", "known_issues": []},
        "es": {"model_modifier": +4, "confidence_boost": 0.15, "transmission_default": "automatic", "known_issues": []},
        "gs": {"model_modifier": +3, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "gx": {"model_modifier": +4, "confidence_boost": 0.15, "transmission_default": "automatic", "known_issues": []},
        "is": {"model_modifier": +4, "confidence_boost": 0.15, "transmission_default": "automatic", "known_issues": []},
        "lbx": {"model_modifier": +2, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "lc": {"model_modifier": +2, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "lm": {"model_modifier": +2, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "ls": {"model_modifier": +3, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "lx": {"model_modifier": +4, "confidence_boost": 0.15, "transmission_default": "automatic", "known_issues": []},
        "nx": {"model_modifier": +3, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "rc": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "rx": {"model_modifier": +3, "confidence_boost": 0.15, "transmission_default": "automatic", "known_issues": []},
        "rz": {"model_modifier": +1, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": []},
        "sc 430": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "ux": {"model_modifier": +4, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Lincoln (3 models) ──
    "lincoln": {
        "mkc / corsair": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["ספורטיבי"]},
        "mkx / nautilus": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "navigator": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Lotus (5 models) ──
    "lotus": {
        "eletre": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "elise": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "emira": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "evora": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה"]},
        "exige": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Lynk & Co (2 models) ──
    "lynk & co": {
        "01": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "02": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["חדש מאוד"]},
    },

    # ── Mahindra (3 models) ──
    "mahindra": {
        "scorpio": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "thar": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "xuv500 / xuv700": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Maserati (7 models) ──
    "maserati": {
        "ghibli": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "grancabrio": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["ספורטיבי"]},
        "granturismo": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "grecale": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "levante": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה"]},
        "mc20": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "quattroporte": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Maxus (3 models) ──
    "maxus": {
        "euniq 5": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "euniq 6": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "t90": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Mazda (23 models) ──
    "mazda": {
        "121": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "manual", "known_issues": []},
        "323": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "manual", "known_issues": []},
        "626": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "bt-50": {"model_modifier": +3, "confidence_boost": 0.1, "transmission_default": "manual", "known_issues": []},
        "cx-3": {"model_modifier": +3, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "cx-30": {"model_modifier": +3, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "cx-5": {"model_modifier": +4, "confidence_boost": 0.15, "transmission_default": "automatic", "known_issues": []},
        "cx-50": {"model_modifier": +2, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "cx-60": {"model_modifier": -3, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["פלטפורמה חדשה בעייתית"]},
        "cx-7": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "cx-80": {"model_modifier": -3, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": []},
        "cx-9": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "cx-90": {"model_modifier": -4, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["CR well-below-average"]},
        "mazda2 / demio": {"model_modifier": +3, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "mazda3": {"model_modifier": +3, "confidence_boost": 0.15, "transmission_default": "automatic", "known_issues": []},
        "mazda5": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "mazda6": {"model_modifier": +3, "confidence_boost": 0.15, "transmission_default": "automatic", "known_issues": []},
        "mpv": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "mx-30": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": []},
        "mx-5": {"model_modifier": +4, "confidence_boost": 0.15, "transmission_default": "manual", "known_issues": []},
        "premacy": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "rx-8": {"model_modifier": -2, "confidence_boost": 0.1, "transmission_default": "manual", "known_issues": ["מנוע רוטרי"]},
        "tribute": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Mclaren (4 models) ──
    "mclaren": {
        "540c / 570s / 570gt": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "720s": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "artura": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "gt": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Mercedes-Benz (37 models) ──
    "mercedes-benz": {
        "190": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "a-class": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": []},
        "amg gt": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["ספורטיבי"]},
        "b-class": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": []},
        "c-class": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "citan": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["מסחרי — עמידות"]},
        "cla": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": []},
        "cle": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "clk": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "cls": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "e-class": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "eqa": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": []},
        "eqb": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": []},
        "eqc": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה"]},
        "eqe": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": []},
        "eqe suv": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה"]},
        "eqs": {"model_modifier": -3, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": []},
        "eqs suv": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה"]},
        "eqv": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה"]},
        "g-class": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "gl-class": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "gla": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": []},
        "glb": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": []},
        "glc": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "gle": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "glk": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "gls": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "maybach gls": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "maybach s-class": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "ml-class": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "r-class": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "s-class": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "sl": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "slk / slc": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "sprinter": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "v-class": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "vito": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Mg (9 models) ──
    "mg": {
        "cyberster": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["ספורטיבי", "חדש מאוד"]},
        "hs / ehs": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "marvel r": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "mg3": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "mg3 hybrid+": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["חדש מאוד"]},
        "mg4": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "mg5": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "zs": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "zs ev": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה"]},
    },

    # ── Mg (British Era) (4 models) ──
    "mg (british era)": {
        "tf": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "zr": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "zs": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "zt": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
    },

    # ── Mini (10 models) ──
    "mini": {
        "aceman": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["חדש מאוד"]},
        "cabrio / convertible": {"model_modifier": +0, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["ספורטיבי"]},
        "clubman": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "cooper / one / hatch": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "cooper s": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "cooper se / electric": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "countryman": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "coupe": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "paceman": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "roadster": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["ספורטיבי"]},
    },

    # ── Mitsubishi (17 models) ──
    "mitsubishi": {
        "3000gt / gto": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "asx": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "cvt", "known_issues": []},
        "attrage": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "cvt", "known_issues": []},
        "carisma": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "colt": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "eclipse cross": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "cvt", "known_issues": []},
        "galant": {"model_modifier": +0, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "grandis": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "i-miev": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה"]},
        "l200 / triton": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["מסחרי — עמידות"]},
        "lancer": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "lancer evolution": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה"]},
        "outlander": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "pajero": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "pajero sport": {"model_modifier": +0, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["ספורטיבי"]},
        "space star": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "cvt", "known_issues": []},
        "space wagon": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
    },

    # ── Nio (3 models) ──
    "nio": {
        "el6": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["חדש מאוד"]},
        "el7": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["חדש מאוד"]},
        "et5": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Nissan (24 models) ──
    "nissan": {
        "200sx / silvia": {"model_modifier": +0, "confidence_boost": 0.1, "transmission_default": "manual", "known_issues": []},
        "370z": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "manual", "known_issues": []},
        "almera": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "altima": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "ariya": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": []},
        "gt-r": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": []},
        "juke": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "kicks": {"model_modifier": +3, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "leaf": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "other", "known_issues": []},
        "maxima": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "micra": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "murano": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "navara": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "manual", "known_issues": []},
        "note": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "cvt", "known_issues": []},
        "nv200": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "pathfinder": {"model_modifier": +0, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "patrol": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "primera": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "qashqai": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "sentra": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "sunny": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "manual", "known_issues": []},
        "terrano": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "manual", "known_issues": []},
        "tiida": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "x-trail": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Omoda (2 models) ──
    "omoda": {
        "c5": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "e5": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["חדש מאוד"]},
    },

    # ── Opel (20 models) ──
    "opel": {
        "adam": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "agila": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "ampera": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "astra": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "calibra": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "combo": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["מסחרי — עמידות"]},
        "corsa": {"model_modifier": +0, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["ספורטיבי"]},
        "crossland": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "frontera": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["חדש מאוד"]},
        "grandland": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "insignia": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "kadett": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "meriva": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "mokka": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "movano": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["מסחרי — עמידות"]},
        "omega": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "tigra": {"model_modifier": -1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "vectra": {"model_modifier": -1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "vivaro": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["מסחרי — עמידות"]},
        "zafira": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Ora (2 models) ──
    "ora": {
        "07": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["חדש מאוד"]},
        "funky cat / 03": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Peugeot (32 models) ──
    "peugeot": {
        "106": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "107": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "108": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "2008": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "205": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "206": {"model_modifier": -1, "confidence_boost": 0.1, "transmission_default": "manual", "known_issues": ["AL4"]},
        "207": {"model_modifier": -2, "confidence_boost": 0.1, "transmission_default": "manual", "known_issues": ["AL4"]},
        "208": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "3008": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "301": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "306": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "307": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "308": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "4007": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "4008": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "406": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "407": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "408": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "5008": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "508": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "607": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "boxer": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["מסחרי — עמידות"]},
        "e-2008": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה"]},
        "e-208": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה"]},
        "e-3008": {"model_modifier": -3, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה", "חדש מאוד"]},
        "e-308": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה"]},
        "e-5008": {"model_modifier": -3, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה", "חדש מאוד"]},
        "expert": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["מסחרי — עמידות"]},
        "partner": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["מסחרי — עמידות"]},
        "rcz": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "rifter": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["מסחרי — עמידות"]},
        "traveller": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Polestar (3 models) ──
    "polestar": {
        "2": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "3": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["חדש מאוד"]},
        "4": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["חדש מאוד"]},
    },

    # ── Porsche (10 models) ──
    "porsche": {
        "718 boxster": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "718 cayman": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "911": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "boxster": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "cayenne": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "cayman": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "macan": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "macan electric": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["חדש מאוד"]},
        "panamera": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "taycan": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Proton (4 models) ──
    "proton": {
        "gen-2": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "persona": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015", "ספורטיבי"]},
        "satria": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "wira": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
    },

    # ── Ram (3 models) ──
    "ram": {
        "1500": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["מסחרי — עמידות"]},
        "2500": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["מסחרי — עמידות"]},
        "promaster": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["מסחרי — עמידות"]},
    },

    # ── Renault (29 models) ──
    "renault": {
        "19": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "21": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "5 / r5 e-tech": {"model_modifier": -3, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה", "חדש מאוד"]},
        "arkana": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "austral": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "captur": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "clio": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "espace": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "fluence": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "grand scenic": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "kadjar": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "kangoo": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["מסחרי — עמידות"]},
        "koleos": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "laguna": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "latitude": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "master": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["מסחרי — עמידות"]},
        "megane": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "megane e-tech": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה"]},
        "megane rs": {"model_modifier": +0, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["ספורטיבי"]},
        "modus": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "rafale": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["חדש מאוד"]},
        "scenic": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "scenic e-tech": {"model_modifier": -3, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה", "חדש מאוד"]},
        "symbioz": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["חדש מאוד"]},
        "symbol": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "talisman": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "trafic": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["מסחרי — עמידות"]},
        "twingo": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "zoe": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "other", "known_issues": []},
    },

    # ── Rolls-Royce (6 models) ──
    "rolls-royce": {
        "cullinan": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "dawn": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "ghost": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "phantom": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "spectre": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה"]},
        "wraith": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Rover (5 models) ──
    "rover": {
        "200": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "25": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "400": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "45": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "75": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
    },

    # ── Saab (4 models) ──
    "saab": {
        "9-3": {"model_modifier": +0, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "9-5": {"model_modifier": +0, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "900": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "9000": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
    },

    # ── Seat (11 models) ──
    "seat": {
        "alhambra": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "altea": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "arona": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": []},
        "ateca": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": []},
        "cordoba": {"model_modifier": -1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "exeo": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": ["הופסק לפני 2015"]},
        "ibiza": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": []},
        "leon": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": []},
        "mii": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": []},
        "tarraco": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": []},
        "toledo": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Seres (2 models) ──
    "seres": {
        "3": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "5": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Skoda (15 models) ──
    "skoda": {
        "citigo": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": []},
        "elroq": {"model_modifier": -3, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה", "חדש מאוד"]},
        "enyaq": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": []},
        "enyaq coupe": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["רכב חשמלי — היסטוריית אמינות קצרה"]},
        "fabia": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "felicia": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "kamiq": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": []},
        "karoq": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": []},
        "kodiaq": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": []},
        "octavia": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "dct", "known_issues": []},
        "rapid": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "dct", "known_issues": []},
        "roomster": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "scala": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": []},
        "superb": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": []},
        "yeti": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "dct", "known_issues": []},
    },

    # ── Skywell (1 models) ──
    "skywell": {
        "et5 / be11": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Smart (4 models) ──
    "smart": {
        "#1": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "#3": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["חדש מאוד"]},
        "forfour": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "fortwo": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Subaru (12 models) ──
    "subaru": {
        "ascent": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "cvt", "known_issues": []},
        "brz": {"model_modifier": +3, "confidence_boost": 0.1, "transmission_default": "manual", "known_issues": []},
        "forester": {"model_modifier": +3, "confidence_boost": 0.15, "transmission_default": "cvt", "known_issues": []},
        "impreza": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "cvt", "known_issues": []},
        "justy": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": []},
        "legacy": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "cvt", "known_issues": []},
        "levorg": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "cvt", "known_issues": []},
        "outback": {"model_modifier": +3, "confidence_boost": 0.15, "transmission_default": "cvt", "known_issues": []},
        "solterra": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": []},
        "tribeca / b9 tribeca": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "wrx / wrx sti": {"model_modifier": +0, "confidence_boost": 0.1, "transmission_default": "manual", "known_issues": []},
        "xv / crosstrek": {"model_modifier": +3, "confidence_boost": 0.15, "transmission_default": "cvt", "known_issues": []},
    },

    # ── Suzuki (18 models) ──
    "suzuki": {
        "across": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "alto": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "manual", "known_issues": []},
        "baleno": {"model_modifier": +2, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "celerio": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "fronx": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["חדש מאוד"]},
        "grand vitara": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "ignis": {"model_modifier": +2, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "jimny": {"model_modifier": +3, "confidence_boost": 0.1, "transmission_default": "manual", "known_issues": []},
        "kizashi": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "liana": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "s-cross": {"model_modifier": +2, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "samurai": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "splash": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "swace": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "swift": {"model_modifier": +3, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "sx4": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "vitara": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "wagon r+": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
    },

    # ── Tata (4 models) ──
    "tata": {
        "indica": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "indigo": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "safari": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "xenon": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Tesla (5 models) ──
    "tesla": {
        "cybertruck": {"model_modifier": -5, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["בעיות בנייה"]},
        "model 3": {"model_modifier": +3, "confidence_boost": 0.1, "transmission_default": "other", "known_issues": []},
        "model s": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "other", "known_issues": []},
        "model x": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "other", "known_issues": []},
        "model y": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "other", "known_issues": []},
    },

    # ── Toyota (34 models) ──
    "toyota": {
        "auris": {"model_modifier": +3, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "avensis": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "aygo": {"model_modifier": +3, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "aygo x": {"model_modifier": +2, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "bz4x": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": ["EV חדש, ריקולים מוקדמים"]},
        "c-hr": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "camry": {"model_modifier": +4, "confidence_boost": 0.15, "transmission_default": "automatic", "known_issues": []},
        "carina e": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "manual", "known_issues": []},
        "celica": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "manual", "known_issues": []},
        "corolla": {"model_modifier": +4, "confidence_boost": 0.15, "transmission_default": "automatic", "known_issues": []},
        "corolla cross": {"model_modifier": +3, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "corona": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "manual", "known_issues": []},
        "crown": {"model_modifier": +3, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "fj cruiser": {"model_modifier": +4, "confidence_boost": 0.15, "transmission_default": "automatic", "known_issues": []},
        "gr86": {"model_modifier": +3, "confidence_boost": 0.1, "transmission_default": "manual", "known_issues": []},
        "gt86": {"model_modifier": +3, "confidence_boost": 0.1, "transmission_default": "manual", "known_issues": []},
        "highlander": {"model_modifier": +3, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "hilux": {"model_modifier": +5, "confidence_boost": 0.15, "transmission_default": "manual", "known_issues": []},
        "iq": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "land cruiser": {"model_modifier": +5, "confidence_boost": 0.15, "transmission_default": "automatic", "known_issues": []},
        "land cruiser prado": {"model_modifier": +5, "confidence_boost": 0.15, "transmission_default": "automatic", "known_issues": []},
        "previa": {"model_modifier": +3, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "prius": {"model_modifier": +5, "confidence_boost": 0.15, "transmission_default": "cvt", "known_issues": []},
        "prius+": {"model_modifier": +4, "confidence_boost": 0.1, "transmission_default": "cvt", "known_issues": []},
        "proace": {"model_modifier": +2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": []},
        "proace city": {"model_modifier": +2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": []},
        "rav4": {"model_modifier": +3, "confidence_boost": 0.15, "transmission_default": "automatic", "known_issues": []},
        "sienna": {"model_modifier": +4, "confidence_boost": 0.15, "transmission_default": "automatic", "known_issues": []},
        "starlet": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "manual", "known_issues": []},
        "supra": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["בסיס BMW Z4"]},
        "urban cruiser": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "verso": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "yaris": {"model_modifier": +3, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "yaris cross": {"model_modifier": +2, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Volkswagen (36 models) ──
    "volkswagen": {
        "amarok": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "arteon": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": []},
        "beetle / new beetle": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "bora": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "caddy": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": []},
        "corrado": {"model_modifier": -3, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים", "ספורטיבי"]},
        "crafter": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["מסחרי — עמידות"]},
        "e-up!": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": []},
        "golf": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "dct", "known_issues": []},
        "golf gti": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "dct", "known_issues": []},
        "golf plus / sportsvan": {"model_modifier": +0, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["ספורטיבי"]},
        "golf r": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": []},
        "golf variant": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "dct", "known_issues": []},
        "id. buzz": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": []},
        "id.3": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": []},
        "id.4": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": []},
        "id.5": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": []},
        "id.7": {"model_modifier": -2, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": []},
        "jetta": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "multivan": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "passat": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": []},
        "passat cc / cc": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": []},
        "phaeton": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "polo": {"model_modifier": +2, "confidence_boost": 0.1, "transmission_default": "dct", "known_issues": []},
        "scirocco": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": []},
        "sharan": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "t-cross": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "t-roc": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": []},
        "taigo": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": []},
        "tiguan": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": []},
        "tiguan allspace": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "dct", "known_issues": []},
        "touareg": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "touran": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "transporter / caravelle": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "up!": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "vento": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
    },

    # ── Volvo (18 models) ──
    "volvo": {
        "850": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "c30": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "c40 / ec40": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "em90": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["חדש מאוד"]},
        "ex30": {"model_modifier": +0, "confidence_boost": 0.0, "transmission_default": "other", "known_issues": []},
        "ex90": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["חדש מאוד"]},
        "s40": {"model_modifier": +0, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "s60": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "s80": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "s90": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "v40": {"model_modifier": -2, "confidence_boost": 0.05, "transmission_default": "manual", "known_issues": ["הופסק לפני 2010, חלפים מוגבלים"]},
        "v50": {"model_modifier": -1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": ["הופסק לפני 2015"]},
        "v60": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "v70 / xc70": {"model_modifier": +1, "confidence_boost": 0.1, "transmission_default": "automatic", "known_issues": []},
        "v90": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "xc40 / ex40": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "xc60": {"model_modifier": +1, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "xc90": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Voyah (2 models) ──
    "voyah": {
        "dream": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["חדש מאוד"]},
        "free": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Wey (2 models) ──
    "wey": {
        "coffee 01": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "coffee 02": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Xpeng (3 models) ──
    "xpeng": {
        "g6": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["חדש מאוד"]},
        "g9": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "p7": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
    },

    # ── Zeekr (3 models) ──
    "zeekr": {
        "001": {"model_modifier": +0, "confidence_boost": 0.05, "transmission_default": "automatic", "known_issues": []},
        "007": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["חדש מאוד"]},
        "x": {"model_modifier": -1, "confidence_boost": 0.0, "transmission_default": "automatic", "known_issues": ["חדש מאוד"]},
    },

}


# ═══════════════════════════════════════════════════════════════════
# LOOKUP FUNCTIONS
# ═══════════════════════════════════════════════════════════════════

def get_make_profile(make: str) -> Dict[str, Any]:
    """Get make-level profile. Case-insensitive."""
    return MAKE_PROFILES.get(make.strip().lower(), MAKE_DEFAULT)


def get_model_override(make: str, model: str) -> Optional[Dict[str, Any]]:
    """
    Get model-level override if exists.
    Tries exact match, then partial match on model name.
    Returns None if no override found (make-level only).
    """
    make_key = make.strip().lower()
    model_key = model.strip().lower()

    make_models = MODEL_OVERRIDES.get(make_key)
    if not make_models:
        return None

    # Exact match
    if model_key in make_models:
        return make_models[model_key]

    # Partial match: model_key contains known key or vice versa
    for known_model, override in make_models.items():
        if model_key.startswith(known_model) or known_model.startswith(model_key):
            return override
        # Normalize: remove spaces/hyphens
        clean_q = model_key.replace(" ", "").replace("-", "")
        clean_k = known_model.replace(" ", "").replace("-", "")
        if clean_q == clean_k or clean_q.startswith(clean_k) or clean_k.startswith(clean_q):
            return override

    return None


def get_exact_model_override(make: str, model: str) -> Optional[Dict[str, Any]]:
    """Get a model override only for exact case-insensitive/normalized matches."""
    make_key = make.strip().lower()
    model_key = model.strip().lower()

    make_models = MODEL_OVERRIDES.get(make_key)
    if not make_models or not model_key:
        return None

    if model_key in make_models:
        return make_models[model_key]

    clean_q = model_key.replace(" ", "").replace("-", "")
    if not clean_q:
        return None

    for known_model, override in make_models.items():
        clean_k = known_model.replace(" ", "").replace("-", "")
        if clean_q == clean_k:
            return override

    return None


def get_combined_score_modifier(make: str, model: str) -> Tuple[int, float, Optional[str]]:
    """
    Returns (total_modifier, confidence_boost, transmission_default).
    total_modifier = make.base_modifier + model.model_modifier
    Use: base = 62 + total_modifier
    """
    profile = get_make_profile(make)
    total = profile["base_modifier"]
    conf = 0.0
    trans = None

    override = get_model_override(make, model)
    if override:
        total += override.get("model_modifier", 0)
        conf = override.get("confidence_boost", 0.0)
        trans = override.get("transmission_default")

    return total, conf, trans
