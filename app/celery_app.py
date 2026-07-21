"""Celery wiring: Redis broker/backend, two queues matching the two workloads.

- `pipeline` — run with a dedicated prefork worker at -c 1, preserving the
  one-run-at-a-time semantics the shared data/warehouse.db path requires.
  (Prefork, not --pool=solo: time limits need it.)
- `chat` — its own worker with higher concurrency; queries queue up instead of
  being rejected like the old single-slot 429.

    celery -A app.celery_app worker -Q pipeline -c 1 -n pipeline@%h
    celery -A app.celery_app worker -Q chat -c 2 -n chat@%h
"""

import os
import sys

from celery import Celery

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

celery = Celery(
    "adep",
    broker=config.REDIS_URL,
    backend=config.REDIS_URL,
    include=["app.tasks"],
)

celery.conf.update(
    # Celery normally reconfigures the root logger in workers; our own setup
    # (logging_setup, applied in worker_process_init) must stay in charge so
    # log lines keep flowing through the redirectable-stdout handler.
    worker_hijack_root_logger=False,
    # Celery also redirects stdout/stderr into its own logger by default. Our
    # _RedisLogRedirector already owns stdout capture end-to-end (Redis,
    # execution.log, real stdout); Celery's redirect would re-emit each
    # already-formatted line as a second log record, doubling every entry in
    # the activity feed.
    worker_redirect_stdouts=False,
    task_acks_late=True,  # re-deliver if a worker dies mid-task
    worker_prefetch_multiplier=1,  # long tasks: no hoarding
    task_reject_on_worker_lost=True,
    result_expires=3600,
    broker_connection_retry_on_startup=True,
    task_routes={
        "tasks.run_pipeline": {"queue": "pipeline"},
        "tasks.chat_query": {"queue": "chat"},
        "tasks.ping": {"queue": "chat"},
    },
)
