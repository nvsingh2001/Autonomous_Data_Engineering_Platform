import os

from dotenv import load_dotenv

load_dotenv()

PIPELINE_MODEL: str = os.environ.get("PIPELINE_MODEL", "ollama/gemma4:31b-cloud")
PIPELINE_BASE_URL: str = os.environ.get("PIPELINE_BASE_URL", "http://localhost:11434")
PIPELINE_API_KEY: str | None = os.environ.get("PIPELINE_API_KEY") or None

# Auth for the web API. Unset means auth is disabled (local development);
# hosted deployments must set it or the whole API is public.
WEB_API_KEY: str | None = os.environ.get("WEB_API_KEY") or None

# Comma-separated origins allowed to call the API cross-origin. Unset means
# same-origin only (the SPA is served by this app, so that is the default).
CORS_ALLOWED_ORIGINS: list[str] = [
    o.strip() for o in os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()
]

# CrewAI's verbose mode prints full agent Thought/Action/Observation traces per LLM
# call — useful for local CLI debugging, but on a hosted deployment (many retries x
# many tables x many pipeline steps) it can outrun platform log-rate limits (e.g.
# Railway's 500 logs/sec). Default off; set "true" locally for CLI debugging.
CREW_VERBOSE: bool = os.environ.get("CREW_VERBOSE", "false").lower() == "true"

# Root log level for every process (web, workers, CLI). DEBUG/INFO/WARNING/ERROR.
LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO").upper()

SQL_MODEL: str | None = os.environ.get("SQL_MODEL") or None
SQL_AWS_REGION: str | None = os.environ.get("SQL_AWS_REGION") or None


BI_MODEL: str | None = os.environ.get("BI_MODEL") or None
BI_AWS_REGION: str | None = os.environ.get("BI_AWS_REGION") or None

LANGSMITH_API_KEY: str | None = os.environ.get("LANGSMITH_API_KEY") or None
LANGSMITH_TRACING: bool = os.environ.get("LANGSMITH_TRACING") == "true"
LANGCHAIN_TRACING_V2: bool = os.environ.get("LANGCHAIN_TRACING_V2") == "true"

# Watchdog for a single LLM call and how often litellm retries it before the
# error propagates. Without a timeout, one hung provider call hangs the run.
LLM_TIMEOUT_SECONDS: int = int(os.environ.get("LLM_TIMEOUT_SECONDS", "300"))
LLM_MAX_RETRIES: int = int(os.environ.get("LLM_MAX_RETRIES", "2"))

# How long a run may sit at the human-approval gate before it is auto-rejected.
# An unanswered gate would otherwise block that run — and all new runs — forever.
APPROVAL_TIMEOUT_SECONDS: int = int(os.environ.get("APPROVAL_TIMEOUT_SECONDS", "3600"))

# Redis backs the Celery broker/result store AND the shared run state (status,
# logs, approval hand-off) between the web process and the workers.
REDIS_URL: str = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# Object storage: local filesystem is the default and requires zero AWS access.
# Set STORAGE_BACKEND=s3 to additionally mirror uploads/warehouse/reports to an
# S3 bucket so they survive a container redeploy without a mounted volume —
# local disk stays the source of truth for the running container either way.
STORAGE_BACKEND: str = os.environ.get("STORAGE_BACKEND", "local").lower()
S3_BUCKET: str | None = os.environ.get("S3_BUCKET") or None
S3_REGION: str | None = os.environ.get("S3_REGION") or None
# Optional key namespace (e.g. to share one bucket across dev/stage). Keys are
# fixed logical names either way ("warehouse/warehouse.db", not per-run — this
# app is single-tenant, "last run wins" already; see crew.py::_clear_previous_run).
S3_PREFIX: str = os.environ.get("S3_PREFIX", "").strip("/")

# Whole-run watchdogs enforced by Celery (requires the prefork pool). The soft
# limit fires SoftTimeLimitExceeded inside the task so the run can be marked
# failed; the hard limit kills the worker process outright 5 minutes later.
PIPELINE_TIME_LIMIT_SECONDS: int = int(
    os.environ.get("PIPELINE_TIME_LIMIT_SECONDS", "5400")
)
CHAT_TIME_LIMIT_SECONDS: int = int(os.environ.get("CHAT_TIME_LIMIT_SECONDS", "300"))


def _has_aws_credentials() -> bool:
    """Any source boto3 can resolve: env keys, a named profile, a Bedrock
    bearer token, or the shared credentials file from `aws configure`."""
    if os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_PROFILE"):
        return True
    if os.environ.get("AWS_BEARER_TOKEN_BEDROCK"):
        return True
    shared = os.environ.get(
        "AWS_SHARED_CREDENTIALS_FILE", os.path.expanduser("~/.aws/credentials")
    )
    return os.path.isfile(os.path.expanduser(shared))


def _model_problems(label: str, model: str | None) -> list[str]:
    if not model:
        return []
    if model.startswith("bedrock/"):
        if not _has_aws_credentials():
            return [
                f"{label}={model} is a Bedrock model but no AWS credentials found "
                "(need AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY, AWS_PROFILE, "
                "AWS_BEARER_TOKEN_BEDROCK, or an ~/.aws/credentials file)."
            ]
        return []
    if not model.startswith("ollama/") and not PIPELINE_API_KEY:
        return [
            f"{label}={model} is a cloud model but PIPELINE_API_KEY is not set."
        ]
    return []


def validate_config() -> list[str]:
    """Misconfigurations that would otherwise surface only at the first LLM call,
    as human-readable problem strings (empty list = config is usable)."""
    problems: list[str] = []
    problems += _model_problems("PIPELINE_MODEL", PIPELINE_MODEL)
    problems += _model_problems("SQL_MODEL", SQL_MODEL)
    problems += _model_problems("BI_MODEL", BI_MODEL)
    if STORAGE_BACKEND not in ("local", "s3"):
        problems.append(f"STORAGE_BACKEND={STORAGE_BACKEND!r} must be 'local' or 's3'.")
    elif STORAGE_BACKEND == "s3":
        if not S3_BUCKET:
            problems.append("STORAGE_BACKEND=s3 but S3_BUCKET is not set.")
        if not _has_aws_credentials():
            problems.append(
                "STORAGE_BACKEND=s3 but no AWS credentials found (need "
                "AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY, AWS_PROFILE, "
                "AWS_BEARER_TOKEN_BEDROCK, or an ~/.aws/credentials file)."
            )
    if LANGSMITH_TRACING and not LANGSMITH_API_KEY:
        problems.append("LANGSMITH_TRACING=true but LANGSMITH_API_KEY is not set.")
    if LLM_TIMEOUT_SECONDS <= 0 or LLM_MAX_RETRIES < 0 or APPROVAL_TIMEOUT_SECONDS <= 0:
        problems.append(
            "LLM_TIMEOUT_SECONDS and APPROVAL_TIMEOUT_SECONDS must be > 0; "
            "LLM_MAX_RETRIES must be >= 0."
        )
    if LOG_LEVEL not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        problems.append(f"LOG_LEVEL={LOG_LEVEL!r} is not a valid logging level.")
    if not REDIS_URL.startswith(("redis://", "rediss://", "unix://")):
        problems.append(
            f"REDIS_URL={REDIS_URL!r} is not a redis://, rediss://, or unix:// URL."
        )
    if PIPELINE_TIME_LIMIT_SECONDS <= 600 or CHAT_TIME_LIMIT_SECONDS <= 0:
        problems.append(
            "PIPELINE_TIME_LIMIT_SECONDS must be > 600 (the soft limit sits 300s "
            "inside it); CHAT_TIME_LIMIT_SECONDS must be > 0."
        )
    return problems


def assert_valid_config() -> None:
    """Fail fast at startup instead of dying mid-pipeline."""
    problems = validate_config()
    if problems:
        raise RuntimeError(
            "Refusing to start — fix these configuration problems:\n"
            + "\n".join(f"  - {p}" for p in problems)
        )
