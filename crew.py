import os

os.environ["CREWAI_DISABLE_TELEMETRY"] = "true"
import sys
from crewai.flow.flow import Flow, start, listen, router
from crewai import LLM
from dotenv import load_dotenv
from tools import (
    ToolRegistry,
    HumanLoopService,
    DatabaseService,
    WebApprovalStrategy,
    ECommerceEntity,
)
from agents import AgentFactory
from pipeline import (
    DataEngineeringState,
    TokenReporter,
    setup_telemetry,
    ProfileStep,
    IntentValidatorStep,
    QualityStep,
    SchemaStep,
    TransformStep,
    AnalyticsStep,
    ReportStep,
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
            model_name=os.environ.get("PIPELINE_MODEL", "ollama/gemma4:31b-cloud"),
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

    def _build_entity_llm_fn(self):
        model = os.environ.get("PIPELINE_MODEL", "ollama/gemma4:31b-cloud")
        base_url = os.environ.get("PIPELINE_BASE_URL", "http://localhost:11434")
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

    def _clear_previous_run(self) -> None:
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
            path = os.path.join(self.state.reports_dir, report)
            if os.path.exists(path):
                os.remove(path)

    @start()
    def profile_datasets(self) -> None:
        self._reporter = TokenReporter()
        DatabaseService.clear_source_cache()
        self._clear_previous_run()
        try:
            result = ProfileStep(
                self.state.data_dir,
                self.state.reports_dir,
                llm_fallback_fn=self._build_entity_llm_fn(),
            ).run()
        except FileNotFoundError as e:
            print(f"[Flow] Error: {e}")
            sys.exit(1)
        self.state.files = result["files"]
        self.state.entity_map = result["entity_map"]
        self.state.profiling_results = result["profiling_results"]

    @listen(profile_datasets)
    def validate_intent(self) -> None:
        result = IntentValidatorStep(
            self.state.reports_dir, self._reporter, self._build_factory
        ).run(
            self.state.user_instructions,
            self.state.profiling_results,
            self._entity_map_text(),
        )
        self.state.intent_report = result["report"]
        if result["status"] == "blocked":
            print(
                "[Flow] The uploaded data cannot answer any of the questions you asked. "
                "See intent_report.md for what is missing — aborting before build."
            )
            print(result["report"])
            sys.exit(1)

    @listen(validate_intent)
    def assess_quality(self) -> None:
        report, score = QualityStep(
            self.state.reports_dir, self._reporter, self._build_factory
        ).run(self.state.profiling_results)
        self.state.quality_report = report
        self.state.quality_score = score

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
        self.state.star_schema = SchemaStep(
            self.state.reports_dir, self._reporter, self._build_factory
        ).run(
            self.state.profiling_results,
            self._entity_map_text(),
            self.state.user_instructions,
        )

    @listen(design_schema)
    def plan_transformations(self) -> None:
        result = TransformStep(
            self.state.db_path,
            self.state.data_dir,
            self.state.reports_dir,
            self._reporter,
            self._build_factory,
        ).run(
            self.state.star_schema,
            self.state.profiling_results,
            self.state.entity_map,
            self._entity_map_text(),
            self.state.user_instructions,
        )
        self.state.clean_sql = result["clean_sql"]
        self.state.primary_fact_table = result["primary_fact_table"]
        self.state.source_row_counts = result["source_row_counts"]
        self.state.verified_metrics = result["verified_metrics"]

    @listen(plan_transformations)
    def run_analytics(self) -> None:
        self.state.kpi_report = AnalyticsStep(
            self.state.reports_dir, self._reporter, self._build_factory
        ).run(
            self.state.clean_sql,
            self.state.primary_fact_table,
            self._entity_map_text(),
            self.state.verified_metrics,
            self.state.user_instructions,
        )

    @listen(run_analytics)
    def compile_final_report(self) -> None:
        self.state.final_summary = ReportStep(
            self.state.reports_dir, self._reporter, self._build_factory
        ).run(
            self.state.profiling_results,
            self.state.quality_report,
            self.state.star_schema,
            self.state.clean_sql,
            self.state.kpi_report,
            self.state.user_instructions,
        )
