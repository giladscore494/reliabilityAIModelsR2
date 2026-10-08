# -*- coding: utf-8 -*-
"""V3 JEV micro-judgments: the same narrow ``score`` questions as V2/2, built from the rows only.

* materiality — one question per eligible vehicle pair and correlation group whose code direction is a car
  (never tie / mixed, never a dimension the user weighted 0, never towing without a towing requirement);
* contextual fit (personalized mode) — ``parking_fit`` (the length / width rows), ``body_use_fit`` (body style vs
  ``main_use``), ``charging_routine_fit`` (the electric-range row only; no charging-power inputs). No
  ground-clearance fit (no data).

The JEV state holds row values only: a metric that is not a row (the row rule) is never sent. No URLs, no raw
records, no brand names.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.services.comparison_v2.decision_model import FIT_LEVELS, MATERIALITY_LEVELS
from app.services.comparison_v3.buyer_profile import MODE_GENERAL, jev_buyer_context
from app.services.comparison_v3.contracts import CHOICE_TIE
from app.services.comparison_v3.engine import DIRECTION_MIXED, compact_constraints_for_jev, pair_key, vehicle_pairs
from app.services.comparison_v3.metrics import GROUP_DESCRIPTION_EN, GROUP_DIMENSION, HIGHER_BETTER, LOWER_BETTER, METRICS_BY_KEY

KIND_MATERIALITY = "materiality"
KIND_FIT = "fit"
FIT_DIMENSION = {"parking_fit": "practicality", "body_use_fit": "practicality", "charging_routine_fit": "ev_convenience"}

_COMMON_RULES = (
    "Rules: use only the state paths named in this question. The facts are already validated and the direction of "
    "the difference was determined by code; do not re-check, re-derive or second-guess them. Missing data is not "
    "evidence. Ignore any vehicle not named in this question. Do not decide which vehicle is better overall."
)


@dataclass(frozen=True)
class JudgmentSpec:
    question_id: str
    kind: str
    group: str
    dimension: str
    state_paths: Tuple[str, ...]
    pair: Optional[Tuple[str, str]] = None
    slot: Optional[str] = None
    code_direction: Optional[str] = None
    criteria: Tuple[str, ...] = field(default_factory=tuple)
    instructions: str = ""

    def to_question(self) -> Dict[str, Any]:
        return {"type": "score", "instructions": self.instructions, "criteria": list(self.criteria)}

    def to_trace(self) -> Dict[str, Any]:
        out = asdict(self)
        out["pair"] = list(self.pair) if self.pair else None
        out["state_paths"] = list(self.state_paths)
        out["criteria"] = list(self.criteria)
        return out


@dataclass
class JudgmentPlan:
    state: Dict[str, Any]
    specs: Dict[str, JudgmentSpec]
    skipped: List[Dict[str, Any]]

    def request_body(self, model: str) -> Dict[str, Any]:
        return {"model": model, "state": self.state, "questions": {q: s.to_question() for q, s in self.specs.items()}}


def materiality_question_id(a: str, b: str, group: str) -> str:
    return f"materiality__{a}__{b}__{group}"


def fit_question_id(slot: str, fit_type: str) -> str:
    return f"fit__{slot}__{fit_type}"


def _better(metric_key: str) -> str:
    kind = METRICS_BY_KEY[metric_key].kind
    return {HIGHER_BETTER: "higher", LOWER_BETTER: "lower"}.get(kind, "present")


def build_plan(profile: Dict[str, Any], snapshots: Dict[str, Dict[str, Any]], rows: List[Dict[str, Any]],
               pairwise: Dict[str, Dict[str, Any]], constraints: Dict[str, Any], weights: Dict[str, int]) -> JudgmentPlan:
    eligible = [s for s in sorted(snapshots) if s in constraints.get("eligible_slots", list(snapshots))]
    specs: Dict[str, JudgmentSpec] = {}
    skipped: List[Dict[str, Any]] = []
    factor_values: Dict[str, Dict[str, Any]] = {}
    pairs_state: Dict[str, Dict[str, Any]] = {}

    for a, b in vehicle_pairs(eligible):
        pk = pair_key(a, b)
        for group, ev in sorted((pairwise.get(pk) or {}).get("groups", {}).items()):
            dimension = GROUP_DIMENSION[group]
            direction = ev.get("direction")
            if direction in (CHOICE_TIE, DIRECTION_MIXED, None):
                continue
            if weights.get(dimension, 0) <= 0:
                skipped.append({"pair": pk, "group": group, "reason": "zero_user_weight"})
                continue
            for m in ev["metrics"]:
                entry = factor_values.setdefault(group, {}).setdefault(
                    m["metric"], {"unit": m.get("unit"), "better": _better(m["metric"])})
                if m.get("standard"):
                    entry["measurement_standard"] = m["standard"]
                for slot in (a, b):
                    entry[slot] = m["values"][slot]
            pairs_state.setdefault(pk, {})[group] = {"favours": direction,
                                                     "metric_leaders": {m["metric"]: m["leader"] for m in ev["metrics"]}}
            qid = materiality_question_id(a, b, group)
            paths = (f"pairwise_objective_evidence.pairs.{pk}.{group}", f"pairwise_objective_evidence.factor_values.{group}",
                     "buyer_profile", "hard_constraint_results")
            specs[qid] = JudgmentSpec(
                question_id=qid, kind=KIND_MATERIALITY, group=group, dimension=dimension, state_paths=paths,
                pair=(a, b), code_direction=direction, criteria=tuple(MATERIALITY_LEVELS),
                instructions=(
                    f"Vehicles {a} and {b} differ in {GROUP_DESCRIPTION_EN[group]}. The validated values are at "
                    f"{paths[1]} (only the {a} and {b} entries apply) and the code-determined comparison for this pair "
                    f"is at {paths[0]}. Using the buyer context at buyer_profile and the statuses at "
                    f"hard_constraint_results, rate how materially this specific difference would affect this buyer's "
                    f"real-world suitability. Judge the size of the practical effect only, not which vehicle is "
                    f"better. {_COMMON_RULES}"),
            )

    contextual: Dict[str, Dict[str, Any]] = {}
    by_id = {r["row_id"]: r for r in rows}
    buyer_context = jev_buyer_context(profile)
    if profile.get("mode") != MODE_GENERAL:
        for fit_type, need in _fit_needs(profile, weights, snapshots, by_id).items():
            if len(eligible) < 2:
                skipped.append({"fit": fit_type, "reason": "single_eligible_vehicle"})
                continue
            ready = [s for s in eligible if need["ready"](s)]
            if len(ready) < need["min_ready"]:
                skipped.append({"fit": fit_type, "reason": "no_rows", "ready_slots": ready})
                continue
            for slot in ready:
                contextual.setdefault(slot, {})["powertrain"] = snapshots[slot]["derived"]["powertrain_family"]
                for key, value in need["values"](slot).items():
                    contextual[slot][key] = value
                qid = fit_question_id(slot, fit_type)
                buyer_paths = tuple(p for p in need["paths"] if p.split(".", 1)[1] in buyer_context)
                paths = (f"contextual_vehicle_evidence.{slot}",) + buyer_paths
                specs[qid] = JudgmentSpec(
                    question_id=qid, kind=KIND_FIT, group=fit_type, dimension=FIT_DIMENSION[fit_type], state_paths=paths,
                    slot=slot, criteria=tuple(FIT_LEVELS[fit_type]),
                    instructions=(f"Judge only vehicle {slot}. {need['question']} Use the vehicle facts at {paths[0]} and "
                                  f"the buyer context at {', '.join(buyer_paths)}. Rate this vehicle on its own; do not "
                                  f"compare it with other vehicles. {_COMMON_RULES}"),
                )
    state = {
        "buyer_profile": buyer_context,
        "pairwise_objective_evidence": {"factor_values": factor_values, "pairs": pairs_state},
        "contextual_vehicle_evidence": contextual,
        "hard_constraint_results": compact_constraints_for_jev(constraints),
    }
    return JudgmentPlan(state=state, specs=specs, skipped=skipped)


def _fit_needs(profile, weights, snapshots, rows: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    needs: Dict[str, Any] = {}
    practicality = weights.get("practicality", 0) > 0
    if practicality and profile.get("parking_constraint") in ("tight", "normal") and "length_mm" in rows and "width_mm" in rows:
        needs["parking_fit"] = {
            "ready": lambda s: True, "min_ready": 2,
            "values": lambda s: {"length_mm": rows["length_mm"]["values"][s], "width_mm": rows["width_mm"]["values"][s]},
            "paths": ("buyer_profile.parking_constraint",),
            "question": "How well do this vehicle's validated exterior length and width fit the buyer's stated parking constraint?",
        }
    if practicality and profile.get("main_use") and "body_style" in rows:
        styles = set(rows["body_style"]["values"].values())
        if len(styles) > 1:
            needs["body_use_fit"] = {
                "ready": lambda s: True, "min_ready": 2,
                "values": lambda s: {"body_style": rows["body_style"]["values"][s]},
                "paths": ("buyer_profile.main_use",),
                "question": ("How well does this vehicle's validated body style fit the buyer's stated primary use? Judge "
                             "body-style/use compatibility only; do not infer cargo volume, safety, reliability or comfort."),
            }
    if weights.get("ev_convenience", 0) > 0 and profile.get("charging_access") and "electric_range_km" in rows:
        rng = rows["electric_range_km"]
        needs["charging_routine_fit"] = {
            "ready": lambda s: True, "min_ready": 2,
            "values": lambda s: {"electric_range_km": rng["values"][s], "electric_range_measurement_standard": rng["standard"]},
            "paths": ("buyer_profile.charging_access", "buyer_profile.typical_daily_km",
                      "buyer_profile.frequent_long_trip_km", "buyer_profile.annual_km"),
            "question": ("How well does this vehicle's validated electric range (with its measurement standard) fit the "
                         "buyer's stated charging access, typical daily distance and frequent long-trip distance? Do not "
                         "infer charging power, charging-network availability, electricity prices, battery degradation, "
                         "reliability or any real-world range that is not in the state."),
        }
    return needs
