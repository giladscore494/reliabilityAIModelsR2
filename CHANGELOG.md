# Changelog

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
