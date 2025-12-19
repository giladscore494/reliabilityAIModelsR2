# Deploy to Render

## Quick steps (Render Web Service)
1. Push this project to GitHub.
2. In Render: **New → Blueprint** and select the repo (it will use `render.yaml`).
3. Set environment variables in Render:
   - `SECRET_KEY` (required)
   - `DATABASE_URL` (optional, only if you use Postgres)
   - OAuth (only if you use Google login):
     - `GOOGLE_CLIENT_ID`
     - `GOOGLE_CLIENT_SECRET`
   - `OWNER_EMAILS` (optional, comma separated)

## OAuth redirect URI
Your app builds the redirect dynamically as: `https://<host>/auth`.
Add both of these to Google OAuth authorized redirect URIs:
- `https://yedaarechev.com/auth`
- `https://<YOUR-RENDER-SERVICE>.onrender.com/auth`

If you use a different custom domain, add `https://<your-domain>/auth` too.
