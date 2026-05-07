"""Comparison metrics helpers."""

from typing import Optional

from app.services.comparison.constants import COMPARE_AI_METRICS


def _inc_compare_metric(metric: str, reason: Optional[str] = None) -> None:
    if metric == "compare_ai_failures_total":
        bucket = COMPARE_AI_METRICS.setdefault(metric, {})
        key = reason or "unknown"
        bucket[key] = int(bucket.get(key, 0)) + 1
        return
    COMPARE_AI_METRICS[metric] = int(COMPARE_AI_METRICS.get(metric, 0)) + 1
