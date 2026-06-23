import os
import yaml
from abc import ABC, abstractmethod
from crewai import Agent, LLM
from tools import ToolRegistry


class LLMProvider(ABC):
    @abstractmethod
    def create(self, temperature: float) -> LLM: ...


class OllamaProvider(LLMProvider):
    def __init__(self, model_name: str, base_url: str):
        self._model_name = model_name
        self._base_url = base_url

    def create(self, temperature: float) -> LLM:
        return LLM(
            model=self._model_name,
            temperature=temperature,
            base_url=self._base_url,
            extra_body={"options": {"num_ctx": 8192}},
        )


class BedrockProvider(LLMProvider):
    def __init__(self, model_name: str, region: str | None = None):
        self._model_name = model_name
        self._region = region

    def create(self, temperature: float) -> LLM:
        kwargs: dict = {
            "model": self._model_name,
            "temperature": temperature,
            "drop_params": True,
        }
        if self._region:
            kwargs["aws_region_name"] = self._region
            os.environ["AWS_DEFAULT_REGION"] = self._region
            os.environ["AWS_REGION_NAME"] = self._region
        llm = LLM(**kwargs)
        # Models that don't support stopSequences in the inference config
        NO_STOP_SEQ = ("nemotron", "qwen", "kimi", "mistral", "deepseek", "grok", "glm")
        # Models that don't support native function calling (use ReAct text format)
        NO_NATIVE_FC = ("nemotron", "qwen", "kimi")
        model_lower = self._model_name.lower()
        if any(m in model_lower for m in NO_STOP_SEQ) and hasattr(
            llm, "_get_inference_config"
        ):
            original = llm._get_inference_config
            llm._get_inference_config = lambda: {
                k: v for k, v in original().items() if k != "stopSequences"
            }
        if any(m in model_lower for m in NO_NATIVE_FC):
            llm.supports_function_calling = lambda: False
        return llm


class CloudProvider(LLMProvider):
    def __init__(self, model_name: str):
        self._model_name = model_name

    def create(self, temperature: float) -> LLM:
        return LLM(model=self._model_name, temperature=temperature)


class AgentFactory:
    def __init__(
        self,
        model_name: str,
        base_url: str,
        tool_registry: ToolRegistry,
        config_path: str = "config/agents.yaml",
        sql_model_name: str | None = None,
        sql_region: str | None = None,
        validation_model_name: str | None = None,
        validation_region: str | None = None,
        bi_model_name: str | None = None,
        bi_region: str | None = None,
    ):
        self._provider = self._build_provider(model_name, base_url)
        self._sql_provider = (
            self._build_provider(sql_model_name, base_url, sql_region)
            if sql_model_name
            else None
        )
        self._validation_provider = (
            self._build_provider(validation_model_name, base_url, validation_region)
            if validation_model_name
            else None
        )
        self._bi_provider = (
            self._build_provider(bi_model_name, base_url, bi_region)
            if bi_model_name
            else None
        )
        self._registry = tool_registry
        with open(config_path, "r", encoding="utf-8") as f:
            self._config = yaml.safe_load(f)

    @staticmethod
    def _build_provider(
        model_name: str, base_url: str, region: str | None = None
    ) -> LLMProvider:
        if model_name.startswith("ollama/"):
            return OllamaProvider(model_name, base_url)
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
        use_validation_provider: bool = False,
        use_bi_provider: bool = False,
    ) -> Agent:
        cfg = self._config[key]
        if use_validation_provider and self._validation_provider:
            provider = self._validation_provider
        elif use_bi_provider and self._bi_provider:
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
            verbose=True,
            max_iter=max_iter,
        )

    def _filter_tools(self, pool: list, names: tuple) -> list:
        return [t for t in pool if t.name in names]

    def create_profiler(self) -> Agent:
        tools = self._filter_tools(
            self._registry.get_db_tools(),
            ("profile_csv_file", "read_csv_preview"),
        )
        return self._make_agent("profiler", tools, 0.0)

    def create_quality_engineer(self) -> Agent:
        tools = self._filter_tools(
            self._registry.get_db_tools(),
            ("profile_csv_file", "run_duckdb_query"),
        )
        return self._make_agent("quality_engineer", tools, 0.1)

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
            ("run_duckdb_query", "search_past_executions"),
        )
        return self._make_agent(
            "analytics_engineer", tools, 0.2, max_iter=25, use_bi_provider=True
        )

    def create_lead_architect(self) -> Agent:
        tools = self._filter_tools(
            self._registry.get_all_tools(),
            ("search_past_executions",),
        )
        return self._make_agent("lead_architect", tools, 0.5)

    def create_validation_engineer(self) -> Agent:
        tools = self._filter_tools(
            self._registry.get_db_tools(),
            ("run_duckdb_query",),
        )
        return self._make_agent(
            "validation_engineer", tools, 0.0, use_validation_provider=True
        )
