from dataclasses import dataclass
from crewai import LLM
from agents import AgentFactory
from config import (
    PIPELINE_MODEL,
    PIPELINE_BASE_URL,
    PIPELINE_API_KEY,
    SQL_MODEL,
    SQL_AWS_REGION,
    BI_MODEL,
    BI_AWS_REGION,
)
from tools import ConnectionManager, ToolRegistry, ECommerceEntity
from .state import DataEngineeringState
from .token_reporter import TokenReporter


@dataclass
class StepContext:
    state: DataEngineeringState
    cm: ConnectionManager
    reporter: TokenReporter

    @property
    def reports_dir(self) -> str:
        return self.state.reports_dir

    def entity_map_text(self) -> str:
        return "\n".join(
            f"  - {fn}: {entity}" for fn, entity in self.state.entity_map.items()
        )

    def build_factory(self) -> AgentFactory:
        """A fresh AgentFactory wired to this run's tools (shared connection manager,
        entity-tagged memory). Model routing comes from the environment."""
        registry = ToolRegistry(
            data_dir=self.state.data_dir,
            chroma_db_path=".chroma",
            db_path=self.state.db_path,
            entity_map=self.state.entity_map,
            connection_manager=self.cm,
        )
        return AgentFactory(
            model_name=PIPELINE_MODEL,
            base_url=PIPELINE_BASE_URL,
            tool_registry=registry,
            sql_model_name=SQL_MODEL,
            sql_region=SQL_AWS_REGION,
            bi_model_name=BI_MODEL,
            bi_region=BI_AWS_REGION,
        )

    def entity_llm_fn(self):
        """LLM fallback for low-confidence entity classification (used by ProfileStep)."""
        from agents.providers import LLM_RESILIENCE_KWARGS

        kwargs: dict = {
            "model": PIPELINE_MODEL,
            "base_url": PIPELINE_BASE_URL,
            "temperature": 0.0,
            **LLM_RESILIENCE_KWARGS,
        }
        if PIPELINE_API_KEY:
            kwargs["api_key"] = PIPELINE_API_KEY
        llm = LLM(**kwargs)
        valid = [e.value for e in ECommerceEntity if e != ECommerceEntity.UNKNOWN]

        def fn(columns: list, filename: str) -> str:
            prompt = (
                f"Classify this dataset into exactly one entity type.\n"
                f"Filename: {filename}\nColumns: {', '.join(columns[:30])}\n"
                f"Valid types: {', '.join(valid)}\n"
                f"Return ONLY the entity type name, nothing else."
            )
            try:
                response = llm.call([{"role": "user", "content": prompt}])
                entity = response.strip().lower()
                return entity if entity in valid else "unknown"
            except Exception:
                return "unknown"

        return fn

    def build_sql_llm(self):
        """SQL-translation LLM for the answer verifier. Mirrors AgentFactory's provider
        auto-selection for the SQL model (independent of the analytics agent)."""
        model = SQL_MODEL or PIPELINE_MODEL
        if model.startswith("bedrock/"):
            from agents.providers import BedrockProvider

            return BedrockProvider(model, SQL_AWS_REGION).create(0.0)
        if model.startswith("ollama/"):
            from agents.providers import OllamaProvider

            return OllamaProvider(model, PIPELINE_BASE_URL).create(0.0)
        from agents.providers import CloudProvider

        return CloudProvider(model).create(0.0)
