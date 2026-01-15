# DB Compatibility Checklist

Use this checklist for every deployment or schema change to avoid duplicate schema errors and ensure compatibility across environments.

## Pre-deploy
- Verify `DATABASE_URL` points to the intended environment (prod vs staging).
- Confirm backup/snapshot strategy is ready (manual snapshot or automated backup).
- Check current Alembic revision: `flask --app main:create_app db current`.
- Check pending heads: `flask --app main:create_app db heads`.
- Validate key tables/columns exist (as applicable):
  - `ip_rate_limit` table
  - `search_history.cache_key` column + index
  - `search_history.duration_ms` column
  - `advisor_history.duration_ms` column
  - `legal_acceptance` table

## Deploy
- Run `flask --app main:create_app db upgrade` and confirm success.
- Watch logs for any migration skip notices (tables/columns already exist).

## Post-deploy
- Re-check `flask --app main:create_app db current` matches the expected head.
- Verify `/healthz` responds with `ok: true`.
- Ensure runtime bootstrap is not mutating schema unexpectedly.
- Verify basic flows:
  - Login works
  - History/dashboard pages load (read-only)
  - AI request succeeds after legal acceptance

## Ongoing Guidance
- Never write migrations that assume a table/column/index doesn’t exist; use idempotent checks where feasible.
- Schema change policy: production schema changes must go through Alembic migrations only (no runtime ALTER/CREATE).
- Runtime bootstrap is disabled by default; enable it explicitly in dev only via `ENABLE_RUNTIME_DB_BOOTSTRAP=1`.
