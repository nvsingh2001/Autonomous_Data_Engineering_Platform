"""VerifyStep's claim-verification section must run independently of whether the user
asked any questions — claims exist for every run's always-on report sections. Before
this restructuring, an empty user_intent hit an early return that skipped verification
entirely; this is the regression guard for removing that early return."""

import json
import os
import shutil
import tempfile
import unittest

from pipeline import DataEngineeringState, StepContext, TokenReporter
from pipeline.steps.verify import VerifyStep
from tools import ConnectionManager


class TestClaimVerificationRunsWithoutUserIntent(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="adep_verifyclaims_test_")
        self.reports_dir = os.path.join(self.temp_dir, "reports")
        os.makedirs(self.reports_dir, exist_ok=True)
        self.db_path = os.path.join(self.temp_dir, "warehouse.db")
        self.cm = ConnectionManager(self.db_path, self.temp_dir)
        with self.cm.warehouse() as conn:
            conn.execute(
                "CREATE TABLE Fact_Orders AS SELECT * FROM (VALUES "
                "('electronics', 1000.0), ('books', 200.0)"
                ") AS t(category, revenue)"
            )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_claims(self, claims: list[dict]) -> None:
        path = os.path.join(self.reports_dir, "claims.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for c in claims:
                f.write(json.dumps(c) + "\n")

    def test_divergent_claim_populates_diverged_list(self):
        self._write_claims(
            [
                {
                    "claim_text": "100% of revenue is electronics",
                    "sql_query": "SELECT category, revenue FROM Fact_Orders",
                    "reported_value": "100%",
                }
            ]
        )
        state = DataEngineeringState(reports_dir=self.reports_dir, user_intent={})
        ctx = StepContext(state=state, cm=self.cm, reporter=TokenReporter())
        VerifyStep(ctx).run()

        self.assertEqual(len(state.definitions_diverged), 1)
        self.assertEqual(state.definitions_diverged[0]["kind"], "claim")
        self.assertIn("REVIEW NEEDED", state.verification_report)
        self.assertNotEqual(state.analytics_feedback, "")

    def test_consistent_claim_does_not_populate_diverged(self):
        self._write_claims(
            [
                {
                    "claim_text": "Electronics revenue is 1,000.00",
                    "sql_query": "SELECT revenue FROM Fact_Orders WHERE category = 'electronics'",
                    "reported_value": "1,000.00",
                }
            ]
        )
        state = DataEngineeringState(reports_dir=self.reports_dir, user_intent={})
        ctx = StepContext(state=state, cm=self.cm, reporter=TokenReporter())
        VerifyStep(ctx).run()

        self.assertEqual(state.definitions_diverged, [])
        self.assertEqual(state.analytics_feedback, "")
        self.assertIn("Verification Status: OK", state.verification_report)

    def test_no_claims_recorded_is_stated_plainly_not_silently_omitted(self):
        state = DataEngineeringState(reports_dir=self.reports_dir, user_intent={})
        ctx = StepContext(state=state, cm=self.cm, reporter=TokenReporter())
        VerifyStep(ctx).run()

        self.assertIn("No claims were recorded", state.verification_report)
        self.assertIn("UNVERIFIED", state.verification_report)


if __name__ == "__main__":
    unittest.main()
