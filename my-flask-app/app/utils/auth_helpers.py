# -*- coding: utf-8 -*-
"""Owner authentication helpers."""

import os
import functools

from flask import abort, current_app
from flask_login import current_user


def is_owner(user=None):
    """
    Return True if *user* is the site owner.

    Checks both:
      - OWNER_EMAIL env var (single address)
      - OWNER_EMAILS set in app config (multi-address, already used elsewhere)
    """
    u = user if user is not None else current_user
    if not getattr(u, "is_authenticated", False):
        return False
    email = (getattr(u, "email", "") or "").lower().strip()
    if not email:
        return False

    # Single OWNER_EMAIL env var
    single = os.environ.get("OWNER_EMAIL", "").lower().strip()
    if single and email == single:
        return True

    # Multi OWNER_EMAILS from app config
    owner_set = current_app.config.get("OWNER_EMAILS", set())
    if email in owner_set:
        return True

    return False


def owner_required(f):
    """
    Decorator: return 404 (not 403) for non-owners.

    Must be stacked *after* @login_required so that
    current_user is guaranteed to be authenticated.
    """
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not is_owner(current_user):
            abort(404)
        return f(*args, **kwargs)
    return decorated
