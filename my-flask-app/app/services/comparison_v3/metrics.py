# -*- coding: utf-8 -*-
"""The V3 metric registry, the categories (``comparison-v3/1``) and the row rule.

Categories, in table order: 0 פרטי הגרסה (unweighted), 1 מחיר, 2 בטיחות, 3 ביצועים, 4 צריכה וסביבה, 5 מידות ומרחב,
היסטוריה ושרידות (unweighted, government history rows), 6 חשמלי (conditional), 7 גרירה (conditional).

The row rule (R) is ONE function, ``comparable_rows``, used by the table, the engine (atomic results, pairwise
evidence), the JEV state and the summary payload: a row exists only when EVERY selected car has a value, under the
same ``standard`` (WLTP with WLTP, NEDC with NEDC; a mass under the same ``definition``) and, for consumption
(L/100km, kWh/100km), the same propulsion family. Otherwise the row is absent everywhere. A category without rows is
absent.

The official type-approval emission figures (CO2 WLTP / NEDC, the green index, the pollution group) are compared across
propulsion families: they are the ministry's own cross-propulsion classification and all of them come from the same
type-approval test. When plug-in / electric cars are compared with combustion cars, each of these rows carries the
same ``note_he`` (a plug-in's WLTP figure is a weighted value that assumes regular charging).

A scored row has ``leader`` (one slot strictly better than EVERY other car after the tie tolerance, else ``tie``) and
``top_slots`` (the leader, or every car within the tolerance of the best value). Texts credit only the leader.

Missing is never zero and never worse; nothing here writes "no data".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.services.comparison_v3.contracts import CHOICE_TIE
from app.services.comparison_v3.labels import GEARBOX_LABELS_HE, SOURCE_NAMES_HE, value_label

HIGHER_BETTER = "higher_better"
LOWER_BETTER = "lower_better"
DISPLAY = "display"

# Category keys in table order. ``dimension`` None == unweighted (display only, never in JEV / the composer).
CATEGORIES: Tuple[Dict[str, Any], ...] = (
    {"key": "variant_details", "label_he": "פרטי הגרסה", "dimension": None},
    {"key": "price", "label_he": "מחיר", "dimension": "purchase_price"},
    {"key": "safety", "label_he": "בטיחות", "dimension": "safety"},
    {"key": "performance", "label_he": "ביצועים", "dimension": "performance"},
    {"key": "efficiency_environment", "label_he": "צריכה וסביבה", "dimension": "efficiency_environment"},
    {"key": "practicality", "label_he": "מידות ומרחב", "dimension": "practicality"},
    {"key": "history", "label_he": "היסטוריה ושרידות", "dimension": None},
    {"key": "ev", "label_he": "חשמלי", "dimension": "ev_convenience"},
    {"key": "towing", "label_he": "גרירה", "dimension": "towing"},
)
CATEGORY_BY_KEY = {c["key"]: c for c in CATEGORIES}
CATEGORY_ORDER = tuple(c["key"] for c in CATEGORIES)
DIMENSION_CATEGORY = {c["dimension"]: c["key"] for c in CATEGORIES if c["dimension"]}

UNIT_LABELS_HE = {
    "hp": "כ״ס", "hp/t": "כ״ס לטון", "cc": "סמ״ק", "kg": "ק״ג", "mm": "מ״מ", "km": "ק״מ", "g/km": "גר׳ לק״מ",
    "L/100km": "ליטר ל־100 ק״מ", "kWh/100km": "קוט״ש ל־100 ק״מ", "ILS": "₪", "count": "",
}

MASS_LABELS_HE = {"eu_running_order": "משקל במצב נסיעה (כולל נהג)", "na_curb": "משקל עצמי"}


@dataclass(frozen=True)
class Metric:
    key: str
    label_he: str
    category: str
    kind: str                                  # higher_better | lower_better | display
    unit: Optional[str] = None
    group: Optional[str] = None                # correlation group: one group == one signal
    tie_abs: float = 0.0
    tie_rel: float = 0.0
    same_standard: bool = False                # every car under the same standard / definition
    same_family: bool = False                  # consumption: one propulsion family
    explain_he: str = ""                       # deterministic explanation: what it measures, source, standard
    reader: Optional[str] = None               # name of a special reader (see READERS)
    history: bool = False                      # government history row: never weighted, never in JEV
    description_en: str = ""                   # what JEV is told the row is (only rows that exist are described)
    mixed_family_note: bool = False            # official emission figure: noted when plug-in meets combustion

    @property
    def scored(self) -> bool:
        return self.kind in (HIGHER_BETTER, LOWER_BETTER)

    @property
    def direction(self) -> int:
        return {HIGHER_BETTER: 1, LOWER_BETTER: -1}.get(self.kind, 0)


def _m(key, label, category, kind, unit=None, **kw) -> Metric:
    return Metric(key=key, label_he=label, category=category, kind=kind, unit=unit, **kw)


METRICS: Tuple[Metric, ...] = (
    # --- 0 variant details (display only) ---
    _m("model_year", "שנת דגם", "variant_details", DISPLAY, reader="model_year",
       explain_he="שנת הדגם של הגרסה כפי שהיא רשומה במאגר הדגמים של משרד התחבורה."),
    _m("propulsion", "סוג הנעה ודלק", "variant_details", DISPLAY, reader="propulsion",
       explain_he="סוג ההנעה (בנזין, דיזל, היברידי, פלאג-אין או חשמלי) כפי שהוא רשום במשרד התחבורה."),
    _m("drivetrain", "הנעה", "variant_details", DISPLAY,
       explain_he="האם הרכב מונע על ציר אחד או בהנעה כפולה, לפי רישום משרד התחבורה."),
    _m("body_style", "מרכב", "variant_details", DISPLAY,
       explain_he="סוג המרכב של הגרסה לפי רישום משרד התחבורה."),
    _m("engine_cc", "נפח מנוע", "variant_details", DISPLAY, "cc", reader="engine_cc",
       explain_he="נפח מנוע הבעירה בסמ״ק כפי שהוא רשום במשרד התחבורה."),
    _m("gearbox", "תיבת הילוכים", "variant_details", DISPLAY, reader="gearbox",
       explain_he="סוג תיבת ההילוכים: לפי משרד התחבורה, ולגרסאות באישור אמריקאי לפי נתוני EPA."),
    # --- 1 price ---
    _m("asking_price_ils", "מחיר מבוקש", "price", LOWER_BETTER, "ILS", group="price", tie_rel=0.02,
       description_en="the asking price entered by the buyer",
       explain_he="המחיר שהזנת עבור הרכב. הוא משמש להשוואת המחיר ולבדיקת התקציב שהגדרת."),
    _m("original_new_price_ils", "מחיר מחירון חדש מקורי", "price", DISPLAY, "ILS", reader="original_price", history=True,
       explain_he="מחיר המחירון של הגרסה כשהייתה חדשה, לפי נתוני משרד התחבורה. מוצג לעיון בלבד."),
    _m("depreciation_from_new", "ירידת ערך מהמחיר החדש", "price", DISPLAY, reader="depreciation", history=True,
       explain_he="ההפרש היחסי בין המחיר שהזנת למחיר המחירון המקורי של הגרסה כשהייתה חדשה. מוצג לעיון בלבד."),
    # --- 2 safety: ONE group for the ministry's rating (its level is built from the equipment) ---
    _m("safety_score", "ניקוד בטיחות (משרד התחבורה)", "safety", HIGHER_BETTER, group="gov_safety_rating", tie_abs=0.5,
       description_en="the official government safety score",
       explain_he="ניקוד הבטיחות שמשרד התחבורה מפרסם לגרסה, המבוסס על מערכות הבטיחות המותקנות בה."),
    _m("safety_equipment_level", "רמת אבזור בטיחותי", "safety", HIGHER_BETTER, group="gov_safety_rating",
       description_en="the government safety-equipment level",
       explain_he="רמת אבזור הבטיחות שמשרד התחבורה קובע לגרסה לפי מערכות הבטיחות שהיצרן דיווח עליהן."),
    _m("adas_systems_count", "מערכות סיוע לנהג", "safety", HIGHER_BETTER, "count", group="gov_safety_rating",
       same_standard=True, reader="adas_count", description_en="the number of driver-assistance systems",
       explain_he="מספר מערכות הסיוע לנהג שקיימות בגרסה, מתוך המערכות שדווחו למשרד התחבורה."),
    _m("airbags", "כריות אוויר", "safety", HIGHER_BETTER, "count", group="passive_safety",
       description_en="the number of airbags",
       explain_he="מספר כריות האוויר בגרסה לפי רישום משרד התחבורה."),
    # --- 3 performance: one group ---
    _m("horsepower", "הספק", "performance", HIGHER_BETTER, "hp", group="power_output", tie_rel=0.05,
       description_en="horsepower",
       explain_he="הספק המנוע בכוחות סוס כפי שהוא רשום במשרד התחבורה."),
    _m("hp_per_tonne", "הספק לטון", "performance", HIGHER_BETTER, "hp/t", group="power_output", tie_rel=0.05,
       same_standard=True, reader="hp_per_tonne", description_en="horsepower per tonne (same mass definition)",
       explain_he="ההספק הרשום במשרד התחבורה מחולק במשקל הרכב (באותה הגדרת משקל לכל הרכבים), בכוח סוס לטון."),
    _m("curb_weight_kg", "משקל", "performance", DISPLAY, "kg", same_standard=True, reader="mass",
       explain_he="משקל הרכב מנתונים פתוחים: במצב נסיעה כולל נהג (EEA) או משקל עצמי (Transport Canada)."),
    # --- 4 consumption and environment: one consumption group, one standard, one propulsion family ---
    _m("fuel_consumption_l_100km", "צריכת דלק משולבת", "efficiency_environment", LOWER_BETTER, "L/100km",
       group="consumption", tie_abs=0.3, same_standard=True, same_family=True,
       description_en="official fuel consumption (same standard and propulsion family)",
       explain_he="צריכת הדלק המשולבת בליטרים ל־100 ק״מ לפי תקן WLTP, מנתוני EEA."),
    _m("energy_consumption_kwh_100km", "צריכת חשמל משולבת", "efficiency_environment", LOWER_BETTER, "kWh/100km",
       group="consumption", tie_abs=0.5, same_standard=True, same_family=True,
       description_en="official electricity consumption (same standard and propulsion family)",
       explain_he="צריכת החשמל המשולבת בקוט״ש ל־100 ק״מ לפי תקן WLTP, מנתוני EEA."),
    _m("co2_wltp", "פליטת CO₂ (WLTP)", "efficiency_environment", LOWER_BETTER, "g/km", group="consumption",
       tie_abs=5, same_standard=True, mixed_family_note=True, description_en="official WLTP CO2 emissions",
       explain_he="פליטת הפחמן הדו-חמצני בגרם לק״מ לפי תקן WLTP, כפי שהיא רשומה במשרד התחבורה."),
    _m("co2_nedc_g_km", "פליטת CO₂ (NEDC)", "efficiency_environment", LOWER_BETTER, "g/km", group="consumption",
       tie_abs=5, same_standard=True, mixed_family_note=True, description_en="official NEDC CO2 emissions",
       explain_he="פליטת הפחמן הדו-חמצני בגרם לק״מ לפי תקן NEDC הישן, מנתוני EEA. אינה ברת השוואה למדידת WLTP."),
    _m("green_index", "מדד ירוק", "efficiency_environment", LOWER_BETTER, group="pollution_class", tie_abs=5,
       mixed_family_note=True, description_en="the government green index",
       explain_he="המדד הירוק של משרד התחבורה, המשקלל את פליטות המזהמים של הגרסה. ערך נמוך יותר מזהם פחות."),
    _m("pollution_group", "קבוצת זיהום", "efficiency_environment", LOWER_BETTER, group="pollution_class",
       mixed_family_note=True, description_en="the government pollution group",
       explain_he="קבוצת הזיהום שמשרד התחבורה קובע לגרסה לפי המדד הירוק. קבוצה נמוכה יותר מזהמת פחות."),
    # --- 5 practicality: wheelbase is the only scored metric ---
    _m("seats", "מושבים", "practicality", DISPLAY, "count",
       explain_he="מספר המושבים לפי רישום משרד התחבורה. משמש לבדיקת מספר הנוסעים שהגדרת."),
    _m("wheelbase_mm", "בסיס גלגלים", "practicality", HIGHER_BETTER, "mm", group="wheelbase", tie_rel=0.02,
       description_en="the wheelbase",
       explain_he="המרחק בין הסרן הקדמי לאחורי במילימטרים, מנתוני EEA או Transport Canada."),
    _m("length_mm", "אורך", "practicality", DISPLAY, "mm",
       explain_he="אורך הרכב במילימטרים, מנתוני Transport Canada (גרסאות באישור אמריקאי)."),
    _m("width_mm", "רוחב", "practicality", DISPLAY, "mm",
       explain_he="רוחב הרכב במילימטרים, מנתוני Transport Canada (גרסאות באישור אמריקאי)."),
    _m("height_mm", "גובה", "practicality", DISPLAY, "mm",
       explain_he="גובה הרכב במילימטרים, מנתוני Transport Canada (גרסאות באישור אמריקאי)."),
    # --- history (government; display only, never weighted) ---
    _m("recalls", "קריאות ריקול לדגם", "history", DISPLAY, reader="recalls", history=True,
       explain_he="מספר קריאות הריקול שפורסמו לדגם, לפי נתוני משרד התחבורה."),
    _m("road_survival", "ירידה מהכביש", "history", DISPLAY, reader="road_survival", history=True,
       explain_he=("שיעור הרכבים מהדגם שירדו מהכביש עד גיל זהה לכל הרכבים בהשוואה, לפי נתוני משרד התחבורה. "
                   "סיבת הירידה מהכביש אינה ידועה.")),
    # --- 6 EV (conditional: every car EV, or every car PHEV) ---
    _m("electric_range_km", "טווח חשמלי", "ev", HIGHER_BETTER, "km", group="electric_range", tie_rel=0.03,
       same_standard=True, description_en="official electric range under the same measurement standard",
       explain_he="הטווח החשמלי בקילומטרים לפי תקן WLTP, מנתוני EEA."),
    # --- 7 towing (conditional on a towing requirement for its weight) ---
    _m("towing_braked_kg", "כושר גרירה עם בלמים", "towing", HIGHER_BETTER, "kg", group="towing", tie_abs=50,
       description_en="official braked towing capacity",
       explain_he="משקל הגרור המרבי עם בלמים שהרכב רשאי לגרור, לפי רישום משרד התחבורה."),
    _m("towing_unbraked_kg", "כושר גרירה ללא בלמים", "towing", HIGHER_BETTER, "kg", group="towing", tie_abs=50,
       description_en="official unbraked towing capacity",
       explain_he="משקל הגרור המרבי ללא בלמים שהרכב רשאי לגרור, לפי רישום משרד התחבורה."),
)
METRICS_BY_KEY = {m.key: m for m in METRICS}

GROUP_DIMENSION = {
    "price": "purchase_price", "gov_safety_rating": "safety", "passive_safety": "safety",
    "power_output": "performance", "consumption": "efficiency_environment",
    "pollution_class": "efficiency_environment", "wheelbase": "practicality", "electric_range": "ev_convenience",
    "towing": "towing",
}
GROUP_LABEL_HE = {
    "price": "מחיר", "gov_safety_rating": "דירוג הבטיחות של משרד התחבורה", "passive_safety": "כריות אוויר",
    "power_output": "הספק", "consumption": "צריכה ופליטת CO₂", "pollution_class": "מדד ירוק וקבוצת זיהום",
    "wheelbase": "בסיס גלגלים", "electric_range": "טווח חשמלי", "towing": "כושר גרירה",
    "parking_fit": "התאמה לחניה", "charging_routine_fit": "התאמה לשגרת הטעינה",
    "awd_preference": "הנעה כפולה (רצוי)",
}
GROUP_DESCRIPTION_EN = {
    "price": "the asking price entered by the buyer",
    "gov_safety_rating": "the official government safety score, safety-equipment level and number of "
                         "driver-assistance systems",
    "passive_safety": "the number of airbags",
    "power_output": "power output (horsepower and, when the mass definition is shared, horsepower per tonne)",
    "consumption": "official consumption / CO2 under the same measurement standard and propulsion family",
    "pollution_class": "the government green index and pollution group",
    "wheelbase": "the wheelbase",
    "electric_range": "official electric range under the same measurement standard",
    "towing": "official braked/unbraked towing capacity",
}
# A group's label / description names the whole group only when the rows it names exist for these cars: each tuple
# needs at least one of its rows. Otherwise the label is built from the rows that exist (a hidden row is never named:
# without the safety score, "the ministry's safety rating" would name a row the table does not show).
GROUP_LABEL_REQUIRES: Dict[str, Tuple[Tuple[str, ...], ...]] = {
    "gov_safety_rating": (("safety_score",),),
    "power_output": (("horsepower",),),
    "consumption": (("fuel_consumption_l_100km", "energy_consumption_kwh_100km"), ("co2_wltp", "co2_nedc_g_km")),
    "pollution_class": (("green_index",), ("pollution_group",)),
    "towing": (("towing_braked_kg",), ("towing_unbraked_kg",)),
}

MIXED_FAMILY_NOTE_HE = {
    "phev": "ברכב פלאג-אין ערך ה-WLTP הוא ממוצע משוקלל של בדיקת האישור, המניח טעינה סדירה מהחשמל; בנסיעה בלי טעינה "
            "הפליטה בפועל גבוהה יותר. המדד הירוק וקבוצת הזיהום מחושבים מאותה בדיקה.",
    "ev": "ברכב חשמלי ערך הפליטה הוא פליטה מהמפלט בלבד, לפי בדיקת האישור; המדד הירוק וקבוצת הזיהום מחושבים מאותה בדיקה.",
}
MIXED_FAMILY_NOTE_EN = ("a plug-in vehicle's type-approval figure is a weighted value that assumes regular charging; an "
                        "electric vehicle's is tailpipe only")


def _join_and_he(parts: List[str]) -> str:
    if len(parts) < 2:
        return "".join(parts)
    last = parts[-1]
    return ", ".join(parts[:-1]) + " " + ("ו" if "א" <= last[0] <= "ת" else "ו־") + last


def _group_named_whole(group: str, metric_keys) -> bool:
    keys = set(metric_keys)
    return all(keys & set(alts) for alts in GROUP_LABEL_REQUIRES.get(group, ()))


def group_label_he(group: str, metric_keys) -> str:
    """The Hebrew label of a correlation group FOR THE ROWS THAT EXIST (``metric_keys``)."""
    keys = list(metric_keys)
    if not keys or _group_named_whole(group, keys):
        return GROUP_LABEL_HE.get(group, group)
    return _join_and_he([METRICS_BY_KEY[k].label_he for k in keys])


def group_description_en(group: str, metric_keys, note: bool = False) -> str:
    """What JEV is told the group is, naming only the rows that exist."""
    keys = list(metric_keys)
    if not keys or _group_named_whole(group, keys):
        text = GROUP_DESCRIPTION_EN[group]
    else:
        text = ", ".join(METRICS_BY_KEY[k].description_en or k for k in keys)
    return text + (f" (note: {MIXED_FAMILY_NOTE_EN})" if note else "")


# ---------------------------------------------------------------------------
# readers: one cell per car, or None (absent)
# ---------------------------------------------------------------------------
def _fact(snap: Dict[str, Any], key: str) -> Optional[Dict[str, Any]]:
    fact = snap["facts"].get(key)
    return fact if fact and fact.get("value") not in (None, "") else None


def _cell(value: Any, fact: Optional[Dict[str, Any]] = None, **extra) -> Dict[str, Any]:
    cell = {"value": value}
    for k in ("standard", "definition", "source", "attribution", "licence", "source_level", "identity_level",
              "resource_id", "dataset_built_at"):
        if fact and fact.get(k) not in (None, ""):
            cell[k] = fact[k]
    cell.update({k: v for k, v in extra.items() if v is not None})
    return cell


def _read_fact(snap, metric):
    fact = _fact(snap, metric.key)
    return _cell(fact["value"], fact) if fact else None


def _read_model_year(snap, metric):
    year = snap["identity"].get("model_year")
    return _cell(year, None, source="government") if year else None


def _read_propulsion(snap, metric):
    fact = _fact(snap, "propulsion")
    if not fact:
        return None
    fuel = _fact(snap, "fuel_type")
    value = fact["value"]
    if value == "conventional" and fuel:
        value = fuel["value"]                               # petrol / diesel
    return _cell(value, fact)


def _read_engine_cc(snap, metric):
    fact = _fact(snap, "engine_cc")
    if not fact or not fact["value"]:
        return None                                         # an electric car has no combustion engine
    return _cell(fact["value"], fact)


def _read_gearbox(snap, metric):
    epa = _fact(snap, "gearbox_type")
    if snap["identity"].get("sug_tkina") == "american" and epa and epa.get("source") == "epa_fueleconomy":
        gears = _fact(snap, "gear_count")
        return _cell(epa["value"], epa, gears=gears["value"] if gears else None)
    auto = _fact(snap, "automatic")
    if not auto:
        return None
    return _cell("automatic" if auto["value"] else "manual", auto)


def _read_adas_count(snap, metric):
    stated = snap.get("equipment") or {}
    if not stated:
        return None
    # the "standard" of a count is the set of flags it counts: counts over different sets are not compared
    return _cell(sum(1 for v in stated.values() if v), None, source="government",
                 standard="stated:" + ",".join(sorted(stated)), of=len(stated))


def _read_mass(snap, metric):
    fact = _fact(snap, "curb_weight_kg")
    if not fact or not fact.get("definition"):
        return None
    return _cell(fact["value"], fact, standard=fact["definition"])


def _read_hp_per_tonne(snap, metric):
    hp, mass = _fact(snap, "horsepower"), _read_mass(snap, metric)
    if not hp or not mass or not mass["value"]:
        return None
    value = round(float(hp["value"]) / (float(mass["value"]) / 1000.0), 1)
    return _cell(value, None, source="derived", standard=mass["standard"],
                 derived_from=["horsepower", "curb_weight_kg"], sources=[hp.get("source"), mass.get("source")])


def _read_original_price(snap, metric):
    fact = snap["facts"].get("original_new_price_ils")
    if not fact:
        return None
    rng = fact.get("range")
    if isinstance(rng, (list, tuple)) and len(rng) == 2 and all(isinstance(x, (int, float)) for x in rng):
        if rng[0] == rng[1]:
            return _cell(rng[0], fact, single=True)
        n_prices = fact.get("n_prices") if fact.get("n_prices") is not None else fact.get("count")
        return _cell([rng[0], rng[1]], fact, n_prices=n_prices, single=False)
    if isinstance(fact.get("value"), (int, float)) and not isinstance(fact.get("value"), bool):
        return _cell(fact["value"], fact, single=True)
    return None


def _read_depreciation(snap, metric):
    original = _read_original_price(snap, metric)
    asking = _fact(snap, "asking_price_ils")
    if not original or not original.get("single") or not asking or not original["value"]:
        return None
    value = 1.0 - float(asking["value"]) / float(original["value"])
    return _cell(round(value, 3), snap["facts"].get("original_new_price_ils"), source="derived")


RECALL_DETAIL_KEYS = ("recall_id", "recall_year", "affected_system", "fault_description", "repair_method",
                      "production_range")


def _read_recalls(snap, metric):
    """TRIPY sends ``recalls`` only for a RESOLVED model (an unresolved one has no field, never ``[]``), so the field
    itself is the resolved-model rule: ``[]`` with ``recall_count`` 0 is a real 0."""
    fact = snap["facts"].get("recalls")
    if not fact or not isinstance(fact.get("value"), list):
        return None
    items = [r for r in fact["value"] if isinstance(r, dict)]
    listed = len(fact["value"])
    count_fact = snap["facts"].get("recall_count") or {}
    count = count_fact.get("value")
    if isinstance(count, bool) or not isinstance(count, (int, type(None))):
        return None
    if count is not None and count != listed:
        return None                                         # the count and the list disagree: no cell
    details = [{k: r[k] for k in RECALL_DETAIL_KEYS if r.get(k) not in (None, "", {})} for r in items]
    return _cell(listed if count is None else count, fact, details=details)


def _survival_shares(snap) -> Optional[Dict[int, float]]:
    fact = snap["facts"].get("road_survival") or snap["facts"].get("road_survival.cancelled_share_by_age")
    if not fact:
        return None
    value = fact.get("value")
    shares = value.get("cancelled_share_by_age") if isinstance(value, dict) and "cancelled_share_by_age" in value else value
    if not isinstance(shares, dict):
        return None
    out = {}
    for age, share in shares.items():
        try:
            age_i, share_f = int(age), float(share)
        except (TypeError, ValueError):
            continue
        if 0.0 <= share_f <= 1.0:
            out[age_i] = share_f
    return out or None


SURVIVAL_COHORT_KEYS = ("cohort_year", "cohort_basis", "reference_month")


def _read_road_survival(snap, metric):
    shares = _survival_shares(snap)
    fact = snap["facts"].get("road_survival") or snap["facts"].get("road_survival.cancelled_share_by_age")
    if not shares:
        return None
    value = fact.get("value") if isinstance(fact.get("value"), dict) else {}
    return _cell(shares, fact, **{k: value.get(k) for k in SURVIVAL_COHORT_KEYS if value.get(k) not in (None, "")})


READERS: Dict[str, Callable[[Dict[str, Any], Metric], Optional[Dict[str, Any]]]] = {
    "model_year": _read_model_year, "propulsion": _read_propulsion, "engine_cc": _read_engine_cc,
    "gearbox": _read_gearbox, "adas_count": _read_adas_count, "mass": _read_mass, "hp_per_tonne": _read_hp_per_tonne,
    "original_price": _read_original_price, "depreciation": _read_depreciation, "recalls": _read_recalls,
    "road_survival": _read_road_survival,
}


def read_cell(snapshot: Dict[str, Any], metric: Metric) -> Optional[Dict[str, Any]]:
    reader = READERS.get(metric.reader or "", _read_fact)
    return reader(snapshot, metric)


# ---------------------------------------------------------------------------
# formatting
# ---------------------------------------------------------------------------
def _num(value: Any) -> str:
    if isinstance(value, bool):
        return "כן" if value else "לא"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{int(value):,}" if value.is_integer() else f"{value:,.1f}"
    return str(value)


def cell_text(metric: Metric, cell: Dict[str, Any], survival_age: Optional[int] = None) -> str:
    value = cell.get("value")
    if metric.key == "road_survival":
        share = (value or {}).get(survival_age)
        return f"{round(share * 100)}% מהרכבים"
    if metric.key == "depreciation_from_new":
        return f"{round(value * 100)}%"
    if metric.key == "original_new_price_ils" and not cell.get("single"):
        low, high = value
        n_prices = cell.get("n_prices")
        text = f"₪{_num(low)}–₪{_num(high)}"
        return text + (f" ({n_prices} מחירים במחירון לשנה זו)" if n_prices else "")
    if metric.key == "gearbox":
        text = GEARBOX_LABELS_HE.get(value, value_label(value))
        return text + (f", {cell['gears']} הילוכים" if cell.get("gears") else "")
    if metric.key == "adas_systems_count":
        return f"{value} מתוך {cell.get('of')}"
    if metric.key == "model_year":
        return str(value)
    if isinstance(value, str):
        return value_label(value)
    if metric.unit == "ILS":
        return f"₪{_num(value)}"
    unit = UNIT_LABELS_HE.get(metric.unit or "", metric.unit or "")
    return f"{_num(value)} {unit}".strip()


# ---------------------------------------------------------------------------
# the row rule (R)
# ---------------------------------------------------------------------------
def _threshold(metric: Metric, a: float, b: float) -> float:
    return max(metric.tie_abs, metric.tie_rel * max(abs(float(a)), abs(float(b))))


def _decide(metric: Metric, values: Dict[str, float]) -> Tuple[str, float]:
    """The leader is strictly better than EVERY other car after the tie tolerance (it beats the second best)."""
    ordered = sorted(values.items(), key=lambda kv: kv[1] * metric.direction, reverse=True)
    (best_slot, best), (_, second) = ordered[0], ordered[1]
    margin = abs(float(best) - float(second))
    return (CHOICE_TIE, margin) if margin <= _threshold(metric, best, second) else (best_slot, margin)


def top_slots(metric: Metric, values: Dict[str, Any]) -> List[str]:
    """The cars at the top of a scored row: the leader alone, or every car within the tie tolerance of the best."""
    if not metric.scored or len(values) < 2:
        return []
    nums = {s: float(v) for s, v in values.items()}
    best = max(nums.values(), key=lambda v: v * metric.direction)
    return sorted(s for s, v in nums.items() if abs(v - best) <= _threshold(metric, best, v))


def leader_of(metric: Metric, values: Dict[str, Any]) -> Optional[str]:
    """The leading slot, ``tie``, or None for a display row."""
    if not metric.scored or len(values) < 2:
        return None
    return _decide(metric, {s: float(v) for s, v in values.items()})[0]


def _common_survival_age(cells: Dict[str, Dict[str, Any]]) -> Optional[int]:
    """The largest age every compared cohort has reached (>= 3), so the cars are compared at the same age."""
    common = None
    for cell in cells.values():
        ages = set(cell["value"])
        common = ages if common is None else common & ages
    eligible = sorted(a for a in (common or ()) if a >= 3)
    return eligible[-1] if eligible else None


def conditional_ok(category: str, snapshots: Dict[str, Dict[str, Any]]) -> bool:
    if category == "ev":
        families = {s["derived"]["powertrain_family"] for s in snapshots.values()}
        return families in ({"ev"}, {"phev"})
    return True


def comparable_rows(snapshots: Dict[str, Dict[str, Any]], metrics: Tuple[Metric, ...] = METRICS) -> List[Dict[str, Any]]:
    """Every row that exists for these cars (the row rule R), in table order."""
    slots = sorted(snapshots)
    rows: List[Dict[str, Any]] = []
    for metric in metrics:
        if not conditional_ok(metric.category, snapshots):
            continue
        cells = {slot: read_cell(snapshots[slot], metric) for slot in slots}
        if any(c is None for c in cells.values()):
            continue                                        # a value is missing for some car: no row
        if metric.same_standard and len({c.get("standard") for c in cells.values()}) != 1:
            continue                                        # WLTP vs NEDC, eu_running_order vs na_curb, ...
        if metric.same_family:
            families = {snapshots[s]["derived"]["powertrain_family"] for s in slots}
            if len(families) != 1 or "unknown" in families:
                continue                                    # PHEV vs petrol consumption: not compared
        label, survival_age = metric.label_he, None
        if metric.key == "road_survival":
            survival_age = _common_survival_age(cells)
            if survival_age is None:
                continue
            label = f"ירידה מהכביש עד גיל {survival_age}"
        if metric.key == "curb_weight_kg":
            label = MASS_LABELS_HE.get(next(iter(cells.values()))["standard"], metric.label_he)
        if metric.key == "adas_systems_count":
            label = f"{metric.label_he} (מתוך {next(iter(cells.values())).get('of')} שדווחו)"
        values = {s: c["value"] for s, c in cells.items()}
        leader = leader_of(metric, values) if metric.scored else None
        families = {snapshots[s]["derived"]["powertrain_family"] for s in slots}
        note = None
        if metric.mixed_family_note and "combustion" in families and families & {"phev", "ev"}:
            note = " ".join(MIXED_FAMILY_NOTE_HE[f] for f in ("phev", "ev") if f in families)
        standard = next(iter(cells.values())).get("standard")
        if metric.key == "adas_systems_count":
            standard = None                                 # internal: the stated set
        row = {
            "row_id": metric.key,
            "category": metric.category,
            "label_he": label,
            "unit": metric.unit,
            "unit_he": UNIT_LABELS_HE.get(metric.unit or "", ""),
            "kind": metric.kind,
            "direction": metric.direction,
            "group": metric.group,
            "standard": standard,
            "display_only": not metric.scored,
            "history": metric.history,
            "values": values if metric.key != "road_survival" else {s: c["value"][survival_age] for s, c in cells.items()},
            "cells": {s: {**{k: v for k, v in c.items() if k not in ("value",)},
                          "text": cell_text(metric, c, survival_age)} for s, c in cells.items()},
            "leader": leader,
            "top_slots": top_slots(metric, values) if metric.scored else [],
            "sources": sorted({src for c in cells.values() for src in ([c.get("source")] + list(c.get("sources") or []))
                               if src}),
        }
        if survival_age is not None:
            row["survival_age"] = survival_age
        if note:
            row["note_he"] = note
        rows.append(row)
    return rows


def rows_by_category(rows: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        out.setdefault(row["category"], []).append(row)
    return out


def available_dimensions(rows: List[Dict[str, Any]]) -> List[str]:
    """Weighted dimensions that have at least one scored row (a slider without rows is hidden)."""
    return sorted({GROUP_DIMENSION[r["group"]] for r in rows if not r["display_only"] and r.get("group")})


def attribution(rows: List[Dict[str, Any]]) -> List[str]:
    """The footer: only the sources actually used, from the facts' attribution / licence."""
    names: Dict[str, str] = {}
    for row in rows:
        for cell in row["cells"].values():
            for src in [cell.get("source")] + list(cell.get("sources") or []):
                name = SOURCE_NAMES_HE.get(src or "")
                if not name:
                    continue
                licence = cell.get("licence") if cell.get("source") == src else None
                if src == "eea_co2_cars":
                    name = "EEA (CC BY 4.0)"
                elif licence and src != "government" and not src.startswith("gov_") and "OGL" not in str(licence) \
                        and "public" not in str(licence):
                    name = f"{name} ({licence})"
                names.setdefault(src, name)
    order = ["government", "gov_new_car_prices", "gov_recall_notices", "gov_road_survival", "eea_co2_cars", "tc_cvs",
             "epa_fueleconomy", "nrcan_fuel_ratings", "ademe_car_labelling"]
    out: List[str] = []
    for src in order:
        if src in names and names[src] not in out:          # the three data.gov.il datasets share one label
            out.append(names[src])
    return out
