# Deploy to Render (Railway -> Render)

## 1) Create the service
Option A (Blueprint):
- In Render: **New > Blueprint**
- Select your GitHub repo
- Render will read `render.yaml` and set build/start commands.

Option B (Manual Web Service):
- New > Web Service > Connect repo
- Build Command: `pip install -r requirements.txt`
- Start Command: `gunicorn "app:create_app()" --bind 0.0.0.0:$PORT --timeout 120`

## 2) Environment variables (Render > Service > Environment)
You need these (same names your app already uses):
- `SECRET_KEY`
- `DATABASE_URL`  (if you use Render Postgres, link it and set this)
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GEMINI_API_KEY`

## 3) Google OAuth redirect URI (IMPORTANT)
In Google Cloud Console > APIs & Services > Credentials > OAuth 2.0 Client ID:
Add **the exact** redirect URI(s) that your app will use:

- Custom domain (already in code):
  - `https://yedaarechev.com/auth`

- Render default domain (replace with your real Render URL):
  - `https://YOUR-SERVICE-NAME.onrender.com/auth`

Notes:
- Do **not** use placeholders like `<YOUR-RENDER-SERVICE>` or angle brackets — Google will reject it.
- No trailing spaces, must be HTTPS.
- After first deploy, copy the real service URL from Render (Dashboard > your service) and paste it exactly.

## 4) Database note
Render (or other providers) may provide `postgres://...`.
The app normalizes it to `postgresql://...` for SQLAlchemy compatibility.
