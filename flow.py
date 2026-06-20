import os
import re
import sys
import json
from typing import List
from pydantic import BaseModel
from crewai import Crew, LLM
from crewai.flow.flow import Flow, start, listen, router
from tools import ToolRegistry, HumanLoopService
from agents import AgentFactory
from tasks import TaskFactory


class DataEngineeringState(BaseModel):
    data_dir: str = "data"
    reports_dir: str = "reports"
    db_path: str = "data/warehouse.db"
    files: List[str] = []
    profiling_results: str = ""
    quality_report: str = ""
    quality_score: int = 100
    star_schema: str = ""
    clean_sql: str = ""
    kpi_report: str = ""
    final_summary: str = ""
    agent_token_usage: dict[str, dict[str, int]] = {}


class DataEngineeringFlow(Flow[DataEngineeringState]):
    def _get_factory_setup(self):
        registry = ToolRegistry(
            data_dir=self.state.data_dir,
            chroma_db_path=".chroma",
            db_path=self.state.db_path,
        )
        factory = AgentFactory(
            model_name="ollama/gemma4:31b-cloud",
            base_url="http://localhost:11434",
            tool_registry=registry,
        )
        return factory, registry

    def _track_crew_usage(self, crew: Crew):
        if not self.state.agent_token_usage:
            self.state.agent_token_usage = {}
            
        for agent in crew.agents:
            role = agent.role
            if hasattr(agent, "llm") and hasattr(agent.llm, "get_token_usage_summary"):
                try:
                    usage = agent.llm.get_token_usage_summary()
                    if role not in self.state.agent_token_usage:
                        self.state.agent_token_usage[role] = {
                            "prompt_tokens": 0,
                            "completion_tokens": 0,
                            "total_tokens": 0,
                            "successful_requests": 0
                        }
                    
                    self.state.agent_token_usage[role]["prompt_tokens"] += usage.prompt_tokens
                    self.state.agent_token_usage[role]["completion_tokens"] += usage.completion_tokens
                    self.state.agent_token_usage[role]["total_tokens"] += usage.total_tokens
                    self.state.agent_token_usage[role]["successful_requests"] += usage.successful_requests
                except Exception as e:
                    print(f"[Flow] Warning: Failed to track token usage for agent '{role}': {e}")

    def _generate_token_report(self):
        if not self.state.agent_token_usage:
            print("[Flow] No token usage tracked.")
            return

        total_prompt = sum(metrics["prompt_tokens"] for metrics in self.state.agent_token_usage.values())
        total_completion = sum(metrics["completion_tokens"] for metrics in self.state.agent_token_usage.values())
        total_tokens = sum(metrics["total_tokens"] for metrics in self.state.agent_token_usage.values())
        total_requests = sum(metrics["successful_requests"] for metrics in self.state.agent_token_usage.values())

        # Write to JSON
        usage_report_path = os.path.join(self.state.reports_dir, "token_usage_report.json")
        with open(usage_report_path, "w", encoding="utf-8") as f:
            json.dump(self.state.agent_token_usage, f, indent=2)

        # Write to Markdown
        md = []
        md.append("# Pipeline Token Usage Report\n")
        md.append("This report details the LLM token usage across all steps in the Autonomous Data Engineering pipeline.\n")
        md.append("## Summary Table\n")
        md.append("| Agent / Role | Prompt Tokens | Completion Tokens | Total Tokens | Requests |")
        md.append("|---|---|---|---|---|")
        for role, metrics in self.state.agent_token_usage.items():
            md.append(f"| {role} | {metrics['prompt_tokens']:,} | {metrics['completion_tokens']:,} | {metrics['total_tokens']:,} | {metrics['successful_requests']} |")
        md.append(f"| **TOTAL** | **{total_prompt:,}** | **{total_completion:,}** | **{total_tokens:,}** | **{total_requests}** |\n")

        md_content = "\n".join(md)
        md_report_path = os.path.join(self.state.reports_dir, "token_usage_report.md")
        with open(md_report_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        print("\n=======================================================")
        print("                TOKEN USAGE SUMMARY")
        print("=======================================================")
        for role, metrics in self.state.agent_token_usage.items():
            print(f"Agent: {role}")
            print(f"  Prompt Tokens:      {metrics['prompt_tokens']:,}")
            print(f"  Completion Tokens:  {metrics['completion_tokens']:,}")
            print(f"  Total Tokens:       {metrics['total_tokens']:,}")
            print(f"  Requests:           {metrics['successful_requests']}")
        print("-------------------------------------------------------")
        print(f"TOTAL PROMPT TOKENS:     {total_prompt:,}")
        print(f"TOTAL COMPLETION TOKENS: {total_completion:,}")
        print(f"TOTAL TOKENS USED:       {total_tokens:,}")
        print(f"TOTAL API REQUESTS:      {total_requests}")
        print("=======================================================\n")


    @start()
    def profile_datasets(self):
        print("[Flow] Starting data profiling...")
        if os.path.exists(self.state.db_path):
            os.remove(self.state.db_path)
            
        old_reports = [
            "profiling_report.json", "quality_report.md", 
            "schema_design.md", "transformations.sql", 
            "kpi_report.md", "executive_summary.md"
        ]
        for report in old_reports:
            old_file = os.path.join(self.state.reports_dir, report)
            if os.path.exists(old_file):
                os.remove(old_file)
        os.makedirs(self.state.data_dir, exist_ok=True)
        discovered = [
            f
            for f in os.listdir(self.state.data_dir)
            if f.endswith((".csv", ".xlsx", ".xls", ".json"))
        ]
        if not discovered:
            print("[Flow] Error: No datasets found in data directory.")
            sys.exit(1)
        self.state.files = discovered

        factory, _ = self._get_factory_setup()
        profiler = factory.create_profiler()

        task_factory = TaskFactory({"profiler": profiler})
        task = task_factory.create_profiling_task()

        crew = Crew(agents=[profiler], tasks=[task], verbose=True)
        result = crew.kickoff(inputs={"files": ", ".join(self.state.files)})
        self._track_crew_usage(crew)
        self.state.profiling_results = result.raw

        os.makedirs(self.state.reports_dir, exist_ok=True)
        with open(
            os.path.join(self.state.reports_dir, "profiling_report.json"),
            "w",
            encoding="utf-8",
        ) as f:
            try:
                json_data = json.loads(result.raw)
                json.dump(json_data, f, indent=2)
            except Exception:
                f.write(result.raw)

    @listen(profile_datasets)
    def assess_quality(self):
        print("[Flow] Assessing data quality...")
        factory, _ = self._get_factory_setup()
        quality_eng = factory.create_quality_engineer()

        task_factory = TaskFactory({"quality_engineer": quality_eng})
        task = task_factory.create_quality_task()

        crew = Crew(agents=[quality_eng], tasks=[task], verbose=True)
        result = crew.kickoff(
            inputs={"profiling_results": self.state.profiling_results}
        )
        self._track_crew_usage(crew)
        self.state.quality_report = result.raw

        with open(
            os.path.join(self.state.reports_dir, "quality_report.md"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(result.raw)

        match = re.search(r"Quality\s+Score:\s*(\d+)", result.raw, re.IGNORECASE)
        if match:
            self.state.quality_score = int(match.group(1))
        else:
            self.state.quality_score = 75
        print(f"[Flow] Calculated quality score: {self.state.quality_score}/100")

    @router(assess_quality)
    def check_quality_threshold(self):
        if self.state.quality_score < 80:
            print("[Flow] Quality score is below 80. Requesting operator approval...")
            summary = (
                self.state.quality_report[:500] + "..."
                if len(self.state.quality_report) > 500
                else self.state.quality_report
            )
            approved = HumanLoopService.request_human_approval(self.state.quality_score, summary)
            if not approved:
                print("[Flow] Pipeline execution aborted by operator.")
                sys.exit(1)
        return "proceed_pipeline"

    @listen("proceed_pipeline")
    def design_schema(self):
        print("[Flow] Designing schema...")
        factory, _ = self._get_factory_setup()
        architect = factory.create_warehouse_architect()

        task_factory = TaskFactory({"warehouse_architect": architect})
        task = task_factory.create_schema_design_task()

        crew = Crew(agents=[architect], tasks=[task], verbose=True)
        result = crew.kickoff(
            inputs={"profiling_results": self.state.profiling_results}
        )
        self._track_crew_usage(crew)
        self.state.star_schema = result.raw

        with open(
            os.path.join(self.state.reports_dir, "schema_design.md"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(self.state.star_schema)

    @listen(design_schema)
    def plan_transformations(self):
        print("[Flow] Planning transformations...")
        from tools.db_tools import DatabaseService
        mappings = []
        for filename in os.listdir(self.state.data_dir):
            if filename.endswith((".csv", ".xlsx", ".xls", ".json")):
                sanitized = DatabaseService.sanitize_table_name(filename)
                mappings.append(f"- '{filename}' is loaded in DuckDB as table/view: '{sanitized}'")
        table_mapping_text = "\n".join(mappings)

        factory, _ = self._get_factory_setup()
        architect = factory.create_warehouse_architect()
        task_factory = TaskFactory({"warehouse_architect": architect})
        task = task_factory.create_transformation_task()
        crew = Crew(agents=[architect], tasks=[task], verbose=True)
        result = crew.kickoff(
            inputs={
                "quality_report": self.state.quality_report,
                "star_schema": self.state.star_schema,
                "table_mapping_text": table_mapping_text,
            }
        )
        self._track_crew_usage(crew)
        self.state.clean_sql = result.raw
        trans_file = os.path.join(self.state.reports_dir, "transformations.sql")
        with open(trans_file, "w", encoding="utf-8") as f:
            f.write(self.state.clean_sql)

        max_retries = 2
        for attempt in range(max_retries + 1):
            if os.path.exists(self.state.db_path):
                os.remove(self.state.db_path)

            print(f"[Flow] Executing transformation SQL (attempt {attempt + 1}/{max_retries + 1})...")
            errors = DatabaseService.execute_sql_script(
                self.state.db_path, trans_file, self.state.data_dir
            )

            if not errors:
                print("[Flow] All SQL statements executed successfully.")
                break

            if attempt < max_retries:
                error_report = "\n".join(
                    f"- Statement {e['statement_index']}: {e['error']}\n  SQL: {e['sql_snippet']}"
                    for e in errors
                )
                print(f"[Flow] {len(errors)} SQL errors detected. Sending back to Warehouse Architect for correction (retry {attempt + 1}/{max_retries})...")

                fix_factory, _ = self._get_factory_setup()
                fix_architect = fix_factory.create_warehouse_architect()
                fix_task_factory = TaskFactory({"warehouse_architect": fix_architect})
                fix_task = fix_task_factory.create_sql_fix_task()
                fix_crew = Crew(agents=[fix_architect], tasks=[fix_task], verbose=True)
                fix_result = fix_crew.kickoff(
                    inputs={
                        "original_sql": self.state.clean_sql,
                        "error_report": error_report,
                        "profiling_results": self.state.profiling_results,
                        "table_mapping_text": table_mapping_text,
                    }
                )
                self._track_crew_usage(fix_crew)
                self.state.clean_sql = fix_result.raw
                with open(trans_file, "w", encoding="utf-8") as f:
                    f.write(self.state.clean_sql)
            else:
                print(f"[Flow] {len(errors)} SQL errors remain after {max_retries} retries. Proceeding with partial warehouse.")

    @listen(plan_transformations)
    def run_analytics(self):
        print("[Flow] Compiling business insights...")
        factory, _ = self._get_factory_setup()
        analytics = factory.create_analytics_engineer()

        task_factory = TaskFactory({"analytics_engineer": analytics})
        task = task_factory.create_business_insights_task()

        crew = Crew(agents=[analytics], tasks=[task], verbose=True)
        result = crew.kickoff(inputs={"clean_sql": self.state.clean_sql})
        self._track_crew_usage(crew)
        self.state.kpi_report = result.raw

        with open(
            os.path.join(self.state.reports_dir, "kpi_report.md"), "w", encoding="utf-8"
        ) as f:
            f.write(self.state.kpi_report)

    @listen(run_analytics)
    def compile_final_report(self):
        print("[Flow] Compiling final executive summaries...")
        factory, _ = self._get_factory_setup()
        lead = factory.create_lead_architect()

        task_factory = TaskFactory({"lead_architect": lead})
        task = task_factory.create_final_report_task()

        crew = Crew(agents=[lead], tasks=[task], verbose=True)
        result = crew.kickoff(
            inputs={
                "profiling_results": self.state.profiling_results,
                "quality_report": self.state.quality_report,
                "star_schema": self.state.star_schema,
                "clean_sql": self.state.clean_sql,
                "kpi_report": self.state.kpi_report,
            }
        )
        self._track_crew_usage(crew)
        self.state.final_summary = result.raw

        with open(
            os.path.join(self.state.reports_dir, "executive_summary.md"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(self.state.final_summary)

        self._generate_token_report()

        print(
            "[Flow] Completed execution. All reports generated in 'reports/' directory."
        )
