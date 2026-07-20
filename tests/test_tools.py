import os
import re
import shutil
import tempfile
import unittest
from tools import (
    ToolRegistry,
    RunDuckDBQueryTool,
    ProfileCSVFileTool,
    ReadCSVPreviewTool,
    SavePastExecutionTool,
    SearchPastExecutionsTool,
)

_PRODUCTS_CSV = """\
product_id,product_name,price_usd,created_at
1,Widget Alpha,9.99,2023-01-01
2,Widget Beta,19.99,2023-01-02
3,Widget Gamma,29.99,2023-01-03
"""


class TestCustomTools(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="adep_test_")
        self.data_dir = self.temp_dir
        self.test_chroma = os.path.join(self.temp_dir, "test_chroma")

        # Write minimal fixture dataset
        with open(os.path.join(self.data_dir, "products.csv"), "w") as f:
            f.write(_PRODUCTS_CSV)

        self.registry = ToolRegistry(
            data_dir=self.data_dir, chroma_db_path=self.test_chroma
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # ── registry ──────────────────────────────────────────────────────────────

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

        # products.csv auto-registered as a named view
        res_view = tool._run("SELECT COUNT(*) AS cnt FROM products")
        self.assertTrue(bool(re.search(r"\d+", res_view)), f"No digit in: {res_view}")

        # also accessible via bare filename reference
        res_path = tool._run("SELECT COUNT(*) AS cnt FROM 'products.csv'")
        self.assertTrue(bool(re.search(r"\d+", res_path)), f"No digit in: {res_path}")

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

        save_res = save_tool._run(
            "schema_decisions",
            "test_key",
            "FactSales grain=one row per order",
        )
        self.assertIn("Saved", save_res)
        self.assertIn("test_key", save_res)

        search_res = search_tool._run("schema_decisions", "FactSales", limit=1)
        self.assertIn("test_key", search_res)
        self.assertIn("FactSales", search_res)

    def test_chromadb_rejects_invalid_category(self):
        save_tool = SavePastExecutionTool(chroma_db_path=self.test_chroma)
        res = save_tool._run("invalid_category", "key", "content")
        self.assertIn("Rejected", res)

    def test_search_rejects_low_jaccard_cross_dataset_memory(self):
        # A memory tagged with a small, generic entity set (2 entities) must
        # not leak into an unrelated, much larger dataset just because it
        # shares those 2 generic names — that let an old fuzzy_factory
        # (pageviews/sessions/refunds) date-dimension memory bleed into an
        # unrelated Olist run in production.
        save_tool = SavePastExecutionTool(
            chroma_db_path=self.test_chroma,
            entity_types=["order_items", "pageviews", "products", "refunds", "sessions"],
        )
        save_tool._run(
            "schema_decisions",
            "date_dimension_generation",
            "Dim_Date is generated using UNNEST(generate_series()).",
        )
        search_tool = SearchPastExecutionsTool(
            chroma_db_path=self.test_chroma,
            entity_types=[
                "categories", "customers", "order_items", "orders",
                "payments", "products", "reviews", "sellers",
            ],
        )
        res = search_tool._run("schema_decisions", "Dim_Date generation", limit=3)
        self.assertNotIn("date_dimension_generation", res)

    def test_search_accepts_same_dataset_memory(self):
        entities = ["orders", "products", "customers"]
        save_tool = SavePastExecutionTool(
            chroma_db_path=self.test_chroma, entity_types=entities
        )
        save_tool._run(
            "schema_decisions", "orders_grain", "orders entity -> Fact_Orders."
        )
        search_tool = SearchPastExecutionsTool(
            chroma_db_path=self.test_chroma, entity_types=entities
        )
        res = search_tool._run("schema_decisions", "Fact_Orders grain", limit=3)
        self.assertIn("orders_grain", res)

    def test_chromadb_analytics_insights_category(self):
        save_tool = SavePastExecutionTool(chroma_db_path=self.test_chroma)
        search_tool = SearchPastExecutionsTool(chroma_db_path=self.test_chroma)

        save_res = save_tool._run(
            "analytics_insights",
            "mom_trend_pattern",
            "MoM growth via LAG window function on date-grained fact table",
        )
        self.assertIn("Saved", save_res)

        search_res = search_tool._run(
            "analytics_insights", "monthly revenue trend", limit=1
        )
        self.assertIn("mom_trend_pattern", search_res)


if __name__ == "__main__":
    unittest.main()
