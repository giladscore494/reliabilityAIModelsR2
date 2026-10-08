# -*- coding: utf-8 -*-
"""Comparison V3 orchestration (``comparison-v3/1``).

validate request (variant keys + optional asking prices) -> TRIPY facts (one call; unavailable ->
``facts_unavailable``, never demo or cached data) -> canonical snapshots (``canonical-vehicle-snapshot/2``) ->
buyer profile (``buyer-profile/3``; a budget needs a price for every car) -> whole-comparison cache -> the row rule
(``metrics.comparable_rows``) -> pairwise evidence + hard constraints -> ONE JEV call (narrow score questions) ->
composition in code -> section texts -> ONE Gemini call for the row explanations (validated, deterministic
fallback) -> ONE Gemini summary call (validated, deterministic fallback) -> persist -> response.

No grounded enrichment, no web search, no MILO / EEA / EPA / CVS / NRCan / ADEME access from this repository.
``run_comparison_v3`` is a generator of events (NDJSON progress for the route).
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

from app.services.comparison_v2.buyer_profile import BuyerProfileError
from app.services.comparison_v2.jev_client import TypeSafeJevClient, run_system_one
from app.services.comparison_v2.summary_writer import GeminiSummaryWriter, build_summary_payload, produce_summary
from app.services.comparison_v3.buyer_profile import BUYER_PROFILE_VERSION, normalize_buyer_profile, profile_summary_he
from app.services.comparison_v3.composer import DecisionComposer, strongest_reasons
from app.services.comparison_v3.contracts import (
    ASKING_PRICE_MAX,
    ASKING_PRICE_MIN,
    BUDGET_NEEDS_PRICES_HE,
    DECISION_UNAVAILABLE,
    ENGINE_VERSION,
    FACTS_UNAVAILABLE,
    FACTS_UNAVAILABLE_HE,
    MAX_CARS,
    MIN_CARS,
    PROGRESS_LABELS_HE,
    SLOT_KEYS,
    SNAPSHOT_CONTRACT_VERSION,
)
from app.services.comparison_v3.engine import DIMENSIONS, build_pairwise_evidence, dimension_weights, evaluate_constraints
from app.services.comparison_v3.explanations import (
    constraint_notes,
    reason_texts,
    recommendation_view,
    section_cards,
)
from app.services.comparison_v3.judgments import KIND_FIT, KIND_MATERIALITY, build_plan
from app.services.comparison_v3.metrics import (
    CATEGORY_ORDER,
    attribution,
    available_dimensions,
    comparable_rows,
    rows_by_category,
)
from app.services.comparison_v3.row_explanations import GeminiRowExplanationWriter, produce_row_explanations
from app.services.comparison_v3.snapshot import build_snapshot
from app.services.comparison_v3.tripy import FactsRepository, TripyUnavailable

logger = logging.getLogger("comparison_v3")

LIMITATIONS_HE = [
    "ההשוואה מבוססת על נתוני משרד התחבורה ועל נתונים פתוחים שעברו התאמה לגרסה המדויקת ב-TRIPY.",
    "מוצגים ומושווים רק נתונים שקיימים לכל הרכבים שנבחרו.",
    "אמינות ארוכת טווח ועלויות אחזקה אינן נכללות בגרסת ההשוואה הזו.",
    "משקלי ההכרעה וספי ההכרעה הם ראשוניים ועדיין לא כוילו על מערך הערכה שלנו.",
]


@dataclass
class PipelineDeps:
    facts: FactsRepository
    jev_client: Optional[TypeSafeJevClient]
    summary_writer: Optional[GeminiSummaryWriter]
    explanation_writer: Optional[GeminiRowExplanationWriter]
    history: Optional[Any] = None
    provider_meta: Dict[str, Any] = field(default_factory=dict)
    jev_unavailable_reason: Optional[str] = None


def _log(event: str, **fields: Any) -> None:
    logger.info("%s %s", event, json.dumps(fields, ensure_ascii=False, sort_keys=True, default=str))


def _progress(stage: str) -> Dict[str, Any]:
    return {"type": "progress", "stage": stage, "label_he": PROGRESS_LABELS_HE.get(stage, stage)}


def _error(status: int, code: str, message: str, **extra: Any) -> Dict[str, Any]:
    return {"type": "error", "status": status, "code": code, "message": message, **extra}


def _is_key(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def validate_request(data: Any):
    """([(key, asking_price_ils | None)], None) or (None, error event)."""
    if not isinstance(data, dict):
        return None, _error(400, "validation_error", "בקשה לא תקינה")
    cars = data.get("cars")
    if not isinstance(cars, list) or len(cars) < MIN_CARS:
        return None, _error(400, "validation_error", "יש לבחור לפחות 2 גרסאות רכב להשוואה")
    if len(cars) > MAX_CARS:
        return None, _error(400, "validation_error", "ניתן להשוות עד 3 רכבים בלבד")
    out = []
    for idx, car in enumerate(cars):
        key = car.get("variant_identity_key") if isinstance(car, dict) else None
        if not _is_key(key):
            return None, _error(400, "variant_required", f"רכב {idx + 1}: יש לבחור גרסה מדויקת מהרשימה")
        if key in [k for k, _ in out]:
            return None, _error(400, "validation_error", "לא ניתן להשוות גרסה לעצמה. יש לבחור גרסאות שונות.")
        price = car.get("asking_price_ils")
        if price in (None, ""):
            price = None
        else:
            if isinstance(price, bool) or not isinstance(price, (int, float, str)):
                return None, _error(400, "invalid_asking_price", f"רכב {idx + 1}: מחיר מבוקש לא תקין", field=f"cars.{idx}.asking_price_ils")
            try:
                num = float(price)
            except ValueError:
                return None, _error(400, "invalid_asking_price", f"רכב {idx + 1}: מחיר מבוקש לא תקין", field=f"cars.{idx}.asking_price_ils")
            if num != num or not num.is_integer() or not (ASKING_PRICE_MIN <= num <= ASKING_PRICE_MAX):
                return None, _error(400, "invalid_asking_price",
                                    f"רכב {idx + 1}: מחיר מבוקש הוא מספר שלם בין ₪{ASKING_PRICE_MIN:,} ל־₪{ASKING_PRICE_MAX:,}",
                                    field=f"cars.{idx}.asking_price_ils")
            price = int(num)
        out.append((key, price))
    return out, None


def _profile_error_message(exc: BuyerProfileError) -> str:
    text = str(exc)
    if text.startswith("buyer_profile") or text.startswith("mode"):
        return "יש לבחור השוואה מותאמת אליי או השוואה כללית"
    if text.startswith("main_use"):
        return "יש לבחור את השימוש העיקרי ברכב"
    if text.startswith("priorities"):
        return "יש לדרג את החשיבות של כל תחום (0–4)"
    return f"אחד משדות ההתאמה אינו תקין ({text.split(':', 1)[0]})"


def compute_request_hash(cars: List[tuple], profile: Dict[str, Any], versions: Dict[str, Any], provider_meta: Dict[str, Any]) -> str:
    material = {
        "engine": ENGINE_VERSION,
        "snapshot": SNAPSHOT_CONTRACT_VERSION,
        "keys": [k for k, _ in cars],
        "asking_prices": [p for _, p in cars],
        "buyer": profile,
        "tripy": {k: versions.get(k) for k in ("contract", "admission", "snapshots_sha", "matcher", "zero_semantics")},
        "models": {k: provider_meta.get(k) for k in ("summary_model", "explanation_model", "jev_model")},
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _car_card(snapshot: Dict[str, Any], price: Optional[int]) -> Dict[str, Any]:
    identity = snapshot["identity"]
    card = {
        "variant_identity_key": identity.get("variant_identity_key"),
        "display_name": identity.get("display_name"),
        "make": identity.get("make_display"),
        "model": identity.get("model_display"),
        "government_model": identity.get("model"),
        "year": identity.get("model_year"),
        "trim": identity.get("trim"),
        "official_model_code": identity.get("official_model_code"),
        "powertrain_family": snapshot["derived"]["powertrain_family"],
    }
    if price is not None:
        card["asking_price_ils"] = price
        card["asking_price_text"] = f"₪{price:,}"
    return {k: v for k, v in card.items() if v not in (None, "")}


def build_table(slots: List[str], cars: Dict[str, Dict[str, Any]], rows: List[Dict[str, Any]],
                cards: Dict[str, Dict[str, Any]], explanations: Dict[str, Any]) -> Dict[str, Any]:
    by_cat = rows_by_category(rows)
    sections = []
    for cat in CATEGORY_ORDER:
        if cat not in cards:
            continue
        card = cards[cat]
        out_rows = []
        for row in by_cat[cat]:
            leader = row.get("leader")
            cells = {}
            for slot in slots:
                cell = row["cells"][slot]
                entry = {"text": cell["text"], "leader": bool(leader == slot and not row["display_only"])}
                for k in ("source", "standard", "definition", "attribution", "licence", "details"):
                    if cell.get(k) not in (None, "", [], {}) and not (k == "standard" and str(cell[k]).startswith("stated:")):
                        entry[k] = cell[k]
                cells[slot] = entry
            item = {"row_id": row["row_id"], "label_he": row["label_he"], "cells": cells,
                    "display_only": row["display_only"], "history": row["history"],
                    "explanation_he": explanations["texts"][row["row_id"]],
                    "explanation_source": explanations["sources"][row["row_id"]]}
            if row.get("unit_he"):
                item["unit_he"] = row["unit_he"]
            if row.get("standard") and not str(row["standard"]).startswith("stated:"):
                item["standard"] = row["standard"]
            if not row["display_only"]:
                item["leader"] = leader
            out_rows.append(item)
        section = {"key": cat, "label_he": card["label_he"], "influence_he": card["influence_he"],
                   "influence_status": card["influence_status"], "rows": out_rows}
        if card.get("favoured_slot"):
            section["favoured_slot"] = card["favoured_slot"]
        sections.append(section)
    sources = attribution(rows)
    table = {"slots": slots, "cars": {s: cars[s] for s in slots}, "sections": sections, "attribution": sources}
    if sources:
        table["attribution_he"] = "מקורות: " + "; ".join(sources)
    return table


def _decision_trace(profile, constraints, plan, jev_run, pairwise, composition, rows) -> Dict[str, Any]:
    answers = jev_run.get("answers") or {}

    def scores(kind):
        return {qid: (answers.get(qid) or {}).get("score") for qid, spec in plan.specs.items() if spec.kind == kind}

    return {
        "buyer_profile_schema": BUYER_PROFILE_VERSION,
        "normalized_buyer_profile": profile,
        "rows": [{k: r[k] for k in ("row_id", "category", "values", "leader", "standard", "display_only")} for r in rows],
        "hard_constraints": constraints,
        "jev_state": plan.state,
        "jev_question_specs": [spec.to_trace() for spec in plan.specs.values()],
        "jev_skipped_questions": plan.skipped,
        "jev_answers": answers,
        "pairwise_direction": {pk: {g: v["direction"] for g, v in p["groups"].items()} for pk, p in pairwise.items()},
        "materiality_scores": scores(KIND_MATERIALITY),
        "contextual_fit_scores": scores(KIND_FIT),
        "category_contributions": {pk: {d: p["dimensions"][d]["contribution"] for d in DIMENSIONS}
                                   for pk, p in composition.get("pairs", {}).items()},
        "overall_composition": composition,
        "provider_model": {"requested": jev_run.get("requested_model"), "response": jev_run.get("response_model")},
        "usage": jev_run.get("usage"),
        "duration_ms": jev_run.get("duration_ms"),
    }


def run_comparison_v3(data: Dict[str, Any], deps: PipelineDeps, *, user_id: Optional[int] = None,
                      session_id: Optional[str] = None, request_id: Optional[str] = None) -> Iterator[Dict[str, Any]]:
    started = time.perf_counter()
    timings: Dict[str, int] = {}
    yield _progress("resolving_vehicles")
    cars_req, err = validate_request(data)
    if err:
        yield err
        return
    slots = list(SLOT_KEYS[: len(cars_req)])
    keys = [k for k, _ in cars_req]

    yield _progress("loading_facts")
    t0 = time.perf_counter()
    try:
        fetched = deps.facts.get_records(keys)
    except TripyUnavailable as exc:
        _log("comparison_v3_facts_unavailable", request_id=request_id, reason=str(exc))
        yield _error(503, FACTS_UNAVAILABLE, FACTS_UNAVAILABLE_HE)
        return
    timings["facts_ms"] = int((time.perf_counter() - t0) * 1000)
    records = fetched["records"]
    versions = fetched.get("versions") or {}
    for idx, key in enumerate(keys):
        rec = records.get(key)
        if not rec or rec.get("status") != "ok":
            yield _error(404, "variant_not_found", f"רכב {idx + 1}: הגרסה לא נמצאה בקטלוג")
            return
    prices = {slot: price for slot, (_, price) in zip(slots, cars_req)}
    snapshots = {slot: build_snapshot(records[key], slot, prices[slot]) for slot, key in zip(slots, keys)}
    families = [snapshots[s]["derived"]["powertrain_family"] for s in slots]
    try:
        profile = normalize_buyer_profile(data.get("buyer_profile"), has_plugin_vehicle=any(f in ("ev", "phev") for f in families))
    except BuyerProfileError as exc:
        yield _error(400, "invalid_buyer_profile", _profile_error_message(exc))
        return
    if profile.get("budget_max_ils") and any(prices[s] is None for s in slots):
        yield _error(400, "invalid_buyer_profile", BUDGET_NEEDS_PRICES_HE, field="budget_max_ils",
                     field_errors={"budget_max_ils": BUDGET_NEEDS_PRICES_HE})
        return
    request_hash = compute_request_hash(cars_req, profile, versions, deps.provider_meta)

    if deps.history is not None:
        try:
            cached = deps.history.find_cached(request_hash)
        except Exception:  # noqa: BLE001
            logger.warning("comparison_v3 result_cache_lookup_failed", exc_info=True)
            cached = None
        if cached:
            response = dict(cached["response"])
            response["cached"] = True
            try:
                response["comparison_id"] = deps.history.save_copy(user_id, session_id, cached, request_hash)
            except Exception:  # noqa: BLE001
                logger.warning("comparison_v3 result_cache_copy_failed", exc_info=True)
            _log("comparison_v3_completed", request_id=request_id, cached=True, remote_calls=1)
            yield _progress("complete")
            yield {"type": "result", "status": 200, "data": response}
            return

    yield _progress("comparing_facts")
    rows = comparable_rows(snapshots)
    evidence_dims = available_dimensions(rows)
    constraints = evaluate_constraints(profile, snapshots)
    pairwise = build_pairwise_evidence(rows, slots)

    yield _progress("evaluating_decision")
    t0 = time.perf_counter()
    weights = dimension_weights(profile, families)
    plan = build_plan(profile, snapshots, rows, pairwise, constraints, weights)
    questions = {qid: spec.to_question() for qid, spec in plan.specs.items()}
    jev_calls_before = deps.jev_client.calls if deps.jev_client is not None else 0
    jev_run = run_system_one(deps.jev_client, plan.request_body, questions)
    if jev_run["status"] == "failed" and deps.jev_client is None:
        jev_run["reason"] = deps.jev_unavailable_reason or jev_run["reason"]
    timings["jev_ms"] = int((time.perf_counter() - t0) * 1000)
    composition = DecisionComposer(profile, snapshots, pairwise, constraints, plan.specs, jev_run, weights, evidence_dims).compose()
    names = {slot: snapshots[slot]["identity"].get("display_name") or slot for slot in slots}
    cards = section_cards(rows_by_category(rows), composition, profile, names)
    reasons = strongest_reasons(composition)
    recommendation = recommendation_view(composition, profile, names)
    recommendation["reasons_he"] = reason_texts(reasons, composition, profile, names)
    notes = constraint_notes(constraints, names)
    profile_summary = profile_summary_he(profile, evidence_dims)
    cars = {slot: _car_card(snapshots[slot], prices[slot]) for slot in slots}

    yield _progress("writing_explanations")
    explanations = produce_row_explanations(deps.explanation_writer, rows, names, profile_summary["headline"])

    yield _progress("writing_summary")
    summary_calls_before = deps.summary_writer.calls if deps.summary_writer is not None else 0
    summary_cards = {k: c for k, c in cards.items() if c["dimension"]}
    summary_payload = build_summary_payload(cars, recommendation, recommendation["reasons_he"], summary_cards, notes,
                                            profile_summary, LIMITATIONS_HE)
    summary = produce_summary(deps.summary_writer, cars, summary_payload)

    table = build_table(slots, cars, rows, cards, explanations)
    response = {
        "engine_version": ENGINE_VERSION,
        "cached": False,
        "comparison_id": None,
        "cars": cars,
        "cars_selected": {slot: {"display_name": c.get("display_name"), **c} for slot, c in cars.items()},
        "cars_selected_list": [{"make": c.get("make"), "model": c.get("model"), "year": c.get("year"), "trim": c.get("trim"),
                                "variant_identity_key": c.get("variant_identity_key"),
                                **({"asking_price_ils": c["asking_price_ils"]} if c.get("asking_price_ils") else {})}
                               for c in cars.values()],
        "buyer_profile": profile,
        "profile_summary": profile_summary,
        "available_dimensions": evidence_dims,
        "recommendation": recommendation,
        "hard_constraints": {**constraints, "notes": notes},
        "table": table,
        "summary": summary["text"],
        "summary_source": summary["source"],
        "limitations": LIMITATIONS_HE,
        "tripy_versions": versions,
        "decision": {"provider": "typesafe", "status": jev_run["status"], "reason": jev_run.get("reason"),
                     "requested_model": jev_run.get("requested_model"), "response_model": jev_run.get("response_model"),
                     "question_count": jev_run["question_count"], "usable_answers": jev_run.get("usable_answers", 0),
                     "composition": "deterministic"},
        "explanations": {"model": explanations.get("model"), "rejected": explanations.get("rejected") or [],
                         "reason": explanations.get("reason")},
        "decision_trace": _decision_trace(profile, constraints, plan, jev_run, pairwise, composition, rows),
        "provider_meta": {
            **deps.provider_meta,
            "facts_provider": "tripy",
            "jev_calls": (deps.jev_client.calls - jev_calls_before) if deps.jev_client is not None else 0,
            "summary_calls": (deps.summary_writer.calls - summary_calls_before) if deps.summary_writer is not None else 0,
            "explanation_calls": explanations.get("calls", 0),
        },
        "timings": timings,
    }
    total_ms = int((time.perf_counter() - started) * 1000)
    response["timings"]["total_ms"] = total_ms
    cacheable = composition["outcome"] != DECISION_UNAVAILABLE and not explanations.get("reason")
    if deps.history is not None:
        try:
            response["comparison_id"] = deps.history.save(user_id, session_id, response, request_hash, total_ms, cacheable)
        except Exception:  # noqa: BLE001
            logger.warning("comparison_v3 history_save_failed request_id=%s", request_id, exc_info=True)
    _log("comparison_v3_completed", request_id=request_id, cached=False, outcome=composition["outcome"],
         rows=len(rows), questions=len(questions), summary_source=summary["source"], total_ms=total_ms)
    yield _progress("complete")
    yield {"type": "result", "status": 200, "data": response}


def collect_result(events: Iterator[Dict[str, Any]]) -> Dict[str, Any]:
    progress: List[str] = []
    final: Dict[str, Any] = {}
    for event in events:
        if event["type"] == "progress":
            progress.append(event["stage"])
        elif event["type"] != "heartbeat":
            final = event
    final["progress"] = progress
    return final


# ---------------------------------------------------------------------------
# history (whole-comparison cache)
# ---------------------------------------------------------------------------
class ComparisonV3HistoryStore:
    """Persists V3 results in ``comparison_history`` (prompt_version = comparison-v3/1)."""

    def __init__(self, ttl_hours: Optional[float] = None,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc).replace(tzinfo=None)):
        self.ttl_hours = ttl_hours if ttl_hours is not None else float(os.environ.get("COMPARISON_V2_RESULT_TTL_HOURS", "24"))
        self.now = now

    def find_cached(self, request_hash: str) -> Optional[Dict[str, Any]]:
        from app.models import ComparisonHistory

        cutoff = self.now() - timedelta(hours=self.ttl_hours)
        rows = (ComparisonHistory.query.filter(ComparisonHistory.request_hash == request_hash,
                                               ComparisonHistory.prompt_version == ENGINE_VERSION,
                                               ComparisonHistory.created_at >= cutoff)
                .order_by(ComparisonHistory.created_at.desc()).limit(5).all())
        for row in rows:
            computed = _load_json(row.computed_result)
            if isinstance(computed, dict) and computed.get("engine_version") == ENGINE_VERSION and computed.get("cacheable"):
                return {"response": computed["response"], "cars": _load_json(row.cars_selected), "row_id": row.id}
        return None

    def _write(self, user_id, session_id, response, request_hash, duration_ms, cacheable) -> int:
        from app.extensions import db
        from app.models import ComparisonHistory

        stored = {"engine_version": ENGINE_VERSION, "cacheable": bool(cacheable), "overall_winner": None,
                  "response": {k: v for k, v in response.items() if k not in ("comparison_id", "cached")}}
        record = ComparisonHistory(
            created_at=self.now(), user_id=user_id, session_id=session_id,
            cars_selected=json.dumps(response.get("cars_selected_list") or [], ensure_ascii=False),
            model_json_raw=json.dumps({"engine_version": ENGINE_VERSION}, ensure_ascii=False),
            computed_result=json.dumps(stored, ensure_ascii=False),
            sources_index=json.dumps({"attribution": (response.get("table") or {}).get("attribution") or []}, ensure_ascii=False),
            model_name=(response.get("provider_meta", {}).get("summary_model") or ENGINE_VERSION)[:64],
            grounding_enabled=False, prompt_version=ENGINE_VERSION, request_hash=request_hash, duration_ms=duration_ms,
        )
        db.session.add(record)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        return record.id

    def save(self, user_id, session_id, response, request_hash, duration_ms, cacheable) -> int:
        return self._write(user_id, session_id, response, request_hash, duration_ms, cacheable)

    def save_copy(self, user_id, session_id, cached, request_hash) -> int:
        return self._write(user_id, session_id, cached["response"], request_hash, 0, False)


def _load_json(value: Any) -> Any:
    from app.services.comparison.cache import _safe_json_obj

    return _safe_json_obj(value, default=None)


# ---------------------------------------------------------------------------
# default wiring (code configuration; TRIPY_BASE_URL / TRIPY_FACTS_TOKEN are Render secrets)
# ---------------------------------------------------------------------------
def offline_mode() -> bool:
    return (os.environ.get("COMPARISON_V2_OFFLINE_MODE") or "false").strip().lower() in ("1", "true", "yes", "on")


def build_default_deps(ai_client: Any = None) -> PipelineDeps:
    from app.services.comparison.model_config import DEFAULT_COMPARISON_V2_MODEL_ID, comparison_summary_model_id
    from app.services.comparison_v2.jev_client import jev_model_configured
    from app.services.comparison_v3.tripy import DemoFactsRepository, TripyClient, TripyFactsRepository

    offline = offline_mode()
    jev_client, jev_reason = None, None
    if offline:
        jev_reason = "offline_mode"
    elif os.environ.get("TYPESAFE_API_KEY"):
        jev_client = TypeSafeJevClient()
    else:
        jev_reason = "jev_not_configured"
    return PipelineDeps(
        facts=DemoFactsRepository() if offline else TripyFactsRepository(TripyClient()),
        jev_client=jev_client,
        summary_writer=None if offline else GeminiSummaryWriter(ai_client, model_id=comparison_summary_model_id()),
        explanation_writer=None if offline else GeminiRowExplanationWriter(ai_client, model_id=DEFAULT_COMPARISON_V2_MODEL_ID),
        history=ComparisonV3HistoryStore(),
        provider_meta={"summary_model": None if offline else comparison_summary_model_id(),
                       "explanation_model": None if offline else DEFAULT_COMPARISON_V2_MODEL_ID,
                       "jev_model": jev_model_configured() or None, "offline_mode": offline},
        jev_unavailable_reason=jev_reason,
    )


def build_default_catalog():
    from app.services.comparison_v3.tripy import DemoCatalogRepository, TripyCatalogRepository, TripyClient

    return DemoCatalogRepository() if offline_mode() else TripyCatalogRepository(TripyClient())
