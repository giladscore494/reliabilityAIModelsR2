# -*- coding: utf-8 -*-
"""Deterministic Hebrew category explanations (no LLM call).

Built only from the deterministic category evidence and the immutable JEV
decision. It never changes a decision and never frames missing data as a
disadvantage. Structure:

* ``what``     — what the category evaluates (static per category);
* ``evidence`` — what the compared data says in this comparison;
* ``why``      — why the category decision was reached (mirrors JEV);
* ``gaps``     — missing / conflicting / not-comparable information.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.comparison_v2.contracts import CHOICE_INSUFFICIENT, CHOICE_TIE, DECISION_UNAVAILABLE, NOT_APPLICABLE

CATEGORY_SCOPE_HE = {
    "safety": "קטגוריה זו משווה את נתוני הבטיחות הזמינים, כולל דירוגי משרד התחבורה, כריות אוויר ומערכות סיוע לנהג.",
    "performance": "קטגוריה זו בוחנת הספק, מומנט, תאוצה, מהירות מרבית ומאפייני מערכת ההנעה כאשר הנתונים זמינים.",
    "efficiency": "קטגוריה זו בוחנת צריכת דלק או צריכת אנרגיה רשמית, לפי סוג ההנעה של כל רכב.",
    "electric_and_charging": "קטגוריה זו בוחנת קיבולת סוללה, טווח נסיעה חשמלי ויכולות טעינה.",
    "practicality": "קטגוריה זו בוחנת מושבים, דלתות, סוג מרכב, מידות ונפח תא מטען. רכב גדול יותר אינו בהכרח עדיף.",
    "towing_and_utility": "קטגוריה זו בוחנת את כושר הגרירה הרשמי של כל רכב.",
    "environment": "קטגוריה זו בוחנת נתוני פליטות וזיהום ממשלתיים, כשכל מדד מושווה רק מול אותו תקן מדידה.",
    "official_price_and_warranty": "קטגוריה זו בוחנת מחיר ותנאי אחריות ממקורות ישראליים רשמיים בלבד. אחריות אינה מדד לאמינות.",
}

# (indefinite, definite)
POWERTRAIN_NOUN_HE = {
    "ev": ("רכב חשמלי", "הרכב החשמלי"),
    "phev": ("רכב פלאג-אין", "רכב הפלאג-אין"),
    "hybrid": ("רכב היברידי", "הרכב ההיברידי"),
    "diesel": ("רכב דיזל", "רכב הדיזל"),
    "combustion": ("רכב בנזין", "רכב הבנזין"),
    "unknown": ("רכב", "הרכב"),
}

NOT_COMPARABLE_REASON_HE = {
    "RANGE_STANDARD_MISMATCH": "תקני מדידה שונים",
    "NOT_CROSS_POWERTRAIN_COMPARABLE": "סוגי הנעה שונים",
    "DC_CHARGE_WINDOW_MISMATCH": "טווחי טעינה שונים",
    "PRICE_MARKET_MISMATCH": "שוק או מטבע שונים",
}


def join_he(items: List[str]) -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    last = items[-1]
    # "ו" attaches directly to a Hebrew word; use maqaf before Latin/digits.
    conj = "ו" if "\u05d0" <= last[0] <= "\u05ea" else "ו־"
    return ", ".join(items[:-1]) + " " + conj + last


def _powertrain_noun(snapshot: Dict[str, Any], definite: bool = False) -> str:
    family = snapshot["derived"]["powertrain_family"]
    if family == "combustion":
        facts = snapshot["government"]["facts"]
        if facts.get("propulsion") == "hybrid":
            family = "hybrid"
        elif facts.get("fuel_type") == "diesel":
            family = "diesel"
    forms = POWERTRAIN_NOUN_HE.get(family, POWERTRAIN_NOUN_HE["unknown"])
    return forms[1] if definite else forms[0]


def _labels(evidence: Dict[str, Any], metrics: List[str], limit: int = 4) -> List[str]:
    by_key = {r["metric"]: r["label_he"] for r in evidence.get("atomic_results") or []}
    return [by_key.get(m, m) for m in metrics[:limit]]


def _evidence_sentence(evidence: Dict[str, Any], names: Dict[str, str]) -> str:
    compared = [r for r in evidence.get("atomic_results") or [] if r.get("status") == "compared"]
    leads: Dict[str, List[str]] = {}
    ties: List[str] = []
    for r in compared:
        if r["leader"] == CHOICE_TIE:
            ties.append(r["label_he"])
        elif r["leader"] in names:
            leads.setdefault(r["leader"], []).append(r["label_he"])
    parts = []
    for slot in names:
        if leads.get(slot):
            parts.append(f"{join_he(leads[slot][:3])} — עדיפות ל־{names[slot]}")
    if ties:
        parts.append(f"{join_he(ties[:3])} — ללא הבדל משמעותי")
    if parts:
        return "בהשוואה הזו: " + "; ".join(parts) + "."
    contextual = [c["label_he"] for c in evidence.get("contextual_facts") or []]
    if contextual:
        return f"הנתונים הזמינים בתחום הם הקשריים ({join_he(contextual[:3])}) ונשקלים לפי צרכי השימוש, לא כמדד של טוב או רע."
    return "אין בתחום זה נתונים בני-השוואה ישירה בין הרכבים שנבחרו."


def _cross_powertrain_sentence(category_label: str, evidence: Dict[str, Any], snapshots: Dict[str, Dict[str, Any]], names: Dict[str, str]) -> str:
    scope = evidence.get("scope_slots") or []
    if len(scope) == 1:
        inside = scope[0]
        others = [s for s in snapshots if s != inside]
        other_nouns = sorted({_powertrain_noun(snapshots[s]) for s in others})
        return (
            f"נתוני {category_label} מוצגים עבור {_powertrain_noun(snapshots[inside], definite=True)} "
            f"({names[inside]}), אך אינם בני-השוואה ישירה מול {join_he(other_nouns)} ולכן לא ניתנה הכרעה בתחום."
        )
    return (
        f"נתוני {category_label} נמדדים ביחידות שונות לפי סוג ההנעה של כל רכב, ולכן הם מוצגים לכל רכב בנפרד "
        "ללא הכרעה ישירה בתחום."
    )


def _why_sentence(decision: Dict[str, Any], evidence: Dict[str, Any], names: Dict[str, str]) -> str:
    choice = decision.get("choice")
    partial = bool(evidence.get("missing_metrics") or evidence.get("conflicted_metrics"))
    if choice in names:
        text = f"על בסיס הנתונים שנבדקו, מנוע ההכרעה נתן עדיפות ל־{names[choice]} בתחום זה."
        if partial:
            text += " ההכרעה מבוססת על כיסוי חלקי של הנתונים."
        return text
    if choice == CHOICE_TIE:
        leaders = {r["leader"] for r in evidence.get("atomic_results") or [] if r.get("status") == "compared" and r.get("leader") in names}
        if len(leaders) >= 2:
            return "מנוע ההכרעה קבע שאין יתרון משמעותי: לכל רכב יתרון במדדים אחרים, והתמונה הכוללת בתחום מאוזנת."
        return "מנוע ההכרעה קבע שאין יתרון משמעותי: ההבדלים בנתונים שנבדקו קטנים מכדי להכריע."
    if choice == CHOICE_INSUFFICIENT:
        return "אין מספיק מידע מאומת ובר-השוואה כדי לבחור רכב בתחום זה."
    if choice == DECISION_UNAVAILABLE:
        return "מנוע ההכרעה לא היה זמין, ולכן מוצגים כאן הנתונים בלבד ללא הכרעה."
    return ""


def _gaps(evidence: Dict[str, Any]) -> List[str]:
    gaps: List[str] = []
    missing = _labels(evidence, evidence.get("missing_metrics") or [], limit=5)
    if missing:
        more = len(evidence.get("missing_metrics") or []) - len(missing)
        suffix = f" ועוד {more}" if more > 0 else ""
        gaps.append(f"חסרים נתונים מאומתים עבור: {', '.join(missing)}{suffix}. נתון חסר אינו נחשב לחיסרון.")
    conflicts = _labels(evidence, evidence.get("conflicted_metrics") or [])
    if conflicts:
        gaps.append(f"נמצאה סתירה בין מקורות רשמיים ולכן הנתון לא השתתף בהכרעה: {', '.join(conflicts)}.")
    not_comp = [n for n in evidence.get("not_comparable") or [] if n.get("reason") != "NOT_CROSS_POWERTRAIN_COMPARABLE" or evidence.get("status") != "cross_powertrain_descriptive"]
    if not_comp:
        items = [f"{n['label_he']} ({NOT_COMPARABLE_REASON_HE.get(n.get('reason'), 'לא בר-השוואה')})" for n in not_comp[:4]]
        gaps.append(f"לא בני-השוואה ישירה: {', '.join(items)}.")
    return gaps


def build_category_explanation(
    category: str,
    evidence: Dict[str, Any],
    decision: Dict[str, Any],
    names: Dict[str, str],
    snapshots: Dict[str, Dict[str, Any]],
    category_label: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Return {what, evidence, why, gaps, text} or None for not-applicable categories."""
    if evidence.get("status") == NOT_APPLICABLE:
        return None
    what = CATEGORY_SCOPE_HE.get(category, "")
    label = category_label or evidence.get("label_he") or category
    if evidence.get("status") == "cross_powertrain_descriptive":
        evidence_text = ""
        why = _cross_powertrain_sentence(label, evidence, snapshots, names)
    else:
        evidence_text = _evidence_sentence(evidence, names)
        why = _why_sentence(decision, evidence, names)
    text = " ".join(t for t in (what, evidence_text, why) if t)
    return {"what": what, "evidence": evidence_text, "why": why, "gaps": _gaps(evidence), "text": text}
