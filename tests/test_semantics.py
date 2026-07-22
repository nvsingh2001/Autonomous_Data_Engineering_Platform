"""SemanticGrounder: every LLM-proposed column role must be verified against real
profiling stats before anything downstream trusts it. Reproduces the exact failure
modes found in production — a name match alone (sales_channel/"sales") is not enough,
and a foreign key (order_id on a child table) must still ground its business role even
though it's deliberately non-unique."""

import unittest

from utils.semantics import SemanticGrounder


def _profile(row_count, columns, sample_values=None):
    return {"row_count": row_count, "columns": columns, "sample_values": sample_values or {}}


def _col(name, datatype, unique_count, null_pct=0.0):
    return {
        "name": name,
        "datatype": datatype,
        "unique_count": unique_count,
        "null_percentage": null_pct,
    }


class TestGroundStructuralRoles(unittest.TestCase):
    def test_monetary_with_string_datatype_rejected(self):
        profiling = {
            "orders.csv": _profile(3, [_col("sales_channel", "STRING", 2)]),
        }
        proposals = [
            {
                "file": "orders.csv",
                "column": "sales_channel",
                "structural_role": "monetary_amount",
                "business_role": "none",
                "evidence": "looks like sales",
                "expected_cardinality": "repeating",
            }
        ]
        grounded = SemanticGrounder.ground(proposals, profiling)
        self.assertFalse(grounded[0]["structural_grounded"])

    def test_monetary_with_numeric_datatype_grounds(self):
        profiling = {"orders.csv": _profile(3, [_col("amount", "FLOAT", 3)])}
        proposals = [
            {
                "file": "orders.csv",
                "column": "amount",
                "structural_role": "monetary_amount",
                "business_role": "none",
                "evidence": "numeric values",
                "expected_cardinality": "repeating",
            }
        ]
        grounded = SemanticGrounder.ground(proposals, profiling)
        self.assertTrue(grounded[0]["structural_grounded"])

    def test_unique_identifier_requires_high_uniqueness(self):
        # order_id on a child (line-item) table repeats — structural unique_identifier
        # must be rejected even though the LLM proposed it.
        profiling = {
            "order_items.csv": _profile(100, [_col("order_id", "STRING", 40)])
        }
        proposals = [
            {
                "file": "order_items.csv",
                "column": "order_id",
                "structural_role": "unique_identifier",
                "business_role": "order_identifier",
                "evidence": "id-looking strings",
                "expected_cardinality": "unique",
            }
        ]
        grounded = SemanticGrounder.ground(proposals, profiling)
        self.assertFalse(grounded[0]["structural_grounded"])


class TestTemporalFallback(unittest.TestCase):
    """TypeInspector profiles a datetime-with-time string ("2018-02-22 21:04:23") as
    STRING, not DATE, since its date-format list has no time-component patterns — a
    real production finding on the Olist delivery-date columns. The temporal gate must
    still ground such a column by parsing its real sample values, or 'temporal' and the
    delivery-date business roles are permanently unreachable on ordinary timestamp data."""

    def test_datetime_string_column_grounds_as_temporal(self):
        profiling = {
            "orders.csv": _profile(
                100,
                [_col("delivered_at", "STRING", 95)],
                {"delivered_at": ["2018-02-22 21:04:23", "2018-02-02 16:12:53"]},
            )
        }
        proposals = [
            {
                "file": "orders.csv",
                "column": "delivered_at",
                "structural_role": "temporal",
                "business_role": "actual_delivery_date",
                "evidence": "delivery timestamps",
                "expected_cardinality": "repeating",
            }
        ]
        grounded = SemanticGrounder.ground(proposals, profiling)
        self.assertTrue(grounded[0]["structural_grounded"])
        self.assertTrue(grounded[0]["business_grounded"])

    def test_non_date_string_does_not_ground_as_temporal(self):
        profiling = {
            "orders.csv": _profile(
                100,
                [_col("status", "STRING", 5)],
                {"status": ["shipped", "delivered"]},
            )
        }
        proposals = [
            {
                "file": "orders.csv",
                "column": "status",
                "structural_role": "temporal",
                "business_role": "none",
                "evidence": "wrong guess",
                "expected_cardinality": "repeating",
            }
        ]
        grounded = SemanticGrounder.ground(proposals, profiling)
        self.assertFalse(grounded[0]["structural_grounded"])

    def test_real_date_datatype_still_grounds_without_sample_values(self):
        profiling = {"orders.csv": _profile(100, [_col("order_date", "DATE", 90)])}
        proposals = [
            {
                "file": "orders.csv",
                "column": "order_date",
                "structural_role": "temporal",
                "business_role": "none",
                "evidence": "dates",
                "expected_cardinality": "repeating",
            }
        ]
        grounded = SemanticGrounder.ground(proposals, profiling)
        self.assertTrue(grounded[0]["structural_grounded"])


class TestGroundBusinessRoles(unittest.TestCase):
    def test_order_identifier_on_low_uniqueness_fk_column_grounds_via_permissive_gate(self):
        profiling = {
            "order_items.csv": _profile(100, [_col("order_id", "STRING", 40, null_pct=0.0)])
        }
        proposals = [
            {
                "file": "order_items.csv",
                "column": "order_id",
                "structural_role": "unique_identifier",
                "business_role": "order_identifier",
                "evidence": "repeats per line item",
                "expected_cardinality": "repeating",
            }
        ]
        grounded = SemanticGrounder.ground(proposals, profiling)
        self.assertFalse(grounded[0]["structural_grounded"])
        self.assertTrue(grounded[0]["business_grounded"])

    def test_identifier_business_role_rejected_when_mostly_null(self):
        profiling = {
            "orders.csv": _profile(100, [_col("customer_id", "STRING", 10, null_pct=40.0)])
        }
        proposals = [
            {
                "file": "orders.csv",
                "column": "customer_id",
                "structural_role": "categorical_label",
                "business_role": "customer_identifier",
                "evidence": "mostly missing",
                "expected_cardinality": "repeating",
            }
        ]
        grounded = SemanticGrounder.ground(proposals, profiling)
        self.assertFalse(grounded[0]["business_grounded"])

    def test_cardinality_mismatched_business_role_flagged_ungrounded(self):
        # payment_type must be a STRING/INTEGER label — a DATE column claiming it is bogus.
        profiling = {"payments.csv": _profile(10, [_col("paid_at", "DATE", 8)])}
        proposals = [
            {
                "file": "payments.csv",
                "column": "paid_at",
                "structural_role": "temporal",
                "business_role": "payment_type",
                "evidence": "wrong guess",
                "expected_cardinality": "repeating",
            }
        ]
        grounded = SemanticGrounder.ground(proposals, profiling)
        self.assertFalse(grounded[0]["business_grounded"])

    def test_column_missing_from_profiling_data_is_ungrounded_not_a_crash(self):
        proposals = [
            {
                "file": "ghost.csv",
                "column": "phantom",
                "structural_role": "monetary_amount",
                "business_role": "none",
                "evidence": "hallucinated",
                "expected_cardinality": "unique",
            }
        ]
        grounded = SemanticGrounder.ground(proposals, {})
        self.assertFalse(grounded[0]["structural_grounded"])
        self.assertFalse(grounded[0]["business_grounded"])


class TestBuildLookup(unittest.TestCase):
    def test_cross_file_same_name_occurrences_kept_separate(self):
        grounded = [
            {"file": "orders.csv", "column": "order_id", "business_role": "order_identifier"},
            {"file": "order_items.csv", "column": "order_id", "business_role": "order_identifier"},
        ]
        lookup = SemanticGrounder.build_lookup(grounded)
        self.assertEqual(len(lookup["orderid"]), 2)
        files = {occ["file"] for occ in lookup["orderid"]}
        self.assertEqual(files, {"orders.csv", "order_items.csv"})

    def test_normalization_matches_metrics_convention(self):
        grounded = [{"file": "f.csv", "column": "Order_ID", "business_role": "order_identifier"}]
        lookup = SemanticGrounder.build_lookup(grounded)
        self.assertIn("orderid", lookup)


if __name__ == "__main__":
    unittest.main()
