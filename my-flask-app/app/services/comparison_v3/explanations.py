# -*- coding: utf-8 -*-
"""Deterministic Hebrew texts of V3 (no LLM): the section influence line, the recommendation headline, the reasons
and the requirement notes. Built only from the rows, the validated profile, the hard constraints and the immutable
composition. Missing data is never framed as a disadvantage, and a category without rows has no section at all.

N cars (L2): an advantage in a row or a group of rows is stated only for the row leader, which is strictly better than
EVERY other car after the tie tolerance (``row["leader"]``). When the rows of a reason have different leaders, the
reason names each row's leader ("מדד ירוק: תיקו בין A ל־B"). The pairwise utilities stay internal: with more than two
cars no text is built per pair ("X מול Y"). ``summary_credit_violation`` rejects a summary that credits a car with an
advantage in a row it does not lead.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

from app.services.comparison_v2.buyer_profile import PRIORITY_LABELS_HE
from app.services.comparison_v3.buyer_profile import FEATURE_LABELS_HE, MODE_GENERAL, PRIORITY_NAMES_HE
from app.services.comparison_v3.composer import BASIS_COMPOSITION, BASIS_HARD_CONSTRAINTS, NO_COMMON_DATA, NO_VEHICLE_MEETS_REQUIREMENTS
from app.services.comparison_v3.contracts import CHOICE_INSUFFICIENT, CHOICE_TIE, DECISION_UNAVAILABLE, NO_COMMON_DATA_HE
from app.services.comparison_v3.engine import CATEGORY_NEGLIGIBLE_CONTRIBUTION, FAIL, UNKNOWN
from app.services.comparison_v3.metrics import CATEGORIES, GROUP_LABEL_HE, METRICS, group_label_he

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


TIE_ALL_HE = "תיקו בין כל הרכבים"


def row_leader_text(row: Dict[str, Any], names: Dict[str, str]) -> str:
    """Who leads a scored row over ALL the cars: the leader's name, or the cars tied at the top."""
    leader = row.get("leader")
    if leader in names:
        return names[leader]
    top = [names[s] for s in row.get("top_slots") or [] if s in names]
    if len(top) < 2 or len(top) >= len(row.get("values") or names):
        return TIE_ALL_HE
    return "תיקו בין " + ", ".join(top[:-1]) + " ל־" + top[-1]


def _scored(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [r for r in rows if not r.get("display_only")]


def _fit_values(composition: Dict[str, Any], fit_type: str) -> Dict[str, float]:
    fits: Dict[str, float] = {}
    for p in (composition.get("pairs") or {}).values():
        for d in p["dimensions"].values():
            for sig in d["signals"]:
                if sig.get("group") == fit_type:
                    fits.update({k: v for k, v in (sig.get("fit") or {}).items() if v is not None})
    return fits


def fit_leader(composition: Dict[str, Any], fit_type: str) -> Optional[str]:
    """The car whose contextual fit is strictly the highest of all eligible cars, or None."""
    fits = _fit_values(composition, fit_type)
    eligible = composition.get("eligible_slots") or list(fits)
    if not eligible or any(s not in fits for s in eligible):
        return None
    best = max(fits[s] for s in eligible)
    top = [s for s in eligible if fits[s] == best]
    return top[0] if len(top) == 1 else None


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
    if len(pairs) > 1:
        return _influence_n_cars(dim, rows, pairs, names)
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


def _influence_n_cars(dim, rows, pairs, names):
    """More than two cars: the leader of every scored row over all cars, never a sentence per pair."""
    dims = [p["dimensions"][dim] for p in pairs]
    unavailable = any(d["contribution"] is None and any(s["status"] == "judgment_unavailable" for s in d["signals"])
                      for d in dims)
    lines = ["שיפוט משמעות הפער לא היה זמין, ולכן התחום לא השפיע על ההכרעה."] if unavailable else []
    used = [d["contribution"] for d in dims if d["contribution"] is not None]
    if not used:
        return BALANCED, " ".join(lines) or "הנתונים בתחום מאוזנים בין הרכבים."
    if all(abs(c) < CATEGORY_NEGLIGIBLE_CONTRIBUTION for c in used):
        lines.insert(0, "הנתונים מאוזנים או שהפערים זניחים לצרכים שהגדרת, ולכן התחום כמעט לא השפיע.")
        return BALANCED, " ".join(lines)
    parts = [f"{r['label_he']}: {row_leader_text(r, names)}" for r in _scored(rows)]
    seen = set()
    for d in dims:
        for sig in d["signals"]:
            if sig["source"] != "contextual" or sig["group"] in seen:
                continue
            seen.add(sig["group"])
            fits = {}
            for dd in dims:
                for ss in dd["signals"]:
                    if ss.get("group") == sig["group"]:
                        fits.update({k: v for k, v in (ss.get("fit") or {}).items() if v is not None})
            if fits:
                parts.append(f"{sig['label_he']} (הערכה לפי הנתונים שהזנת): " +
                             ", ".join(f"{names[s]}: {FIT_LABEL_HE[int(round(fits[s] * 4))]}" for s in sorted(fits)))
    text = "התחום השפיע על ההכרעה."
    if parts:
        text += " המוביל בכל נתון מבין כל הרכבים: " + "; ".join(parts) + "."
    return INFLUENCED, " ".join([text] + lines)


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


def _reason_rows(r: Dict[str, Any], by_id: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [by_id[m] for m in r.get("metrics") or [] if m in by_id]


def _objective_for(r, winner, names, by_id, imp, one_pair) -> Optional[str]:
    """``ל־W יתרון ב…`` when W leads a row of the group and is at the top of all of them; else None."""
    group_rows = _reason_rows(r, by_id)
    led = [row for row in group_rows if row.get("leader") == winner]
    if not led or any(winner not in (row.get("top_slots") or []) for row in group_rows):
        return None
    label = r["label_he"] if len(led) == len(group_rows) else group_label_he(r["group"], [x["row_id"] for x in led])
    text = f"{imp}. ל־{names[winner]} יתרון ב{label}"
    if one_pair and r.get("materiality_label_he"):
        text += f", ומשמעות הפער לשימוש שהגדרת הוערכה כ{r['materiality_label_he']}"
    return text + "."


def _category_leaders(category, winner, names, by_id, imp) -> Optional[str]:
    """The rows of a category have different leaders: name each row's leader (only if W is at the top of one)."""
    rows = _scored(r for r in by_id.values() if r["category"] == category)
    if not any(winner in (row.get("top_slots") or []) for row in rows):
        return None
    return f"{imp}. " + "; ".join(f"{row['label_he']}: {row_leader_text(row, names)}" for row in rows) + "."


def reason_texts(reasons, composition, profile, names, rows: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """The recommendation reasons over ALL cars (L2). ``rows`` are the rows that exist (the row rule)."""
    winner = composition.get("recommended_slot")
    out: Dict[str, List[str]] = {"for": [], "against": []}
    if not winner or composition.get("basis") != BASIS_COMPOSITION:
        return out
    weights = composition["weights"]
    by_id = {row["row_id"]: row for row in rows}
    one_pair = len(composition.get("pairs") or {}) == 1
    per_row_categories = set()
    for r in reasons.get("for", []):
        imp = _importance(r["dimension"], profile, weights)
        if r["source"] == "objective":
            text = _objective_for(r, winner, names, by_id, imp, one_pair)
            if text is None and r.get("category") not in per_row_categories:
                per_row_categories.add(r.get("category"))
                text = _category_leaders(r.get("category"), winner, names, by_id, imp)
            if text:
                out["for"].append(text)
        elif r["source"] == "contextual":
            if fit_leader(composition, r["group"]) == winner:
                out["for"].append(f"{imp}. {names[winner]} הותאם טוב יותר מבחינת {r['label_he']} לפי הנתונים שהזנת.")
        elif r["group"].startswith("feature:"):
            feature = FEATURE_LABELS_HE.get(r["group"].split(":", 1)[1], r["label_he"])
            out["for"].append(f"ציינת {feature} כאבזור רצוי, והוא קיים ב־{names[winner]}.")
        else:
            out["for"].append(f"ציינת הנעה כפולה כרצויה, ול־{names[winner]} יש הנעה כפולה.")
    tail = ", אך הוא לא הכריע את התמונה הכוללת לפי העדיפויות שלך."
    for r in reasons.get("against", []):
        if r["source"] == "objective":
            group_rows = _reason_rows(r, by_id)
            by_leader: Dict[str, List[str]] = {}
            for row in group_rows:
                if row.get("leader") in names and row["leader"] != winner:
                    by_leader.setdefault(row["leader"], []).append(row["row_id"])
            for leader, ids in by_leader.items():
                label = r["label_he"] if len(ids) == len(group_rows) else group_label_he(r["group"], ids)
                out["against"].append(f"ל־{names[leader]} יתרון ב{label}{tail}")
        elif r["source"] == "contextual":
            leader = fit_leader(composition, r["group"])
            if leader and leader != winner:
                out["against"].append(f"ל־{names[leader]} יתרון ב{r['label_he']}{tail}")
        else:
            others = [names[s] for s in r.get("against") or [] if s in names]
            if others:
                out["against"].append(f"ל־{join_he(others)} יתרון ב{r['label_he']}{tail}")
    return out


def row_leaders_for_summary(rows: List[Dict[str, Any]], names: Dict[str, str]) -> List[Dict[str, str]]:
    """The leader of every scored row over all cars, for the summary payload (the only advantages it may state)."""
    return [{"row": r["label_he"], "leader": row_leader_text(r, names)} for r in _scored(rows)]


# ---------------------------------------------------------------------------
# summary guard (L2): a summary sentence may credit a car only with a row it leads
# ---------------------------------------------------------------------------
ADVANTAGE_MARKERS = ("יתרון", "עדיף", "טוב יותר", "מוביל", "גבוה יותר", "נמוך יותר", "חזק יותר", "בטוח יותר",
                     "חסכוני יותר", "פחות מזהם", "מזהם פחות", "יותר טוב", "בולט")
_CLAUSE_SPLIT = re.compile(r"[,;:.!?]|\s(?:אך|אבל|ואילו|בעוד|לעומת)\s")


def _car_aliases(cars: Dict[str, Dict[str, Any]]) -> Dict[str, List[str]]:
    aliases: Dict[str, List[str]] = {s: [c.get("display_name") or ""] for s, c in cars.items()}
    for key in ("make", "model"):
        values = [str(c.get(key) or "") for c in cars.values()]
        for slot, c in cars.items():
            v = str(c.get(key) or "")
            if len(v) >= 2 and values.count(v) == 1:
                aliases[slot].append(v)
    return {s: [a for a in v if a] for s, v in aliases.items()}


def _label_index(rows: List[Dict[str, Any]]) -> List[tuple]:
    """(label, rows it names | None for a label with no displayed row), longest label first."""
    present = {r["row_id"]: r for r in rows}
    index: Dict[str, Optional[List[Dict[str, Any]]]] = {}
    for m in METRICS:
        index.setdefault(m.label_he, [present[m.key]] if m.key in present else None)
    for r in rows:
        index[r["label_he"]] = [r]
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        if r.get("group") and not r.get("display_only"):
            groups.setdefault(r["group"], []).append(r)
    for group, label in GROUP_LABEL_HE.items():
        grows = groups.get(group)
        # the whole-group label names its rows only when they exist ("דירוג הבטיחות" without the safety score: hidden)
        whole = bool(grows) and group_label_he(group, [r["row_id"] for r in grows]) == label
        index.setdefault(label, grows if whole else None)
    for group, grows in groups.items():
        index.setdefault(group_label_he(group, [r["row_id"] for r in grows]), grows)
    for cat in CATEGORIES:
        if cat["dimension"]:
            index.setdefault(cat["label_he"], _scored(r for r in rows if r["category"] == cat["key"]) or None)
    return sorted(index.items(), key=lambda kv: -len(kv[0]))


def _credited_ok(slot: str, named_rows: Optional[List[Dict[str, Any]]]) -> bool:
    if not named_rows:
        return False                                    # a hidden row, or a row that decides nothing
    scored = _scored(named_rows)
    if not scored:
        return False
    if len(scored) == 1:
        return scored[0].get("leader") == slot
    return all(slot in (r.get("top_slots") or []) for r in scored) and any(r.get("leader") == slot for r in scored)


def summary_credit_violation(text: str, rows: List[Dict[str, Any]], cars: Dict[str, Dict[str, Any]]) -> Optional[str]:
    """None when the text is acceptable; otherwise why it must fall back to the deterministic summary."""
    aliases = _car_aliases(cars)
    labels = _label_index(rows)
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        named_in_sentence = [s for s, al in aliases.items() if any(a in sentence for a in al)]
        if "מול" in sentence and len(named_in_sentence) >= 2:
            return "pairwise_sentence"
        for clause in _CLAUSE_SPLIT.split(sentence):
            if not clause or not any(m in clause for m in ADVANTAGE_MARKERS):
                continue
            named = [s for s, al in aliases.items() if any(a in clause for a in al)]
            if not named:
                continue
            rest = clause
            mentioned = []
            for label, named_rows in labels:
                if label and label in rest:
                    mentioned.append((label, named_rows))
                    rest = rest.replace(label, " ")
            for label, named_rows in mentioned:
                for slot in named:
                    if not _credited_ok(slot, named_rows):
                        return f"credits_non_leader:{slot}:{label}"
    return None


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
