# Medical Case Generator

An AI-powered system that generates comprehensive emergency medicine cases with multi-tiered diagnostic frameworks and evidence-based likelihood ratios for medical education. Supports two output formats: **Sim-Ready** (default, writes directly to a simulator-compatible database) and **Beta** (full LR/entropy schema).

## Features

- **AI Case Generation**: Creates detailed cases from brief descriptions using OpenAI GPT-4o structured outputs
- **Sim-Ready Output (Default)**: Generates cases matching the simulator's `case_details` schema — including a full Clinical Dashboard, Door Chart, OLDCARTS HPI, and all standard medical history sections. Cases are saved directly to the simulator database.
- **Beta Output**: Full LR/entropy schema with multi-tier diagnostic frameworks, likelihood ratios, and export to CSV/Excel/JSON
- **Preview & Edit Workflow**: Generate, review, modify, then finalize cases via a session-based editing flow. Sim-ready content is editable in split view (Clinical Dashboard + Door Chart) with native form inputs for simulator fields — no raw JSON editing required.
- **Dual Database**: Sim-ready cases go to the simulator DB (`POSTGRES_URL_SIM_READY`); beta cases go to the internal DB (`POSTGRES_URL`)
- **Export Formats**: JSON, CSV, Excel — compatible with the [Transcript Feature Check Simulator](https://github.com/DrDavidL/transcript-feature-check). Both sim-ready and beta cases support full exports including diagnostic framework and likelihood ratio data.
- **Web Interface**: Streamlit frontend with 4 tabs (Generate, Edit, View, Export)

## Architecture

```
┌─────────────┐     HTTP      ┌──────────────┐     SQL      ┌─────────────────┐
│  Streamlit   │ ──────────── │   FastAPI     │ ──────────── │ PostgreSQL (Beta)│
│  Frontend    │  Basic Auth  │   Backend     │              │  POSTGRES_URL    │
└─────────────┘               └──────────────┘              └─────────────────┘
                                     │                              │
                              ┌──────┴──────┐              ┌───────┴─────────────┐
                              │             │              │ PostgreSQL (Sim-Ready)│
                         ┌────┴───┐   ┌─────┴────┐        │ POSTGRES_URL_SIM_READY│
                         │ Redis  │   │ OpenAI   │        │ (case_details table)  │
                         │Sessions│   │ GPT-4o   │        └───────────────────────┘
                         └────────┘   └──────────┘
```

| Component | Tech | Purpose |
|-----------|------|---------|
| Backend | FastAPI (Python 3.11) | REST API, LLM orchestration, data persistence |
| Frontend | Streamlit | Case creation, editing, visualization, export |
| Beta Database | PostgreSQL (Neon) | Internal case storage (cases, diagnostic_frameworks, feature_likelihood_ratios) |
| Sim-Ready Database | PostgreSQL (Neon) | Simulator-compatible case storage (case_details table) |
| Cache | Redis | Temporary editing sessions (1-hour TTL) |
| LLM | OpenAI GPT-4o | Structured case generation (3 sequential calls per case) |

## Quick Start

### Prerequisites

- Python 3.11+
- PostgreSQL database (e.g., [Neon](https://neon.tech))
- Redis (local or hosted)
- OpenAI API key

### Local Development

```bash
# Clone and setup
git clone <repo-url>
cd case-gen-entropy

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies (use uv)
uv pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your actual values (see Environment Variables below)

# Start backend (terminal 1)
python start_backend.py    # http://localhost:8000

# Start frontend (terminal 2)
python start_frontend.py   # http://localhost:8501
```

### Docker

```bash
docker compose up --build
```

This starts Redis, backend (:8000), and frontend (:8501) with hot-reload.

### Access

- Frontend: http://localhost:8501
- API docs: http://localhost:8000/docs
- Health check: http://localhost:8000/

## Environment Variables

```bash
# Required
OPENAI_API_KEY=sk-...              # OpenAI API key
POSTGRES_URL=postgresql://...      # Beta DB connection string (include sslmode=require for cloud)

# Sim-Ready database (required for sim-ready output format)
POSTGRES_URL_SIM_READY=postgresql://...  # Simulator DB with case_details table

# Optional
REDIS_URL=redis://localhost:6379/0 # Redis connection (default: localhost)
BACKEND_URL=http://localhost:8000  # Backend URL for frontend
APP_USERNAME=admin                 # Basic auth username
APP_PASSWORD=changeme              # Basic auth password

# LLM tuning
LLM_REQUEST_TIMEOUT=120            # OpenAI request timeout in seconds
LLM_MAX_RETRIES=3                  # Max retry attempts for LLM calls
LLM_RETRY_BASE_DELAY=2.0          # Base delay between retries in seconds
```

## Usage Workflow

1. **Generate**: Enter a brief case description and primary diagnosis. Select output format: **Sim-Ready** (default) or **Beta**. The system makes 3 LLM calls (~15-30 seconds).
2. **Edit**: Review and modify generated content. Sim-ready format splits the content into an editable Clinical Dashboard and Door Chart (with warnings to preserve delimiters), plus native form inputs for simulator fields (prespecified results, image links, additional instructions, learner tasks, allow orders). Beta format shows the full LR/framework editors.
3. **Finalize**: Save the edited case. Sim-ready saves to the simulator database (`case_details` table). Beta saves to the internal database (3 tables).
4. **Export**: Download files for use with the simulator app:
   - **Sim-Ready**: Content markdown, custom input/evaluation JSON, learner tasks, full case JSON, plus diagnostic framework and likelihood ratio data (available in the same session)
   - **Beta**: LR Matrix (CSV/Excel), Prior Probabilities (JSON), Case Summary (text), original JSON files

### Sim-Ready Output Format

The sim-ready format generates cases matching the simulator's `case_details` table schema:

| Column | Type | Description |
|--------|------|-------------|
| `id` | SERIAL PK | Auto-generated |
| `saved_name` | VARCHAR | Case title (e.g., "Chest Pain with ECG Changes") |
| `content` | TEXT | Full markdown: Clinical Dashboard + Door Chart |
| `custom_input` | JSON | Prespecified results & image links for the simulator |
| `custom_evaluation` | JSON | Additional instructions for simulated patient behavior |
| `allow_orders` | BOOLEAN | Whether the simulator allows ordering tests (default: true) |
| `learner_tasks` | TEXT | Markdown task list for the learner |

The `content` field includes a **Clinical Dashboard** (paragraph summary, patient approach, OLDCARTS HPI, PMHx, SHx, FHx, medications/allergies, ROS, physical exam, diagnostic reasoning, teaching points) and a **Door Chart** section (patient demographics, chief complaint, vital signs) separated by a `## PATIENT DOOR CHART and Learner Instructions` heading for easy parsing.

## API Endpoints

### Case Generation (requires auth)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/preview-case` | Generate case preview, store in Redis session. Accepts `output_format`: `"sim_ready"` (default) or `"beta"` |
| `PUT` | `/edit-case` | Update case data in editing session |
| `GET` | `/session/{id}` | Retrieve session data |
| `POST` | `/finalize-case` | Save edited case. Routes to sim-ready DB or beta DB based on `output_format` |
| `POST` | `/generate-case` | Generate and save in one step (legacy, beta only) |

### Sim-Ready Case Retrieval (no auth)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/sim-ready/cases` | List all sim-ready cases from the simulator database |
| `GET` | `/sim-ready/case/{id}` | Retrieve a single sim-ready case with all fields |

### Beta Case Retrieval & Export (no auth)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/cases` | List all beta cases |
| `GET` | `/case/{id}/output-files` | Get 3 JSON output files |
| `GET` | `/case/{id}/simulator-exports` | Get export metadata |
| `GET` | `/case/{id}/simulator-export/lr-matrix-csv` | LR matrix as CSV |
| `GET` | `/case/{id}/simulator-export/lr-matrix-excel` | LR matrix as Excel |
| `GET` | `/case/{id}/simulator-export/prior-probabilities` | Prior probabilities JSON |
| `GET` | `/case/{id}/simulator-export/case-summary` | Case summary text |

## Project Structure

```
case-gen-entropy/
├── backend/
│   ├── app/main.py                  # FastAPI app, all endpoints (sim-ready + beta)
│   ├── models/
│   │   ├── database.py              # SQLAlchemy ORM (beta tables + CaseDetailSimReady)
│   │   ├── schemas.py               # API response schemas (CaseResponse, SimReadyCaseResponse)
│   │   ├── editing_schemas.py       # Session/editing schemas (CaseSaveRequest with output_format, rendered_content override)
│   │   └── structured_outputs.py    # OpenAI structured output models (original + SimReadyCaseDetailsStructured)
│   └── utils/
│       ├── llm_service.py           # LLM calls with retry logic (beta + sim-ready prompts)
│       ├── auth.py                  # HTTP Basic Auth
│       ├── simulator_export.py      # Export formatting (CSV, Excel, JSON) — beta only
│       └── sim_ready_transform.py   # Sim-ready markdown renderer + default builders
├── frontend/
│   ├── app.py                       # Streamlit UI (split content editor, native sim field inputs, dual export)
│   └── auth.py                      # Frontend auth management
├── Dockerfile.backend
├── Dockerfile.frontend
├── docker-compose.yml
├── deploy-aca.sh                    # Azure Container Apps deployment script
├── requirements.txt
├── start_backend.py
├── start_frontend.py
├── .env.example
├── CLAUDE.md                        # AI assistant project guide
├── SECURITY_GUIDE.md
└── DEPLOYMENT_GUIDE.md
```

## Deployment (Azure Container Apps)

Deployed to **Azure Container Apps** with 3 apps: Redis (internal), Backend (external), Frontend (external).

### First-time setup

Requires Azure CLI (`az login`) and a `.env` file with `OPENAI_API_KEY` and `POSTGRES_URL`.

```bash
./deploy-aca.sh                     # Creates resource group, ACR, environment, deploys all apps
```

This creates:
- **Resource group**: `casegen-rg`
- **Container registry**: `casegenacr` (images built in the cloud via ACR, no local Docker needed)
- **Environment**: `casegen-env` with Log Analytics
- **3 container apps**: `casegen-redis`, `casegen-backend`, `casegen-frontend`

### Redeploy after code changes

```bash
./deploy-aca.sh redeploy            # Rebuild images in ACR + update running apps
```

### Live URLs

- **Frontend**: https://casegen-frontend.greenbush-b78bdd23.eastus.azurecontainerapps.io
- **Backend API**: https://casegen-backend.greenbush-b78bdd23.eastus.azurecontainerapps.io
- **API docs**: https://casegen-backend.greenbush-b78bdd23.eastus.azurecontainerapps.io/docs

### Tear down

```bash
az group delete --name casegen-rg --yes   # Deletes everything
```

## Security

See [SECURITY_GUIDE.md](SECURITY_GUIDE.md). Key points:

- `.env` is git-ignored (contains secrets)
- Secrets (`OPENAI_API_KEY`, `POSTGRES_URL`, `APP_PASSWORD`) stored as Azure Container Apps secrets, not plaintext env vars
- All mutating API endpoints require HTTP Basic Auth
- Database connections use SSL (`sslmode=require`)

## License

MIT License
