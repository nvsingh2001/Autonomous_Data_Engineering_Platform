"""ClaimVerifier: re-executes an agent's own cited SQL for a self-recorded claim and
checks whether the claimed number actually appears in the result. Reproduces the exact
failure mode found live on the Amazon Sale Report dataset: a "100% of revenue" claim
whose underlying query, once re-run independently, does not actually support it."""

import os
import shutil
import tempfile
import unittest

from tools import ConnectionManager
from utils.claim_verifier import ClaimVerifier


class TestClaimVerifier(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="adep_claimverifier_test_")
        self.db_path = os.path.join(self.temp_dir, "warehouse.db")
        self.cm = ConnectionManager(self.db_path, self.temp_dir)
        with self.cm.warehouse() as conn:
            conn.execute(
                "CREATE TABLE Fact_Orders AS SELECT * FROM (VALUES "
                "('electronics', 10, 1000.0), ('books', 5, 200.0), ('toys', 3, 150.0)"
                ") AS t(category, orders, revenue)"
            )
        self.verifier = ClaimVerifier(self.cm)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_scalar_claim_matches(self):
        claim = {
            "claim_text": "Total revenue is 1,350.00",
            "sql_query": "SELECT SUM(revenue) FROM Fact_Orders",
            "reported_value": "1,350.00",
        }
        result = self.verifier.verify(claim)
        self.assertEqual(result["status"], "CONSISTENT")

    def test_group_by_claim_about_a_non_first_column_matches(self):
        # Locks in that only the FIRST cell isn't checked — a legitimate claim about
        # the third column of a multi-row GROUP BY result must not be flagged DIVERGENT.
        claim = {
            "claim_text": "Electronics category revenue is 1,000.00",
            "sql_query": "SELECT category, orders, revenue FROM Fact_Orders ORDER BY revenue DESC",
            "reported_value": "1,000.00",
        }
        result = self.verifier.verify(claim)
        self.assertEqual(result["status"], "CONSISTENT")

    def test_divergent_when_claimed_number_does_not_appear(self):
        # Reproduces the live Amazon Sale Report bug: a claimed "100%" whose cited
        # query, re-run independently, has no 100 anywhere in its actual result.
        claim = {
            "claim_text": "100% of revenue flows through this channel",
            "sql_query": "SELECT category, revenue FROM Fact_Orders WHERE category = 'electronics'",
            "reported_value": "100%",
        }
        result = self.verifier.verify(claim)
        self.assertEqual(result["status"], "DIVERGENT")

    def test_empty_when_query_returns_no_rows(self):
        claim = {
            "claim_text": "No orders in Q5",
            "sql_query": "SELECT * FROM Fact_Orders WHERE category = 'nonexistent'",
            "reported_value": "0",
        }
        result = self.verifier.verify(claim)
        self.assertEqual(result["status"], "EMPTY")

    def test_error_when_sql_is_invalid(self):
        claim = {
            "claim_text": "bad SQL",
            "sql_query": "SELECT * FROM Not_A_Real_Table",
            "reported_value": "42",
        }
        result = self.verifier.verify(claim)
        self.assertEqual(result["status"], "ERROR")
        self.assertIsNotNone(result["error"])

    def test_empty_when_reported_value_has_no_number(self):
        claim = {
            "claim_text": "revenue trend is positive",
            "sql_query": "SELECT * FROM Fact_Orders",
            "reported_value": "n/a",
        }
        result = self.verifier.verify(claim)
        self.assertEqual(result["status"], "EMPTY")


class TestClaimVerifierTimedeltaValues(unittest.TestCase):
    """Live false positive found on the Olist run: AVG(delivered_at - estimated_at), a
    very common delivery-latency claim, returns a Python timedelta — float() on it
    raised TypeError, silently dropping the cell and flagging an otherwise-correct
    claim as DIVERGENT. -20.077 days (the real value) must match a claim of '-20.08
    days'."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="adep_claimverifier_td_test_")
        self.db_path = os.path.join(self.temp_dir, "warehouse.db")
        self.cm = ConnectionManager(self.db_path, self.temp_dir)
        with self.cm.warehouse() as conn:
            # delivered - estimated = -(20 days, 1:55:12) = -20.08 days exactly.
            conn.execute(
                "CREATE TABLE Fact_Orders AS SELECT * FROM (VALUES "
                "(TIMESTAMP '2018-01-01 00:00:00', TIMESTAMP '2018-01-21 01:55:12')"
                ") AS t(delivered_at, estimated_at)"
            )
        self.verifier = ClaimVerifier(self.cm)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_fractional_day_timedelta_matches_its_rounded_claim(self):
        claim = {
            "claim_text": "Average delivery is 20.08 days early",
            "sql_query": "SELECT AVG(delivered_at - estimated_at) FROM Fact_Orders",
            "reported_value": "-20.08 days",
        }
        result = self.verifier.verify(claim)
        self.assertEqual(result["status"], "CONSISTENT")


if __name__ == "__main__":
    unittest.main()
