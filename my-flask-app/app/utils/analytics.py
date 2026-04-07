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
        _posthog_enabled = False
        logger.info("[POSTHOG] No POSTHOG_API_KEY – analytics disabled (no-op mode)")
        return

    try:
        import posthog
        posthog.api_key = api_key
        posthog.host = host
        posthog.debug = False
        posthog.on_error = lambda e, batch: None  # swallow errors
        _posthog_client = posthog
        _posthog_enabled = True
        logger.info("[POSTHOG] Initialised (host=%s)", host)
    except Exception:
        _posthog_enabled = False
        logger.info("[POSTHOG] Failed to import posthog – analytics disabled")


def track_event(distinct_id, event_name, properties=None):
    """
    Fire a PostHog event.  Swallows ALL exceptions so analytics never
    breaks a request.
    """
    if not _posthog_enabled or not _posthog_client:
        return
    try:
        props = dict(properties) if properties else {}
        _posthog_client.capture(distinct_id, event_name, properties=props)
    except Exception:
        pass
