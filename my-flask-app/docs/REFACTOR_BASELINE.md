# Refactor Baseline (Phase 0)

Baseline captured before starting the infrastructure maintainability refactor on
branch `copilot/refactor-infrastructure-for-maintenance`.

## Environment

```
SECRET_KEY=test-key
DATABASE_URL=sqlite:///:memory:
SKIP_CREATE_ALL=1
```

## `python -m compileall -q app main.py`

OK — exit code 0, no compile errors.

## `pytest -q`

```
356 passed, 19 warnings in ~9s
```

(Warnings are pre-existing `datetime.utcnow()` and SQLAlchemy 2.0 `Query.get()`
deprecations — not in scope for this refactor.)

## `flask --app main:create_app db heads`

```
bb03_research_260425 (head)
```

Single head, no merge needed.

## `flask --app main:create_app db current`

When run against an empty in-memory SQLite there is no current revision —
expected, the migrations table is created on first `db upgrade`.

## Decision

App imports cleanly and tests pass. Safe to continue with subsequent phases.
