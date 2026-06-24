"""Production observability helpers for review and comparison tools."""

from __future__ import annotations

import logging
import os
import signal
import time
from datetime import datetime, timezone

_BOOT_MONOTONIC = time.monotonic()
_BOOT_ISO = datetime.now(timezone.utc).isoformat()
_LOG = logging.getLogger(__name__)


def env_ms(name: str, default_ms: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default_ms
    try:
        return max(1, int(raw))
    except ValueError:
        _LOG.warning("[CONFIG] invalid integer env %s=%r using_default=%s", name, raw, default_ms)
        return default_ms


def log_slow_operation(logger, *, feature: str, stage: str, duration_ms: int, request_id: str | None = None) -> None:
    timeout_ms = env_ms("GUNICORN_WORKER_TIMEOUT_MS", int(os.environ.get("WEB_CONCURRENCY_TIMEOUT_MS", "120000")))
    if stage in ("model_call", "stage_a", "stage_b") and duration_ms > 45000:
        logger.warning("[SLOW_AI] feature=%s stage=%s duration_ms=%s threshold_ms=45000 request_id=%s", feature, stage, duration_ms, request_id or "unknown")
    if stage == "total" and duration_ms > 60000:
        logger.warning("[SLOW_REQUEST] feature=%s duration_ms=%s threshold_ms=60000 request_id=%s", feature, duration_ms, request_id or "unknown")
    if timeout_ms and duration_ms >= int(timeout_ms * 0.8):
        logger.warning("[WORKER_TIMEOUT_NEAR] feature=%s stage=%s duration_ms=%s worker_timeout_ms=%s request_id=%s", feature, stage, duration_ms, timeout_ms, request_id or "unknown")


def log_boot_metadata(logger) -> None:
    render_keys = ["RENDER", "RENDER_SERVICE_ID", "RENDER_SERVICE_NAME", "RENDER_INSTANCE_ID", "RENDER_GIT_COMMIT", "RENDER_EXTERNAL_URL"]
    logger.info(
        "[BOOT] pid=%s worker_id=%s app_version=%s commit_sha=%s boot_ts=%s render_meta=%s",
        os.getpid(),
        os.environ.get("GUNICORN_WORKER_ID") or os.environ.get("WEB_CONCURRENCY") or "unknown",
        os.environ.get("APP_VERSION", "unknown"),
        os.environ.get("RENDER_GIT_COMMIT") or os.environ.get("COMMIT_SHA") or os.environ.get("GIT_COMMIT") or "unknown",
        _BOOT_ISO,
        {k: os.environ.get(k) for k in render_keys if os.environ.get(k)},
    )


def install_shutdown_logging(logger) -> None:
    def _handler(signum, frame):  # pragma: no cover - signal path
        logger.warning(
            "[SHUTDOWN] signal=%s pid=%s uptime_seconds=%.2f boot_ts=%s",
            signum,
            os.getpid(),
            time.monotonic() - _BOOT_MONOTONIC,
            _BOOT_ISO,
        )
        previous = signal.getsignal(signum)
        if callable(previous) and previous is not _handler:
            previous(signum, frame)
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handler)
        except Exception:
            logger.debug("[SHUTDOWN] signal handler install failed sig=%s", sig, exc_info=True)
