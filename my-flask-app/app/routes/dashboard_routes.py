# -*- coding: utf-8 -*-
"""Dashboard routes blueprint."""

import json
from flask import Blueprint, render_template, current_app, request
from flask_login import current_user, login_required

from app.extensions import db
from app.models import SearchHistory, AdvisorHistory, User
from app.utils.http_helpers import api_ok, api_error, get_request_id, is_owner_user
from app.utils.sanitization import sanitize_analyze_response
from flask_login import logout_user

bp = Blueprint('dashboard', __name__)


@bp.route('/dashboard')
@login_required
def dashboard():
    history_error = None
    user_searches = []
    advisor_entries = []
    try:
        user_searches = SearchHistory.query.filter_by(
            user_id=current_user.id
        ).order_by(SearchHistory.timestamp.desc()).all()

        advisor_entries = AdvisorHistory.query.filter_by(
            user_id=current_user.id
        ).order_by(AdvisorHistory.timestamp.desc()).all()
    except Exception:
        history_error = "לא הצלחנו לטעון את ההיסטוריה כעת."
        try:
            db.session.rollback()
        except Exception:
            current_app.logger.exception("[DASH] rollback failed request_id=%s", get_request_id())
        current_app.logger.exception("[DASH] DB query failed request_id=%s", get_request_id())

    searches_data = []
    for s in user_searches:
        try:
            parsed_result = json.loads(s.result_json)
        except Exception:
            current_app.logger.warning(
                "[DASH] Malformed result_json search_id=%s request_id=%s",
                s.id,
                get_request_id(),
            )
            parsed_result = {}
        searches_data.append({
            "id": s.id,
            "timestamp": s.timestamp.strftime('%d/%m/%Y %H:%M'),
            "make": s.make,
            "model": s.model,
            "year": s.year,
            "mileage_range": s.mileage_range or '',
            "fuel_type": s.fuel_type or '',
            "transmission": s.transmission or '',
            "data": parsed_result
        })

    advisor_count = len(advisor_entries)

    return render_template(
        'dashboard.html',
        searches=searches_data,
        advisor_count=advisor_count,
        user=current_user,
        is_owner=is_owner_user(),
        history_error=history_error,
    )


@bp.route('/search-details/<int:search_id>')
@login_required
def search_details(search_id):
    try:
        s = SearchHistory.query.filter_by(id=search_id, user_id=current_user.id).first()
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
        data_safe = sanitize_analyze_response(json.loads(s.result_json))
        return api_ok({"meta": meta, "data": data_safe})
    except Exception as e:
        current_app.logger.error(f"[DETAILS] Error fetching search details: {e}")
        return api_error("details_fetch_failed", "שגיאת שרת בשליפת נתוני חיפוש", status=500)


@bp.route('/api/account/delete', methods=['POST'])
@login_required
def delete_account():
    """
    Delete user account and all associated data.
    Requires confirmation text 'DELETE' in request body.
    """
    request_id = get_request_id()
    try:
        data = request.get_json() or {}
        confirmation = data.get('confirm', '').strip()
        
        if confirmation != 'DELETE':
            return api_error(
                'INVALID_CONFIRMATION',
                'נדרש לכתוב DELETE בדיוק כדי לאשר מחיקה',
                status=400,
                request_id=request_id
            )
        
        user_id = current_user.id
        user_email = current_user.email
        
        # Log the deletion (without PII in the message, just request_id)
        current_app.logger.info(f"[{request_id}] Account deletion initiated for user_id={user_id}")
        
        # Logout first
        logout_user()
        
        # Delete user (cascade will delete all related data: searches, advisor_searches, quota, reservations)
        user_to_delete = User.query.get(user_id)
        if user_to_delete:
            db.session.delete(user_to_delete)
            db.session.commit()
            current_app.logger.info(f"[{request_id}] Account deleted successfully")
        
        return api_ok(
            {'message': 'החשבון נמחק בהצלחה'},
            status=200,
            request_id=request_id
        )
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"[{request_id}] Account deletion failed: {str(e)}")
        return api_error(
            'DELETE_FAILED',
            'שגיאה במחיקת החשבון',
            status=500,
            details={'error': str(e)},
            request_id=request_id
        )


@bp.route('/api/history/list', methods=['GET'])
@login_required
def history_list():
    """
    Returns list of user's search history (Reliability Analyzer only).
    """
    try:
        searches = SearchHistory.query.filter_by(
            user_id=current_user.id
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
                'transmission': s.transmission
            })
        
        return api_ok({'searches': history_items})
        
    except Exception as e:
        current_app.logger.error(f"History list error: {str(e)}")
        return api_error('HISTORY_LIST_FAILED', 'Failed to fetch history', status=500)


@bp.route('/api/history/item/<int:item_id>', methods=['GET'])
@login_required
def history_item(item_id):
    """
    Returns a specific search history item (current_user only).
    """
    try:
        search = SearchHistory.query.filter_by(
            id=item_id,
            user_id=current_user.id
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
            'result': result_data
        })
        
    except Exception as e:
        current_app.logger.error(f"History item error: {str(e)}")
        return api_error('HISTORY_ITEM_FAILED', 'Failed to fetch history item', status=500)
