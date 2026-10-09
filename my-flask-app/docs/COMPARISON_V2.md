# Comparison V2 (`comparison-v2/2`)

> **Superseded.** `/compare` now runs Comparison V3 (`comparison-v3/1`, facts from TRIPY): see
> [COMPARISON_V3.md](COMPARISON_V3.md). This document describes the V2 pipeline, which remains for stored
> `comparison-v2/*` history rows and can be selected in code (`DEFAULT_COMPARISON_ENGINE = "v2"`);
> `COMPARISON_V2_ENABLED` no longer selects an engine.

Former feature flag: `COMPARISON_V2_ENABLED`. With the V2 engine selected, `/compare` shows the exact
variant picker plus the personalization step, and `POST /api/compare` runs
the V2 pipeline.

`comparison-v2/2` replaced the V2/1 broad JEV `choice` questions (one per
category + `overall`) with many narrow `score` questions whose answers are
composed in code. Stored `comparison-v2/1` rows keep their own result
contract and renderer (`compare_v2.js` dispatches on `engine_version`).

## Invariant

```
Gemini finds/extracts facts
-> code validates/canonicalizes facts
-> code performs objective comparisons
-> user supplies explicit needs/preferences (buyer-profile/2)
-> JEV makes many narrow semantic judgments (materiality, contextual fit)
-> code composes those judgments + deterministic facts + user weights
-> Gemini explains the immutable composed result
```

JEV is never asked which car is better overall, which car wins a category,
whether a car fits the budget / seats / towing / AWD / a required feature, or
how important a dimension is to the user.

## Flow

```
validate request (exact variant_identity_key per car)
 -> resolve variants              VehicleCatalogRepository (DemoVehicleCatalogRepository today, MiloCatalogRepository later)
 -> Level 1.5 snapshots           level15.build_level15_snapshot  (MoT facts + 19 ADAS flags, null stays null)
 -> buyer profile                 buyer_profile.normalize_buyer_profile (buyer-profile/2; 400 invalid_buyer_profile)
 -> official enrichment per car   OfficialEnrichmentRepository.get_or_enrich
                                  (per-car cache first; at most ONE grounded Gemini call per car, concurrent)
 -> deterministic validation      field_validator.FieldValidator
 -> canonical merge               CanonicalVehicleSnapshot (Level 1.5 never overwritten; field-level provenance)
 -> deterministic comparison      deterministic_engine (atomic results, CategoryEvidence, coverage)
                                  + build_pairwise_evidence (direction per vehicle pair x correlation group)
 -> hard constraints              hard_constraints.HardConstraintEvaluator (pass / fail / unknown / not_applicable)
 -> JEV micro-judgments           judgments.JevJudgmentRegistry -> ONE POST /v1/systemone, many `score` questions
 -> composition                   composer.DecisionComposer (weights, renormalization, coverage, selection)
 -> explanations                  explanations.build_category_cards / reason_texts (deterministic Hebrew, no LLM)
 -> summary                       ONE ungrounded Gemini call on the composed result; validated; deterministic fallback
 -> persist                       comparison_history (prompt_version = comparison-v2/2, decision_trace included)
 -> response / NDJSON progress stream (decision_trace stripped for non-owners)
```

Progress stages (streamed when the client sends `Accept: application/x-ndjson`):
`resolving_vehicles, loading_government_data, enriching_car_N, validating_sources,
comparing_facts, evaluating_decision, writing_summary, complete`.

## Modules (`app/services/comparison_v2/`)

| module | role |
|---|---|
| `contracts.py` | versions (`comparison-v2/2`, readable `comparison-v2/1`), vocabulary, repository boundaries |
| `demo_catalog.py` | 7 Level 1.5 fixtures from MILO Production (`app/data/comparison_v2_demo_catalog.json`) |
| `source_registry.py` | `OFFICIAL_SOURCE_REGISTRY` (`official-source-registry/1`): allowed hosts, seed URLs, brand notes |
| `level15.py` | canonical snapshot builder |
| `field_registry.py` | Level 2 field contracts (units, conversions, ranges, applicability, Israeli-only, freshness group) |
| `official_variant_matcher.py` | deterministic variant identity check |
| `field_validator.py` | claim validation + merge + conflicts |
| `official_enrichment.py` | prompt, Gemini provider (Google Search + JSON schema), per-car cache planning, concurrent fetch |
| `cache.py` | per-car Level 2 cache (in-process + `vehicle_official_enrichment_cache`), field-group freshness |
| `deterministic_engine.py` | `MetricRegistry`, atomic comparisons, `CategoryEvidence`, coverage, `build_pairwise_evidence` |
| `buyer_profile.py` | **BuyerPreferenceProfile** — `buyer-profile/2` schema, validation, balanced defaults, JEV context, UI options |
| `hard_constraints.py` | **HardConstraintEvaluator** — budget, passengers, towing, AWD required, must-have features |
| `decision_model.py` | dimensions, correlation group -> dimension, Score level texts, **named provisional constants** |
| `judgments.py` | **JevJudgmentRegistry** — compact state + materiality / fit question specs with explicit state paths |
| `jev_client.py` | TypeSafe client, model verification, typed `score` / `noul` / `choice` parsing (**JevMicroJudgmentResult**), the single call |
| `composer.py` | **DecisionComposer** — pair utilities, coverage, hard-constraint-first selection, strongest reasons |
| `explanations.py` | four-layer category cards, influence wording, recommendation headline, constraint notes |
| `summary_writer.py` | Gemini summary of the composed result + validator + deterministic fallback |
| `pipeline.py` | orchestration generator, whole-comparison history cache, decision trace, default wiring |

## Buyer profile (`buyer-profile/2`)

The user explicitly picks `השוואה מותאמת אליי` (personalized) or `השוואה כללית`
(general). Nothing is assumed.

* **general** -> documented balanced default: every applicable priority = `2`
  (`ev_convenience` only when an EV/PHEV is selected); no requirements; no contextual-fit questions.
* **personalized** -> `main_use` (city / mixed / highway / long_trips / family / commuting / work, required),
  `annual_km`, `regular_passengers` (1-9), priorities 0-4 for `safety, performance, efficiency,
  practicality, purchase_price, warranty, equipment, environment` (+ `ev_convenience` with an EV/PHEV; all
  required), and optional structured requirements: `budget_max_ils`, `cargo_need` (low/medium/high),
  `parking_constraint` (tight/normal/no_constraint), `towing_braked_required_kg`, `awd_requirement`
  (required/preferred/not_important), `road_conditions` (normal_roads/rough_roads/off_road), EV section
  `charging_access` (home/work/home_and_work/public_only/none/unknown), `typical_daily_km`,
  `frequent_long_trip_km`, and `must_have_features` / `nice_to_have_features` (canonical feature keys only).
* Everything is an enum, a bounded number or a known feature key — no free text. Unknown keys inside
  `priorities`, out-of-range numbers and unknown enums are rejected (400 `invalid_buyer_profile`).
* `reliability`, ownership cost and ride comfort are shown disabled with `עדיין לא נכלל בגרסה הזו` and
  can never influence the result.
* The complete normalized profile is persisted with the comparison and is part of the request hash, so
  two different profiles never share a cached decision.
* Priorities are applied as weights in code and are **not** sent to JEV (no double counting). `main_use`
  and the other context fields only help JEV judge how much a factual difference matters.

## Hard constraints

`HardConstraintEvaluator` returns `pass / fail / unknown / not_applicable` per car for: budget (validated
Israeli official price only), passengers (seats), towing (`towing_braked_kg`), AWD when `required`, and
each must-have feature (validated official Level 2 boolean or an unambiguous government ADAS flag).
Missing data is `unknown`, never `fail`. A car that definitively fails is never selected while another
car passes; if only one car passes it is selected "by requirement" and no JEV question is spent; if none
passes the outcome is `no_vehicle_meets_requirements`. JEV only sees the statuses as context.

## JEV micro-judgments

* **Materiality** (`materiality__car_A__car_B__<group>`): one `score` question per eligible vehicle pair
  and correlation group whose code direction is a car (never for tie / mixed / missing / conflicted /
  not-comparable groups, never for a dimension the user weighted 0, never for towing without a towing
  requirement). Common 0-4 scale (`decision_model.MATERIALITY_LEVELS`). JEV never supplies the
  direction; the question text does not name the leading car, so swapping slots gives the same question
  over swapped data.
* **Contextual fit** (`fit__car_N__<type>`), per car, 0-4 concrete scales (`FIT_LEVELS`), personalized mode
  only: `parking_fit` (validated length/width + tight/normal parking), `body_use_fit` (body style vs
  `main_use` only), `ground_clearance_fit` (validated clearance + drivetrain, only for rough/off-road),
  `charging_routine_fit` (each EV/PHEV: validated range + standard, AC/DC power, DC window vs charging
  access / daily / long-trip distance; non-plug-in cars get a fixed neutral fit in code).
* 2 or 3 cars: pairwise questions for every eligible pair (`car_1__car_2`, `car_1__car_3`, `car_2__car_3`).

### Request (redacted, Audi Q3 vs BMW i4, commuter with home charging, performance = 0)

```
POST {TYPESAFE_BASE_URL}/v1/systemone
Authorization: Bearer [REDACTED]
{
  "model": "<JEV_MODEL verified via GET /v1/models>",
  "state": {
    "buyer_profile": {"mode": "personalized", "main_use": "commuting", "annual_km": 30000,
                      "charging_access": "home", "typical_daily_km": 60},
    "pairwise_objective_evidence": {
      "factor_values": {"gov_safety_rating": {"safety_score": {"unit": null, "better": "higher", "car_1": 1, "car_2": 3},
                                              "safety_equipment_level": {"unit": null, "better": "higher", "car_1": 1, "car_2": 3}},
                        "adas_equipment": {"adas_systems_count": {"unit": "count", "better": "higher", "car_1": 12, "car_2": 15}}},
      "pairs": {"car_1__car_2": {"gov_safety_rating": {"favours": "car_2", "metric_leaders": {...}},
                                 "adas_equipment": {"favours": "car_2", "metric_leaders": {...}}}}
    },
    "contextual_vehicle_evidence": {
      "car_1": {"powertrain": "combustion", "body_style": "suv"},
      "car_2": {"powertrain": "ev", "body_style": "hatchback", "electric_range_km": 483.0,
                "electric_range_measurement_standard": "WLTP", "battery_capacity_net_kwh": 67.1,
                "ac_charging_power_kw": 11.0, "dc_charging_power_kw": 180.0}
    },
    "hard_constraint_results": {}
  },
  "questions": {
    "materiality__car_1__car_2__gov_safety_rating": {"type": "score", "instructions": "Vehicles car_1 and car_2 differ in ... The validated values are at pairwise_objective_evidence.factor_values.gov_safety_rating ... the code-determined comparison for this pair is at pairwise_objective_evidence.pairs.car_1__car_2.gov_safety_rating ...", "criteria": ["The difference is negligible ...", "... small ...", "... noticeable ...", "... large ...", "... potentially decisive."]},
    "materiality__car_1__car_2__adas_equipment": {...},
    "fit__car_1__body_use_fit": {...}, "fit__car_2__body_use_fit": {...},
    "fit__car_2__charging_routine_fit": {"type": "score", "instructions": "Judge only vehicle car_2. ... Use the vehicle facts at contextual_vehicle_evidence.car_2 and the buyer context at buyer_profile.charging_access, buyer_profile.typical_daily_km, buyer_profile.annual_km. ...", "criteria": [5 concrete fit levels]}
  }
}
```

No URLs, HTML, prose, brand names, rejected claims or duplicated values are sent; questions are
English; every referenced state path exists (tested).

### Answers

Typed parsing per question: `ScoreAnswer` (finite score within 0..levels-1, probability keys within the
levels, finite values summing to 1, expected value consistent with the distribution, confidence in
[0,1], `legend` kept), `NoulAnswer`, `ChoiceAnswer`. Anything malformed becomes `judgment_unavailable`
for that question only; unrequested answer ids are ignored and recorded. Stored verbatim: score,
probabilities, confidence, legend, `response.model`, `usage`.

## Composition (`DecisionComposer`)

For every eligible pair (a, b) and dimension d:

* objective signal = code direction (+1 favours a / -1 favours b) x JEV materiality / 4; a code `tie` or
  `mixed` group is a usable 0;
* contextual signal = fit(a) - fit(b), fit = JEV fit score / 4;
* deterministic soft signals: each declared nice-to-have feature and an AWD preference
  (`NICE_TO_HAVE_STRENGTH`, `AWD_PREFERENCE_STRENGTH`);
* one correlation group = one signal; dimension signal s_d = mean of its usable signals.

`U(a,b) = sum(w_d * s_d) / sum(w_d)` over dimensions with usable evidence (weights renormalize over the
available evidence; missing and conflicted data stay neutral). `effective_weight_coverage = usable weight /
applicable weight`, tracked separately from strength. Priority 0 has no influence. Towing weighs
`TOWING_WEIGHT_WHEN_REQUIRED` only with a towing requirement. Equipment carries weight only through
declared nice-to-have features (a car never gains from undeclared equipment).

Selection: hard constraints first; JEV failure (questions asked) -> `decision_unavailable`; a pair is
`insufficient_evidence` when coverage < `MIN_EFFECTIVE_WEIGHT_COVERAGE`, `tie` when |U| <
`PRACTICAL_TIE_MARGIN`; a car is recommended only if it beats every other eligible car; strength
(`clear / moderate / slight`) comes from the smallest |U| against the others.

| constant (`decision_model.py`) | value | status |
|---|---|---|
| `MIN_EFFECTIVE_WEIGHT_COVERAGE` | 0.25 | provisional |
| `PRACTICAL_TIE_MARGIN` | 0.05 | provisional |
| `STRENGTH_CLEAR` / `STRENGTH_MODERATE` | 0.35 / 0.15 | provisional (wording only) |
| `TOWING_WEIGHT_WHEN_REQUIRED` | 4 | provisional |
| `NON_PLUGIN_CHARGING_FIT` | 0.5 | provisional |
| `AWD_PREFERENCE_STRENGTH` / `NICE_TO_HAVE_STRENGTH` | 0.5 / 0.5 | provisional |
| `CATEGORY_NEGLIGIBLE_CONTRIBUTION` | 0.01 | provisional (wording only) |

**None of these weights or thresholds is calibrated.** They must be calibrated on our own labeled
evaluation set before anyone describes the outcome as calibrated. There is no /100 score and no overall
confidence; JEV confidences are never averaged.

## Source enforcement

* Only https URLs whose host equals an allowed host, or is a subdomain of an
  allowed host marked `subdomains=True` (`bmw.co.il.evil.com` is rejected).
* The cited URL must also be corroborated by evidence Google itself produced
  for that call (`grounding.GroundingIndex`; `SOURCE_NOT_GROUNDED` otherwise),
  never by anything the model wrote. Evidence: grounding-chunk redirect
  targets (one header-only request to Google's redirect endpoint; the page is
  never fetched), URLs the URL-context tool retrieved successfully, and chunk
  titles that are bare hostnames. The Gemini Developer API never fills
  `web.domain`. Correlation tiers, recorded on every fact as
  `grounding_tier`: `url` (exact retrieved URL) > `host` > `site` (same
  official site, e.g. `uploads.audi-mediacenter.com` cited while Search names
  `audi-mediacenter.com`). `COMPARISON_GROUNDING_MIN_TIER` can tighten this.
  Citation metadata is not search evidence.
* Identity: trim-dependent fields need the full variant match. Values fixed
  by the powertrain (torque, 0-100, top speed, battery, charging, gearbox,
  fuel tank) may match at powertrain level when the page does not name the
  Israeli trim (`identity_match="powertrain"`); a stated contradicting trim is
  still a mismatch.
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
| cold, 2 cars | 4 enrichment (technical + commercial task per car, all concurrent) + 1 JEV (many Score questions) + 1 summary |
| Level 2 cache hit | 1 JEV + 1 summary |
| whole comparison cached (same cars + same NORMALIZED buyer profile + versions; only when every car's Level 2 is healthy; until the earliest group freshness, max 24h) | 0 |
| a car fails a hard requirement and only one car remains eligible | 0 JEV (nothing left to weigh) |
| `COMPARISON_V2_OFFLINE_MODE=true` | 0 |

Enrichment provider timeout 125s per task (own window each, +10s grace),
capped by the request deadline derived from the server timeout. Freshness is a
*meaningful observation* per group (`group_freshness`), never "the model
returned JSON":

| state | meaning | fresh for (technical / price / warranty) |
|---|---|---|
| `complete` | >= half the requested fields validated | 30 d / 24 h / 7 d |
| `partial` | some fields validated | 3 d / 24 h / 2 d (values kept on re-search) |
| `empty` | grounded search, nothing reported | 3 d / 12 h / 2 d |
| `rejected` | claims returned, all rejected | 12 h / 6 h / 12 h |
| `grounding_unverifiable`, `ungrounded`, `failed` | no meaningful observation | never cached |

Timeouts, provider errors, `INVALID_JSON` and any finish reason other than
`STOP` (e.g. `MAX_TOKENS`) are `failed`. Only stale groups are searched again.
Cache keys include the enrichment model, contract, registry and
`FIELD_VALIDATOR_VERSION`, so changing any of them invalidates old rows. `GET /v1/models` is
called to verify `JEV_MODEL` and cached in-process for 6 hours.

No retries: one enrichment attempt per task (the only exception: an HTTP 400
rejecting the URL-context tool is retried once with Google Search only), one JEV attempt, one summary
attempt. Enrichment failure -> Level 1.5 only. JEV failure (when questions were asked) ->
`decision_unavailable` and deterministic facts; one malformed answer only
drops that question (`judgment_unavailable`). Summary failure/rejection ->
deterministic Hebrew template.

## Gemini enrichment adapter (google-genai 2.25.0)

* Model `COMPARISON_ENRICHMENT_MODEL` (default `gemini-3.1-pro-preview`; the summary keeps its own
  `COMPARISON_SUMMARY_MODEL`). Endpoint: Gemini Developer API `POST /v1beta/models/{model}:generateContent`.
* One `models.generate_content` call per task (technical / commercial) with `GenerateContentConfig(
  tools=[google_search, url_context], response_mime_type="application/json",
  response_json_schema=<task-scoped field enum>, max_output_tokens=32768,
  thinking_config=ThinkingConfig(thinking_level=LOW), automatic_function_calling=disable,
  http_options=HttpOptions(timeout=COMPARISON_ENRICHMENT_TIMEOUT_SEC*1000, retry_options=HttpRetryOptions(attempts=1)))`.
  Temperature is the model default (Gemini 3 guidance); `COMPARISON_ENRICHMENT_TEMPERATURE`,
  `COMPARISON_ENRICHMENT_THINKING_LEVEL`, `COMPARISON_ENRICHMENT_MAX_OUTPUT_TOKENS`,
  `COMPARISON_ENRICHMENT_URL_CONTEXT`, `COMPARISON_ENRICHMENT_SPLIT` override.
* The SDK's "AFC is enabled with max remote calls: 10" line refers to automatic *Python* function
  calling; it never limited Google Search. AFC is now disabled explicitly.
* Government cross-check fields (horsepower, engine_cc, seats, doors) are no longer requested.
* Output: `response.parsed` (the SDK's `json.loads` of the text for a dict schema) first; otherwise a
  strict parse of the text (raw, fenced, outermost `{...}`, or the last text part that is a complete
  object). Never a second "repair" call.
* `INVALID_JSON` / `FINISH_*` / `PROMPT_BLOCKED:*` log `vehicle_enrichment_unusable_response` with model, finish reason, usage, candidate count, text
  length, native-parsed presence, sanitized head/tail fragments, grounding metadata presence, chunk and
  query counts, parser reason. The API response carries the same metadata without text fragments.
* Grounding evidence: see "Source enforcement" (`grounding.py`).
* `enrich_many`: every task has its own window (provider timeout + 10s grace) measured from the moment it
  starts, capped by the request deadline; a call past its window is reported `CALL_TIMEOUT` /
  `DEADLINE_EXCEEDED` and abandoned (never awaited). The streaming route sends blank keep-alive lines
  while enrichment runs.
* Per car, `vehicle_enrichment_completed` logs: requested groups/field count, model, duration, status,
  finish reasons, token usage, grounding presence, search query / chunk / URL-context counts, grounded
  official hosts, accepted / rejected / model-generic / conflict / missing counts, rejection-reason
  histogram, group states, cache-write decision and groups marked fresh. Never prompts, model text or keys.
* Wall-clock: the request budget is the Gunicorn `--timeout` (read from the command line /
  `GUNICORN_CMD_ARGS`, or `COMPARISON_V2_SERVER_TIMEOUT_SEC`) minus 12s; enrichment must leave 30s for
  JEV + summary, and both are capped by (or skipped for) what remains, so a finished enrichment is never
  killed by the worker timeout.

## Category cards (deterministic, no LLM)

Every category card has four layers: `what` (static scope text), `importance` (the user's declared
value — never from JEV), `data` (validated evidence; contextual fits), `influence` (why it did or did
not influence: favoured car + materiality / fit, zero priority, no towing need, no declared features,
decided by requirements, judgment unavailable, insufficient data, not directly comparable).
Cross-powertrain categories distinguish `not_applicable` (irrelevant to every car),
`not_directly_comparable` (e.g. EV charging vs a petrol car, L/100km vs kWh/100km) and
`insufficient_data`. New category `equipment_and_convenience` (`אבזור ונוחות שימוש`) — not ride comfort.

Raw JEV confidence appears only inside a card's details, labelled `ודאות מודל בשיפוט הזה` with the note
that it is not a correctness probability.

## Summary request (redacted)

`models.generate_content(model=COMPARISON_SUMMARY_MODEL, temperature=0, thinking_level=LOW,
max_output_tokens=700, response_json_schema={stated_outcome, summary_he})` with the immutable composed
result: outcome + recommended name, basis, strength wording, evidence-coverage label, the profile
headline and priorities, the deterministic reasons, requirement notes, per-category influence text and
limitations. Rejected (deterministic fallback) if it states another outcome, names another car as the
better fit, adds numbers not in the payload, states a percentage, uses absolute / score wording, or
breaks the size contract. No tools, no web data, no API key in the payload.

## Diagnostics

* `decision_trace` (persisted; returned only to owners): normalized buyer profile, hard constraints, JEV
  state, question specs (with state paths), skipped questions, typed answers, pairwise directions,
  materiality and fit scores, category contributions, effective coverage per pair, the full composition,
  provider model, usage and latency. No secrets.
* `python -m scripts.jev_models` prints the model ids/aliases available to the configured
  `TYPESAFE_API_KEY` (key shown only as a fingerprint) and whether `JEV_MODEL` is among them.

## Live verification (pending)

Not yet run against the live TypeSafe API: this environment has no `TYPESAFE_API_KEY` / `JEV_MODEL` /
`GEMINI_API_KEY` and its network policy blocks `api.typesafe.ai`. To run it: set the keys as environment
secrets, run `python -m scripts.jev_models` (confirm `JEV_MODEL`, e.g. `jev-latest`), then compare Audi Q3
2024 S LINE vs BMW i4 eDrive35 2024 PURE under the performance, family/practicality, towing and
home-charging profiles, and inspect `decision_trace` (as owner) for question count, usage, latency, score
distributions, contributions and the composition; repeat with the slots swapped.

## Tests

All offline (`tests/test_comparison_v2_*.py`, fakes in `tests/comparison_v2_fakes.py`; the fake JEV's
readings depend only on explicit profile data and validated values). Mocked "official page" claims are
test fixtures, not real specifications. `tests/fixtures/comparison_v2_1_result.json` is a real V2/1
result used to prove stored V2/1 history still renders.
