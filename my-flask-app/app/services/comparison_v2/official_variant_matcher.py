# -*- coding: utf-8 -*-
"""Deterministic check that an official claim describes THIS exact variant.

The model never decides whether a claim belongs to the variant; it only
reports the identity evidence it saw on the page. This matcher compares that
evidence with Level 1.5:

* STRONG via exact official model code, or
* STRONG via model + year (when stated) + trim + propulsion + drivetrain +
  engine displacement / motor configuration, all present and consistent.

Anything partial is ``VARIANT_SCOPE_AMBIGUOUS``; any explicit contradiction is
a mismatch. Only STRONG claims may enter the variant snapshot.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

MATCH_STRONG = "strong"
MATCH_AMBIGUOUS = "VARIANT_SCOPE_AMBIGUOUS"
MATCH_MISMATCH = "VARIANT_MISMATCH"

_AWD_TOKENS = ("awd", "4wd", "4x4", "quattro", "4matic", "xdrive", "allwheel", "dualmotor", "htrac", "efour", "4motion", "twinmotor", "trimotor", "הנעהכפולה", "הנעהלכלהגלגלים", "4x4")
_2WD_TOKENS = ("2wd", "fwd", "rwd", "frontwheel", "rearwheel", "singlemotor", "הנעהקדמית", "הנעהאחורית")
_EV_TOKENS = ("electric", "bev", "ev", "חשמלי", "edrive")
_PHEV_TOKENS = ("pluginhybrid", "phev", "plugin", "פלאגאין")
_HYBRID_TOKENS = ("hybrid", "hev", "היברידי", "mildhybrid", "mhev", "48v")
_PETROL_TOKENS = ("petrol", "gasoline", "tfsi", "tsi", "tgdi", "gdi", "בנזין")


def _compact(text: Any) -> str:
    return re.sub(r"[^0-9a-z֐-׿.]+", "", str(text or "").lower())


def _tokens(text: Any) -> List[str]:
    return [t for t in re.split(r"[^0-9a-z֐-׿]+", str(text or "").lower()) if t]


def normalize_model_code(code: Any) -> str:
    return re.sub(r"[^0-9A-Z]", "", str(code or "").upper())


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


def _years_in(text: str) -> List[int]:
    return [int(y) for y in re.findall(r"(?<!\d)(20[0-4]\d)(?!\d)", text or "")]


def _check_model(snapshot_model: str, evidence_text: str) -> Optional[bool]:
    want = _tokens(snapshot_model)
    if not want:
        return None
    have = _compact(evidence_text)
    if not have:
        return None
    return all(tok in have for tok in want)


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
    lowered = (text or "").lower()
    hp = [float(v) for v in re.findall(r"(\d{2,4})\s*(?:hp|bhp|ps|cv|כ\"ס|כ״ס|כוח סוס)", lowered)]
    kw = [float(v) for v in re.findall(r"(\d{2,4})\s*kw(?!h)", lowered)]
    return {"hp": hp, "kw": kw}


def _check_engine(facts: Dict[str, Any], evidence_text: str, compact: str) -> Optional[bool]:
    """Engine displacement for combustion; motor configuration/power for EVs."""
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
    liters = [float(v) for v in re.findall(r"(?<![\d.])(\d\.\d)\s*(?:l|ליטר|t|tfsi|tsi|tgdi|gdi|turbo|hybrid|\b)", (evidence_text or "").lower())]
    ccs = [float(v) for v in re.findall(r"(\d{3,4})\s*(?:cc|סמ\"ק|סמ״ק|cm3)", (evidence_text or "").lower())]
    displacement_match = None
    if liters or ccs:
        want_l = round(cc / 1000.0, 1)
        displacement_match = any(abs(v - want_l) < 0.05 for v in liters) or any(abs(v - cc) <= 60 for v in ccs)
    if displacement_match is False:
        return False
    if displacement_match is True:
        return power_match is not False
    return None


def match_variant(snapshot: Dict[str, Any], claim: Dict[str, Any]) -> Dict[str, Any]:
    """Return {status, matched_by, checks, reasons}."""
    identity = snapshot["identity"]
    facts = snapshot["government"]["facts"]
    evidence = claim.get("identity_evidence") if isinstance(claim.get("identity_evidence"), dict) else {}
    evidence = {k: str(v) for k, v in evidence.items() if isinstance(v, (str, int, float)) and str(v).strip()}
    evidence_text = " ".join(evidence.values())
    compact = _compact(evidence_text)
    words = _tokens(evidence_text)

    code_evidence = normalize_model_code(evidence.get("model_code"))
    code_known = normalize_model_code(identity.get("official_model_code"))
    if code_evidence:
        if code_known and code_evidence == code_known:
            return {"status": MATCH_STRONG, "matched_by": "model_code", "checks": {"model_code": True}, "reasons": []}
        return {
            "status": MATCH_MISMATCH,
            "matched_by": None,
            "checks": {"model_code": False},
            "reasons": ["VARIANT_MODEL_CODE_MISMATCH"],
        }

    years = []
    source_year = claim.get("source_year")
    if isinstance(source_year, int) and not isinstance(source_year, bool):
        years.append(source_year)
    years += _years_in(evidence_text)
    model_year = identity.get("model_year")
    year_check: Optional[bool] = None
    if years and model_year:
        year_check = all(y == model_year for y in years)

    checks = {
        "model": _check_model(identity.get("model"), evidence.get("model", "") + " " + evidence_text),
        "year": year_check,
        "trim": _check_trim(identity.get("trim"), evidence.get("trim", "")),
        "propulsion": _check_propulsion(facts.get("propulsion"), compact, words),
        "drivetrain": _check_drivetrain(facts.get("drivetrain"), compact, words),
        "engine": _check_engine(facts, evidence_text, compact),
    }
    failed = [k for k, v in checks.items() if v is False]
    if failed:
        return {
            "status": MATCH_MISMATCH,
            "matched_by": None,
            "checks": checks,
            "reasons": [f"VARIANT_{k.upper()}_MISMATCH" for k in failed],
        }
    # Year is required only "where available"; every other element must be
    # present and consistent for a strong attribute match.
    missing = [k for k, v in checks.items() if v is None and k != "year"]
    if missing:
        return {
            "status": MATCH_AMBIGUOUS,
            "matched_by": None,
            "checks": checks,
            "reasons": [f"VARIANT_{k.upper()}_NOT_STATED" for k in missing],
        }
    return {"status": MATCH_STRONG, "matched_by": "attributes", "checks": checks, "reasons": []}


class OfficialVariantMatcher:
    """Object wrapper so the matcher can be injected/replaced (MILO later)."""

    def match(self, snapshot: Dict[str, Any], claim: Dict[str, Any]) -> Dict[str, Any]:
        return match_variant(snapshot, claim)
