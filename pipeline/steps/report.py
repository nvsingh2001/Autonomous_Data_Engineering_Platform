import os
from crewai import Crew
from tasks import TaskFactory


class ReportStep:
    def __init__(self, reports_dir: str, reporter, build_factory_fn):
        self._reports_dir = reports_dir
        self._reporter = reporter
        self._build_factory = build_factory_fn

    def run(
        self,
        profiling_results: str,
        quality_report: str,
        star_schema: str,
        clean_sql: str,
        kpi_report: str,
    ) -> str:
        print("[Flow] Compiling final executive summaries...")
        factory = self._build_factory()
        lead = factory.create_lead_architect()
        task = TaskFactory({"lead_architect": lead}).create_final_report_task()
        crew = Crew(agents=[lead], tasks=[task], verbose=True)
        result = crew.kickoff(
            inputs={
                "profiling_results": profiling_results,
                "quality_report": quality_report,
                "star_schema": star_schema,
                "clean_sql": clean_sql,
                "kpi_report": kpi_report,
            }
        )
        self._reporter.track(crew)

        final_summary = result.raw
        with open(
            os.path.join(self._reports_dir, "executive_summary.md"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(final_summary)

        self._reporter.write(self._reports_dir)
        print("[Flow] Completed. All reports generated in 'reports/'.")
        return final_summary
