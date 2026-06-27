import os
import re
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

        output = result.pydantic
        if output is not None:
            report = output.report
            score = output.score
        else:
            report = result.raw or ""
            m = re.search(r"Quality\s+Score:\s*\**\s*(\d+)", report, re.IGNORECASE)
            score = int(m.group(1)) if m else 0
            print(
                "[Flow] Warning: quality output was unstructured — scraped score "
                f"from raw text (score={score})."
            )

        report = f"<!-- Quality Score: {score}/100 -->\n\n{report}"
        with open(
            os.path.join(self._reports_dir, "quality_report.md"), "w", encoding="utf-8"
        ) as f:
            f.write(report)

        print(f"[Flow] Quality score: {score}/100")
        return report, score
