# -*- coding: utf-8 -*-
"""DecisionComposer: combines facts, JEV judgments and user weights in code.

For every eligible vehicle pair (a, b) and every decision dimension:

* objective signal  = code direction (+1 favours a, -1 favours b) x JEV
  materiality score / 4; a code ``tie`` / ``mixed`` group is a usable 0;
* contextual signal = normalized fit(a) - normalized fit(b) (JEV fit score / 4;
  non-plug-in cars get a fixed neutral charging fit);
* deterministic soft signals: declared nice-to-have features and an AWD
  preference (+/- a named constant);
* one correlation group == one signal; a dimension's signal is the mean of its
  usable signals.

Pair utility U(a, b) = sum(w_d * s_d) / sum(w_d) over dimensions with usable
evidence (weights renormalize over the available evidence), and
``effective_weight_coverage`` = usable weight / applicable weight. Priority 0
has no influence. There is no /100 score and no averaged confidence.

Selection: hard constraints first (a failing car is never selected while
another passes); then a car must beat every other eligible car by more than
``PRACTICAL_TIE_MARGIN`` with enough coverage, otherwise the outcome is a tie
or insufficient evidence.

All constants are provisional and uncalibrated (see ``decision_model``).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.services.comparison_v2.buyer_profile import FEATURE_LABELS_HE
from app.services.comparison_v2.contracts import CHOICE_INSUFFICIENT, CHOICE_TIE, DECISION_UNAVAILABLE
from app.services.comparison_v2.decision_model import (
    AWD_PREFERENCE_STRENGTH,
    CATEGORY_DIMENSIONS,
    DIMENSIONS,
    GROUP_DIMENSION,
    GROUP_LABEL_HE,
    MATERIALITY_LABEL_HE,
    MIN_EFFECTIVE_WEIGHT_COVERAGE,
    NICE_TO_HAVE_STRENGTH,
    NON_PLUGIN_CHARGING_FIT,
    PRACTICAL_TIE_MARGIN,
    SCORE_MAX,
    STRENGTH_CLEAR,
    STRENGTH_MODERATE,
)
from app.services.comparison_v2.deterministic_engine import DIRECTION_MIXED, pair_key, vehicle_pairs
from app.services.comparison_v2.hard_constraints import feature_value
from app.services.comparison_v2.judgments import FIT_DIMENSION, fit_question_id, materiality_question_id

NO_VEHICLE_MEETS_REQUIREMENTS = "no_vehicle_meets_requirements"

BASIS_COMPOSITION = "composition"
BASIS_HARD_CONSTRAINTS = "hard_constraints"

FIT_CATEGORY = {
    "parking_fit": "practicality",
    "body_use_fit": "practicality",
    "ground_clearance_fit": "practicality",
    "charging_routine_fit": "electric_and_charging",
    "awd_preference": "practicality",
}

DIMENSION_CATEGORY = {dim: cat for cat, dims in CATEGORY_DIMENSIONS.items() for dim in dims}


def _family(snapshot: Dict[str, Any]) -> str:
    return snapshot["derived"]["powertrain_family"]


def _round(x: Optional[float], n: int = 4) -> Optional[float]:
    return None if x is None else round(float(x), n)


def strength_label(utility_abs: float) -> str:
    if utility_abs >= STRENGTH_CLEAR:
        return "clear"
    if utility_abs >= STRENGTH_MODERATE:
        return "moderate"
    return "slight"


class DecisionComposer:
    def __init__(self, profile: Dict[str, Any], snapshots: Dict[str, Dict[str, Any]], pairwise: Dict[str, Any],
                 constraints: Dict[str, Any], specs: Dict[str, Any], jev_run: Dict[str, Any], weights: Dict[str, int]):
        self.profile = profile
        self.snapshots = snapshots
        self.pairwise = pairwise
        self.constraints = constraints
        self.specs = specs
        self.jev_run = jev_run
        self.answers = jev_run.get("answers") or {}
        self.weights = weights

    # ---- signals -----------------------------------------------------------
    def _answer(self, qid: str) -> Tuple[Optional[float], str]:
        if qid not in self.specs:
            return None, "not_asked"
        ans = self.answers.get(qid)
        if not ans:
            return None, "judgment_unavailable"
        if ans.get("status") != "ok" or ans.get("score") is None:
            return None, "judgment_unavailable"
        return float(ans["score"]), "ok"

    def _fit(self, slot: str, fit_type: str) -> Tuple[Optional[float], str]:
        if fit_type == "charging_routine_fit" and _family(self.snapshots[slot]) not in ("ev", "phev"):
            return NON_PLUGIN_CHARGING_FIT, "non_plugin_baseline"
        score, status = self._answer(fit_question_id(slot, fit_type))
        return (None if score is None else score / SCORE_MAX), status

    def pair_signals(self, a: str, b: str) -> List[Dict[str, Any]]:
        """Every candidate signal for the pair; ``value`` > 0 favours ``a``."""
        signals: List[Dict[str, Any]] = []
        groups = (self.pairwise.get(pair_key(a, b)) or {}).get("groups", {})
        for group, ev in sorted(groups.items()):
            dimension = GROUP_DIMENSION.get(group)
            if dimension is None:
                continue
            direction = ev.get("direction")
            base = {"source": "objective", "group": group, "dimension": dimension, "category": ev.get("category"),
                    "label_he": GROUP_LABEL_HE.get(group, group), "code_direction": direction}
            if direction == "none":
                continue
            if direction in (CHOICE_TIE, DIRECTION_MIXED):
                signals.append({**base, "usable": True, "value": 0.0, "status": direction})
                continue
            qid = materiality_question_id(a, b, group)
            score, status = self._answer(qid)
            if score is None:
                signals.append({**base, "usable": False, "value": None, "status": status, "question_id": qid})
                continue
            sign = 1.0 if direction == a else -1.0
            signals.append({**base, "usable": True, "value": sign * score / SCORE_MAX, "status": "ok", "question_id": qid,
                            "materiality": score, "materiality_level": int(round(score)),
                            "materiality_label_he": MATERIALITY_LABEL_HE[int(round(score))]})

        for fit_type, dimension in FIT_DIMENSION.items():
            qa, qb = fit_question_id(a, fit_type), fit_question_id(b, fit_type)
            if qa not in self.specs and qb not in self.specs:
                continue
            fa, sa = self._fit(a, fit_type)
            fb, sb = self._fit(b, fit_type)
            base = {"source": "contextual", "group": fit_type, "dimension": dimension, "category": FIT_CATEGORY[fit_type],
                    "label_he": GROUP_LABEL_HE[fit_type], "fit": {a: _round(fa), b: _round(fb)}, "fit_status": {a: sa, b: sb}}
            if fa is None or fb is None:
                signals.append({**base, "usable": False, "value": None, "status": "judgment_unavailable"})
            else:
                signals.append({**base, "usable": True, "value": fa - fb, "status": "ok"})

        if self.profile.get("awd_requirement") == "preferred":
            da = self.snapshots[a]["government"]["facts"].get("drivetrain")
            db = self.snapshots[b]["government"]["facts"].get("drivetrain")
            base = {"source": "preference", "group": "awd_preference", "dimension": "practicality", "category": "practicality",
                    "label_he": GROUP_LABEL_HE["awd_preference"]}
            if da is None or db is None:
                signals.append({**base, "usable": False, "value": None, "status": "insufficient_data"})
            else:
                value = AWD_PREFERENCE_STRENGTH * ((da == "awd") - (db == "awd"))
                signals.append({**base, "usable": True, "value": float(value), "status": "ok"})

        for feature in self.profile.get("nice_to_have_features") or []:
            va, vb = feature_value(self.snapshots[a], feature), feature_value(self.snapshots[b], feature)
            base = {"source": "preference", "group": f"feature:{feature}", "dimension": "equipment",
                    "category": "equipment_and_convenience", "label_he": FEATURE_LABELS_HE[feature]}
            if va is None or vb is None:
                signals.append({**base, "usable": False, "value": None, "status": "insufficient_data"})
            else:
                signals.append({**base, "usable": True, "value": NICE_TO_HAVE_STRENGTH * (bool(va) - bool(vb)), "status": "ok"})
        return signals

    def applicable_weight(self, dim: str) -> int:
        """User weight of a dimension that CAN carry evidence for this profile.

        Equipment carries weight only through declared nice-to-have features
        (must-have features are hard constraints); a car never gains from
        having more undeclared equipment, so without declared features the
        dimension is not applicable and does not lower coverage.
        """
        if dim == "equipment" and not self.profile.get("nice_to_have_features"):
            return 0
        return self.weights.get(dim, 0)

    # ---- pair composition ----------------------------------------------------
    def compose_pair(self, a: str, b: str) -> Dict[str, Any]:
        signals = self.pair_signals(a, b)
        dims: Dict[str, Dict[str, Any]] = {}
        for dim in DIMENSIONS:
            weight = self.applicable_weight(dim)
            dim_signals = [s for s in signals if s["dimension"] == dim]
            usable = [s for s in dim_signals if s["usable"]]
            dims[dim] = {
                "weight": weight,
                "applicable": weight > 0,
                "usable": weight > 0 and bool(usable),
                "signal": (sum(s["value"] for s in usable) / len(usable)) if usable else None,
                "signals": dim_signals,
            }
        applicable_w = sum(d["weight"] for d in dims.values() if d["applicable"])
        usable_w = sum(d["weight"] for d in dims.values() if d["usable"])
        utility = (sum(d["weight"] * d["signal"] for d in dims.values() if d["usable"]) / usable_w) if usable_w else None
        coverage = (usable_w / applicable_w) if applicable_w else 0.0
        for d in dims.values():
            d["normalized_weight"] = _round(d["weight"] / usable_w) if d["usable"] and usable_w else 0.0
            d["contribution"] = _round(d["weight"] * d["signal"] / usable_w) if d["usable"] and usable_w else None
            n = len([s for s in d["signals"] if s["usable"]])
            for s in d["signals"]:
                s["contribution"] = _round(d["weight"] * s["value"] / usable_w / n) if (d["usable"] and s["usable"] and usable_w) else None
                if s.get("value") is not None:
                    s["value"] = _round(s["value"])
            d["signal"] = _round(d["signal"])
        if utility is None or coverage < MIN_EFFECTIVE_WEIGHT_COVERAGE:
            outcome = CHOICE_INSUFFICIENT
        elif abs(utility) < PRACTICAL_TIE_MARGIN:
            outcome = CHOICE_TIE
        else:
            outcome = a if utility > 0 else b
        return {
            "pair": [a, b],
            "utility": _round(utility),
            "effective_weight_coverage": _round(coverage),
            "usable_weight": usable_w,
            "applicable_weight": applicable_w,
            "outcome": outcome,
            "dimensions": dims,
        }

    # ---- full decision --------------------------------------------------------
    def compose(self) -> Dict[str, Any]:
        slots = sorted(self.snapshots)
        eligible = [s for s in slots if s in self.constraints.get("eligible_slots", slots)]
        weights_total = sum(w for w in self.weights.values() if w > 0)
        base = {
            "weights": dict(self.weights),
            "normalized_weights": {d: _round(w / weights_total) if weights_total and w > 0 else 0.0 for d, w in self.weights.items()},
            "eligible_slots": eligible,
            "failing_slots": list(self.constraints.get("failing_slots") or []),
            "constants": {
                "min_effective_weight_coverage": MIN_EFFECTIVE_WEIGHT_COVERAGE,
                "practical_tie_margin": PRACTICAL_TIE_MARGIN,
                "strength_clear": STRENGTH_CLEAR,
                "strength_moderate": STRENGTH_MODERATE,
                "calibrated": False,
            },
        }
        pairs = {pair_key(a, b): self.compose_pair(a, b) for a, b in vehicle_pairs(eligible)}
        base["pairs"] = pairs

        if not eligible:
            return {**base, "outcome": NO_VEHICLE_MEETS_REQUIREMENTS, "recommended_slot": None, "basis": BASIS_HARD_CONSTRAINTS,
                    "strength": None, "effective_weight_coverage": None, "tied_slots": []}
        if len(eligible) == 1:
            return {**base, "outcome": eligible[0], "recommended_slot": eligible[0], "basis": BASIS_HARD_CONSTRAINTS,
                    "strength": None, "effective_weight_coverage": None, "tied_slots": []}
        if self.specs and self.jev_run.get("status") != "ok":
            return {**base, "outcome": DECISION_UNAVAILABLE, "recommended_slot": None, "basis": None, "strength": None,
                    "effective_weight_coverage": None, "tied_slots": [], "reason": self.jev_run.get("reason")}

        beats = {s: set() for s in eligible}
        losses = {s: 0 for s in eligible}
        insufficient = False
        for p in pairs.values():
            a, b = p["pair"]
            if p["outcome"] == CHOICE_INSUFFICIENT:
                insufficient = True
            elif p["outcome"] in (a, b):
                loser = b if p["outcome"] == a else a
                beats[p["outcome"]].add(loser)
                losses[loser] += 1
        winner = next((s for s in eligible if len(beats[s]) == len(eligible) - 1), None)
        if winner:
            own = [p for p in pairs.values() if winner in p["pair"]]
            return {**base, "outcome": winner, "recommended_slot": winner, "basis": BASIS_COMPOSITION,
                    "strength": strength_label(min(abs(p["utility"]) for p in own)),
                    "effective_weight_coverage": min(p["effective_weight_coverage"] for p in own), "tied_slots": []}
        coverage = min(p["effective_weight_coverage"] for p in pairs.values())
        if insufficient:
            # Some pair lacks enough evidence and nobody beats everyone: never
            # promote a car over one it could not be compared with.
            return {**base, "outcome": CHOICE_INSUFFICIENT, "recommended_slot": None, "basis": None, "strength": None,
                    "effective_weight_coverage": coverage, "tied_slots": [s for s in eligible if losses[s] == 0]}
        return {**base, "outcome": CHOICE_TIE, "recommended_slot": None, "basis": BASIS_COMPOSITION, "strength": None,
                "effective_weight_coverage": coverage, "tied_slots": [s for s in eligible if losses[s] == 0]}


# ---------------------------------------------------------------------------
# reasons (deterministic, from contributions)
# ---------------------------------------------------------------------------
def strongest_reasons(composition: Dict[str, Any], limit: int = 4) -> Dict[str, List[Dict[str, Any]]]:
    """Top signals for the recommended car (and the strongest counter-points).

    For each signal group, the winner-oriented contribution is averaged over
    the winner's pairs. Only real, positive contributions are reasons.
    """
    winner = composition.get("recommended_slot")
    if not winner or composition.get("basis") != BASIS_COMPOSITION:
        return {"for": [], "against": []}
    acc: Dict[str, Dict[str, Any]] = {}
    own = [p for p in composition["pairs"].values() if winner in p["pair"]]
    for p in own:
        sign = 1.0 if p["pair"][0] == winner else -1.0
        other = p["pair"][1] if sign > 0 else p["pair"][0]
        for dim, d in p["dimensions"].items():
            for s in d["signals"]:
                if s.get("contribution") is None:
                    continue
                entry = acc.setdefault(s["group"], {"group": s["group"], "label_he": s["label_he"], "dimension": dim,
                                                    "category": s.get("category") or DIMENSION_CATEGORY.get(dim),
                                                    "source": s["source"], "contribution": 0.0, "against": {},
                                                    "materiality_label_he": s.get("materiality_label_he")})
                entry["contribution"] += sign * s["contribution"] / len(own)
                if sign * s["contribution"] < 0:
                    entry["against"][other] = True
    items = [dict(v, contribution=round(v["contribution"], 4), against=sorted(v["against"])) for v in acc.values()]
    pros = sorted([i for i in items if i["contribution"] > 0], key=lambda i: -i["contribution"])[:limit]
    cons = sorted([i for i in items if i["contribution"] < 0], key=lambda i: i["contribution"])[:2]
    return {"for": pros, "against": cons}
