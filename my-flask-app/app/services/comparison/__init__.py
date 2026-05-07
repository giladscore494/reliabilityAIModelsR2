"""Comparison service package.

Submodules contain extracted comparison helpers. Legacy package-level imports
are resolved lazily to avoid circular imports while ``comparison_service`` moves
real code into this package.
"""


def __getattr__(name):
    from app.services import comparison_service as _impl

    try:
        return getattr(_impl, name)
    except AttributeError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
