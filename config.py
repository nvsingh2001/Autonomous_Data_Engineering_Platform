import os

from dotenv import load_dotenv

load_dotenv()

# ── General pipeline model ──────────────────────────────────────────────────
PIPELINE_MODEL: str = os.environ.get("PIPELINE_MODEL", "ollama/gemma4:31b-cloud")
PIPELINE_BASE_URL: str = os.environ.get("PIPELINE_BASE_URL", "http://localhost:11434")
PIPELINE_API_KEY: str | None = os.environ.get("PIPELINE_API_KEY") or None

# ── Per-role model overrides (fall back to PIPELINE_MODEL when None) ────────
SQL_MODEL: str | None = os.environ.get("SQL_MODEL") or None
SQL_AWS_REGION: str | None = os.environ.get("SQL_AWS_REGION") or None


BI_MODEL: str | None = os.environ.get("BI_MODEL") or None
BI_AWS_REGION: str | None = os.environ.get("BI_AWS_REGION") or None

# ── Observability ───────────────────────────────────────────────────────────
LANGSMITH_API_KEY: str | None = os.environ.get("LANGSMITH_API_KEY") or None
LANGSMITH_TRACING: bool = os.environ.get("LANGSMITH_TRACING") == "true"
LANGCHAIN_TRACING_V2: bool = os.environ.get("LANGCHAIN_TRACING_V2") == "true"
