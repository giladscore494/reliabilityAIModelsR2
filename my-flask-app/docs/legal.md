# Legal acceptance & enforcement

## Acceptance proof
The service stores a LegalAcceptance audit record per user and version:
- user id
- terms/privacy versions
- accepted timestamp (UTC)
- accepted IP (normalized: IPv4 /24, IPv6 /64, or hashed if `LEGAL_IP_HASH_SALT` is set)
- user-agent
- source (web)

This keeps a minimal, auditable trail without retaining full IP precision.

## Version updates
To require re-acceptance, bump the version constants:
- `TERMS_VERSION` (env or `app/legal.py`)
- `PRIVACY_VERSION` (env or `app/legal.py`)

Any user whose stored versions do not match the current versions receives a 412 response
and must re-accept.

## Central enforcement (protected endpoints)
Legal acceptance is enforced centrally in `app.factory.create_app()` for all authenticated
requests **except** an explicit allowlist:
- `/terms`, `/privacy`
- `/api/legal/accept`
- `/login`, `/logout`, `/auth`
- `/static/*`, `/assets/*`, `/favicon.ico`, `/healthz`, `/`, `/recommendations`

Every other authenticated endpoint is treated as protected. This includes AI calls,
history reads, and personalized analysis (e.g., `/analyze`, `/advisor_api`, `/api/history/*`,
`/search-details/*`, `/dashboard`).

When adding new endpoints that trigger AI calls or return personalized data, ensure they
remain outside the allowlist so the central check continues to apply automatically.
