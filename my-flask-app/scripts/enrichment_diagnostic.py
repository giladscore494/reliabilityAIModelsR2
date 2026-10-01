# -*- coding: utf-8 -*-
"""Live Level 2 enrichment diagnostic for ONE demo vehicle (one paid Gemini call).

Usage (server-side, GEMINI_API_KEY set):
    python -m scripts.enrichment_diagnostic audi
    python -m scripts.enrichment_diagnostic bmw

Runs exactly the production path for a single car — the same provider config
(Google Search grounding, JSON schema, low thinking, provider HTTP timeout, no
SDK retry) and the same deterministic validation — without the cache, JEV or
the summary. Prints safe metadata only: latency, parse source, grounding
counts, accepted fields with their source hosts, rejection reasons, and the
INVALID_JSON diagnostics when applicable. The API key and the prompt are never
printed.
"""

from __future__ import annotations

import json
import os
import sys

ALIASES = {
    "audi": "182116040879ae9539249b243610e1db750726454cbe0eb645a7b696bf96abdf",
    "bmw": "6107a336e30d19eca5b76f5b7ca1c6ed8b67ee6a4c70c3040a0e3b34708c66e2",
    "tucson": "16bb9f3825573c1745b6741cd18b23814b0c8315e16a898daf2f2092e1e57572",
    "xpeng": "0195742780b50926c1f19aeca63601d1f6c4b17aeaa063f3dbdc00df6bd24b88",
    "sienna": "1fe0f557428d8114fbf3af6a317fe8a0972d8e2e7a7bdbd7915051a891d14bd2",
    "cle": "ebbcdf940d27890fc82da0264e12b23f2a0eb86ca8faaad156ed11c08498bd20",
    "escalade": "c3249a13a6e515e4be50fb50bb15b653552d09b2f781027b7e080c8987bfc735",
}


def main(argv) -> int:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from google import genai

    from app.services.comparison_v2.cache import InProcessEnrichmentCache
    from app.services.comparison_v2.demo_catalog import DemoVehicleCatalogRepository
    from app.services.comparison_v2.level15 import build_level15_snapshot
    from app.services.comparison_v2.official_enrichment import (
        GeminiOfficialEnrichmentProvider,
        LiveOfficialEnrichmentRepository,
        enrich_many,
    )

    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        print("GEMINI_API_KEY is not set.")
        return 2
    name = (argv[1] if len(argv) > 1 else "audi").lower()
    variant = ALIASES.get(name, name)
    record = DemoVehicleCatalogRepository().get_variant(variant)
    if not record:
        print(f"unknown vehicle: {name}")
        return 2
    snapshot = build_level15_snapshot(record, "car_1")
    provider = GeminiOfficialEnrichmentProvider(genai.Client(api_key=key))
    repo = LiveOfficialEnrichmentRepository(provider, InProcessEnrichmentCache())
    outcome, meta = enrich_many(repo, [snapshot])[0]

    facts = outcome.get("facts") or {}
    report = {
        "vehicle": snapshot["identity"]["display_name"],
        "model": provider.model_id,
        "provider_timeout_sec": provider.timeout_sec,
        "latency_ms": meta.get("duration_ms"),
        "status": outcome.get("status"),
        "error_code": outcome.get("error_code"),
        "parse_source": meta.get("parse_source"),
        "grounded_sources": meta.get("grounded_source_count"),
        "grounding": meta.get("grounding"),
        "grounded_official_hosts": outcome.get("grounded_official_hosts"),
        "ignored_grounding_hosts": outcome.get("ignored_grounding_hosts"),
        "accepted": {k: {"value": f.get("value"), "unit": f.get("unit"), "host": (f.get("source_url") or "").split("/")[2] if f.get("source_url") else None,
                         "market": f.get("source_market")} for k, f in facts.items()},
        "rejected": [{"field": r.get("field"), "reason": r.get("reason"), "host": r.get("host")} for r in outcome.get("rejected_claims") or []],
        "model_generic": [{"field": r.get("field"), "host": r.get("host")} for r in outcome.get("model_generic_claims") or []],
        "conflicts": [c.get("field") for c in outcome.get("conflicts") or []],
        "government_conflicts": [c.get("field") for c in outcome.get("government_conflicts") or []],
        "provider_diagnostics": outcome.get("provider_diagnostics"),
    }
    print(json.dumps(report, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
