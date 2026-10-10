# -*- coding: utf-8 -*-
"""V3 DecisionComposer: the V2/2 composition over the V3 dimensions.

For every eligible pair (a, b) and dimension: objective signal = code direction x JEV materiality / 4 (a code tie /
mixed group is a usable 0); contextual signal = fit(a) - fit(b); deterministic soft signals (declared nice-to-have
ADAS features in ``safety``, an AWD preference in ``practicality``); one correlation group == one signal; a
dimension's signal is the mean of its usable signals.

U(a, b) = sum(w_d * s_d) / sum(w_d) over dimensions with usable evidence. A dimension with no row and no signal for
these cars is not applicable: its weight drops and the rest renormalizes (rule 3). Constants are provisional
(``engine``).

Pairwise utilities are internal: no user-facing text is built per pair for more than two cars (``explanations``
states row leaders over ALL cars). A signal is labelled by the rows that exist (``metrics.group_label_he``), and the
AWD preference reads the drivetrain only when it is a row for every car.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.services.comparison_v3.buyer_profile import FEATURE_LABELS_HE
from app.services.comparison_v3.contracts import CHOICE_INSUFFICIENT, CHOICE_TIE, DECISION_UNAVAILABLE
from app.services.comparison_v3.engine import (
    AWD_PREFERENCE_STRENGTH,
    DIMENSIONS,
    DIRECTION_MIXED,
    MIN_EFFECTIVE_WEIGHT_COVERAGE,
    NICE_TO_HAVE_STRENGTH,
    NON_PLUGIN_CHARGING_FIT,
    PRACTICAL_TIE_MARGIN,
    SCORE_MAX,
    STRENGTH_CLEAR,
    STRENGTH_MODERATE,
    feature_value,
    pair_key,
    vehicle_pairs,
)
from app.services.comparison_v3.judgments import FIT_DIMENSION, fit_question_id, materiality_question_id
from app.services.comparison_v3.metrics import DIMENSION_CATEGORY, GROUP_LABEL_HE, group_label_he
from app.services.comparison_v3.snapshot import fact_value

from app.services.comparison_v2.decision_model import MATERIALITY_LABEL_HE

NO_VEHICLE_MEETS_REQUIREMENTS = "no_vehicle_meets_requirements"
NO_COMMON_DATA = "no_common_data"
BASIS_COMPOSITION = "composition"
BASIS_HARD_CONSTRAINTS = "hard_constraints"


def _round(x: Optional[float], n: int = 4) -> Optional[float]:
    return None if x is None else round(float(x), n)


def strength_label(utility_abs: float) -> str:
    if utility_abs >= STRENGTH_CLEAR:
        return "clear"
    if utility_abs >= STRENGTH_MODERATE:
        return "moderate"
    return "slight"


class DecisionComposer:
    def __init__(self, profile, snapshots, pairwise, constraints, specs, jev_run, weights, evidence_dimensions,
                 row_ids=None):
        self.profile, self.snapshots, self.pairwise = profile, snapshots, pairwise
        self.constraints, self.specs, self.jev_run, self.weights = constraints, specs, jev_run, weights
        self.answers = jev_run.get("answers") or {}
        # dimensions with at least one scored row for these cars (rule 3)
        self.evidence_dimensions = set(evidence_dimensions)
        # the rows that exist (the row rule); None: every fact the pair shares (unit tests of the composer alone)
        self.row_ids = None if row_ids is None else set(row_ids)

    def _answer(self, qid: str) -> Tuple[Optional[float], str]:
        if qid not in self.specs:
            return None, "not_asked"
        ans = self.answers.get(qid)
        if not ans or ans.get("status") != "ok" or ans.get("score") is None:
            return None, "judgment_unavailable"
        return float(ans["score"]), "ok"

    def _fit(self, slot: str, fit_type: str) -> Tuple[Optional[float], str]:
        if fit_type == "charging_routine_fit" and self.snapshots[slot]["derived"]["powertrain_family"] not in ("ev", "phev"):
            return NON_PLUGIN_CHARGING_FIT, "non_plugin_baseline"
        score, status = self._answer(fit_question_id(slot, fit_type))
        return (None if score is None else score / SCORE_MAX), status

    def pair_signals(self, a: str, b: str) -> List[Dict[str, Any]]:
        signals: List[Dict[str, Any]] = []
        for group, ev in sorted(((self.pairwise.get(pair_key(a, b)) or {}).get("groups") or {}).items()):
            direction = ev["direction"]
            metrics = [m["metric"] for m in ev["metrics"]]
            base = {"source": "objective", "group": group, "dimension": ev["dimension"], "category": ev["category"],
                    "label_he": group_label_he(group, metrics), "metrics": metrics, "code_direction": direction}
            if direction in (CHOICE_TIE, DIRECTION_MIXED):
                signals.append({**base, "usable": True, "value": 0.0, "status": direction})
                continue
            qid = materiality_question_id(a, b, group)
            score, status = self._answer(qid)
            if score is None:
                signals.append({**base, "usable": False, "value": None, "status": status, "question_id": qid})
                continue
            sign = 1.0 if direction == a else -1.0
            level = int(round(score))
            signals.append({**base, "usable": True, "value": sign * score / SCORE_MAX, "status": "ok", "question_id": qid,
                            "materiality": score, "materiality_level": level, "materiality_label_he": MATERIALITY_LABEL_HE[level]})
        for fit_type, dimension in FIT_DIMENSION.items():
            if fit_question_id(a, fit_type) not in self.specs and fit_question_id(b, fit_type) not in self.specs:
                continue
            fa, sa = self._fit(a, fit_type)
            fb, sb = self._fit(b, fit_type)
            base = {"source": "contextual", "group": fit_type, "dimension": dimension,
                    "category": DIMENSION_CATEGORY[dimension], "label_he": GROUP_LABEL_HE[fit_type],
                    "fit": {a: _round(fa), b: _round(fb)}, "fit_status": {a: sa, b: sb}}
            if fa is None or fb is None:
                signals.append({**base, "usable": False, "value": None, "status": "judgment_unavailable"})
            else:
                signals.append({**base, "usable": True, "value": fa - fb, "status": "ok"})
        if self.profile.get("awd_requirement") == "preferred" and (self.row_ids is None or "drivetrain" in self.row_ids):
            da, db = fact_value(self.snapshots[a], "drivetrain"), fact_value(self.snapshots[b], "drivetrain")
            if da is not None and db is not None:
                awd = ("awd", "four_wheel_drive")
                signals.append({"source": "preference", "group": "awd_preference", "dimension": "practicality",
                                "category": "practicality", "label_he": GROUP_LABEL_HE["awd_preference"], "usable": True,
                                "value": float(AWD_PREFERENCE_STRENGTH * ((da in awd) - (db in awd))), "status": "ok"})
        for feature in self.profile.get("nice_to_have_features") or []:
            va, vb = feature_value(self.snapshots[a], feature), feature_value(self.snapshots[b], feature)
            if va is None or vb is None:
                continue                                    # an unstated flag is unknown: no signal at all
            signals.append({"source": "preference", "group": f"feature:{feature}", "dimension": "safety",
                            "category": "safety", "label_he": FEATURE_LABELS_HE[feature], "usable": True,
                            "value": NICE_TO_HAVE_STRENGTH * (bool(va) - bool(vb)), "status": "ok"})
        return signals

    def compose_pair(self, a: str, b: str) -> Dict[str, Any]:
        signals = self.pair_signals(a, b)
        dims: Dict[str, Dict[str, Any]] = {}
        for dim in DIMENSIONS:
            dim_signals = [s for s in signals if s["dimension"] == dim]
            present = dim in self.evidence_dimensions or bool(dim_signals)
            weight = self.weights.get(dim, 0) if present else 0
            usable = [s for s in dim_signals if s["usable"]]
            dims[dim] = {"weight": weight, "applicable": weight > 0, "usable": weight > 0 and bool(usable),
                         "signal": (sum(s["value"] for s in usable) / len(usable)) if usable else None,
                         "signals": dim_signals}
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
        return {"pair": [a, b], "utility": _round(utility), "effective_weight_coverage": _round(coverage),
                "usable_weight": usable_w, "applicable_weight": applicable_w, "outcome": outcome, "dimensions": dims}

    def compose(self) -> Dict[str, Any]:
        slots = sorted(self.snapshots)
        eligible = [s for s in slots if s in self.constraints.get("eligible_slots", slots)]
        weights_total = sum(w for w in self.weights.values() if w > 0)
        base = {
            "weights": dict(self.weights),
            "normalized_weights": {d: _round(w / weights_total) if weights_total and w > 0 else 0.0 for d, w in self.weights.items()},
            "evidence_dimensions": sorted(self.evidence_dimensions),
            "eligible_slots": eligible,
            "failing_slots": list(self.constraints.get("failing_slots") or []),
            "constants": {"min_effective_weight_coverage": MIN_EFFECTIVE_WEIGHT_COVERAGE,
                          "practical_tie_margin": PRACTICAL_TIE_MARGIN, "strength_clear": STRENGTH_CLEAR,
                          "strength_moderate": STRENGTH_MODERATE, "calibrated": False},
        }
        pairs = {pair_key(a, b): self.compose_pair(a, b) for a, b in vehicle_pairs(eligible)}
        base["pairs"] = pairs
        none = {"recommended_slot": None, "strength": None, "effective_weight_coverage": None, "tied_slots": []}
        if not eligible:
            return {**base, **none, "outcome": NO_VEHICLE_MEETS_REQUIREMENTS, "basis": BASIS_HARD_CONSTRAINTS}
        if len(eligible) == 1:
            return {**base, **none, "outcome": eligible[0], "recommended_slot": eligible[0], "basis": BASIS_HARD_CONSTRAINTS}
        if not self.evidence_dimensions:
            # no weighted category remains for these cars: the one whole-result "no data" message
            return {**base, **none, "outcome": CHOICE_INSUFFICIENT, "basis": None, "reason": NO_COMMON_DATA}
        if self.specs and self.jev_run.get("status") != "ok":
            return {**base, **none, "outcome": DECISION_UNAVAILABLE, "basis": None, "reason": self.jev_run.get("reason")}
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
        tied = [s for s in eligible if losses[s] == 0]
        if insufficient:
            return {**base, **none, "outcome": CHOICE_INSUFFICIENT, "basis": None, "effective_weight_coverage": coverage,
                    "tied_slots": tied}
        return {**base, **none, "outcome": CHOICE_TIE, "basis": BASIS_COMPOSITION, "effective_weight_coverage": coverage,
                "tied_slots": tied}


def strongest_reasons(composition: Dict[str, Any], limit: int = 4) -> Dict[str, List[Dict[str, Any]]]:
    """Top signals for the recommended car and the strongest counter-points (from the contributions)."""
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
                                                    "category": s.get("category"), "source": s["source"],
                                                    "metrics": list(s.get("metrics") or []),
                                                    "contribution": 0.0, "against": {},
                                                    "materiality_label_he": s.get("materiality_label_he")})
                entry["contribution"] += sign * s["contribution"] / len(own)
                if sign * s["contribution"] < 0:
                    entry["against"][other] = True
    items = [dict(v, contribution=round(v["contribution"], 4), against=sorted(v["against"])) for v in acc.values()]
    pros = sorted([i for i in items if i["contribution"] > 0], key=lambda i: -i["contribution"])[:limit]
    cons = sorted([i for i in items if i["contribution"] < 0], key=lambda i: i["contribution"])[:2]
    return {"for": pros, "against": cons}
