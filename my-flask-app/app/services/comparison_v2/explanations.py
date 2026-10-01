# -*- coding: utf-8 -*-
"""Deterministic Hebrew explanations for V2/2 (no LLM call).

Built only from deterministic evidence, the validated buyer profile, hard
constraint results and the immutable composition. Every category card has
four layers:

* ``what``       — what the category checks (static);
* ``importance`` — what the user declared as important here (never from JEV);
* ``data``       — what the validated data says;
* ``influence``  — why the category did or did not influence the result.

Missing data is never framed as a disadvantage.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.comparison_v2.buyer_profile import (
    FEATURE_LABELS_HE,
    MODE_GENERAL,
    PRIORITY_LABELS_HE,
    PRIORITY_NAMES_HE,
)
from app.services.comparison_v2.composer import (
    BASIS_COMPOSITION,
    BASIS_HARD_CONSTRAINTS,
    NO_VEHICLE_MEETS_REQUIREMENTS,
)
from app.services.comparison_v2.contracts import CHOICE_INSUFFICIENT, CHOICE_TIE, DECISION_UNAVAILABLE, NOT_APPLICABLE
from app.services.comparison_v2.decision_model import (
    CATEGORY_DIMENSIONS,
    CATEGORY_NEGLIGIBLE_CONTRIBUTION,
    FIT_LABEL_HE,
    GROUP_DIMENSION,
)
from app.services.comparison_v2.deterministic_engine import (
    CATEGORIES,
    CATEGORY_LABELS_HE,
    EVIDENCE_CROSS_POWERTRAIN,
    EVIDENCE_NONE_COMPARABLE,
    coverage_label_he,
)
from app.services.comparison_v2.hard_constraints import FAIL, UNKNOWN

CATEGORY_SCOPE_HE = {
    "safety": "בודקת דירוגי בטיחות ממשלתיים, מערכות סיוע לנהג, כריות אוויר ומערכות בטיחות זמינות.",
    "performance": "בודקת הספק, מומנט, תאוצה, מהירות מרבית ומאפייני הנעה כאשר קיימים נתונים מאומתים.",
    "efficiency": "בודקת צריכת דלק או אנרגיה רק כאשר הנתונים בני-השוואה באותן יחידות ובאותה מסגרת.",
    "electric_and_charging": "בודקת טווח, קיבולת סוללה ויכולות טעינת AC/DC עבור רכבים שבהם הנתונים רלוונטיים.",
    "practicality": "בודקת התאמה למספר הנוסעים, נפח מטען, מידות, מרכב ומגבלות החניה שהזנת.",
    "towing_and_utility": "בודקת את יכולת הגרירה מול צורך אמיתי שהזנת.",
    "environment": "בודקת רק מדדי פליטה בני-השוואה מאותו סוג.",
    "official_price_and_warranty": "בודקת מחיר ישראלי רשמי ואחריות כאשר נמצאו מקורות רשמיים מתאימים. אחריות אינה מדד לאמינות.",
    "equipment_and_convenience": "בודקת התאמה לאבזור החובה והאבזור הרצוי שהגדרת. אין כאן מדידה של נוחות נסיעה.",
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
    "DC_CHARGE_WINDOW_MISSING": "חלון הטעינה (מ־% עד %) לא פורסם",
    "PRICE_MARKET_MISMATCH": "שוק או מטבע שונים",
}

STRENGTH_LABEL_HE = {"clear": "יתרון ברור", "moderate": "יתרון מתון", "slight": "יתרון קל"}
IMPORTANCE_PHRASE_HE = {4: "קריטי עבורך", 3: "חשוב לך", 2: "בחשיבות בינונית עבורך", 1: "מעט חשוב לך", 0: "לא חשוב לך"}

# Card influence states (UI vocabulary).
INFLUENCED = "influenced"
BALANCED = "balanced"
ZERO_WEIGHT = "zero_weight"
NO_TOWING_NEED = "no_towing_need"
DECIDED_BY_REQUIREMENTS = "decided_by_requirements"
JUDGMENT_UNAVAILABLE = "judgment_unavailable"
INSUFFICIENT_DATA = "insufficient_data"
NOT_DIRECTLY_COMPARABLE = "not_directly_comparable"
NO_DECLARED_FEATURES = "no_declared_features"

MODEL_CERTAINTY_LABEL_HE = "ודאות מודל בשיפוט הזה"
MODEL_CERTAINTY_NOTE_HE = "מדד לריכוז התשובה של מנוע השיפוט בשאלה הספציפית — אינו הסתברות שההמלצה נכונה."


def join_he(items: List[str]) -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    last = items[-1]
    # "ו" attaches directly to a Hebrew word; use maqaf before Latin/digits.
    conj = "ו" if "א" <= last[0] <= "ת" else "ו־"
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


def _to_noun(snapshot: Dict[str, Any]) -> str:
    """'ל' + definite noun ('להרכב' contracts to 'לרכב')."""
    definite = _powertrain_noun(snapshot, definite=True)
    return "ל" + (definite[1:] if definite.startswith("ה") else definite)


def _labels(evidence: Dict[str, Any], metrics: List[str], limit: int = 4) -> List[str]:
    by_key = {r["metric"]: r["label_he"] for r in evidence.get("atomic_results") or []}
    return [by_key.get(m, m) for m in metrics[:limit]]


# ---------------------------------------------------------------------------
# data layer
# ---------------------------------------------------------------------------
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
        return f"הנתונים הזמינים בתחום הם הקשריים ({join_he(contextual[:3])}) ונשקלים לפי צרכי השימוש שלך, לא כמדד של טוב או רע."
    return "אין בתחום זה נתונים מאומתים בני-השוואה ישירה בין הרכבים שנבחרו."


def _cross_powertrain_sentence(category_label: str, evidence: Dict[str, Any], snapshots: Dict[str, Dict[str, Any]], names: Dict[str, str]) -> str:
    scope = evidence.get("scope_slots") or []
    if len(scope) == 1:
        inside = scope[0]
        others = [s for s in snapshots if s != inside]
        other_names = [names[s] for s in others]
        return (
            f"נתוני {category_label} של {names[inside]} מוצגים משום שהם רלוונטיים {_to_noun(snapshots[inside])}, "
            f"אך אין להם נתון מקביל ב־{join_he(other_names)} ולכן לא נבחר מנצח ישיר בתחום הזה."
        )
    return (
        f"נתוני {category_label} נמדדים ביחידות שונות לפי סוג ההנעה של כל רכב, ולכן הם מוצגים לכל רכב בנפרד "
        "ללא הכרעה ישירה בתחום."
    )


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
    not_comp = [n for n in evidence.get("not_comparable") or []
                if n.get("reason") != "NOT_CROSS_POWERTRAIN_COMPARABLE" or evidence.get("status") != EVIDENCE_CROSS_POWERTRAIN]
    if not_comp:
        items = [f"{n['label_he']} ({NOT_COMPARABLE_REASON_HE.get(n.get('reason'), 'לא בר-השוואה')})" for n in not_comp[:4]]
        gaps.append(f"לא בני-השוואה ישירה: {', '.join(items)}.")
    return gaps


# ---------------------------------------------------------------------------
# importance layer (user-declared only)
# ---------------------------------------------------------------------------
def importance_items(category: str, profile: Dict[str, Any], weights: Dict[str, int]) -> List[Dict[str, Any]]:
    items = []
    for dim in CATEGORY_DIMENSIONS[category]:
        if dim == "towing":
            need = profile.get("towing_braked_required_kg")
            items.append({"dimension": dim, "name_he": "גרירה", "value": weights.get(dim, 0),
                          "label_he": f"נדרשת גרירה של {need:,} ק״ג" if need else "לא הוגדר צורך בגרירה"})
            continue
        if dim not in (profile.get("priorities") or {}):
            continue
        value = weights.get(dim, 0)
        items.append({"dimension": dim, "name_he": PRIORITY_NAMES_HE.get(dim, dim), "value": value, "label_he": PRIORITY_LABELS_HE[value]})
    return items


def _importance_text(items: List[Dict[str, Any]], profile: Dict[str, Any]) -> str:
    if not items:
        return "התחום אינו רלוונטי לרכבים שנבחרו."
    text = " · ".join(f"{i['name_he']}: {i['label_he']}" for i in items)
    if profile.get("mode") == MODE_GENERAL and any(i["dimension"] != "towing" for i in items):
        text += " (השוואה כללית — משקל שווה לכל התחומים)"
    return text


# ---------------------------------------------------------------------------
# influence layer
# ---------------------------------------------------------------------------
def _signal_view(signal: Dict[str, Any], pair: List[str], names: Dict[str, str], answers: Dict[str, Any]) -> Dict[str, Any]:
    a, b = pair
    view = {
        "label_he": signal["label_he"],
        "source": signal["source"],
        "group": signal["group"],
        "pair": pair,
        "status": signal["status"],
        "usable": signal["usable"],
        "contribution": signal.get("contribution"),
    }
    if signal["source"] == "objective":
        direction = signal.get("code_direction")
        view["code_direction"] = direction
        view["favoured_name"] = names.get(direction) if direction in names else None
        if signal.get("materiality_label_he"):
            view["materiality_label_he"] = signal["materiality_label_he"]
            view["materiality_level"] = signal.get("materiality_level")
        ans = answers.get(signal.get("question_id") or "") or {}
        if ans.get("confidence") is not None:
            view["model_certainty"] = ans["confidence"]
    elif signal["source"] == "contextual":
        view["fit"] = {}
        for slot in (a, b):
            value = (signal.get("fit") or {}).get(slot)
            status = (signal.get("fit_status") or {}).get(slot)
            if value is None:
                view["fit"][slot] = {"label_he": "שיפוט לא זמין", "status": status}
            elif status == "non_plugin_baseline":
                view["fit"][slot] = {"label_he": "לא תלוי בטעינה (נייטרלי)", "status": status}
            else:
                view["fit"][slot] = {"label_he": FIT_LABEL_HE[int(round(value * 4))], "status": status}
    else:
        if signal.get("value"):
            view["favoured_name"] = names[a] if signal["value"] > 0 else names[b]
    return view


def _pair_influence_text(cat_label: str, contribution: float, signals: List[Dict[str, Any]], pair: List[str], names: Dict[str, str]) -> str:
    a, b = pair
    favoured = a if contribution > 0 else b
    sign = 1 if favoured == a else -1
    helping = sorted([s for s in signals if s.get("contribution") and s["contribution"] * sign > 0],
                     key=lambda s: -abs(s["contribution"]))
    labels = [s["label_he"] for s in helping[:3]]
    text = f"התחום השפיע לטובת {names[favoured]}"
    if labels:
        text += f" בזכות {join_he(labels)}"
    top = helping[0] if helping else None
    if top and top.get("materiality_label_he"):
        text += f"; משמעות הפער לשימוש שלך: {top['materiality_label_he']}"
    elif top and top["source"] == "contextual":
        fa, fb = (top.get("fit") or {}).get(a), (top.get("fit") or {}).get(b)
        if fa and fb:
            text += f"; {top['label_he']}: {names[a]} — {fa['label_he']}, {names[b]} — {fb['label_he']}"
    return text + "."


def _has_difference(signals: List[Dict[str, Any]], names: Dict[str, str]) -> Optional[str]:
    for s in signals:
        if s["source"] == "objective" and s.get("code_direction") in names:
            return s["code_direction"]
    return None


def build_category_cards(
    comparison: Dict[str, Any],
    composition: Dict[str, Any],
    profile: Dict[str, Any],
    snapshots: Dict[str, Dict[str, Any]],
    pairwise: Dict[str, Any],
    answers: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    names = {slot: snap["identity"]["display_name"] for slot, snap in snapshots.items()}
    weights = composition["weights"]
    pairs = list(composition.get("pairs", {}).values())
    cards: Dict[str, Dict[str, Any]] = {}
    for cat in CATEGORIES:
        ev = comparison["categories"][cat]
        label = CATEGORY_LABELS_HE[cat]
        dims = CATEGORY_DIMENSIONS[cat]
        weight = sum(weights.get(d, 0) for d in dims)
        importance = importance_items(cat, profile, weights)
        # every objective group of this category across ALL pairs (also for
        # zero-weight / non-eligible explanations)
        raw_groups = [
            {"source": "objective", "code_direction": g.get("direction"), "label_he": cat}
            for p in pairwise.values() for grp, g in p["groups"].items()
            if g.get("category") == cat and grp in GROUP_DIMENSION
        ]
        if ev["status"] == EVIDENCE_CROSS_POWERTRAIN:
            data_text = _cross_powertrain_sentence(label, ev, snapshots, names)
        else:
            data_text = _evidence_sentence(ev, names)

        pair_views: List[Dict[str, Any]] = []
        for p in pairs:
            cat_signals = [s for d in dims for s in p["dimensions"][d]["signals"] if (s.get("category") or cat) == cat]
            contribs = [p["dimensions"][d]["contribution"] for d in dims if p["dimensions"][d]["contribution"] is not None]
            pair_views.append({
                "pair": p["pair"],
                "contribution": round(sum(contribs), 4) if contribs else None,
                "signals": [_signal_view(s, p["pair"], names, answers) for s in cat_signals],
            })

        status, influence, favoured = _influence(cat, label, ev, weight, profile, composition, pair_views, raw_groups, names)
        if status in (INFLUENCED, BALANCED) and ev["status"] != EVIDENCE_CROSS_POWERTRAIN:
            fits = [s for pv in pair_views for s in pv["signals"] if s["source"] == "contextual" and s.get("fit")]
            seen = set()
            for s in fits:
                for slot, f in s["fit"].items():
                    if (s["group"], slot) in seen:
                        continue
                    seen.add((s["group"], slot))
                    data_text += f" {s['label_he']} — {names[slot]}: {f['label_he']}."
        cards[cat] = {
            "key": cat,
            "label_he": label,
            "evidence_status": {
                NOT_APPLICABLE: NOT_APPLICABLE,
                EVIDENCE_CROSS_POWERTRAIN: NOT_DIRECTLY_COMPARABLE,
                EVIDENCE_NONE_COMPARABLE: INSUFFICIENT_DATA,
            }.get(ev["status"], "ready"),
            "influence_status": status,
            "favoured_slot": favoured,
            "weight": weight,
            "importance": importance,
            "layers": {
                "what": CATEGORY_SCOPE_HE[cat],
                "importance": _importance_text(importance, profile),
                "data": data_text,
                "influence": influence,
            },
            "pairs": pair_views,
            "gaps": _gaps(ev),
            "evidence": ev,
        }
    return cards


def _influence(cat, label, ev, weight, profile, composition, pair_views, raw_groups, names):
    """Return (status, text, favoured_slot)."""
    top_priority = max([composition["weights"].get(d, 0) for d in CATEGORY_DIMENSIONS[cat]] or [0])
    if ev["status"] == NOT_APPLICABLE:
        return NOT_APPLICABLE, "התחום אינו רלוונטי לרכבים שנבחרו ולכן לא השפיע.", None
    leader = _has_difference(raw_groups, names)
    if cat == "towing_and_utility" and weight == 0:
        if leader:
            return NO_TOWING_NEED, f"ל־{names[leader]} יתרון בגרירה, אך לא ציינת צורך בגרירה ולכן היתרון לא השפיע על ההתאמה הכוללת.", None
        return NO_TOWING_NEED, "לא ציינת צורך בגרירה ולכן התחום לא השפיע על ההתאמה הכוללת.", None
    if weight == 0:
        if leader:
            return ZERO_WEIGHT, "הפער קיים, אבל התחום הוגדר אצלך כלא חשוב ולכן לא השפיע על ההכרעה הכוללת.", None
        return ZERO_WEIGHT, "התחום הוגדר אצלך כלא חשוב ולכן לא השפיע על ההכרעה הכוללת.", None
    if cat == "equipment_and_convenience" and not profile.get("nice_to_have_features"):
        return NO_DECLARED_FEATURES, ("לא הגדרת אבזור רצוי, ולכן התחום אינו משפיע על ההכרעה — רכב אינו מקבל יתרון רק משום "
                                      "שיש בו יותר אבזור. אבזור חובה נבדק בנפרד כדרישה."), None
    if composition.get("basis") == BASIS_HARD_CONSTRAINTS or not pair_views:
        return DECIDED_BY_REQUIREMENTS, "ההכרעה נקבעה לפי דרישות החובה שהגדרת; הנתונים בתחום מוצגים לעיון.", None
    if composition.get("outcome") == DECISION_UNAVAILABLE:
        return JUDGMENT_UNAVAILABLE, "מנוע השיפוט לא היה זמין, ולכן התחום לא שוקלל ומוצגים הנתונים בלבד.", None

    usable = [pv for pv in pair_views if pv["contribution"] is not None]
    if not usable:
        if ev["status"] == EVIDENCE_CROSS_POWERTRAIN:
            return NOT_DIRECTLY_COMPARABLE, "אין נתון בר-השוואה ישירה בין סוגי ההנעה, ולכן התחום לא השפיע על ההכרעה.", None
        if any(s["status"] == "judgment_unavailable" for pv in pair_views for s in pv["signals"]):
            return JUDGMENT_UNAVAILABLE, "שיפוט משמעות הפער לא היה זמין, ולכן התחום לא השפיע על ההכרעה.", None
        if top_priority >= 3:
            return INSUFFICIENT_DATA, "התחום חשוב לך מאוד, אך אין מספיק מידע מאומת ולכן לא נתנו יתרון לאף רכב.", None
        return INSUFFICIENT_DATA, "אין מספיק מידע מאומת בר-השוואה, ולכן התחום לא השפיע על ההכרעה.", None

    lines = []
    favoured_slots = set()
    for pv in usable:
        c = pv["contribution"]
        a, b = pv["pair"]
        prefix = f"{names[a]} מול {names[b]}: " if len(pair_views) > 1 else ""
        if abs(c) < CATEGORY_NEGLIGIBLE_CONTRIBUTION:
            lines.append(prefix + "הנתונים מאוזנים או שהפערים זניחים לצרכים שהגדרת, ולכן התחום כמעט לא השפיע.")
        else:
            favoured_slots.add(a if c > 0 else b)
            lines.append(prefix + _pair_influence_text(label, c, pv["signals"], pv["pair"], names))
    if not favoured_slots:
        return BALANCED, " ".join(lines), None
    favoured = next(iter(favoured_slots)) if len(favoured_slots) == 1 and len(pair_views) == 1 else None
    return INFLUENCED, " ".join(lines), favoured


# ---------------------------------------------------------------------------
# headline, reasons, hard constraints
# ---------------------------------------------------------------------------
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
    elif outcome == CHOICE_TIE:
        view["title_he"] = "אין כרגע יתרון משמעותי בהשוואה כללית" if general else "אין כרגע יתרון משמעותי לצרכים שהגדרת"
    elif outcome == CHOICE_INSUFFICIENT:
        view["title_he"] = "אין מספיק מידע מאומת להכרעה כללית" if general else "אין מספיק מידע מאומת להכרעה מותאמת"
    elif outcome == NO_VEHICLE_MEETS_REQUIREMENTS:
        view["title_he"] = "אף אחד מהרכבים אינו עומד בכל דרישות החובה שהגדרת"
    else:
        view["title_he"] = "ההכרעה המותאמת אינה זמינה כרגע — מוצגים הנתונים בלבד"
    cov = composition.get("effective_weight_coverage")
    if cov is not None:
        view["evidence_coverage"] = cov
        view["evidence_coverage_label_he"] = coverage_label_he(cov)
    return view


def _importance_phrase(dimension: str, profile: Dict[str, Any], weights: Dict[str, int]) -> str:
    name = PRIORITY_NAMES_HE.get(dimension, "גרירה" if dimension == "towing" else dimension)
    if dimension == "towing":
        return f"ציינת צורך בגרירה של {profile.get('towing_braked_required_kg'):,} ק״ג"
    if profile.get("mode") == MODE_GENERAL:
        return f"בהשוואה כללית ל{name} משקל שווה לשאר התחומים"
    return f"{name} — {IMPORTANCE_PHRASE_HE[weights.get(dimension, 0)]}"


def reason_texts(reasons: Dict[str, List[Dict[str, Any]]], composition: Dict[str, Any], profile: Dict[str, Any],
                 names: Dict[str, str]) -> Dict[str, List[str]]:
    winner = composition.get("recommended_slot")
    weights = composition["weights"]
    out: Dict[str, List[str]] = {"for": [], "against": []}
    if not winner or composition.get("basis") != BASIS_COMPOSITION:
        return out
    for r in reasons.get("for", []):
        imp = _importance_phrase(r["dimension"], profile, weights)
        if r["source"] == "objective":
            text = f"{imp}. ל־{names[winner]} יתרון מאומת ב{r['label_he']}"
            if r.get("materiality_label_he"):
                text += f", ומשמעות הפער לשימוש שהגדרת הוערכה כ{r['materiality_label_he']}"
            out["for"].append(text + ".")
        elif r["source"] == "contextual":
            out["for"].append(f"{imp}. {names[winner]} הותאם טוב יותר מבחינת {r['label_he']} לפי הנתונים שהזנת.")
        elif r["group"].startswith("feature:"):
            feature = FEATURE_LABELS_HE.get(r["group"].split(":", 1)[1], r["label_he"])
            out["for"].append(f"ציינת {feature} כאבזור רצוי — הוא קיים ב־{names[winner]}.")
        else:
            out["for"].append(f"ציינת הנעה כפולה כרצויה — ל־{names[winner]} יש הנעה כפולה.")
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
                              "text_he": f"לא ניתן לאמת את דרישת {c['label_he']} עבור {names[slot]} — הנתון חסר, וזה אינו נחשב לכישלון."})
    return notes
