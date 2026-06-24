"""Comparison Gemini model configuration and validation helpers."""

from __future__ import annotations

import logging
import os
from typing import Set

DEFAULT_COMPARISON_MODEL_ID = "gemini-3.1-pro-preview"
DEFAULT_COMPARISON_FALLBACK_MODEL_ID = "gemini-3.5-flash"
DEFAULT_COMPARISON_LOW_COST_MODEL_ID = "gemini-3.5-flash"

# Static allow-list based on Gemini API model ids that support generateContent
# for the comparison service's current text/JSON/search-grounding usage. Keep
# intentionally narrow so invented shorthand ids fail fast at startup.
SDK_SUPPORTED_COMPARISON_MODEL_IDS: Set[str] = {
    DEFAULT_COMPARISON_MODEL_ID,
    DEFAULT_COMPARISON_FALLBACK_MODEL_ID,
    DEFAULT_COMPARISON_LOW_COST_MODEL_ID,
    "gemini-3.1-flash-lite",
    "gemini-3.1-pro-preview",
}

COMPARISON_MODEL_ENV_VARS = (
    "COMPARISON_STAGE_A_MODEL",
    "COMPARISON_STAGE_A_REPAIR_MODEL",
    "COMPARISON_STAGE_B_MODEL",
    "COMPARISON_FALLBACK_MODEL",
    "COMPARISON_LOW_COST_MODEL",
)


class InvalidComparisonModelConfig(ValueError):
    """Raised when a configured comparison model id is not supported."""


def normalize_model_id(model_id: str | None) -> str:
    return (model_id or "").strip().removeprefix("models/")


def is_model_not_found_error(exc: Exception) -> bool:
    raw = f"{getattr(exc, 'status_code', '')} {getattr(exc, 'code', '')} {exc}"
    response = getattr(exc, "response", None)
    if response is not None:
        raw += f" {getattr(response, 'status_code', '')} {getattr(response, 'text', '')}"
    lowered = raw.lower()
    return (
        "404" in lowered
        or "not found" in lowered
        or "is not supported for generatecontent" in lowered
    )


def _configured_model(env_name: str, default: str) -> str:
    return normalize_model_id(os.environ.get(env_name, default))


def _comparison_default_model() -> str:
    # Backwards-compatible only: prefer explicit stage env vars, but allow the
    # old global env when a stage-specific value is absent. Never hardcode the
    # invalid gemini-3.1-pro family as a default.
    return normalize_model_id(os.environ.get("GEMINI_COMPARE_MODEL_ID", DEFAULT_COMPARISON_MODEL_ID))


def comparison_stage_a_model_id() -> str:
    return _configured_model("COMPARISON_STAGE_A_MODEL", _comparison_default_model())


def comparison_stage_a_repair_model_id() -> str:
    return _configured_model("COMPARISON_STAGE_A_REPAIR_MODEL", _comparison_default_model())


def comparison_stage_b_model_id() -> str:
    return _configured_model("COMPARISON_STAGE_B_MODEL", _comparison_default_model())


def comparison_safe_model_id() -> str:
    return comparison_fallback_model_id()


def comparison_fallback_model_id() -> str:
    return normalize_model_id(
        os.environ.get(
            "COMPARISON_FALLBACK_MODEL",
            os.environ.get("GEMINI_COMPARE_SAFE_MODEL_ID", DEFAULT_COMPARISON_FALLBACK_MODEL_ID),
        )
    )


def comparison_low_cost_model_id() -> str:
    return normalize_model_id(os.environ.get("COMPARISON_LOW_COST_MODEL", DEFAULT_COMPARISON_LOW_COST_MODEL_ID))


def validate_comparison_model_id(model_id: str, *, env_name: str = "model") -> str:
    normalized = normalize_model_id(model_id)
    if normalized not in SDK_SUPPORTED_COMPARISON_MODEL_IDS:
        allowed = ", ".join(sorted(SDK_SUPPORTED_COMPARISON_MODEL_IDS))
        raise InvalidComparisonModelConfig(
            f"Unsupported comparison Gemini model id for {env_name}: {model_id!r}. "
            f"Use one of: {allowed}"
        )
    return normalized


def validate_comparison_model_config(log: logging.Logger | None = None) -> None:
    configured = {
        "COMPARISON_STAGE_A_MODEL": comparison_stage_a_model_id(),
        "COMPARISON_STAGE_A_REPAIR_MODEL": comparison_stage_a_repair_model_id(),
        "COMPARISON_STAGE_B_MODEL": comparison_stage_b_model_id(),
        "COMPARISON_FALLBACK_MODEL": comparison_fallback_model_id(),
        "COMPARISON_LOW_COST_MODEL": comparison_low_cost_model_id(),
    }
    for env_name, model_id in configured.items():
        validate_comparison_model_id(model_id, env_name=env_name)
    if log:
        log.info(
            "[AI] comparison_model_config_valid stage_a_model=%s stage_a_repair_model=%s stage_b_model=%s fallback_model=%s low_cost_model=%s",
            configured["COMPARISON_STAGE_A_MODEL"],
            configured["COMPARISON_STAGE_A_REPAIR_MODEL"],
            configured["COMPARISON_STAGE_B_MODEL"],
            configured["COMPARISON_FALLBACK_MODEL"],
            configured["COMPARISON_LOW_COST_MODEL"],
        )
