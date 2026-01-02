"""Application entrypoint.

This module intentionally contains minimal logic.
The Flask application factory is expected to live in `legacy_main.py`.

Why:
- Keeps backwards-compatible app factory code in a dedicated module.
- Allows other tooling (gunicorn, tests) to import `create_app` reliably.
"""

from __future__ import annotations


def _missing_legacy_main_error(exc: Exception) -> ImportError:
    return ImportError(
        "Unable to import `create_app` from `my-flask-app/legacy_main.py`. "
        "This repository expects the Flask app factory to be defined in "
        "`my-flask-app/legacy_main.py` as `create_app()`. "
        "\n\nIf you recently refactored files, restore or create that module, "
        "or update this entrypoint to point to the correct location."
    ) from exc


try:
    # NOTE: This file lives under `my-flask-app/`, which is not a valid Python
    # package name due to the hyphen. Many projects still run it as a script.
    # We therefore use a relative import fallback via direct module import.
    #
    # If you have a proper package, update imports accordingly.
    from legacy_main import create_app  # type: ignore
except Exception as exc:  # pragma: no cover
    raise _missing_legacy_main_error(exc)


__all__ = ["create_app"]
