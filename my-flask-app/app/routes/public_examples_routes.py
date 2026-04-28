# -*- coding: utf-8 -*-
"""Public example routes – anonymous access to pre-generated analyses."""

import json

from flask import Blueprint, render_template
from flask_login import current_user

from app.models import SearchHistory
from app.utils.http_helpers import api_ok, is_owner_user
from app.utils.sanitization import sanitize_analyze_response

bp = Blueprint('examples', __name__)


@bp.route('/example/<slug>')
def example_detail(slug):
    """Render a full public example analysis page. No auth required."""
    row = SearchHistory.query.filter_by(
        is_public_example=True,
        example_slug=slug,
    ).first_or_404()

    # Parse stored result JSON
    try:
        if isinstance(row.result_json, str):
            result_data = json.loads(row.result_json)
        else:
            result_data = row.result_json
    except (json.JSONDecodeError, TypeError):
        result_data = {}

    return render_template(
        'example.html',
        example=row,
        result_data=result_data,
        slug=slug,
        user=current_user,
        is_owner=is_owner_user(),
    )


@bp.route('/api/examples')
def list_examples():
    """Return JSON list of all public examples for landing cards."""
    rows = SearchHistory.query.filter_by(is_public_example=True).all()
    examples = []
    for r in rows:
        try:
            if isinstance(r.result_json, str):
                rj = json.loads(r.result_json)
            else:
                rj = r.result_json or {}
        except (json.JSONDecodeError, TypeError):
            rj = {}

        rj = sanitize_analyze_response(rj)
        summary = (
            rj.get("reliability_summary_simple")
            or rj.get("reliability_summary")
            or ""
        )
        if isinstance(summary, str) and len(summary) > 160:
            summary = summary[:157] + "..."

        examples.append({
            "slug": r.example_slug,
            "make": r.make,
            "model": r.model,
            "year": r.year,
            "label": f"{r.make} {r.model} {r.year}",
            "summary": summary,
            "data_quality_label": rj.get("data_quality_label"),
            "decision_readiness": rj.get("decision_readiness"),
            "top_missing_info": (rj.get("missing_critical_info") or [None])[0],
            "top_verification_focus": (rj.get("verification_focus") or [None])[0],
        })
    return api_ok({"examples": examples})
