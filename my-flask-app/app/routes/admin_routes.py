# -*- coding: utf-8 -*-
"""Admin-only operational health endpoints."""

from flask import Blueprint
from flask_login import login_required

from app.extensions import GEMINI_RELIABILITY_MODEL_ID, GEMINI_RECOMMENDER_MODEL_ID
from app.services.comparison.model_config import (
    comparison_stage_a_model_id,
    comparison_stage_a_repair_model_id,
    comparison_safe_model_id,
    comparison_stage_b_model_id,
)
from app.services.gemini_grounding_client import call_grounded_model, call_plain_model
from app.utils.auth_helpers import owner_required
from app.utils.http_helpers import api_ok

bp = Blueprint("admin", __name__)


def _check_result(result, *, require_grounding: bool = False):
    err = result.get("error_code") if isinstance(result, dict) else "EMPTY_RESULT"
    text = (result.get("text") or "") if isinstance(result, dict) else ""
    meta = result.get("grounding_meta") if isinstance(result, dict) else {}
    grounding_ok = bool((meta or {}).get("grounding_successful"))
    ok = not err and bool(text.strip()) and (grounding_ok if require_grounding else True)
    return {"ok": ok, "error": err if err else (None if ok else "NO_GROUNDING_METADATA")}


@bp.route("/api/admin/gemini-health", methods=["GET"])
@login_required
@owner_required
def gemini_health():
    """Separate plain Gemini model access from Google Search grounding access."""
    plain = call_plain_model(
        GEMINI_RELIABILITY_MODEL_ID,
        "Return OK",
        timeout_sec=15,
        max_output_tokens=16,
        temperature=0.0,
    )
    grounded = call_grounded_model(
        GEMINI_RELIABILITY_MODEL_ID,
        "Search Google for the current year and return OK",
        timeout_sec=20,
    )
    return api_ok(
        {
            "plain": _check_result(plain),
            "grounded": _check_result(grounded, require_grounding=True),
            "configured_models": {
                "reliability": GEMINI_RELIABILITY_MODEL_ID,
                "recommender": GEMINI_RECOMMENDER_MODEL_ID,
                "comparison_stage_a": comparison_stage_a_model_id(),
                "comparison_stage_a_repair": comparison_stage_a_repair_model_id(),
                "comparison_stage_b": comparison_stage_b_model_id(),
                "comparison_safe": comparison_safe_model_id(),
            },
        }
    )
