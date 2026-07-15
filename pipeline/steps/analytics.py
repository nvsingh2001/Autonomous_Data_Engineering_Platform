import json

from tasks import TaskFactory
from pipeline.core import PipelineStep


class AnalyticsStep(PipelineStep):
    """Generates the business-insights KPI report → `state.kpi_report`. Degrades
    gracefully (the warehouse is already built and validated) rather than failing the run."""

    def run(self) -> None:
        feedback = self.state.analytics_feedback
        print(
            "[Flow] Recomputing business insights (definition correction)..."
            if feedback
            else "[Flow] Compiling business insights..."
        )
        user_instructions = self.state.user_instructions
        if feedback:
            user_instructions = f"{user_instructions}\n\n{feedback}"
        analytics = self._ctx.build_factory().create_analytics_engineer()
        task = TaskFactory(
            {"analytics_engineer": analytics}
        ).create_business_insights_task()
        try:
            result = self._run_single_agent_crew(
                analytics,
                task,
                {
                    "clean_sql": self.state.clean_sql,
                    "primary_fact_table": self.state.primary_fact_table,
                    "entity_map": self._ctx.entity_map_text(),
                    "verified_metrics": json.dumps(
                        self.state.verified_metrics, indent=2
                    ),
                    "user_instructions": user_instructions,
                },
            )
            kpi_report = self._extract(result)
        except Exception as e:
            print(f"[Flow] Analytics agent error: {e}")
            kpi_report = (
                "# Business Insights\n\n"
                f"_Automated analytics could not be generated: {e}_\n\n"
                "The warehouse is built and validated — query it directly or via "
                "the chat panel.\n"
            )

        self._write_report("kpi_report.md", kpi_report)
        self.state.kpi_report = kpi_report
