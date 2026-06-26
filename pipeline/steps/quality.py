import re
import os
from crewai import Crew
from tasks import TaskFactory


class QualityStep:
    def __init__(self, reports_dir: str, reporter, build_factory_fn):
        self._reports_dir = reports_dir
        self._reporter = reporter
        self._build_factory = build_factory_fn

    def run(self, profiling_results: str) -> tuple[str, int]:
        print("[Flow] Assessing data quality...")
        factory = self._build_factory()
        quality_eng = factory.create_quality_engineer()
        task = TaskFactory({"quality_engineer": quality_eng}).create_quality_task()
        crew = Crew(agents=[quality_eng], tasks=[task], verbose=True)
        result = crew.kickoff(inputs={"profiling_results": profiling_results})
        self._reporter.track(crew)

        report = result.raw
        with open(
            os.path.join(self._reports_dir, "quality_report.md"), "w", encoding="utf-8"
        ) as f:
            f.write(report)

        match = re.search(r"Quality\s+Score:\s*(\d+)", report, re.IGNORECASE)
        score = int(match.group(1)) if match else 75
        print(f"[Flow] Quality score: {score}/100")
        return report, score
