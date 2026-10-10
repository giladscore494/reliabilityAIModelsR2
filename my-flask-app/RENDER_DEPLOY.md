# Deploy to Render (Railway -> Render)

## 1) Create the service
Option A (Blueprint):
- In Render: **New > Blueprint**
- Select your GitHub repo
- Render will read `render.yaml` and set build/start commands.

Option B (Manual Web Service):
- New > Web Service > Connect repo
- Set **Root Directory** to `my-flask-app` (repo root contains docs/tests; the app code lives here)
- Build Command: `pip install -r requirements.txt`
- Predeploy/Release Command: `flask --app main:create_app db upgrade && flask --app main:create_app db current && python -c "import os; from sqlalchemy import create_engine, inspect; url = os.environ.get('DATABASE_URL') or os.environ.get('SQLALCHEMY_DATABASE_URI'); assert url, 'DATABASE_URL missing'; eng = create_engine(url); insp = inspect(eng); assert insp.has_table('legal_acceptance'), 'legal_acceptance table missing after db upgrade'; print('OK: legal_acceptance exists')"`
 - Start Command (recommended): `flask --app main:create_app db upgrade && flask --app main:create_app db current && gunicorn "main:create_app()" --bind 0.0.0.0:$PORT --timeout 180 --graceful-timeout 30 --keep-alive 5 --workers 2`

## 2) Environment variables (Render > Service > Environment)
These must be present (app will hard-fail on Render without `SECRET_KEY`/`DATABASE_URL`):
- `SECRET_KEY` (required, no default in production)
- `DATABASE_URL` (required on Render; use the Internal Postgres URL)
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GEMINI_API_KEY`
- `GEMINI_RELIABILITY_MODEL_ID` (optional, default: `gemini-3.5-flash`)
- `GEMINI_RECOMMENDER_MODEL_ID` (optional, default: `gemini-3.5-flash`)
- `GEMINI_COMPARE_MODEL_ID` (optional, default: `gemini-3.5-flash`; falls back to `GEMINI_RECOMMENDER_MODEL_ID` if set)
- `APP_TZ=Asia/Jerusalem` (explicitly set production timezone)
- `OWNER_EMAILS` (comma-separated, lowercase)
- `OWNER_BYPASS_QUOTA` (`0` or `1`, controls owner quota bypass)
- `ADVISOR_OWNER_ONLY` (`0` or `1`, restricts advisor/recommendations to owners)
- `RELIABILITY_OWNER_ONLY` (`0` or `1`, default `1`; restricts Vehicle Review to `OWNER_EMAIL`; no need to set it in deployment)
- `CANONICAL_BASE_URL=https://yedaarechev.com` (callback + redirects use apex)
- `WEB_CONCURRENCY` (optional, defaults to 2 gunicorn workers)
- `POSTHOG_API_KEY` (optional; PostHog analytics API key. If empty/missing, analytics are silently disabled)
- `POSTHOG_HOST` (optional; default `https://us.i.posthog.com`)
- `OWNER_EMAIL` (optional; single email address of the site owner for the owner management UI, e.g. `gilad@example.com`)

### Comparison V3 (the `/compare` engine; see `docs/COMPARISON_V3.md`)
- `TRIPY_BASE_URL` (secret; the TRIPY deployment, e.g. `https://<tripy-host>`) and `TRIPY_FACTS_TOKEN` (secret; sent as `Authorization: Bearer`, accepted by TRIPY only on `/api/facts/v1/*`). Without them `/compare` answers `facts_unavailable` (503); it never falls back to demo data.
- The engine is a code default (`DEFAULT_COMPARISON_ENGINE` in `app/services/comparison/model_config.py`), not an env var. Row explanations use `DEFAULT_COMPARISON_V2_MODEL_ID` (code); the summary uses `COMPARISON_SUMMARY_MODEL`; JEV uses `TYPESAFE_API_KEY` / `JEV_MODEL` below. V3 makes no enrichment calls, so the enrichment variables below apply to V2 only.

### Comparison V2 (stored history; selectable in code only; see `docs/COMPARISON_V2.md`)
- `COMPARISON_V2_ENABLED` no longer selects an engine (it is ignored).
- `COMPARISON_ENRICHMENT_MODEL` (default `gemini-3.1-pro-preview`; grounded official-source extraction, technical + commercial task per car). If this variable is set in the Render dashboard it overrides the default — remove it (or set it to `gemini-3.1-pro-preview`).
- `COMPARISON_SUMMARY_MODEL` (default `gemini-3.8-flash`; one ungrounded summary after the JEV decision)
- `TYPESAFE_API_KEY` (secret; server-side only)
- `JEV_MODEL` (no default — set it to an id/alias printed by `python -m scripts.jev_models`; an id that `GET /v1/models` does not list is never used)
- `TYPESAFE_BASE_URL` (optional, default `https://api.typesafe.ai`)
- `COMPARISON_V2_OFFLINE_MODE` (optional, default `false`; `true` = Level 1.5 only, zero remote calls)
- `COMPARISON_ENRICHMENT_TIMEOUT_SEC` (optional, default `125`; provider-level HTTP timeout per enrichment task, each task gets its own window), `JEV_TIMEOUT_SEC` (optional, default `30`), `COMPARISON_SUMMARY_TIMEOUT_SEC` (optional, default `25`)
- `COMPARISON_ENRICHMENT_CONCURRENCY` (optional, default `0` = all tasks concurrent; do not set it below 6 for 3 cars), `COMPARISON_V2_RESULT_TTL_HOURS` (optional, default `24`)
- Optional enrichment knobs: `COMPARISON_ENRICHMENT_THINKING_LEVEL` (`LOW`), `COMPARISON_ENRICHMENT_MAX_OUTPUT_TOKENS` (`32768`), `COMPARISON_ENRICHMENT_URL_CONTEXT` (`true`), `COMPARISON_ENRICHMENT_SPLIT` (`true`), `COMPARISON_ENRICHMENT_TEMPERATURE` (unset = model default), `COMPARISON_GROUNDING_RESOLVE_REDIRECTS` (`true`), `COMPARISON_GROUNDING_MIN_TIER` (`site`), `COMPARISON_V2_SERVER_TIMEOUT_SEC` (auto-detected from gunicorn `--timeout`)
- Uses the existing `GEMINI_API_KEY`. Migration `cc01_vehicle_enrichment_cache` runs via the existing `preDeployCommand`.
- gunicorn `--timeout 240` (render.yaml / Procfile). Production was observed running `--timeout 180`, i.e. the dashboard start command overrides render.yaml: update it there. The pipeline reads the effective timeout and caps enrichment/JEV/summary to it (worst case at 180: enrichment 135s, then JEV and summary shrink to the ~33s left), so the worker is never killed after a successful enrichment — but 240 leaves the full JEV/summary windows.
- Live check of one car (two paid calls, safe output): `python -m scripts.enrichment_diagnostic audi` / `bmw` / `tucson technical`.

## 3) Google OAuth redirect URI (IMPORTANT)
In Google Cloud Console > APIs & Services > Credentials > OAuth 2.0 Client ID:
Add **the exact** redirect URI(s) that your app will use:

- Custom domain (already in code):
  - `https://yedaarechev.com/auth` (www redirects to apex before auth)

- Render default domain:
  - `https://my-flask-app.onrender.com/auth`

Notes:
- Do **not** use placeholders like `<YOUR-RENDER-SERVICE>` or angle brackets — Google will reject it.
- No trailing spaces, must be HTTPS.
- After first deploy, copy the real service URL from Render (Dashboard > your service) and paste it exactly.

## 4) Database note
Render (or other providers) may provide `postgres://...`.
The app normalizes it to `postgresql://...` for SQLAlchemy compatibility.
