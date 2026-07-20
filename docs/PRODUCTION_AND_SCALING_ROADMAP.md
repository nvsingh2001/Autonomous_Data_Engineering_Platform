# Production & Scaling Roadmap

This document captures assessments of ADEP made against the actual codebase
(July 2026): what it takes to make the current monolith **production grade**
(Part 1), how to evolve it into a **service-oriented architecture** when scale
demands it (Part 2), and a concrete **job-queue design with Redis + Celery**
that implements Phase 1 of that evolution (Part 3). Every finding cites the
file and line it was observed at, so items can be re-verified as the code
moves.

Overall verdict: the pipeline core is in decent shape — solid LangSmith/OTel
telemetry, fully pinned dependencies, tests for the pipeline layer. The web tier
is the weak spot: it has real security holes, and the whole app is architected
around "one process, one run at a time, local disk."

---

## Part 1 — Production Upgrades

Ordered by priority. Tier 1 closes actively dangerous holes; each later tier
assumes the ones before it.

### Tier 1 — Security (must fix before this faces any network)

**1.1 Path traversal in four endpoints.** Four routes join raw, user-controlled
filenames onto a directory with no sanitization:

| Endpoint | Location | Risk |
|---|---|---|
| `POST /api/upload` | `app/server.py:319-322` | `f.filename` written as-is — a multipart filename like `../crew.py` escapes `data/` (arbitrary file **write**) |
| `DELETE /api/files/{filename}` | `app/server.py:332-342` | arbitrary file **delete** |
| `GET /api/reports/{filename}` | `app/server.py:378-384` | arbitrary file **read** (e.g. `/api/reports/../../config.py`) |
| `GET /api/reports/download/{filename}` | `app/server.py:387-396` | arbitrary file **read/download** |

The fix pattern already exists in the same file: the chart/export routes only
serve filenames matching a strict regex allow-list
(`_CHART_FILENAME_RE` / `_EXPORT_FILENAME_RE`, `app/server.py:37-38`). Apply the
same idea to these four routes — sanitize upload names to a basename with an
allow-listed charset, and validate report filenames against the known report
list (`get_reports_summary` already enumerates it, `app/server.py:359-369`).

**1.2 No authentication, CORS, or rate limiting.** There is no auth dependency,
no `CORSMiddleware`, and no rate limiter anywhere in `app/` — every endpoint is
public, including data upload and the LLM-spending `/api/query`. The only guard
is a single-slot mutex on chat returning HTTP 429, which is concurrency control,
not rate limiting. Minimum bar: an API-key header check plus explicit CORS
configuration. (Use a dedicated `WEB_API_KEY` env var — `PIPELINE_API_KEY` is
already taken: it is the LLM provider credential, passed into LLM kwargs in
`app/chat.py:31`, `app/intent_chat.py:21`, `pipeline/core/context.py:60`, and
`agents/factory.py:20`.) The instruction/question validators
(`_validate_instructions`, `app/server.py:56-74`) are a blunt keyword filter —
useful hygiene, but not an injection defense and not a substitute for auth.

**1.3 Rotate working-tree secrets.** `.env` contains live-looking AWS access
keys and API keys. It was never committed (verified against git history) and is
both gitignored and dockerignored — good — but it sits in plaintext in the
working tree. If this tree was ever shared, screen-shared, or copied, rotate the
keys.

**1.4 Container runs as root.** The `Dockerfile` has no `USER` directive. Add a
non-root user and own `/app` by it.

### Tier 2 — Reliability (stop runs from hanging or failing silently)

**2.1 No timeouts or retries anywhere in project code.** No use of `tenacity`,
`backoff`, or any timeout parameter in `agents/`, `pipeline/`, `tools/`, or
`crew.py` (both libraries are already installed as transitive dependencies).
LLM retry behavior is delegated entirely to CrewAI/litellm internals; a hung LLM
call hangs the whole run with no watchdog. Add per-stage timeouts and
retry-with-backoff around LLM calls.

**2.2 Unbounded approval wait.** The human-approval gate blocks its background
thread on `self.approval_event.wait()` with no timeout
(`app/manager.py:111-122`). An unanswered approval is an immortal zombie run
that also blocks all new runs (the busy-check at `app/server.py` rejects starts
while `status in ("running", "waiting_approval")`). Add a bounded wait with a
default decision (reject) on expiry.

**2.3 No fail-fast config validation.** `config.py` reads every env var with a
silent default — e.g. `PIPELINE_MODEL` falls back to `ollama/gemma4:31b-cloud`
and `PIPELINE_BASE_URL` to `http://localhost:11434` (`config.py:7-8`). A deploy
with missing env vars starts cleanly and dies at the first LLM call. Add a
startup check that required settings are present. Also: `CREW_VERBOSE` defaults
to `"true"` (`config.py:15`) — its own comment warns this can outrun Railway's
log-rate limit, i.e. the default is wrong for production and must be manually
overridden.

**2.4 Silent verification degradation.** `verify_answers` catches every
exception and prints "Answer verification skipped (error)" (`crew.py:142-143`),
so a run can complete "successfully" with unverified answers. Surface this in
run status / the final report instead of swallowing it.

**2.5 No checkpoint/resume.** Any crash re-runs the entire pipeline from
scratch, and `_clear_previous_run` (`crew.py:49`) deletes the warehouse and
prior reports at the start of each run, so nothing partial is retained. The only
self-healing today is the single analytics corrective re-run
(`MAX_ANALYTICS_CORRECTION = 1`, `crew.py:33,128-140`). Persisting per-stage
outputs so a run can resume from the failed stage is the single biggest
reliability win for long pipelines — and it is a prerequisite for the async
approval model in Part 2 (§2 hard problem 3), so build it once, use it twice.

### Tier 3 — Observability

**3.1 Replace `print()` with real logging.** There is no `import logging`
anywhere in `app/`, `pipeline/`, `tools/`, `agents/`, or `crew.py` — everything
is `print()`, captured by a process-global stdout/stderr redirect
(`IOStreamRedirector`, `app/manager.py:160-173`) into an in-memory buffer and
appended to `reports/execution.log` (`app/manager.py:104-109`). No levels, no
timestamps, no structure. Move to the `logging` module with structured
(JSON-capable) output; this is a prerequisite for debugging anything in
production and for the log-shipping model in Part 2.

**3.2 A real health check.** `/api/status` doubles as the Railway healthcheck
(`railway.toml`) but always returns 200 — it is liveness only. Add a readiness
endpoint that verifies the LLM provider is reachable and the storage mount is
writable.

**3.3 Application metrics.** The LangSmith/OTel tracing
(`pipeline/core/telemetry.py`) is genuinely good but trace-only and off by
default (requires `LANGSMITH_TRACING` + `LANGSMITH_API_KEY`). There are no
request/error/latency metrics. A `/metrics` endpoint (Prometheus format) or
platform-native metrics rounds out the picture.

### Tier 4 — Architecture (single-process assumptions)

These are the structural limits; they are also exactly Phase 0 of Part 2.

**4.1 In-memory run state.** All run state — status, logs, chat jobs, approval
events, intent history — lives in one in-process singleton
(`mgr = RunManager()`, `app/manager.py:154`). This is why `start.sh` pins
`--workers 1` (`start.sh:17`): with more than one uvicorn worker the state would
be split-brained. The flag is load-bearing, not a default. Fix: run state in a
database (see Part 2).

**4.2 Durability hangs on one volume.** `start.sh:4-10` symlinks `/app/data`,
`/app/reports`, and `/app/.chroma` to `${ADEP_MOUNT:-/mnt/adep}`. On Railway,
everything survives restarts **only if** a persistent volume is mounted there;
otherwise total data loss on redeploy. There is no cloud object storage
integration (`boto3` is used only for Bedrock). Move uploads, reports, and
warehouse artifacts to S3-compatible object storage.

**4.3 Unbounded in-memory dataframe cache.** `ConnectionManager` keeps a
per-instance Polars frame cache (`_df_cache` in `tools/connection_manager.py`)
that is never evicted — combined with the 200 MB per-file upload cap
(`_MAX_FILE_BYTES`, `app/server.py:53`) this is an OOM risk with no configured
container memory limit (`railway.toml` declares none). Bound the cache.

**4.4 Global AWS region mutation.** The Bedrock provider sets
`os.environ["AWS_DEFAULT_REGION"]` per model (`agents/providers.py:42-43`), so
two models configured with different regions (`SQL_AWS_REGION` vs
`BI_AWS_REGION`) race through a process-global. Pass the region per-client
instead of via env.

### Tier 5 — Engineering hygiene

- **CI:** there is no `.github/` at all. Add a GitHub Actions workflow running
  `pytest` on PRs to `dev` — table stakes, and what gives every other tier a
  regression gate.
- **App-layer tests:** none of the 8 files in `tests/` import `app/` — the
  entire FastAPI surface (upload validation, path handling, RunManager
  concurrency, approval flow) is untested. FastAPI's `TestClient` makes this
  cheap; the Tier 1 fixes should land with tests proving the traversal cases
  are closed.
- **Lint/format/packaging:** no ruff/black/mypy config and no `pyproject.toml`.
  Adopt ruff + a minimal `pyproject.toml`.
- **Dependency drift:** `requirements.in` pins OpenTelemetry `~=1.34.0` but the
  compiled `requirements.txt` ships `opentelemetry-api/sdk==1.42.1` alongside
  `1.34.x` exporters — a known-fragile mix. Recompile to a consistent set. Note
  the Dockerfile installs with `pip install --no-deps`, so the lockfile must be
  complete or the image breaks at runtime.
- **Cruft:** stray zero-byte Docker build artifacts in the repo root (`=`, `[`,
  `[internal]`, `CACHED`, `ERROR`, `transferring`, `1033`) and duplicate
  generated-output trees (`reports/`, `local_storage/reports/`,
  `adep_storage/`). Delete.

### Recommended sequence

1. **Tier 1.1 + 1.2** — fix the four traversal routes and add API-key auth.
   Roughly a day of work; closes the actively dangerous holes.
2. **Tier 2.1–2.3 + 3.1** — timeouts, bounded approval, config validation,
   structured logging. Makes the app operable.
3. **Tier 5 CI + app tests** — keeps it that way.
4. **Tier 2.5 checkpoint/resume** — biggest reliability win; also unlocks
   Part 2.
5. **Tier 4** — do as Phase 0 of Part 2, only when multi-user/multi-instance is
   actually on the roadmap.

---

## Part 2 — Microservices Scaling

Short version: **a full microservices explosion would hurt this project; the
right move is a 2–4 service decomposition along seams that already exist**, with
prerequisites that matter more than the split itself.

### Where the natural service boundaries are

The codebase already runs three distinct workloads in one process:

1. **Web/API tier** — `app/server.py` + the static SPA (`app/static/`):
   uploads, run control, status, report/chart serving. Short requests, must be
   always-up.
2. **Pipeline execution** — the 9-stage CrewAI flow in `crew.py`, launched via
   `app/worker.py` in a FastAPI background thread. Long-running (minutes),
   LLM-heavy, memory-heavy (Polars), serialized to one run at a time.
3. **Interactive analysis** — `app/chat.py` (warehouse Q&A) and
   `app/intent_chat.py` (conversational intake). LLM-bound, latency-sensitive,
   read-only against the warehouse.

### Target architecture

- **`web` service** — FastAPI gateway: auth, uploads, run CRUD, serves the SPA
  and artifacts. Stateless.
- **`pipeline-worker` service** — pulls run jobs off a queue, executes the flow,
  writes results. One concurrent run per instance; scale by adding instances
  (which also removes today's one-run-per-deployment limit).
- **`analyst` service** — chat + intent endpoints, read-only warehouse access,
  scaled independently for concurrent users. Intent chat lives here; it does not
  warrant its own service.
- **Shared infrastructure** — Postgres for run state/metadata, Redis (or
  similar) as job queue, S3-compatible object storage for
  datasets/reports/charts/warehouse files, and ChromaDB as its own server
  instead of embedded.

The repo stays a **monorepo**: one Dockerfile per service, with shared code
(`schemas/`, `tools/`, `utils/`, `pipeline/`) as a common internal package.
Railway supports multiple services from one repo, so the deploy story barely
changes.

### The three hard problems the split forces you to solve

The service boundaries are the easy part; this is the actual work.

**1. The in-memory `RunManager` singleton must die.** Everything — status,
logs, chat jobs, approval events — lives in `mgr = RunManager()`
(`app/manager.py:154`) and only works because all code shares one process. In
the target architecture, run state moves to Postgres (a `runs` table with a
status state machine); the web tier reads status from the DB. Log streaming,
currently a process-global `sys.stdout` redirect (`app/manager.py:167-168`),
becomes structured logging shipped to the DB or Redis pub/sub per run (which is
why Tier 3.1 comes first).

**2. DuckDB is the awkward piece.** It is an embedded, single-writer,
single-file database — the one component that actively resists a service split.
Two workable options:

- **Recommended — keep DuckDB, treat the warehouse as an artifact.** The
  pipeline-worker builds `warehouse.db`, uploads it to object storage on
  completion; the analyst service downloads it and opens it **read-only**.
  Write/read separation falls out naturally: the flow already builds the
  warehouse in one shot and chat only reads it. Lowest-change option;
  `tools/connection_manager.py` needs only a fetch-and-open-readonly mode.
- **Alternative — client-server warehouse** (MotherDuck, Postgres). Cleaner
  concurrency story, but it invalidates chunks of
  `tools/connection_manager.py`, `utils/sql_executor.py`, and the SQL dialect
  the agents generate. Only worth it if concurrent multi-user *writes* or very
  large warehouses become real.

**3. Human-in-the-loop approval must become asynchronous.** Today approval
blocks a worker thread on `threading.Event().wait()`
(`app/manager.py:111-122`). Across services this becomes a state transition:
the worker reaches the approval gate, persists a checkpoint, marks the run
`waiting_approval`, and **exits**; `/api/approve` flips the DB status and
re-enqueues the job; a worker resumes from the checkpoint. This forces
stage-level checkpoint/resume — the same work as Tier 2.5. Build it once, use
it twice.

### Migration path (strangler-style, not big-bang)

- **Phase 0 — modular monolith.** Externalize run state to Postgres, move
  artifacts to object storage, replace prints with structured logging, and
  enforce that `app/` talks to the pipeline only through a narrow interface. No
  new deployables yet. This is the same work as Tier 4 and roughly 70% of the
  total effort.
- **Phase 1 — split out the worker.** Two containers from one repo: `web` and
  `pipeline-worker`, Redis queue between them. This alone delivers most of the
  value: the API stays responsive during runs, a crashed pipeline cannot take
  down the web tier, and `--workers 1` stops being load-bearing so the web tier
  can finally scale.
- **Phase 2 — split out the analyst.** Move `app/chat.py` /
  `app/intent_chat.py` into their own service with read-only warehouse access.
  Do this when concurrent chat users actually contend with pipeline runs — not
  before.
- **Phase 3 — stop.** Finer-grained splits (per-pipeline-stage services, a
  separate LLM gateway) add network hops, versioning pain, and ops burden with
  no payoff at this scale. If a single stage ever needs independent scaling,
  revisit then.

### The honest caveat

Microservices solve organizational and scaling problems — independent teams,
independent deploy cadences, wildly different load profiles. This project has
exactly one genuine instance of that problem: long-running LLM pipeline runs
sharing a process with an interactive API. **Phase 1 solves it.** Everything
beyond Phase 2 is architecture for an audience that doesn't exist yet and would
slow a solo developer considerably.

Recommendation: do Phase 0 regardless (it is production hygiene, not
microservices), do Phase 1 when deploy stability matters, and treat the rest as
optional.

---

## Part 3 — Job Queue Design (Redis + Celery)

The concrete design for Phase 1's queue. The current code is well-shaped for
this move: `/api/query` already implements a poor-man's job queue — job ID +
background task + poll endpoint (`app/server.py:228-253`) — and Celery/Redis
essentially replaces its in-memory machinery (`mgr.chat_jobs`, `_chat_lock`,
FastAPI `BackgroundTasks`) with real infrastructure.

### What changes conceptually

Today both job types run inside the uvicorn process: pipeline runs via
`background_tasks.add_task(execute_pipeline, mgr)` (`app/server.py:146`), chat
queries via `_run_chat_job` (`app/server.py:217-225`). With Celery, the web
process only **enqueues** — Redis is the broker and result backend, and
separate worker processes execute.

Two queues, matching the two workloads:

- **`pipeline` queue** — one dedicated worker with `--concurrency=1`,
  preserving today's one-run-at-a-time semantics (which the shared
  `data/warehouse.db` path requires anyway).
- **`chat` queue** — its own worker with higher concurrency. This replaces the
  `_chat_lock` single-slot HTTP 429 (`app/server.py:238-241`): queries queue up
  instead of being rejected (or keep a cap via Celery rate limits).

### Core wiring

```python
# app/celery_app.py
from celery import Celery

celery = Celery("adep", broker=REDIS_URL, backend=REDIS_URL)
celery.conf.update(
    task_acks_late=True,               # re-deliver if a worker dies mid-task
    worker_prefetch_multiplier=1,      # long tasks: no hoarding
    task_reject_on_worker_lost=True,
    result_expires=3600,
    task_routes={
        "tasks.run_pipeline": {"queue": "pipeline"},
        "tasks.chat_query":   {"queue": "chat"},
    },
)

# app/tasks.py
@celery.task(name="tasks.run_pipeline", time_limit=3600, soft_time_limit=3300)
def run_pipeline(run_id: str, instructions: str, intent: dict): ...

@celery.task(name="tasks.chat_query", autoretry_for=(TransientLLMError,),
             retry_backoff=True, max_retries=2, time_limit=300)
def chat_query(question: str, db_path: str, entity_map: dict): ...
```

Endpoint changes are small: `/api/run` calls `run_pipeline.apply_async(...)`
and stores the task ID; `/api/query` returns `chat_query.delay(...).id`; the
poll endpoint reads `AsyncResult(job_id)` instead of `mgr.chat_jobs`.

The `time_limit` on the pipeline task is Tier 2.1's run watchdog falling out
for free — a hung LLM call finally has a ceiling. Footnote: Celery time limits
require the prefork pool, so run the pipeline worker as prefork `-c 1`, not
`--pool=solo`.

### The three things Celery forces you to fix

All three are already on the roadmap — Celery doesn't add the work, it makes it
unavoidable.

**1. `RunManager` state moves to Redis (Tier 4.1).** The worker is a different
process, so it can't touch the `mgr` singleton. Run status/active-step/error go
into a Redis hash (`run:{id}`), which the worker updates and `/api/status`
reads. Logs follow the same path: `IOStreamRedirector` (`app/manager.py:160`)
changes from appending to an in-memory buffer to `RPUSH run:{id}:log` +
`LTRIM`, and the UI's activity feed reads `LRANGE`. Keep the API response shape
of `get_state()` (`app/manager.py:143-151`) identical so the SPA needs no
changes.

**2. The approval gate can't be a `threading.Event` (Tier 2.2).**
`request_approval` blocks on an event the web process sets
(`app/manager.py:111-127`) — that doesn't cross processes. Two options:

- *Incremental (start here):* the worker's `WebApprovalStrategy` writes the
  approval request into Redis, sets status to `waiting_approval`, then does a
  **`BLPOP approval:{run_id}` with a timeout**; `/api/approve` does `RPUSH`
  with the decision. Same blocking semantics, but cross-process and finally
  time-bounded. ~30 lines, plugs into the existing
  `HumanLoopService.set_strategy` seam (`app/manager.py:155-157`).
- *Target:* the task checkpoints and **exits** at the approval gate;
  `/api/approve` enqueues a resume task. Cleaner (no worker slot held hostage),
  but requires stage checkpoint/resume (Tier 2.5). Migrate once that exists.

**3. The shared filesystem decides the deployment shape.** Worker and web both
need `data/` and `reports/` (worker writes the warehouse and reports; web
serves uploads in and reports out). On Railway **a volume mounts to exactly one
service**, so a separate worker service cannot share the web service's disk.
Two honest steps:

- **Step 1 (works today):** run uvicorn + the Celery workers in the *same
  container* (small supervisor script or
  `bash -c "celery ... & exec uvicorn ..."` in `start.sh`), sharing the
  existing volume. No process isolation yet, but you immediately get durable
  queue semantics, task time limits, retries, run state in Redis, and the chat
  queue. All the code changes are the real migration; the topology split
  becomes a config change later.
- **Step 2 (real split):** move artifacts to object storage first (uploads
  pulled by the worker, warehouse/reports pushed on completion), then the
  worker becomes its own Railway service. This is exactly why Phase 0 precedes
  Phase 1.

Either way: add a Redis instance (Railway one-click) and a `REDIS_URL` env var
with fail-fast validation (Tier 2.3).

### Sequenced task list

1. Add `app/celery_app.py` + `app/tasks.py`; add `celery` + `redis` to
   `requirements.in`; add `REDIS_URL` to `config.py` with startup validation.
2. Move run status/logs from `RunManager` to Redis keys, keeping the
   `get_state()` response shape identical.
3. Convert `/api/query` to Celery on the `chat` queue — lowest risk, already
   job-shaped.
4. Convert `/api/run` to Celery on the `pipeline` queue (prefork `-c 1`), with
   the BLPOP approval bridge.
5. Same-container deployment with Redis attached.
6. (Later, after Phase 0 object storage) split the worker into its own Railway
   service.

### Why Celery and not something lighter

If the only goal were background jobs, RQ or `arq` would be lighter. Celery
earns its complexity here because the design wants distinct queues with
different concurrency, hard/soft time limits, `acks_late` redelivery, and
(eventually) beat-scheduled jobs — and it is the ecosystem-standard choice.
