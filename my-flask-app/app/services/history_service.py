# -*- coding: utf-8 -*-
"""History service helpers."""

import json
from typing import Any, Dict, List, Tuple, Optional
from sqlalchemy.exc import SQLAlchemyError

from flask import current_app

from app.extensions import db
from app.models import SearchHistory, AdvisorHistory
from app.utils.http_helpers import api_ok, api_error, get_request_id
from app.utils.ai_guardrails import apply_feature_guardrails, safe_json_obj
from app.utils.sanitization import (
    sanitize_analyze_response,
    sanitize_reliability_report_response,
    derive_information_status,
)

# Dashboard query limits — keep dashboard lightweight.
DASHBOARD_SEARCH_LIMIT = 50
DASHBOARD_ADVISOR_LIMIT = 50


def fetch_dashboard_history(user_id: int) -> Tuple[list, list, Optional[str], Optional[str]]:
    search_error = None
    advisor_error = None
    logger = current_app.logger

    try:
        user_searches = SearchHistory.query.filter_by(
            user_id=user_id
        ).order_by(SearchHistory.timestamp.desc()).limit(DASHBOARD_SEARCH_LIMIT).all()
    except Exception:
        search_error = "לא הצלחנו לטעון את ההיסטוריה כעת."
        try:
            db.session.rollback()
        except Exception:
            logger.exception("[DASH] rollback failed request_id=%s", get_request_id())
        logger.exception("[DASH] DB query failed request_id=%s", get_request_id())
        user_searches = []

    try:
        advisor_entries = AdvisorHistory.query.filter_by(
            user_id=user_id
        ).order_by(AdvisorHistory.timestamp.desc()).limit(DASHBOARD_ADVISOR_LIMIT).all()
    except Exception:
        advisor_error = "לא הצלחנו לטעון את היסטוריית ההמלצות כעת."
        try:
            db.session.rollback()
        except Exception:
            logger.exception("[DASH] advisor rollback failed request_id=%s", get_request_id())
        logger.exception("[DASH] advisor DB query failed request_id=%s", get_request_id())
        advisor_entries = []

    return user_searches, advisor_entries, search_error, advisor_error


def _normalize_reliability_history_payload(raw: Any) -> Dict[str, Any]:
    payload = dict(raw) if isinstance(raw, dict) else {}
    sanitized_report = sanitize_reliability_report_response(payload.get("reliability_report"))
    payload["reliability_report"] = sanitized_report
    payload.update(
        derive_information_status(
            payload,
            sanitized_report=sanitized_report,
        )
    )
    return payload


def _extract_lightweight_card_fields(parsed_result: Dict[str, Any]) -> Dict[str, Any]:
    """Extract only the minimal safe fields needed for dashboard card rendering.

    This avoids sending the full AI result payload to the template and keeps
    the dashboard HTML small and fast.
    """
    card = {}
    for key in (
        "data_quality_label",
        "decision_readiness",
        "missing_critical_info",
        "verification_focus",
    ):
        val = parsed_result.get(key)
        if val is not None:
            card[key] = val
    # Include whether a reliability_report and vehicle_profile exist (booleans, not full data).
    rr = parsed_result.get("reliability_report")
    card["has_reliability_report"] = bool(rr and isinstance(rr, dict) and rr.get("available") is not False and len(rr) > 0)
    vp = parsed_result.get("vehicle_profile")
    if isinstance(vp, dict):
        vi = vp.get("vehicle_identity")
        if isinstance(vi, dict):
            card["israel_market_status"] = vi.get("israel_market_status")
    card["guardrail_meta"] = parsed_result.get("guardrail_meta", {})
    card["legacy_notice"] = parsed_result.get("legacy_notice")
    return card


def build_searches_data(user_searches: List[SearchHistory]) -> list:
    """Build lightweight search-history cards for the dashboard list.

    Per-row guardrail validation is replaced by a single batch sanitise pass
    (``apply_feature_guardrails`` with ``log_validation=False``) followed by
    one aggregated log line.
    """
    logger = current_app.logger
    request_id = get_request_id()
    searches_data: list = []
    total = 0
    warnings_count = 0
    critical_count = 0
    repaired_count = 0
    for s in user_searches:
        total += 1
        parsed_result = safe_json_obj(s.result_json, default={})
        parsed_result = _normalize_reliability_history_payload(parsed_result)
        parsed_result, report = apply_feature_guardrails(
            "dashboard_history", {}, parsed_result, log_validation=False,
        )
        if report.get("warnings"):
            warnings_count += 1
        if report.get("critical_issues"):
            critical_count += 1
        if report.get("status") == "passed" or (
            isinstance(parsed_result, dict) and parsed_result.get("guardrail_meta", {}).get("repaired")
        ):
            repaired_count += 1

        card_fields = _extract_lightweight_card_fields(parsed_result)
        searches_data.append({
            "id": s.id,
            "timestamp": s.timestamp.strftime('%d/%m/%Y %H:%M'),
            "make": s.make,
            "model": s.model,
            "year": s.year,
            "mileage_range": s.mileage_range or '',
            "fuel_type": s.fuel_type or '',
            "transmission": s.transmission or '',
            "duration_ms": getattr(s, "duration_ms", None),
            **card_fields,
        })
    if total:
        logger.info(
            "[DASH] history_guardrail_summary request_id=%s total=%d warnings=%d critical=%d repaired=%d",
            request_id, total, warnings_count, critical_count, repaired_count,
        )
    return searches_data


def build_advisor_data(advisor_entries: List[AdvisorHistory]) -> list:
    """Build lightweight advisor-history cards for dashboard list rendering."""
    logger = current_app.logger
    request_id = get_request_id()
    data: list = []
    total = 0
    warnings_count = 0
    critical_count = 0
    for entry in advisor_entries:
        total += 1
        parsed_result = safe_json_obj(entry.result_json, default={})
        if not isinstance(parsed_result, dict):
            parsed_result = {}
        cars = parsed_result.get("recommended_cars") if isinstance(parsed_result.get("recommended_cars"), list) else []
        first = cars[0] if cars else {}
        top_recommendation = ""
        if isinstance(first, dict):
            brand = (first.get("brand") or "").strip()
            model = (first.get("model") or "").strip()
            top_recommendation = f"{brand} {model}".strip()
        parsed_result, report = apply_feature_guardrails(
            "dashboard_history", {}, parsed_result, log_validation=False,
        )
        if report.get("warnings"):
            warnings_count += 1
        if report.get("critical_issues"):
            critical_count += 1
        data.append({
            "id": entry.id,
            "timestamp": entry.timestamp.strftime("%d/%m/%Y %H:%M"),
            "top_recommendation": top_recommendation,
            "duration_ms": getattr(entry, "duration_ms", None),
            "guardrail_meta": parsed_result.get("guardrail_meta", {}),
            "legacy_notice": parsed_result.get("legacy_notice"),
        })
    if total:
        logger.info(
            "[DASH] advisor_guardrail_summary request_id=%s total=%d warnings=%d critical=%d",
            request_id, total, warnings_count, critical_count,
        )
    return data


def search_details_response(search_id: int, user_id: int):
    try:
        s = SearchHistory.query.filter_by(id=search_id, user_id=user_id).first()
        if not s:
            return api_error("not_found", "לא נמצא רישום מתאים", status=404)

        meta = {
            "id": s.id,
            "timestamp": s.timestamp.strftime("%d/%m/%Y %H:%M"),
            "make": s.make.title() if s.make else "",
            "model": s.model.title() if s.model else "",
            "year": s.year,
            "mileage_range": s.mileage_range,
            "fuel_type": s.fuel_type,
            "transmission": s.transmission,
        }
        raw = safe_json_obj(s.result_json, default={})

        raw = _normalize_reliability_history_payload(raw)
        data_safe = sanitize_analyze_response(raw)
        data_safe, _ = apply_feature_guardrails("dashboard_history", {}, data_safe)
        return api_ok({"meta": meta, "data": data_safe})
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            current_app.logger.exception("[DETAILS] rollback failed request_id=%s", get_request_id())
        current_app.logger.error(f"[DETAILS] Error fetching search details: {e}")
        return api_error("details_fetch_failed", "שגיאת שרת בשליפת נתוני חיפוש", status=500)


def history_list_response(user_id: int):
    logger = current_app.logger
    request_id = get_request_id()
    try:
        searches = SearchHistory.query.filter_by(
            user_id=user_id
        ).order_by(SearchHistory.timestamp.desc()).limit(50).all()

        history_items = []
        for s in searches:
            history_items.append({
                'id': s.id,
                'timestamp': s.timestamp.isoformat(),
                'make': s.make,
                'model': s.model,
                'year': s.year,
                'mileage_range': s.mileage_range,
                'fuel_type': s.fuel_type,
                'transmission': s.transmission,
                'duration_ms': getattr(s, "duration_ms", None),
            })

        return api_ok({'searches': history_items})
    except SQLAlchemyError as e:
        try:
            db.session.rollback()
        except SQLAlchemyError:
            logger.exception("[HIST] history_list rollback failed request_id=%s", request_id)
        logger.exception("[HIST] history_list query failed request_id=%s error=%s", request_id, str(e))
        return api_error('history_unavailable', 'שגיאה בשליפת היסטוריית חיפושים', status=500)


def history_item_response(item_id: int, user_id: int):
    logger = current_app.logger
    request_id = get_request_id()
    try:
        search = SearchHistory.query.filter_by(
            id=item_id,
            user_id=user_id
        ).first()

        if not search:
            return api_error('NOT_FOUND', 'פריט לא נמצא או אין לך גישה אליו', status=404)

        result_data = safe_json_obj(search.result_json, default={}) if search.result_json else {}
        result_data = sanitize_analyze_response(_normalize_reliability_history_payload(result_data))
        result_data, _ = apply_feature_guardrails("dashboard_history", {}, result_data)

        return api_ok({
            'id': search.id,
            'timestamp': search.timestamp.isoformat(),
            'make': search.make,
            'model': search.model,
            'year': search.year,
            'mileage_range': search.mileage_range,
            'fuel_type': search.fuel_type,
            'transmission': search.transmission,
            'duration_ms': getattr(search, "duration_ms", None),
            'result': result_data
        })
    except SQLAlchemyError as e:
        try:
            db.session.rollback()
        except SQLAlchemyError:
            logger.exception("[HIST] history_item rollback failed request_id=%s", request_id)
        logger.exception("[HIST] history item error request_id=%s error=%s", request_id, str(e))
        return api_error('history_unavailable', 'שגיאה בשליפת היסטוריית חיפושים', status=500)
