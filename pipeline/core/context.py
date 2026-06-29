"""Per-run shared services for pipeline steps.

`StepContext` bundles the cross-cutting collaborators every step needs — the run state,
the connection manager, the token reporter — and centralizes the factory/LLM construction
that used to live ad-hoc on the Flow and inside individual steps. Built once in `crew.py`
and passed to every `PipelineStep`.
"""

import os
from dataclasses import dataclass

from crewai import LLM

from agents import AgentFactory
from tools import ConnectionManager, ToolRegistry, ECommerceEntity

from .state import DataEngineeringState
from .token_reporter import TokenReporter

_DEFAULT_MODEL = "ollama/gemma4:31b-cloud"
_DEFAULT_BASE_URL = "http://localhost:11434"


@dataclass
class StepContext:
    state: DataEngineeringState  # the SAME object as Flow.state — step mutations propagate
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
            model_name=os.environ.get("PIPELINE_MODEL", _DEFAULT_MODEL),
            base_url=os.environ.get("PIPELINE_BASE_URL", _DEFAULT_BASE_URL),
            tool_registry=registry,
            sql_model_name=os.environ.get("SQL_MODEL") or None,
            sql_region=os.environ.get("SQL_AWS_REGION") or None,
            validation_model_name=os.environ.get("VALIDATION_MODEL") or None,
            validation_region=os.environ.get("VALIDATION_AWS_REGION") or None,
            bi_model_name=os.environ.get("BI_MODEL") or None,
            bi_region=os.environ.get("BI_AWS_REGION") or None,
        )

    def entity_llm_fn(self):
        """LLM fallback for low-confidence entity classification (used by ProfileStep)."""
        model = os.environ.get("PIPELINE_MODEL", _DEFAULT_MODEL)
        base_url = os.environ.get("PIPELINE_BASE_URL", _DEFAULT_BASE_URL)
        llm = LLM(model=model, base_url=base_url, temperature=0.0)
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
        model = os.environ.get("SQL_MODEL") or os.environ.get(
            "PIPELINE_MODEL", _DEFAULT_MODEL
        )
        if model.startswith("bedrock/"):
            from agents.providers import BedrockProvider

            return BedrockProvider(model, os.environ.get("SQL_AWS_REGION") or None).create(0.0)
        if model.startswith("ollama/"):
            from agents.providers import OllamaProvider

            return OllamaProvider(
                model, os.environ.get("PIPELINE_BASE_URL", _DEFAULT_BASE_URL)
            ).create(0.0)
        from agents.providers import CloudProvider

        return CloudProvider(model).create(0.0)
