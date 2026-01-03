# QA Report

- Commit: 09ac2089e37d687c22ff7f0e182730aa5b20a89e
- python -m compileall -q my-flask-app: PASS
- python -m pytest -q: PASS

## Manual verification (to run on deploy)
- [ ] GET /healthz → 200
- [ ] /login → 302 to Google
- [ ] /auth callback completes without TypeError; user logged in
- [ ] POST /analyze returns ok:true and contains micro_reliability/timeline_plan/sim_model
- [ ] Repeat analyze → cache hit (no extra AI call)
