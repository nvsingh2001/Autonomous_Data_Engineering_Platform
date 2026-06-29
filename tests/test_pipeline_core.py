import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace

from pipeline import DataEngineeringState, StepContext, PipelineStep, TokenReporter


class _DummyStep(PipelineStep):
    def run(self) -> None:  # abstract contract — not exercised here
        pass


class TestPipelineCore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="adep_core_")
        self.state = DataEngineeringState(
            reports_dir=self.tmp,
            entity_map={"a.csv": "orders", "b.csv": "customers"},
        )
        # cm is not exercised by these tests, so None is fine (plain dataclass).
        self.ctx = StepContext(state=self.state, cm=None, reporter=TokenReporter())
        self.step = _DummyStep(self.ctx)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── StepContext ─────────────────────────────────────────────────────────

    def test_context_reports_dir(self):
        self.assertEqual(self.ctx.reports_dir, self.tmp)

    def test_context_entity_map_text(self):
        txt = self.ctx.entity_map_text()
        self.assertIn("a.csv: orders", txt)
        self.assertIn("b.csv: customers", txt)

    # ── PipelineStep base ───────────────────────────────────────────────────

    def test_step_accessors(self):
        self.assertIs(self.step.state, self.state)
        self.assertEqual(self.step.reports_dir, self.tmp)

    def test_write_report(self):
        self.step._write_report("x.md", "hello")
        with open(os.path.join(self.tmp, "x.md"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "hello")

    def test_extract_pydantic(self):
        r = SimpleNamespace(pydantic=SimpleNamespace(report="P"), raw="R")
        self.assertEqual(PipelineStep._extract(r), "P")

    def test_extract_raw_fallback(self):
        r = SimpleNamespace(pydantic=None, raw="R")
        self.assertEqual(PipelineStep._extract(r), "R")

    def test_extract_custom_attr(self):
        r = SimpleNamespace(pydantic=SimpleNamespace(score=42), raw="R")
        self.assertEqual(PipelineStep._extract(r, "score"), 42)


if __name__ == "__main__":
    unittest.main()
