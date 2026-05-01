# Changelog

## 2026-04-30
### Data Quality Indicator – Visual Anchor (בטוח משפטית)

- **`static/script.js`**: Added `buildDataQualityIndicator(data, infoReview)` function that replaces the old score block in `renderAnalyzeResult`. The component includes:
  - **5-bar visual meter** (`role="meter"`, `aria-valuenow`, `aria-valuemin="0"`, `aria-valuemax="5"`, `aria-label="איכות המידע הזמין על הרכב"`): 1/5 bars (orange) for `חסרה`, 3/5 bars (amber) for `חלקית`, 5/5 bars (green) for `טובה`.
  - **Fallback state** when `data_quality_label` is absent: `aria-busy="true"`, 0/5 bars, text "מידע על איכות הניתוח טרם נטען".
  - **Sub-label** explaining it measures data availability, not car quality.
  - **Source chips** (`<dl>` semantic): 🇮🇱 Israeli sources, 📚 global sources (from `source_count` + `source_scope_label`), ⚠️ weak-sources warning (from `weakly_sourced`).
  - **Decision readiness badge** from `decision_readiness` field (`חסר מידע קריטי` / `נדרש אימות נוסף` / `מוכן לבדיקה מקצועית`) with color-coded border.
  - **Prominent disclaimer** "המערכת לא קובעת אם לקנות את הרכב, אלא מציפה מה לבדוק." integrated into the component.
- **`templates/reliability_app.html`**: Updated `reliability-score-container` div to `w-full mb-10` (RTL-friendly, full width).
- **`templates/example.html`**: Updated `reliability-score-container` div to `w-full mb-8` (RTL-friendly, full width).
- **`tests/test_data_quality_indicator.py`** (new): Contract tests covering `derive_information_status` field presence, `sanitize_analyze_response` pass-through of `source_count`/`source_scope_label`/`weakly_sourced`, deprecated score key absence, template `reliability-score-container` id, and `script.js` ARIA marker presence.
- **API contract unchanged**: No new fields added to `analyze_service.py` or `derive_information_quality_review`. All UI data comes from existing fields: `data_quality_label`, `source_count`, `source_scope_label`, `weakly_sourced`, `decision_readiness`.
- **RTL + Accessibility**: Component uses `dir="rtl"` context already set on `<html>`, chips in `<dl>` with screen-reader `<dt>` labels, meter with full ARIA attributes.

## 2026-04-29
### Decision-Support Positioning Alignment

- **`landing.html`**: Updated title, Hero headline, sub-headline, and CTA copy to lead with "לפני שתחתום על הרכב הבא – דע מה לבדוק." Replaced "ציון אמינות" / "בדיקת אמינות" language with "תחומי סיכון", "מה לבדוק לפני קנייה". Added explanatory sub-text alongside CTA: "כלי שמכין אותך לבדיקה — לא ממליץ לקנות או לא לקנות." Updated gallery CTA text and disclaimer copy.
- **`example.html`**: Added "how-to-read" banner at the top of example pages explaining the analysis surfaces risk areas rather than making buy/no-buy decisions. Renamed section headings from "תקלות נפוצות / עלויות אחזקה / מתחרים" to "תחומי סיכון / רגישות עלויות / מה עוד לא ידוע" to align with the new positioning.
- **`reliability_app.html`**: Renamed "אי-ודאויות" tab label → **"מה עוד לא ידוע"** for accessibility and human-friendliness. `data-tab="competitors"` attribute unchanged to preserve JS tab-switching.
- **`script.js`**:
  - Updated `competitorsContainer` intro text to be more actionable ("נקודות שעדיין חסר עליהן מידע ושכדאי לוודא מול המוכר/מוסך בדיקה").
  - `faultsContainer`: when `data.common_issues` or `data.recommended_checks` exist, they are rendered as sub-lists "תקלות מתועדות בדגם" / "בדיקות קונקרטיות מומלצות" inside the "תחומי סיכון" tab — restoring concrete value for history records using the legacy schema.
  - `costsContainer`: when `data.avg_repair_cost_ILS` or `data.issues_with_costs` exist, they are rendered as a "טווחי עלויות משוערים" sub-block inside the "רגישות עלויות" tab.
- **`tests/test_app.py`**: Replaced fragile string-matching assertions (`console.info('[ANALYZE_START]'`, `rawText = await response.text();`, etc.) with a size-based sanity check and functional marker checks. Kept assertions for `showAnalyzeError(` and absence of legacy `alert(...)`.
- **`tests/test_reliability_fallback.py`**: Added three new error-contract tests (`test_analyze_error_contract_*`) that verify `/analyze` always returns `request_id`, `ok: false`, and `error.message` for validation failures, AI failures, and `ok=false` AI responses.


### Task 1 — PostHog Analytics (7-point funnel)
- Added `posthog` SDK and `app/utils/analytics.py` with `track_event()` helper (silent no-op when `POSTHOG_API_KEY` is missing).
- Instrumented 7 funnel events: `landing_viewed`, `example_viewed`, `signup_clicked`, `signup_completed`, `analyze_completed`, `compare_completed`, `feedback_given`.
- PostHog JS snippet added to landing, compare, dashboard, recommendations, and example templates (conditional on key).
- Anonymous visitor tracking via `yrc_anon` httponly cookie.
- Updated CSP to allow PostHog domains.

### Task 2 — Public Example Previews
- New Alembic migration adding `is_public_example` + `example_slug` columns to `search_history`.
- New public routes: `GET /example/<slug>` (full analysis view), `GET /api/examples` (JSON for landing cards).
- Landing page updated with hero CTA + example gallery section.
- Compare GET and Recommendations GET now publicly accessible (POST routes remain `@login_required`).
- Login modal component (`_login_modal.html`) with `showLoginModal(source)` JS helper.
- Seed script `scripts/seed_public_examples.py` for managing public examples.
- Legal enforcement allowlist updated for public routes.

### Task 3 — CTA Feedback (thumbs up/down)
- New Alembic migration creating `feedback` table with UPSERT support.
- New `POST /api/feedback` endpoint with ownership validation.
- Feedback UI (thumbs up/down) added to analyze results and compare results.

### Follow-up — Owner UI for Managing Public Examples
- New `OWNER_EMAIL` env var and `app/utils/auth_helpers.py` with `is_owner()` + `@owner_required` decorator.
- New owner-only routes: `GET /owner/examples` and `POST /owner/examples/update`.
- Owner dashboard template with checkboxes to select up to 4 public examples.
- Auto-slug generator, validation (strict ASCII slug regex), ownership checks.
- Navbar shows "🛠 ניהול דוגמאות" link for owner users only.

### Cross-cutting
- Comprehensive pytest tests for all three features.
- New env vars: `POSTHOG_API_KEY`, `POSTHOG_HOST` (both optional).

## 2026-01-03
- Enforced canonical redirect to `yedaarechev.com`, unified request IDs, and added request timing logs.
- Standardized JSON responses (`ok`/`error`/`request_id`) and hardened frontend fetch parsing.
- Added quota reservation/finalize/refund flow (`quota_reservation` table) so failed AI calls refund daily quota.
- Updated CSP Report-Only allowlist and removed deprecated `google-generativeai` dependency.
- Fixed CI workflow indentation and added pytest coverage for redirect, schema, and quota fairness.

### Verification
- Install deps: `cd my-flask-app && python -m pip install -r requirements.txt`
- Run tests: `cd my-flask-app && pytest`
- Manual checks: `www.yedaarechev.com` → 301 apex, `/analyze` responses include `ok` + `request_id`.

### New env vars
- `SIMULATE_AI_FAIL=1` (optional; force AI failure to exercise quota refund paths)
