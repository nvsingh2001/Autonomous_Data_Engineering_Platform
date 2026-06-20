import unittest
from flow import DataEngineeringFlow, DataEngineeringState
from tools import ToolRegistry
from agents import AgentFactory
from tasks import TaskFactory

class TestFlowArchitecture(unittest.TestCase):
    def setUp(self):
        self.registry = ToolRegistry(data_dir="data", chroma_db_path="test_chroma")
        self.factory = AgentFactory(
            model_name="ollama/gemma4:31b-cloud",
            base_url="http://localhost:11434",
            tool_registry=self.registry
        )

    def test_state_defaults(self):
        state = DataEngineeringState()
        self.assertEqual(state.data_dir, "data")
        self.assertEqual(len(state.files), 0)

    def test_agent_factory(self):
        profiler = self.factory.create_profiler()
        quality_eng = self.factory.create_quality_engineer()
        architect = self.factory.create_warehouse_architect()
        analytics = self.factory.create_analytics_engineer()
        lead = self.factory.create_lead_architect()

        self.assertEqual(profiler.role, "Senior Data Profiling & Metadata Engineer")
        self.assertEqual(quality_eng.role, "Lead Data Quality Assurance Engineer")
        self.assertEqual(architect.role, "Principal Data Warehouse Architect")
        self.assertEqual(analytics.role, "Senior Analytics Engineer")
        self.assertEqual(lead.role, "Chief Data Architect & Manager")

    def test_task_factory(self):
        profiler = self.factory.create_profiler()
        task_factory = TaskFactory({"profiler": profiler})
        task = task_factory.create_profiling_task()
        
        self.assertIn("files", task.description)
        self.assertEqual(task.agent.role, "Senior Data Profiling & Metadata Engineer")

    def test_token_tracking_helper(self):
        from crewai.types.usage_metrics import UsageMetrics
        from unittest.mock import MagicMock
        
        flow = DataEngineeringFlow()
        flow.state.agent_token_usage = {}
        
        # Mock agent
        mock_agent = MagicMock()
        mock_agent.role = "Test Agent"
        mock_agent.llm = MagicMock()
        
        # Mock UsageMetrics return
        dummy_metrics = UsageMetrics(
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            successful_requests=2
        )
        mock_agent.llm.get_token_usage_summary.return_value = dummy_metrics
        
        # Mock crew
        mock_crew = MagicMock()
        mock_crew.agents = [mock_agent]
        
        flow._track_crew_usage(mock_crew)
        
        self.assertIn("Test Agent", flow.state.agent_token_usage)
        metrics = flow.state.agent_token_usage["Test Agent"]
        self.assertEqual(metrics["prompt_tokens"], 100)
        self.assertEqual(metrics["completion_tokens"], 50)
        self.assertEqual(metrics["total_tokens"], 150)
        self.assertEqual(metrics["successful_requests"], 2)

if __name__ == "__main__":
    unittest.main()
