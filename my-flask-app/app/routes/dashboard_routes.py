# -*- coding: utf-8 -*-
"""Dashboard routes blueprint."""

from flask import Blueprint, render_template, current_app, request, jsonify
from flask_login import current_user, login_required

from app.extensions import db
from app.models import User
from app.utils.http_helpers import api_ok, api_error, get_request_id, is_owner_user
from app.services import history_service
from flask_login import logout_user

bp = Blueprint('dashboard', __name__)


@bp.route('/dashboard')
@login_required
def dashboard():
    user_searches, advisor_entries, search_error, advisor_error = history_service.fetch_dashboard_history(current_user.id)
    history_error = search_error or advisor_error
    searches_data = history_service.build_searches_data(user_searches)
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
    return history_service.search_details_response(search_id, current_user.id)


@bp.route('/api/account/delete', methods=['POST'])
@login_required
def delete_account():
    """
    Delete user account and all associated data.
    Requires confirmation text 'DELETE' in request body.
    Requires Content-Type: application/json and Origin/Referer validation (handled by factory.py).
    """
    request_id = get_request_id()
    
    # Validate Content-Type
    content_type = request.headers.get('Content-Type', '')
    if 'application/json' not in content_type.lower():
        return api_error(
            'INVALID_CONTENT_TYPE',
            'דרוש Content-Type: application/json',
            status=400,
            request_id=request_id
        )
    
    try:
        data = request.get_json() or {}
    except Exception:
        return api_error(
            'INVALID_JSON',
            'נתוני JSON לא תקינים',
            status=400,
            request_id=request_id
        )
    
    try:
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
        
        # Check if user is owner (owners cannot be deleted)
        if is_owner_user():
            return api_error(
                "owner_forbidden",
                "Owner account cannot be deleted",
                status=403,
                request_id=request_id,
            )
        
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
        
        resp = jsonify({"ok": True, "message": "Account deleted", "request_id": request_id})
        resp.status_code = 200
        resp.headers["X-Request-ID"] = request_id
        return resp
        
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
    return history_service.history_list_response(current_user.id)


@bp.route('/api/history/item/<int:item_id>', methods=['GET'])
@login_required
def history_item(item_id):
    """
    Returns a specific search history item (current_user only).
    """
    return history_service.history_item_response(item_id, current_user.id)
