# -*- coding: utf-8 -*-
"""Live Level 2 enrichment diagnostic for ONE demo vehicle (paid Gemini calls).

Usage (server-side, GEMINI_API_KEY set):
    python -m scripts.enrichment_diagnostic audi
    python -m scripts.enrichment_diagnostic bmw technical          # one task only
    python -m scripts.enrichment_diagnostic tucson price,warranty
    python -m scripts.enrichment_diagnostic production             # Audi Q3 + BMW i4 + Mercedes CLE, concurrently
    python -m scripts.enrichment_diagnostic audi,bmw,cle

Runs exactly the production enrichment path for a single car — the same
``GeminiOfficialEnrichmentProvider`` (model from COMPARISON_ENRICHMENT_MODEL,
Google Search + URL context, task-scoped JSON schema, thinking level, output
ceiling, provider HTTP timeout, no SDK retry, grounding-redirect resolution),
the same task split, the same ``FieldValidator`` / grounding correlation and
the same freshness classification — without JEV or the summary. The cache is
an in-process one, so nothing is written to the production cache; the report
shows the decision production WOULD take.

Prints safe metadata only (the same ``enrichment_report`` production logs as
``vehicle_enrichment_completed``, plus per-field detail). The API key, the
prompt and the raw model response are never printed.
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict

ALIASES = {
    "audi": "182116040879ae9539249b243610e1db750726454cbe0eb645a7b696bf96abdf",
    "bmw": "6107a336e30d19eca5b76f5b7ca1c6ed8b67ee6a4c70c3040a0e3b34708c66e2",
    "tucson": "16bb9f3825573c1745b6741cd18b23814b0c8315e16a898daf2f2092e1e57572",
    "xpeng": "0195742780b50926c1f19aeca63601d1f6c4b17aeaa063f3dbdc00df6bd24b88",
    "sienna": "1fe0f557428d8114fbf3af6a317fe8a0972d8e2e7a7bdbd7915051a891d14bd2",
    "cle": "ebbcdf940d27890fc82da0264e12b23f2a0eb86ca8faaad156ed11c08498bd20",
    "escalade": "c3249a13a6e515e4be50fb50bb15b653552d09b2f781027b7e080c8987bfc735",
}
# The three vehicles of the production run at 3d62dda.
GROUPS_OF_NAMES = {"production": ("audi", "bmw", "cle")}


def build_report(snapshot, outcome, meta, provider) -> dict:
    """Pure function (unit-tested): diagnostic report from a production outcome."""
    from app.services.comparison_v2.official_enrichment import (
        enrichment_max_output_tokens,
        enrichment_report,
        enrichment_thinking_level,
        plan_tasks,
        requested_fields,
    )

    family = snapshot["derived"]["powertrain_family"]
    groups = meta.get("groups") or []
    facts = outcome.get("facts") or {}
    rejected_by_reason = defaultdict(list)
    for r in outcome.get("rejected_claims") or []:
        item = {"field": r.get("field"), "host": r.get("host"), "scope": (r.get("variant_match") or {}).get("scope")}
        if r.get("reason") == "VARIANT_YEAR_MISMATCH":
            item["years"] = (r.get("variant_match") or {}).get("years")
        rejected_by_reason[r.get("reason") or "UNKNOWN"].append(item)
    report = {
        "vehicle": snapshot["identity"]["display_name"],
        "powertrain_family": family,
        "model": provider.model_id,
        "config": {
            "provider_timeout_sec": provider.timeout_sec,
            "url_context": getattr(provider, "url_context", None),
            "resolve_grounding_redirects": getattr(provider, "resolve_redirects", None),
            "thinking_level": enrichment_thinking_level(),
            "max_output_tokens": enrichment_max_output_tokens(),
            "tasks": [{"task": name, "groups": list(g)} for name, g in plan_tasks(groups)],
        },
        "requested_fields": requested_fields(family, groups),
        "summary": enrichment_report(snapshot, outcome, meta),
        "tasks": outcome.get("tasks") or [],
        "accepted": {
            k: {
                "value": f.get("value"),
                "unit": f.get("unit"),
                "host": (f.get("source_url") or "").split("/")[2] if f.get("source_url") else None,
                "market": f.get("source_market"),
                "grounding_tier": f.get("grounding_tier"),
                "identity_match": f.get("identity_match"),
                "identity_scope": f.get("identity_scope"),
                "vehicle_model_year": f.get("vehicle_model_year"),
                "source_publication_year": f.get("source_publication_year"),
                "measurement_standard": f.get("measurement_standard"),
            }
            for k, f in facts.items()
        },
        "rejected_by_reason": dict(rejected_by_reason),
        "model_generic": [{"field": r.get("field"), "host": r.get("host")} for r in outcome.get("model_generic_claims") or []],
        "missing": outcome.get("missing") or [],
        "conflicts": [c.get("field") for c in outcome.get("conflicts") or []],
        "grounded_official_hosts": outcome.get("grounded_official_hosts") or [],
        "ignored_grounding_hosts": outcome.get("ignored_grounding_hosts") or [],
        "group_freshness": outcome.get("group_freshness") or {},
        "cache_decision": {
            "cache_write": outcome.get("cache_write"),
            "fresh_groups_marked": outcome.get("fresh_groups_written") or [],
            "level2_health": outcome.get("level2_health"),
            "whole_comparison_cache_eligible": outcome.get("level2_health") in ("complete", "partial", "empty"),
        },
        "provider_diagnostics": outcome.get("provider_diagnostics"),
    }
    return report


def main(argv) -> int:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from google import genai

    from app.services.comparison.model_config import comparison_enrichment_model_id
    from app.services.comparison_v2.cache import InProcessEnrichmentCache
    from app.services.comparison_v2.demo_catalog import DemoVehicleCatalogRepository
    from app.services.comparison_v2.level15 import build_level15_snapshot
    from app.services.comparison_v2.official_enrichment import (
        ALL_FRESHNESS_GROUPS,
        GeminiOfficialEnrichmentProvider,
        LiveOfficialEnrichmentRepository,
        enrich_many,
    )

    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        print("GEMINI_API_KEY is not set.")
        return 2
    arg = (argv[1] if len(argv) > 1 else "audi").lower()
    names = list(GROUPS_OF_NAMES.get(arg, arg.split(",")))
    groups = tuple(g for g in (argv[2].split(",") if len(argv) > 2 else ALL_FRESHNESS_GROUPS) if g in ALL_FRESHNESS_GROUPS)
    catalog = DemoVehicleCatalogRepository()
    snapshots = []
    for i, name in enumerate(names):
        record = catalog.get_variant(ALIASES.get(name, name))
        if not record:
            print(f"unknown vehicle: {name}")
            return 2
        snapshots.append(build_level15_snapshot(record, f"car_{i + 1}"))
    provider = GeminiOfficialEnrichmentProvider(genai.Client(api_key=key), model_id=comparison_enrichment_model_id())
    cache = InProcessEnrichmentCache()
    repo = LiveOfficialEnrichmentRepository(provider, cache)
    if set(groups) != set(ALL_FRESHNESS_GROUPS):
        # Restrict the run to the requested groups by marking the others fresh.
        original_plan = repo.plan
        repo.plan = lambda snap: (original_plan(snap)[0], groups)  # type: ignore[assignment]
    results = enrich_many(repo, snapshots)
    reports = [build_report(snapshot, outcome, meta, provider) for snapshot, (outcome, meta) in zip(snapshots, results)]
    print(json.dumps(reports[0] if len(reports) == 1 else reports, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
