"""Module-level configuration constants for the Flask app.

Extracted from ``app.factory`` (Phase 3 of the maintainability refactor).

Backward compatibility: ``app.factory`` re-exports every name defined here, so
existing ``from app.factory import GLOBAL_DAILY_LIMIT`` (etc.) imports continue
to work. New code should prefer ``from app.config import ...``.

All env-var names are preserved exactly to avoid changing deployment surface.
"""

from __future__ import annotations

import atexit
import concurrent.futures
import os

# --- AI / model timing ---
AI_CALL_TIMEOUT_SEC = max(1, int(os.environ.get("RELIABILITY_AI_TIMEOUT_MS", "170000")) // 1000) if os.environ.get("RELIABILITY_AI_TIMEOUT_MS") else int(os.environ.get("AI_CALL_TIMEOUT_SEC", "170"))
AI_EXECUTOR_WORKERS = int(os.environ.get("AI_EXECUTOR_WORKERS", "8"))
AI_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=AI_EXECUTOR_WORKERS)
atexit.register(lambda: AI_EXECUTOR.shutdown(wait=True))
WEB_GROUNDING_PROVIDER = os.environ.get("WEB_GROUNDING_PROVIDER", "gemini_search")
ALLOW_EXTERNAL_SEARCH_GROUNDING = os.environ.get("ALLOW_EXTERNAL_SEARCH_GROUNDING", "false").lower() == "true"

# --- Quota / rate-limit ---
GLOBAL_DAILY_LIMIT = 1000
USER_DAILY_LIMIT = int(os.environ.get("QUOTA_LIMIT", "5"))
MAX_CACHE_DAYS = 45
PER_IP_PER_MIN_LIMIT = 20
QUOTA_RESERVATION_TTL_SECONDS = int(os.environ.get("QUOTA_RESERVATION_TTL_SECONDS", "600"))
MAX_ACTIVE_RESERVATIONS = 1

# --- HTTP payload limits ---
MAX_CONTENT_LENGTH_DEFAULT = 8 * 1024 * 1024
DEFAULT_API_PAYLOAD_LIMIT_BYTES = 256 * 1024

__all__ = [
    "AI_CALL_TIMEOUT_SEC",
    "AI_EXECUTOR_WORKERS",
    "AI_EXECUTOR",
    "WEB_GROUNDING_PROVIDER",
    "ALLOW_EXTERNAL_SEARCH_GROUNDING",
    "GLOBAL_DAILY_LIMIT",
    "USER_DAILY_LIMIT",
    "MAX_CACHE_DAYS",
    "PER_IP_PER_MIN_LIMIT",
    "QUOTA_RESERVATION_TTL_SECONDS",
    "MAX_ACTIVE_RESERVATIONS",
    "MAX_CONTENT_LENGTH_DEFAULT",
    "DEFAULT_API_PAYLOAD_LIMIT_BYTES",
]
