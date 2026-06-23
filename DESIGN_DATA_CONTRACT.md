# DESIGN_DATA_CONTRACT.md

> **Status:** Inspection & documentation only. **No UI, backend, route, schema,
> or selector was changed by this task.** This file is the binding contract that
> any *future* recommendations redesign must obey. The uploaded Claude Design
> prototype is a **visual reference only** — its runtime files
> (`support.js`, `image-slot.js`, `.dc.html`) must **never** be copied into
> production.

---

## 1. Where the design reference files were found

The Claude Design prototype was uploaded as a single archive at the repo root:

```
./ידע רכב prototype design.zip
```

Archive contents (7 files):

| File | Role |
|---|---|
| `Canvas.dc.html` (≈50 KB) | Master prototype — Home / Analyzing / **Results** / Compare / Details screens for the advisor flow. The primary visual reference. |
| `ScoreGauge.dc.html` (≈3 KB) | Standalone circular **score gauge** component (animated SVG ring + center number + label). |
| `image-slot.js` (≈31 KB) | Design-canvas runtime for the `<image-slot>` drag-drop placeholder. **Prototype tooling — do NOT ship.** |
| `support.js` (≈55 KB) | Design-canvas framework (`DCLogic`, `<x-dc>`, `<sc-if>`, `<sc-for>`, `<dc-import>`). **Prototype tooling — do NOT ship.** |
| `screenshots/home.png`, `screenshots/home2.png` | Reference screenshots of the home screen (dark chrome header over a light/white body). |
| `.thumbnail` | Archive thumbnail. |

The archive is **not extracted into the repo**; it was inspected from a scratch
copy. The only sibling design doc already in the repo is
[`REDESIGN_NOTES.md`](./REDESIGN_NOTES.md), which documents the existing
white/chrome theme pass (Tailwind token inversion + `static/theme.css`).

Production files inspected for the contract:

- `my-flask-app/templates/recommendations.html`
- `my-flask-app/static/recommendations.js`
- `my-flask-app/static/theme.css` (existing `.yr-*` design system)
- `REDESIGN_NOTES.md`

---

## 2. Visual direction implied by the prototype

Extracted from `Canvas.dc.html` + `ScoreGauge.dc.html` (inline styles, gradients,
fonts) and the screenshots. This matches the existing `.yr-*` system in
`theme.css`, so the redesign direction is **continuity, not a new palette**.

- **White / chrome base.** Body sits on a light gradient
  `linear-gradient(180deg,#f7f9fc,#eef1f6)` with two soft ambient radial blobs
  (blue `rgba(47,107,255,.13)` + cyan `rgba(22,200,230,.10)`). Ink text `#0e1218`
  / `#11161d`; muted `#5a636f` / `#94a0ad`.
- **Dark chrome header.** Sticky bar `#13161b` with a fine 45°/-45° brushed-metal
  cross-hatch (`repeating-linear-gradient(... rgba(255,255,255,.045) ...)`), thin
  light bottom border, and a thin progress underline.
- **Premium glass cards.** Translucent panels
  `linear-gradient(180deg,rgba(255,255,255,.82),rgba(255,255,255,.6))` +
  `backdrop-filter: blur(16–22px) saturate(140%)`, `1px` near-white border,
  large soft shadows (`0 30px 70px -28px rgba(20,40,80,.34)`), big radii
  (16–26px). Hover = lift (`translateY(-3/-4px)`) + deeper shadow.
- **Metallic borders & buttons.** Brushed-metal secondary buttons
  `linear-gradient(180deg,#fdfefe,#eef1f5 48%,#dde2e9 52%,#f3f5f8)` with
  `inset 0 1px 0 #fff`. Primary CTA = blue gradient `#3b7bff→#1f57e6` with an
  animated specular **sweep** highlight.
- **Score gauge style.** Circular SVG ring, `r≈60`, `stroke-width≈11`, track
  `#eaedf2`, progress gradient `#3b7bff→#2f6bff→#16c8e6`, `stroke-linecap:round`,
  rotated -90°, blue drop-shadow, animated count-up (cubic ease-out, ~1.15s).
  Center number in **Rubik 800**, optional label below in **Heebo 600** `#9aa1ab`.
  Used at multiple sizes: hero ~148px, preview ~92px, "others" ~78px, compare
  ~66px, details header ~56px.
- **High-tech automotive dashboard feel.** Outline (stroke) iconography, metric
  "gauge strip" cards with mini progress bars, segmented cost-breakdown bar,
  "ההתאמה הטובה ביותר" hero badge, live "מנתח בזמן אמת" pulse dot.
- **Hebrew RTL.** Root is `direction:rtl`; all copy is Hebrew. (Production
  `recommendations.html` already declares `<html lang="he" dir="rtl">`.)
- **Sporty but clean.** Display type **Rubik** (700–800, negative letter-spacing)
  for headings/numbers; **Heebo** for body. Tight, confident, lots of whitespace,
  restrained accent color, no clutter.

Accent palette: primary `#2f6bff` (blue), secondary `#1f57e6`, cyan `#16c8e6`;
success `#1f8a4d`; risk/warn amber. (Production `primary`=`#2f6bff`,
`secondary`=`#1f57e6` — identical.)

---

## 3. Exact data contract for recommendation outputs

Source of truth: `/advisor_api` JSON → consumed by `renderResults()` in
`recommendations.js`. **This shape is frozen.** A redesign may only re-skin how
these values are displayed; it must not rename, drop, reshape, or re-fetch them.

### 3.1 Top-level response (MUST be preserved exactly)

| Field | Type | Notes |
|---|---|---|
| `search_performed` | bool | Whether a web search ran. |
| `search_queries` | string[] | Queries run; rendered into `#advisor-search-queries`. |
| `recommended_cars` | object[] | The car list (see 3.2). Empty array ⇒ "no results" empty state. |
| `history_id` | string/int/null | Stored as `currentHistoryId`; drives research card + analytics. |

Also passed through by the client but not part of the redesign surface:
`request_id` (error/telemetry only).

### 3.2 `recommended_cars[]` item fields (ALL preserved)

`brand`, `model`, `year`, `fuel`, `gear`, `turbo`, `engine_cc`,
`price_range_nis`, `avg_fuel_consumption`, `fuel_method`, `annual_fee`,
`fee_method`, `reliability_score`, `reliability_method`, `maintenance_cost`,
`maintenance_method`, `safety_rating`, `safety_method`, `insurance_cost`,
`insurance_method`, `resale_value`, `resale_method`, `performance_score`,
`performance_method`, `comfort_features`, `comfort_method`, `suitability`,
`suitability_method`, `market_supply`, `supply_method`, `fit_score`,
`comparison_comment`, `not_recommended_reason`, `annual_energy_cost`,
`annual_fuel_cost`, `total_annual_cost`.

Type / formatting rules already enforced in `recommendations.js` (must be kept):

- All values may be `null`/missing → render `-` (helper `h()` / `safeNum()`).
- `price_range_nis` may be a 2-element array `[min,max]` → `formatPriceRange()`
  renders `min–max ₪`.
- `avg_fuel_consumption` unit is fuel-type dependent: EV → `קוט״ש ל-100 ק״מ`,
  else `ק״מ לליטר` (`isEVFuel()` checks `fuel`).
- `*_method` strings are already Hebrew, free text; field **labels** come from
  `methodLabelMap` (`fuel_method`, `fee_method`, `reliability_method`,
  `maintenance_method`, `safety_method`, `insurance_method`, `resale_method`,
  `performance_method`, `comfort_method`, `suitability_method`, `supply_method`).
  A `*_method` row only renders when its value is truthy.
- `reliability_score` is converted to a **risk grade** (`getReliabilityGrade`):
  ≥7 → "נמוכה" (low risk, green), ≥4 → "בינונית" (amber), else "גבוהה" (red),
  null → "לא ידוע". Higher score = lower maintenance risk.
- `fit_score` is a 0–100 number; cards sort **descending** by it. Color buckets:
  ≥85 green, ≥70 amber, else neutral.
- Fallback copy (must remain): `comparison_comment` →
  `advisorCopy.fitFallback`; `not_recommended_reason` → `advisorCopy.caveatFallback`.
- **Security:** every dynamic value is HTML-escaped (`escapeHtml`/`h`) before
  `innerHTML`. Any redesign MUST keep escaping every field it interpolates.

> ⚠️ The current production card additionally surfaces `market_supply` (as a chip
> + table) but does **not** yet render `annual_energy_cost`, `annual_fuel_cost`,
> or `total_annual_cost` inside the per-car card (`total_annual_cost` is only used
> by the "cheapest to own" highlight). These three cost fields are in the
> contract because the prototype's cost-breakdown panel expects them; a future
> redesign may surface them, but must read them from these exact keys.

---

## 4. Required UI → data mapping (prototype → contract)

The "best match" hero = `recommended_cars` sorted by `fit_score` desc, `[0]`.

| Prototype element | Data source |
|---|---|
| Best-match **hero card** | top car by `fit_score` |
| **Score gauge** (hero + per-card + compare) | `fit_score` |
| "**למה זה מתאים לך**" / verdict | `comparison_comment` (fallback `advisorCopy.fitFallback`) |
| "**הערות סיכון**" / caveats | `not_recommended_reason` (fallback `advisorCopy.caveatFallback`) |
| **Annual ownership cost** | `total_annual_cost` |
| **Fuel / energy cost** | `annual_energy_cost` (EV) / `annual_fuel_cost` (fuel) |
| **Maintenance** | `maintenance_cost` + `maintenance_method` |
| **Insurance** | `insurance_cost` + `insurance_method` |
| **License fee** (אגרה) | `annual_fee` + `fee_method` |
| **Reliability / maintenance risk** | `reliability_score` (→ risk grade) + `reliability_method` |
| **Safety** | `safety_rating` + `safety_method` |
| **Resale / value retention** | `resale_value` + `resale_method` |
| **Performance** | `performance_score` + `performance_method` |
| **Comfort** | `comfort_features` + `comfort_method` |
| **Suitability** | `suitability` + `suitability_method` |
| **Market supply** | `market_supply` + `supply_method` |
| **Search queries** | `search_queries` |
| Identity / spec chips | `brand`, `model`, `year`, `fuel`, `gear`, `turbo`, `engine_cc`, `price_range_nis`, `avg_fuel_consumption` (+ `fuel_method`) |

---

## 5. Selector preservation list (MUST NOT break)

These IDs are queried by `recommendations.js` and/or are the integration
contract between template and JS. A redesign may restyle them but must keep the
**same element with the same `id`**, in the DOM, before `recommendations.js`
runs.

| Selector (`#id`) | Used for |
|---|---|
| `advisor-results` | Results `<section>` shown/hidden by the open/close flow. |
| `advisor-search-queries` | Container for `search_queries` list. |
| `advisor-profile-summary` | Driver-profile summary chips (`renderProfileSummary`). |
| `advisor-highlight-cards` | Best-fit / cheapest / most-reliable highlight grid. |
| `advisor-table-wrapper` | Per-car card+table render target (`tableWrapper.innerHTML`). |
| `advisorResultReadyPanel` | "✅ התוצאה מוכנה" panel (`resultReadyPanel`). |
| `advisorOpenResultButton` | "פתח תוצאה" button (opens results). |

**Also load-bearing (do not break even though not in the headline list):**
`advisor-form`, `advisor-submit` (+ inner `.spinner` / `.button-text`),
`advisor-error`, `advisor-consent`, the form field `name`s consumed by
`buildPayload()`/`applyHistoryProfile()` (`budget_min/max`, `year_min/max`,
`fuels_he`, `gears_he`, `turbo_choice_he`, `main_use`, `annual_km`, `driver_age`,
`license_years`, `driver_gender`, `body_style`, `driving_style`, `seats_choice`,
`family_size`, `cargo_need`, `insurance_history`, `violations`,
`safety_required_radio`, `consider_supply`, `fuel_price`, `electricity_price`,
`excluded_colors`, weight sliders `w_reliability`/`w_resale`/`w_fuel`/
`w_performance`/`w_comfort`), the timing-banner IDs
(`advisorTimingBanner`, `advisorElapsedTime`, `advisorEtaText`,
`advisorStatusText`, `advisorProgressRing`, gradient `advisorRainbowGradient`),
all `advisorResearch*` IDs (research card/form), and the
`meta[name="csrf-token"]` tag. Changing/removing any of these breaks submit,
history rehydration, the progress ring, or the research flow.

---

## 6. Future implementation warnings

1. **Visual reference only.** Do **not** copy `support.js`, `image-slot.js`, or
   any `.dc.html` into production. They are a proprietary design-canvas runtime
   (`<x-dc>`, `<sc-if>`, `<sc-for>`, `<dc-import>`, `<image-slot>`) and are not
   browser-shippable app code. Re-implement the *look* with the existing
   `.yr-*` classes in `theme.css` + Tailwind, not by importing prototype JS.
2. **Reuse the existing design system.** `static/theme.css` already ships the
   matching white/chrome tokens and classes: `.yr-chrome-card`, `.yr-glass`,
   `.yr-chip`/`.yr-chip--chrome`, `.yr-gauge`, `.yr-btn`/`.yr-btn--chrome`,
   `.yr-risk-note`(`--warn`/`--info`), `.yr-section-header`, `.yr-grid*`,
   `.yr-form`, `.yr-chrome-text`, `.yr-accent-text`, `.yr-header`, `.yr-brand`,
   `.yr-nav-pill`, `.yr-footer`, `.yr-rise`. Prefer these over new CSS.
3. **Keep all field rendering & escaping.** Every `recommended_cars[]` field and
   `*_method` row currently rendered must remain reachable, and every
   interpolated value must stay HTML-escaped (`escapeHtml`/`h`). Backend already
   sanitizes; the client escape is the second layer — do not drop it.
4. **Sort + fallback semantics are part of the contract.** Keep `fit_score`
   descending sort, the reliability→risk-grade mapping (higher score = lower
   risk), the fit-score color buckets, and the `comparison_comment` /
   `not_recommended_reason` fallbacks.
5. **Compliance copy is mandatory.** The disclaimers ("התאמת העדפות בלבד", the
   amber "סיכונים / הסתייגויות", "אינו מהווה ייעוץ", the "Fit Score" explainer)
   are legally-reviewed and covered by tests — they must survive the redesign.
   The prototype's marketing tone (e.g. "הרכב המושלם") must not replace them.
6. **Do not touch backend / API / data.** No changes to backend logic, API
   routes, `/advisor_api`, Gemini/model calls, DB schema, `.env`, secrets,
   tokens, or deployment config. No paid API calls. RTL (`dir="rtl"`,
   `lang="he"`) and the `csp_nonce` on inline scripts must be preserved.
7. **Cost fields not yet on-screen.** `annual_energy_cost`, `annual_fuel_cost`,
   `total_annual_cost` exist in the contract but are not all rendered in the
   current per-car card. If a redesign adds the prototype's cost-breakdown
   panel, it must read these exact keys (and tolerate `null`).
