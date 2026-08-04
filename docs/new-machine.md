# Starting on another computer

Verified end to end on 2026-08-03 by cloning both repos fresh and building them. Everything
here was run, not recalled.

**Read `../ToDos.md` "Start here" next.** This document gets you a working checkout; that one
tells you where the work is.

---

## 1. What is already running

Nothing here needs deploying to start work — both apps are live and current.

| | URL | Serves |
|---|---|---|
| Case generator API + React SPA | `https://casegen-backend.greenbush-b78bdd23.eastus.azurecontainerapps.io` | API at `/`, the editor at **`/app`** |
| Case generator, Streamlit | `https://casegen-frontend.greenbush-b78bdd23.eastus.azurecontainerapps.io` | The original UI, still live |
| Simulator | `https://direct-sim-beta.yellowmushroom-f2e62d1e.eastus.azurecontainerapps.io` | Learner-facing app + API |

Both repos deploy on push to `main`; there is no manual step. **Verify the deploy actually
rolled** — `GET /` on case-gen and `GET /api/version` on `direct-sim-beta` return `git_sha`.
A green workflow is not proof: `ADR-012` exists because a mutable image tag let a four-month-old
image serve behind green deploys for four months.

`direct-sim` (without `-beta`) is the legacy Streamlit app. It answers every path with its index
HTML, so a version check against it looks like a broken endpoint rather than the wrong host.

## 2. Clone and install

```bash
git clone https://github.com/DrDavidL/case-gen-entropy.git
git clone https://github.com/DrDavidL/direct-sim.git
```

Both repos are the source of truth. Nothing needed to build them is missing from git — checked
by cloning into a temp directory and building, not by inspection. `web/src/lib/types.gen.ts` is
generated (`npm run build` runs `gen:types` first) and deliberately not committed.

```bash
# case-gen-entropy
cd case-gen-entropy
uv venv && uv pip install -r requirements.txt
(cd web && npm ci)          # then `npm run dev` on :5174

# direct-sim
cd ../direct-sim
uv sync --frozen
```

## 3. Secrets — the only thing git cannot give you

Nothing below is in the repo, and nothing works without it. Values live in **Azure Container
Apps secrets** (`casegen-rg`), in Neon, and in the OpenRouter dashboard.

`case-gen-entropy/.env`:

| Variable | Notes |
|---|---|
| `OPENROUTER_API_KEY` | Every LLM call routes through OpenRouter. **This key carries zero-data-retention; the direct OpenAI key does not**, which is why there is no fallback (`ADR-016`) |
| `OPENAI_API_KEY` | Legacy path only, used when `LLM_PROVIDER=openai` |
| `POSTGRES_URL` | Legacy beta database (second Neon project) |
| `POSTGRES_URL_SIM_READY` | **The shared database.** Case content, authoring schema, Final Orders, panels |
| `REDIS_URL` | Editing sessions only, 1-hour TTL. `redis://localhost:6379/0` locally |
| `BACKEND_URL` | Streamlit → backend |
| `APP_USERNAME`, `APP_PASSWORD` | Single shared credential for both UIs (`ADR-021`) |

`direct-sim/.env`: `password`, `password_admin`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`,
`elevenlabs_api_key`, `url`.

`direct-sim/.streamlit/secrets.toml` holds the same four lowercase keys. **Alembic falls back to
it** for the database URL when `DATABASE_URL` is unset, so migrations need it or an explicit
`DATABASE_URL`.

## 4. Per-clone setup that is easy to miss

```bash
brew install gitleaks                              # hard prerequisite in BOTH repos
cd case-gen-entropy && uvx pre-commit install      # pre-commit framework
cd ../direct-sim && git config core.hooksPath .githooks   # this repo's own hooks
```

The two repos use **different hook mechanisms on purpose** — `direct-sim` already had
`.githooks/pre-commit`, case-gen had none. Both now **fail the commit when gitleaks is missing**
rather than skipping the scan, so the brew install is not optional.

`.github/workflows/secret-scan.yml` is the backstop a `--no-verify` cannot skip, and it checks
out with `fetch-depth: 0` so history is scanned too.

## 5. Four things that will bite you

**A stale terminal silently breaks every LLM call.** `load_dotenv()` does not override an
already-set variable and treats empty-string as set, so a shell that exports
`OPENROUTER_API_KEY=""` makes `.env` invisible and the backend raises at startup. Check:

```bash
python3 -c "import os; print(repr(os.environ.get('OPENROUTER_API_KEY')))"
# ''   -> poisoned, restart the terminal
# None -> correct
```

The shell profiles were cleaned on 2026-07-30, but a terminal opened before that still carries
the empty value and passes it to every child. This was hit again on 2026-08-01.

**Generate the OpenAPI schema against the pinned dependencies.** The JSON Schema is
version-sensitive: fastapi 0.104.1 / pydantic 2.5.0 wrap `$ref`s in `allOf` and omit
`additionalProperties`; newer releases do the opposite. An ad-hoc
`uv run --with fastapi --with pydantic` pulls the newest and produces a schema that does not
describe the deployed API. CI checks this and will fail the push.

```bash
uv run python scripts/dump_openapi.py && (cd web && npm run gen:types)
```

**The pre-push hook uses the system `ruff`, not the newer `uvx` one.** Run both
`ruff check .` and `ruff format --check .` with the system binary before pushing.

**Never put `git push` in the same shell invocation as `git add`/`git commit`.** The pre-push
hook matches `git push` at any line start and blocks the entire call, so the commit silently
does not run.

## 6. Schema changes are cross-repo

`direct-sim` owns Alembic and the shared database's migration history; the SQLAlchemy models
live in `case-gen-entropy`. This app **detects** tables and degrades rather than running DDL, so
the two never race.

Current revision: **`0004_panel_run_item_snapshot`**. Runbook: `direct-sim/MIGRATIONS.md`.
Adding a column means a migration there and a model change here, applied before the code that
needs it deploys.

## 7. Two live caveats

- **`APP_PASSWORD` is a publicly known default**, and it now guards a working browser editor.
  Deferred deliberately until the whole research team is reachable, because rotating locks out
  anyone holding the current value. **Keep the `/app` URL inside the research group until then.**
- **The rating stem has only a preliminary yes.** Runs stamp `stem_version`, so anything
  generated now is identifiable and discardable, but do not start accumulating research data
  under it until the group confirms.
