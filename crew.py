import os

os.environ["CREWAI_DISABLE_TELEMETRY"] = "true"
import re
import sys
import json
from crewai import Crew
from crewai.flow.flow import Flow, start, listen, router
from dotenv import load_dotenv

from tools import (
    ToolRegistry,
    HumanLoopService,
    DatabaseService,
    EntityClassifier,
    WebApprovalStrategy,
    ProfileCSVFileTool,
)
from agents import AgentFactory
from tasks import TaskFactory
from pipeline import (
    DataEngineeringState,
    TokenReporter,
    extract_columns_from_raw,
    SchemaPlanner,
    TableBuilder,
    compute_verified_metrics,
    setup_telemetry,
)

load_dotenv()

setup_telemetry()


class DataEngineeringFlow(Flow[DataEngineeringState]):
    def _build_factory(self) -> AgentFactory:
        registry = ToolRegistry(
            data_dir=self.state.data_dir,
            chroma_db_path=".chroma",
            db_path=self.state.db_path,
            entity_map=self.state.entity_map,
        )
        return AgentFactory(
            model_name=os.environ.get("PIPELINE_MODEL"),
            base_url=os.environ.get("PIPELINE_BASE_URL", "http://localhost:11434"),
            tool_registry=registry,
            sql_model_name=os.environ.get("SQL_MODEL") or None,
            sql_region=os.environ.get("SQL_AWS_REGION") or None,
            validation_model_name=os.environ.get("VALIDATION_MODEL") or None,
            validation_region=os.environ.get("VALIDATION_AWS_REGION") or None,
            bi_model_name=os.environ.get("BI_MODEL") or None,
            bi_region=os.environ.get("BI_AWS_REGION") or None,
        )

    def _entity_map_text(self) -> str:
        return "\n".join(
            f"  - {fn}: {entity}" for fn, entity in self.state.entity_map.items()
        )

    @start()
    def profile_datasets(self) -> None:
        self._reporter = TokenReporter()
        print("[Flow] Starting data profiling...")
        DatabaseService.clear_source_cache()
        if os.path.exists(self.state.db_path):
            os.remove(self.state.db_path)

        for report in [
            "profiling_report.json",
            "quality_report.md",
            "schema_design.md",
            "transformations.sql",
            "kpi_report.md",
            "executive_summary.md",
        ]:
            old = os.path.join(self.state.reports_dir, report)
            if os.path.exists(old):
                os.remove(old)

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

        combined_results: dict = {}

        profiler_tool = ProfileCSVFileTool(data_dir=self.state.data_dir)
        for filename in self.state.files:
            print(f"[Flow] Profiling file: {filename}...")
            try:
                combined_results[filename] = profiler_tool.profile_as_dict(filename)
            except Exception as e:
                print(f"[Flow] Warning: Failed to profile {filename}: {e}")
                combined_results[filename] = {"raw_output": str(e)}

        entity_map: dict = {}
        for filename, raw_profile in combined_results.items():
            cols, row_count = extract_columns_from_raw(raw_profile, filename)
            if not cols and "raw_output" in raw_profile:
                cols = re.findall(r'"([^"]+)":\s*\{', raw_profile["raw_output"])

            classification = EntityClassifier.classify(
                cols, row_count=row_count, filename=filename
            )
            entity_map[filename] = classification["entity"].value
            combined_results[filename]["_entity"] = classification["entity"].value
            combined_results[filename]["_entity_confidence"] = classification[
                "confidence"
            ]
            combined_results[filename]["_entity_grain"] = classification["grain"]
            if classification["notes"]:
                combined_results[filename]["_entity_notes"] = classification["notes"]
            print(
                f"[Flow] Entity: {filename} → {classification['entity'].value}"
                f" (conf={classification['confidence']:.2f})"
                f"{' | ' + classification['notes'] if classification['notes'] else ''}"
            )
        self.state.entity_map = entity_map

        self.state.profiling_results = json.dumps(combined_results, indent=2)
        os.makedirs(self.state.reports_dir, exist_ok=True)
        with open(
            os.path.join(self.state.reports_dir, "profiling_report.json"),
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(combined_results, f, indent=2)

    @listen(profile_datasets)
    def assess_quality(self) -> None:
        print("[Flow] Assessing data quality...")
        factory = self._build_factory()
        quality_eng = factory.create_quality_engineer()
        task = TaskFactory({"quality_engineer": quality_eng}).create_quality_task()
        crew = Crew(agents=[quality_eng], tasks=[task], verbose=True)
        result = crew.kickoff(
            inputs={"profiling_results": self.state.profiling_results}
        )
        self._reporter.track(crew)
        self.state.quality_report = result.raw
        with open(
            os.path.join(self.state.reports_dir, "quality_report.md"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(result.raw)
        match = re.search(r"Quality\s+Score:\s*(\d+)", result.raw, re.IGNORECASE)
        self.state.quality_score = int(match.group(1)) if match else 75
        print(f"[Flow] Quality score: {self.state.quality_score}/100")

    @router(assess_quality)
    def check_quality_threshold(self) -> str:
        if self.state.quality_score < 60:
            is_web = isinstance(HumanLoopService._strategy, WebApprovalStrategy)
            if not is_web and not sys.stdin.isatty():
                print(
                    f"[Flow] Quality score {self.state.quality_score}/100 is below 60 — "
                    "auto-approving (non-interactive mode)."
                )
            else:
                print("[Flow] Quality below 60 — requesting operator approval...")
                summary = (
                    self.state.quality_report[:500] + "..."
                    if len(self.state.quality_report) > 500
                    else self.state.quality_report
                )
                if not HumanLoopService.request_human_approval(
                    self.state.quality_score, summary
                ):
                    print("[Flow] Pipeline aborted by operator.")
                    sys.exit(1)
        return "proceed_pipeline"

    @listen("proceed_pipeline")
    def design_schema(self) -> None:
        print("[Flow] Designing schema...")
        factory = self._build_factory()
        architect = factory.create_warehouse_architect()
        task = TaskFactory(
            {"warehouse_architect": architect}
        ).create_schema_design_task()
        crew = Crew(agents=[architect], tasks=[task], verbose=True)
        result = crew.kickoff(
            inputs={
                "profiling_results": self.state.profiling_results,
                "entity_map": self._entity_map_text(),
            }
        )
        self._reporter.track(crew)
        self.state.star_schema = result.raw
        with open(
            os.path.join(self.state.reports_dir, "schema_design.md"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(self.state.star_schema)

    @listen(design_schema)
    def plan_transformations(self) -> None:
        print("[Flow] Planning transformations...")

        print("[Flow] Counting source rows for data retention audit...")
        try:
            self.state.source_row_counts = DatabaseService.count_source_rows(
                self.state.data_dir
            )
            print(
                f"[Flow] Source row counts: {dict(sorted(self.state.source_row_counts.items(), key=lambda x: -x[1]))}"
            )
        except Exception as e:
            print(f"[Flow] Warning: Could not count source rows: {e}")

        planner = SchemaPlanner(
            self.state.data_dir, self.state.star_schema, self.state.source_row_counts
        )
        table_mapping = planner.table_mapping_text()

        # Phase 1: Schema plan (LLM → JSON)
        print("[Flow] Generating schema plan...")
        factory = self._build_factory()
        architect = factory.create_warehouse_architect()
        plan_crew = Crew(
            agents=[architect],
            tasks=[
                TaskFactory(
                    {"warehouse_architect": architect}
                ).create_schema_plan_task()
            ],
            verbose=True,
        )
        plan_raw = plan_crew.kickoff(
            inputs={
                "star_schema": self.state.star_schema,
                "entity_map": self._entity_map_text(),
                "table_mapping_text": table_mapping,
            }
        )
        self._reporter.track(plan_crew)
        schema_plan = planner.parse_schema_plan(plan_raw.raw)
        print(f"[Flow] Schema plan: {[t['name'] for t in schema_plan]}")

        # Phase 2: Per-table build loop
        builder = TableBuilder(
            db_path=self.state.db_path,
            data_dir=self.state.data_dir,
            reports_dir=self.state.reports_dir,
            profiling_results=self.state.profiling_results,
            star_schema=self.state.star_schema,
            build_factory_fn=self._build_factory,
            track_usage_fn=self._reporter.track,
        )
        _, combined_sql, primary_fact = builder.build_all(schema_plan, table_mapping)

        self.state.clean_sql = combined_sql
        self.state.primary_fact_table = primary_fact

        if not self.state.primary_fact_table:
            raise ValueError(
                "No Fact_ tables were successfully created. Pipeline cannot continue."
            )

        for err in builder.run_retention_check(self.state.source_row_counts):
            print(f"[Flow] Retention warning: {err['error'][:120]}...")

        print("[Flow] Running database validation agent...")
        try:
            vf = self._build_factory()
            ve = vf.create_validation_engineer()
            val_crew = Crew(
                agents=[ve],
                tasks=[
                    TaskFactory({"validation_engineer": ve}).create_validation_task()
                ],
                verbose=True,
            )
            val_res = val_crew.kickoff(
                inputs={
                    "source_row_counts": json.dumps(self.state.source_row_counts),
                    "primary_fact_table": self.state.primary_fact_table,
                    "entity_map": self._entity_map_text(),
                }
            )
            self._reporter.track(val_crew)
            with open(
                os.path.join(self.state.reports_dir, "validation_report.md"),
                "w",
                encoding="utf-8",
            ) as f:
                f.write(val_res.raw)
            status = "FAIL" if "Validation Status: FAIL" in val_res.raw else "PASS"
            print(f"[Flow] Validation {status}.")
            if status == "FAIL":
                raise RuntimeError(
                    "Validation FAILED — warehouse assertions did not pass. "
                    "See validation_report.md for details."
                )
        except RuntimeError:
            raise
        except Exception as e:
            print(f"[Flow] Validation agent error: {e}")

        self.state.verified_metrics = compute_verified_metrics(
            self.state.db_path, self.state.primary_fact_table, self.state.entity_map
        )
        print(
            f"[Flow] Verified metrics: {list(self.state.verified_metrics.get('fact_tables', {}).keys())}"
        )
        metrics_path = os.path.join(self.state.reports_dir, "verified_metrics.json")
        try:
            with open(metrics_path, "w", encoding="utf-8") as f:
                json.dump(self.state.verified_metrics, f, indent=2)
            print(f"[Flow] Saved verified metrics to {metrics_path}")
        except Exception as e:
            print(f"[Flow] Error saving verified metrics: {e}")

    @listen(plan_transformations)
    def run_analytics(self) -> None:
        print("[Flow] Compiling business insights...")
        factory = self._build_factory()
        analytics = factory.create_analytics_engineer()
        task = TaskFactory(
            {"analytics_engineer": analytics}
        ).create_business_insights_task()
        crew = Crew(agents=[analytics], tasks=[task], verbose=True)
        result = crew.kickoff(
            inputs={
                "clean_sql": self.state.clean_sql,
                "primary_fact_table": self.state.primary_fact_table,
                "entity_map": self._entity_map_text(),
                "verified_metrics": json.dumps(self.state.verified_metrics, indent=2),
                "user_instructions": self.state.user_instructions,
            }
        )
        self._reporter.track(crew)
        self.state.kpi_report = result.raw
        with open(
            os.path.join(self.state.reports_dir, "kpi_report.md"), "w", encoding="utf-8"
        ) as f:
            f.write(self.state.kpi_report)

    @listen(run_analytics)
    def compile_final_report(self) -> None:
        print("[Flow] Compiling final executive summaries...")
        factory = self._build_factory()
        lead = factory.create_lead_architect()
        task = TaskFactory({"lead_architect": lead}).create_final_report_task()
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
        self._reporter.track(crew)
        self.state.final_summary = result.raw
        with open(
            os.path.join(self.state.reports_dir, "executive_summary.md"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(self.state.final_summary)

        self._reporter.write(self.state.reports_dir)
        print("[Flow] Completed. All reports generated in 'reports/'.")
