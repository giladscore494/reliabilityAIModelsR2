"""Comparison service package (Phase 5 of the infra refactor).

The comparison logic historically lives in :mod:`app.services.comparison_service`
as a single ~5,600-line module. This package establishes the canonical, smaller
modules described in the refactor brief and re-exports the existing public
surface so all current call-sites keep working unchanged. The intent is that
future changes can land in the appropriate sub-module without touching the
legacy mega-module.

Sub-module responsibilities (as specified in the brief):

- :mod:`pipeline`       — orchestration only (``handle_comparison_request``)
- :mod:`prompts`        — prompt builders only
- :mod:`grounding`      — Stage A factual extraction / grounded model calls
- :mod:`writer`         — Stage B narrative generation
- :mod:`scoring`        — deterministic scoring
- :mod:`normalization`  — vehicle / source / label normalization
- :mod:`cache`          — request hash / safe parse cache
- :mod:`history`        — DB persistence / read helpers
- :mod:`schemas`        — internal validation helpers
- :mod:`fallbacks`      — fallback payloads

Every public name from ``comparison_service`` is re-exported here so callers
may use either path:

    from app.services import comparison_service          # legacy, still works
    from app.services.comparison import handle_comparison_request  # new
    from app.services.comparison.scoring import compute_overall_score
"""

from app.services import comparison_service as _impl
from app.services.comparison_service import *  # noqa: F401,F403 — re-export public API

# Convenience explicit re-exports for the main public surface used by routes.
from app.services.comparison_service import (  # noqa: F401
    handle_comparison_request,
    get_comparison_history,
    get_comparison_detail,
    regenerate_comparison_ai,
)

__all__ = [name for name in dir(_impl) if not name.startswith("_")]
