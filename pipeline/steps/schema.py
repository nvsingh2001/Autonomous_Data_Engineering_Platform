from tasks import TaskFactory
from pipeline.core import PipelineStep


class SchemaStep(PipelineStep):
    """Designs the star schema and writes `state.star_schema` + `schema_design.md`."""

    def run(self) -> None:
        print("[Flow] Designing schema...")
        architect = self._ctx.build_factory().create_warehouse_architect()
        task = TaskFactory(
            {"warehouse_architect": architect}
        ).create_schema_design_task()
        result = self._run_single_agent_crew(
            architect,
            task,
            {
                "profiling_results": self.state.profiling_results,
                "entity_map": self._ctx.entity_map_text(),
                "user_instructions": self.state.user_instructions,
            },
        )
        self.state.star_schema = self._extract(result)
        self._write_report("schema_design.md", self.state.star_schema)
