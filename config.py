import os

from dotenv import load_dotenv

load_dotenv()

PIPELINE_MODEL: str = os.environ.get("PIPELINE_MODEL", "ollama/gemma4:31b-cloud")
PIPELINE_BASE_URL: str = os.environ.get("PIPELINE_BASE_URL", "http://localhost:11434")
PIPELINE_API_KEY: str | None = os.environ.get("PIPELINE_API_KEY") or None

# CrewAI's verbose mode prints full agent Thought/Action/Observation traces per LLM
# call — useful for local CLI debugging, but on a hosted deployment (many retries x
# many tables x many pipeline steps) it can outrun platform log-rate limits (e.g.
# Railway's 500 logs/sec). Default on for local dev; set to "false" in production.
CREW_VERBOSE: bool = os.environ.get("CREW_VERBOSE", "true").lower() == "true"

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


def _model_problems(label: str, model: str | None) -> list[str]:
    if not model:
        return []
    if model.startswith("bedrock/"):
        if not (os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_PROFILE")):
            return [
                f"{label}={model} is a Bedrock model but no AWS credentials are set "
                "(need AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY or AWS_PROFILE)."
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
    if LANGSMITH_TRACING and not LANGSMITH_API_KEY:
        problems.append("LANGSMITH_TRACING=true but LANGSMITH_API_KEY is not set.")
    if LLM_TIMEOUT_SECONDS <= 0 or LLM_MAX_RETRIES < 0 or APPROVAL_TIMEOUT_SECONDS <= 0:
        problems.append(
            "LLM_TIMEOUT_SECONDS and APPROVAL_TIMEOUT_SECONDS must be > 0; "
            "LLM_MAX_RETRIES must be >= 0."
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
