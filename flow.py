import os
import re
import sys
import json
from typing import List
from pydantic import BaseModel
from crewai import Crew
from crewai.flow.flow import Flow, start, listen, router
from tools import ToolRegistry, request_human_approval
from agents import AgentFactory
from tasks import TaskFactory


class DataEngineeringState(BaseModel):
    data_dir: str = "data"
    reports_dir: str = "reports"
    files: List[str] = [
        "crm_customers.csv",
        "products.csv",
        "sales_transactions.csv",
        "support_logs.csv",
    ]
    profiling_results: str = ""
    quality_report: str = ""
    quality_score: int = 100
    star_schema: str = ""
    clean_sql: str = ""
    kpi_report: str = ""
    final_summary: str = ""


class DataEngineeringFlow(Flow[DataEngineeringState]):
    def _get_factory_setup(self):
        registry = ToolRegistry(data_dir=self.state.data_dir, chroma_db_path=".chroma")
        factory = AgentFactory(
            model_name="ollama/gemma4:31b-cloud",
            base_url="http://localhost:11434",
            tool_registry=registry,
        )
        return factory, registry

    @start()
    def profile_datasets(self):
        print("[Flow] Starting data profiling...")
        factory, _ = self._get_factory_setup()
        profiler = factory.create_profiler()

        task_factory = TaskFactory({"profiler": profiler})
        task = task_factory.create_profiling_task(self.state.files)

        crew = Crew(agents=[profiler], tasks=[task], verbose=True)
        result = crew.kickoff()
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

        profiler = factory.create_profiler()
        dummy_profiling_task = TaskFactory(
            {"profiler": profiler}
        ).create_profiling_task(self.state.files)
        dummy_profiling_task.output = self.state.profiling_results

        task = task_factory.create_quality_task(dummy_profiling_task)

        crew = Crew(agents=[quality_eng], tasks=[task], verbose=True)
        result = crew.kickoff()
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
            return "hitl_approval"
        return "proceed_pipeline"

    @listen("hitl_approval")
    def run_human_approval(self):
        print("[Flow] Quality score is below 80. Requesting operator approval...")
        summary = (
            self.state.quality_report[:500] + "..."
            if len(self.state.quality_report) > 500
            else self.state.quality_report
        )
        approved = request_human_approval(self.state.quality_score, summary)
        if approved:
            return "proceed_pipeline"
        print("[Flow] Pipeline execution aborted by operator.")
        sys.exit(1)

    @listen("proceed_pipeline")
    def design_warehouse_and_transform(self):
        print("[Flow] Designing schema and transformations...")
        factory, _ = self._get_factory_setup()
        architect = factory.create_warehouse_architect()

        task_factory = TaskFactory({"warehouse_architect": architect})

        profiler = factory.create_profiler()
        dummy_profiling = TaskFactory({"profiler": profiler}).create_profiling_task(
            self.state.files
        )
        dummy_profiling.output = self.state.profiling_results

        quality_eng = factory.create_quality_engineer()
        dummy_quality = TaskFactory(
            {"quality_engineer": quality_eng}
        ).create_quality_task(dummy_profiling)
        dummy_quality.output = self.state.quality_report

        schema_task = task_factory.create_schema_design_task(dummy_profiling)
        transform_task = task_factory.create_transformation_task(
            dummy_quality, schema_task
        )

        crew = Crew(
            agents=[architect], tasks=[schema_task, transform_task], verbose=True
        )
        crew.kickoff()

        self.state.star_schema = schema_task.output.raw
        self.state.clean_sql = transform_task.output.raw

        with open(
            os.path.join(self.state.reports_dir, "schema_design.md"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(self.state.star_schema)

        with open(
            os.path.join(self.state.reports_dir, "transformations.sql"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(self.state.clean_sql)

    @listen(design_warehouse_and_transform)
    def run_insights_and_report(self):
        print("[Flow] Compiling business insights and final executive summaries...")
        factory, _ = self._get_factory_setup()
        analytics = factory.create_analytics_engineer()
        lead = factory.create_lead_architect()

        task_factory = TaskFactory(
            {"analytics_engineer": analytics, "lead_architect": lead}
        )

        architect = factory.create_warehouse_architect()
        dummy_transform = TaskFactory(
            {"warehouse_architect": architect}
        ).create_transformation_task(None, None)
        dummy_transform.output = self.state.clean_sql

        insights_task = task_factory.create_business_insights_task(dummy_transform)

        profiler = factory.create_profiler()
        dummy_profiling = TaskFactory({"profiler": profiler}).create_profiling_task(
            self.state.files
        )
        dummy_profiling.output = self.state.profiling_results

        quality_eng = factory.create_quality_engineer()
        dummy_quality = TaskFactory(
            {"quality_engineer": quality_eng}
        ).create_quality_task(dummy_profiling)
        dummy_quality.output = self.state.quality_report

        dummy_schema = TaskFactory(
            {"warehouse_architect": architect}
        ).create_schema_design_task(dummy_profiling)
        dummy_schema.output = self.state.star_schema

        final_report_task = task_factory.create_final_report_task(
            [
                dummy_profiling,
                dummy_quality,
                dummy_schema,
                dummy_transform,
                insights_task,
            ]
        )

        crew = Crew(
            agents=[analytics, lead],
            tasks=[insights_task, final_report_task],
            verbose=True,
        )
        crew.kickoff()

        self.state.kpi_report = insights_task.output.raw
        self.state.final_summary = final_report_task.output.raw

        with open(
            os.path.join(self.state.reports_dir, "kpi_report.md"), "w", encoding="utf-8"
        ) as f:
            f.write(self.state.kpi_report)

        with open(
            os.path.join(self.state.reports_dir, "executive_summary.md"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(self.state.final_summary)

        print(
            "[Flow] Completed execution. All reports generated in 'reports/' directory."
        )
