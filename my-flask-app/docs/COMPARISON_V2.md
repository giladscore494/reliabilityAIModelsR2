# Comparison V2 (`comparison-v2/1`)

Feature flag: `COMPARISON_V2_ENABLED` (default `false`). With the flag off the
legacy single-pass flow is untouched; with it on, `/compare` shows the exact
variant picker and `POST /api/compare` runs the V2 pipeline.

## Invariant

```
Gemini may FIND and EXTRACT facts.
Code decides whether those facts are valid.
Code performs objective comparisons.
JEV performs structured judgment.
Gemini explains the result.
```

## Flow

```
validate request (exact variant_identity_key per car)
 -> resolve variants              VehicleCatalogRepository (DemoVehicleCatalogRepository today, MiloCatalogRepository later)
 -> Level 1.5 snapshots           level15.build_level15_snapshot  (MoT facts + 19 ADAS flags, null stays null)
 -> official enrichment per car   OfficialEnrichmentRepository.get_or_enrich
                                  (per-car cache first; at most ONE grounded Gemini call per car, concurrent)
 -> deterministic validation      field_validator.FieldValidator
                                  (contract field, registry allowlist, grounding corroboration, Israeli-only fields,
                                   variant scope, OfficialVariantMatcher, units, ranges, applicability, conflicts)
 -> canonical merge               CanonicalVehicleSnapshot (Level 1.5 never overwritten; field-level provenance)
 -> deterministic comparison      deterministic_engine (MetricRegistry -> atomic results + CategoryEvidence + coverage;
                                  no category or overall decision, no score)
 -> JEV                           ONE POST /v1/systemone with every applicable category + overall as `choice` questions
 -> summary                       ONE ungrounded Gemini call; validated; deterministic Hebrew fallback
 -> persist                       comparison_history (prompt_version = comparison-v2/1)
 -> response / NDJSON progress stream
```

Progress stages (streamed when the client sends `Accept: application/x-ndjson`):
`resolving_vehicles, loading_government_data, enriching_car_N, validating_sources,
comparing_facts, evaluating_decision, writing_summary, complete`.

## Modules (`app/services/comparison_v2/`)

| module | role |
|---|---|
| `contracts.py` | versions, vocabulary, `VehicleCatalogRepository` / `OfficialEnrichmentRepository` boundaries |
| `demo_catalog.py` | 7 Level 1.5 fixtures from MILO Production (`app/data/comparison_v2_demo_catalog.json`) |
| `source_registry.py` | `OFFICIAL_SOURCE_REGISTRY` (`official-source-registry/1`): allowed hosts, seed URLs, brand notes, `is_allowed_official_url` |
| `level15.py` | canonical snapshot builder |
| `field_registry.py` | Level 2 field contracts (units, conversions, ranges, applicability, Israeli-only, freshness group) |
| `official_variant_matcher.py` | deterministic variant identity check (model code, or model+year+trim+propulsion+drivetrain+engine) |
| `field_validator.py` | claim validation + merge + conflicts |
| `official_enrichment.py` | prompt, Gemini provider (Google Search + JSON schema), per-car cache planning, concurrent fetch |
| `cache.py` | `VehicleOfficialEnrichmentCache` (in-process + `vehicle_official_enrichment_cache` table), field-group freshness |
| `deterministic_engine.py` | `MetricRegistry`, atomic comparisons, `CategoryEvidence`, coverage |
| `jev_client.py` | TypeSafe client, model verification via `GET /v1/models`, request builder, response parsing |
| `summary_writer.py` | Gemini summary + validator + deterministic fallback |
| `pipeline.py` | orchestration generator, whole-comparison history cache, default wiring |

## Source enforcement

* Only https URLs whose host equals an allowed host, or is a subdomain of an
  allowed host marked `subdomains=True` (`bmw.co.il.evil.com` is rejected).
* The cited host must also appear in the Google Search grounding metadata of
  that call (`SOURCE_NOT_GROUNDED` otherwise).
* Market (IL / GLOBAL) comes from the registry, never from the model.
  Price, registration fee and warranty require an Israeli official host.
* `seed_urls` are prompt starting points only; they never make a claim valid.

## Missing data

`missing != zero`, `missing != worse`. A car without a value is excluded from
that metric; fewer than two values means `insufficient_data`. Coverage is
reported separately and never becomes an advantage.

## Caching and cost

| situation | remote calls |
|---|---|
| cold, 2 cars | 2 enrichment + 1 JEV + 1 summary |
| Level 2 cache hit | 1 JEV + 1 summary |
| whole comparison cached (24h, same cars + buyer profile + versions) | 0 |
| `COMPARISON_V2_OFFLINE_MODE=true` | 0 |

TTL per field group: technical 30 days, price 24 hours, warranty 7 days. A
stale group triggers one call for that group only. `GET /v1/models` is
called to verify `JEV_MODEL` and cached in-process for 6 hours.

No retries: one enrichment attempt per car, one JEV attempt, one summary
attempt. Enrichment failure -> Level 1.5 only. JEV failure ->
`decision_unavailable` and deterministic facts. Summary failure/rejection ->
deterministic Hebrew template.

## JEV request (redacted)

```
POST {TYPESAFE_BASE_URL}/v1/systemone
Authorization: Bearer [REDACTED]
{
  "model": "<JEV_MODEL verified via GET /v1/models>",
  "state": {
    "car_1": {"identity": {...}, "government_level_1_5": {"facts": {...}, "driver_assistance_systems": {...}},
              "official_level_2": {"facts": {"torque_nm": {"value": 320, "unit": "Nm", "source_level": "2",
                                    "source_type": "official_importer", "source_market": "IL"}},
                                   "conflicted_fields": [], "missing_fields": [...], "enrichment_status": "enriched"},
              "derived": {"powertrain_family": "combustion", "is_plugin": false}},
    "car_2": {...},
    "deterministic_evidence": {"safety": {"atomic_results": [...], "correlation_groups": [...],
                               "contextual_facts": [...], "not_comparable": [...],
                               "missing_metrics": [...], "conflicted_metrics": [...], "coverage": {...}}, ...},
    "coverage": {...},
    "buyer_profile": {...}
  },
  "questions": {
    "safety": {"type": "choice", "instructions": "...",
               "criteria": {"car_1": "Meaningful evidence-supported advantage for car 1 (Audi Q3)",
                            "car_2": "Meaningful evidence-supported advantage for car 2 (Hyundai Tucson Hybrid)",
                            "tie": "Differences are balanced or not meaningful",
                            "insufficient_evidence": "Available evidence is insufficient"}},
    "...": "every applicable category",
    "overall": {"type": "choice", "instructions": "...", "criteria": {...}}
  }
}
```

Stored from the response: per question `choice`, `confidence`,
`probabilities` (verbatim), plus `response.model` and `usage`.

## Summary request (redacted)

`models.generate_content(model=COMPARISON_SUMMARY_MODEL, temperature=0,
thinking_level=LOW, max_output_tokens=700, response_json_schema={stated_overall_choice, summary_he})`
with a payload of car display identities, JEV category/overall choices and
confidence, top deterministic reasons, coverage labels, buyer priorities and
limitations. No tools, no web data, no API key in the payload.

## Diagnostics

`python -m scripts.jev_models` prints the model ids/aliases available to the
configured `TYPESAFE_API_KEY` (key shown only as a fingerprint) and whether
`JEV_MODEL` is among them.

## Tests

All offline (`tests/test_comparison_v2_*.py`, fakes in
`tests/comparison_v2_fakes.py`). Mocked "official page" claims are test
fixtures, not real specifications.
