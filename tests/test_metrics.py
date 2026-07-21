"""WarehouseMetrics structural validation: declared grain keys must win over the
*_id/*_key naming heuristic, since the heuristic can flag a legitimate one-to-many
attribute (e.g. style_id on a table correctly deduplicated on sku_code) as a
duplicate-key defect even when the SQL is correct."""

import os
import shutil
import tempfile
import unittest

from tools import ConnectionManager
from utils.metrics import WarehouseMetrics, _resolve_pk, _pick_priority_table


class TestResolvePk(unittest.TestCase):
    def test_declared_key_wins_when_present(self):
        self.assertEqual(
            _resolve_pk("sku_code", ["sku_code", "style_id", "category"]),
            "sku_code",
        )

    def test_declared_key_matches_case_insensitively(self):
        self.assertEqual(
            _resolve_pk("SKU_Code", ["sku_code", "style_id"]), "sku_code"
        )

    def test_falls_back_to_heuristic_when_declared_key_not_a_real_column(self):
        # A hallucinated/stale declaration must not silently pass through.
        self.assertEqual(
            _resolve_pk("not_a_column", ["style_id", "sku_code"]), "style_id"
        )

    def test_falls_back_to_heuristic_when_nothing_declared(self):
        self.assertEqual(_resolve_pk("", ["style_id", "sku_code"]), "style_id")
        self.assertEqual(_resolve_pk(None, ["style_id", "sku_code"]), "style_id")


class TestDimPkUniquenessCheck(unittest.TestCase):
    """Reproduces the Dim_Products false positive: style_id (first *_id match by
    name) is a legitimate one-to-many attribute, not the table's actual grain —
    sku_code is, and the table is correctly deduplicated on it."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="adep_metrics_test_")
        self.db_path = os.path.join(self.temp_dir, "warehouse.db")
        self.cm = ConnectionManager(self.db_path, self.temp_dir)
        with self.cm.warehouse() as conn:
            conn.execute(
                "CREATE TABLE Dim_Products AS SELECT * FROM (VALUES "
                "('A1', 'S1', 'shirt'), ('A2', 'S1', 'shirt'), ('A3', 'S2', 'pants') "
                ") AS t(sku_code, style_id, category)"
            )
            conn.execute("CREATE TABLE Fact_Orders AS SELECT * FROM (VALUES (1, 10.0)) AS t(order_id, amount)")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _dim_check(self, result: dict) -> dict:
        return next(c for c in result["checks"] if c["name"] == "Dim PK uniqueness — Dim_Products")

    def test_without_declared_key_the_naming_heuristic_produces_a_false_failure(self):
        metrics = WarehouseMetrics(self.cm)
        result = metrics.run_structural_validation({}, None, "Fact_Orders")
        check = self._dim_check(result)
        self.assertEqual(check["status"], "FAIL")
        self.assertIn("style_id", check["detail"])

    def test_declared_key_fixes_the_false_failure(self):
        metrics = WarehouseMetrics(self.cm)
        result = metrics.run_structural_validation(
            {}, None, "Fact_Orders", declared_keys={"Dim_Products": "sku_code"}
        )
        check = self._dim_check(result)
        self.assertEqual(check["status"], "PASS")
        self.assertIn("sku_code", check["detail"])


class TestPickPriorityTable(unittest.TestCase):
    """Reproduces the Fact_Financials/Fact_Orders bug: a 4-row table built from an
    unrelated expense sheet name-matched 'financials' and outranked a 129k-row
    orders fact table purely because 'financials' sits above 'orders' in the
    priority list, with no check on whether it actually covers the data."""

    def test_tiny_priority_match_does_not_outrank_a_dominant_orders_table(self):
        entity_map = {"expense.csv": "financials", "amazon_sale.csv": "orders"}
        row_counts = {"Fact_Financials": 4, "Fact_Orders": 128975}
        picked = _pick_priority_table(
            ["Fact_Financials", "Fact_Orders"], entity_map, row_counts
        )
        self.assertEqual(picked, "Fact_Orders")

    def test_priority_match_with_real_coverage_still_wins(self):
        entity_map = {"payments.csv": "payments", "orders.csv": "orders"}
        row_counts = {"Fact_Payments": 9000, "Fact_Orders": 10000}
        picked = _pick_priority_table(
            ["Fact_Payments", "Fact_Orders"], entity_map, row_counts
        )
        self.assertEqual(picked, "Fact_Payments")

    def test_ties_within_a_tier_go_to_the_larger_table(self):
        entity_map = {"a.csv": "orders", "b.csv": "orders"}
        row_counts = {"Fact_Orders_A": 500, "Fact_Orders_B": 9000}
        picked = _pick_priority_table(
            ["Fact_Orders_A", "Fact_Orders_B"], entity_map, row_counts
        )
        self.assertEqual(picked, "Fact_Orders_B")

    def test_no_candidate_clears_the_bar_returns_none(self):
        entity_map = {"expense.csv": "financials"}
        row_counts = {"Fact_Financials": 4, "Fact_Other": 128975}
        picked = _pick_priority_table(
            ["Fact_Financials", "Fact_Other"], entity_map, row_counts
        )
        self.assertIsNone(picked)


class TestSelectPrimaryFactCoverageGuard(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="adep_primary_fact_test_")
        self.db_path = os.path.join(self.temp_dir, "warehouse.db")
        self.cm = ConnectionManager(self.db_path, self.temp_dir)
        with self.cm.warehouse() as conn:
            conn.execute(
                "CREATE TABLE Fact_Financials AS SELECT * FROM (VALUES "
                "(1, 500.0), (2, 600.0), (3, 400.0), (4, 409.0)"
                ") AS t(financial_transaction_id, amount)"
            )
            conn.execute(
                "CREATE TABLE Fact_Orders AS SELECT * FROM range(128975) "
                "AS t(order_line_id)"
            )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_tiny_financials_table_does_not_beat_the_real_orders_table(self):
        metrics = WarehouseMetrics(self.cm)
        entity_map = {"expense.csv": "financials", "amazon_sale.csv": "orders"}
        picked = metrics.select_primary_fact(
            ["Fact_Financials", "Fact_Orders"], entity_map
        )
        self.assertEqual(picked, "Fact_Orders")


if __name__ == "__main__":
    unittest.main()
