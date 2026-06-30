import unittest

from pipeline import DataEngineeringState, StepContext, TokenReporter
from pipeline.steps.verify import VerifyStep


def _verify():
    ctx = StepContext(state=DataEngineeringState(), cm=None, reporter=TokenReporter())
    return VerifyStep(ctx)


class TestDefinitionAnchoring(unittest.TestCase):
    """The verifier enforces AGREED DEFINITIONS, not the looser question text — so a report
    that re-frames a defined metric is caught. Pure logic; no warehouse, no LLM."""

    def test_targets_anchor_on_definitions_when_present(self):
        intent = {
            "questions": ["Is slower delivery hurting satisfaction?"],
            "metric_definitions": [
                {"name": "satisfaction by band",
                 "definition": "avg review_score by delivery band <=3/4-7/8+ days, delivered only"},
            ],
        }
        targets = _verify()._targets(intent)
        self.assertEqual(len(targets), 1)
        name, metric, is_def = targets[0]
        self.assertEqual(name, "satisfaction by band")
        self.assertIn("4-7", metric)
        self.assertTrue(is_def)  # an agreed definition — enforced

    def test_targets_fall_back_to_questions_without_definitions(self):
        targets = _verify()._targets({"questions": ["What is revenue?"]})
        self.assertEqual(targets, [("Q1", "What is revenue?", False)])  # flag-only

    def test_correction_feedback_names_metric_and_recompute(self):
        fb = _verify()._correction_feedback([
            {"name": "satisfaction by band",
             "definition": "avg review_score by <=3/4-7/8+ days",
             "rows": [("<=3 days", 4.46), ("4-7 days", 4.40), ("8+ days", 4.04)]},
        ])
        self.assertIn("CORRECTION REQUIRED", fb)
        self.assertIn("satisfaction by band", fb)
        self.assertIn("4.46", fb)  # the independent recompute is handed back as an anchor


if __name__ == "__main__":
    unittest.main()
