# CLAUDE.md — Project Guide for AI Assistants

> ## ⚠️ Read this first
>
> **This file describes the system as it exists today. `Decisions.md` describes where it is
> going, and several accepted decisions supersede what is documented here.** Where they
> conflict, `Decisions.md` wins.
>
> Building from this file alone will reproduce the architecture we are actively moving away
> from — in particular the dual-output-format design in the section marked
> **[SUPERSEDED by ADR-001]** below.
>
> **Documentation map:** `docs/README.md` — routing table for deep reference, loaded on demand.
> **Planned work:** `ToDos.md`. **Companion repo:** `../direct-sim` (simulator, shared database).
> **Setting up on another machine:** `docs/new-machine.md` — verified clone-to-running steps,
> the secrets git cannot give you, and the four things that bite.

## Push back before building

Say plainly when a decision is unsustainable or against best practice, including when it is the
author's decision. Name the recommended option and what the alternative costs concretely. Do not
present a real tradeoff as a neutral choice, and do not let a permission request ("may I add this
dependency?") read as neutral when you already know the answer. Then build what the author
decides, noting the assumption.

This repo's own history is the argument. Every expensive item in `Decisions.md` was cheap at the
moment it was chosen and expensive by the time it surfaced:

- **ADR-012** — a mutable `:v1` image tag made `az containerapp update` a silent no-op. Deploys
  reported success while the backend served a four-month-old image, from 2026-03-10 to
  2026-07-28. Nobody decided to do that; it was the default nobody questioned.
- **ADR-001** — the sim-ready path generated the diagnostic framework and full LR matrix, then
  discarded them into `st.session_state`. Every case paid for the analysis and threw it away, and
  the two databases could not be joined to recover it.
- **The `sim_image_links` leak** — state initialised with `if "key" not in st.session_state`
  carried one case's image links into another and saved them.

Two things in this system make silent failure especially costly, so weigh them heavily:

- **The Oracle is a measurement instrument.** Changing the rating stem invalidates every
  distribution generated under the old wording. Anything that alters what the panel is asked, or
  lets it rate a case the learner will not see, is a correctness problem and not a preference.
- **`case_details` is shared with the simulator.** A schema or content change here reaches
  learners through `../direct-sim`. Cross-repo blast radius deserves the objection stated up
  front, not a note at the end.

Settled questions live in `Decisions.md` as ADRs. Check it before reopening one, and if a
decision genuinely should be revisited, say so and mark the old ADR `SUPERSEDED` rather than
quietly building against the new view.

## Project Overview

Medical Case Generator: an AI-powered system that creates educational emergency medicine cases with tiered diagnostic frameworks and evidence-based likelihood ratios. Supports two output formats: **Sim-Ready** (default, writes to a simulator-compatible database) and **Beta** (full LR/entropy schema). Backend is FastAPI, frontend is Streamlit, deployed via Docker to Azure Container Apps.

> **[SUPERSEDED by ADR-001]** The two-format split is being retired in favor of one canonical
> case record. See `docs/architecture-target.md`.

## Architecture

```
backend/
  app/main.py          — FastAPI app, all API endpoints (sim-ready + beta paths)
  models/
    database.py        — SQLAlchemy ORM: beta tables (Case, DiagnosticFramework, FeatureLikelihoodRatio)
                         + sim-ready table (CaseDetailSimReady on separate DB engine)
    schemas.py         — Pydantic response models (CaseInput, CaseResponse, SimReadyCaseResponse)
    editing_schemas.py — Session/editing models (CaseSaveRequest with output_format + rendered_content override, SimReadyCasePreviewResponse)
    structured_outputs.py — OpenAI structured output models:
                            - CaseDetailsStructured (beta, original)
                            - SimReadyCaseDetailsStructured (sim-ready, expanded with OLDCARTS HPI, Door Chart, etc.)
  utils/
    llm_service.py     — LLMService class: beta + sim-ready LLM calls, _sim_ready_to_case_details adapter
    auth.py            — HTTP Basic Auth (env-based credentials)
    simulator_export.py — Pandas-based export to CSV/Excel/JSON/text (beta format only)
    sim_ready_transform.py — render_sim_ready_content(), default builders, coerce_json_field()
frontend/                — LEGACY Streamlit UI. Retired at Phase 4e; still the only editor today
  app.py               — 4 tabs (Generate, Edit, View, Export) with output format selector.
                         Sim-ready Edit: split content editor (Clinical Dashboard / Door Chart), native
                         Streamlit inputs for simulator fields. Export: both sim DB files + in-memory LR data.
  auth.py              — Frontend auth session management
web/                     — React SPA (ADR-020). Served by the backend at /app, NOT at / --
                         GET / stays the build stamp every deploy is verified against (ADR-012).
                         Case list, case view, structured editor, Generate, Final Orders +
                         Oracle, framework/LR editing. Streamlit still runs alongside it
  openapi.json         — committed schema; the contract the TS types are generated from
  src/lib/types.gen.ts — GENERATED, do not hand-edit
  src/lib/api.ts       — typed client
scripts/
  dump_openapi.py      — rewrites web/openapi.json from the live route table
```

**Two directories, on purpose.** `frontend/` is Streamlit and `web/` is React; they coexist until
Phase 4e. `web/` is what gets served after cutover.

**After changing any request or response model**, regenerate both or the frontend types go stale:

```bash
uv run python scripts/dump_openapi.py && (cd web && npm run gen:types)
```

**An endpoint the SPA consumes needs an explicit `response_model`.** Without one FastAPI documents
it as an empty object, `openapi-typescript` emits `{}`, and every field access on the client is a
type error — generation that looks protective while describing nothing. This was true of all four
SPA endpoints until 2026-07-31.

## Key Technical Details

- **LLM calls** use `client.beta.chat.completions.parse()` (OpenAI structured outputs). NOT `client.chat.completions.parse()`.
- **3 sequential LLM calls** per case: case_details -> diagnostic_framework -> feature_likelihood_ratios. Each depends on the previous.
- **Sim-ready cases** use `generate_sim_ready_case_details()` which produces `SimReadyCaseDetailsStructured` with expanded fields. The `_sim_ready_to_case_details()` adapter converts it to `CaseDetailsStructured` so the downstream framework/LR calls work unchanged.
- LLM calls run via `asyncio.to_thread()` to avoid blocking the FastAPI event loop.
- LLM retry logic: exponential backoff on rate limits, timeouts, connection errors, 5xx. Configurable via `LLM_REQUEST_TIMEOUT`, `LLM_MAX_RETRIES`, `LLM_RETRY_BASE_DELAY` env vars.
- **Beta Database** (`POSTGRES_URL`): PostgreSQL (Neon) with SQLAlchemy. Stores cases, diagnostic_frameworks, feature_likelihood_ratios tables. Connection pool: size=5, max_overflow=10, pre_ping=True, recycle=3600s. SSL required.
- **Sim-Ready Database** (`POSTGRES_URL_SIM_READY`): Separate PostgreSQL (Neon) with its own engine. Stores to existing `case_details` table. Optional — if not configured, only beta format is available.
- **Redis**: Used for editing sessions only (1-hour TTL). Key format: `session:{uuid}`.
- **ORM relationships**: `Case.frameworks` and `Case.feature_lrs` use `lazy="selectin"` to avoid N+1 queries.
- **Auth**: HTTP Basic (`Depends(verify_credentials)`) on all mutating endpoints **and, since
  2026-08-01, on every read that returns case content** — the case list, a case, its structured
  record, its analysis, its Oracle results, and the beta exports. Three things stay open on
  purpose: `GET /` (the build stamp, also the Docker healthcheck),
  `GET /sim-ready/case/{id}/final-orders` (the simulator's read, specified unauthenticated in
  `direct-sim/FINAL_ORDERS_TODO.md`), and the pure-render endpoints. The SPA sends Basic from its
  own login form rather than a JWT — see `ADR-021` for why, and for the revisit triggers.

## Dual Output Format System

> **[SUPERSEDED by ADR-001, ADR-002]** — accurate description of current behavior, but this is
> the design being replaced. Two problems drive the change: the sim-ready path **generates the
> diagnostic framework and full LR matrix and then discards them** (they survive only in
> `st.session_state`), and the two destinations are separate Neon projects so LR data cannot be
> joined to `case_details` at all. Target: one canonical structured record per case, markdown
> derived from it, everything in one database. See `docs/architecture-target.md`.

### Sim-Ready (default)

The sim-ready format generates cases matching the simulator's `case_details` table:

| Column | Type | Source |
|--------|------|--------|
| `saved_name` | VARCHAR | `case_title` from LLM or user-provided title |
| `content` | TEXT | Rendered markdown from `render_sim_ready_content()` |
| `custom_input` | JSON | User-editable; default from `build_default_custom_input()` |
| `custom_evaluation` | JSON | User-editable; default from `build_default_custom_evaluation()` |
| `allow_orders` | BOOLEAN | User toggle, default `True` |
| `learner_tasks` | TEXT | User-editable markdown; default from `build_default_learner_tasks()` |

The `content` markdown has two major sections separated by `## PATIENT DOOR CHART and Learner Instructions`:
1. **Clinical Dashboard**: paragraph summary, patient approach, OLDCARTS HPI, PMHx, SHx, FHx, medications/allergies, ROS, physical exam, diagnostic reasoning, teaching points
2. **Door Chart**: patient name, age, legal sex, chief complaint, clinical setting, vital signs

### Beta (legacy)

Saves to 3 tables: `cases`, `diagnostic_frameworks`, `feature_likelihood_ratios`. Full LR/entropy data is persisted and exportable.

### How the format choice flows through the system

1. `CaseInput.output_format` ("sim_ready" or "beta") is set in the frontend
2. `/preview-case` branches: sim-ready calls `generate_sim_ready_case_details_async()`, beta calls `generate_case_details_async()`
3. For sim-ready, `_sim_ready_to_case_details()` adapts the expanded model for the unchanged framework/LR pipeline
4. `SessionData.output_format` tracks the choice through the Redis session
5. `/finalize-case` branches: sim-ready uses user-edited `rendered_content` (or re-renders) + saves to sim-ready DB, beta saves to 3 beta tables
6. LR/entropy data is generated for both formats but only persisted for beta. For sim-ready, the frontend merges the finalize response into existing session state so LR/framework data remains available for export in the same session.

## Data Flow

### Sim-Ready Path
1. User submits description + primary diagnosis + `output_format="sim_ready"`
2. `POST /preview-case` -> `generate_sim_ready_case_details` -> `_sim_ready_to_case_details` adapter -> framework + LR calls -> Redis session
3. User edits content (Clinical Dashboard + Door Chart markdown split) + simulator fields in Streamlit. Sim fields use native Streamlit inputs (text areas, dynamic list for image links) — no raw JSON editing.
4. `POST /finalize-case` -> uses `rendered_content` from frontend if edited, else falls back to `render_sim_ready_content()` -> save to `case_details` table in sim-ready DB -> delete Redis session
5. Retrievable via `GET /sim-ready/cases` and `GET /sim-ready/case/{id}`
6. Export tab: simulator files downloaded from `/sim-ready/case/{id}`, diagnostic data (framework, LRs, a priori probs) available from in-memory session state

### Beta Path
1. User submits description + primary diagnosis + `output_format="beta"`
2. `POST /preview-case` -> `generate_case_details` -> framework + LR calls -> Redis session
3. User edits all fields in Streamlit
4. `POST /finalize-case` -> save to cases, diagnostic_frameworks, feature_likelihood_ratios tables -> delete Redis session
5. Export endpoints query beta DB and transform to CSV/Excel/JSON

## Environment Variables

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `OPENAI_API_KEY` | Yes | — | OpenAI API auth |
| `POSTGRES_URL` | Yes | — | Beta DB connection string |
| `POSTGRES_URL_SIM_READY` | No | — | Sim-ready DB connection string (enables sim-ready format) |
| `REDIS_URL` | No | `redis://localhost:6379/0` | Redis connection |
| `BACKEND_URL` | No | `http://localhost:8000` | Frontend -> backend URL |
| `APP_USERNAME` | No | `admin` | Basic auth username |
| `APP_PASSWORD` | No | `dhds-bypass` | Basic auth password |
| `LLM_REQUEST_TIMEOUT` | No | `120` | OpenAI request timeout (seconds) |
| `LLM_MAX_RETRIES` | No | `3` | Max LLM retry attempts |
| `LLM_RETRY_BASE_DELAY` | No | `2.0` | Base delay between retries (seconds) |
| `OPENROUTER_API_KEY` | Yes* | — | Required when `LLM_PROVIDER=openrouter` (the default). No silent fallback |
| `LLM_PROVIDER` | No | `openrouter` | `openrouter` or `openai` |
| `CASE_GEN_MODEL` | No | `openai/gpt-4o-2024-08-06` | Generation-pipeline model |
| `ORACLE_MODEL` | No | `openai/gpt-5.6-sol` | Oracle panel model. Bare `openai/gpt-5.6` does **not** exist |
| `ORACLE_REASONING_EFFORT` | No | `medium` | Confirmed by the research group, ADR-014 |
| `ORACLE_STEM_VERSION` | No | `v2_revised` | `v1_original` or `v2_revised`. Stem approved 2026-08-04, ADR-014 |
| `ORACLE_CONCURRENCY` | No | `8` | Panel semaphore. Sequential would take ~30 min/case |
| `ORACLE_DEFAULT_SPECIALTY` | No | generic | Fallback for the roster's subspecialist seat |
| `PANEL_REQUEST_TIMEOUT` | No | `180` | Per-panelist request timeout (seconds) |
| `PANEL_MAX_RETRIES` | No | `3` | Per-panelist retry attempts |
| `PANEL_RETRY_BASE_DELAY` | No | `2.0` | Base delay between panelist retries (seconds) |

## Secret scanning

Per clone, once:

```bash
brew install gitleaks     # the hook uses the system binary, see below
uvx pre-commit install
```

`.pre-commit-config.yaml` pins `gitleaks-system`, not the default `gitleaks` hook: the default
builds from source and pre-commit hardcodes `GOTOOLCHAIN=local`, so it cannot fetch the Go
toolchain gitleaks now requires. `.github/workflows/secret-scan.yml` is the backstop that a
`--no-verify` cannot skip, and it checks out with `fetch-depth: 0` so history is scanned too.

**The two repos use different hook mechanisms, deliberately.** `direct-sim` already had
`.githooks/pre-commit` covering ruff, gitleaks, large files, `uv.lock`, and `pip-audit`; it just
needed `git config core.hooksPath .githooks` to be active. This repo had no hook to preserve, so it
uses the pre-commit framework. Both now fail the commit when the gitleaks binary is missing rather
than skipping the scan, so `brew install gitleaks` is a hard prerequisite in either repo.

## Commands

```bash
# Tests. No database, Redis, or network needed -- see tests/README.md for the coverage
# boundary and what has to be refactored before the rest can be tested at all.
pip install -r requirements-dev.txt && pytest
uv run --with pytest==9.1.1 pytest      # without installing dev deps into the venv

# Local dev (requires .env, Redis, PostgreSQL)
python start_backend.py        # FastAPI on :8000
python start_frontend.py       # Streamlit on :8501

# React SPA (web/) — port 5174, not 5173, so it can run alongside direct-sim's dev server
cd web && npm ci && npm run dev
cd web && npm run build && npm run lint   # tsc -b runs inside build

# Docker
docker compose up --build      # All services (Redis, backend, frontend)

# Deploy to Azure Container Apps
./deploy-aca.sh                # First-time setup: creates RG, ACR, env, deploys all 3 apps
./deploy-aca.sh redeploy       # Rebuild images + update running apps after code changes

# Tear down Azure resources
az group delete --name casegen-rg --yes
```

## Azure Container Apps Deployment

**Resource naming**: `casegen-rg` (resource group), `casegenacr` (ACR), `casegen-env` (environment)

**Apps**:
| App | Ingress | CPU/Mem | Purpose |
|-----|---------|---------|---------|
| `casegen-redis` | internal (TCP 6379) | 0.25 / 0.5Gi | Session cache |
| `casegen-backend` | external (HTTP 8000) | 1.0 / 2.0Gi | FastAPI API |
| `casegen-frontend` | external (HTTP 8501) | 0.5 / 1.0Gi | Streamlit UI |

**URLs**:
- Frontend: `https://casegen-frontend.greenbush-b78bdd23.eastus.azurecontainerapps.io`
- Backend: `https://casegen-backend.greenbush-b78bdd23.eastus.azurecontainerapps.io`

**Secrets**: `OPENAI_API_KEY`, `POSTGRES_URL`, `APP_PASSWORD` are stored as Container Apps secrets (referenced via `secretref:`). Not passed as plaintext env vars.

**Deploying**: push to `main`. `.github/workflows/deploy.yml` builds both images in ACR and
updates both container apps. `./deploy-aca.sh redeploy` does the same thing manually.

**Images are tagged uniquely per build** (`<sha>-<timestamp>` from the script, `<sha>` from CI).
This is load-bearing, not cosmetic: Container Apps only creates a new revision when the revision
spec changes, so reusing a mutable `:v1` tag makes `az containerapp update` a silent no-op. That
went unnoticed from 2026-03-10 to 2026-07-28, during which the backend served a four-month-old
image while deploys reported success. See `Decisions.md` ADR-012.

**Verify every deploy**: `curl https://<backend>/` returns `build.git_sha` and
`build.build_time`; the Streamlit footer shows frontend and backend stamps side by side and warns
when they diverge. If a deploy "succeeded" but the SHA did not move, the revision did not roll.

**Legacy ACI scripts** (`setup-azure.sh`, `deploy-manual.sh`, `create-deployment-config.sh`) are
kept for reference only.

**An older generation of this app** (`backend-app`, `frontend-app`, `redis-app` on
`medical-case-env`, same resource group) ran live on Sept 2025 code against the same production
database until 2026-07-28. It is now scaled to zero with ingress disabled, pending deletion —
see `ToDos.md` "Deferred / revisit". Do not confuse it with the current apps, which are all
prefixed `casegen-`.

## API Endpoints (Auth Required = *)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/` | No | Health check |
| POST | `/preview-case` | * | Generate case preview (accepts `output_format`), store in Redis |
| PUT | `/edit-case` | * | Update session data |
| GET | `/session/{id}` | * | Get session data |
| POST | `/finalize-case` | * | Save to database (routes by `output_format`) |
| POST | `/generate-case` | * | Generate + save (legacy, beta only) |
| GET | `/auth/check` | * | Validate a credential without acting. The SPA's login `ADR-021` |
| POST | `/sim-ready/render-preview` | No | Render a structured record to markdown. Writes nothing |
| GET | `/sim-ready/cases` | * | List all sim-ready cases |
| GET | `/sim-ready/case/{id}` | * | Get a single sim-ready case |
| PUT | `/sim-ready/case/{id}` | * | Save an edited case. `save_mode` defaults to `new_version` |
| POST | `/sim-ready/case/{id}/copy` | * | Fork into a new simulator row + new family at v1 |
| POST | `/sim-ready/case/{id}/adopt` | * | First `case_version` for a pre-authoring-record case |
| PUT | `/sim-ready/case/{id}/analysis` | * | Edit LRs and tier priors **in place**, no new version `ADR-007` |
| GET | `/cases` | * | List all beta cases |
| GET | `/case/{id}/output-files` | * | Export 3 JSON files (beta) |
| GET | `/case/{id}/simulator-exports` | * | Export metadata (beta) |
| GET | `/case/{id}/simulator-export/lr-matrix-csv` | * | LR matrix CSV (beta) |
| GET | `/case/{id}/simulator-export/lr-matrix-excel` | * | LR matrix Excel (beta) |
| GET | `/case/{id}/simulator-export/prior-probabilities` | * | Prior probs JSON (beta) |
| GET | `/case/{id}/simulator-export/case-summary` | * | Case summary text (beta) |
| GET | `/case/{id}/debug-lr-data` | * | Debug raw LR data (beta) |
| POST | `/final-orders/propose` | * | Candidate Final Orders. Writes nothing |
| GET | `/sim-ready/case/{id}/final-orders` | No | Orders + suppression terms (the simulator's read) |
| PUT | `/sim-ready/case/{id}/final-orders` | * | Replace the list on the case's latest version |
| GET | `/sim-ready/case/{id}/oracle/preflight` | * | Blinded context + blocking leak audit |
| POST | `/sim-ready/case/{id}/oracle/run` | * | Queue the Oracle panel (background) |
| GET | `/sim-ready/case/{id}/oracle` | * | Distributions + item-quality flags |
| POST | `/sim-ready/case/{id}/resync` | * | Rebuild the structured record from edited markdown as a new version |
| GET | `/sim-ready/case/{id}/structured` | * | The canonical structured record + parity state `ADR-002` |
| PUT | `/sim-ready/case/{id}/structured` | * | Save structured fields; renderer writes the markdown `ADR-002` |
| GET | `/oracle/stems` | No | Both rating-stem versions, rendered side by side |
| POST | `/oracle/render-items` | No | Render learner items for a set of orders from the active stem |
| GET | `/oracle/roster` | No | Versioned panel roster + provider settings |

## Final Orders and the Oracle panel

Built 2026-07-29, **not yet applied to production**. Specs: `docs/final-orders-sct.md` (authoring,
the rating stem) and `docs/llm-panels.md` (roster, blinding, aggregation). Decisions: `ADR-004`,
`ADR-005`, `ADR-006`, `ADR-014`.

Four things that are easy to get wrong:

- **No Final Orders means no Oracle panel.** Not an optimisation — the research group's explicit
  condition. Zero rows is the entire opt-out mechanism; there is no global toggle.
- **All LLM calls go through OpenRouter** via `backend/utils/llm_client.py`. Model ids are
  provider-prefixed (`openai/gpt-4o-2024-08-06`) — the bare OpenAI ids do not resolve there.
  Reasoning effort is passed as `extra_body={"reasoning": {"effort": ...}}`, OpenRouter's unified
  parameter. There is **no fallback** to the OpenAI API: the OpenRouter key carries
  zero-data-retention and the direct key does not, so a silent switch would change the retention
  posture unnoticed.
- **The Oracle must rate the case the learner sees.** `check_content_parity()` blocks the panel
  when the structured record and `case_details.content` have diverged — by a hand-edit
  (`render_detached`) or by an in-place update (`content_drift`). Not overridable. The way out is
  `POST /sim-ready/case/{id}/resync`, which rebuilds the structured record from the edited markdown
  as a new version. Comparison is whitespace-insensitive so the editor's split/rejoin does not
  block a save that changed nothing.
- **The simulator does not read `GET /sim-ready/case/{id}/final-orders`.** As of 2026-08-04 it
  resolves suppression terms straight from `authoring.case_final_orders`, because suppression sits
  on the learner's request path and must not depend on this service being reachable. It does call
  `POST /oracle/render-items` for the phase-3 rating items — the stem is the instrument and has one
  renderer — so `CASE_GEN_URL` being wrong costs the ratings, not the suppression. The endpoint
  stays as the documented contract and for anything else that needs it.
- **Migration `0003_final_orders_and_panels` lives in `direct-sim`**, which owns the shared
  database's schema. This app probes with `final_orders_schema_ready()` and degrades to 503 rather
  than running DDL. That probe is deliberately separate from `authoring_schema_ready()`, so a
  missing 0003 does not also disable framework/LR persistence.
- **The rating stem is the measurement instrument.** Changing it invalidates every distribution
  generated under the old wording, so both versions are held in `backend/utils/oracle_stems.py` and
  stamped onto each run. Do not inline stem text anywhere else — render it from the registry.
- **Nearly every existing case has no `case_version`** (102 of 103 as of 2026-07-30), and every
  Final Orders / Oracle path resolves through one. Those cases are reached via
  `POST /sim-ready/case/{id}/adopt`, which rebuilds the structured record from the markdown the
  simulator already serves. Per case and author-initiated, not a bulk backfill — see ADR-019.
- **A blank `primary_diagnosis` blocks the panel and is not overridable.** `audit_leak` derives
  its search terms from that field, so an empty one passes having checked nothing. The leak
  override exists for a true hit with a benign explanation; it does not cover an audit that never
  ran.

## Common Pitfalls

- OpenAI structured outputs require `client.beta.chat.completions.parse()` — the `beta` prefix is mandatory.
- `POSTGRES_URL_SIM_READY` must be set for sim-ready format to work. Without it, `get_sim_ready_db()` raises `ValueError`. The env var is read at import time — restart the backend after changing `.env`.
- The sim-ready DB already has a `case_details` table with existing data. `SimReadyBase.metadata.create_all()` uses `checkfirst=True` by default and won't alter existing tables.
- `_sim_ready_to_case_details()` maps `paragraph_summary` -> `presentation` and `patient_approach.communication_style` -> `patient_personality`. This adapter is what lets the unchanged framework/LR pipeline work with sim-ready data.
- `deployment-config.yaml` (ACI legacy) and `.env` contain secrets and are git-ignored.
- The frontend finalize-case call must include auth headers (`get_auth_header()`).
- Database retry logic (`retry_db_operation`) only handles SSL reconnection errors — other DB errors propagate immediately.
- Probability distributions for each tier must sum to 1.0; validation happens at export time, not generation time.
- The frontend merges finalize response into `st.session_state.generated_case` (not replaces) so LR/framework data survives for export. If the user navigates away or refreshes, this in-memory data is lost — the Export tab shows a warning.
- `CaseSaveRequest.rendered_content` is optional. When provided (user edited content), it overrides `render_sim_ready_content()`. When `None`, content is re-rendered from structured data.
- The Door Chart section delimiter (`## PATIENT DOOR CHART and Learner Instructions`) is critical — the frontend splits content on this exact string for editing, and the simulator parses by it. Warnings in the UI tell users not to alter it.
- Sim-ready Export tab depends on two sources: DB (via `/sim-ready/case/{id}`) for persisted fields, and `st.session_state` for LR/framework data that was never persisted.

## Streamlit gotchas (`frontend/app.py`)

- **`st.rerun()` resets the active tab.** `st.tabs` tracks its selection client-side; a
  server-forced rerun rebuilds the widget tree and discards it, dropping the user on tab 1. For
  state mutations that stay within a tab, use `on_click` callbacks — they run before the script
  re-executes, so the new state is already present when widgets render. Reserve `st.rerun()` for
  deliberate end-of-flow transitions.
- **`st.success` / `st.error` inside a callback draws before the tab is laid out.** Stash the
  outcome in session state and let the script body render it (see `_regenerate_lrs`).
- **State derived once is never refreshed.** Anything initialised with
  `if "key" not in st.session_state` persists across case loads. `sim_image_links` did exactly
  this and leaked one case's image links into another, which then got saved. When loading a case,
  clear every derived key, not just the ones the loader happens to set.

## Package Management

- Use `uv` for Python package management, not `pip install`.
- Dependencies are in `requirements.txt` (no pyproject.toml).
