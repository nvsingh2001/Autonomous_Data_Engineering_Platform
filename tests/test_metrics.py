"""WarehouseMetrics structural validation: declared grain keys must win over the
*_id/*_key naming heuristic, since the heuristic can flag a legitimate one-to-many
attribute (e.g. style_id on a table correctly deduplicated on sku_code) as a
duplicate-key defect even when the SQL is correct."""

import os
import shutil
import tempfile
import unittest

from tools import ConnectionManager
from utils.metrics import (
    WarehouseMetrics,
    _resolve_pk,
    _pick_priority_table,
    _pick_revenue_column,
    _pick_order_id_column,
    _find_date_fk,
)


def _occurrence(column, structural_role=None, business_role=None, file="f.csv"):
    """A grounded (or deliberately ungrounded) semantic occurrence, matching
    SemanticGrounder's output shape."""
    return {
        "file": file,
        "column": column,
        "structural_role": structural_role or "categorical_label",
        "structural_grounded": structural_role is not None,
        "business_role": business_role or "none",
        "business_grounded": business_role is not None,
    }


def _lookup(*occurrences) -> dict:
    lookup: dict = {}
    for occ in occurrences:
        lookup.setdefault(occ["column"].lower().replace("_", ""), []).append(occ)
    return lookup


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


class TestResolvePkWithSemantics(unittest.TestCase):
    """A grounded semantic unique_identifier is a second, weaker signal than a
    declared key — used only when nothing was declared, and never trusted if the
    grounding gate rejected the proposal (e.g. a repeating FK column) OR if it fails
    a real uniqueness check on THIS table. The same column name can be a genuine PK
    in one source file (grounding says unique_identifier=True) and a repeating FK in
    another built table — the live check is what tells these apart."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="adep_resolvepk_test_")
        self.db_path = os.path.join(self.temp_dir, "warehouse.db")
        self.cm = ConnectionManager(self.db_path, self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_semantic_unique_identifier_wins_when_it_actually_is_unique_here(self):
        with self.cm.warehouse() as conn:
            conn.execute(
                "CREATE TABLE Dim_Products AS SELECT * FROM (VALUES "
                "('S1', 'A1'), ('S1', 'A2'), ('S2', 'A3')"
                ") AS t(style_id, sku_code)"
            )
            semantics = _lookup(_occurrence("sku_code", structural_role="unique_identifier"))
            picked = _resolve_pk(
                None, ["style_id", "sku_code"], semantics, conn, "Dim_Products"
            )
        self.assertEqual(picked, "sku_code")

    def test_declared_key_still_wins_over_semantic_proposal(self):
        semantics = _lookup(
            _occurrence("style_id", structural_role="unique_identifier")
        )
        self.assertEqual(
            _resolve_pk("sku_code", ["sku_code", "style_id"], semantics), "sku_code"
        )

    def test_ungrounded_semantic_proposal_is_ignored(self):
        semantics = {
            "styleid": [
                {
                    "file": "f.csv",
                    "column": "style_id",
                    "structural_role": "unique_identifier",
                    "structural_grounded": False,
                    "business_role": "none",
                    "business_grounded": False,
                }
            ]
        }
        self.assertEqual(
            _resolve_pk(None, ["style_id", "sku_code"], semantics), "style_id"
        )

    def test_semantic_match_that_is_unique_elsewhere_but_repeats_here_is_rejected(self):
        # order_id is grounded unique_identifier from orders.csv (a genuine PK there),
        # but on THIS fact table it's a repeating FK (many line items per order) — the
        # live uniqueness check must reject it and fall back to the real grain key.
        with self.cm.warehouse() as conn:
            conn.execute(
                "CREATE TABLE Fact_OrderItems AS SELECT * FROM (VALUES "
                "(1, 'O1'), (2, 'O1'), (3, 'O2'), (4, 'O2'), (5, 'O3')"
                ") AS t(line_item_id, order_id)"
            )
            semantics = _lookup(_occurrence("order_id", structural_role="unique_identifier"))
            picked = _resolve_pk(
                None, ["line_item_id", "order_id"], semantics, conn, "Fact_OrderItems"
            )
        self.assertEqual(picked, "line_item_id")

    def test_no_conn_falls_back_to_naming_heuristic_without_trusting_semantics_blindly(self):
        semantics = _lookup(_occurrence("order_id", structural_role="unique_identifier"))
        self.assertEqual(
            _resolve_pk(None, ["line_item_id", "order_id"], semantics), "line_item_id"
        )


class TestPickOrderIdColumn(unittest.TestCase):
    def test_semantic_order_identifier_replaces_suffix_match(self):
        semantics = _lookup(
            _occurrence("order_ref", business_role="order_identifier")
        )
        self.assertEqual(
            _pick_order_id_column(["order_ref", "other_col"], semantics), "order_ref"
        )

    def test_falls_back_to_suffix_match_without_semantics(self):
        self.assertEqual(
            _pick_order_id_column(["order_id", "other"], None), "order_id"
        )

    def test_ungrounded_business_role_falls_back_to_suffix_match(self):
        semantics = {
            "orderref": [
                {
                    "file": "f.csv",
                    "column": "order_ref",
                    "structural_role": "categorical_label",
                    "structural_grounded": False,
                    "business_role": "order_identifier",
                    "business_grounded": False,
                }
            ]
        }
        self.assertEqual(
            _pick_order_id_column(["order_ref", "order_id"], semantics), "order_id"
        )


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


class TestPickRevenueColumn(unittest.TestCase):
    """Reproduces the sales_channel bug: a keyword substring match ("sales" in
    "sales_channel") beat the real "amount" column purely by column order, even
    though sales_channel is a text label that TRY_CASTs to NULL for every row."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="adep_revcol_test_")
        self.db_path = os.path.join(self.temp_dir, "warehouse.db")
        self.cm = ConnectionManager(self.db_path, self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_text_column_matching_a_keyword_substring_is_skipped(self):
        with self.cm.warehouse() as conn:
            conn.execute(
                "CREATE TABLE Fact_Orders AS SELECT * FROM (VALUES "
                "('Amazon.in', 100.0), ('Amazon.in', 200.0), ('Flipkart', 50.0)"
                ") AS t(sales_channel, amount)"
            )
            picked = _pick_revenue_column(
                conn, "Fact_Orders", ["sales_channel", "amount"]
            )
        self.assertEqual(picked, "amount")

    def test_no_numeric_candidate_returns_none(self):
        with self.cm.warehouse() as conn:
            conn.execute(
                "CREATE TABLE Fact_Orders AS SELECT * FROM (VALUES "
                "('Amazon.in',), ('Flipkart',)"
                ") AS t(sales_channel)"
            )
            picked = _pick_revenue_column(conn, "Fact_Orders", ["sales_channel"])
        self.assertIsNone(picked)

    def test_semantic_monetary_column_found_when_name_has_no_revenue_keyword(self):
        # "grand_sum" matches none of _REVENUE_KEYS — only the semantic classifier
        # can surface it as the revenue column.
        with self.cm.warehouse() as conn:
            conn.execute(
                "CREATE TABLE Fact_Orders AS SELECT * FROM (VALUES "
                "('A', 100.0), ('B', 200.0)"
                ") AS t(label, grand_sum)"
            )
            semantics = _lookup(
                _occurrence("grand_sum", structural_role="monetary_amount")
            )
            picked = _pick_revenue_column(
                conn, "Fact_Orders", ["label", "grand_sum"], semantics
            )
        self.assertEqual(picked, "grand_sum")

    def test_semantic_monetary_proposal_still_requires_numeric_castability(self):
        # A grounded proposal is a weaker signal than the real data — this proves
        # classification alone is never trusted, only used to pick candidates.
        with self.cm.warehouse() as conn:
            conn.execute(
                "CREATE TABLE Fact_Orders AS SELECT * FROM (VALUES "
                "('A', 'not-a-number'), ('B', 'also-text')"
                ") AS t(label, grand_sum)"
            )
            semantics = _lookup(
                _occurrence("grand_sum", structural_role="monetary_amount")
            )
            picked = _pick_revenue_column(
                conn, "Fact_Orders", ["label", "grand_sum"], semantics
            )
        self.assertIsNone(picked)


class TestFindDateFk(unittest.TestCase):
    """The date-FK column is synthesized at build time and never appears in source
    profiling data — grounded instead by a real join-coverage query against Dim_Date,
    not a name guess."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="adep_datefk_test_")
        self.db_path = os.path.join(self.temp_dir, "warehouse.db")
        self.cm = ConnectionManager(self.db_path, self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_picks_the_key_that_actually_joins_over_a_decoy(self):
        with self.cm.warehouse() as conn:
            conn.execute("CREATE TABLE Dim_Date AS SELECT * FROM range(1, 101) AS t(date_key)")
            conn.execute(
                "CREATE TABLE Fact_Orders AS SELECT * FROM (VALUES "
                "(1, 5, 999), (2, 10, 999), (3, 15, 999), (4, 999, 999)"
                ") AS t(order_id, order_date_key, decoy_date_key)"
            )
            picked = _find_date_fk(
                conn, "Fact_Orders", ["order_id", "order_date_key", "decoy_date_key"]
            )
        self.assertEqual(picked, "order_date_key")

    def test_returns_none_when_no_candidate_clears_the_join_threshold(self):
        with self.cm.warehouse() as conn:
            conn.execute("CREATE TABLE Dim_Date AS SELECT * FROM range(1, 101) AS t(date_key)")
            conn.execute(
                "CREATE TABLE Fact_Orders AS SELECT * FROM (VALUES (999,), (998,)"
                ") AS t(ship_date_key)"
            )
            picked = _find_date_fk(conn, "Fact_Orders", ["ship_date_key"])
        self.assertIsNone(picked)

    def test_returns_none_without_a_dim_date_table(self):
        with self.cm.warehouse() as conn:
            conn.execute(
                "CREATE TABLE Fact_Orders AS SELECT * FROM (VALUES (1,)) AS t(order_date_key)"
            )
            picked = _find_date_fk(conn, "Fact_Orders", ["order_date_key"])
        self.assertIsNone(picked)


if __name__ == "__main__":
    unittest.main()
