import re

from tasks import TaskFactory
from pipeline.core import PipelineStep


class QualityStep(PipelineStep):
    """Scores data quality and writes `state.quality_report` + `state.quality_score`
    (the score gates the human-in-the-loop router)."""

    def run(self) -> None:
        print("[Flow] Assessing data quality...")
        quality_eng = self._ctx.build_factory().create_quality_engineer()
        task = TaskFactory({"quality_engineer": quality_eng}).create_quality_task()
        result = self._run_single_agent_crew(
            quality_eng, task, {"profiling_results": self.state.profiling_results}
        )

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
        self._write_report("quality_report.md", report)

        print(f"[Flow] Quality score: {score}/100")
        self.state.quality_report = report
        self.state.quality_score = score
