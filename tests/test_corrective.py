import unittest

from pipeline import DataEngineeringState, StepContext, TokenReporter
from pipeline.steps.transform import TransformStep


def _step():
    ctx = StepContext(state=DataEngineeringState(), cm=None, reporter=TokenReporter())
    return TransformStep(ctx)


class TestFailingTables(unittest.TestCase):
    """The check->table mapping that turns a structural-validation FAIL into a targeted
    corrective rebuild request. Pure logic — no warehouse, no LLM."""

    def test_maps_fail_to_table_with_reason(self):
        checks = [
            {"name": "Dim PK uniqueness — Dim_Date", "status": "FAIL",
             "detail": "PK 'date_key': 1,127 duplicate keys"},
        ]
        out = _step()._failing_tables(checks)
        self.assertEqual(len(out), 1)
        table, reason = out[0]
        self.assertEqual(table, "Dim_Date")
        self.assertIn("date_key", reason)
        self.assertIn("Dim_Date", reason)

    def test_ignores_pass_and_warn(self):
        checks = [
            {"name": "Data Retention", "status": "WARN", "detail": "..."},
            {"name": "Dim PK uniqueness — Dim_Customers", "status": "PASS", "detail": "ok"},
            {"name": "Date-FK null rate — Fact_Orders", "status": "WARN", "detail": "..."},
        ]
        self.assertEqual(_step()._failing_tables(checks), [])

    def test_dedups_multiple_fails_same_table(self):
        checks = [
            {"name": "Cartesian check — Fact_Payments", "status": "FAIL", "detail": "ratio 9.0"},
            {"name": "Revenue integrity — Fact_Payments", "status": "FAIL", "detail": "3 negatives"},
        ]
        out = _step()._failing_tables(checks)
        self.assertEqual([t for t, _ in out], ["Fact_Payments"])

    def test_extracts_multiple_distinct_tables(self):
        checks = [
            {"name": "Dim PK uniqueness — Dim_Date", "status": "FAIL", "detail": "dups"},
            {"name": "Cartesian check — Fact_Payments", "status": "FAIL", "detail": "fanout"},
        ]
        out = _step()._failing_tables(checks)
        self.assertEqual({t for t, _ in out}, {"Dim_Date", "Fact_Payments"})


if __name__ == "__main__":
    unittest.main()
