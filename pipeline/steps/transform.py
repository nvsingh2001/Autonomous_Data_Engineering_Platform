import os
import json
from crewai import Crew
from tasks import TaskFactory
from tools import DatabaseService
from pipeline.schema_planner import SchemaPlanner
from pipeline.sql_executor import TableBuilder
from pipeline.metrics import (
    compute_verified_metrics,
    select_primary_fact,
    run_structural_validation,
)


class TransformStep:
    def __init__(
        self,
        db_path: str,
        data_dir: str,
        reports_dir: str,
        reporter,
        build_factory_fn,
    ):
        self._db_path = db_path
        self._data_dir = data_dir
        self._reports_dir = reports_dir
        self._reporter = reporter
        self._build_factory = build_factory_fn

    def run(
        self,
        star_schema: str,
        profiling_results: str,
        entity_map: dict,
        entity_map_text: str,
        user_instructions: str = "",
    ) -> dict:
        source_row_counts = self._count_source_rows()

        planner = SchemaPlanner(self._data_dir, star_schema, source_row_counts)
        table_mapping = planner.table_mapping_text()

        schema_plan = self._generate_schema_plan(
            planner, star_schema, entity_map_text, table_mapping, user_instructions
        )

        clean_sql, created_tables = self._build_tables(
            schema_plan, table_mapping, profiling_results, star_schema
        )

        fact_tables = [t for t in created_tables if t.lower().startswith("fact_")]
        if not fact_tables:
            raise ValueError(
                "No Fact_ tables were successfully created. Pipeline cannot continue."
            )

        # Choose the primary fact by e-commerce entity role (revenue/transaction
        # priority), not by table size — see metrics.select_primary_fact.
        primary_fact = select_primary_fact(self._db_path, fact_tables, entity_map)
        print(f"[Flow] Primary fact table (by entity role): {primary_fact}")

        self._check_retention(source_row_counts)
        self._run_validation(source_row_counts, primary_fact, entity_map)

        verified_metrics = self._compute_metrics(primary_fact, entity_map)

        return {
            "clean_sql": clean_sql,
            "primary_fact_table": primary_fact,
            "source_row_counts": source_row_counts,
            "verified_metrics": verified_metrics,
        }

    def _count_source_rows(self) -> dict:
        print("[Flow] Counting source rows for data retention audit...")
        try:
            counts = DatabaseService.count_source_rows(self._data_dir)
            print(
                f"[Flow] Source row counts: "
                f"{dict(sorted(counts.items(), key=lambda x: -x[1]))}"
            )
            return counts
        except Exception as e:
            print(f"[Flow] Warning: Could not count source rows: {e}")
            return {}

    def _generate_schema_plan(
        self,
        planner: SchemaPlanner,
        star_schema: str,
        entity_map_text: str,
        table_mapping: str,
        user_instructions: str = "",
    ) -> list:
        print("[Flow] Generating schema plan...")
        factory = self._build_factory()
        architect = factory.create_warehouse_architect()
        plan_crew = Crew(
            agents=[architect],
            tasks=[
                TaskFactory(
                    {"warehouse_architect": architect}
                ).create_schema_plan_task()
            ],
            verbose=True,
        )
        plan_raw = plan_crew.kickoff(
            inputs={
                "star_schema": star_schema,
                "entity_map": entity_map_text,
                "table_mapping_text": table_mapping,
                "user_instructions": user_instructions,
            }
        )
        self._reporter.track(plan_crew)
        if plan_raw.pydantic and plan_raw.pydantic.tables:
            schema_plan = planner._complete_schema_plan(plan_raw.pydantic.tables)
        else:
            schema_plan = planner.parse_schema_plan(plan_raw.raw)
        print(f"[Flow] Schema plan: {[t['name'] for t in schema_plan]}")
        return schema_plan

    def _build_tables(
        self,
        schema_plan: list,
        table_mapping: str,
        profiling_results: str,
        star_schema: str,
    ) -> tuple[str, list[str]]:
        builder = TableBuilder(
            db_path=self._db_path,
            data_dir=self._data_dir,
            reports_dir=self._reports_dir,
            profiling_results=profiling_results,
            star_schema=star_schema,
            build_factory_fn=self._build_factory,
            track_usage_fn=self._reporter.track,
        )
        created_tables, combined_sql = builder.build_all(schema_plan, table_mapping)
        self._builder = builder
        return combined_sql, created_tables

    def _check_retention(self, source_row_counts: dict) -> None:
        for err in self._builder.run_retention_check(source_row_counts):
            print(f"[Flow] Retention warning: {err['error'][:120]}...")

    def _run_validation(
        self,
        source_row_counts: dict,
        primary_fact: str,
        entity_map: dict,
    ) -> None:
        # Deterministic structural audit — every number from a real DuckDB query in
        # a Python loop, so checks can't be skipped, mis-summed, or fabricated (an
        # LLM agent did all three). See metrics.run_structural_validation.
        print("[Flow] Running deterministic structural validation...")
        result = run_structural_validation(
            self._db_path, source_row_counts, entity_map, primary_fact
        )
        with open(
            os.path.join(self._reports_dir, "validation_report.md"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(result["report"])
        print(f"[Flow] Validation {result['status']}.")
        if result["status"] == "FAIL":
            fails = [c["name"] for c in result["checks"] if c["status"] == "FAIL"]
            raise RuntimeError(
                "Validation FAILED — structural integrity checks did not pass: "
                f"{fails}. See validation_report.md for details."
            )

    def _compute_metrics(self, primary_fact: str, entity_map: dict) -> dict:
        verified_metrics = compute_verified_metrics(
            self._db_path, primary_fact, entity_map
        )
        print(
            f"[Flow] Verified metrics: "
            f"{list(verified_metrics.get('fact_tables', {}).keys())}"
        )
        metrics_path = os.path.join(self._reports_dir, "verified_metrics.json")
        try:
            with open(metrics_path, "w", encoding="utf-8") as f:
                json.dump(verified_metrics, f, indent=2)
            print(f"[Flow] Saved verified metrics to {metrics_path}")
        except Exception as e:
            print(f"[Flow] Error saving verified metrics: {e}")
        return verified_metrics
