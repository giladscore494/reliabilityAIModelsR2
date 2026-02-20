# -*- coding: utf-8 -*-
"""History service helpers."""

import json
from typing import List, Tuple, Optional
from sqlalchemy.exc import SQLAlchemyError

from flask import current_app

from app.extensions import db
from app.models import SearchHistory, AdvisorHistory, LeasingAdvisorHistory
from app.utils.http_helpers import api_ok, api_error, get_request_id
from app.utils.sanitization import sanitize_analyze_response


def safe_json_obj(value, default=None):
    """Safely decode value into dict/list, including a double-encoded JSON string."""
    fallback = {} if default is None else default
    try:
        if isinstance(value, (dict, list)):
            return value
        if not isinstance(value, str):
            return fallback
        stripped = value.strip()
        if not stripped:
            return fallback
        result = json.loads(stripped)
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except Exception:
                return fallback
        return result if isinstance(result, (dict, list)) else fallback
    except Exception:
        return fallback


def fetch_dashboard_history(user_id: int) -> Tuple[list, list, Optional[str], Optional[str]]:
    search_error = None
    advisor_error = None
    logger = current_app.logger

    try:
        user_searches = SearchHistory.query.filter_by(
            user_id=user_id
        ).order_by(SearchHistory.timestamp.desc()).all()
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
        ).order_by(AdvisorHistory.timestamp.desc()).all()
    except Exception:
        advisor_error = "לא הצלחנו לטעון את היסטוריית ההמלצות כעת."
        try:
            db.session.rollback()
        except Exception:
            logger.exception("[DASH] advisor rollback failed request_id=%s", get_request_id())
        logger.exception("[DASH] advisor DB query failed request_id=%s", get_request_id())
        advisor_entries = []

    return user_searches, advisor_entries, search_error, advisor_error


def fetch_leasing_history(user_id: int) -> Tuple[list, Optional[str]]:
    """Fetch leasing advisor history for dashboard."""
    logger = current_app.logger
    try:
        entries = LeasingAdvisorHistory.query.filter_by(
            user_id=user_id
        ).order_by(LeasingAdvisorHistory.created_at.desc()).limit(50).all()
        return entries, None
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            logger.exception("[DASH] leasing rollback failed request_id=%s", get_request_id())
        logger.exception("[DASH] leasing DB query failed request_id=%s", get_request_id())
        return [], "לא הצלחנו לטעון את היסטוריית יועץ הליסינג כעת."


def build_leasing_data(entries: list) -> list:
    """Build leasing history summary for dashboard display."""
    result = []
    for e in entries:
        frame = safe_json_obj(e.frame_input_json, default={})
        response = safe_json_obj(e.gemini_response_json, default={})
        if not isinstance(frame, dict):
            frame = {}
        top_rec = ""
        top3 = response.get("top3", []) if isinstance(response, dict) else []
        if isinstance(top3, list) and top3:
            first = top3[0]
            if isinstance(first, dict):
                top_rec = f"{first.get('make', '')} {first.get('model', '')}"
        result.append({
            "id": e.id,
            "created_at": e.created_at.strftime("%d/%m/%Y %H:%M"),
            "frame_summary": f"BIK: {frame.get('max_bik', '—')} | {frame.get('source', '')}",
            "top_recommendation": top_rec.strip(),
            "duration_ms": e.duration_ms,
        })
    return result


def build_searches_data(user_searches: List[SearchHistory]) -> list:
    logger = current_app.logger
    searches_data = []
    for s in user_searches:
        try:
            parsed_result = json.loads(s.result_json)
        except Exception:
            logger.warning(
                "[DASH] Malformed result_json search_id=%s request_id=%s",
                s.id,
                get_request_id(),
            )
            parsed_result = {}
        parsed_result.pop("reliability_score", None)
        parsed_result.pop("base_score_calculated", None)
        searches_data.append({
            "id": s.id,
            "timestamp": s.timestamp.strftime('%d/%m/%Y %H:%M'),
            "make": s.make,
            "model": s.model,
            "year": s.year,
            "mileage_range": s.mileage_range or '',
            "fuel_type": s.fuel_type or '',
            "transmission": s.transmission or '',
            "data": parsed_result,
            "duration_ms": getattr(s, "duration_ms", None),
        })
    return searches_data


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
        raw = json.loads(s.result_json)

        est = raw.get("estimated_reliability")
        estimated_map = {
            "low": "נמוך",
            "medium": "בינוני",
            "high": "גבוה",
            "unknown": "לא ידוע",
            "נמוך": "נמוך",
            "בינוני": "בינוני",
            "גבוה": "גבוה",
            "לא ידוע": "לא ידוע",
            "": "לא ידוע",
            None: "לא ידוע",
        }
        derived = None
        est_norm = str(est).strip().lower() if est is not None else "unknown"
        if estimated_map.get(est_norm) is None or estimated_map.get(est_norm) == "לא ידוע":
            try:
                if "base_score_calculated" in raw:
                    base_val = float(raw["base_score_calculated"])
                    if base_val >= 80:
                        derived = "גבוה"
                    elif base_val >= 60:
                        derived = "בינוני"
                    else:
                        derived = "נמוך"
                elif "reliability_score" in raw:
                    rel_val = float(raw["reliability_score"])
                    if rel_val >= 7:
                        derived = "גבוה"
                    elif rel_val >= 4:
                        derived = "בינוני"
                    else:
                        derived = "נמוך"
            except Exception:
                derived = None
        final_est = estimated_map.get(est_norm)
        if final_est is None or final_est == "לא ידוע":
            final_est = derived or "לא ידוע"

        raw["estimated_reliability"] = final_est
        raw.pop("base_score_calculated", None)
        raw.pop("reliability_score", None)

        allowed_set = {"נמוך", "בינוני", "גבוה", "לא ידוע"}
        if raw["estimated_reliability"] not in allowed_set:
            raw["estimated_reliability"] = "לא ידוע"

        data_safe = sanitize_analyze_response(raw)
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

        result_data = json.loads(search.result_json) if search.result_json else {}

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
