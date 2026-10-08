# -*- coding: utf-8 -*-
"""Comparison routes blueprint for Car Comparison feature."""

import json
import re
from datetime import datetime, timedelta
from flask import Blueprint, Response, render_template, request, current_app, session, make_response, stream_with_context
from flask_login import current_user, login_required

from app.services.vehicle_catalog_service import get_vehicle_catalog_ui_data, get_flat_vehicle_catalog
from app.factory import USER_DAILY_LIMIT
from app.quota import (
    check_and_increment_ip_rate_limit,
    get_client_ip,
    log_access_decision,
    resolve_app_timezone,
    compute_quota_window,
    reserve_daily_quota,
    finalize_quota_reservation,
    release_quota_reservation,
    get_daily_quota_usage,
    PER_IP_PER_MIN_LIMIT,
)
from app.legal import (
    TERMS_VERSION,
    PRIVACY_VERSION,
    COMPARE_RESULT_ACK_KEY,
    COMPARE_RESULT_ACK_VERSION,
    has_accepted_feature,
)
from app.models import LegalAcceptance, QuotaReservation
from app.utils.http_helpers import api_error, api_ok, is_owner_user, get_request_id, _utcnow
from app.services import comparison_service
from app.services.gemini_health_verdict import log_product_call_verdict_input
from app.services.comparison.model_config import (
    COMPARISON_ENGINE_V2,
    COMPARISON_ENGINE_V3,
    DEFAULT_COMPARISON_V2_MODEL_ID,
    comparison_engine,
    comparison_enrichment_model_id,
    comparison_stage_a_model_id,
)
from app.services.comparison_v2.buyer_profile import ui_options as comparison_v2_ui_options
from app.services.comparison_v2.demo_catalog import DemoVehicleCatalogRepository
from app.services.comparison_v3.buyer_profile import ui_options as comparison_v3_ui_options
from app.utils.analytics import track_event

bp = Blueprint('comparison', __name__)


@bp.route('/compare')
def compare_page():
    """Render the car comparison page (publicly accessible GET)."""
    if not current_user.is_authenticated:
        return render_template(
            'compare.html',
            user=current_user,
            user_email="",
            is_owner=False,
            car_models_data={},
            compare_catalog_url='/api/compare/catalog',
            legal_accepted=False,
            compare_results_acknowledged=False,
            accepted_terms=False,
            accepted_privacy=False,
            terms_version=current_app.config.get("TERMS_VERSION", TERMS_VERSION),
            privacy_version=current_app.config.get("PRIVACY_VERSION", PRIVACY_VERSION),
            compare_result_ack_key=COMPARE_RESULT_ACK_KEY,
            compare_result_ack_version=COMPARE_RESULT_ACK_VERSION,
            **_v2_template_context(),
        )
    user_email = getattr(current_user, "email", "") if current_user.is_authenticated else ""
    terms_version = current_app.config.get("TERMS_VERSION", TERMS_VERSION)
    privacy_version = current_app.config.get("PRIVACY_VERSION", PRIVACY_VERSION)
    legal_accepted = LegalAcceptance.query.filter_by(
        user_id=current_user.id,
        terms_version=terms_version,
        privacy_version=privacy_version,
    ).first() is not None
    compare_results_acknowledged = has_accepted_feature(
        current_user.id,
        COMPARE_RESULT_ACK_KEY,
        COMPARE_RESULT_ACK_VERSION,
    )
    return render_template(
        'compare.html',
        user=current_user,
        user_email=user_email,
        is_owner=is_owner_user(),
        car_models_data={},
        compare_catalog_url='/api/compare/catalog',
        legal_accepted=legal_accepted,
        compare_results_acknowledged=compare_results_acknowledged,
        accepted_terms=legal_accepted,
        accepted_privacy=legal_accepted,
        terms_version=terms_version,
        privacy_version=privacy_version,
        compare_result_ack_key=COMPARE_RESULT_ACK_KEY,
        compare_result_ack_version=COMPARE_RESULT_ACK_VERSION,
        **_v2_template_context(),
    )


def _v2_template_context():
    """The engine's picker / personalization context (V3 by default; V2 / legacy only when selected in code)."""
    engine = comparison_engine()
    v2 = engine == COMPARISON_ENGINE_V2
    v3 = engine == COMPARISON_ENGINE_V3
    return {
        "comparison_engine": engine,
        "comparison_v3_enabled": v3,
        "comparison_v3_profile_options": comparison_v3_ui_options() if v3 else {},
        "comparison_v2_enabled": v2,
        "comparison_v2_variants": DemoVehicleCatalogRepository().picker_entries() if v2 else [],
        "comparison_v2_profile_options": comparison_v2_ui_options() if v2 else {},
    }


# ---------------------------------------------------------------------------
# Comparison V3: the picker cascade and the slider availability, through TRIPY (the token stays on the server)
# ---------------------------------------------------------------------------
_V3_CATALOG_KINDS = ("manufacturers", "models", "years", "trims")


@bp.route('/api/compare/v3/catalog/<kind>', methods=['GET'])
@login_required
def compare_v3_catalog(kind):
    from app.services.comparison_v3.contracts import FACTS_UNAVAILABLE, FACTS_UNAVAILABLE_HE
    from app.services.comparison_v3.pipeline import build_default_catalog
    from app.services.comparison_v3.tripy import TripyUnavailable

    if kind not in _V3_CATALOG_KINDS:
        return api_error("not_found", "לא נמצא", status=404)
    args = request.args
    manufacturer, model = (args.get("manufacturer") or "").strip(), (args.get("model") or "").strip()
    year = (args.get("year") or "").strip()
    if (kind in ("models", "years", "trims") and not manufacturer) or (kind in ("years", "trims") and not model) \
            or (kind == "trims" and not year.isdigit()):
        return api_error("validation_error", "חסרים פרטי בחירה", status=400)
    catalog = build_default_catalog()
    try:
        if kind == "manufacturers":
            items = catalog.manufacturers()
        elif kind == "models":
            items = catalog.models(manufacturer)
        elif kind == "years":
            items = catalog.years(manufacturer, model)
        else:
            items = catalog.trims(manufacturer, model, int(year))
    except TripyUnavailable:
        return api_error(FACTS_UNAVAILABLE, FACTS_UNAVAILABLE_HE, status=503)
    return api_ok({kind: items})


@bp.route('/api/compare/v3/availability', methods=['POST'])
@login_required
def compare_v3_availability():
    """Which weighted categories have rows for these cars (a slider without rows is hidden) and whether a plug-in
    car is selected. Calls the facts API; no comparison is run."""
    from app.services.comparison_v3.contracts import FACTS_UNAVAILABLE, FACTS_UNAVAILABLE_HE, SLOT_KEYS
    from app.services.comparison_v3.metrics import available_dimensions, comparable_rows
    from app.services.comparison_v3.pipeline import build_default_deps, validate_request
    from app.services.comparison_v3.snapshot import build_snapshot
    from app.services.comparison_v3.tripy import TripyUnavailable

    data = request.get_json(silent=True) or {}
    cars, err = validate_request(data)
    if err:
        return api_error(err["code"], err["message"], status=err["status"])
    deps = build_default_deps(None)
    try:
        records = deps.facts.get_records([k for k, _ in cars])["records"]
    except TripyUnavailable:
        return api_error(FACTS_UNAVAILABLE, FACTS_UNAVAILABLE_HE, status=503)
    snapshots = {}
    for slot, (key, price) in zip(SLOT_KEYS, cars):
        rec = records.get(key)
        if not rec or rec.get("status") != "ok":
            return api_error("variant_not_found", "הגרסה לא נמצאה בקטלוג", status=404)
        snapshots[slot] = build_snapshot(rec, slot, price)
    rows = comparable_rows(snapshots)
    families = sorted({s["derived"]["powertrain_family"] for s in snapshots.values()})
    return api_ok({"available_dimensions": available_dimensions(rows),
                   "categories": sorted({r["category"] for r in rows}),
                   "plugin_selected": any(f in ("ev", "phev") for f in families)})


@bp.route('/api/compare/catalog', methods=['GET'])
def compare_catalog():
    """Cacheable catalog payload for the compare-page autocomplete."""
    resp = make_response(api_ok({"catalog": get_vehicle_catalog_ui_data()}))
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


@bp.route('/api/compare', methods=['POST'])
@login_required
def compare_api():
    """
    API endpoint for car comparison.
    Accepts a JSON payload with cars to compare (2-3 cars).
    Returns comparison results with deterministic scoring.
    """
    per_ip_limit = current_app.config.get('PER_IP_PER_MIN_LIMIT', PER_IP_PER_MIN_LIMIT)
    
    # Rate limit check
    client_ip = get_client_ip()
    ip_allowed, ip_count, ip_resets_at = check_and_increment_ip_rate_limit(client_ip, limit=per_ip_limit)
    if not ip_allowed:
        retry_after = max(0, int((ip_resets_at - _utcnow()).total_seconds()))
        resp = api_error(
            "rate_limited",
            "חרגת ממגבלת הבקשות לדקה.",
            status=429,
            details={
                "limit": per_ip_limit,
                "used": ip_count,
                "remaining": max(0, per_ip_limit - ip_count),
                "resets_at": ip_resets_at.isoformat(),
            },
        )
        resp.headers["Retry-After"] = str(retry_after)
        return resp
    
    # Log access
    user_id = current_user.id if current_user.is_authenticated else None
    log_access_decision('/api/compare', user_id, 'allowed', 'authenticated user')
    
    # Validate content type
    if not request.is_json:
        log_access_decision('/api/compare', user_id, 'rejected', 'validation error: content-type')
        return api_error("invalid_content_type", "Content-Type must be application/json", status=415)
    
    # Parse JSON payload
    try:
        data = request.get_json(silent=False) or {}
    except Exception:
        log_access_decision('/api/compare', user_id, 'rejected', 'validation error: invalid JSON')
        return api_error("invalid_json", "קלט JSON לא תקין", status=400)
    
    # Get session ID for anonymous tracking
    session_id = session.get('_id') if not user_id else None
    
    # Daily quota enforcement
    tz, _ = resolve_app_timezone()
    day_key, _, _, resets_at, _, _ = compute_quota_window(tz)
    daily_limit = current_app.config.get("USER_DAILY_LIMIT", USER_DAILY_LIMIT)
    owner_bypass = is_owner_user()
    request_id = get_request_id()
    reservation_id = None
    idempotency_key = (request.headers.get("X-Idempotency-Key") or "").strip()
    if len(idempotency_key) > 64:
        return api_error("invalid_idempotency_key", "X-Idempotency-Key must be at most 64 chars", status=400)
    idempotent_retry = False
    reservation_request_id = request_id

    if owner_bypass:
        current_app.logger.info("[QUOTA] method=POST path=/api/compare uid=%s cache_hit=false quota_bypass_reason=owner/admin limit=%s request_id=%s", user_id, daily_limit, request_id)
    if not owner_bypass:
        if idempotency_key:
            idem_request_id = f"idem:{idempotency_key}"
            idem_ttl_seconds = int(current_app.config.get("COMPARE_IDEMPOTENCY_TTL_SECONDS", 300))
            ttl_cutoff = _utcnow() - timedelta(seconds=idem_ttl_seconds)
            existing = (
                QuotaReservation.query
                .filter(
                    QuotaReservation.user_id == user_id,
                    QuotaReservation.day == day_key,
                    QuotaReservation.request_id == idem_request_id,
                    QuotaReservation.created_at >= ttl_cutoff,
                    QuotaReservation.status.in_(("reserved", "consumed")),
                )
                .order_by(QuotaReservation.created_at.desc())
                .first()
            )
            if existing and existing.status == "reserved":
                return api_error("request_in_progress", "בקשה זהה עדיין בתהליך.", status=409)
            if existing and existing.status == "consumed":
                idempotent_retry = True
            else:
                reservation_request_id = idem_request_id
        try:
            if not idempotent_retry:
                ok, used, _active, reservation_id = reserve_daily_quota(
                    user_id, day_key, daily_limit, reservation_request_id
                )
            else:
                ok, used = True, get_daily_quota_usage(user_id, day_key)
        except Exception:
            return api_error("quota_error", "Quota system error", status=500)
        if not ok:
            log_access_decision('/api/compare', user_id, 'rejected', 'daily limit reached')
            return api_error(
                "daily_limit_reached",
                f"הגעת למגבלת השימוש היומית. ניתן לנסות שוב לאחר האיפוס.",
                status=429,
                details={"limit": daily_limit, "used": used, "reset_at": resets_at.isoformat()},
                request_id=request_id,
            )

    engine = comparison_engine()
    if engine == COMPARISON_ENGINE_V3:
        return _compare_v3(
            data, user_id, session_id, owner_bypass, request_id, reservation_id, idempotent_retry, day_key, daily_limit
        )
    if engine == COMPARISON_ENGINE_V2:
        return _compare_v2(
            data, user_id, session_id, owner_bypass, request_id, reservation_id, idempotent_retry, day_key, daily_limit
        )

    # Process comparison
    try:
        log_product_call_verdict_input(
            request_id=request_id,
            feature="compare",
            model=comparison_stage_a_model_id(),
            api_method="interactions_grounded",
            endpoint_family="interactions",
            tools=["google_search"],
        )
        resp = comparison_service.handle_comparison_request(data, user_id, session_id, owner_bypass=owner_bypass)
        current_app.logger.info("[QUOTA] method=POST path=/api/compare uid=%s cache_hit=%s quota_bypass_reason=%s limit=%s request_id=%s status=%s", user_id, False, "owner/admin" if owner_bypass else "none", daily_limit, request_id, resp.status_code)
        if reservation_id and not idempotent_retry:
            if resp.status_code < 400:
                finalize_quota_reservation(reservation_id, user_id, day_key)
            else:
                release_quota_reservation(reservation_id, user_id, day_key)
        # Check for legal terms enforcement
        # Support both standard API format and legal enforcement format
        if resp.status_code == 403:
            try:
                resp_json = resp.get_json()
                if resp_json and resp_json.get("error") == "TERMS_NOT_ACCEPTED":
                    return resp
            except Exception:
                pass  # If JSON parsing fails, just return the response
        # PostHog: compare_completed
        if resp.status_code < 400:
            try:
                track_event(str(user_id), "compare_completed", {"request_id": get_request_id()})
            except Exception:
                pass
        return resp
    except Exception:
        if reservation_id:
            release_quota_reservation(reservation_id, user_id, day_key)
        current_app.logger.exception("compare_api failed")
        return api_error("server_error", "שגיאת שרת בעת השוואה", status=500)


def _wants_stream() -> bool:
    return "application/x-ndjson" in (request.headers.get("Accept") or "")


def _compare_v2(data, user_id, session_id, owner_bypass, request_id, reservation_id, idempotent_retry, day_key, daily_limit):
    """Comparison V2: Level 1.5 + official Level 2 + deterministic engine + JEV + summary."""
    from app.extensions import ai_client
    from app.services.comparison_v2.official_enrichment import url_context_enabled as enrichment_url_context_enabled
    from app.services.comparison_v2.pipeline import build_default_deps, collect_result, run_comparison_v2

    def settle(ok: bool) -> None:
        if not reservation_id or idempotent_retry:
            return
        if ok:
            finalize_quota_reservation(reservation_id, user_id, day_key)
        else:
            release_quota_reservation(reservation_id, user_id, day_key)

    # buyer-profile/2 is validated inside the pipeline (it needs the resolved
    # vehicles to know whether EV questions apply); errors come back as events.
    buyer_profile = data.get("buyer_profile")

    log_product_call_verdict_input(
        request_id=request_id,
        feature="compare_v2",
        model=comparison_enrichment_model_id(),
        api_method="generate_content_grounded",
        endpoint_family="models.generateContent",
        tools=["google_search"] + (["url_context"] if enrichment_url_context_enabled() else []),
    )
    deps = build_default_deps(ai_client)
    events = run_comparison_v2(
        data, deps, user_id=user_id, session_id=session_id, buyer_profile=buyer_profile, request_id=request_id
    )

    def track_completed():
        try:
            track_event(str(user_id), "compare_completed", {"request_id": request_id, "engine": "v2"})
        except Exception:
            pass

    if _wants_stream():
        def generate():
            settled = False
            try:
                for event in events:
                    if event["type"] == "heartbeat":
                        # keeps proxies from idling out a long enrichment;
                        # the client ignores blank lines
                        yield "\n"
                        continue
                    if event["type"] == "progress":
                        line = {"type": "progress", "stage": event["stage"], "label_he": event["label_he"]}
                        if event.get("mode"):
                            line["mode"] = event["mode"]
                    elif event["type"] == "result":
                        settle(True)
                        settled = True
                        track_completed()
                        line = {"type": "result", "ok": True, "data": _public_v2_result(event["data"], owner_bypass)}
                    else:
                        settle(False)
                        settled = True
                        line = {"type": "error", "ok": False, "error": {"code": event["code"], "message": event["message"]}}
                    yield json.dumps(line, ensure_ascii=False) + "\n"
            except Exception:
                current_app.logger.exception("compare_v2 stream failed request_id=%s", request_id)
                if not settled:
                    settle(False)
                    settled = True
                yield json.dumps({"type": "error", "ok": False, "error": {"code": "server_error", "message": "שגיאת שרת בעת השוואה"}}, ensure_ascii=False) + "\n"
            finally:
                if not settled:
                    settle(False)

        resp = Response(stream_with_context(generate()), mimetype="application/x-ndjson")
        resp.implicit_sequence_conversion = False  # keep progress events streaming
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Accel-Buffering"] = "no"
        return resp

    try:
        final = collect_result(events)
    except Exception:
        settle(False)
        current_app.logger.exception("compare_v2 failed request_id=%s", request_id)
        return api_error("server_error", "שגיאת שרת בעת השוואה", status=500)
    if final.get("type") != "result":
        settle(False)
        return api_error(final.get("code", "server_error"), final.get("message", "שגיאה"), status=final.get("status", 500))
    settle(True)
    track_completed()
    payload = _public_v2_result(final["data"], owner_bypass)
    payload["progress"] = final.get("progress", [])
    return api_ok(payload)


def _compare_v3(data, user_id, session_id, owner_bypass, request_id, reservation_id, idempotent_retry, day_key, daily_limit):
    """Comparison V3: facts from TRIPY + deterministic engine + JEV + row explanations + summary (no enrichment)."""
    from app.extensions import ai_client
    from app.services.comparison_v3.pipeline import build_default_deps, collect_result, run_comparison_v3

    def settle(ok: bool) -> None:
        if not reservation_id or idempotent_retry:
            return
        if ok:
            finalize_quota_reservation(reservation_id, user_id, day_key)
        else:
            release_quota_reservation(reservation_id, user_id, day_key)

    log_product_call_verdict_input(
        request_id=request_id,
        feature="compare_v3",
        model=DEFAULT_COMPARISON_V2_MODEL_ID,
        api_method="generate_content",
        endpoint_family="models.generateContent",
        tools=[],
    )
    deps = build_default_deps(ai_client)
    events = run_comparison_v3(data, deps, user_id=user_id, session_id=session_id, request_id=request_id)

    def track_completed():
        try:
            track_event(str(user_id), "compare_completed", {"request_id": request_id, "engine": "v3"})
        except Exception:
            pass

    def error_body(event):
        body = {"code": event["code"], "message": event["message"]}
        for key in ("field", "field_errors"):
            if event.get(key):
                body[key] = event[key]
        return body

    if _wants_stream():
        def generate():
            settled = False
            try:
                for event in events:
                    if event["type"] == "heartbeat":
                        yield "\n"
                        continue
                    if event["type"] == "progress":
                        line = {"type": "progress", "stage": event["stage"], "label_he": event["label_he"]}
                    elif event["type"] == "result":
                        settle(True)
                        settled = True
                        track_completed()
                        line = {"type": "result", "ok": True, "data": _public_v2_result(event["data"], owner_bypass)}
                    else:
                        settle(False)
                        settled = True
                        line = {"type": "error", "ok": False, "status": event.get("status"), "error": error_body(event)}
                    yield json.dumps(line, ensure_ascii=False) + "\n"
            except Exception:
                current_app.logger.exception("compare_v3 stream failed request_id=%s", request_id)
                if not settled:
                    settle(False)
                    settled = True
                yield json.dumps({"type": "error", "ok": False, "error": {"code": "server_error", "message": "שגיאת שרת בעת השוואה"}}, ensure_ascii=False) + "\n"
            finally:
                if not settled:
                    settle(False)

        resp = Response(stream_with_context(generate()), mimetype="application/x-ndjson")
        resp.implicit_sequence_conversion = False
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Accel-Buffering"] = "no"
        return resp

    try:
        final = collect_result(events)
    except Exception:
        settle(False)
        current_app.logger.exception("compare_v3 failed request_id=%s", request_id)
        return api_error("server_error", "שגיאת שרת בעת השוואה", status=500)
    if final.get("type") != "result":
        settle(False)
        details = {k: final[k] for k in ("field", "field_errors") if final.get(k)} or None
        return api_error(final.get("code", "server_error"), final.get("message", "שגיאה"), status=final.get("status", 500),
                         details=details)
    settle(True)
    track_completed()
    payload = _public_v2_result(final["data"], owner_bypass)
    payload["progress"] = final.get("progress", [])
    return api_ok(payload)


def _public_v2_result(data, is_owner):
    """The full decision trace (JEV state, question specs, raw answers) is owner/debug only."""
    payload = dict(data)
    if not is_owner:
        payload.pop("decision_trace", None)
    return payload


@bp.route('/api/compare/history', methods=['GET'])
@login_required
def compare_history():
    """Get comparison history for the current user."""
    user_id = current_user.id
    limit = min(int(request.args.get('limit', 10)), 50)
    
    try:
        history = comparison_service.get_comparison_history(user_id, limit=limit)
        return api_ok({"history": history})
    except Exception:
        # Broad catch intentional: ensures JSON error response instead of HTML 500 page
        # for any failure (database errors, unexpected exceptions, etc.)
        current_app.logger.exception(
            "compare_history failed", 
            extra={"user_id": user_id}
        )
        return api_error(
            "compare_history_failed", 
            "Failed to load comparison history", 
            status=500
        )


@bp.route('/api/compare/<int:comparison_id>', methods=['GET'])
@login_required
def compare_detail(comparison_id):
    """Get details of a specific comparison."""
    user_id = current_user.id
    
    detail = comparison_service.get_comparison_detail(comparison_id, user_id)
    if not detail:
        return api_error("not_found", "השוואה לא נמצאה", status=404)
    if isinstance(detail.get("v2_result"), dict):
        detail = {**detail, "v2_result": _public_v2_result(detail["v2_result"], is_owner_user())}

    return api_ok(detail)


@bp.route('/api/compare/ai-regenerate', methods=['POST'])
@login_required
def compare_ai_regenerate():
    comparison_id_raw = request.args.get("comparison_id", "").strip()
    if not comparison_id_raw.isdigit():
        return api_error("invalid_comparison_id", "comparison_id is required", status=400)

    comparison_id = int(comparison_id_raw)
    try:
        regenerated = comparison_service.regenerate_comparison_ai(comparison_id, current_user.id)
    except Exception:
        current_app.logger.exception(
            "compare_ai_regenerate failed request_id=%s comparison_id=%s user_id=%s",
            get_request_id(),
            comparison_id,
            current_user.id,
        )
        return api_ok({
            "comparison_id": comparison_id,
            "ai": {
                "status": "fallback",
                "reason": "stage_b_error",
                "error": "CALL_FAILED:UNKNOWN",
                "stage_a": None,
                "stage_b": None,
            },
            "narrative": None,
        })

    if not regenerated:
        return api_error("not_found", "השוואה לא נמצאה", status=404)
    return api_ok(regenerated)


@bp.route('/api/compare/cars', methods=['GET'])
@login_required
def get_available_cars():
    """
    Return the car dictionary for the autocomplete.
    Returns a flat list suitable for frontend autocomplete.
    """
    return api_ok({"cars": get_flat_vehicle_catalog()})
