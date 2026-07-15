import os

from crewai import Crew

from config import CREW_VERBOSE
from .context import StepContext


class PipelineStep:
    def __init__(self, ctx: StepContext):
        self._ctx = ctx

    @property
    def state(self):
        return self._ctx.state

    @property
    def cm(self):
        return self._ctx.cm

    @property
    def reporter(self):
        return self._ctx.reporter

    @property
    def reports_dir(self) -> str:
        return self._ctx.state.reports_dir

    def run(self) -> None:
        raise NotImplementedError

    def _write_report(self, filename: str, content: str) -> None:
        path = os.path.join(self.reports_dir, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    @staticmethod
    def _extract(result, attr: str = "report") -> str:
        """The output a step keeps: the structured pydantic field when present, else the
        raw text. One place instead of `result.pydantic.X if result.pydantic else result.raw`
        scattered across every step."""
        if result.pydantic:
            return getattr(result.pydantic, attr)
        return result.raw or ""

    def _run_single_agent_crew(self, agent, task, inputs: dict):
        """Run a one-agent, one-task crew and account its tokens. Returns the crew result."""
        crew = Crew(agents=[agent], tasks=[task], verbose=CREW_VERBOSE)
        result = crew.kickoff(inputs=inputs)
        self.reporter.track(crew)
        return result
