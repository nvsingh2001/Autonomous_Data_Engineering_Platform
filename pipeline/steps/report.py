from tasks import TaskFactory
from pipeline.core import PipelineStep


class ReportStep(PipelineStep):
    """Compiles the executive summary → `state.final_summary` + `executive_summary.md`,
    then flushes the token-usage report (last step of the run)."""

    def run(self) -> None:
        print("[Flow] Compiling final executive summaries...")
        lead = self._ctx.build_factory().create_lead_architect()
        task = TaskFactory({"lead_architect": lead}).create_final_report_task()
        result = self._run_single_agent_crew(
            lead,
            task,
            {
                "profiling_results": self.state.profiling_results,
                "quality_report": self.state.quality_report,
                "star_schema": self.state.star_schema,
                "clean_sql": self.state.clean_sql,
                "kpi_report": self.state.kpi_report,
                "user_instructions": self.state.user_instructions,
            },
        )
        final_summary = self._extract(result)
        self._write_report("executive_summary.md", final_summary)

        self.reporter.write(self.reports_dir)
        print("[Flow] Completed. All reports generated in 'reports/'.")
        self.state.final_summary = final_summary
