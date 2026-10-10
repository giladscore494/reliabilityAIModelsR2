# Comparison V3 (`comparison-v3/1`)

V3 is the engine of `/compare` and `POST /api/compare`. This is a code default
(`DEFAULT_COMPARISON_ENGINE = "v3"` in `app/services/comparison/model_config.py`), not an env flag.
`COMPARISON_V2_ENABLED` no longer selects an engine. Tests of the older engines select theirs in code with the
`legacy_compare_engine` / `v2_compare_engine` fixtures.

Stored `comparison-v2/1`, `comparison-v2/2` and legacy V1 rows keep their own result contract and renderer. The
dispatch is by `engine_version`: `compare_v3.js` handles `comparison-v3/1` and `compare_v2.js` handles V2.

| contract | version |
|---|---|
| engine / stored result | `comparison-v3/1` |
| buyer profile | `buyer-profile/3` (a `/2` profile is migrated) |
| snapshot | `canonical-vehicle-snapshot/2` (source levels `1.5` government, `government_dataset`, `open_data`, `user_supplied`) |
| facts | TRIPY `vehicle-facts/1` |

## Invariant

```
TRIPY serves the facts (government + open data, merged and matched to the exact variant)
-> the user enters the asking price per car (optional)
-> code builds the rows that exist for EVERY car (row rule R)
-> code performs objective comparisons on those rows
-> the user supplies needs / priorities (buyer-profile/3)
-> JEV makes narrow semantic judgments (materiality per pair x correlation group, contextual fit)
-> code composes judgments + rows + user weights
-> one ungrounded Gemini call explains each row; one explains the composed result
```

What V3 does not do:

* no grounded Gemini enrichment and no Level 2 web search;
* no direct MILO / EEA / EPA / CVS / NRCan / ADEME access;
* the V3 pipeline never imports `official_enrichment`, `grounding` or `source_registry`. A subprocess test checks
  `sys.modules` for this.

## Data: TRIPY

`POST {TRIPY_BASE_URL}/api/facts/v1/vehicles` with body `{"variant_identity_keys": [1-3]}`:

* Auth: `Authorization: Bearer {TRIPY_FACTS_TOKEN}`. Both values are Render secrets.
* Timeouts (code configuration, `tripy.py`): facts 20 s, catalog 15 s. A cold TRIPY answered the first catalog call
  in 6.3 s, so the old 5 s timeout returned 503 on the first picker load.
* Only the catalog retries: once, 1 s later, on a connection error / timeout or a 502 / 503 / 504. The facts call
  never retries.
* A network error, timeout, 4xx, invalid JSON, contract mismatch or missing configuration all become
  `TripyUnavailable`.
* `TripyUnavailable` → HTTP 503 `facts_unavailable` with "ההשוואה לא זמינה כרגע, נסו שוב בעוד כמה דקות".
  V3 never falls back to demo data or a stale cached comparison.

The picker cascade (manufacturers → models → years → trims) goes through the server at
`GET /api/compare/v3/catalog/<kind>` (`/api/facts/v1/catalog/*`, 600 s in-process cache). The TRIPY token never
reaches the browser. The picker shows "טוען…" while a list loads, a Hebrew error with a "נסו שוב" button when it
fails, and a Hebrew explanation (also with the button) when a list comes back empty; never an unexplained empty list.

`COMPARISON_V2_OFFLINE_MODE=true` serves the Level 1.5 demo fixtures as `vehicle-facts/1` records (dev and offline
only), with no JEV, summary or explanation calls.

Field names (D2):

* TRIPY `fuel_consumption_combined_l_100km` is read as `fuel_consumption_l_100km`.
* `curb_weight_kg` carries its open-data `definition`:
  * `eu_running_order` → "משקל במצב נסיעה (כולל נהג)" (EEA);
  * `na_curb` → "משקל עצמי" (Transport Canada).
* Government ADAS flags arrive as `adas.<flag>`.

## Asking price

"מחיר מבוקש (₪)" is an optional integer per car, 1,000–3,000,000, with provenance `user_supplied`. It feeds:

* the `asking_price_ils` row (lower is better, tie at 2%) and so the `purchase_price` dimension;
* the budget constraint. A budget with any price missing → 400 `invalid_buyer_profile` with
  "כדי לבדוק תקציב יש להזין מחיר לכל רכב". The constraint is evaluated only with every price.

The prices are part of the request hash.

## The row rule (R)

`metrics.comparable_rows(snapshots)` is the single function the table, the engine, the JEV state, the summary
payload and the slider availability all read. A row exists only when:

1. every selected car has a value;
2. the value is under the same `standard` for every car: WLTP with WLTP, NEDC with NEDC, a mass under the same
   `definition`, an ADAS count over the same set of reported flags;
3. for measured consumption (L/100km, kWh/100km), every car is in the same propulsion family (EV / PHEV /
   combustion).

Otherwise the row is absent everywhere.

**One row set (L1).** The decision, the JEV questions, the reasons, the section texts, the summary and the row
explanations all consume this output. A row missing for any car is absent from every weight, score, question and
text, including its name: a correlation group is labelled and described by the rows that exist
(`metrics.group_label_he` / `group_description_en`), so without the safety score the reason names "מערכות סיוע לנהג",
never "דירוג הבטיחות של משרד התחבורה". The AWD preference counts only when the drivetrain is a row.

**Official emission figures across propulsion families (L4).** CO2 WLTP / NEDC, the green index and the pollution
group are the ministry's type-approval classification, all from the same test, and are compared across families. When
a plug-in or electric car is compared with a combustion car, each of these rows carries the same `note_he` (shown
under the row label and in its explanation): a plug-in's WLTP figure is a weighted value that assumes regular
charging; an electric car's is tailpipe only.

**Leaders over all cars (L2).** A scored row's `leader` is the one car strictly better than every other car after the
tie margin, else `tie`; `top_slots` are the cars within the margin of the best. Reasons and section texts credit only
the leader. When the rows of a reason have different leaders, the text names each row's leader ("קבוצת זיהום: תיקו
בין A ל־B"). With three cars no text is built per pair ("X מול Y"); the pairwise utilities stay internal. The summary
gets `row_leaders`, and a summary sentence that credits a car with a row it does not lead (or a hidden row, or a
pairwise "X מול Y") falls back to the deterministic summary. A category without rows is absent. A dimension without a scored row has its
slider hidden: the picker calls `POST /api/compare/v3/availability` before the personalization step.

When nothing is common, the only text is "אין מספיק נתונים משותפים להשוואה בין הרכבים האלה". There is no "אין מידע",
no "—" and no null cell.

## Categories (`comparison-v3/1`)

| # | category | dimension (slider) | rows | correlation groups |
|---|---|---|---|---|
| 0 | פרטי הגרסה | unweighted | model year, propulsion and fuel, drivetrain, body, engine cc (0 = absent), gearbox (EPA for American-route cars, otherwise government) | none |
| 1 | מחיר | `purchase_price` | `asking_price_ils`; history: `original_new_price_ils`, `depreciation_from_new` | `price` |
| 2 | בטיחות | `safety` | `safety_score`, `safety_equipment_level`, `adas_systems_count`; `airbags` | `gov_safety_rating` (one signal); `passive_safety`. No ABS / ESC. |
| 3 | ביצועים | `performance` | `horsepower`, `hp_per_tonne` (same mass definition only); mass (display) | `power_output` |
| 4 | צריכה וסביבה | `efficiency_environment` | fuel / energy consumption, CO2 WLTP / NEDC; `green_index`, `pollution_group` (NOx dropped) | `consumption`; `pollution_class` |
| 5 | מידות ומרחב | `practicality` | `wheelbase_mm` (the only scored metric); seats, length, width, height (display) | `wheelbase` |
| — | היסטוריה ושרידות | unweighted | `recalls`, `road_survival` | none |
| 6 | חשמלי | `ev_convenience` (only with a plug-in car) | `electric_range_km`. Shown only when every car is EV or every car is PHEV. | `electric_range` |
| 7 | גרירה | weighted only with a towing requirement | braked / unbraked towing | `towing` |

Removed from V2:

* the `warranty` and `equipment` dimensions;
* official features: features are now the four government ADAS flags only;
* ground-clearance fit, cargo, road conditions, NOx;
* every Level 2 metric.

### History rows (display only, never weighted, never in JEV or the summary reasons)

* `original_new_price_ils`: a single value, or a range shown as "₪min–₪max (n_prices מחירים במחירון לשנה זו)".
  * The derived "ירידת ערך מהמחיר החדש" = 1 − asking / original, only when the original is a single value.
* `recalls`: the count in the cell (`recall_count.value`, else the length of `recalls.value`; when both exist and
  disagree, no cell). A resolved model with no notice is a real 0. The row exists only when every car has the
  `recalls` field. The row chevron lists recall_year · affected_system · fault_description · repair_method ·
  production range; the explanation may cite only `affected_system` and `recall_year`, never a severity.
* `road_survival.cancelled_share_by_age`: X = the largest age ≥ 3 that every compared cohort has reached.
  * The cell reads "Y% מהרכבים" and keeps `cohort_year`, `cohort_basis` and `reference_month` for the explanation.
  * The explanation never says "אמין" / "אמינות" and states that the cancellation reason is unknown.

**Field shapes (TRIPY `vehicle-facts/1.1`, tripy #77).** The three fields come from the ministry's data.gov.il
datasets, `source_level: "government_dataset"` (shown as "משרד התחבורה — מאגר data.gov.il", never as open data), and
keep `attribution`, `licence`, `resource_id` and `dataset_built_at`:

* `original_new_price_ils`: `{value}` or `{range: [min, max], n_prices}` (`count` accepted as a fallback);
* `recalls`: `value` = `[{recall_id, recall_year, affected_system, fault_description, repair_method,
  production_range}]` (sent only for a resolved model, `[]` when it has no notice), with `recall_count` `{value: n}`;
* `road_survival`: `value` = `{cohort_size, cancelled_share_by_age: {age: fraction}, median_age_at_final_cancellation,
  final_cancellation_rate, definition_he, cohort_basis, cohort_year, reference_month}`.

When the fields are absent, the rows are absent.

## Buyer profile (`buyer-profile/3`)

* Sliders 0–4 (default 2): `purchase_price`, `safety`, `performance`, `efficiency_environment`, `practicality`, plus
  `ev_convenience` with a plug-in car. A hidden slider is not sent.
* A `/2` profile is migrated:
  * `efficiency_environment = max(efficiency, environment)`;
  * `warranty` / `equipment` are dropped.
* Requirements: passengers, budget (needs every price), towing, AWD, and must-have / nice-to-have ADAS flags.
  Nice-to-have flags count in `safety`.
  A nice-to-have flag counts only when the "מערכות סיוע לנהג" row exists (every car reports the same set of flags):
  that row's chevron lists, per car, exactly which of the 19 government systems it has, in Hebrew
  (`labels.ADAS_LABELS_HE`, cell `systems_he`), so every flag that affects the score is visible.

## Decision

The composer and JEV micro-judgments are the V2/2 design, applied to rows only:

* `U(a, b) = Σ w·s / Σ w`;
* one correlation group = one signal;
* a dimension's weight counts only when the dimension has rows or signals;
* fits come from rows (parking: length / width; charging routine: the range row). The body-style vs use fit is not
  weighted: it was a JEV judgement with no deterministic rule and no row of its own (L3).

| constant (`comparison_v3/engine.py`) | value | status |
|---|---|---|
| `MIN_EFFECTIVE_WEIGHT_COVERAGE` | 0.25 | provisional |
| `PRACTICAL_TIE_MARGIN` | 0.05 | provisional |
| `STRENGTH_CLEAR` / `STRENGTH_MODERATE` | 0.35 / 0.15 | provisional (wording only) |
| `TOWING_WEIGHT_WHEN_REQUIRED` | 4 | provisional |
| `NON_PLUGIN_CHARGING_FIT` | 0.5 | provisional |
| `AWD_PREFERENCE_STRENGTH` / `NICE_TO_HAVE_STRENGTH` | 0.5 / 0.5 | provisional |
| `CATEGORY_NEGLIGIBLE_CONTRIBUTION` | 0.01 | provisional (wording only) |
| row tie margins (`metrics.py`): price 2%, hp 5%, wheelbase 2%, range 3%, safety score 0.5, fuel 0.3 L, energy 0.5 kWh, CO2 / green index 5, towing 50 kg | as listed | provisional |

**None of these weights or thresholds is calibrated.**

## Row explanations (E)

* One call in stage `writing_explanations`, before `writing_summary`.
  * Model: `DEFAULT_COMPARISON_V2_MODEL_ID` (`gemini-3.8-flash`, code configuration).
  * Settings: temperature 0, `thinking_level` LOW, no tools, JSON output `{row_id: text}`.
* Input per row: label, unit, standard, sources, the cell text per car, direction, leader / tie, and the buyer
  headline. No URLs and no raw records.
* Each text is 2–4 sentences, ≤ 450 characters, and is validated per row:
  * only numbers that appear in that row;
  * no car called better unless it is the row's leader;
  * no %, score or "/100" wording;
  * no other row's label;
  * history rows never say "אמין";
  * road survival must state that the reason is unknown.
* A rejected or missing text falls back to the metric's deterministic `explain_he`.
* The texts are stored with the comparison.

## Table UI (`static/compare_v3.js`, `static/compare_v3.css`)

* At most 3 car columns. The header shows name, year, trim and asking price.
* One section per category, with its influence text behind the section chevron.
* The leader cell is bold with a ● marker and screen-reader text. Ties and display-only rows are unmarked.
* Every row label is a `<button aria-expanded aria-controls>` (Enter / Space work natively) that opens a full-width
  explanation panel.
* RTL. Below 640 px the label moves above the values and the value cells share the width (no fixed widths). A
  Chromium test at 375 px checks for no horizontal scroll (skipped where Playwright / Chromium is not installed;
  the CSS snapshot test always runs).
* Footer: "מקורות: …" lists only the sources the rows used, e.g. "משרד התחבורה; EEA (CC BY 4.0); Transport Canada".

## Caching and cost

| situation | calls |
|---|---|
| cold | 1 TRIPY + 1 JEV + 1 row explanations + 1 summary |
| whole comparison cached (same keys, prices, normalized profile, TRIPY versions, models; max 24 h; not when the decision or the explanations failed) | 1 TRIPY (for its versions), 0 model calls |
| TRIPY down | 1 facts attempt (the catalog: up to 2), nothing else |
| `COMPARISON_V2_OFFLINE_MODE=true` | 0 |

Cache key: engine, rules version (`DECISION_RULES_VERSION`), snapshot contract, variant keys, asking prices, normalized profile, the TRIPY versions
(`contract`, `admission`, `snapshots_sha`, `matcher`, `zero_semantics`) and the summary / explanation / JEV models.

## Tests

* `tests/test_comparison_v3_engine.py`: the row rule, categories, price / budget, safety, hp per tonne, migration,
  explanations, TRIPY access, attribution, the no-import rule.
* `tests/test_comparison_v3_api_ui.py`: API, cache, stream, availability, catalog, template, JS renderer (node),
  the 375 px CSS snapshot and the Chromium check, and stored V2 history.
* `tests/test_comparison_v3_live_fixes.py`: the first live comparison (BMW 530E / Audi A7 SPORTBACK plug-ins, Alfa
  Romeo GIULIA petrol): the hidden safety score nowhere, N-car reasons and ties, the summary guard, no body fit, the
  CO2 row and its note, the TRIPY timeouts / retry, the picker loading / error / retry states (node).
* Fakes: `tests/comparison_v3_fakes.py` (including the live trio, `LIVE_TRIO`).
