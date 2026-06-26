import os
from crewai import Crew
from tasks import TaskFactory


class SchemaStep:
    def __init__(self, reports_dir: str, reporter, build_factory_fn):
        self._reports_dir = reports_dir
        self._reporter = reporter
        self._build_factory = build_factory_fn

    def run(self, profiling_results: str, entity_map_text: str) -> str:
        print("[Flow] Designing schema...")
        factory = self._build_factory()
        architect = factory.create_warehouse_architect()
        task = TaskFactory(
            {"warehouse_architect": architect}
        ).create_schema_design_task()
        crew = Crew(agents=[architect], tasks=[task], verbose=True)
        result = crew.kickoff(
            inputs={
                "profiling_results": profiling_results,
                "entity_map": entity_map_text,
            }
        )
        self._reporter.track(crew)

        star_schema = result.raw
        with open(
            os.path.join(self._reports_dir, "schema_design.md"), "w", encoding="utf-8"
        ) as f:
            f.write(star_schema)
        return star_schema
