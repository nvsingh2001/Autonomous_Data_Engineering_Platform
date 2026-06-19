import yaml
from crewai import Task

class TaskFactory:
    def __init__(self, agents_dict: dict, config_path: str = "config/tasks.yaml"):
        self._agents = agents_dict
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)

    def create_profiling_task(self) -> Task:
        cfg = self.config["profiling_task"]
        return Task(
            description=cfg["description"],
            expected_output=cfg["expected_output"],
            agent=self._agents["profiler"]
        )

    def create_quality_task(self) -> Task:
        cfg = self.config["quality_task"]
        return Task(
            description=cfg["description"],
            expected_output=cfg["expected_output"],
            agent=self._agents["quality_engineer"]
        )

    def create_schema_design_task(self) -> Task:
        cfg = self.config["schema_design_task"]
        return Task(
            description=cfg["description"],
            expected_output=cfg["expected_output"],
            agent=self._agents["warehouse_architect"]
        )

    def create_transformation_task(self) -> Task:
        cfg = self.config["transformation_task"]
        return Task(
            description=cfg["description"],
            expected_output=cfg["expected_output"],
            agent=self._agents["warehouse_architect"]
        )

    def create_business_insights_task(self) -> Task:
        cfg = self.config["business_insights_task"]
        return Task(
            description=cfg["description"],
            expected_output=cfg["expected_output"],
            agent=self._agents["analytics_engineer"]
        )

    def create_final_report_task(self) -> Task:
        cfg = self.config["final_report_task"]
        return Task(
            description=cfg["description"],
            expected_output=cfg["expected_output"],
            agent=self._agents["lead_architect"]
        )
