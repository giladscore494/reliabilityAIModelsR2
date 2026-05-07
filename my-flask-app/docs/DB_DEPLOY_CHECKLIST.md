# DB Deploy Checklist

This document is the source of truth for safely deploying schema changes to the
Yeda Rechev production database.

The application **never** runs `db.create_all()` in production and **never**
issues runtime `ALTER TABLE` statements. All schema changes go through
**Alembic / Flask-Migrate**.

## Pre-deploy

1. **Verify `DATABASE_URL` points to the intended production DB.**
   - In the deploy shell: `echo "$DATABASE_URL" | sed 's/:[^:@]*@/:***@/'`
   - Confirm host / database name match the production instance.

2. **Inspect Alembic state.**
   ```sh
   flask --app main:create_app db heads
   flask --app main:create_app db current
   ```
   - There must be **exactly one head**. If there are multiple, create a merge
     migration first (`flask db merge -m "..." <head1> <head2>`).

3. **Apply migrations against the target DB.**
   ```sh
   flask --app main:create_app db upgrade
   ```

4. **Verify `current == head` after upgrade.**
   ```sh
   flask --app main:create_app db current   # should match the value of `db heads`
   ```

5. **Sanity-check the schema** (only if a non-trivial migration ran):
   - Tables expected by the active features must exist:
     `user`, `search_history`, `advisor_history`, `comparison_history`,
     `daily_quota_usage`, `quota_reservation`, `ip_rate_limit`,
     `legal_acceptance`, `legal_feature_acceptance`, `research_consent`,
     `research_response_session`, `feedback`, `public_examples`.

## Deploy

- Render `preDeployCommand` runs migrations: `flask --app main:create_app db upgrade && flask --app main:create_app db current`.
- Render `startCommand` only starts gunicorn — no migration step in the start command.
- The `Procfile` likewise only invokes gunicorn (it does **not** run `flask db upgrade`).
- gunicorn workers therefore start only after migrations succeed; if migrations fail, the deploy is rejected before any worker boots.

## Post-deploy

1. **App import works.**
   - The Render deploy log should show `[BOOT] ...` lines and no traceback.
2. **`/healthz` responds.**
   ```sh
   curl -fsS https://<host>/healthz
   ```
3. **Active routes respond as expected:**
   - `/`           — landing
   - `/app`        — reliability analysis
   - `/compare`    — vehicle comparison
   - `/advisor`    — recommendations
   - `/dashboard`  — requires login
   - `/terms`, `/privacy` — legal pages
4. **No missing-table / missing-column errors in logs.**
   - Tail the Render logs for `OperationalError`, `UndefinedTable`,
     `UndefinedColumn`, or `relation ... does not exist`.

## What you must NOT do

- ❌ Do **not** run `db.create_all()` against the production DB.
- ❌ Do **not** issue ad-hoc `ALTER TABLE` against production.
- ❌ Do **not** run `flask db stamp head` unless this is an **emergency**, you
   have manually verified the schema matches the head revision, and you have
   an explicit reason that you have written down. Stamping advances the
   Alembic pointer without running migrations and can mask drift.

## Render configuration

- `preDeployCommand`: `flask --app main:create_app db upgrade && flask --app main:create_app db current`
- `startCommand`: `gunicorn "main:create_app()" ...` (gunicorn only — no migration step in the start command)
- `Procfile`: `web: gunicorn "main:create_app()" ...` (gunicorn only — used by Heroku-style hosts; no migration step)

This separation guarantees migrations run **once per deploy**, not once per
worker boot, and that gunicorn workers fail fast if the DB is not at head.

## Emergency recovery

If you need to reconcile a drifted production schema:

1. Take a snapshot of the production DB.
2. Locally reproduce against a copy.
3. Decide whether the right action is a new migration, a manual one-off SQL
   script reviewed by a second engineer, or `flask db stamp <rev>`.
4. Document the action in the PR / incident notes before applying it.

The legacy runtime helpers previously in `app/utils/db_bootstrap.py` have
been moved to `scripts/dev/emergency_db_bootstrap.py`. They are **not**
imported by application startup. They remain available as a manual,
opt-in escape hatch (still gated behind `ENABLE_RUNTIME_DB_BOOTSTRAP=1`) and
are **never** invoked in production.
