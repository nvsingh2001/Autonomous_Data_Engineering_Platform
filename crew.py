import os

os.environ["CREWAI_DISABLE_TELEMETRY"] = "true"
import sys
from crewai.flow.flow import Flow, start, listen, router
from dotenv import load_dotenv
from tools import (
    HumanLoopService,
    ConnectionManager,
    WebApprovalStrategy,
)
from pipeline import (
    DataEngineeringState,
    StepContext,
    TokenReporter,
    setup_telemetry,
    ProfileStep,
    IntentValidatorStep,
    QualityStep,
    SchemaStep,
    TransformStep,
    AnalyticsStep,
    VerifyStep,
    ReportStep,
)

load_dotenv()

setup_telemetry()


class DataEngineeringFlow(Flow[DataEngineeringState]):
    def _ctx(self) -> StepContext:
        """The per-run StepContext: bundles the shared state, the connection manager
        (owns the source cache + all DuckDB lifecycle), and the token reporter. Created
        once (lazily) and handed to every step."""
        ctx = getattr(self, "_step_ctx", None)
        if ctx is None:
            ctx = StepContext(
                state=self.state,
                cm=ConnectionManager(self.state.db_path, self.state.data_dir),
                reporter=TokenReporter(),
            )
            self._step_ctx = ctx
        return ctx

    def _clear_previous_run(self) -> None:
        if os.path.exists(self.state.db_path):
            os.remove(self.state.db_path)
        for report in [
            "profiling_report.json",
            "quality_report.md",
            "schema_design.md",
            "transformations.sql",
            "kpi_report.md",
            "verification_report.md",
            "executive_summary.md",
        ]:
            path = os.path.join(self.state.reports_dir, report)
            if os.path.exists(path):
                os.remove(path)

    @start()
    def profile_datasets(self) -> None:
        self._clear_previous_run()
        try:
            ProfileStep(self._ctx()).run()
        except FileNotFoundError as e:
            print(f"[Flow] Error: {e}")
            sys.exit(1)

    @listen(profile_datasets)
    def validate_intent(self) -> None:
        IntentValidatorStep(self._ctx()).run()
        if self.state.intent_status == "blocked":
            print(
                "[Flow] The uploaded data cannot answer any of the questions you asked. "
                "See intent_report.md for what is missing — aborting before build."
            )
            print(self.state.intent_report)
            sys.exit(1)

    @listen(validate_intent)
    def assess_quality(self) -> None:
        QualityStep(self._ctx()).run()

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
        SchemaStep(self._ctx()).run()

    @listen(design_schema)
    def plan_transformations(self) -> None:
        TransformStep(self._ctx()).run()

    @listen(plan_transformations)
    def run_analytics(self) -> None:
        AnalyticsStep(self._ctx()).run()

    @listen(run_analytics)
    def verify_answers(self) -> None:
        # Independent recompute of each requested metric from the agreed definition, flagged
        # into verification_report.md. A safety net, never a gate — a verifier failure must
        # not block the final report.
        try:
            VerifyStep(self._ctx()).run()
        except Exception as e:
            print(f"[Flow] Answer verification skipped (error): {e}")

    @listen(verify_answers)
    def compile_final_report(self) -> None:
        ReportStep(self._ctx()).run()
