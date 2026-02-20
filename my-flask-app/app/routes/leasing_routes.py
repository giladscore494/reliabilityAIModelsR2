# -*- coding: utf-8 -*-
"""Leasing Advisor routes blueprint."""

import time
from datetime import datetime

from flask import Blueprint, render_template, request, current_app
from flask_login import login_required, current_user

from app.quota import (
    check_and_increment_ip_rate_limit,
    get_client_ip,
    log_access_decision,
    resolve_app_timezone,
    compute_quota_window,
    reserve_daily_quota,
    finalize_quota_reservation,
    release_quota_reservation,
    PER_IP_PER_MIN_LIMIT,
)
from app.utils.http_helpers import api_ok, api_error, get_request_id, is_owner_user
from app.services import leasing_advisor_service as leasing_svc
from app.services.history_service import fetch_leasing_history, build_leasing_data, safe_json_obj
from app.legal import TERMS_VERSION, PRIVACY_VERSION, parse_legal_confirm
from app.models import LegalAcceptance, LeasingAdvisorHistory

bp = Blueprint("leasing", __name__)


def _check_legal_accepted() -> bool:
    """Check if current user has accepted the current legal terms."""
    if not current_user.is_authenticated:
        return False
    from flask import current_app
    tv = current_app.config.get("TERMS_VERSION", TERMS_VERSION)
    pv = current_app.config.get("PRIVACY_VERSION", PRIVACY_VERSION)
    return LegalAcceptance.query.filter_by(
        user_id=current_user.id,
        terms_version=tv,
        privacy_version=pv,
    ).first() is not None


@bp.route("/leasing")
@login_required
def leasing_page():
    """Render leasing advisor page."""
    legal_accepted = _check_legal_accepted()
    return render_template(
        "leasing_advisor.html",
        user=current_user,
        is_owner=is_owner_user(),
        active_page="leasing",
        legal_accepted=legal_accepted,
        terms_version=current_app.config.get("TERMS_VERSION", TERMS_VERSION),
        privacy_version=current_app.config.get("PRIVACY_VERSION", PRIVACY_VERSION),
    )


@bp.route("/api/leasing/frame", methods=["POST"])
@login_required
def leasing_frame():
    """
    Compute BIK frame and return candidate list.
    Accepts either uploaded file or manual input (BIK / list price).
    """
    request_id = get_request_id()
    user_id = current_user.id

    # IP rate limit
    per_ip_limit = current_app.config.get("PER_IP_PER_MIN_LIMIT", PER_IP_PER_MIN_LIMIT)
    client_ip = get_client_ip()
    ip_allowed, ip_count, ip_resets_at = check_and_increment_ip_rate_limit(client_ip, limit=per_ip_limit)
    if not ip_allowed:
        retry_after = max(0, int((ip_resets_at - datetime.utcnow()).total_seconds()))
        resp = api_error("rate_limited", "חרגת ממגבלת הבקשות לדקה.", status=429)
        resp.headers["Retry-After"] = str(retry_after)
        return resp

    # Check for uploaded file
    uploaded_file = request.files.get("file")
    candidates = []
    frame_context = {}

    if uploaded_file and uploaded_file.filename:
        # Validate file extension
        fname = (uploaded_file.filename or "").lower()
        if not (fname.endswith(".csv") or fname.endswith(".xlsx")):
            return api_error("invalid_file", "Only CSV and XLSX files are accepted.", status=400)

        rows, err = leasing_svc.parse_company_file(uploaded_file)
        if err:
            return api_error("file_parse_error", err, status=400)

        # Get filter params from form
        max_bik_str = request.form.get("max_bik", "")
        powertrain = request.form.get("powertrain", "").lower().strip()
        body_type = request.form.get("body_type", "").lower().strip()

        max_bik = None
        if max_bik_str:
            try:
                max_bik = float(max_bik_str)
                if max_bik < 0 or max_bik > 50000:
                    return api_error("validation_error", "BIK value out of range", status=400)
            except ValueError:
                return api_error("validation_error", "Invalid BIK value", status=400)

        if powertrain and powertrain not in leasing_svc.ALLOWED_POWERTRAINS:
            powertrain = ""

        candidates = leasing_svc.select_candidates(rows, max_bik=max_bik, powertrain=powertrain or None, body_type=body_type or None)
        frame_context = {"source": "upload", "filename": fname[:100], "total_rows": len(rows), "max_bik": max_bik, "powertrain": powertrain}
    else:
        # Manual input mode (JSON body)
        if request.is_json:
            data = request.get_json(silent=True) or {}
        else:
            data = {k: request.form.get(k) for k in ("max_bik", "list_price", "powertrain", "body_type")}

        max_bik_raw = data.get("max_bik")
        list_price_raw = data.get("list_price")
        powertrain = (data.get("powertrain") or "unknown").lower().strip()
        body_type = (data.get("body_type") or "").lower().strip()

        if powertrain and powertrain not in leasing_svc.ALLOWED_POWERTRAINS:
            powertrain = "unknown"

        max_bik = None
        if list_price_raw:
            try:
                list_price = int(list_price_raw)
                if list_price < 10000 or list_price > 2000000:
                    return api_error("validation_error", "List price out of range", status=400)
            except (ValueError, TypeError):
                return api_error("validation_error", "Invalid list price", status=400)

            bik_info = leasing_svc.calc_bik_2026(list_price, powertrain if powertrain != "unknown" else "ice")
            max_bik = bik_info["monthly_bik"] * 1.15  # 15% tolerance
            frame_context = {"source": "list_price", "list_price": list_price, "computed_bik": bik_info}
        elif max_bik_raw:
            try:
                max_bik = float(max_bik_raw)
                if max_bik < 0 or max_bik > 50000:
                    return api_error("validation_error", "BIK value out of range", status=400)
            except (ValueError, TypeError):
                return api_error("validation_error", "Invalid BIK value", status=400)

            inverted = leasing_svc.invert_list_price_from_bik(max_bik, powertrain)
            frame_context = {"source": "max_bik", "max_bik": max_bik, "inverted": inverted}
        else:
            # No filter: return full catalog
            frame_context = {"source": "catalog", "max_bik": None}

        catalog = leasing_svc.load_catalog()
        candidates = leasing_svc.select_candidates(
            catalog,
            max_bik=max_bik,
            powertrain=powertrain if powertrain != "unknown" else None,
            body_type=body_type or None,
        )

    log_access_decision("/api/leasing/frame", user_id, "allowed", f"candidates={len(candidates)}")
    return api_ok({
        "candidates": candidates[:50],
        "frame": frame_context,
        "total_candidates": len(candidates),
    })


@bp.route("/api/leasing/recommend", methods=["POST"])
@login_required
def leasing_recommend():
    """
    Run Gemini 3 Flash ranking on candidates + preferences.
    Requires legal acceptance. Subject to daily quota (5/day).
    """
    request_id = get_request_id()
    user_id = current_user.id

    # IP rate limit
    per_ip_limit = current_app.config.get("PER_IP_PER_MIN_LIMIT", PER_IP_PER_MIN_LIMIT)
    client_ip = get_client_ip()
    ip_allowed, ip_count, ip_resets_at = check_and_increment_ip_rate_limit(client_ip, limit=per_ip_limit)
    if not ip_allowed:
        retry_after = max(0, int((ip_resets_at - datetime.utcnow()).total_seconds()))
        resp = api_error("rate_limited", "חרגת ממגבלת הבקשות לדקה.", status=429)
        resp.headers["Retry-After"] = str(retry_after)
        return resp

    if not request.is_json:
        return api_error("invalid_content_type", "Content-Type must be application/json", status=415)

    data = request.get_json(silent=True) or {}
    if not parse_legal_confirm(data.get("legal_confirm")) or not _check_legal_accepted():
        return api_error(
            "TERMS_NOT_ACCEPTED",
            "יש לאשר תנאי שימוש ומדיניות פרטיות לפני המשך.",
            status=403,
        )
    candidates = data.get("candidates")
    prefs = data.get("prefs")
    frame_context = data.get("frame") or {}

    if not candidates or not isinstance(candidates, list):
        return api_error("validation_error", "candidates list is required", status=400)
    if not prefs or not isinstance(prefs, dict):
        return api_error("validation_error", "prefs object is required", status=400)
    if len(candidates) > 50:
        candidates = candidates[:50]

    # Daily quota enforcement
    from app.factory import USER_DAILY_LIMIT
    tz, _ = resolve_app_timezone()
    day_key, _, _, resets_at, _, retry_after = compute_quota_window(tz)
    daily_limit = current_app.config.get("USER_DAILY_LIMIT", USER_DAILY_LIMIT)

    owner_bypass = is_owner_user()
    reservation_id = None

    if not owner_bypass:
        try:
            ok, used, active, reservation_id = reserve_daily_quota(user_id, day_key, daily_limit, request_id)
        except Exception:
            return api_error("quota_error", "Quota system error", status=500)
        if not ok:
            log_access_decision("/api/leasing/recommend", user_id, "rejected", "daily limit reached")
            return api_error(
                "DAILY_LIMIT_REACHED",
                "הגעת למגבלת השימוש היומית.",
                status=429,
                details={"limit": daily_limit, "used": used, "resets_at": resets_at.isoformat()},
            )

    # Call Gemini
    start_time = time.perf_counter()
    result, err = leasing_svc.call_gemini_leasing(prefs, candidates, frame_context)
    duration_ms = int((time.perf_counter() - start_time) * 1000)

    if err:
        if reservation_id:
            release_quota_reservation(reservation_id, user_id, day_key)
        log_access_decision("/api/leasing/recommend", user_id, "error", f"AI error: {err}")
        return api_error("leasing_ai_error", err, status=502)

    # Finalize quota
    if reservation_id:
        finalize_quota_reservation(reservation_id, user_id, day_key)

    # Save to history
    history_id = leasing_svc.save_leasing_history(
        user_id=user_id,
        frame_input=frame_context,
        candidates=candidates,
        prefs=prefs,
        gemini_response=result,
        duration_ms=duration_ms,
        request_id=request_id,
    )

    log_access_decision("/api/leasing/recommend", user_id, "allowed", f"history_id={history_id}")
    return api_ok({
        "result": result,
        "history_id": history_id,
        "duration_ms": duration_ms,
        "request_id": request_id,
    })


@bp.route("/api/leasing/history", methods=["GET"])
@login_required
def leasing_history():
    """Return leasing advisor history summaries for the current user."""
    try:
        limit = int(request.args.get("limit", 20))
    except (TypeError, ValueError):
        limit = 20
    limit = min(max(limit, 1), 50)

    entries, err = fetch_leasing_history(current_user.id)
    if err:
        return api_error("leasing_history_failed", err, status=500)

    return api_ok({"history": build_leasing_data(entries)[:limit]})


@bp.route("/api/leasing/<int:history_id>", methods=["GET"])
@login_required
def leasing_history_detail(history_id):
    """Return a specific leasing advisor history record owned by the current user."""
    item = LeasingAdvisorHistory.query.filter_by(
        id=history_id,
        user_id=current_user.id,
    ).first()
    if not item:
        return api_error("not_found", "יועץ ליסינג לא נמצא", status=404)

    return api_ok({
        "id": item.id,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "duration_ms": item.duration_ms,
        "request_id": item.request_id,
        "frame_input_json": safe_json_obj(item.frame_input_json, default={}),
        "candidates_json": safe_json_obj(item.candidates_json, default=[]),
        "prefs_json": safe_json_obj(item.prefs_json, default={}),
        "gemini_response_json": safe_json_obj(item.gemini_response_json, default={}),
    })
