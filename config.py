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
# Railway's 500 logs/sec). Default on for local dev; set to "false" in production.
CREW_VERBOSE: bool = os.environ.get("CREW_VERBOSE", "true").lower() == "true"

SQL_MODEL: str | None = os.environ.get("SQL_MODEL") or None
SQL_AWS_REGION: str | None = os.environ.get("SQL_AWS_REGION") or None


BI_MODEL: str | None = os.environ.get("BI_MODEL") or None
BI_AWS_REGION: str | None = os.environ.get("BI_AWS_REGION") or None

LANGSMITH_API_KEY: str | None = os.environ.get("LANGSMITH_API_KEY") or None
LANGSMITH_TRACING: bool = os.environ.get("LANGSMITH_TRACING") == "true"
LANGCHAIN_TRACING_V2: bool = os.environ.get("LANGCHAIN_TRACING_V2") == "true"
