# -*- coding: utf-8 -*-
"""Comparison V2 orchestration.

validate request -> resolve variants -> Level 1.5 snapshots -> validate the
buyer profile (``buyer-profile/2``) -> official enrichment per car (cache
first, max one remote call per car, concurrent) -> deterministic validation +
canonical merge -> deterministic comparison + pairwise evidence -> hard
constraints -> ONE JEV System One call with many narrow Score questions ->
deterministic composition (code decides) -> one Gemini summary call that
explains the immutable result -> persist -> response.

``run_comparison_v2`` is a generator of events so the route can stream
meaningful progress (NDJSON) or simply collect the final event.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterator, List, Optional

from app.services.comparison_v2.buyer_profile import (
    BUYER_PROFILE_VERSION,
    UNSUPPORTED_CONCEPTS,
    UNSUPPORTED_LABELS_HE,
    BuyerProfileError,
    normalize_buyer_profile,
    profile_summary_he,
)
from app.services.comparison_v2.composer import DecisionComposer, strongest_reasons
from app.services.comparison_v2.contracts import (
    DECISION_UNAVAILABLE,
    ENGINE_VERSION,
    ENRICHMENT_CONTRACT_VERSION,
    MAX_CARS,
    MIN_CARS,
    PROGRESS_LABELS_HE,
    SLOT_KEYS,
    VehicleCatalogRepository,
    OfficialEnrichmentRepository,
)
from app.services.comparison_v2.demo_catalog import is_valid_identity_key
from app.services.comparison_v2.decision_model import DIMENSIONS, dimension_weights
from app.services.comparison_v2.deterministic_engine import (
    CATEGORIES,
    STATUS_COMPARED,
    build_pairwise_evidence,
    run_deterministic_comparison,
)
from app.services.comparison_v2.explanations import (
    MODEL_CERTAINTY_LABEL_HE,
    MODEL_CERTAINTY_NOTE_HE,
    build_category_cards,
    constraint_notes,
    reason_texts,
    recommendation_view,
)
from app.services.comparison_v2.hard_constraints import HardConstraintEvaluator
from app.services.comparison_v2.jev_client import TypeSafeJevClient, run_system_one
from app.services.comparison_v2.judgments import KIND_FIT, KIND_MATERIALITY, JevJudgmentRegistry
from app.services.comparison_v2.level15 import build_level15_snapshot
from app.services.comparison_v2.official_enrichment import LiveOfficialEnrichmentRepository, enrich_many
from app.services.comparison_v2.source_registry import SOURCE_REGISTRY_VERSION
from app.services.comparison_v2.summary_writer import GeminiSummaryWriter, build_summary_payload, produce_summary

logger = logging.getLogger("comparison_v2")

LIMITATION_SOURCES_HE = "ההשוואה מבוססת על נתוני משרד התחבורה ועל אתרי יבואן/יצרן רשמיים בלבד."
LIMITATION_RELIABILITY_HE = "אמינות ארוכת טווח עדיין אינה נכללת בגרסת ההשוואה הזו."
LIMITATION_COST_HE = "עלויות אחזקה אינן נכללות בגרסה זו; נכלל רק מחיר רשמי מיבואן כשהוא מפורסם."
LIMITATION_WARRANTY_HE = "תנאי אחריות אינם מדד לאמינות."

LIMITATION_WEIGHTS_HE = "משקלי ההכרעה וספי ההכרעה הם ראשוניים ועדיין לא כוילו על מערך הערכה שלנו."


@dataclass
class PipelineDeps:
    catalog: VehicleCatalogRepository
    enrichment: OfficialEnrichmentRepository
    jev_client: Optional[TypeSafeJevClient]
    summary_writer: Optional[GeminiSummaryWriter]
    history: Optional[Any] = None  # ComparisonV2HistoryStore-like
    enrichment_concurrency: int = 3
    provider_meta: Dict[str, Any] = field(default_factory=dict)
    jev_unavailable_reason: Optional[str] = None


def _log(event: str, **fields: Any) -> None:
    logger.info("%s %s", event, json.dumps(fields, ensure_ascii=False, sort_keys=True, default=str))


def _progress(stage: str, **extra: Any) -> Dict[str, Any]:
    return {"type": "progress", "stage": stage, "label_he": PROGRESS_LABELS_HE.get(stage, stage), **extra}


def _error(status: int, code: str, message: str) -> Dict[str, Any]:
    return {"type": "error", "status": status, "code": code, "message": message}


# ---------------------------------------------------------------------------
# request / buyer profile
# ---------------------------------------------------------------------------
def validate_v2_request(data: Any, catalog: VehicleCatalogRepository):
    if not isinstance(data, dict):
        return None, _error(400, "validation_error", "בקשה לא תקינה")
    cars = data.get("cars")
    if not isinstance(cars, list) or len(cars) < MIN_CARS:
        return None, _error(400, "validation_error", "יש לבחור לפחות 2 גרסאות רכב להשוואה")
    if len(cars) > MAX_CARS:
        return None, _error(400, "validation_error", "ניתן להשוות עד 3 רכבים בלבד")
    keys: List[str] = []
    for idx, car in enumerate(cars):
        key = car.get("variant_identity_key") if isinstance(car, dict) else None
        if not is_valid_identity_key(key):
            return None, _error(400, "variant_required", f"רכב {idx + 1}: יש לבחור גרסה מדויקת מהרשימה")
        if key in keys:
            return None, _error(400, "validation_error", "לא ניתן להשוות גרסה לעצמה. יש לבחור גרסאות שונות.")
        keys.append(key)
    records = []
    for idx, key in enumerate(keys):
        rec = catalog.get_variant(key)
        if not rec:
            return None, _error(404, "variant_not_found", f"רכב {idx + 1}: הגרסה לא נמצאה בקטלוג")
        records.append(rec)
    return records, None


def limitations_for(comparison: Dict[str, Any]) -> List[str]:
    out = [LIMITATION_SOURCES_HE, LIMITATION_RELIABILITY_HE, LIMITATION_COST_HE]
    warranty_compared = any(
        r["metric"].startswith("warranty_") and r["status"] == STATUS_COMPARED
        for r in comparison["categories"]["official_price_and_warranty"]["atomic_results"]
    )
    if warranty_compared:
        out.append(LIMITATION_WARRANTY_HE)
    out.append(LIMITATION_WEIGHTS_HE)
    return out


def _profile_error_message(exc: BuyerProfileError) -> str:
    text = str(exc)
    if text.startswith("buyer_profile") or text.startswith("mode"):
        return "יש לבחור השוואה מותאמת אליי או השוואה כללית"
    if text.startswith("main_use"):
        return "יש לבחור את השימוש העיקרי ברכב"
    if text.startswith("priorities"):
        return "יש לדרג את החשיבות של כל תחום (0–4)"
    return f"אחד משדות ההתאמה אינו תקין ({text.split(':', 1)[0]})"


def compute_request_hash(keys: List[str], buyer_profile: Optional[Dict[str, Any]], provider_meta: Dict[str, Any]) -> str:
    material = {
        "engine": ENGINE_VERSION,
        "keys": keys,
        "buyer": buyer_profile or {},  # the complete NORMALIZED buyer-profile/2
        "registry": SOURCE_REGISTRY_VERSION,
        "contract": ENRICHMENT_CONTRACT_VERSION,
        "models": {k: provider_meta.get(k) for k in ("enrichment_model", "summary_model", "jev_model")},
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# response helpers
# ---------------------------------------------------------------------------
def _car_card(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    identity = snapshot["identity"]
    facts = snapshot["government"]["facts"]
    return {
        "variant_identity_key": identity["variant_identity_key"],
        "display_name": identity["display_name"],
        "make": identity["make_display"],
        "model": identity["model_display"],
        "government_model": identity["model"],
        "year": identity["model_year"],
        "trim": identity["trim"],
        "official_model_code": identity["official_model_code"],
        "horsepower": facts.get("horsepower"),
        "drivetrain": snapshot["derived"]["drivetrain_label"],
        "fuel_label": snapshot["derived"]["fuel_label_he"] if facts.get("propulsion") == "conventional" else snapshot["derived"]["propulsion_label_he"],
        "body_label": snapshot["derived"]["body_label_he"],
        "seats": facts.get("seats"),
    }


def _response_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    official = snapshot.get("official_enrichment") or {}
    return {
        **{k: snapshot[k] for k in ("contract_version", "vehicle_id", "slot", "identity", "government", "derived", "coverage")},
        "official_enrichment": {
            "level": official.get("level"),
            "status": official.get("status"),
            "facts": official.get("facts") or {},
            "conflicts": official.get("conflicts") or [],
            "missing": official.get("missing") or [],
            "observed_at": official.get("observed_at") or {},
        },
    }


def _diagnostics(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    official = snapshot.get("official_enrichment") or {}

    def slim(items, keys):
        return [{k: i.get(k) for k in keys if i.get(k) is not None} for i in items or []][:40]

    return {
        "enrichment_status": official.get("status"),
        "error_code": official.get("error_code"),
        "provider_diagnostics": official.get("provider_diagnostics"),
        "refreshed_groups": official.get("refreshed_groups"),
        "stale_fields": official.get("stale_fields") or [],
        "rejected_claims": slim(official.get("rejected_claims"), ("field", "reason", "host", "source_url", "raw_value", "raw_unit")),
        "model_generic_claims": slim(official.get("model_generic_claims"), ("field", "host", "source_url", "raw_value", "raw_unit")),
        "superseded_claims": slim(official.get("superseded_claims"), ("field", "reason", "host", "source_url", "normalized_value")),
        "conflicts": official.get("conflicts") or [],
        "government_conflicts": slim(official.get("government_conflicts"), ("field", "level15_value", "normalized_value", "source_url")),
        "extra_official_equipment": official.get("extra_official_equipment") or [],
        "ignored_grounding_hosts": official.get("ignored_grounding_hosts") or [],
    }


def _sources(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    by_url: Dict[str, Dict[str, Any]] = {}
    for key, fact in ((snapshot.get("official_enrichment") or {}).get("facts") or {}).items():
        for src in fact.get("sources") or []:
            entry = by_url.setdefault(src["source_url"], {**src, "fields": []})
            entry["fields"].append(key)
    return list(by_url.values())


def _decision_trace(profile, constraints, plan, jev_run, pairwise, composition) -> Dict[str, Any]:
    """Everything needed to audit one decision later (owner/debug only; no secrets)."""
    answers = jev_run.get("answers") or {}

    def scores(kind):
        return {qid: (answers.get(qid) or {}).get("score") for qid, spec in plan.specs.items() if spec.kind == kind}

    return {
        "buyer_profile_schema": BUYER_PROFILE_VERSION,
        "normalized_buyer_profile": profile,
        "hard_constraints": constraints,
        "jev_state": plan.state,
        "jev_question_specs": [spec.to_trace() for spec in plan.specs.values()],
        "jev_skipped_questions": plan.skipped,
        "jev_answers": answers,
        "jev_unexpected_answer_ids": jev_run.get("unexpected_answer_ids") or [],
        "pairwise_direction": {pk: {g: v["direction"] for g, v in p["groups"].items()} for pk, p in pairwise.items()},
        "materiality_scores": scores(KIND_MATERIALITY),
        "contextual_fit_scores": scores(KIND_FIT),
        "category_contributions": {
            pk: {d: p["dimensions"][d]["contribution"] for d in DIMENSIONS} for pk, p in composition.get("pairs", {}).items()
        },
        "effective_weight_coverage": {pk: p["effective_weight_coverage"] for pk, p in composition.get("pairs", {}).items()},
        "overall_composition": composition,
        "provider_model": {"requested": jev_run.get("requested_model"), "response": jev_run.get("response_model")},
        "usage": jev_run.get("usage"),
        "duration_ms": jev_run.get("duration_ms"),
    }


# ---------------------------------------------------------------------------
# main generator
# ---------------------------------------------------------------------------
def run_comparison_v2(
    data: Dict[str, Any],
    deps: PipelineDeps,
    *,
    user_id: Optional[int] = None,
    session_id: Optional[str] = None,
    buyer_profile: Optional[Dict[str, Any]] = None,
    request_id: Optional[str] = None,
) -> Iterator[Dict[str, Any]]:
    started = time.perf_counter()
    timings: Dict[str, int] = {}
    _log("comparison_v2_started", request_id=request_id, cars=len(data.get("cars") or []) if isinstance(data, dict) else 0)

    yield _progress("resolving_vehicles")
    records, err = validate_v2_request(data, deps.catalog)
    if err:
        yield err
        return
    slots = list(SLOT_KEYS[: len(records)])
    keys = [r["variant_identity_key"] for r in records]

    yield _progress("loading_government_data")
    snapshots = {slot: build_level15_snapshot(rec, slot) for slot, rec in zip(slots, records)}
    families = [snapshots[s]["derived"]["powertrain_family"] for s in slots]
    try:
        profile = normalize_buyer_profile(buyer_profile, has_plugin_vehicle=any(f in ("ev", "phev") for f in families))
    except BuyerProfileError as exc:
        yield _error(400, "invalid_buyer_profile", _profile_error_message(exc))
        return
    request_hash = compute_request_hash(keys, profile, deps.provider_meta)

    if deps.history is not None:
        try:
            cached = deps.history.find_cached(request_hash)
        except Exception:
            logger.warning("comparison_v2 result_cache_lookup_failed", exc_info=True)
            cached = None
        if cached:
            response = dict(cached["response"])
            response["cached"] = True
            try:
                response["comparison_id"] = deps.history.save_copy(user_id, session_id, cached, request_hash)
            except Exception:
                logger.warning("comparison_v2 result_cache_copy_failed", exc_info=True)
            _log("comparison_v2_completed", request_id=request_id, cached=True, remote_calls=0,
                 total_ms=int((time.perf_counter() - started) * 1000))
            yield _progress("complete")
            yield {"type": "result", "status": 200, "data": response}
            return

    # --- official enrichment (one remote call per car at most) -----------
    t0 = time.perf_counter()
    snapshot_list = [snapshots[s] for s in slots]
    remote_calls = 0
    if isinstance(deps.enrichment, LiveOfficialEnrichmentRepository):
        plans = [deps.enrichment.plan(s) for s in snapshot_list]
        for idx, (slot, (_, groups)) in enumerate(zip(slots, plans)):
            mode = "live" if groups else "cache"
            yield _progress(f"enriching_{slot}", mode=mode)
            event = "vehicle_enrichment_started" if groups else "vehicle_enrichment_cache_hit"
            _log(event, request_id=request_id, slot=slot, vehicle=snapshot_list[idx]["vehicle_id"][:12], groups=list(groups),
                 model=deps.enrichment.provider.model_id)
        outcomes = enrich_many(deps.enrichment, snapshot_list, max_workers=deps.enrichment_concurrency)
    else:
        outcomes = []
        for slot, snap in zip(slots, snapshot_list):
            yield _progress(f"enriching_{slot}", mode="repository")
            outcomes.append((deps.enrichment.get_or_enrich(snap), {"remote_call": None}))
    yield _progress("validating_sources")
    for slot, (outcome, meta) in zip(slots, outcomes):
        if meta.get("remote_call"):
            remote_calls += 1
        snapshots[slot]["official_enrichment"] = outcome
        facts = outcome.get("facts") or {}
        _log(
            "vehicle_enrichment_completed",
            request_id=request_id,
            slot=slot,
            status=outcome.get("status"),
            remote_call=meta.get("remote_call"),
            accepted=len(facts),
            rejected=len(outcome.get("rejected_claims") or []),
            conflicts=len(outcome.get("conflicts") or []),
            grounded_sources=meta.get("grounded_source_count"),
            grounding=meta.get("grounding"),
            parse_source=meta.get("parse_source"),
            model_generic=len(outcome.get("model_generic_claims") or []),
            duration_ms=meta.get("duration_ms"),
            error=outcome.get("error_code"),
        )
    timings["enrichment_ms"] = int((time.perf_counter() - t0) * 1000)

    # --- deterministic comparison ---------------------------------------
    yield _progress("comparing_facts")
    t0 = time.perf_counter()
    comparison = run_deterministic_comparison(snapshots)
    for slot in slots:
        snapshots[slot]["coverage"] = comparison["coverage"][slot]
    timings["deterministic_ms"] = int((time.perf_counter() - t0) * 1000)
    _log("deterministic_compare_completed", request_id=request_id, duration_ms=timings["deterministic_ms"],
         ready=[c for c, e in comparison["categories"].items() if e["status"] == "ready"])

    # --- requirements, pairwise evidence, JEV micro-judgments --------------
    yield _progress("evaluating_decision")
    t0 = time.perf_counter()
    constraints = HardConstraintEvaluator().evaluate(profile, snapshots)
    pairwise = build_pairwise_evidence(snapshots)
    weights = dimension_weights(profile, families)
    plan = JevJudgmentRegistry().build(profile, snapshots, pairwise, constraints, weights)
    questions = {qid: spec.to_question() for qid, spec in plan.specs.items()}
    jev_calls_before = deps.jev_client.calls if deps.jev_client is not None else 0
    jev_run = run_system_one(deps.jev_client, plan.request_body, questions)
    if jev_run["status"] == "failed" and deps.jev_client is None:
        jev_run["reason"] = deps.jev_unavailable_reason or jev_run["reason"]
    timings["jev_ms"] = int((time.perf_counter() - t0) * 1000)
    _log("jev_completed" if jev_run["status"] != "failed" else "jev_failed", request_id=request_id,
         status=jev_run["status"], reason=jev_run.get("reason"), questions=len(questions),
         usable=jev_run.get("usable_answers"), requested_model=jev_run.get("requested_model"),
         response_model=jev_run.get("response_model"), usage=jev_run.get("usage"), duration_ms=timings["jev_ms"])

    # --- deterministic composition (code decides) ----------------------------
    composition = DecisionComposer(profile, snapshots, pairwise, constraints, plan.specs, jev_run, weights).compose()
    names = {slot: snapshots[slot]["identity"]["display_name"] for slot in slots}
    answers = jev_run.get("answers") or {}
    cards = build_category_cards(comparison, composition, profile, snapshots, pairwise, answers)
    reasons = strongest_reasons(composition)
    recommendation = recommendation_view(composition, profile, names)
    recommendation["reasons_he"] = reason_texts(reasons, composition, profile, names)
    notes = constraint_notes(constraints, names)
    limitations = limitations_for(comparison)
    profile_summary = profile_summary_he(profile)

    # --- summary (one call) ----------------------------------------------
    yield _progress("writing_summary")
    cars = {slot: _car_card(snapshots[slot]) for slot in slots}
    summary_calls_before = deps.summary_writer.calls if deps.summary_writer is not None else 0
    summary_payload = build_summary_payload(cars, recommendation, recommendation["reasons_he"], cards, notes, profile_summary, limitations)
    summary = produce_summary(deps.summary_writer, cars, summary_payload)
    _log("summary_completed" if summary["source"] == "gemini" else "summary_failed", request_id=request_id,
         source=summary["source"], reason=summary.get("reason"), duration_ms=summary.get("duration_ms"))

    response = {
        "engine_version": ENGINE_VERSION,
        "cached": False,
        "comparison_id": None,
        "cars": cars,
        "cars_selected": {slot: {"display_name": c["display_name"], **c} for slot, c in cars.items()},
        "cars_selected_list": [
            {"make": c["make"], "model": c["model"], "year": c["year"], "trim": c["trim"], "variant_identity_key": c["variant_identity_key"]}
            for c in cars.values()
        ],
        "vehicle_snapshots": {slot: _response_snapshot(snapshots[slot]) for slot in slots},
        "buyer_profile": profile,
        "profile_summary": profile_summary,
        "unsupported_concepts": [{"key": k, "label_he": UNSUPPORTED_LABELS_HE[k]} for k in UNSUPPORTED_CONCEPTS],
        "recommendation": recommendation,
        "hard_constraints": {**constraints, "notes": notes},
        "categories": cards,
        "category_order": list(CATEGORIES),
        "coverage": comparison["coverage"],
        "summary": summary["text"],
        "summary_source": summary["source"],
        "sources": {slot: _sources(snapshots[slot]) for slot in slots},
        "diagnostics": {slot: _diagnostics(snapshots[slot]) for slot in slots},
        "limitations": limitations,
        "model_certainty": {"label_he": MODEL_CERTAINTY_LABEL_HE, "note_he": MODEL_CERTAINTY_NOTE_HE},
        "decision": {
            "provider": "typesafe",
            "status": jev_run["status"],
            "reason": jev_run.get("reason"),
            "requested_model": jev_run.get("requested_model"),
            "response_model": jev_run.get("response_model"),
            "usage": jev_run.get("usage"),
            "question_count": jev_run["question_count"],
            "usable_answers": jev_run.get("usable_answers", 0),
            "primitives": sorted({q["type"] for q in questions.values()}),
            "composition": "deterministic",
        },
        "decision_trace": _decision_trace(profile, constraints, plan, jev_run, pairwise, composition),
        "provider_meta": {
            **deps.provider_meta,
            "decision_provider": "typesafe",
            "jev_response_model": jev_run.get("response_model"),
            "remote_enrichment_calls": remote_calls,
            "jev_calls": (deps.jev_client.calls - jev_calls_before) if deps.jev_client is not None else 0,
            "summary_calls": (deps.summary_writer.calls - summary_calls_before) if deps.summary_writer is not None else 0,
        },
        "timings": timings,
    }
    total_ms = int((time.perf_counter() - started) * 1000)
    response["timings"]["total_ms"] = total_ms

    cacheable = (
        composition["outcome"] != DECISION_UNAVAILABLE
        and all((snapshots[s]["official_enrichment"].get("status") in ("enriched", "refreshed", "cache_hit")) for s in slots)
    )
    if deps.history is not None:
        try:
            response["comparison_id"] = deps.history.save(user_id, session_id, response, request_hash, total_ms, cacheable)
        except Exception:
            logger.warning("comparison_v2 history_save_failed request_id=%s", request_id, exc_info=True)

    _log("comparison_v2_completed", request_id=request_id, cached=False, remote_enrichment_calls=remote_calls,
         outcome=composition["outcome"], questions=len(questions), summary_source=summary["source"], total_ms=total_ms, **{k: v for k, v in timings.items() if k != "total_ms"})
    yield _progress("complete")
    yield {"type": "result", "status": 200, "data": response}


def collect_result(events: Iterator[Dict[str, Any]]) -> Dict[str, Any]:
    """Consume a pipeline run; return the final result/error event plus progress."""
    progress: List[str] = []
    final: Dict[str, Any] = {}
    for event in events:
        if event["type"] == "progress":
            progress.append(event["stage"])
        else:
            final = event
    final["progress"] = progress
    return final


# ---------------------------------------------------------------------------
# history store (whole-comparison cache)
# ---------------------------------------------------------------------------
class ComparisonV2HistoryStore:
    """Persists V2 results in ``comparison_history`` without breaking legacy rows."""

    def __init__(self, ttl_hours: Optional[float] = None, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc).replace(tzinfo=None)):
        self.ttl_hours = ttl_hours if ttl_hours is not None else float(os.environ.get("COMPARISON_V2_RESULT_TTL_HOURS", "24"))
        self.now = now

    def find_cached(self, request_hash: str) -> Optional[Dict[str, Any]]:
        from app.models import ComparisonHistory

        cutoff = self.now() - timedelta(hours=self.ttl_hours)
        rows = (
            ComparisonHistory.query.filter(
                ComparisonHistory.request_hash == request_hash,
                ComparisonHistory.prompt_version == ENGINE_VERSION,
                ComparisonHistory.created_at >= cutoff,
            )
            .order_by(ComparisonHistory.created_at.desc())
            .limit(5)
            .all()
        )
        for row in rows:
            computed = _load_json(row.computed_result)
            if isinstance(computed, dict) and computed.get("engine_version") == ENGINE_VERSION and computed.get("cacheable"):
                return {"response": computed["response"], "cars": _load_json(row.cars_selected), "row_id": row.id}
        return None

    def _write(self, user_id, session_id, cars_list, response, request_hash, duration_ms, cacheable) -> int:
        from app.extensions import db
        from app.models import ComparisonHistory

        stored = {
            "engine_version": ENGINE_VERSION,
            "cacheable": bool(cacheable),
            "overall_winner": None,
            "response": {k: v for k, v in response.items() if k not in ("comparison_id", "cached")},
        }
        record = ComparisonHistory(
            created_at=self.now(),
            user_id=user_id,
            session_id=session_id,
            cars_selected=json.dumps(cars_list, ensure_ascii=False),
            model_json_raw=json.dumps({"engine_version": ENGINE_VERSION}, ensure_ascii=False),
            computed_result=json.dumps(stored, ensure_ascii=False),
            sources_index=json.dumps(response.get("sources") or {}, ensure_ascii=False),
            model_name=", ".join(
                filter(None, [response.get("provider_meta", {}).get("enrichment_model"), response.get("provider_meta", {}).get("summary_model")])
            )[:64] or ENGINE_VERSION,
            grounding_enabled=True,
            prompt_version=ENGINE_VERSION,
            request_hash=request_hash,
            duration_ms=duration_ms,
        )
        db.session.add(record)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        return record.id

    def save(self, user_id, session_id, response, request_hash, duration_ms, cacheable) -> int:
        return self._write(user_id, session_id, response.get("cars_selected_list") or [], response, request_hash, duration_ms, cacheable)

    def save_copy(self, user_id, session_id, cached, request_hash) -> int:
        response = cached["response"]
        return self._write(user_id, session_id, response.get("cars_selected_list") or [], response, request_hash, 0, True)


def _load_json(value: Any) -> Any:
    """Tolerates the double-encoded JSON text that JSONEncodedText stores on Postgres."""
    from app.services.comparison.cache import _safe_json_obj

    return _safe_json_obj(value, default=None)


# ---------------------------------------------------------------------------
# default wiring
# ---------------------------------------------------------------------------
def offline_mode() -> bool:
    return (os.environ.get("COMPARISON_V2_OFFLINE_MODE") or "false").strip().lower() in ("1", "true", "yes", "on")


def build_default_deps(ai_client: Any = None) -> PipelineDeps:
    from app.services.comparison.model_config import comparison_enrichment_model_id, comparison_summary_model_id
    from app.services.comparison_v2.cache import DatabaseEnrichmentCache, LayeredEnrichmentCache, process_cache
    from app.services.comparison_v2.demo_catalog import DemoVehicleCatalogRepository
    from app.services.comparison_v2.jev_client import jev_model_configured
    from app.services.comparison_v2.official_enrichment import GeminiOfficialEnrichmentProvider, OfflineEnrichmentProvider

    offline = offline_mode()
    if offline:
        provider = OfflineEnrichmentProvider()
    else:
        provider = GeminiOfficialEnrichmentProvider(ai_client, model_id=comparison_enrichment_model_id())
    cache = LayeredEnrichmentCache(process_cache(), DatabaseEnrichmentCache(ENRICHMENT_CONTRACT_VERSION, SOURCE_REGISTRY_VERSION))
    jev_client = None
    jev_reason = None
    if offline:
        jev_reason = "offline_mode"
    elif os.environ.get("TYPESAFE_API_KEY"):
        jev_client = TypeSafeJevClient()
    else:
        jev_reason = "jev_not_configured"
    writer = None if offline else GeminiSummaryWriter(ai_client, model_id=comparison_summary_model_id())
    return PipelineDeps(
        catalog=DemoVehicleCatalogRepository(),
        enrichment=LiveOfficialEnrichmentRepository(provider, cache),
        jev_client=jev_client,
        summary_writer=writer,
        history=ComparisonV2HistoryStore(),
        enrichment_concurrency=int(os.environ.get("COMPARISON_ENRICHMENT_CONCURRENCY", "3")),
        provider_meta={
            "enrichment_model": None if offline else comparison_enrichment_model_id(),
            "summary_model": None if offline else comparison_summary_model_id(),
            "jev_model": jev_model_configured() or None,
            "offline_mode": offline,
        },
        jev_unavailable_reason=jev_reason,
    )
