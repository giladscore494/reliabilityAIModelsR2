"""AI-call utility helpers and compatibility wrappers."""

import logging
from typing import Dict, Optional, Tuple

from app.services.comparison.constants import COMPARISON_MODEL_ID
from app.services.comparison.metrics import _inc_compare_metric


logger = logging.getLogger(__name__)


def _estimate_token_count(text: str) -> int:
    return max(1, int(len(text or "") / 4))


def _is_output_too_long_error(raw: str) -> bool:
    lowered = (raw or "").lower()
    return (
        "answer candidate length is too long" in lowered
        or "maximum token limit" in lowered
        or "token limit of 8192" in lowered
    )


def _safe_ai_response_snippet(exc: Exception, max_len: int = 280) -> str:
    response = getattr(exc, "response", None)
    if response is None:
        return ""
    text = ""
    try:
        text = getattr(response, "text", "") or ""
        if not text:
            content = getattr(response, "content", b"")
            if isinstance(content, bytes):
                text = content.decode("utf-8", errors="ignore")
            elif content is not None:
                text = str(content)
    except Exception:
        text = ""
    text = " ".join(str(text).split())
    return text[:max_len]


def _log_ai_client_error(
    feature: str,
    exc: Exception,
    request_id: Optional[str] = None,
    log: Optional[logging.Logger] = None,
) -> None:
    status_code = (
        getattr(exc, "status_code", None)
        or getattr(exc, "code", None)
        or getattr(getattr(exc, "response", None), "status_code", None)
    )
    message = str(exc)
    reason = "output_too_long" if _is_output_too_long_error(message) else "client_error"
    _inc_compare_metric("compare_ai_failures_total", reason=reason)
    (log or logger).error(
        "[AI] request_id=%s feature=%s model=%s error_code=%s reason=%s error_type=%s response_snippet=%s",
        request_id or "unknown",
        feature,
        COMPARISON_MODEL_ID,
        status_code,
        reason,
        type(exc).__name__,
        _safe_ai_response_snippet(exc),
    )


def call_gemini_comparison(prompt: str, timeout_sec: int) -> Tuple[Optional[Dict], Optional[str]]:
    from app.services.comparison.grounding import call_gemini_comparison as _impl

    return _impl(prompt, timeout_sec)


def call_gemini_single_car(
    prompt: str,
    car_label: str,
    timeout_sec: int,
    request_id: Optional[str] = None,
    log: Optional[logging.Logger] = None,
) -> Tuple[Optional[Dict], Optional[str]]:
    from app.services.comparison.grounding import call_gemini_single_car as _impl

    return _impl(prompt, car_label, timeout_sec, request_id, log)


def generate_narrative(cars_selected_slots: Dict, computed_result: Dict, timeout_sec: int = 60) -> Optional[Dict]:
    from app.services.comparison.writer import generate_narrative as _impl

    return _impl(cars_selected_slots, computed_result, timeout_sec)
