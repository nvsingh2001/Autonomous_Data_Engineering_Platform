import os
import json

from crewai import Crew
from tasks import TaskFactory
from utils import SchemaPlanner, TableBuilder, WarehouseMetrics
from config import CREW_VERBOSE
from pipeline.core import PipelineStep


class TransformStep(PipelineStep):
    """Plans and builds the warehouse tables, picks the primary fact, runs the
    deterministic structural validation, and computes the verified metrics. Writes
    `state.clean_sql`, `state.primary_fact_table`, `state.source_row_counts`,
    `state.verified_metrics`."""

    # Corrective rebuild rounds when structural validation FAILs (self-healing before abort).
    MAX_VALIDATION_FIX = 2

    def run(self) -> None:
        star_schema = self.state.star_schema
        profiling_results = self.state.profiling_results
        entity_map = self.state.entity_map
        entity_map_text = self._ctx.entity_map_text()
        user_instructions = self.state.user_instructions

        source_row_counts = self._count_source_rows()

        planner = SchemaPlanner(self.state.data_dir, star_schema, source_row_counts)
        table_mapping = planner.table_mapping_text()

        schema_plan = self._generate_schema_plan(
            planner, star_schema, entity_map_text, table_mapping, user_instructions
        )

        _, created_tables = self._build_tables(
            schema_plan, table_mapping, profiling_results, star_schema
        )

        fact_tables = [t for t in created_tables if t.lower().startswith("fact_")]
        if not fact_tables:
            raise ValueError(
                "No Fact_ tables were successfully created. Pipeline cannot continue."
            )

        metrics = WarehouseMetrics(self.cm)

        # Choose the primary fact by e-commerce entity role (revenue/transaction
        # priority), not by table size — see WarehouseMetrics.select_primary_fact.
        primary_fact = metrics.select_primary_fact(fact_tables, entity_map)
        print(f"[Flow] Primary fact table (by entity role): {primary_fact}")

        self._check_retention(source_row_counts)
        self._validate_with_correction(
            metrics, source_row_counts, primary_fact, entity_map
        )

        verified_metrics = self._compute_metrics(metrics, primary_fact, entity_map)

        self.state.clean_sql = self._builder.combined_sql()
        self.state.primary_fact_table = primary_fact
        self.state.source_row_counts = source_row_counts
        self.state.verified_metrics = verified_metrics

    def _count_source_rows(self) -> dict:
        print("[Flow] Counting source rows for data retention audit...")
        try:
            counts = self.cm.count_source_rows()
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
        architect = self._ctx.build_factory().create_warehouse_architect()
        plan_crew = Crew(
            agents=[architect],
            tasks=[
                TaskFactory(
                    {"warehouse_architect": architect}
                ).create_schema_plan_task()
            ],
            verbose=CREW_VERBOSE,
        )
        plan_raw = plan_crew.kickoff(
            inputs={
                "star_schema": star_schema,
                "entity_map": entity_map_text,
                "table_mapping_text": table_mapping,
                "user_instructions": user_instructions,
            }
        )
        self.reporter.track(plan_crew)
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
            cm=self.cm,
            reports_dir=self.reports_dir,
            profiling_results=profiling_results,
            star_schema=star_schema,
            build_factory_fn=self._ctx.build_factory,
            track_usage_fn=self.reporter.track,
        )
        created_tables, combined_sql = builder.build_all(schema_plan, table_mapping)
        self._builder = builder
        return combined_sql, created_tables

    def _check_retention(self, source_row_counts: dict) -> None:
        for err in self._builder.run_retention_check(source_row_counts):
            print(f"[Flow] Retention warning: {err['error'][:120]}...")

    def _validate_with_correction(
        self,
        metrics: WarehouseMetrics,
        source_row_counts: dict,
        primary_fact: str,
        entity_map: dict,
    ) -> None:
        result: dict = {}
        for attempt in range(self.MAX_VALIDATION_FIX + 1):
            print("[Flow] Running deterministic structural validation...")
            result = metrics.run_structural_validation(
                source_row_counts, entity_map, primary_fact
            )
            if result["status"] == "PASS":
                break
            failing = self._failing_tables(result["checks"])
            if not failing or attempt == self.MAX_VALIDATION_FIX:
                break
            names = ", ".join(t for t, _ in failing)
            print(
                f"[Flow] Validation FAILED — corrective rebuild "
                f"(round {attempt + 1}/{self.MAX_VALIDATION_FIX}) of: {names}"
            )
            for table, reason in failing:
                self._builder.fix_table(table, reason)

        self._write_report("validation_report.md", result["report"])
        print(f"[Flow] Validation {result['status']}.")
        if result["status"] == "FAIL":
            fails = [c["name"] for c in result["checks"] if c["status"] == "FAIL"]
            raise RuntimeError(
                "Validation FAILED after corrective rebuild — structural integrity checks "
                f"did not pass: {fails}. See validation_report.md for details."
            )

    def _failing_tables(self, checks: list[dict]) -> list[tuple[str, str]]:
        """Map each FAILing structural check to (table, actionable error message). The
        check name encodes its table ('Dim PK uniqueness — Dim_Date'); the message hands
        the architect the defect and how to eliminate it without changing the grain."""
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        for c in checks:
            if c["status"] != "FAIL":
                continue
            name = c["name"]
            table = name.split("—")[-1].strip() if "—" in name else ""
            if not table or table in seen:
                continue
            seen.add(table)
            reason = (
                f"Structural integrity check FAILED — {name}: {c['detail']}. "
                f"Regenerate the COMPLETE SQL for {table} so this check passes: keep the "
                "same columns and intended grain, but eliminate the defect. A dimension's "
                "key must be unique (use UNION not UNION ALL, or wrap the final SELECT in "
                "SELECT DISTINCT / GROUP BY the key); a fact must not fan out into a "
                "cartesian product; revenue columns must not be negative."
            )
            out.append((table, reason))
        return out

    def _compute_metrics(
        self, metrics: WarehouseMetrics, primary_fact: str, entity_map: dict
    ) -> dict:
        verified_metrics = metrics.compute_verified(primary_fact, entity_map)
        print(
            f"[Flow] Verified metrics: "
            f"{list(verified_metrics.get('fact_tables', {}).keys())}"
        )
        metrics_path = os.path.join(self.reports_dir, "verified_metrics.json")
        try:
            self._write_report(
                "verified_metrics.json", json.dumps(verified_metrics, indent=2)
            )
            print(f"[Flow] Saved verified metrics to {metrics_path}")
        except Exception as e:
            print(f"[Flow] Error saving verified metrics: {e}")
        return verified_metrics
