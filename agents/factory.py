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
    def __init__(self, model_name: str):
        self._model_name = model_name

    def create(self, temperature: float) -> LLM:
        llm = LLM(model=self._model_name, temperature=temperature, drop_params=True)
        if "nemotron" in self._model_name.lower():
            llm.supports_function_calling = lambda: False
            if hasattr(llm, "_get_inference_config"):
                original = llm._get_inference_config
                llm._get_inference_config = lambda: {
                    k: v for k, v in original().items() if k != "stopSequences"
                }
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
    ):
        self._provider = self._build_provider(model_name, base_url)
        self._registry = tool_registry
        with open(config_path, "r", encoding="utf-8") as f:
            self._config = yaml.safe_load(f)

    @staticmethod
    def _build_provider(model_name: str, base_url: str) -> LLMProvider:
        if model_name.startswith("ollama/"):
            return OllamaProvider(model_name, base_url)
        if model_name.startswith("bedrock/"):
            return BedrockProvider(model_name)
        return CloudProvider(model_name)

    def _make_agent(self, key: str, tools: list, temperature: float, max_iter: int = 15) -> Agent:
        cfg = self._config[key]
        return Agent(
            role=cfg["role"],
            goal=cfg["goal"],
            backstory=cfg["backstory"],
            tools=tools,
            llm=self._provider.create(temperature),
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
        return self._make_agent("warehouse_architect", tools, 0.1)

    def create_analytics_engineer(self) -> Agent:
        tools = self._filter_tools(
            self._registry.get_db_tools(),
            ("run_duckdb_query",),
        )
        return self._make_agent("analytics_engineer", tools, 0.2)

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
        return self._make_agent("validation_engineer", tools, 0.0)
