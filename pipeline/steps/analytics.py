import json
import os
from crewai import Crew
from tasks import TaskFactory


class AnalyticsStep:
    def __init__(self, reports_dir: str, reporter, build_factory_fn):
        self._reports_dir = reports_dir
        self._reporter = reporter
        self._build_factory = build_factory_fn

    def run(
        self,
        clean_sql: str,
        primary_fact_table: str,
        entity_map_text: str,
        verified_metrics: dict,
        user_instructions: str,
    ) -> str:
        print("[Flow] Compiling business insights...")
        factory = self._build_factory()
        analytics = factory.create_analytics_engineer()
        task = TaskFactory(
            {"analytics_engineer": analytics}
        ).create_business_insights_task()
        crew = Crew(agents=[analytics], tasks=[task], verbose=True)
        try:
            result = crew.kickoff(
                inputs={
                    "clean_sql": clean_sql,
                    "primary_fact_table": primary_fact_table,
                    "entity_map": entity_map_text,
                    "verified_metrics": json.dumps(verified_metrics, indent=2),
                    "user_instructions": user_instructions,
                }
            )
            self._reporter.track(crew)
            kpi_report = (
                result.pydantic.report if result.pydantic else result.raw
            ) or ""
        except Exception as e:
            # Analytics is the last LLM-heavy step and the warehouse is already
            # built and validated — degrade gracefully instead of failing the run.
            print(f"[Flow] Analytics agent error: {e}")
            kpi_report = (
                "# Business Insights\n\n"
                f"_Automated analytics could not be generated: {e}_\n\n"
                "The warehouse is built and validated — query it directly or via "
                "the chat panel.\n"
            )

        with open(
            os.path.join(self._reports_dir, "kpi_report.md"), "w", encoding="utf-8"
        ) as f:
            f.write(kpi_report)
        return kpi_report
