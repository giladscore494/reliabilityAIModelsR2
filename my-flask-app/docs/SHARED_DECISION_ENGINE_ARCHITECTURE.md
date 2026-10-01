# Shared Vehicle Decision Engine Architecture

**Status:** Architecture target after PR #300  
**Baseline:** `comparison-v2/2` + `buyer-profile/2`  
**Repository baseline at design time:** `main@d7935fa5b51ffabebe8d42d5b836205453c50905`

---

# 1. Purpose

The comparison engine and the recommendation engine should not become two separate intelligence systems.

They solve the same underlying problem:

> Given validated vehicle facts and a user's actual needs, determine how well each exact vehicle variant fits those needs, explain why, and abstain when the evidence is insufficient.

The only major difference is **where the candidate vehicles come from**.

- **Comparison:** the user chooses 2–3 exact variants.
- **Recommendations:** the system retrieves candidate variants from the MILO-backed vehicle database.

Everything after candidate selection should converge into the same shared decision architecture.

This is the central design principle of the product.

---

# 2. The architecture in one diagram

```
                              ┌──────────────────────────┐
                              │      MILO DATA PLANE     │
                              │                          │
Government registry ─────────►│ Level 1.5 canonical     │
Official importer/manufacturer│ Level 2 official facts  │
Future ownership sources ────►│ Level 3 ownership data  │
                              │ Provenance / freshness   │
                              │ validation / conflicts   │
                              └────────────┬─────────────┘
                                           │
                                           ▼
                              ┌──────────────────────────┐
                              │  CANONICAL VEHICLE DB    │
                              │ exact variant identities │
                              │ validated evidence only  │
                              └────────────┬─────────────┘
                                           │
                  ┌────────────────────────┴────────────────────────┐
                  │                                                 │
                  ▼                                                 ▼
       ┌───────────────────────┐                       ┌────────────────────────┐
       │      COMPARISON       │                       │    RECOMMENDATIONS     │
       │ user selects 2–3      │                       │ CandidateRetriever     │
       │ exact variants        │                       │ finds serious matches  │
       └───────────┬───────────┘                       └────────────┬───────────┘
                   │                                                │
                   └───────────────────────┬────────────────────────┘
                                           ▼
                          ┌─────────────────────────────────┐
                          │       SHARED DECISION ENGINE    │
                          │                                 │
                          │ BuyerPreferenceProfile          │
                          │ HardConstraintEvaluator         │
                          │ EvidenceBuilder                 │
                          │ Deterministic comparisons       │
                          │ JEV narrow semantic judgments   │
                          │ DecisionComposer                │
                          │ ExplanationBuilder              │
                          │ DecisionTrace                   │
                          └────────────────┬────────────────┘
                                           │
                          ┌────────────────┴────────────────┐
                          │                                 │
                          ▼                                 ▼
                 comparison result                ranked recommendations
                          │                                 │
                          └────────────────┬────────────────┘
                                           ▼
                              ┌────────────────────────┐
                              │       PRESENTATION     │
                              │ deterministic reasons │
                              │ optional Gemini prose │
                              │ sources / coverage    │
                              └────────────────────────┘
```

---

# 3. Why this architecture exists

The system deliberately separates four different concepts that must never be collapsed into one model call.

## 3.1 Facts

Examples:

- horsepower;
- seats;
- towing capacity;
- cargo volume;
- official price;
- electric range;
- charging power;
- government safety indicators.

These are not opinions.

They come from validated data sources and are owned by the data layer.

## 3.2 Factual comparison

Examples:

- 286 hp is higher than 190 hp;
- 500 L cargo is greater than 380 L;
- ₪180,000 is over a ₪160,000 budget;
- a 1,600 kg towing limit does not satisfy a 1,900 kg requirement.

These are deterministic operations.

Code owns them.

## 3.3 Meaning

Examples:

- does an extra 100 L of cargo materially matter to this family?
- is a 1.5 second acceleration gap important to a buyer who barely cares about performance?
- is the size difference meaningful for a user with tight parking?
- does an EV's validated range/charging capability fit this user's charging routine?

These are contextual judgments.

This is the narrow layer where JEV can add value.

## 3.4 Decision

The final result must combine:

```
user-declared importance
×
validated evidence
×
contextual materiality/fit
```

Code owns this composition.

JEV does not choose the overall winner.

Gemini does not choose the overall winner.

---

# 4. Shared contracts

The two products should reuse the same core contracts.

## 4.1 Vehicle identity

Every decision operates on an exact canonical variant identity, not merely a model family.

Conceptually:

```
make
model
model_year
trim
official_model_code
variant_identity_key
```

This identity must remain stable enough to bind:

- Level 1.5 government data;
- Level 2 official enrichment;
- later Level 3 ownership evidence;
- history;
- caching;
- comparison results;
- recommendation results.

A model must never assign an uncertain generic claim to an exact variant.

---

## 4.2 BuyerPreferenceProfile

The shared buyer contract is currently `buyer-profile/2`.

It separates three kinds of user input.

### Hard requirements

Examples:

```
budget_max_ils
regular_passengers
towing_braked_required_kg
awd_requirement = required
must_have_features
```

When evidence is available, these resolve deterministically.

### Explicit priorities

Current dimensions include:

```
safety
performance
efficiency
practicality
purchase_price
warranty
equipment
environment
ev_convenience
```

The user owns these weights.

JEV must not silently change them.

### Context

Examples:

```
main_use
annual_km
cargo_need
parking_constraint
road_conditions
charging_access
typical_daily_km
frequent_long_trip_km
```

Context does not directly become a hidden weight.

It helps interpret the practical meaning of validated facts.

---

# 5. Shared decision pipeline

Once candidates are known, both products should execute the same logical stages.

```
Candidates
↓
Load canonical snapshots
↓
Resolve/refresh evidence
↓
Evaluate hard constraints
↓
Build deterministic pairwise evidence
↓
Generate narrow JEV judgments where needed
↓
Compose dimensions in code
↓
Compose final fit/ranking in code
↓
Generate deterministic reasons
↓
Optional Gemini explanation
↓
Persist DecisionTrace
```

Each stage has a strict responsibility.

---

# 6. Data layers

## 6.1 Level 1.5 — government canonical truth

Level 1.5 is the government-derived normalized base.

Typical evidence includes:

- exact identity;
- fuel / propulsion;
- drivetrain;
- body style;
- engine displacement;
- horsepower;
- doors;
- seats;
- gross mass;
- towing;
- government environmental values;
- government safety indicators and ADAS;
- provenance.

Level 1.5 must never be overwritten by Level 2.

If official enrichment disagrees with a government field, the conflict should be visible rather than silently replacing the government fact.

---

## 6.2 Level 2 — official variant enrichment

Level 2 uses importer/manufacturer evidence only.

Typical fields include:

### Performance
```
torque_nm
acceleration_0_100_s
top_speed_kmh
```

### Efficiency
```
fuel_consumption_l_100km
energy_consumption_kwh_100km
```

### EV / battery
```
battery_capacity_kwh
battery_capacity_net_kwh
electric_range_km
electric_range_standard
ac_charging_power_kw
dc_charging_power_kw
dc_charge_time_minutes
dc_charge_from_pct
dc_charge_to_pct
```

### Dimensions / practicality
```
length_mm
width_mm
height_mm
wheelbase_mm
ground_clearance_mm
cargo_volume_l
fuel_tank_l
```

### Equipment
```
apple_carplay
android_auto
heated_front_seats
ventilated_front_seats
power_front_seats
panoramic_roof
surround_view_camera
premium_audio
```

### Israel-specific commercial data
```
official_price_ils
registration_fee_ils
vehicle_warranty
battery_warranty
```

Level 2 rules:

- official source only;
- exact variant matching;
- grounded source required;
- deterministic validation;
- validated units/ranges;
- model-generic claims remain isolated;
- missing remains missing;
- conflicts remain visible;
- no LLM repair loop that changes the factual contract.

---

## 6.3 Level 3 — future ownership/economic evidence

This layer should eventually add:

```
reliability patterns
known recurring failures
maintenance costs
insurance
depreciation
used-market liquidity
real market pricing
recalls
real-world consumption
ownership evidence
```

Warranty is not reliability.

Official price is not total cost of ownership.

These concepts must stay disabled until a trustworthy Level 3 exists.

---

# 7. MILO's role

MILO should evolve from an enrichment agent into the product's **data maintenance plane**.

Its job is not to answer every user request live.

Its job is to keep the canonical database decision-ready.

The target responsibilities are:

```
discover
capture
normalize
identify
match
validate
enrich
detect conflicts
detect staleness
refresh
measure coverage
```

---

## 7.1 Weekly incremental refresh

A maintenance cycle should conceptually do:

```
latest government snapshot
↓
identity/content diff
↓
NEW
CHANGED
UNCHANGED
INACTIVE/REMOVED
```

Unchanged variants should not be unnecessarily re-enriched.

---

## 7.2 Incremental Level 2 refresh

Each variant can have freshness states such as:

```
fresh
stale_technical
stale_price
stale_warranty
missing_decision_fields
source_changed
identity_changed
conflicted
```

Refresh only what is stale.

Example:

- an unchanged technical specification should not be re-searched just because price is stale;
- a price refresh should not destroy cached charging/specification evidence.

---

## 7.3 Decision-aware enrichment priority

MILO should eventually optimize enrichment for product value.

A useful priority concept is:

```
decision usage
×
decision-critical missingness
×
staleness
×
source availability
```

This is better than spending the same enrichment effort on every field of every variant.

The database should track, per canonical variant:

- Level 1.5 completeness;
- Level 2 completeness;
- decision-critical missing fields;
- last successful enrichment;
- observed source hashes;
- source authority;
- conflicts;
- freshness by field group.

---

# 8. HardConstraintEvaluator

Hard constraints belong entirely to code.

Examples:

## Budget

```
official validated Israeli price <= user budget → pass
official validated Israeli price > user budget  → fail
missing price                                  → unknown
```

## Seats

```
seats >= regular passengers → pass
seats < regular passengers  → fail
missing seats               → unknown
```

## Towing

```
towing capacity >= required kg → pass
towing capacity < required kg  → fail
missing capacity               → unknown
```

## Required AWD

```
validated AWD     → pass
validated non-AWD → fail
missing drivetrain→ unknown
```

## Must-have equipment

```
explicit validated true  → pass
explicit validated false → fail
missing                   → unknown
```

The invariant is:

> Missing evidence is never automatically a failure.

A vehicle that definitively fails a hard requirement must not be recommended over a vehicle that definitively passes it.

---

# 9. Deterministic evidence engine

The next shared layer compares facts.

It should not count every raw field as an independent vote.

Related metrics belong to correlation groups.

Examples:

```
SAFETY
  gov_safety_rating
  adas_equipment
  passive_safety
  stability_basics

PERFORMANCE
  power_output
  acceleration
  top_speed

EFFICIENCY
  fuel_use
  energy_use

EV
  battery_size
  electric_range
  ac_charging
  dc_charging

PRACTICALITY
  cargo

ENVIRONMENT
  co2_wltp
  pollution_class
  tailpipe_other

COMMERCIAL
  price
  vehicle_warranty
  battery_warranty
```

This prevents accidental double counting.

For every comparable group, code determines factual direction.

Example:

```
power_output:
BMW > Audi
direction = BMW
```

JEV is never asked to rediscover that direction.

---

# 10. JEV's exact role

JEV is a **semantic micro-judgment engine**, not the decision-maker.

Its job is to answer narrow questions that code cannot reliably derive from raw numbers alone.

There are two main families.

---

## 10.1 Materiality judgments

Code already knows the factual direction.

Example:

```
BMW has the validated performance advantage.
```

JEV may answer:

> How materially does this specific validated difference affect this buyer's real-world suitability?

Use a concrete Score scale such as:

```
0 = negligible / irrelevant
1 = small practical effect
2 = noticeable
3 = large
4 = potentially decisive
```

The resulting signal is conceptually:

```
deterministic direction
×
JEV materiality
```

JEV does not return the winning car.

---

## 10.2 Contextual-fit judgments

Some facts do not have a universal “higher is better” direction.

Examples:

- parking fit;
- body-style/use fit;
- ground-clearance fit;
- EV charging-routine fit.

For these, JEV can score one vehicle against explicit buyer context.

Example:

> How well do this vehicle's validated dimensions fit the buyer's stated tight-parking situation?

Again, this is narrow and bounded.

JEV must not use unsupported world knowledge to fill missing evidence.

---

## 10.3 What JEV must not do

Do not ask:

```
Which car is better overall?
Which car wins safety?
Which car wins performance?
Rank all vehicles.
```

Do not use JEV for:

- arithmetic;
- budget checks;
- seat checks;
- towing thresholds;
- AWD checks;
- exact feature presence;
- deciding factual metric direction;
- inventing missing vehicle facts.

---

# 11. DecisionComposer

The final fit decision is composed in code.

Conceptually:

```
dimension contribution
=
user importance
×
validated dimension signal
```

A dimension signal may itself be built from:

```
objective direction × JEV materiality
contextual fit differences
small deterministic declared preferences
```

Only usable evidence contributes.

Weights are renormalized over available evidence so missing data remains neutral.

The engine separately tracks:

```
recommendation strength
```

and:

```
effective evidence coverage
```

These are different concepts.

A strong recommendation on weak coverage should not be presented as if the system had complete evidence.

---

# 12. Why no universal /100 vehicle score

A vehicle does not possess one objective “87/100” quality score.

The appropriate result depends on the buyer.

The same exact vehicle can be:

- a strong fit for one person;
- a weak fit for another;
- impossible to recommend when it fails a hard requirement.

Therefore the engine should prefer:

- fit outcome;
- evidence coverage;
- strongest reasons;
- important counter-points;
- missing evidence.

If a percentage-style fit indicator is ever introduced, it must be derived transparently from the calibrated composition model and must never imply reliability truth or universal vehicle quality.

---

# 13. Comparison product

Comparison is the simpler entry point into the shared engine.

The user provides the candidates.

```
User selects exact car_1 / car_2 / optional car_3
↓
BuyerPreferenceProfile
↓
Shared Decision Engine
↓
Pairwise/category result
↓
Overall fit for this profile
```

The comparison UI should answer:

1. What does this category evaluate?
2. What did the user say matters here?
3. What do the validated facts say?
4. Why did this category affect or not affect the decision?
5. What important evidence is missing?

The result is therefore more than a specification table.

It is an explainable decision trace.

---

# 14. Recommendation product

Recommendations should use the same intelligence policy.

The difference is candidate discovery.

The current legacy flow:

```
questionnaire
→ Gemini + web search
→ Gemini invents 5–10 candidates
→ Gemini invents fit_score
```

should become:

```
BuyerPreferenceProfile
↓
CandidateRetriever
↓
MILO canonical database
↓
hard constraints / SQL filtering
↓
cheap deterministic pre-ranking
↓
small serious candidate pool
↓
Shared Decision Engine
↓
top recommendations
```

---

# 15. CandidateRetriever

The recommendation engine must not run JEV over ~110k raw records.

Retrieval should be multi-stage.

Conceptually:

```
~110k canonical rows
↓
hard SQL filters
↓
hundreds / thousands of plausible variants
↓
cheap deterministic pre-ranking
↓
top 20–50 serious candidates
↓
full shared decision evaluation
↓
top 3–5 recommendations
```

The exact pool sizes are benchmark parameters, not architectural constants.

---

## 15.1 Hard retrieval filters

Potential filters include:

- year range;
- explicit budget when price evidence exists;
- seat requirement;
- explicit powertrain restriction;
- mandatory transmission/drivetrain;
- towing requirement;
- truly mandatory equipment;
- body style only when explicitly hard.

Unknown evidence should not silently eliminate a candidate unless the product policy explicitly defines that behavior.

---

## 15.2 Soft retrieval

Soft preferences should not act like marketplace checkboxes.

Example:

If a user prefers SUV but does not require it, a hatchback that fits every important need extremely well may remain a valid candidate.

This is one of the main differences from conventional marketplace filtering.

---

# 16. Shared engine vs marketplace filtering

A marketplace filter answers:

> Show me vehicles with attributes I already know I want.

The shared decision engine answers:

> Given what I actually need, which exact vehicles fit me best, how much do their differences matter, and why?

The difference is:

```
FILTER:
attribute matches condition → keep/remove

DECISION ENGINE:
hard requirements
+
soft priorities
+
validated vehicle evidence
+
contextual meaning
+
missing-data neutrality
+
explainable composition
```

This is the core product differentiation.

---

# 17. Example: towing advantage

Vehicle A:

```
2,000 kg braked towing
```

Vehicle B:

```
1,600 kg braked towing
```

For a buyer who never tows:

```
towing relevance = 0
overall contribution = 0
```

For a buyer who needs 1,900 kg:

```
Vehicle A = PASS
Vehicle B = FAIL
```

No model is required for either case.

The same factual difference has completely different decision meaning because the buyer context differs.

---

# 18. Example: cargo

Suppose:

```
Vehicle A cargo = 520 L
Vehicle B cargo = 410 L
```

Code knows:

```
A leads by 110 L
```

For a single commuter who rates practicality low, JEV may judge the difference as low materiality.

For a family carrying a stroller and luggage, JEV may judge the same difference as high materiality.

The user priority then determines how strongly that semantic materiality contributes to the final composition.

This is where JEV can add value without controlling the recommendation.

---

# 19. Example: EV routine

The engine may know:

```
validated range
AC charging power
DC charging power
charge window/time
```

The buyer may say:

```
home charging
55 km daily
300 km frequent long trip
```

JEV may answer a narrow question:

> How well do these validated EV capabilities fit this explicit routine?

It may not infer:

- charging-station availability;
- electricity prices;
- battery degradation;
- undocumented real-world range.

---

# 20. Explanation architecture

The system should explain decisions from structured evidence.

The first explanation layer should be deterministic.

Example:

> Performance is important to you. BMW has the validated power advantage, and the difference was judged materially relevant to your stated use, so this category contributed in BMW's favor.

Or:

> Audi has a towing advantage, but you did not state a towing need, so the advantage did not affect the overall fit.

The explanation must be derivable from:

```
buyer profile
+
validated evidence
+
JEV micro-judgment
+
deterministic contribution
```

Gemini may optionally transform the immutable result into concise natural Hebrew.

Gemini must not:

- change the outcome;
- add facts;
- invent preferences;
- reinterpret missing data;
- override hard requirements.

A deterministic fallback must always exist.

---

# 21. DecisionTrace

Every result should be auditable.

Persist enough information to reconstruct the decision:

```
normalized_buyer_profile
candidate identities
hard_constraint_results
pairwise objective evidence
JEV question specifications
JEV answers
materiality scores
contextual fit scores
dimension signals
dimension contributions
effective evidence coverage
final composition
provider models
usage
latency
```

Do not persist secrets.

DecisionTrace is critical for:

- debugging;
- calibration;
- regression analysis;
- owner diagnostics;
- later commercial trust.

---

# 22. Caching model

Vehicle facts and user decisions have different cache semantics.

## Vehicle evidence cache

Keyed by canonical variant + evidence contract/provider versions.

Reusable across all users.

A Level 2 fact found once can serve many comparisons and recommendation requests.

## Decision cache

Must include the normalized buyer profile.

Same vehicles + different buyer needs must not share the same final recommendation result.

Conceptually:

```
vehicle evidence cache
= facts about the car

decision cache
= facts + candidates + normalized buyer profile + engine version
```

---

# 23. Runtime cost target

The long-term architecture should move expensive AI work away from each user request.

Target:

```
MILO performs expensive discovery/enrichment occasionally
↓
canonical DB remains warm
↓
user request uses:
SQL
Python
cache
small JEV call only where useful
optional short Gemini explanation
```

As calibration improves, some JEV micro-judgments may also become deterministic curves or rules.

The objective is not “remove AI at all costs.”

The objective is:

> Use AI only where it provides measurable value over deterministic code.

---

# 24. Calibration

The current V2/2 constants are provisional.

Examples include:

- minimum effective evidence coverage;
- practical tie margin;
- recommendation-strength bands;
- AWD preference strength;
- nice-to-have strength.

These should be tuned against a labeled evaluation corpus, not intuition.

---

## 24.1 Evaluation corpus

Include:

- ICE vs ICE;
- EV vs EV;
- EV vs ICE;
- hybrid/PHEV;
- close competitors;
- obvious hard-constraint cases;
- sparse evidence;
- conflicting evidence;
- 2-car comparisons;
- 3-car comparisons;
- multiple buyer profiles over the same vehicles.

---

## 24.2 Evaluation goals

Measure:

- factual direction correctness;
- hard-constraint correctness;
- materiality judgment quality;
- preference sensitivity;
- slot/permutation stability;
- abstention quality;
- missing-data neutrality;
- final decision agreement with labeled judgments;
- explanation correctness;
- evidence coverage.

Compare at minimum:

```
simple filtering
deterministic-only engine
deterministic + JEV
```

If JEV does not improve the labeled benchmark for a question family, replace that family with calibrated code.

---

# 25. Future optimization: dynamic materiality curves

Some semantic judgments may eventually become deterministic after enough data.

Examples:

- acceleration gap materiality by declared performance importance;
- cargo difference materiality by family/cargo need;
- parking dimensional thresholds;
- EV daily-range sufficiency;
- towing relevance;
- passenger capacity;
- budget.

The transition path should be:

```
JEV micro-judgment
↓
collect evaluation evidence
↓
fit calibrated rule/curve
↓
benchmark against JEV
↓
move to code only if equal or better
```

This allows request-time model use to shrink without sacrificing decision quality.

---

# 26. Product-specific responsibilities

## Comparison owns

- exact variant picker;
- 2–3 candidate selection;
- side-by-side category presentation;
- pairwise reasons;
- cross-powertrain presentation;
- comparison history.

## Recommendations owns

- candidate retrieval;
- candidate-universe recall;
- pre-ranking;
- top-N selection;
- recommendation presentation;
- exploration into comparison.

## Shared engine owns

- buyer profile;
- evidence contract;
- hard constraints;
- deterministic factual comparison;
- JEV micro-judgments;
- composition;
- coverage;
- reasons;
- DecisionTrace.

This boundary should be enforced in code.

---

# 27. Recommended module direction

The long-term shared package should conceptually contain:

```
decision_engine/
  buyer_profile.py
  evidence.py
  hard_constraints.py
  correlation_groups.py
  pairwise.py
  judgments.py
  jev_client.py
  composer.py
  explanations.py
  trace.py
  calibration.py
```

Comparison-specific code should mostly handle:

```
candidate selection
rendering
history / API orchestration
```

Recommendation-specific code should mostly handle:

```
candidate retrieval
pre-ranking
top-N orchestration
rendering
```

Avoid copying the decision logic into the recommendation service.

---

# 28. What should happen to the legacy recommendation engine

The current Gemini-first advisor can remain temporarily as legacy while Recommendation V2 is built.

Do not rewrite it piecemeal into the new engine.

Create Recommendation V2 behind a feature flag.

Then migrate:

```
legacy Gemini candidate generation
→ MILO CandidateRetriever

legacy Gemini fit_score
→ Shared DecisionComposer

legacy Gemini factual claims
→ canonical Level 1.5 / 2 / 3 evidence

legacy explanation
→ deterministic reasons + optional Gemini summary
```

After parity and benchmark validation, retire the old path.

---

# 29. Release sequence

## Gate 1 — Live V2/2 validation

Before extending recommendations:

- live Level 2 must work on representative variants;
- live JEV Score payload must be verified;
- typed parsing must match real API output;
- slot swap must pass;
- hard constraints must remain authoritative;
- latency and cost must be logged.

## Gate 2 — Decision-coverage audit

Every buyer question must map to real canonical evidence.

Add missing Level 2 fields only when official source coverage supports them.

## Gate 3 — Calibration

Build labeled fixtures and tune constants.

Do not call the engine calibrated before this gate.

## Gate 4 — Shared engine extraction

Refactor comparison V2/2 internals into reusable decision-engine modules without changing behavior.

## Gate 5 — Recommendation V2 retrieval

Replace Gemini candidate generation with MILO candidate retrieval and deterministic pre-ranking.

## Gate 6 — Recommendation V2 shared composition

Run top candidates through the same decision engine.

## Gate 7 — MILO maintenance automation

Move expensive factual enrichment into incremental maintenance.

## Gate 8 — Level 3

Add ownership/economic dimensions only when source quality is sufficient.

---

# 30. Immediate next step

The immediate engineering task remains:

> Run a real V2/2 comparison with live Level 2 and live `jev-latest`, inspect the complete owner DecisionTrace, and fix only issues demonstrated by live evidence.

After that:

> Perform the Level 2 decision-coverage audit and define the canonical evidence registry that both Comparison and Recommendation V2 will consume.

Only after those two steps should Recommendation V2 begin.

---

# 31. Final architectural invariant

The target system should always preserve this separation:

```
MILO
= maintains validated vehicle truth

CandidateRetriever
= decides which vehicles are worth evaluating

Shared Decision Engine
= turns facts + explicit buyer needs into an explainable decision

JEV
= narrow semantic meaning only

Gemini
= data extraction during enrichment + optional final wording

Code
= validation, constraints, arithmetic, weighting, composition and final authority
```

That separation is what makes the system:

- cheaper;
- more testable;
- more explainable;
- easier to calibrate;
- easier to scale;
- less dependent on model behavior;
- reusable across both comparison and recommendation products.
