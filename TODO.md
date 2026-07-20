# TODO

## Shipped 2026-07-19 — PRs open into dev
- [x] `feature/api-security-hardening` → PR #2 (traversal fixes, WEB_API_KEY
      auth, CORS, SPA api() wrapper, 18 tests, first CI workflow)
- [x] `feature/reliability-hardening` → PR #3 (fail-fast config, bounded
      approval wait, LLM timeout/retries, verify-failure surfacing, 17 tests)
- [x] `chore/upgrade-crewai-1.15` → PR #4 (lockfile: crewai 1.15.4, OTel
      exporters 1.42.1)
- [x] Merge the PRs — all merged (#2–#6), dev CI green
- [x] `refactor/usage-capture-event-bus` → PR #5 (docs-only: why usage capture
      can't move to the event bus)

## Still on you
- [x] Rotate the AWS keys — done 2026-07-19: new IAM access keys in ~/.aws
      work (both Bedrock models verified in ap-south-1); dead bearer token
      removed from .env and ~/.bashrc
- [ ] Set `WEB_API_KEY` (and optionally `CORS_ALLOWED_ORIGINS`) on Railway —
      auth stays disabled until it is set
- [ ] Railway: add a Redis instance and set `REDIS_URL` (reference the Redis
      service's private URL) — required before the next deploy of dev
- [ ] Decide whether to commit `docs/PRODUCTION_AND_SCALING_ROADMAP.md`
      (currently untracked by choice)

## Next up (see docs/PRODUCTION_AND_SCALING_ROADMAP.md)
- [x] Tier 2 reliability (PR #3; checkpoint/resume 2.5 deferred to Celery work)
- [x] Part 3: Redis + Celery job queue steps 1–5 — PR #6 MERGED into dev;
      locally verified end-to-end (full run + chat vs ground truth).
      **Railway needs a Redis instance + `REDIS_URL` before dev deploys**
- [x] Tier 3 observability — PR #7 open into dev, CI green:
      logging_setup.py, 69 prints → loggers, /api/ready probe (also fixes
      the /api/status-would-401 healthcheck trap), Bedrock cred check
      accepts ~/.aws/credentials
- [x] Merge PR #7 — merged, branch deleted
- [x] Worker supervision in start.sh — PR #9 merged (with PR #8: non-root
      container user + logging_setup.py COPY fix); live-verified
- [x] Tier 2.5 checkpoint/resume — `feature/checkpoint-resume` branch, not yet
      pushed. `DataEngineeringState.completed_stages` + a guard on each Flow
      stage in crew.py; `_mark_done()` checkpoints `state.model_dump()` to
      Redis (`adep:run:{id}` hash, best-effort — a Redis blip just costs a
      later resume one extra stage) when `state.run_id` is set (unset in CLI
      mode → no Redis touched, stages just always run). Worker-crash/hard-kill
      resume is *expected* to happen automatically via Celery's acks_late
      redelivery of the same run_id (`run_pipeline` hydrates via
      `flow.kickoff(inputs=checkpoint)` if one exists) — verified that
      re-entry hydrates correctly, NOT verified against a real kill+redeliver
      (soft-timeout returns normally, so it never redelivers; only a true
      hard-kill/OOM does). Soft-timeout still marks the run "failed" (SPA
      behavior unchanged) but keeps its checkpoint; `POST /api/run/{id}/resume`
      re-enqueues it. Resume assumes `data/warehouse.db` still exists on
      whichever worker picks it up — true only under the current
      single-container topology; Part 3 step 6 (object storage) must carry
      the warehouse forward or resume breaks. Exit-at-approval-gate model
      still not done — deferred, the BLPOP wait still holds a worker for the
      whole approval window.
- [ ] Small cleanups batch: ruff + pyproject.toml linting, bound the Polars
      _df_cache in tools/connection_manager.py, stop mutating global
      AWS_DEFAULT_REGION in agents/providers.py, revisit CREW_VERBOSE=true
      default (wrong for production)
- [ ] Part 3 step 6 (later): object storage, then split the worker into its
      own Railway service (dissolves start.sh into per-service commands)
- [x] Dim_Date generation self-poisoning via ChromaDB long-term memory — found
      2026-07-20 during local checkpoint/resume testing (unrelated bug, not
      caused by that work), fixed on `fix/dim-date-memory-generalization`
      (not yet pushed). Two generalized fixes, not an Olist-specific patch —
      matches "domain specific, dataset agnostic":
      (1) `tools/memory_tools.py::SearchPastExecutionsTool` scored entity
      overlap as `overlap / len(stored_entities)`, so a memory tagged with a
      small, generic entity set (e.g. 2 entities) could clear the relevance
      threshold against almost any dataset — that's exactly how an old
      fuzzy_factory memory (pageviews/refunds/sessions, unrelated to Olist)
      leaked into today's run. Changed to Jaccard (`overlap / union`), which
      correctly penalizes a small/broad stored set against a much larger
      current one. 2 new regression tests in test_tools.py (reproduces the
      exact leak, and a same-dataset-still-passes sanity check).
      (2) `config/tasks/transform.yaml` rule 8 said "use UNNEST(generate_series(…))"
      without specifying placement — the LLM kept generating
      `FROM UNNEST(generate_series(...)) AS alias`, which produces a
      `STRUCT(unnest TIMESTAMP)` wrapper column in DuckDB, not a scalar; every
      corrective retry patched a symptom (wrong date function, hallucinated
      `format_timestamp`) without ever fixing that shape. Rule 8 now specifies
      `SELECT UNNEST(...) AS date` explicitly and to derive the date range
      from the data instead of guessing one; also added this failure mode to
      `fix_table_sql_task`'s known-error-type list so the corrective loop
      recognizes it immediately next time instead of burning all retries.
      Also purged/consolidated the already-poisoned local `.chroma` entries
      (data cleanup only, not shipped — `.chroma/` is gitignored).
