import json

from tasks import TaskFactory
from utils import SemanticGrounder
from pipeline.core import PipelineStep
from logging_setup import get_logger

_LOG = get_logger("Flow")


class ClassifySemanticsStep(PipelineStep):
    """Grounded column-level semantic classification. An LLM proposes a structural and
    business role per source column from real sample values (never the column name);
    SemanticGrounder then verifies every proposal against the already-computed profiling
    stats before anything downstream trusts it. Failure here is non-fatal — an empty
    `state.column_semantics` means every consumer takes its existing name-heuristic
    fallback path unchanged."""

    def run(self) -> None:
        self.state.column_semantics = {}
        try:
            self._classify()
        except Exception as e:
            _LOG.warning(f"Column semantic classification failed (non-fatal): {e}")

    def _classify(self) -> None:
        profiling_data = json.loads(self.state.profiling_results or "{}")
        if not profiling_data:
            return

        _LOG.info("Classifying column semantics...")
        agent = self._ctx.build_factory().create_semantics_analyst()
        task = TaskFactory({"semantics_analyst": agent}).create_column_classification_task()
        result = self._run_single_agent_crew(
            agent,
            task,
            {
                "entity_map": self._ctx.entity_map_text(),
                "profiling_results": json.dumps(self._trim(profiling_data)),
            },
        )

        proposals = self._parse(result)
        if not proposals:
            return

        grounded = SemanticGrounder.ground(proposals, profiling_data)
        n_struct = sum(1 for g in grounded if g["structural_grounded"])
        n_biz = sum(1 for g in grounded if g["business_grounded"])
        _LOG.info(
            f"Column semantics: {len(grounded)} proposed, "
            f"{n_struct} structurally grounded, {n_biz} business-role grounded."
        )
        self._write_report("column_semantics.json", json.dumps(grounded, indent=2))
        self.state.column_semantics = SemanticGrounder.build_lookup(grounded)

    def _trim(self, profiling_data: dict) -> dict:
        """The classifier only needs, per column, the fields it must judge from —
        datatype, null/uniqueness signal, and sample values. Passing the full profiling
        report (encoding, duplicate_rows, anomalies, entity-classifier notes, ...) roughly
        doubles the prompt for no classification benefit and risks the model's context
        window on datasets with many files."""
        trimmed: dict = {}
        for file_name, profile in profiling_data.items():
            if not isinstance(profile, dict):
                continue
            samples = profile.get("sample_values", {})
            trimmed[file_name] = {
                "columns": [
                    {
                        "name": c["name"],
                        "datatype": c.get("datatype"),
                        "unique_count": c.get("unique_count"),
                        "null_percentage": c.get("null_percentage"),
                        "sample_values": samples.get(c["name"], []),
                    }
                    for c in profile.get("columns", [])
                ]
            }
        return trimmed

    def _parse(self, result) -> list[dict]:
        if result.pydantic:
            return [c.model_dump() for c in result.pydantic.columns]
        _LOG.warning("column semantics output was unstructured — skipping this run.")
        return []
