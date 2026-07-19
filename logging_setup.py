"""Project-wide logging: timestamped, leveled, and redirect-friendly.

Every component logs through here instead of print(). Two constraints shape the
design:

- The Celery pipeline task replaces sys.stdout mid-process so a run's output is
  captured into Redis (activity feed, step markers). A normal StreamHandler
  binds the stream object once at creation and would silently bypass that
  redirect — the proxy below resolves sys.stdout at every write instead.
- The run store and the SPA activity feed identify pipeline narration by the
  literal "[Flow]" tag in each line. The format renders the logger name in
  brackets, so the logger named "Flow" keeps that contract byte-compatible.
"""

import logging
import sys

import config


class _CurrentStdout:
    """File-like object that always writes to the *current* sys.stdout."""

    def write(self, s: str) -> None:
        sys.stdout.write(s)

    def flush(self) -> None:
        sys.stdout.flush()


_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
_DATEFMT = "%H:%M:%S"
_CONFIGURED_FLAG = "_adep_logging_configured"

# Chatty third-party loggers that would flood INFO now that the root logger
# has a real handler; they still surface warnings and errors.
_QUIET = ("httpx", "httpcore", "botocore", "boto3", "urllib3", "LiteLLM",
          "openai", "chromadb", "opentelemetry")


def setup_logging() -> None:
    """Configure the root logger once per process (idempotent). Level comes
    from LOG_LEVEL (default INFO)."""
    root = logging.getLogger()
    if getattr(root, _CONFIGURED_FLAG, False):
        return
    setattr(root, _CONFIGURED_FLAG, True)
    handler = logging.StreamHandler(_CurrentStdout())
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
    root.addHandler(handler)
    root.setLevel(config.LOG_LEVEL)
    for name in _QUIET:
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """The named logger, ensuring the process is configured first."""
    setup_logging()
    return logging.getLogger(name)
