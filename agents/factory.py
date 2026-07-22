import yaml
from crewai import Agent
from config import PIPELINE_API_KEY, CREW_VERBOSE
from tools import ToolRegistry
from .providers import LLMProvider, OllamaProvider, BedrockProvider, CloudProvider


class AgentFactory:
    def __init__(
        self,
        model_name: str,
        base_url: str,
        tool_registry: ToolRegistry,
        config_path: str = "config/agents.yaml",
        sql_model_name: str | None = None,
        sql_region: str | None = None,
        bi_model_name: str | None = None,
        bi_region: str | None = None,
    ):
        api_key = PIPELINE_API_KEY
        self._provider = self._build_provider(model_name, base_url, api_key=api_key)
        self._sql_provider = (
            self._build_provider(sql_model_name, base_url, sql_region, api_key)
            if sql_model_name
            else None
        )
        self._bi_provider = (
            self._build_provider(bi_model_name, base_url, bi_region, api_key)
            if bi_model_name
            else None
        )
        self._registry = tool_registry
        with open(config_path, "r", encoding="utf-8") as f:
            self._config = yaml.safe_load(f)

    @staticmethod
    def _build_provider(
        model_name: str,
        base_url: str,
        region: str | None = None,
        api_key: str | None = None,
    ) -> LLMProvider:
        if model_name.startswith("ollama/"):
            return OllamaProvider(model_name, base_url, api_key)
        if model_name.startswith("bedrock/"):
            return BedrockProvider(model_name, region)
        return CloudProvider(model_name)

    def _make_agent(
        self,
        key: str,
        tools: list,
        temperature: float,
        max_iter: int = 15,
        use_sql_provider: bool = False,
        use_bi_provider: bool = False,
    ) -> Agent:
        cfg = self._config[key]
        if use_bi_provider and self._bi_provider:
            provider = self._bi_provider
        elif use_sql_provider:
            provider = self._sql_provider or self._provider
        else:
            provider = self._provider
        return Agent(
            role=cfg["role"],
            goal=cfg["goal"],
            backstory=cfg["backstory"],
            tools=tools,
            llm=provider.create(temperature),
            verbose=CREW_VERBOSE,
            max_iter=max_iter,
        )

    def _filter_tools(self, pool: list, names: tuple) -> list:
        return [t for t in pool if t.name in names]

    def create_quality_engineer(self) -> Agent:
        tools = self._filter_tools(
            self._registry.get_db_tools(),
            ("profile_csv_file", "run_duckdb_query"),
        )
        return self._make_agent("quality_engineer", tools, 0.1)

    def create_intent_validator(self) -> Agent:
        return self._make_agent("intent_validator", [], 0.1)

    def create_semantics_analyst(self) -> Agent:
        # Classifies every column across every source file in one batched call — needs
        # the SQL provider's larger context and stronger structured-output reasoning,
        # not the lighter default PIPELINE_MODEL used for single-verdict tasks.
        return self._make_agent("semantics_analyst", [], 0.1, use_sql_provider=True)

    def create_warehouse_architect(self) -> Agent:
        tools = self._filter_tools(
            self._registry.get_all_tools(),
            ("run_duckdb_query", "save_past_execution", "search_past_executions"),
        )
        return self._make_agent(
            "warehouse_architect", tools, 0.1, use_sql_provider=True
        )

    def create_analytics_engineer(self) -> Agent:
        tools = self._filter_tools(
            self._registry.get_all_tools(),
            (
                "run_duckdb_query",
                "search_past_executions",
                "save_past_execution",
                "record_claims",
            ),
        )
        return self._make_agent(
            "analytics_engineer", tools, 0.2, max_iter=35, use_bi_provider=True
        )

    def create_lead_architect(self) -> Agent:
        tools = self._filter_tools(
            self._registry.get_all_tools(),
            ("search_past_executions",),
        )
        return self._make_agent("lead_architect", tools, 0.5)

    def create_chat_analyst(self, extra_tools: list | None = None) -> Agent:
        tools = self._filter_tools(
            self._registry.get_db_tools(),
            ("run_duckdb_query",),
        ) + (extra_tools or [])
        return self._make_agent(
            "data_chat_analyst", tools, 0.1, max_iter=8, use_bi_provider=True
        )
