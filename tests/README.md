# Tests

```bash
uv run --with pytest==9.1.1 pytest          # or: pip install -r requirements-dev.txt && pytest
```

Deliberately narrow at the start. It covers the pure modules that decide what a
distribution *means*, and nothing that needs a database, Redis, or a network call:

| File | What it protects |
|------|------------------|
| `test_panel_aggregate.py` | The denominator. Which calls count, which are excluded, and what the author is told about the ones that were not. |
| `test_panel_runner.py` | Failure classification and retry. A truncated response must retry and, if it never succeeds, must not be labelled as something the model chose to do. |
| `test_oracle_stems.py` | The instrument. Rendered item text, the anchors, and the refusal to substitute a stem the run was not labelled with. |

**What is not here yet**, and what each thing actually costs:

- **The leak audit, the blinded-context builder, and the sim-ready renderers.** Nothing
  blocks these. `create_engine()` does not connect, so importing `backend.models.database`
  needs `POSTGRES_URL` to be *set*, not reachable — a dummy value in a conftest fixture is
  enough. These are pure functions over text.
- **`oracle_service.check_content_parity`.** Needs a stub session: it uses `db` for a
  single `query().filter().first()`. Real Postgres would be better and is not worth it —
  the schema lives in `../direct-sim` migrations, so a test database here would depend on
  another repo's migration ordering.
- **The API endpoints.** Genuinely blocked. `backend/app/main.py` runs
  `Base.metadata.create_all()` and the readiness probes at module scope, so importing the
  app connects. `scripts/dump_openapi.py` works around it by monkeypatching both before
  the import; a TestClient fixture could do the same, or startup could move into a
  lifespan handler. The second is the real fix and is a change to production startup.

Two rules for anything added here:

- **No network, no database, no Redis.** A test that needs a real OpenRouter call is not
  a test of ours; fake the client as `test_panel_runner.py` does.
- **A test that encodes a research decision says so in a comment.** Thresholds like
  `MIN_USEFUL_N` are judgement calls the group may move; a test that fails after such a
  move should make it obvious that the *decision* changed, not that the code broke.
