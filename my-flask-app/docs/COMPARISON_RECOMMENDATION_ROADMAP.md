# Comparison + Recommendation Engine — Post-#300 Roadmap

Status date: 2026-10-01  
Baseline: `main@22e4cfa5685137cdd27e2771fbdff5fe6c5244f9` after PR #300.

## 1. Current production architecture

PR #300 establishes `comparison-v2/2` and `buyer-profile/2`.

The invariant is now:

```
Gemini finds/extracts facts
→ deterministic validation/canonicalization
→ deterministic factual comparison
→ explicit buyer needs/preferences
→ JEV narrow semantic judgments only
→ deterministic composition in code
→ Gemini explains the immutable result
```

JEV does not choose the overall winner and does not decide factual direction.
Hard requirements are code-owned.
Missing data remains neutral.
JEV confidence is not treated as recommendation correctness probability.

This architecture should become the shared decision layer for both:
- `/compare`
- the future recommendations engine

The recommendations product should eventually be:

```
MILO vehicle universe
→ candidate retrieval
→ hard constraints
→ deterministic pre-ranking
→ validated evidence
→ shared Decision Engine
→ top candidates
→ explanation
```

not:

```
questionnaire
→ Gemini searches the web
→ Gemini invents candidate list
→ Gemini returns fit_score
```

---

# Phase A — Live V2/2 production verification

Do this before calibration work.

## A1. Verify live Level 2 first

Run the existing Audi Q3 vs BMW i4 smoke comparison and confirm for each car:

- request completes inside provider timeout;
- `response.parsed` or strict fallback produces a valid object;
- grounding metadata is actually present;
- accepted official fields > 0 when official exact-variant evidence exists;
- validator rejection reasons are sensible;
- no Level 1.5 fact is overwritten;
- no second Gemini repair call is made;
- latency and token usage are logged.

Then repeat with:
- Toyota Sienna
- Hyundai Tucson Hybrid
- one Mercedes/Cadillac/XPeng demo variant

Record:
- accepted fields;
- rejected fields/reasons;
- grounded source count;
- latency;
- input/output/thinking tokens;
- cache behavior on second run.

## A2. Verify live JEV request/response

Use `jev-latest` and confirm the live API accepts the V2/2 payload.

For Audi Q3 vs BMW i4 run at least:

1. performance-focused profile;
2. family/practicality profile;
3. towing profile;
4. EV-friendly high-mileage profile with home charging;
5. same EV profile without meaningful charging access.

Capture:
- question count;
- question IDs;
- primitive type;
- input/output tokens;
- latency;
- score/probability/confidence distributions;
- usable/unavailable answers;
- final deterministic composition.

Required invariants:
- exactly one System One request;
- no broad overall winner question;
- no category-winner question;
- slot swap preserves semantics;
- priority 0 contributes 0;
- failed hard requirement cannot be overridden;
- missing data remains neutral.

---

# Phase B — Level 2 decision-coverage audit

Before adding more buyer questions, map every decision input to evidence we really possess.

Create a code-owned registry:

```
buyer need
→ canonical evidence fields
→ source level
→ deterministic/JEV treatment
→ current coverage
```

Examples:

```
parking_constraint
→ length_mm, width_mm, width_with_mirrors_mm, turning_circle_m
→ Level 2 official
→ contextual JEV fit

cargo_need
→ cargo_volume_l, cargo_volume_seats_folded_l
→ Level 2 official
→ deterministic direction + JEV materiality

charging_routine
→ electric_range_km, AC/DC charging, charge window/time,
  battery_preconditioning
→ Level 2 official
→ contextual JEV fit

towing_requirement
→ towing_braked_kg
→ Level 1.5 government
→ hard constraint
```

## B1. Recommended Level 2 additions

Add only when exact official variant evidence supports them:

```
width_with_mirrors_mm
width_mirrors_folded_mm
turning_circle_m

cargo_volume_seats_folded_l

battery_preconditioning
heat_pump

wireless_apple_carplay
wireless_android_auto
wireless_phone_charging
keyless_entry
power_tailgate
```

Apply the existing Level 2 rules:
- manufacturer/importer sources only;
- exact variant assignment;
- deterministic range/unit validation;
- Israeli source priority for local trim equipment;
- null when unavailable;
- no inference;
- model-generic claims never merge into a variant.

Do not duplicate the government ADAS schema in Level 2.
Map overlapping equipment to Level 1.5 instead.

## B2. Road-condition semantics

The current profile still contains `off_road`.

Replace it with the safer:

```
normal_roads
rough_roads
frequent_unpaved_roads
```

until dedicated off-road evidence exists.

Do not infer true off-road capability from body style, AWD or ground clearance alone.

Potential future fields, only if official coverage proves reliable:

```
approach_angle_deg
departure_angle_deg
breakover_angle_deg
wading_depth_mm
```

---

# Phase C — Calibration and evaluation

The current constants are intentionally provisional.

Do not tune by intuition on individual examples.

Build a labeled evaluation set.

## C1. Evaluation corpus

Start with a diverse matrix of:
- ICE vs ICE;
- EV vs EV;
- ICE vs EV;
- hybrid/PHEV;
- compact/family/SUV/luxury/work-oriented vehicles;
- close matches;
- obvious hard-constraint cases;
- sparse-data cases;
- conflicting-evidence cases;
- 2-car and 3-car comparisons.

For every vehicle set, create several buyer profiles.

The same cars should intentionally produce different appropriate outcomes under different stated needs.

## C2. Human labels

For each fixture record:
- hard-constraint expected statuses;
- expected relevant dimensions;
- expected materiality band per important factor;
- acceptable final outcome(s);
- whether abstention is preferred;
- explanation-critical facts.

Keep factual correctness separate from preference judgment.

## C3. Metrics

Measure at least:
- hard-constraint correctness;
- pairwise decision agreement;
- abstention quality;
- false confident recommendation rate;
- slot/permutation stability;
- preference sensitivity;
- missing-data neutrality;
- explanation trace correctness;
- JEV micro-judgment stability;
- effective evidence coverage.

Do not optimize against JEV confidence itself.

## C4. Tune named constants only

Tune:
- `MIN_EFFECTIVE_WEIGHT_COVERAGE`;
- `PRACTICAL_TIE_MARGIN`;
- strength wording bands;
- AWD preferred strength;
- nice-to-have strength;
- any future deterministic materiality curves.

Every change should run against the full labeled set.

---

# Phase D — Convert recommendations to candidate retrieval + shared Decision Engine

This is the largest product upgrade after comparison calibration.

The current recommendations flow allows Gemini to create 5–10 candidate cars from web search and to output `fit_score`.

That should become legacy.

## D1. Candidate universe

Use MILO/government canonical variants as the source of candidate identities.

The model must not invent which vehicles exist.

The pipeline should be:

```
buyer-profile/2+
→ SQL hard filtering
→ deterministic coarse ranking
→ top candidate pool
→ validated evidence completion
→ shared Decision Engine
→ final top recommendations
```

## D2. Hard filtering

Examples:
- model year;
- passenger requirement;
- explicit fuel/powertrain restriction;
- body requirement only when declared hard;
- mandatory drivetrain;
- towing requirement;
- must-have equipment when known;
- budget when reliable price evidence exists.

Unknown must remain unknown, not fail.

## D3. Soft retrieval ranking

Use cheap deterministic signals to reduce the candidate pool.

Do not run JEV across 110k raw rows.

Target flow conceptually:

```
~110k canonical rows
→ SQL/hard constraints
→ hundreds of eligible variants
→ deterministic pre-ranking
→ top 20–50 serious candidates
→ full Decision Engine
→ top 3–5 presented candidates
```

Exact pool sizes should be benchmarked, not hard-coded from this document.

## D4. Reuse the comparison engine

The recommendation engine should not have a separate intelligence policy.

Extract the shared pieces into reusable services:

```
BuyerPreferenceProfile
HardConstraintEvaluator
EvidenceBuilder
JevJudgmentRegistry
DecisionComposer
ExplanationBuilder
```

Comparison:
```
user-selected candidates → shared engine
```

Recommendations:
```
database-selected candidates → shared engine
```

## D5. Retire model-generated fit_score

Gemini must stop inventing a `fit_score: 87`.

If a user-visible fit percentage is ever added later, it must be derived transparently from deterministic composition and evidence coverage and must not imply vehicle quality, reliability truth or purchase approval.

Prefer explainable outcome bands until calibration is strong.

---

# Phase E — Move AI cost from request-time to MILO maintenance-time

The target economics are:

```
MILO maintains truth
Decision Engine consumes truth
Frontend explains decisions
```

## E1. Weekly incremental government refresh

On each maintenance run:

```
current government snapshot
→ identity/hash diff
→ NEW / CHANGED / UNCHANGED / REMOVED_OR_INACTIVE
```

Do not reprocess unchanged rows unnecessarily.

## E2. Incremental enrichment

For each canonical variant classify Level 2 state:

```
fresh
stale_technical
stale_price
stale_warranty
missing_fields
source_changed
identity_changed
conflicted
```

Refresh only the required freshness groups.

A stable technical specification should not trigger a full model call because a price TTL expired.

## E3. Coverage optimization

MILO should prioritize enrichment that improves decision-engine coverage.

Priority concept:

```
high recommendation/comparison usage
×
high missing decision-value
×
staleness
×
source availability
```

This is better than blindly enriching every field equally.

Track per canonical variant:
- Level 1.5 completeness;
- Level 2 completeness;
- decision-critical missing fields;
- last successful enrichment;
- source authority;
- conflicts;
- last observed content hash.

---

# Phase F — Level 3 ownership/economic evidence

Do not fake these fields from warranty or generic model knowledge.

A future Level 3 should cover:

```
reliability / recurring failure patterns
maintenance cost
insurance
depreciation
used-market liquidity
real asking/transaction prices
recalls
real-world consumption
ownership evidence
```

Each family needs its own source policy and provenance.

Once Level 3 is reliable, extend buyer priorities with:
- long-term reliability;
- total ownership cost;
- depreciation/resale;
- insurance sensitivity;
- market liquidity.

Then add those dimensions to the same shared Decision Engine.

---

# Phase G — Runtime optimization

The long-term objective is to make request-time model cost close to zero.

## G1. Precompute factual evidence

Cache:
- canonical snapshots;
- correlation-group values;
- variant static features;
- reusable deterministic comparisons where useful.

## G2. Reduce JEV dependence after calibration

JEV should remain only where semantic materiality adds measurable value.

As the evaluation set grows, some micro-judgments may become calibrated deterministic functions.

Examples that may become code-owned later:
- towing relevance;
- passenger fit;
- budget;
- cargo thresholds;
- acceleration materiality bands;
- parking dimensional thresholds;
- daily EV-range sufficiency.

Do not remove JEV merely to remove AI.
Remove a JEV question only when code matches or exceeds it on the labeled evaluation set.

## G3. Gemini at runtime

The desired end state:
- no Gemini candidate generation;
- no Gemini factual web research during ordinary cached comparisons/recommendations;
- optional one short explanation call, with deterministic fallback;
- most expensive Gemini work runs asynchronously in MILO maintenance/enrichment.

---

# Phase H — Production/product validation

Technical accuracy alone does not establish product value.

Instrument:
- questionnaire completion;
- comparison completion;
- recommendation engagement;
- category-details opens;
- change-of-preference reruns;
- recommendation → compare;
- recommendation → listing/dealer lead;
- return usage;
- latency;
- cache-hit ratio;
- cost per completed decision;
- insufficient-evidence rate.

Create controlled product tests comparing:
1. simple marketplace filtering;
2. deterministic-only ranking;
3. deterministic + JEV V2/2;
4. future calibrated engine.

The goal is to prove that semantic judgments materially improve user decisions, not merely that they sound smarter.

---

# Release gates

## Gate 1 — V2/2 live-ready

Required:
- live Level 2 succeeds on representative variants;
- live `jev-latest` accepts the new Score payload;
- no schema/parser mismatch;
- slot swap passes live;
- no hard-constraint override;
- cost/latency logged.

## Gate 2 — Calibrated comparison engine

Required:
- labeled eval set exists;
- constants tuned against it;
- regression suite prevents preference/slot/missing-data failures;
- known failure modes documented.

## Gate 3 — Recommendation V2

Required:
- candidates originate from MILO DB;
- no Gemini-created candidate universe;
- same Decision Engine used by compare and recommendations;
- top-N retrieval benchmarks show acceptable recall;
- result trace can explain why every candidate rose or fell.

## Gate 4 — Low-cost production

Required:
- high Level 2 cache coverage;
- incremental MILO refresh in production;
- request-time Gemini factual calls are rare;
- JEV retained only where benchmarked useful;
- per-decision infrastructure/model cost measured.

## Gate 5 — Commercial validation

Required:
- real-user usage;
- measurable preference-fit value vs filters;
- conversion/lead or willingness-to-pay signal;
- stable data refresh process;
- documented source/licensing policy.

---

# Immediate next action

The next engineering task after PR #300 is:

**Run the live Audi Q3 vs BMW i4 V2/2 smoke test with real Level 2 + real `jev-latest`, capture the complete owner `decision_trace`, and fix only issues demonstrated by that run.**

After that, do the Level 2 decision-coverage audit before expanding recommendation logic.

Do not start Recommendation V2 until V2/2 live behavior is verified and the evidence registry is explicit.
