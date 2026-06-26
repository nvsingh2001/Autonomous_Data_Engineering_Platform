import os
from dotenv import load_dotenv
import unittest
from crew import DataEngineeringFlow, DataEngineeringState
from tools import ToolRegistry
from agents import AgentFactory
from tasks import TaskFactory

load_dotenv()


class TestFlowArchitecture(unittest.TestCase):
    def setUp(self):
        self.registry = ToolRegistry(data_dir="data", chroma_db_path="test_chroma")
        model_name = os.environ.get("PIPELINE_MODEL", "ollama/gemma4:31b-cloud")
        base_url = os.environ.get("PIPELINE_BASE_URL", "http://localhost:11434")
        self.factory = AgentFactory(
            model_name=model_name, base_url=base_url, tool_registry=self.registry
        )

    def test_state_defaults(self):
        state = DataEngineeringState()
        self.assertEqual(state.data_dir, "data")
        self.assertEqual(len(state.files), 0)

    def test_agent_factory(self):
        quality_eng = self.factory.create_quality_engineer()
        architect = self.factory.create_warehouse_architect()
        analytics = self.factory.create_analytics_engineer()
        lead = self.factory.create_lead_architect()
        validation = self.factory.create_validation_engineer()

        self.assertEqual(quality_eng.role, "Lead Data Quality Assurance Engineer")
        self.assertEqual(architect.role, "Principal Data Warehouse Architect")
        self.assertEqual(analytics.role, "Senior Analytics Engineer")
        self.assertEqual(lead.role, "Chief Data Architect & Manager")
        self.assertEqual(validation.role, "Senior Data Warehouse Validation Engineer")

    def test_task_factory(self):
        quality_eng = self.factory.create_quality_engineer()
        task_factory = TaskFactory({"quality_engineer": quality_eng})
        task = task_factory.create_quality_task()

        self.assertIn("quality", task.description.lower() if task.description else "")
        self.assertEqual(task.agent.role, "Lead Data Quality Assurance Engineer")

        validation_eng = self.factory.create_validation_engineer()
        task_factory_val = TaskFactory({"validation_engineer": validation_eng})
        task_val = task_factory_val.create_validation_task()
        self.assertEqual(
            task_val.agent.role, "Senior Data Warehouse Validation Engineer"
        )

    def test_token_tracking_helper(self):
        from crewai.types.usage_metrics import UsageMetrics
        from unittest.mock import MagicMock
        from pipeline import TokenReporter

        reporter = TokenReporter()

        mock_agent = MagicMock()
        mock_agent.role = "Test Agent"
        mock_agent.llm = MagicMock()

        dummy_metrics = UsageMetrics(
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            successful_requests=2,
        )
        mock_agent.llm.get_token_usage_summary.return_value = dummy_metrics

        mock_crew = MagicMock()
        mock_crew.agents = [mock_agent]

        reporter.track(mock_crew)

        self.assertIn("Test Agent", reporter._usage)
        metrics = reporter._usage["Test Agent"]
        self.assertEqual(metrics["prompt_tokens"], 100)
        self.assertEqual(metrics["completion_tokens"], 50)
        self.assertEqual(metrics["total_tokens"], 150)
        self.assertEqual(metrics["successful_requests"], 2)


if __name__ == "__main__":
    unittest.main()
