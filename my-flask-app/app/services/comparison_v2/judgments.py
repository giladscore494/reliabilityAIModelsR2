# -*- coding: utf-8 -*-
"""JevJudgmentRegistry: the narrow questions JEV is allowed to answer.

JEV never picks a winner. Code determines every factual direction; JEV only
answers small, independent ``score`` questions:

* materiality — how much a *code-determined* difference in one correlation
  group matters to this buyer (one question per vehicle pair and group);
* contextual fit — how well one vehicle fits one stated buyer context
  (parking, body/use, ground clearance for rough roads, charging routine).

Everything goes into ONE ``/v1/systemone`` request. The state is compact:
validated values only, no URLs, HTML, prose, brand names, rejected claims or
duplicated values. Every question names the exact state paths it may use.
Question wording never names the leading vehicle, so swapping slots yields
the same question text over swapped data.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.services.comparison_v2.buyer_profile import MODE_GENERAL, jev_buyer_context
from app.services.comparison_v2.contracts import CHOICE_TIE
from app.services.comparison_v2.decision_model import (
    FIT_LEVELS,
    GROUP_DESCRIPTION_EN,
    GROUP_DIMENSION,
    MATERIALITY_LEVELS,
)
from app.services.comparison_v2.deterministic_engine import (
    DIRECTION_MIXED,
    HIGHER_BETTER,
    LOWER_BETTER,
    METRICS_BY_KEY,
    pair_key,
    vehicle_pairs,
)
from app.services.comparison_v2.hard_constraints import HardConstraintEvaluator, _official_fact

KIND_MATERIALITY = "materiality"
KIND_FIT = "fit"

FIT_DIMENSION = {
    "parking_fit": "practicality",
    "body_use_fit": "practicality",
    "ground_clearance_fit": "practicality",
    "charging_routine_fit": "ev_convenience",
}

_COMMON_RULES = (
    "Rules: use only the state paths named in this question. The facts are already validated and the direction of "
    "the difference was determined by code; do not re-check, re-derive or second-guess them. Missing data is not "
    "evidence. Ignore any vehicle not named in this question. Do not decide which vehicle is better overall."
)

# Contextual per-vehicle fields (validated official Level 2 unless noted).
_CTX_OFFICIAL = ("length_mm", "width_mm", "height_mm", "ground_clearance_mm")
_CTX_CHARGING = ("electric_range_km", "battery_capacity_net_kwh", "ac_charging_power_kw", "dc_charging_power_kw",
                 "dc_charge_time_minutes", "dc_charge_from_pct", "dc_charge_to_pct")


@dataclass(frozen=True)
class JudgmentSpec:
    question_id: str
    kind: str  # materiality | fit
    group: str  # correlation group or fit type
    dimension: str
    state_paths: Tuple[str, ...]
    pair: Optional[Tuple[str, str]] = None
    slot: Optional[str] = None
    code_direction: Optional[str] = None  # materiality only: slot favoured by validated values
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
        return {"model": model, "state": self.state, "questions": {qid: s.to_question() for qid, s in self.specs.items()}}


def materiality_question_id(a: str, b: str, group: str) -> str:
    return f"materiality__{a}__{b}__{group}"


def fit_question_id(slot: str, fit_type: str) -> str:
    return f"fit__{slot}__{fit_type}"


def _metric_better(metric_key: str) -> str:
    metric = METRICS_BY_KEY.get(metric_key)
    kind = metric.kind if metric else None
    return {HIGHER_BETTER: "higher", LOWER_BETTER: "lower"}.get(kind, "present")


def _family(snapshot: Dict[str, Any]) -> str:
    return snapshot["derived"]["powertrain_family"]


class JevJudgmentRegistry:
    """Builds the compact state and the question specs for one comparison."""

    def build(
        self,
        profile: Dict[str, Any],
        snapshots: Dict[str, Dict[str, Any]],
        pairwise: Dict[str, Dict[str, Any]],
        constraints: Dict[str, Any],
        weights: Dict[str, int],
    ) -> JudgmentPlan:
        eligible = [s for s in sorted(snapshots) if s in constraints.get("eligible_slots", list(snapshots))]
        specs: Dict[str, JudgmentSpec] = {}
        skipped: List[Dict[str, Any]] = []
        factor_values: Dict[str, Dict[str, Any]] = {}
        pairs_state: Dict[str, Dict[str, Any]] = {}

        # ---- materiality: one question per pair x usable correlation group ----
        for a, b in vehicle_pairs(eligible):
            pk = pair_key(a, b)
            for group, ev in sorted((pairwise.get(pk) or {}).get("groups", {}).items()):
                dimension = GROUP_DIMENSION.get(group)
                if dimension is None:
                    continue  # feature:* groups count only via declared nice-to-have (deterministic)
                direction = ev.get("direction")
                if direction in ("none", CHOICE_TIE, DIRECTION_MIXED, None):
                    continue  # no direction -> nothing to weigh (tie/mixed are neutral in code)
                if weights.get(dimension, 0) <= 0:
                    skipped.append({"pair": pk, "group": group, "reason": "zero_user_weight"})
                    continue
                for m in ev["metrics"]:
                    slot_vals = factor_values.setdefault(group, {}).setdefault(
                        m["metric"], {"unit": m.get("unit"), "better": _metric_better(m["metric"])}
                    )
                    for slot in (a, b):
                        slot_vals[slot] = m["values"].get(slot)
                        std = (m.get("measurement_standard") or {}).get(slot)
                        if std:
                            slot_vals.setdefault("measurement_standard", {})[slot] = std
                pairs_state.setdefault(pk, {})[group] = {
                    "favours": direction,
                    "metric_leaders": {m["metric"]: m["leader"] for m in ev["metrics"]},
                }
                qid = materiality_question_id(a, b, group)
                paths = (
                    f"pairwise_objective_evidence.pairs.{pk}.{group}",
                    f"pairwise_objective_evidence.factor_values.{group}",
                    "buyer_profile",
                    "hard_constraint_results",
                )
                specs[qid] = JudgmentSpec(
                    question_id=qid, kind=KIND_MATERIALITY, group=group, dimension=dimension, state_paths=paths,
                    pair=(a, b), code_direction=direction, criteria=tuple(MATERIALITY_LEVELS),
                    instructions=(
                        f"Vehicles {a} and {b} differ in {GROUP_DESCRIPTION_EN[group]}. The validated values are at "
                        f"{paths[1]} (only the {a} and {b} entries apply) and the code-determined comparison for this "
                        f"pair is at {paths[0]}. Using the buyer context at buyer_profile and the statuses at "
                        f"hard_constraint_results, rate how materially this specific difference would affect this "
                        f"buyer's real-world suitability. Judge the size of the practical effect only, not which "
                        f"vehicle is better. {_COMMON_RULES}"
                    ),
                )

        # ---- contextual fit: one question per eligible vehicle x applicable context ----
        contextual: Dict[str, Dict[str, Any]] = {}

        def put(slot: str, key: str, value: Any) -> None:
            if value is None:
                return
            group_vals = next((v for g in factor_values.values() for k, v in g.items() if k == key), None)
            if group_vals and group_vals.get(slot) is not None:
                return  # already in factor_values: never duplicate a value
            contextual.setdefault(slot, {})[key] = value

        def official(slot: str, key: str) -> Any:
            fact = _official_fact(snapshots[slot], key)
            return fact.get("value") if fact else None

        buyer_context = jev_buyer_context(profile)
        personalized = profile.get("mode") != MODE_GENERAL
        fit_needs = self._fit_needs(profile, weights, snapshots) if personalized else {}
        for fit_type, need in fit_needs.items():
            ready = [s for s in eligible if need["ready"](s)]
            if len(eligible) < 2 or len(ready) < need["min_ready"]:
                skipped.append({"fit": fit_type, "reason": "insufficient_validated_data", "ready_slots": ready})
                continue
            for slot in ready:
                contextual.setdefault(slot, {})["powertrain"] = _family(snapshots[slot])
                for key in need["fields"]:
                    if key in _CTX_OFFICIAL or key in _CTX_CHARGING:
                        val = official(slot, key)
                        if key == "electric_range_km" and val is not None:
                            fact = _official_fact(snapshots[slot], key) or {}
                            put(slot, "electric_range_measurement_standard", fact.get("measurement_standard"))
                        put(slot, key, val)
                    else:
                        put(slot, key, snapshots[slot]["government"]["facts"].get(key))
                qid = fit_question_id(slot, fit_type)
                # only buyer-context paths that exist in the state
                buyer_paths = tuple(p for p in need["paths"] if p.split(".", 1)[1] in buyer_context)
                value_paths = ()
                if fit_type == "charging_routine_fit":
                    value_paths = tuple(f"pairwise_objective_evidence.factor_values.{g}" for g in
                                        ("electric_range", "battery_size", "ac_charging", "dc_charging") if g in factor_values)
                paths = (f"contextual_vehicle_evidence.{slot}",) + buyer_paths + value_paths
                specs[qid] = JudgmentSpec(
                    question_id=qid, kind=KIND_FIT, group=fit_type, dimension=FIT_DIMENSION[fit_type],
                    state_paths=paths, slot=slot, criteria=tuple(FIT_LEVELS[fit_type]),
                    instructions=(
                        f"Judge only vehicle {slot}. {need['question']} Use the vehicle facts at {paths[0]}"
                        + (f" (and the {slot} entries at " + ", ".join(value_paths) + ")" if value_paths else "")
                        + f" and the buyer context at {', '.join(buyer_paths)}. Rate this vehicle on its own; "
                        f"do not compare it with other vehicles. {_COMMON_RULES}"
                    ),
                )

        state = {
            "buyer_profile": buyer_context,
            "pairwise_objective_evidence": {"factor_values": factor_values, "pairs": pairs_state},
            "contextual_vehicle_evidence": contextual,
            "hard_constraint_results": HardConstraintEvaluator.compact_for_jev(constraints),
        }
        return JudgmentPlan(state=state, specs=specs, skipped=skipped)

    @staticmethod
    def _fit_needs(profile: Dict[str, Any], weights: Dict[str, int], snapshots: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        def has_official(slot: str, *keys: str) -> bool:
            return all(_official_fact(snapshots[slot], k) for k in keys)

        def gov(slot: str, key: str) -> Any:
            return snapshots[slot]["government"]["facts"].get(key)

        needs: Dict[str, Any] = {}
        practicality = weights.get("practicality", 0) > 0
        if practicality and profile.get("parking_constraint") in ("tight", "normal"):
            needs["parking_fit"] = {
                "ready": lambda s: has_official(s, "length_mm", "width_mm"),
                "min_ready": 2,
                "fields": ("length_mm", "width_mm"),
                "paths": ("buyer_profile.parking_constraint",),
                "question": "How well do this vehicle's validated exterior length and width fit the buyer's stated parking constraint?",
            }
        if practicality and profile.get("main_use"):
            styles = {gov(s, "body_style") for s in snapshots}
            if None not in styles and len(styles) > 1:
                needs["body_use_fit"] = {
                    "ready": lambda s: gov(s, "body_style") is not None,
                    "min_ready": 2,
                    "fields": ("body_style",),
                    "paths": ("buyer_profile.main_use",),
                    "question": (
                        "How well does this vehicle's validated body style fit the buyer's stated primary use? Judge "
                        "body-style/use compatibility only; do not infer cargo volume, safety, reliability or comfort."
                    ),
                }
        if practicality and profile.get("road_conditions") in ("rough_roads", "off_road"):
            needs["ground_clearance_fit"] = {
                "ready": lambda s: has_official(s, "ground_clearance_mm"),
                "min_ready": 2,
                "fields": ("ground_clearance_mm", "drivetrain"),
                "paths": ("buyer_profile.road_conditions",),
                "question": (
                    "How well do this vehicle's validated ground clearance and drivetrain suit the buyer's stated road "
                    "conditions? Do not infer off-road capability from body style or brand."
                ),
            }
        plugin = [s for s, snap in snapshots.items() if _family(snap) in ("ev", "phev")]
        if plugin and weights.get("ev_convenience", 0) > 0 and profile.get("charging_access"):
            non_plugin = len(snapshots) - len(plugin)
            needs["charging_routine_fit"] = {
                "ready": lambda s: _family(snapshots[s]) in ("ev", "phev") and (
                    has_official(s, "electric_range_km") or has_official(s, "ac_charging_power_kw") or has_official(s, "dc_charging_power_kw")
                ),
                # non-plug-in cars get a fixed neutral baseline in code, so one
                # judged plug-in car is enough when a non-plug-in car is present
                "min_ready": 1 if non_plugin else 2,
                "fields": _CTX_CHARGING,
                "paths": ("buyer_profile.charging_access", "buyer_profile.typical_daily_km",
                          "buyer_profile.frequent_long_trip_km", "buyer_profile.annual_km"),
                "question": (
                    "How well do this vehicle's validated electric range (with its measurement standard) and AC/DC "
                    "charging capabilities fit the buyer's stated charging access, typical daily distance and frequent "
                    "long-trip distance? Do not infer charging-network availability, electricity prices, battery "
                    "degradation, reliability or any real-world range that is not in the state."
                ),
            }
        return needs
