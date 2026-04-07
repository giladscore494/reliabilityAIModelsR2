# Changelog

## 2026-04-07
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
