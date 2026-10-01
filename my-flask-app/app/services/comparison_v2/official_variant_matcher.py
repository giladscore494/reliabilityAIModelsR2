# -*- coding: utf-8 -*-
"""Deterministic check that an official claim describes THIS exact variant.

The model never decides whether a claim belongs to the variant; it only
reports the identity evidence it saw on the page. This matcher compares that
evidence with Level 1.5 and returns, per identity element, True (stated and
consistent), False (explicitly contradicted) or None (not stated).

Absence is not contradiction. A missing trim or a missing model year never
rejects a claim by itself; which elements must be POSITIVELY established
depends on the field's identity scope (``field_registry.IDENTITY_SCOPES``):

* ``exact_variant``   — the exact official model code (with the model name),
  or model + Israeli trim + propulsion + drivetrain + engine/motor;
* ``powertrain``      — model + propulsion + drivetrain + engine/motor;
* ``powertrain_body`` — powertrain + an explicitly stated, compatible body;
* ``model_generation``— same model family plus a generation anchor
  (generation code, the exact powertrain, or the stated vehicle model year).

Every scope rejects an explicit contradiction of the model, body,
generation, stated vehicle model year, propulsion, drivetrain, engine/motor
or model code. A different trim is a contradiction unless the exact official
model code matches. A different seating layout is a contradiction for every
scope except ``powertrain``.

Year semantics: ``vehicle_model_year`` is the model year the official source
explicitly attributes to the specification. ``source_publication_year`` (the
publication / revision date of a page or PDF) is provenance only and never
takes part in matching; neither does any other year that merely appears in
the evidence text (URL, file name, copyright, news date). The legacy
``source_year`` key had both meanings, so it is treated as publication year.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

MATCH_STRONG = "strong"
MATCH_AMBIGUOUS = "VARIANT_SCOPE_AMBIGUOUS"
MATCH_MISMATCH = "VARIANT_MISMATCH"

SCOPE_EXACT_VARIANT = "exact_variant"
SCOPE_POWERTRAIN = "powertrain"
SCOPE_POWERTRAIN_BODY = "powertrain_body"
SCOPE_MODEL_GENERATION = "model_generation"

_AWD_TOKENS = ("awd", "4wd", "4x4", "quattro", "4matic", "xdrive", "allwheel", "dualmotor", "htrac", "efour", "4motion", "twinmotor", "trimotor", "הנעהכפולה", "הנעהלכלהגלגלים", "4x4")
_2WD_TOKENS = ("2wd", "fwd", "rwd", "frontwheel", "rearwheel", "singlemotor", "הנעהקדמית", "הנעהאחורית")
_EV_TOKENS = ("electric", "bev", "ev", "חשמלי", "edrive")
_PHEV_TOKENS = ("pluginhybrid", "phev", "plugin", "פלאגאין")
_HYBRID_TOKENS = ("hybrid", "hev", "היברידי", "mildhybrid", "mhev", "48v", "isg", "eqboost", "startergenerator")
_PETROL_TOKENS = ("petrol", "gasoline", "tfsi", "tsi", "tgdi", "gdi", "בנזין")

# Explicit body variants. A token contradicts the Level 1.5 vehicle unless the
# government model name carries it or the government body style is one of
# the compatible styles. Only unambiguous variant names are listed; a generic
# word such as "coupe" is not (BMW calls the i4 a "Gran Coupe").
_BODY_VARIANTS: Tuple[Tuple[Tuple[str, ...], Tuple[str, ...]], ...] = (
    (("cabriolet", "cabrio", "convertible", "roadster", "spyder", "spider"), ("convertible", "cabriolet", "roadster")),
    (("avant", "touring", "estate", "wagon", "shooting brake", "sports tourer", "sportswagon", "kombi"), ("wagon", "estate")),
    (("sportback",), ()),
    (("long wheelbase", "lwb"), ()),
)
# Positive body statements per Level 1.5 body style.
_BODY_POSITIVE = {
    "suv": ("suv", "sport utility", "sports utility", "sports activity vehicle", "crossover"),
    "coupe": ("coupe",),
    "hatchback": ("hatchback", "gran coupe", "liftback", "fastback", "5 door"),
    "sedan": ("sedan", "saloon", "limousine"),
    "mpv": ("mpv", "minivan", "people carrier"),
}


def _plain(text: Any) -> str:
    lowered = str(text or "").lower()
    for accented, plain in (("é", "e"), ("è", "e"), ("ü", "u"), ("ö", "o"), ("ä", "a")):
        lowered = lowered.replace(accented, plain)
    return lowered


def _phrase_in(text: str, phrase: str) -> bool:
    """Whole-word phrase match (``avant`` never matches ``avantgarde``)."""
    words = [re.escape(w) for w in phrase.split()]
    pattern = r"(?<![0-9a-z])" + r"[\s\-]*".join(words) + r"(?![0-9a-z])"
    return re.search(pattern, text) is not None


# Generation (chassis) codes the matcher knows per brand + model token, with
# the government model years they cover and the body they belong to. Only
# codes that are certain are listed; an unknown code is simply "not stated".
_GENERATIONS: Dict[Tuple[str, str], Tuple[Dict[str, Any], ...]] = {
    ("ב מ וו", "i4"): ({"code": "G26", "from": 2021, "to": None, "body": None},),
    ("מרצדס", "cle"): (
        {"code": "C236", "from": 2023, "to": None, "body": "coupe"},
        {"code": "A236", "from": 2024, "to": None, "body": "convertible"},
    ),
}

_MY_PATTERNS = (
    re.compile(r"\bMY\s?'?(20\d{2})(?!\d)", re.IGNORECASE),
    re.compile(r"\bMY'?(\d{2})(?!\d)", re.IGNORECASE),
    re.compile(r"model[\s_-]*year\s*[:=]?\s*(20\d{2})(?!\d)", re.IGNORECASE),
    re.compile(r"modelljahr\s*[:=]?\s*(20\d{2})(?!\d)", re.IGNORECASE),
    re.compile(r"ann[ée]e[\s-]*mod[èe]le\s*[:=]?\s*(20\d{2})(?!\d)", re.IGNORECASE),
    re.compile(r"(?:שנת\s*דגם|שנתון)\s*[:=]?\s*(20\d{2})(?!\d)"),
)


def _compact(text: Any) -> str:
    return re.sub(r"[^0-9a-z֐-׿.]+", "", str(text or "").lower())


def _tokens(text: Any) -> List[str]:
    return [t for t in re.split(r"[^0-9a-z֐-׿]+", str(text or "").lower()) if t]


def normalize_model_code(code: Any) -> str:
    return re.sub(r"[^0-9A-Z]", "", str(code or "").upper())


def _int_year(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 1980 <= value <= 2100 else None


def _has_any(compact: str, needles, words: Optional[List[str]] = None) -> bool:
    """Long needles match inside the compacted text; short ones (<=4 chars,
    e.g. ``ev``/``hev``/``tsi``) only as whole words to avoid false hits."""
    for needle in needles:
        if len(needle) <= 4 and words is not None:
            if needle in words:
                return True
        elif needle in compact:
            return True
    return False


# ---------------------------------------------------------------------------
# years
# ---------------------------------------------------------------------------
def explicit_model_years(evidence_text: str) -> List[int]:
    """Model years the evidence states EXPLICITLY as a model year ("MY2024",
    "model year 2024", "Modelljahr 2024", "שנת דגם 2024"). A bare year in a
    title, URL, file name or date is never a model year."""
    years: List[int] = []
    for pattern in _MY_PATTERNS:
        for raw in pattern.findall(evidence_text or ""):
            year = int(raw) if len(raw) == 4 else 2000 + int(raw)
            if 2000 <= year <= 2099 and year not in years:
                years.append(year)
    return years


def claim_years(claim: Dict[str, Any], evidence_text: str = "") -> Dict[str, Any]:
    """Year metadata of one claim; only ``stated_model_years`` may be matched."""
    vehicle_model_year = _int_year(claim.get("vehicle_model_year"))
    publication = _int_year(claim.get("source_publication_year"))
    if publication is None:
        publication = _int_year(claim.get("source_year"))  # legacy: ambiguous -> provenance only
    stated = ([vehicle_model_year] if vehicle_model_year else []) + [
        y for y in explicit_model_years(evidence_text) if y != vehicle_model_year
    ]
    return {"vehicle_model_year": vehicle_model_year, "source_publication_year": publication, "stated_model_years": stated}


def _check_year(model_year: Any, years: Dict[str, Any]) -> Optional[bool]:
    """None = no explicit model year (never a mismatch); False only when the
    source explicitly attributes the value to a different model year."""
    stated = years["stated_model_years"]
    if not stated or not isinstance(model_year, int):
        return None
    if years["vehicle_model_year"] is not None and years["vehicle_model_year"] != model_year:
        return False
    return model_year in stated


# ---------------------------------------------------------------------------
# identity elements
# ---------------------------------------------------------------------------
def _check_model(snapshot_model: str, evidence_model: str, evidence_text: str) -> Optional[bool]:
    """True when every government model token appears in the evidence; False
    only when the evidence STATES a model that lacks them; None when the
    evidence does not state the model at all (absence is not contradiction)."""
    want = _tokens(snapshot_model)
    if not want:
        return None
    if all(tok in _compact(evidence_model + " " + evidence_text) for tok in want):
        return True
    return False if _compact(evidence_model) else None


# Ministry model names often embed powertrain designations ("I4 EDRIVE35",
# "CLE300 4MATIC"); the body generation is named by the family alone.
_DESIGNATION = re.compile(
    r"^(?:[a-z]?drive\d*|\d{2,3}[a-z]{0,5}|4matic|quattro|xdrive|awd|4wd|2wd|fwd|rwd|hybrid|phev|hev|mhev|ev|plugin|tfsi|tsi|tdi|tgdi|gdi)$"
)


def model_family_tokens(snapshot_model: Any) -> List[str]:
    """``CLE300 4MATIC`` -> [cle]; ``I4 EDRIVE35`` -> [i4]; ``Q3`` -> [q3];
    ``ESCALADE IQ`` -> [escalade, iq]."""
    out: List[str] = []
    for i, token in enumerate(_tokens(snapshot_model)):
        if i == 0:
            token = re.sub(r"(?<=[a-z])\d{3}$", "", token)
        elif _DESIGNATION.match(token):
            continue
        if token:
            out.append(token)
    return out


def _check_model_family(snapshot_model: str, evidence_model: str, evidence_text: str) -> Optional[bool]:
    """Whole-token match (``cle`` matches ``CLE`` / ``CLE300``, never ``vehicle``)."""
    want = model_family_tokens(snapshot_model)
    if not want:
        return None
    have = _tokens(evidence_model + " " + evidence_text)
    if all(any(t == w or (t.startswith(w) and t[len(w):].isdigit()) for t in have) for w in want):
        return True
    return False if _compact(evidence_model) else None


_DESIGNATION_WORDS = ("rs", "amg", "gt", "gts", "gti", "n", "m", "s", "sq", "svr", "type")


def extra_model_designations(identity: Dict[str, Any], evidence_model: str) -> List[str]:
    """Variant designations in the evidence model name that the government
    model name does not carry ("CLE 53", "i4 M50", "RS Q3", "Q3 45 TFSI").

    They do not reject a dimension by themselves (the Ministry name for an
    Audi is just "Q3"), but they mean the source names a specific variant, so
    a model_generation match then needs the exact powertrain, not only a
    generation code or model year."""
    gov = _compact(identity.get("model"))
    family = model_family_tokens(identity.get("model"))
    make = _compact(identity.get("make_display"))
    out = []
    for token in _tokens(evidence_model):
        if token in family or token in gov or (make and token in make) or re.fullmatch(r"20\d{2}", token):
            continue
        if any(ch.isdigit() for ch in token) or token in _DESIGNATION_WORDS:
            out.append(token)
    return out


def _check_trim(snapshot_trim: str, evidence_trim: str) -> Optional[bool]:
    want = _tokens(snapshot_trim)
    if not want or not _compact(evidence_trim):
        return None
    have = _compact(evidence_trim)
    return all(tok in have for tok in want)


def _check_propulsion(propulsion: str, compact: str, words: List[str]) -> Optional[bool]:
    if not compact:
        return None
    says_phev = _has_any(compact, _PHEV_TOKENS, words)
    says_hybrid = _has_any(compact, _HYBRID_TOKENS, words) and not says_phev
    says_ev = _has_any(compact, _EV_TOKENS, words) and not says_hybrid and not says_phev
    says_petrol = _has_any(compact, _PETROL_TOKENS, words)
    if propulsion == "battery_electric":
        if says_hybrid or says_phev or says_petrol:
            return False
        return True if says_ev else None
    if propulsion == "plug_in_hybrid":
        if says_ev and not says_phev:
            return False
        return True if says_phev else None
    if propulsion == "hybrid":
        if says_phev or says_ev:
            return False
        return True if says_hybrid else None
    if propulsion == "conventional":
        if says_hybrid or says_phev or says_ev:
            return False
        return True if says_petrol else None
    return None


def _check_drivetrain(drivetrain: str, compact: str, words: List[str]) -> Optional[bool]:
    says_awd = _has_any(compact, _AWD_TOKENS, words)
    says_2wd = _has_any(compact, _2WD_TOKENS, words)
    if says_awd and says_2wd:
        return None
    if drivetrain == "awd":
        if says_2wd:
            return False
        return True if says_awd else None
    if drivetrain == "two_wheel_drive":
        if says_awd:
            return False
        return True if says_2wd else None
    return None


def _power_values(text: str) -> Dict[str, List[float]]:
    """Engine / motor outputs. Charging powers ("11 kW AC", "DC 180 kW",
    "charging up to 205 kW") are not outputs and are ignored."""
    lowered = (text or "").lower()
    hp = [float(v) for v in re.findall(r"(\d{2,4})\s*(?:hp|bhp|ps|cv|כ\"ס|כ״ס|כוח סוס)", lowered)]
    kw = []
    for match in re.finditer(r"(\d{1,4}(?:\.\d)?)\s*kw(?!h)", lowered):
        adjacent = lowered[max(0, match.start() - 8):match.start()] + " " + lowered[match.end():match.end() + 5]
        before = lowered[max(0, match.start() - 24):match.start()]
        if re.search(r"\b(?:ac|dc)\b", adjacent) or re.search(r"charg|טעינ|wallbox", before):
            continue
        kw.append(float(match.group(1)))
    return {"hp": hp, "kw": kw}


def _check_engine(facts: Dict[str, Any], evidence_text: str, compact: str) -> Optional[bool]:
    """Engine displacement and/or output for combustion; motor configuration
    or output for EVs. A stated displacement that differs, or a stated output
    that matches none of the government output (±3 %), is a contradiction.
    With no displacement stated, a matching output alone establishes the
    engine configuration (official technical data often omits the litres)."""
    propulsion = facts.get("propulsion")
    hp = facts.get("horsepower")
    powers = _power_values(evidence_text)
    power_match = None
    if hp:
        candidates = powers["hp"] + [k * 1.341022 for k in powers["kw"]]
        if candidates:
            power_match = any(abs(c - hp) / hp <= 0.03 for c in candidates)
    if propulsion == "battery_electric":
        if power_match is not None:
            return power_match
        says_multi = _has_any(compact, ("dualmotor", "twinmotor", "trimotor", "quadmotor"))
        says_single = "singlemotor" in compact
        drivetrain = facts.get("drivetrain")
        if says_multi or says_single:
            return (says_multi and drivetrain == "awd") or (says_single and drivetrain == "two_wheel_drive")
        return None
    cc = facts.get("engine_cc")
    if not cc:
        return power_match
    lowered = (evidence_text or "").lower()
    # A decimal is a displacement only next to an engine word ("2.0 TFSI",
    # "2.0L", "1.6 T-GDi", "2.0 petrol") — never "7.4 s", "8.6 l/100 km"
    # or "16.1 kWh".
    liters = [float(v) for v in re.findall(
        r"(?<![\d.])([0-8]\.\d)\s*-?\s*(?:l\b(?!\s*/)|litre(?!s?\s*/)|liter(?!s?\s*/)|ליטר|t\b|t-?gdi|tfsi|tsi|tdi|gdi|turbo|hybrid|petrol|gasoline|benzin|diesel|engine|בנזין)",
        lowered)]
    ccs = [float(v) for v in re.findall(r"(\d{3,4})\s*(?:cc|סמ\"ק|סמ״ק|cm3|cm³|ccm)", lowered)]
    displacement_match = None
    if liters or ccs:
        want_l = round(cc / 1000.0, 1)
        displacement_match = any(abs(v - want_l) < 0.05 for v in liters) or any(abs(v - cc) <= 60 for v in ccs)
    if displacement_match is False:
        return False
    if displacement_match is True:
        return power_match is not False
    return power_match


def _check_body(identity: Dict[str, Any], facts: Dict[str, Any], body_text: str, evidence_text: str) -> Optional[bool]:
    """False when the evidence names a different body variant (cabriolet,
    wagon, Sportback, long wheelbase ...) than the Level 1.5 vehicle; True
    when it names a compatible body; None when it says nothing about it."""
    gov_model = _plain(identity.get("model"))
    gov_body = (facts.get("body_style") or "").lower()
    text = _plain(evidence_text)
    for needles, compatible in _BODY_VARIANTS:
        stated = any(_phrase_in(text, n) for n in needles)
        if stated and not any(_phrase_in(gov_model, n) for n in needles) and gov_body not in compatible:
            return False
    body = _plain(body_text)
    if not body.strip():
        return None
    if any(_phrase_in(body, p) for p in _BODY_POSITIVE.get(gov_body, ())):
        return True
    for needles, _ in _BODY_VARIANTS:
        if any(_phrase_in(body, n) for n in needles) and any(_phrase_in(gov_model, n) for n in needles):
            return True
    return None


def _generation_entries(identity: Dict[str, Any]) -> Tuple[Dict[str, Any], ...]:
    manufacturer = (identity.get("manufacturer") or "").strip()
    gov_model = _compact(identity.get("model"))
    for (brand, token), entries in _GENERATIONS.items():
        if brand == manufacturer and token in gov_model:
            return entries
    return ()


def _check_generation(identity: Dict[str, Any], facts: Dict[str, Any], codes: List[str]) -> Optional[bool]:
    entries = _generation_entries(identity)
    known = [e for e in entries if e["code"] in codes]
    if not known:
        return None
    year = identity.get("model_year")
    body = (facts.get("body_style") or "").lower()
    for entry in known:
        year_ok = not isinstance(year, int) or ((entry["from"] is None or year >= entry["from"]) and (entry["to"] is None or year <= entry["to"]))
        body_ok = entry["body"] is None or entry["body"] == body or (entry["body"] == "convertible" and body in ("convertible", "cabriolet"))
        if year_ok and body_ok:
            return True
    return False


def _check_seats(seats: Any, evidence_text: str) -> Optional[bool]:
    if not isinstance(seats, int) or isinstance(seats, bool):
        return None
    lowered = (evidence_text or "").lower()
    stated = {int(v) for v in re.findall(r"(?<!\d)([2-9])\s*[- ]?(?:seats?|seater|sitzer|sitze|places|מושבים)", lowered)}
    if not stated:
        return None
    return seats in stated


# ---------------------------------------------------------------------------
# decision
# ---------------------------------------------------------------------------
_HARD_CHECKS = ("model", "body", "generation", "year", "propulsion", "drivetrain", "engine")
_POWERTRAIN_CHECKS = ("model", "propulsion", "drivetrain", "engine")


def evaluate_identity(snapshot: Dict[str, Any], claim: Dict[str, Any]) -> Dict[str, Any]:
    """Per-element identity checks of one claim (True / False / None)."""
    identity = snapshot["identity"]
    facts = snapshot["government"]["facts"]
    evidence = claim.get("identity_evidence") if isinstance(claim.get("identity_evidence"), dict) else {}
    evidence = {k: str(v) for k, v in evidence.items() if isinstance(v, (str, int, float)) and not isinstance(v, bool) and str(v).strip()}
    evidence_text = " ".join(evidence.values())
    compact = _compact(evidence_text)
    words = _tokens(evidence_text)

    # A chassis/generation code reported as "model_code" (e.g. G26) is
    # generation evidence, not a sales model code.
    gen_codes_known = {e["code"] for e in _generation_entries(identity)}
    code_evidence = normalize_model_code(evidence.get("model_code"))
    gen_codes = [normalize_model_code(t) for t in re.split(r"[^0-9A-Za-z]+", evidence_text) if normalize_model_code(t) in gen_codes_known]
    if code_evidence in gen_codes_known:
        code_evidence = ""
    code_known = normalize_model_code(identity.get("official_model_code"))
    model_code = None
    if code_evidence:
        model_code = bool(code_known) and code_evidence == code_known

    years = claim_years(claim, evidence_text)
    designations = extra_model_designations(identity, evidence.get("model", ""))
    checks = {
        "model_code": model_code,
        "model": _check_model(identity.get("model"), evidence.get("model", ""), evidence_text),
        "model_family": _check_model_family(identity.get("model"), evidence.get("model", ""), evidence_text),
        "body": _check_body(identity, facts, " ".join(evidence.get(k, "") for k in ("body", "model", "seating")),
                            " ".join(evidence.get(k, "") for k in ("model", "body", "generation"))),
        "generation": _check_generation(identity, facts, gen_codes),
        "year": _check_year(identity.get("model_year"), years),
        "trim": _check_trim(identity.get("trim"), evidence.get("trim", "")),
        "propulsion": _check_propulsion(facts.get("propulsion"), compact, words),
        "drivetrain": _check_drivetrain(facts.get("drivetrain"), compact, words),
        "engine": _check_engine(facts, evidence_text, compact),
        "seats": _check_seats(facts.get("seats"), evidence_text),
    }
    return {
        "checks": checks,
        "designations": designations,
        "years": {"government_model_year": identity.get("model_year"),
                  "claimed_vehicle_model_year": years["vehicle_model_year"],
                  "source_publication_year": years["source_publication_year"],
                  "stated_model_years": years["stated_model_years"]},
    }


def _result(status: str, matched_by: Optional[str], checks: Dict[str, Any], reasons: List[str], scope: str, years: Dict[str, Any]) -> Dict[str, Any]:
    return {"status": status, "matched_by": matched_by, "checks": checks, "reasons": reasons, "scope": scope, "years": years}


def match_variant(snapshot: Dict[str, Any], claim: Dict[str, Any], scope: str = SCOPE_EXACT_VARIANT) -> Dict[str, Any]:
    """Return {status, matched_by, checks, reasons, scope, years}.

    ``matched_by``: model_code | attributes | powertrain | powertrain_body |
    model_generation (None unless strong).
    """
    evaluated = evaluate_identity(snapshot, claim)
    checks, years = evaluated["checks"], evaluated["years"]
    designations = evaluated["designations"]

    if checks["model_code"] is False:
        return _result(MATCH_MISMATCH, None, checks, ["VARIANT_MODEL_CODE_MISMATCH"], scope, years)

    # Dimensions belong to the body generation: the model FAMILY must match;
    # a different powertrain designation in the model name is judged by the
    # propulsion / drivetrain / engine checks, not by the name.
    model_key = "model_family" if scope == SCOPE_MODEL_GENERATION else "model"
    hard = [k for k in _HARD_CHECKS if checks[model_key if k == "model" else k] is False]
    if checks["seats"] is False and scope != SCOPE_POWERTRAIN:
        hard.append("seats")
    if checks["model_code"] is True:
        # The exact official model code outranks marketing trim wording, but
        # never an explicit configuration contradiction on the same page.
        if hard:
            return _result(MATCH_MISMATCH, None, checks, [f"VARIANT_{k.upper()}_MISMATCH" for k in hard], scope, years)
        if checks["model"] is True:
            return _result(MATCH_STRONG, "model_code", checks, [], scope, years)
    if checks["trim"] is False:
        hard.append("trim")
    if hard:
        return _result(MATCH_MISMATCH, None, checks, [f"VARIANT_{k.upper()}_MISMATCH" for k in hard], scope, years)

    powertrain_ok = all(checks[k] is True for k in _POWERTRAIN_CHECKS)
    if powertrain_ok and checks["trim"] is True:
        return _result(MATCH_STRONG, "attributes", checks, [], scope, years)
    if scope == SCOPE_POWERTRAIN and powertrain_ok:
        return _result(MATCH_STRONG, "powertrain", checks, [], scope, years)
    if scope == SCOPE_POWERTRAIN_BODY and powertrain_ok and checks["body"] is True:
        return _result(MATCH_STRONG, "powertrain_body", checks, [], scope, years)
    if scope == SCOPE_MODEL_GENERATION and checks["model_family"] is True and (
        powertrain_ok or (not designations and (checks["generation"] is True or checks["year"] is True))
    ):
        return _result(MATCH_STRONG, "model_generation", checks, [], scope, years)

    required = list(_POWERTRAIN_CHECKS)
    if scope == SCOPE_EXACT_VARIANT:
        required.insert(1, "trim")
    elif scope == SCOPE_POWERTRAIN_BODY:
        required.append("body")
    elif scope == SCOPE_MODEL_GENERATION:
        if checks["model_family"] is not True:
            required = ["model_family"]
        else:
            required = ["propulsion", "drivetrain", "engine"] if designations else ["generation"]
    missing = [k for k in required if checks[k] is not True]
    return _result(MATCH_AMBIGUOUS, None, checks, [f"VARIANT_{k.upper()}_NOT_STATED" for k in missing], scope, years)


class OfficialVariantMatcher:
    """Object wrapper so the matcher can be injected/replaced (MILO later)."""

    def match(self, snapshot: Dict[str, Any], claim: Dict[str, Any], scope: str = SCOPE_EXACT_VARIANT) -> Dict[str, Any]:
        return match_variant(snapshot, claim, scope)
