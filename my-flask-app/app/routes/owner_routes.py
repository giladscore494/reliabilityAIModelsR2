# -*- coding: utf-8 -*-
"""Owner-only routes for managing public examples."""

import re
import logging

from flask import Blueprint, render_template, request, current_app
from flask_login import current_user, login_required

from app.extensions import db
from app.models import SearchHistory
from app.utils.http_helpers import api_error, api_ok
from app.utils.auth_helpers import owner_required

logger = logging.getLogger(__name__)

bp = Blueprint('owner', __name__)

_SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9-]{1,62}$')
_MAX_SELECTIONS = 4


@bp.route('/owner/examples')
@login_required
@owner_required
def owner_examples():
    """Render the owner dashboard for managing public examples."""
    my_rows = (
        SearchHistory.query
        .filter_by(user_id=current_user.id)
        .order_by(SearchHistory.id.desc())
        .limit(100)
        .all()
    )
    public_rows = (
        SearchHistory.query
        .filter_by(is_public_example=True)
        .all()
    )
    return render_template(
        'owner_examples.html',
        my_rows=my_rows,
        public_rows=public_rows,
        user=current_user,
    )


@bp.route('/owner/examples/update', methods=['POST'])
@login_required
@owner_required
def owner_examples_update():
    """Replace public examples with the submitted selections."""
    if not request.is_json:
        return api_error(
            "invalid_content_type",
            "Content-Type must be application/json",
            status=415,
        )

    try:
        data = request.get_json(silent=False) or {}
    except Exception:
        return api_error("invalid_json", "Invalid JSON", status=400)

    selections = data.get("selections", [])
    if not isinstance(selections, list):
        return api_error(
            "validation_error", "selections must be an array",
            status=400,
        )
    if len(selections) > _MAX_SELECTIONS:
        return api_error(
            "validation_error",
            f"ניתן לבחור עד {_MAX_SELECTIONS} דוגמאות",
            status=400,
        )

    # Validate each selection
    seen_slugs = set()
    validated = []
    for sel in selections:
        hid = sel.get("history_id")
        slug = (sel.get("slug") or "").strip()

        if not isinstance(hid, int):
            return api_error(
                "validation_error",
                "history_id must be an integer",
                status=400,
            )
        if not _SLUG_RE.match(slug):
            return api_error(
                "validation_error",
                f"slug '{slug}' is invalid (a-z0-9 and hyphens only)",
                status=400,
            )
        if slug in seen_slugs:
            return api_error(
                "validation_error",
                f"duplicate slug: {slug}",
                status=400,
            )
        seen_slugs.add(slug)

        # Ownership check — must belong to current user
        row = SearchHistory.query.filter_by(
            id=hid, user_id=current_user.id,
        ).first()
        if not row:
            return api_error(
                "validation_error",
                f"history_id {hid} not found or not yours",
                status=400,
            )
        validated.append((row, slug))

    # Single transaction: clear all, then set new
    try:
        old_public = SearchHistory.query.filter_by(
            is_public_example=True,
        ).all()
        for r in old_public:
            r.is_public_example = False
            r.example_slug = None

        for row, slug in validated:
            row.is_public_example = True
            row.example_slug = slug

        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("[OWNER] update failed")
        return api_error("server_error", "שגיאת שרת", status=500)

    new_slugs = [s for _, s in validated]
    logger.info(
        "[OWNER] Public examples updated by uid=%s: %s",
        current_user.id, new_slugs,
    )

    return api_ok({"ok": True, "count": len(validated)})
