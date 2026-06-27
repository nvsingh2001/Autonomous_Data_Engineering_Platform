import os
from crewai import Crew
from tasks import TaskFactory

_ICON = {"answerable": "✅", "partial": "🟡", "unanswerable": "❌"}


class IntentValidatorStep:
    """Answerability gate. Runs after profiling, before the expensive build steps:
    asks whether the source data can actually answer the user's stated questions and
    returns a gate decision (proceed / blocked / skipped)."""

    def __init__(self, reports_dir: str, reporter, build_factory_fn):
        self._reports_dir = reports_dir
        self._reporter = reporter
        self._build_factory = build_factory_fn

    def run(
        self,
        user_instructions: str,
        profiling_results: str,
        entity_map_text: str,
    ) -> dict:
        if not user_instructions or not user_instructions.strip():
            print("[Flow] No business questions provided — skipping answerability check.")
            return {"status": "skipped", "report": "", "verdicts": [], "counts": {}}

        print("[Flow] Checking whether the data can answer your questions...")
        factory = self._build_factory()
        agent = factory.create_intent_validator()
        task = TaskFactory({"intent_validator": agent}).create_intent_validation_task()
        crew = Crew(agents=[agent], tasks=[task], verbose=True)
        result = crew.kickoff(
            inputs={
                "user_instructions": user_instructions,
                "entity_map": entity_map_text,
                "profiling_results": profiling_results,
            }
        )
        self._reporter.track(crew)

        verdicts, summary = self._parse(result)
        report = self._render(verdicts, summary)
        with open(
            os.path.join(self._reports_dir, "intent_report.md"), "w", encoding="utf-8"
        ) as f:
            f.write(report)

        counts = {"answerable": 0, "partial": 0, "unanswerable": 0}
        for v in verdicts:
            counts[v["verdict"]] = counts.get(v["verdict"], 0) + 1
        print(
            f"[Flow] Answerability: {counts['answerable']} answerable, "
            f"{counts['partial']} partial, {counts['unanswerable']} unanswerable."
        )

        # Block only when the user asked something AND nothing is even partially
        # answerable. If verdicts could not be parsed, proceed (never abort blindly).
        answered = counts["answerable"] + counts["partial"]
        status = "blocked" if (verdicts and answered == 0) else "proceed"
        return {
            "status": status,
            "report": report,
            "verdicts": verdicts,
            "counts": counts,
        }

    def _parse(self, result) -> tuple[list[dict], str]:
        if result.pydantic:
            verdicts = [v.model_dump() for v in result.pydantic.verdicts]
            return verdicts, result.pydantic.summary
        # Unstructured output — surface the raw text, return no verdicts so the gate
        # proceeds rather than blocking on an unparseable response.
        print("[Flow] Warning: answerability output was unstructured — proceeding.")
        return [], (result.raw or "")

    def _render(self, verdicts: list[dict], summary: str) -> str:
        lines = ["# Answerability Assessment", "", summary, "", "## Per-question verdicts", ""]
        if not verdicts:
            lines.append("_No structured verdicts were returned._")
        for v in verdicts:
            lines.append(
                f"- {_ICON.get(v['verdict'], '•')} **{v['verdict'].upper()}** — {v['question']}"
            )
            lines.append(f"  - {v['reason']}")
        return "\n".join(lines) + "\n"
