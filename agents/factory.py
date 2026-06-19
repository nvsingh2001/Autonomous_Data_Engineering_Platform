from crewai import Agent, LLM
from tools import ToolRegistry

class AgentFactory:
    def __init__(self, model_name: str, base_url: str, tool_registry: ToolRegistry):
        self._model_name = model_name
        self._base_url = base_url
        self._registry = tool_registry

    def _get_llm(self, temp: float) -> LLM:
        return LLM(
            model=self._model_name,
            base_url=self._base_url,
            temperature=temp
        )

    def create_profiler(self) -> Agent:
        db_tools = self._registry.get_db_tools()
        profiler_tools = [t for t in db_tools if t.name in ["profile_csv_file", "read_csv_preview"]]
        return Agent(
            role="Data Profiling Engineer",
            goal="Analyze datasets to detect datatypes, missing values, duplicates, and column structure.",
            backstory="Expert data profiler who analyzes raw data structures and outputs structured profiling summaries.",
            tools=profiler_tools,
            llm=self._get_llm(0.0),
            verbose=True,
            max_iter=3
        )

    def create_quality_engineer(self) -> Agent:
        db_tools = self._registry.get_db_tools()
        quality_tools = [t for t in db_tools if t.name in ["profile_csv_file", "run_duckdb_query"]]
        return Agent(
            role="Data Quality Engineer",
            goal="Scan datasets for anomalies, duplicates, and integrity issues, computing a quality score out of 100.",
            backstory="Meticulous quality engineer dedicated to finding anomalies and estimating data reliability.",
            tools=quality_tools,
            llm=self._get_llm(0.1),
            verbose=True,
            max_iter=3
        )

    def create_warehouse_architect(self) -> Agent:
        all_tools = self._registry.get_all_tools()
        architect_tools = [t for t in all_tools if t.name in ["run_duckdb_query", "save_past_execution", "search_past_executions"]]
        return Agent(
            role="Data Warehouse Architect",
            goal="Discover relationships, design star schema with Fact/Dimension tables, and write transformation SQL queries.",
            backstory="Senior warehouse architect who models facts/dimensions and designs optimized transformation flows.",
            tools=architect_tools,
            llm=self._get_llm(0.1),
            verbose=True,
            max_iter=3
        )

    def create_analytics_engineer(self) -> Agent:
        db_tools = self._registry.get_db_tools()
        analytics_tools = [t for t in db_tools if t.name == "run_duckdb_query"]
        return Agent(
            role="Analytics Engineer",
            goal="Generate KPI analyses, run analytical queries, and produce business insight summaries.",
            backstory="Business-savvy analytics engineer who extracts key performance indicators and patterns from data.",
            tools=analytics_tools,
            llm=self._get_llm(0.2),
            verbose=True,
            max_iter=3
        )

    def create_lead_architect(self) -> Agent:
        all_tools = self._registry.get_all_tools()
        lead_tools = [t for t in all_tools if t.name in ["search_past_executions"]]
        return Agent(
            role="Senior Data Architect",
            goal="Review all data reports, coordinate findings, and produce final executive summaries.",
            backstory="Supervising architect who coordinates data quality, schemas, and analytical recommendations.",
            tools=lead_tools,
            llm=self._get_llm(0.5),
            verbose=True,
            max_iter=3
        )
