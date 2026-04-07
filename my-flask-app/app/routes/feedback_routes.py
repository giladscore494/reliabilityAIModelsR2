# -*- coding: utf-8 -*-
"""Feedback routes blueprint – thumbs up/down on analyses."""

from flask import Blueprint, request, current_app
from flask_login import current_user, login_required

from app.extensions import db
from app.models import Feedback, SearchHistory
from app.utils.http_helpers import api_error, api_ok, get_request_id
from app.utils.analytics import track_event

bp = Blueprint('feedback', __name__)


@bp.route('/api/feedback', methods=['POST'])
@login_required
def submit_feedback():
    """Accept thumbs-up/down feedback. UPSERT on (user_id, search_history_id)."""
    if not request.is_json:
        return api_error(
            "invalid_content_type",
            "Content-Type must be application/json",
            status=415,
        )

    try:
        data = request.get_json(silent=False) or {}
    except Exception:
        return api_error("invalid_json", "קלט JSON לא תקין", status=400)

    is_positive = data.get("is_positive")
    if not isinstance(is_positive, bool):
        return api_error(
            "validation_error",
            "is_positive must be a boolean",
            status=400,
        )

    search_history_id = data.get("search_history_id")
    if search_history_id is not None:
        try:
            search_history_id = int(search_history_id)
        except (TypeError, ValueError):
            return api_error(
                "validation_error",
                "search_history_id must be an integer",
                status=400,
            )
        # Ownership check
        row = SearchHistory.query.filter_by(
            id=search_history_id, user_id=current_user.id,
        ).first()
        if not row:
            return api_error(
                "not_found",
                "search_history_id not found or not yours",
                status=404,
            )

    # UPSERT
    existing = Feedback.query.filter_by(
        user_id=current_user.id,
        search_history_id=search_history_id,
    ).first()

    if existing:
        existing.is_positive = is_positive
    else:
        fb = Feedback(
            user_id=current_user.id,
            search_history_id=search_history_id,
            is_positive=is_positive,
        )
        db.session.add(fb)

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("[FEEDBACK] commit failed")
        return api_error(
            "server_error", "שגיאת שרת בעת שמירת פידבק", status=500,
        )

    # PostHog event
    track_event(
        str(current_user.id),
        "feedback_given",
        {"is_positive": is_positive, "request_id": get_request_id()},
    )

    return api_ok({"ok": True})
