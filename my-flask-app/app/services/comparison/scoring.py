"""Deterministic scoring (code-only — no AI).

Re-export façade — see :mod:`app.services.comparison` package docstring.

The scoring weights and ordinal score tables are sourced directly from the
legacy implementation module. Importing them through this façade is the
recommended path for new code.
"""

from app.services.comparison_service import (  # noqa: F401
    CATEGORY_WEIGHTS,
    ORDINAL_SCORES_NEGATIVE,
    ORDINAL_SCORES_POSITIVE,
    score_ordinal_negative,
    score_ordinal_positive,
    compute_category_score,
    compute_overall_score,
    determine_winner,
    compute_comparison_results,
)

__all__ = [
    "CATEGORY_WEIGHTS",
    "ORDINAL_SCORES_NEGATIVE",
    "ORDINAL_SCORES_POSITIVE",
    "score_ordinal_negative",
    "score_ordinal_positive",
    "compute_category_score",
    "compute_overall_score",
    "determine_winner",
    "compute_comparison_results",
]
