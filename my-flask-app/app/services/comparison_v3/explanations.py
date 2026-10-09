# -*- coding: utf-8 -*-
"""Deterministic Hebrew texts of V3 (no LLM): the section influence line, the recommendation headline, the reasons
and the requirement notes. Built only from the rows, the validated profile, the hard constraints and the immutable
composition. Missing data is never framed as a disadvantage, and a category without rows has no section at all.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.comparison_v2.buyer_profile import PRIORITY_LABELS_HE
from app.services.comparison_v3.buyer_profile import FEATURE_LABELS_HE, MODE_GENERAL, PRIORITY_NAMES_HE
from app.services.comparison_v3.composer import BASIS_COMPOSITION, BASIS_HARD_CONSTRAINTS, NO_COMMON_DATA, NO_VEHICLE_MEETS_REQUIREMENTS
from app.services.comparison_v3.contracts import CHOICE_INSUFFICIENT, CHOICE_TIE, DECISION_UNAVAILABLE, NO_COMMON_DATA_HE
from app.services.comparison_v3.engine import CATEGORY_NEGLIGIBLE_CONTRIBUTION, FAIL, UNKNOWN
from app.services.comparison_v3.metrics import CATEGORIES

STRENGTH_LABEL_HE = {"clear": "יתרון ברור", "moderate": "יתרון מתון", "slight": "יתרון קל"}
IMPORTANCE_PHRASE_HE = {4: "קריטי עבורך", 3: "חשוב לך", 2: "בחשיבות בינונית עבורך", 1: "מעט חשוב לך", 0: "לא חשוב לך"}
FIT_LABEL_HE = {0: "התאמה חלשה", 1: "התאמה עם פשרות", 2: "התאמה סבירה", 3: "התאמה טובה", 4: "התאמה מצוינת"}

INFLUENCED, BALANCED, ZERO_WEIGHT, NO_TOWING_NEED = "influenced", "balanced", "zero_weight", "no_towing_need"
DECIDED_BY_REQUIREMENTS, JUDGMENT_UNAVAILABLE, DISPLAY_ONLY = "decided_by_requirements", "judgment_unavailable", "display_only"

UNWEIGHTED_INFLUENCE_HE = {
    "variant_details": "פרטי הזיהוי של הגרסאות מוצגים לעיון ואינם משפיעים על ההכרעה.",
    "history": "נתוני ההיסטוריה של משרד התחבורה מוצגים לעיון בלבד ואינם משפיעים על ההכרעה.",
}


def join_he(items: List[str]) -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    last = items[-1]
    conj = "ו" if "א" <= last[0] <= "ת" else "ו־"
    return ", ".join(items[:-1]) + " " + conj + last


def _pair_text(contribution: float, signals: List[Dict[str, Any]], pair: List[str], names: Dict[str, str]) -> str:
    a, b = pair
    favoured = a if contribution > 0 else b
    sign = 1 if favoured == a else -1
    helping = sorted([s for s in signals if s.get("contribution") and s["contribution"] * sign > 0],
                     key=lambda s: -abs(s["contribution"]))
    text = f"התחום השפיע לטובת {names[favoured]}"
    labels = [s["label_he"] for s in helping[:3]]
    if labels:
        text += f" בזכות {join_he(labels)}"
    top = helping[0] if helping else None
    if top and top.get("materiality_label_he"):
        text += f"; משמעות הפער לשימוש שלך: {top['materiality_label_he']}"
    elif top and top["source"] == "contextual":
        fa, fb = (top.get("fit") or {}).get(a), (top.get("fit") or {}).get(b)
        if fa is not None and fb is not None:
            text += (f"; {top['label_he']}: {names[a]}: {FIT_LABEL_HE[int(round(fa * 4))]}, "
                     f"{names[b]}: {FIT_LABEL_HE[int(round(fb * 4))]}")
    return text + "."


def section_cards(rows_by_cat: Dict[str, List[Dict[str, Any]]], composition: Dict[str, Any], profile: Dict[str, Any],
                  names: Dict[str, str]) -> Dict[str, Dict[str, Any]]:
    """One card per category that has rows (table order). ``influence_he`` is the collapsed section-header text."""
    weights = composition["weights"]
    pairs = list((composition.get("pairs") or {}).values())
    cards: Dict[str, Dict[str, Any]] = {}
    for cat in CATEGORIES:
        rows = rows_by_cat.get(cat["key"]) or []
        if not rows:
            continue                                        # a category without rows is absent
        dim = cat["dimension"]
        card = {"key": cat["key"], "label_he": cat["label_he"], "dimension": dim, "rows": [r["row_id"] for r in rows],
                "weight": weights.get(dim, 0) if dim else 0, "favoured_slot": None}
        status, text = _influence(cat["key"], dim, rows, weights, profile, composition, pairs, names, card)
        card["influence_status"], card["influence_he"] = status, text
        card["layers"] = {"influence": text}                # the summary payload reads layers.influence
        cards[cat["key"]] = card
    return cards


def _influence(cat, dim, rows, weights, profile, composition, pairs, names, card):
    if dim is None or all(r["display_only"] for r in rows) and not _dimension_signals(pairs, dim):
        return DISPLAY_ONLY, UNWEIGHTED_INFLUENCE_HE.get(cat, "הנתונים בתחום מוצגים לעיון ואינם משפיעים על ההכרעה.")
    leader = next((r["leader"] for r in rows if r.get("leader") in names), None)
    if dim == "towing" and not weights.get("towing"):
        if leader:
            return NO_TOWING_NEED, f"ל־{names[leader]} יתרון בגרירה, אך לא ציינת צורך בגרירה ולכן היתרון לא השפיע על ההכרעה."
        return NO_TOWING_NEED, "לא ציינת צורך בגרירה ולכן התחום לא השפיע על ההכרעה."
    if not weights.get(dim):
        if leader:
            return ZERO_WEIGHT, "הפער קיים, אבל התחום הוגדר אצלך כלא חשוב ולכן לא השפיע על ההכרעה."
        return ZERO_WEIGHT, "התחום הוגדר אצלך כלא חשוב ולכן לא השפיע על ההכרעה."
    if composition.get("basis") == BASIS_HARD_CONSTRAINTS or not pairs:
        return DECIDED_BY_REQUIREMENTS, "ההכרעה נקבעה לפי דרישות החובה שהגדרת; הנתונים בתחום מוצגים לעיון."
    if composition.get("outcome") == DECISION_UNAVAILABLE:
        return JUDGMENT_UNAVAILABLE, "מנוע השיפוט לא היה זמין, ולכן התחום לא שוקלל ומוצגים הנתונים בלבד."
    lines, favoured = [], set()
    for p in pairs:
        d = p["dimensions"][dim]
        if d["contribution"] is None:
            if any(s["status"] == "judgment_unavailable" for s in d["signals"]):
                lines.append("שיפוט משמעות הפער לא היה זמין, ולכן התחום לא השפיע על ההכרעה.")
            continue
        a, b = p["pair"]
        prefix = f"{names[a]} מול {names[b]}: " if len(pairs) > 1 else ""
        if abs(d["contribution"]) < CATEGORY_NEGLIGIBLE_CONTRIBUTION:
            lines.append(prefix + "הנתונים מאוזנים או שהפערים זניחים לצרכים שהגדרת, ולכן התחום כמעט לא השפיע.")
        else:
            favoured.add(a if d["contribution"] > 0 else b)
            lines.append(prefix + _pair_text(d["contribution"], d["signals"], p["pair"], names))
    if not lines:
        return BALANCED, "הנתונים בתחום מאוזנים בין הרכבים."
    if not favoured:
        return BALANCED, " ".join(dict.fromkeys(lines))
    if len(favoured) == 1 and len(pairs) == 1:
        card["favoured_slot"] = next(iter(favoured))
    return INFLUENCED, " ".join(dict.fromkeys(lines))


def _dimension_signals(pairs, dim) -> bool:
    return any(p["dimensions"][dim]["signals"] for p in pairs)


def recommendation_view(composition: Dict[str, Any], profile: Dict[str, Any], names: Dict[str, str]) -> Dict[str, Any]:
    outcome = composition["outcome"]
    general = profile.get("mode") == MODE_GENERAL
    view: Dict[str, Any] = {"outcome": outcome, "recommended_slot": composition.get("recommended_slot"),
                            "basis": composition.get("basis"), "strength": composition.get("strength")}
    if outcome in names:
        view["title_he"] = "עדיפות כוללת בהשוואה כללית" if general else "מתאים יותר לצרכים שהגדרת"
        view["name"] = names[outcome]
        if composition.get("basis") == BASIS_HARD_CONSTRAINTS:
            view["subtitle_he"] = "נבחר משום שהרכבים האחרים אינם עומדים בדרישות החובה שהגדרת."
        else:
            view["strength_label_he"] = STRENGTH_LABEL_HE.get(composition.get("strength"))
    elif composition.get("reason") == NO_COMMON_DATA:
        view["title_he"] = NO_COMMON_DATA_HE
    elif outcome == CHOICE_TIE:
        view["title_he"] = "אין כרגע יתרון משמעותי בהשוואה כללית" if general else "אין כרגע יתרון משמעותי לצרכים שהגדרת"
    elif outcome == CHOICE_INSUFFICIENT:
        view["title_he"] = "הנתונים המשותפים אינם מספיקים להכרעה כללית" if general else "הנתונים המשותפים אינם מספיקים להכרעה מותאמת"
    elif outcome == NO_VEHICLE_MEETS_REQUIREMENTS:
        view["title_he"] = "אף אחד מהרכבים אינו עומד בכל דרישות החובה שהגדרת"
    else:
        view["title_he"] = "ההכרעה המותאמת אינה זמינה כרגע, ולכן מוצגים הנתונים בלבד"
    return view


def _importance(dimension: str, profile: Dict[str, Any], weights: Dict[str, int]) -> str:
    if dimension == "towing":
        return f"ציינת צורך בגרירה של {profile.get('towing_braked_required_kg'):,} ק״ג"
    name = PRIORITY_NAMES_HE.get(dimension, dimension)
    if profile.get("mode") == MODE_GENERAL:
        return f"בהשוואה כללית ל{name} משקל שווה לשאר התחומים"
    return f"{name}: {IMPORTANCE_PHRASE_HE[weights.get(dimension, 0)]}"


def reason_texts(reasons, composition, profile, names) -> Dict[str, List[str]]:
    winner = composition.get("recommended_slot")
    out: Dict[str, List[str]] = {"for": [], "against": []}
    if not winner or composition.get("basis") != BASIS_COMPOSITION:
        return out
    weights = composition["weights"]
    for r in reasons.get("for", []):
        imp = _importance(r["dimension"], profile, weights)
        if r["source"] == "objective":
            text = f"{imp}. ל־{names[winner]} יתרון ב{r['label_he']}"
            if r.get("materiality_label_he"):
                text += f", ומשמעות הפער לשימוש שהגדרת הוערכה כ{r['materiality_label_he']}"
            out["for"].append(text + ".")
        elif r["source"] == "contextual":
            out["for"].append(f"{imp}. {names[winner]} הותאם טוב יותר מבחינת {r['label_he']} לפי הנתונים שהזנת.")
        elif r["group"].startswith("feature:"):
            feature = FEATURE_LABELS_HE.get(r["group"].split(":", 1)[1], r["label_he"])
            out["for"].append(f"ציינת {feature} כאבזור רצוי, והוא קיים ב־{names[winner]}.")
        else:
            out["for"].append(f"ציינת הנעה כפולה כרצויה, ול־{names[winner]} יש הנעה כפולה.")
    for r in reasons.get("against", []):
        others = [names[s] for s in r.get("against") or [] if s in names]
        if others:
            out["against"].append(f"ל־{join_he(others)} יתרון ב{r['label_he']}, אך הוא לא הכריע את התמונה הכוללת לפי העדיפויות שלך.")
    return out


def constraint_notes(constraints: Dict[str, Any], names: Dict[str, str]) -> List[Dict[str, str]]:
    notes = []
    for c in constraints.get("constraints") or []:
        for slot, r in c["per_car"].items():
            if r["status"] == FAIL:
                notes.append({"level": "fail", "slot": slot,
                              "text_he": f"{names[slot]} אינו עומד בדרישת {c['label_he']} ({c['requirement_he']}; בפועל: {r['display']})."})
            elif r["status"] == UNKNOWN:
                notes.append({"level": "unknown", "slot": slot,
                              "text_he": f"לא ניתן לאמת את דרישת {c['label_he']} עבור {names[slot]}, וזה אינו נחשב לכישלון."})
    return notes


def priority_label(value: Optional[int]) -> str:
    return PRIORITY_LABELS_HE.get(value or 0, "")
