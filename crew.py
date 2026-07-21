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
from logging_setup import get_logger
from utils.storage import get_storage_backend

_LOG = get_logger("Flow")

load_dotenv()

setup_telemetry()


def _write_verification_failure(state: DataEngineeringState, error: Exception) -> None:
    """A verification crash must not let the run finish looking verified: the
    failure becomes the verification report itself, on disk and in the UI."""
    report = (
        "# Answer Verification\n\n"
        f" Verification FAILED to run: {error}\n\n"
        "The numbers in the KPI report were NOT independently checked — treat them "
        "as unverified.\n\n"
        "Verification Status: FAILED — answers are unverified.\n"
    )
    state.verification_report = report
    try:
        path = os.path.join(state.reports_dir, "verification_report.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(report)
    except OSError as write_err:
        _LOG.info(f"Could not write verification_report.md: {write_err}")


class DataEngineeringFlow(Flow[DataEngineeringState]):
    MAX_ANALYTICS_CORRECTION = 1

    def _done(self, stage: str) -> bool:
        return stage in self.state.completed_stages

    def _mark_done(self, stage: str) -> None:
        """Record the stage and, for a queued run, checkpoint state to Redis
        so a crash/timeout can resume here instead of restarting the run."""
        self.state.completed_stages.append(stage)
        if self.state.run_id:
            from app import run_store

            try:
                run_store.save_checkpoint(
                    self.state.run_id, self.state.model_dump(mode="json")
                )
            except Exception as e:
                # A Redis blip must not kill the run — worst case, a later
                # crash resumes from an earlier stage instead of this one.
                _LOG.warning(f"Could not checkpoint stage {stage}: {e}")

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
        storage = get_storage_backend()
        if os.path.exists(self.state.db_path):
            os.remove(self.state.db_path)
        storage.delete("warehouse/warehouse.db")
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
            storage.delete(f"reports/{report}")

    @start()
    def profile_datasets(self) -> None:
        if self._done("profile_datasets"):
            # Resumed from checkpoint — local files may be missing if this
            # container was redeployed since the crash. Restore before
            # trusting "already done" stages that depend on them.
            storage = get_storage_backend()
            if not os.path.exists(self.state.db_path):
                storage.download_file("warehouse/warehouse.db", self.state.db_path)
            storage.sync_dir_down("data", self.state.data_dir)
            return
        self._clear_previous_run()
        try:
            ProfileStep(self._ctx()).run()
        except FileNotFoundError as e:
            _LOG.error(f"{e}")
            sys.exit(1)
        self._mark_done("profile_datasets")

    @listen(profile_datasets)
    def validate_intent(self) -> None:
        if self._done("validate_intent"):
            return
        IntentValidatorStep(self._ctx()).run()
        if self.state.intent_status == "blocked":
            _LOG.info(
                "The uploaded data cannot answer any of the questions you asked. "
                "See intent_report.md for what is missing — aborting before build."
            )
            print(self.state.intent_report)
            sys.exit(1)
        self._mark_done("validate_intent")

    @listen(validate_intent)
    def assess_quality(self) -> None:
        if self._done("assess_quality"):
            return
        QualityStep(self._ctx()).run()
        self._mark_done("assess_quality")

    @router(assess_quality)
    def check_quality_threshold(self) -> str:
        if self._done("check_quality_threshold"):
            return "proceed_pipeline"
        if self.state.quality_score < 60:
            is_web = isinstance(HumanLoopService._strategy, WebApprovalStrategy)
            if not is_web and not sys.stdin.isatty():
                _LOG.info(
                    f"Quality score {self.state.quality_score}/100 is below 60 — "
                    "auto-approving (non-interactive mode)."
                )
            else:
                _LOG.info("Quality below 60 — requesting operator approval...")
                summary = (
                    self.state.quality_report[:500] + "..."
                    if len(self.state.quality_report) > 500
                    else self.state.quality_report
                )
                if not HumanLoopService.request_human_approval(
                    self.state.quality_score, summary
                ):
                    _LOG.info("Pipeline aborted by operator.")
                    sys.exit(1)
        self._mark_done("check_quality_threshold")
        return "proceed_pipeline"

    @listen("proceed_pipeline")
    def design_schema(self) -> None:
        if self._done("design_schema"):
            return
        SchemaStep(self._ctx()).run()
        self._mark_done("design_schema")

    @listen(design_schema)
    def plan_transformations(self) -> None:
        if self._done("plan_transformations"):
            return
        TransformStep(self._ctx()).run()
        self._mark_done("plan_transformations")

    @listen(plan_transformations)
    def run_analytics(self) -> None:
        if self._done("run_analytics"):
            return
        AnalyticsStep(self._ctx()).run()
        self._mark_done("run_analytics")

    @listen(run_analytics)
    def verify_answers(self) -> None:
        if self._done("verify_answers"):
            return
        try:
            ctx = self._ctx()
            for round_idx in range(self.MAX_ANALYTICS_CORRECTION + 1):
                VerifyStep(ctx).run()
                if (
                    not self.state.definitions_diverged
                    or round_idx == self.MAX_ANALYTICS_CORRECTION
                ):
                    break
                names = ", ".join(d["name"] for d in self.state.definitions_diverged)
                _LOG.info(
                    f"Analytics deviated from agreed definition(s): {names} — "
                    f"corrective re-run {round_idx + 1}/{self.MAX_ANALYTICS_CORRECTION}"
                )
                AnalyticsStep(ctx).run()
            self.state.analytics_feedback = ""
        except Exception as e:
            _LOG.info(
                f"Answer verification FAILED: {e} — KPI report numbers are UNVERIFIED."
            )
            _write_verification_failure(self.state, e)
        self._mark_done("verify_answers")

    @listen(verify_answers)
    def compile_final_report(self) -> None:
        if self._done("compile_final_report"):
            return
        ReportStep(self._ctx()).run()
        self._mark_done("compile_final_report")
        storage = get_storage_backend()
        storage.upload_file(self.state.db_path, "warehouse/warehouse.db")
        storage.sync_dir_up(
            self.state.reports_dir, "reports", exclude={"execution.log"}
        )
