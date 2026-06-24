# -*- coding: utf-8 -*-
"""PostHog analytics helper – silent no-op when POSTHOG_API_KEY is not set."""

import logging
import os

logger = logging.getLogger(__name__)

_posthog_client = None
_posthog_enabled = False


def init_posthog(app):
    """Initialise PostHog once at app startup. Call from factory.py."""
    global _posthog_client, _posthog_enabled

    api_key = os.environ.get("POSTHOG_API_KEY", "").strip()
    host = os.environ.get("POSTHOG_HOST", "https://us.i.posthog.com").strip()

    if not api_key:
        _posthog_client = None
        _posthog_enabled = False
        logger.info("[POSTHOG] No POSTHOG_API_KEY – analytics disabled (no-op mode)")
        logger.info(
            "[POSTHOG] server initialization status enabled=%s host=%s",
            False,
            host,
        )
        return

    try:
        import posthog
        posthog.api_key = api_key
        posthog.host = host
        posthog.debug = False
        posthog.on_error = lambda e, batch: logger.warning(
            "[POSTHOG] batch error host=%s error=%s batch_size=%s",
            host,
            e,
            len(batch) if batch is not None else 0,
        )
        _posthog_client = posthog
        _posthog_enabled = True
        logger.info("[POSTHOG] Initialised (host=%s)", host)
    except Exception:
        _posthog_client = None
        _posthog_enabled = False
        logger.exception("[POSTHOG] Failed to import posthog – analytics disabled")
    logger.info(
        "[POSTHOG] server initialization status enabled=%s host=%s",
        _posthog_enabled,
        host,
    )


def _capture_compat(client, distinct_id, event_name, props):
    """Call PostHog capture across SDK versions.

    Newer SDKs require keyword arguments; older/global clients accepted
    positional distinct_id/event. Keep both paths isolated so analytics can
    never break an application request.
    """
    try:
        return client.capture(distinct_id=distinct_id, event=event_name, properties=props)
    except TypeError as first_error:
        try:
            return client.capture(distinct_id, event_name, properties=props)
        except TypeError:
            raise first_error


def track_event(distinct_id, event_name, properties=None):
    """Fire a PostHog event; swallow all failures."""
    if not _posthog_enabled or not _posthog_client:
        return
    try:
        props = dict(properties) if properties else {}
        _capture_compat(_posthog_client, distinct_id, event_name, props)
    except Exception as exc:
        log_fn = logger.exception if os.environ.get("DEBUG_ANALYTICS", "").lower() in ("1", "true", "yes") else logger.warning
        log_fn(
            "[POSTHOG] capture failed event=%s error=%s distinct_id_present=%s",
            event_name,
            type(exc).__name__,
            bool(distinct_id),
        )
