"""The two Celery tasks: the 9-stage pipeline run and warehouse Q&A queries.

Workers are separate processes — everything the web tier needs to observe goes
through the Redis run store, and the human-approval gate crosses processes via
the store's BLPOP bridge instead of an in-process threading.Event.
"""

import os
import sys

from celery.exceptions import SoftTimeLimitExceeded
from celery.signals import worker_process_init

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from app.celery_app import celery
from app import run_store

# The hard limit kills the worker process; the soft limit fires inside the task
# with enough margin to mark the run failed and clean up.
_SOFT_MARGIN = 300


@worker_process_init.connect
def _init_worker(**_):
    """Each prefork child gets logging, the fail-fast config check, and
    telemetry once."""
    from logging_setup import setup_logging

    setup_logging()
    config.assert_valid_config()
    from pipeline.core import setup_telemetry

    setup_telemetry()
    from utils.storage import get_storage_backend

    if config.DATA_SOURCE == "local":
        get_storage_backend().sync_dir_down("data", "data")


class _RedisLogRedirector:
    """Process-wide stdout/stderr capture for a pipeline run: every write goes
    to the run's Redis log list (which also advances the active step), the
    on-disk execution.log, and the real stdout for the worker's own logs."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.log_path = os.path.join("reports", "execution.log")
        self.old_stdout = sys.stdout
        self.old_stderr = sys.stderr

    def __enter__(self):
        sys.stdout = self
        sys.stderr = self
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        sys.stdout = self.old_stdout
        sys.stderr = self.old_stderr

    def write(self, s):
        try:
            run_store.append_log(self.run_id, s)
        except Exception:
            pass  # a Redis blip must not kill the run mid-stage
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(s)
        except Exception:
            pass
        self.old_stdout.write(s)

    def flush(self):
        self.old_stdout.flush()


@celery.task(
    name="tasks.run_pipeline",
    time_limit=config.PIPELINE_TIME_LIMIT_SECONDS,
    soft_time_limit=config.PIPELINE_TIME_LIMIT_SECONDS - _SOFT_MARGIN,
)
def run_pipeline(run_id: str, instructions: str, intent: dict) -> str:
    from tools import HumanLoopService, WebApprovalStrategy
    from pipeline.core import set_thread, new_thread_id

    HumanLoopService.set_strategy(
        WebApprovalStrategy(
            lambda score, summary: run_store.request_approval(run_id, score, summary)
        )
    )
    with _RedisLogRedirector(run_id):
        set_thread(new_thread_id("pipeline"))
        try:
            from crew import DataEngineeringFlow

            flow = DataEngineeringFlow()
            checkpoint = run_store.load_checkpoint(run_id)
            if checkpoint:
                flow.kickoff(inputs=checkpoint)
            else:
                flow.state.run_id = run_id
                flow.state.user_instructions = instructions or ""
                flow.state.user_intent = intent or {}
                flow.kickoff()
            run_store.complete(run_id, flow.state.db_path, dict(flow.state.entity_map))
        except SystemExit:
            run_store.fail(
                run_id, "Pipeline halted before completion — see logs for details."
            )
        except SoftTimeLimitExceeded:
            run_store.fail(
                run_id,
                f"Run exceeded the {config.PIPELINE_TIME_LIMIT_SECONDS - _SOFT_MARGIN}s "
                "time limit and was stopped.",
            )
        except Exception as e:
            run_store.fail(run_id, str(e))
        finally:
            set_thread(None)
    return run_store.get_status(run_id)


@celery.task(name="tasks.ping", time_limit=10)
def ping() -> str:
    """Broker/worker health probe: enqueue-and-wait proves the whole path."""
    return "pong"


@celery.task(name="tasks.chat_query", time_limit=config.CHAT_TIME_LIMIT_SECONDS)
def chat_query(
    question: str, db_path: str, entity_map: dict, thread_id: str = ""
) -> str:
    from pipeline.core import set_thread
    from app.chat import run_chat_query

    set_thread(thread_id or None)
    try:
        return run_chat_query(question, db_path, entity_map or {})
    finally:
        set_thread(None)
