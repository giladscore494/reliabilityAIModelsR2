# QA Report

- Commit: 36fff6fd8bad3de2e5afbc4b6f0e8eb9426f83bb
- python -m compileall -q my-flask-app: PASS
- python -m pytest -q: PASS

## Manual verification (to run on deploy)
- [ ] GET /healthz → 200
- [ ] /login → 302 to Google
- [ ] /auth callback completes without TypeError; user logged in
- [ ] POST /analyze returns ok:true and contains micro_reliability/timeline_plan/sim_model
- [ ] Repeat analyze → cache hit (no extra AI call)
