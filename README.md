# Autonomous Data Engineering Platform (ADEP)

A self-healing, multi-agent pipeline — built on CrewAI Flows — that turns raw CSV/Excel/JSON uploads into a validated DuckDB star-schema warehouse and a KPI report, driven by the business questions the user actually asked.

**Pipeline stages:** Profile → Validate Intent → Quality Gate (human-in-the-loop) → Schema Design → Transform (self-healing SQL build) → Analytics → Verify (independent answer re-computation) → Report.

See [`docs/PROJECT_DOCUMENTATION.md`](docs/PROJECT_DOCUMENTATION.md) for the full architecture write-up implementation-level details.

## Project Structure

```
Autonomous_Data_Engineering_Platform/
├── agents/              # AgentFactory + LLM provider selection (Ollama / Bedrock / cloud)
├── app/                 # FastAPI web dashboard: server, run manager, chat, intent intake
├── config/               # agents.yaml + tasks/*.yaml (one YAML per pipeline stage)
├── data/                 # Active dataset (CSV/Excel/JSON) — swap with switch_dataset.py
├── docs/                 # Architecture write-up
├── pipeline/              # DataEngineeringFlow's steps, shared state, telemetry
│   ├── core/               #   StepContext, DataEngineeringState, telemetry/tracing setup
│   └── steps/               #   One class per pipeline stage (ProfileStep, TransformStep, ...)
├── reports/               # Generated deliverables (profiling, quality, schema, SQL, KPIs, ...)
├── schemas/               # Pydantic I/O schemas for tools, tasks, and the API
├── tasks/                 # TaskFactory — builds CrewAI Tasks from config/tasks/*.yaml
├── tests/                 # pytest suite
├── tools/                 # Custom CrewAI BaseTool subclasses (DB, profiling, memory, ...)
├── utils/                 # Deterministic (non-agent) logic: schema planning, SQL execution,
│                           # warehouse metrics, answer verification
├── config.py              # Env-driven model/tracing configuration
├── crew.py                # DataEngineeringFlow — the Flow definition
├── main.py                # CLI entrypoint
└── switch_dataset.py       # Swap the active dataset in data/
```

## Setup

### 1. Requirements
Python 3.12 (via pyenv):
```bash
pyenv shell 3.12.11
```

### 2. Install dependencies
```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure environment
Copy `.env` and set the model(s) the pipeline should use. `PIPELINE_MODEL` is the default for every agent; `SQL_MODEL` and `BI_MODEL` optionally override it per role.

| Env var | Default | Purpose |
|---|---|---|
| `PIPELINE_MODEL` | `ollama/gemma4:31b-cloud` | Default model for all agents |
| `PIPELINE_BASE_URL` | `http://localhost:11434` | Ollama endpoint (ignored for non-Ollama models) |
| `SQL_MODEL` / `SQL_AWS_REGION` | — | Warehouse Architect (schema + SQL generation) override |
| `BI_MODEL` / `BI_AWS_REGION` | — | Analytics Engineer override |
| `LLM_TIMEOUT_SECONDS` | `300` | Per-LLM-call watchdog (Ollama/cloud providers) — a hung call errors instead of hanging the run |
| `LLM_MAX_RETRIES` | `2` | SDK-level retries per LLM call before the error propagates |
| `APPROVAL_TIMEOUT_SECONDS` | `3600` | How long a run waits at the quality-approval gate before auto-rejecting |
| `WEB_API_KEY` | — | API key required (as `X-API-Key` header) by the web API. Unset disables auth — **set it on any hosted deployment** |
| `CORS_ALLOWED_ORIGINS` | — | Comma-separated origins allowed cross-origin; unset means same-origin only |
| `REDIS_URL` | `redis://localhost:6379/0` | Celery broker/result backend and shared run state — required by the web dashboard |
| `PIPELINE_TIME_LIMIT_SECONDS` | `5400` | Whole-run ceiling enforced by Celery (soft limit fires 300s earlier to mark the run failed) |
| `CHAT_TIME_LIMIT_SECONDS` | `300` | Per-query ceiling on the chat queue |
| `CHAT_CONCURRENCY` | `2` | Chat worker concurrency (start.sh) |

Any `ollama/*` model requires a running Ollama server (`ollama serve`); any `bedrock/*` model requires AWS credentials in the environment; anything else is treated as an OpenAI-compatible cloud model.

The web UI asks for the API key on the first rejected request and remembers it in the browser's localStorage.

### 4. Load a dataset
`data/` holds the active dataset. Switch between the bundled sample datasets (others are kept in `data/{name}_backup/`):
```bash
python switch_dataset.py [fuzzy|olist|mock|amazon]
```

## Running

### CLI mode
```bash
python main.py
```

### Web dashboard
Pipeline runs and warehouse Q&A execute on Celery workers with Redis as broker,
result backend, and shared run state — so the dashboard needs three processes
plus a Redis. All must run from the project root (paths resolve relative to cwd):
```bash
docker run -d --name adep-redis -p 6379:6379 redis   # or any Redis; set REDIS_URL if elsewhere

celery -A app.celery_app worker -Q pipeline -c 1 -n pipeline@%h &   # one run at a time
celery -A app.celery_app worker -Q chat -c 2 -n chat@%h &           # Q&A queries
uvicorn app.server:app --port 8000
```
Open `http://localhost:8000` for file upload, a conversational intent intake, live pipeline progress, report browsing/download, and a post-run warehouse Q&A chat.

The pipeline worker must be prefork (the default) with `-c 1`: prefork because Celery time limits require it, `-c 1` because the shared `data/warehouse.db` allows one build at a time.

### Tests
```bash
python -m pytest tests/
# or a single test class:
python -m unittest tests.test_flow.TestFlowArchitecture
```

## Outputs

All deliverables land in `reports/`:

| File | Produced by |
|---|---|
| `profiling_report.json` | Profiling + deterministic entity classification |
| `quality_report.md` | Data quality score and findings (human approval gate below 60/100) |
| `schema_design.md` | Star schema design (Fact/Dim tables, grains, FKs) |
| `transformations.sql` | DuckDB SQL that builds the warehouse (overwritten on each retry) |
| `validation_report.md` | Deterministic structural integrity checks |
| `verified_metrics.json` | Numbers computed directly by the pipeline engine (ground truth for analytics) |
| `kpi_report.md` | Analytics report |
| `verification_report.md` | Independent re-computation of agreed metric definitions vs. the KPI report |
| `executive_summary.md` | Final executive summary |
| `token_usage_report.json` / `.md` | Per-agent LLM token usage |

## Observability: LangSmith Tracing

To watch agent prompts, tool calls, latency, cost, and token usage in real time:

1. Get an API key from [smith.langchain.com](https://smith.langchain.com).
2. Set in `.env`:
   ```env
   LANGSMITH_TRACING=true
   LANGSMITH_API_KEY=your_actual_langsmith_api_key_here
   LANGSMITH_PROJECT=ADEP-Data-Warehouse-Crew
   ```
3. Run the pipeline (CLI or web dashboard). Every agent — including Bedrock-backed ones — reports full input/output messages and token usage.

Related traces are grouped into **LangSmith threads**: each pipeline run, each intent-intake conversation, and each warehouse Q&A session gets its own thread, so a whole run (or conversation) shows up as one connected trace rather than scattered independent ones.

## Deployment

`Dockerfile` + `start.sh` build a container that runs uvicorn plus both Celery workers in the same container (Railway volumes attach to exactly one service, and worker and web share `data/`/`reports/`). A Redis instance must be attached and `REDIS_URL` set — it carries the queues, results, run state, logs, and the approval hand-off. `railway.toml` configures the deployment against `/api/status` as the health check; `start.sh` symlinks `data/`, `reports/`, and `.chroma/` to a persistent disk mount (`ADEP_MOUNT`, default `/mnt/adep`) so uploaded datasets and generated reports survive redeploys.
