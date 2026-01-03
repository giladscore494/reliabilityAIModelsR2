# Implementation Summary – Final Stabilization

## Current State (Ready to Ship)
- **OAuth/Login:** GET /login → Google OAuth redirect (apex redirect_uri from CANONICAL_BASE_URL). /auth handles callback. www → apex redirect happens before host checks. Origin/Referer checks skip auth GET. Login link is an `<a>` (no form submit).
- **/analyze pipeline:** validate/normalize (strict allowlist) → stable cache_key (includes usage_profile) → per-user cache lookup (TTL) → quota reserve (miss only) → single LLM call → sanitize → deterministic post-process (micro_reliability, timeline_plan, sim_model) → sanitize → persist SearchHistory with cache_key → api_ok.
- **Deterministic add-ons:** micro_reliability, 36-month timeline_plan (JSON schedules/costs), sim_model coefficients for client sliders. No extra LLM calls; cache hits return full data with zero LLM cost.
- **Concurrency hardening:** per-IP rate limit with unique bucket + upsert; daily quota unique row + with_for_update; reservation reserve/finalize/refund with TTL cleanup. Cache scoped to user_id.
- **Security/caching:** Host allowlist + CSRF Origin/Referer on POST; CSP Report-Only; MAX_CONTENT_LENGTH; sanitization allowlist extended to new fields; SearchHistory cache scoped to user.

## Remaining TODOs
- None pending; focus on keeping migrations applied via `flask db upgrade` in deployments.

## Tests Added/Kept
- AI failure quota refund; atomic quota; IP rate-limit uniqueness.
- /analyze contract: micro_reliability, timeline_plan, sim_model present; cache-hit path skips extra LLM (mocked).
- Login redirect test: GET /login returns 302 to accounts.google.com with apex redirect_uri.

## Deployment Notes
- Preferred canonical domain: https://yedaarechev.com (no www).
- Redirect URI for Google Console: `https://yedaarechev.com/auth` (add localhost/127.0.0.1 variants for dev).
- Run migrations (Flask-Migrate/Alembic) before deploy: includes cache_key column/index in SearchHistory and prior quota/rate-limit tables.
