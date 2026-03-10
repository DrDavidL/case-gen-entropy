# CLAUDE.md — Project Guide for AI Assistants

## Project Overview

Medical Case Generator: an AI-powered system that creates educational emergency medicine cases with tiered diagnostic frameworks and evidence-based likelihood ratios. Supports two output formats: **Sim-Ready** (default, writes to a simulator-compatible database) and **Beta** (full LR/entropy schema). Backend is FastAPI, frontend is Streamlit, deployed via Docker to Azure Container Instances.

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
    sim_ready_transform.py — render_sim_ready_content(), default builders, extract_door_chart_section()
frontend/
  app.py               — Streamlit UI (4 tabs: Generate, Edit, View, Export) with output format selector.
                         Sim-ready Edit: split content editor (Clinical Dashboard / Door Chart), native
                         Streamlit inputs for simulator fields. Export: both sim DB files + in-memory LR data.
  auth.py              — Frontend auth session management
```

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
- **Auth**: All mutating endpoints require HTTP Basic Auth (`Depends(verify_credentials)`). Read-only and sim-ready list endpoints do not.

## Dual Output Format System

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

## Commands

```bash
# Local dev (requires .env, Redis, PostgreSQL)
python start_backend.py        # FastAPI on :8000
python start_frontend.py       # Streamlit on :8501

# Docker
docker compose up --build      # All services (Redis, backend, frontend)

# Deploy to Azure
./setup-azure.sh               # One-time infra setup
./create-deployment-config.sh  # Generate secrets config (local only)
./deploy-manual.sh             # Deploy to ACI
```

## API Endpoints (Auth Required = *)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/` | No | Health check |
| POST | `/preview-case` | * | Generate case preview (accepts `output_format`), store in Redis |
| PUT | `/edit-case` | * | Update session data |
| GET | `/session/{id}` | * | Get session data |
| POST | `/finalize-case` | * | Save to database (routes by `output_format`) |
| POST | `/generate-case` | * | Generate + save (legacy, beta only) |
| GET | `/sim-ready/cases` | No | List all sim-ready cases |
| GET | `/sim-ready/case/{id}` | No | Get a single sim-ready case |
| GET | `/cases` | No | List all beta cases |
| GET | `/case/{id}/output-files` | No | Export 3 JSON files (beta) |
| GET | `/case/{id}/simulator-exports` | No | Export metadata (beta) |
| GET | `/case/{id}/simulator-export/lr-matrix-csv` | No | LR matrix CSV (beta) |
| GET | `/case/{id}/simulator-export/lr-matrix-excel` | No | LR matrix Excel (beta) |
| GET | `/case/{id}/simulator-export/prior-probabilities` | No | Prior probs JSON (beta) |
| GET | `/case/{id}/simulator-export/case-summary` | No | Case summary text (beta) |
| GET | `/case/{id}/debug-lr-data` | No | Debug raw LR data (beta) |

## Common Pitfalls

- OpenAI structured outputs require `client.beta.chat.completions.parse()` — the `beta` prefix is mandatory.
- `POSTGRES_URL_SIM_READY` must be set for sim-ready format to work. Without it, `get_sim_ready_db()` raises `ValueError`. The env var is read at import time — restart the backend after changing `.env`.
- The sim-ready DB already has a `case_details` table with existing data. `SimReadyBase.metadata.create_all()` uses `checkfirst=True` by default and won't alter existing tables.
- `_sim_ready_to_case_details()` maps `paragraph_summary` -> `presentation` and `patient_approach.communication_style` -> `patient_personality`. This adapter is what lets the unchanged framework/LR pipeline work with sim-ready data.
- `deployment-config.yaml` contains secrets and is git-ignored. Use the template or GitHub Actions secrets.
- The frontend finalize-case call must include auth headers (`get_auth_header()`).
- Database retry logic (`retry_db_operation`) only handles SSL reconnection errors — other DB errors propagate immediately.
- Probability distributions for each tier must sum to 1.0; validation happens at export time, not generation time.
- The frontend merges finalize response into `st.session_state.generated_case` (not replaces) so LR/framework data survives for export. If the user navigates away or refreshes, this in-memory data is lost — the Export tab shows a warning.
- `CaseSaveRequest.rendered_content` is optional. When provided (user edited content), it overrides `render_sim_ready_content()`. When `None`, content is re-rendered from structured data.
- The Door Chart section delimiter (`## PATIENT DOOR CHART and Learner Instructions`) is critical — the frontend splits content on this exact string for editing, and the simulator parses by it. Warnings in the UI tell users not to alter it.
- Sim-ready Export tab depends on two sources: DB (via `/sim-ready/case/{id}`) for persisted fields, and `st.session_state` for LR/framework data that was never persisted.

## Package Management

- Use `uv` for Python package management, not `pip install`.
- Dependencies are in `requirements.txt` (no pyproject.toml).
