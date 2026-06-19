import yaml
from crewai import Agent, LLM
from tools import ToolRegistry

class AgentFactory:
    def __init__(self, model_name: str, base_url: str, tool_registry: ToolRegistry, config_path: str = "config/agents.yaml"):
        self._model_name = model_name
        self._base_url = base_url
        self._registry = tool_registry
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)

    def _get_llm(self, temp: float) -> LLM:
        return LLM(
            model=self._model_name,
            base_url=self._base_url,
            temperature=temp
        )

    def create_profiler(self) -> Agent:
        db_tools = self._registry.get_db_tools()
        profiler_tools = [t for t in db_tools if t.name in ["profile_csv_file", "read_csv_preview"]]
        cfg = self.config["profiler"]
        return Agent(
            role=cfg["role"],
            goal=cfg["goal"],
            backstory=cfg["backstory"],
            tools=profiler_tools,
            llm=self._get_llm(0.0),
            verbose=True,
            max_iter=3
        )

    def create_quality_engineer(self) -> Agent:
        db_tools = self._registry.get_db_tools()
        quality_tools = [t for t in db_tools if t.name in ["profile_csv_file", "run_duckdb_query"]]
        cfg = self.config["quality_engineer"]
        return Agent(
            role=cfg["role"],
            goal=cfg["goal"],
            backstory=cfg["backstory"],
            tools=quality_tools,
            llm=self._get_llm(0.1),
            verbose=True,
            max_iter=3
        )

    def create_warehouse_architect(self) -> Agent:
        all_tools = self._registry.get_all_tools()
        architect_tools = [t for t in all_tools if t.name in ["run_duckdb_query", "save_past_execution", "search_past_executions"]]
        cfg = self.config["warehouse_architect"]
        return Agent(
            role=cfg["role"],
            goal=cfg["goal"],
            backstory=cfg["backstory"],
            tools=architect_tools,
            llm=self._get_llm(0.1),
            verbose=True,
            max_iter=3
        )

    def create_analytics_engineer(self) -> Agent:
        db_tools = self._registry.get_db_tools()
        analytics_tools = [t for t in db_tools if t.name == "run_duckdb_query"]
        cfg = self.config["analytics_engineer"]
        return Agent(
            role=cfg["role"],
            goal=cfg["goal"],
            backstory=cfg["backstory"],
            tools=analytics_tools,
            llm=self._get_llm(0.2),
            verbose=True,
            max_iter=3
        )

    def create_lead_architect(self) -> Agent:
        all_tools = self._registry.get_all_tools()
        lead_tools = [t for t in all_tools if t.name in ["search_past_executions"]]
        cfg = self.config["lead_architect"]
        return Agent(
            role=cfg["role"],
            goal=cfg["goal"],
            backstory=cfg["backstory"],
            tools=lead_tools,
            llm=self._get_llm(0.5),
            verbose=True,
            max_iter=3
        )
