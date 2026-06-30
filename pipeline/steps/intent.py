from tasks import TaskFactory
from pipeline.core import PipelineStep

_ICON = {"answerable": "✅", "partial": "🟡", "unanswerable": "❌"}


class IntentValidatorStep(PipelineStep):
    """Answerability gate. Runs after profiling, before the expensive build steps:
    asks whether the source data can actually answer the user's stated questions and
    records the decision in `state.intent_status` (skipped / proceed / blocked) plus the
    `intent_report.md` write."""

    def run(self) -> None:
        user_instructions = self.state.user_instructions
        if not user_instructions or not user_instructions.strip():
            print("[Flow] No business questions provided — skipping answerability check.")
            self.state.intent_report = ""
            self.state.intent_status = "skipped"
            return

        print("[Flow] Checking whether the data can answer your questions...")
        agent = self._ctx.build_factory().create_intent_validator()
        task = TaskFactory({"intent_validator": agent}).create_intent_validation_task()
        result = self._run_single_agent_crew(
            agent,
            task,
            {
                "user_instructions": user_instructions,
                "entity_map": self._ctx.entity_map_text(),
                "profiling_results": self.state.profiling_results,
            },
        )

        verdicts, summary = self._parse(result)
        report = self._render(verdicts, summary)
        self._write_report("intent_report.md", report)

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
        self.state.intent_report = report
        self.state.intent_status = "blocked" if (verdicts and answered == 0) else "proceed"

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
