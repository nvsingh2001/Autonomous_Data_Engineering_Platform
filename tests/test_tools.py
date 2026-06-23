import unittest
import os
import shutil
from tools import (
    ToolRegistry,
    RunDuckDBQueryTool,
    ProfileCSVFileTool,
    ReadCSVPreviewTool,
    SavePastExecutionTool,
    SearchPastExecutionsTool,
)


class TestCustomTools(unittest.TestCase):
    def setUp(self):
        self.data_dir = "data"
        self.test_chroma = "test_chroma"
        self.registry = ToolRegistry(
            data_dir=self.data_dir, chroma_db_path=self.test_chroma
        )

    def tearDown(self):
        if os.path.exists(self.test_chroma):
            shutil.rmtree(self.test_chroma)

    def test_registry_initialization(self):
        db_tools = self.registry.get_db_tools()
        mem_tools = self.registry.get_memory_tools()
        self.assertEqual(len(db_tools), 3)
        self.assertEqual(len(mem_tools), 2)

    def test_run_duckdb_query(self):
        tool = RunDuckDBQueryTool(data_dir=self.data_dir)
        res = tool._run("SELECT 1 AS val")
        self.assertIn("1", res)

    def test_run_duckdb_query_views(self):
        tool = RunDuckDBQueryTool(data_dir=self.data_dir)
        res_view = tool._run("SELECT COUNT(*) AS count FROM products")
        import re

        self.assertTrue(bool(re.search(r"\d+", res_view)))

        res_path = tool._run("SELECT COUNT(*) AS count FROM 'products.csv'")
        self.assertTrue(bool(re.search(r"\d+", res_path)))

    def test_profile_csv_file(self):
        tool = ProfileCSVFileTool(data_dir=self.data_dir)
        res = tool._run("products.csv")
        self.assertIn("Total Rows", res)
        self.assertIn("product_id", res)

    def test_read_csv_preview(self):
        tool = ReadCSVPreviewTool(data_dir=self.data_dir)
        res = tool._run("products.csv", limit=2)
        self.assertIn("product_id", res)
        self.assertIn("product_name", res)

    def test_chromadb_memory_tools(self):
        save_tool = SavePastExecutionTool(chroma_db_path=self.test_chroma)
        search_tool = SearchPastExecutionsTool(chroma_db_path=self.test_chroma)

        save_res = save_tool._run("schema_design", "test_key", "FactSales")
        self.assertIn("Successfully saved", save_res)

        search_res = search_tool._run("schema_design", "FactSales", limit=1)
        self.assertIn("test_key", search_res)
        self.assertIn("FactSales", search_res)


if __name__ == "__main__":
    unittest.main()
