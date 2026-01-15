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
 - Start Command (recommended): `flask --app main:create_app db upgrade && flask --app main:create_app db current && python -c "import os; from sqlalchemy import create_engine, inspect; url = os.environ.get('DATABASE_URL') or os.environ.get('SQLALCHEMY_DATABASE_URI'); assert url, 'DATABASE_URL missing'; eng = create_engine(url); insp = inspect(eng); assert insp.has_table('legal_acceptance'), 'legal_acceptance table missing after db upgrade'; print('OK: legal_acceptance exists')" && gunicorn "main:create_app()" --bind 0.0.0.0:$PORT --timeout 180 --graceful-timeout 30 --keep-alive 5 --workers 2`

## 2) Environment variables (Render > Service > Environment)
These must be present (app will hard-fail on Render without `SECRET_KEY`/`DATABASE_URL`):
- `SECRET_KEY` (required, no default in production)
- `DATABASE_URL` (required on Render; use the Internal Postgres URL)
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GEMINI_API_KEY`
- `APP_TZ=Asia/Jerusalem` (explicitly set production timezone)
- `OWNER_EMAILS` (comma-separated, lowercase)
- `OWNER_BYPASS_QUOTA` (`0` or `1`, controls owner quota bypass)
- `ADVISOR_OWNER_ONLY` (`0` or `1`, restricts advisor/recommendations to owners)
- `CANONICAL_BASE_URL=https://yedaarechev.com` (callback + redirects use apex)
- `WEB_CONCURRENCY` (optional, defaults to 2 gunicorn workers)

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
