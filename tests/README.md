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

**What is not here yet**, in rough order of what would pay off next: the content-parity
check that blocks the Oracle (`oracle_service.check_content_parity`), the leak audit,
`sim_ready_transform` round-tripping, and the API endpoints. Those need the database
modules, which build engines at import time from `POSTGRES_URL`, so they need a fixture
that fakes or injects the engine before they can be tested at all. That refactor is the
prerequisite, not the tests.

Two rules for anything added here:

- **No network, no database, no Redis.** A test that needs a real OpenRouter call is not
  a test of ours; fake the client as `test_panel_runner.py` does.
- **A test that encodes a research decision says so in a comment.** Thresholds like
  `MIN_USEFUL_N` are judgement calls the group may move; a test that fails after such a
  move should make it obvious that the *decision* changed, not that the code broke.
