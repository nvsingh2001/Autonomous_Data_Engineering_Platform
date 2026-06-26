import yaml
from crewai import Task
from tasks.output_schemas import QualityOutput


class TaskFactory:
    def __init__(self, agents_dict: dict, config_path: str = "config/tasks.yaml"):
        self._agents = agents_dict
        with open(config_path, "r", encoding="utf-8") as f:
            self._config = yaml.safe_load(f)

    def _task(self, config_key: str, agent_key: str) -> Task:
        cfg = self._config[config_key]
        return Task(
            description=cfg["description"],
            expected_output=cfg["expected_output"],
            agent=self._agents[agent_key],
        )

    def create_quality_task(self) -> Task:
        cfg = self._config["quality_task"]
        return Task(
            description=cfg["description"],
            expected_output=cfg["expected_output"],
            agent=self._agents["quality_engineer"],
            output_pydantic=QualityOutput,
        )

    def create_schema_design_task(self) -> Task:
        return self._task("schema_design_task", "warehouse_architect")

    def create_transformation_task(self) -> Task:
        return self._task("transformation_task", "warehouse_architect")

    def create_business_insights_task(self) -> Task:
        return self._task("business_insights_task", "analytics_engineer")

    def create_final_report_task(self) -> Task:
        return self._task("final_report_task", "lead_architect")

    def create_schema_plan_task(self) -> Task:
        return self._task("schema_plan_task", "warehouse_architect")

    def create_generate_table_sql_task(self) -> Task:
        return self._task("generate_table_sql_task", "warehouse_architect")

    def create_fix_table_sql_task(self) -> Task:
        return self._task("fix_table_sql_task", "warehouse_architect")

    def create_sql_fix_task(self) -> Task:
        return self._task("sql_fix_task", "warehouse_architect")

    def create_validation_task(self) -> Task:
        return self._task("validation_task", "validation_engineer")
