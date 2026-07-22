import glob
import os

import yaml
from crewai import Task
from schemas import (
    SchemaOutput,
    SchemaPlanOutput,
    SQLOutput,
    ReportOutput,
    AnswerabilityOutput,
    ColumnSemanticsOutput,
)


class TaskFactory:
    def __init__(self, agents_dict: dict, config_dir: str = "config/tasks"):
        self._agents = agents_dict
        self._config: dict = {}
        for path in sorted(glob.glob(os.path.join(config_dir, "*.yaml"))):
            with open(path, "r", encoding="utf-8") as f:
                self._config.update(yaml.safe_load(f))

    def _task(self, config_key: str, agent_key: str, output_schema=None) -> Task:
        cfg = self._config[config_key]
        kwargs: dict = dict(
            description=cfg["description"],
            expected_output=cfg["expected_output"],
            agent=self._agents[agent_key],
        )
        if output_schema:
            kwargs["output_pydantic"] = output_schema
        return Task(**kwargs)

    def create_quality_task(self) -> Task:
        # No output_pydantic: the report is a large freeform markdown body, and the
        # model has crashed pydantic's strict JSON parser before by emitting a markdown
        # backslash escape (e.g. `\_`) inside the JSON string, which isn't valid JSON.
        # QualityStep reads result.raw and scrapes the score via regex instead.
        return self._task("quality_task", "quality_engineer")

    def create_intent_validation_task(self) -> Task:
        return self._task(
            "intent_validation_task", "intent_validator", AnswerabilityOutput
        )

    def create_column_classification_task(self) -> Task:
        return self._task(
            "column_classification_task", "semantics_analyst", ColumnSemanticsOutput
        )

    def create_schema_design_task(self) -> Task:
        return self._task("schema_design_task", "warehouse_architect", SchemaOutput)

    def create_business_insights_task(self) -> Task:
        return self._task("business_insights_task", "analytics_engineer")

    def create_final_report_task(self) -> Task:
        return self._task("final_report_task", "lead_architect", ReportOutput)

    def create_schema_plan_task(self) -> Task:
        return self._task("schema_plan_task", "warehouse_architect", SchemaPlanOutput)

    def create_generate_table_sql_task(self) -> Task:
        return self._task("generate_table_sql_task", "warehouse_architect", SQLOutput)

    def create_fix_table_sql_task(self) -> Task:
        return self._task("fix_table_sql_task", "warehouse_architect", SQLOutput)
