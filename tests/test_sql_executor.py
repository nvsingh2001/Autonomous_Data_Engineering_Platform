"""TableBuilder's declared grain-key freeze: the architect reports its intended
key once, at first successful build, and fix_table() (driven by a validation
failure — the model is under pressure) must not be able to redeclare it."""

import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tools import ConnectionManager
from utils import sql_executor
from utils.sql_executor import TableBuilder

_PRODUCTS_CSV = """sku_code,style_id,category
A1,S1,shirt
A2,S1,shirt
A3,S2,pants
"""


class _FakeResult:
    def __init__(self, sql: str, primary_key: str):
        self.pydantic = SimpleNamespace(sql=sql, primary_key=primary_key)
        self.raw = sql


class _FakeCrew:
    """Stands in for crewai.Crew: returns whatever sql/primary_key the test set
    via class attributes, ignoring the agents/tasks it was built with."""

    next_sql = ""
    next_pk = ""

    def __init__(self, agents, tasks, verbose=False):
        pass

    def kickoff(self, inputs):
        return _FakeResult(_FakeCrew.next_sql, _FakeCrew.next_pk)


class _FakeTaskFactory:
    """Stands in for tasks.TaskFactory: _FakeCrew never inspects its tasks, so
    these don't need to be real crewai Task objects (which would validate
    `agent` against the real Agent pydantic model)."""

    def __init__(self, agents_dict):
        pass

    def create_generate_table_sql_task(self):
        return None

    def create_fix_table_sql_task(self):
        return None


class TestTableBuilderKeyFreeze(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="adep_tb_test_")
        self.db_path = os.path.join(self.temp_dir, "warehouse.db")
        with open(os.path.join(self.temp_dir, "products.csv"), "w") as f:
            f.write(_PRODUCTS_CSV)
        self.cm = ConnectionManager(self.db_path, self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _builder(self) -> TableBuilder:
        return TableBuilder(
            cm=self.cm,
            reports_dir=self.temp_dir,
            profiling_results="{}",
            star_schema="",
            build_factory_fn=lambda: SimpleNamespace(
                create_warehouse_architect=lambda: object()
            ),
            track_usage_fn=lambda crew: None,
        )

    def _spec(self) -> list:
        return [
            {
                "name": "Dim_Products",
                "type": "dimension",
                "sources": ["products"],
                "description": "",
            }
        ]

    def test_declared_key_captured_on_first_success(self):
        _FakeCrew.next_sql = (
            "CREATE OR REPLACE TABLE Dim_Products AS "
            "SELECT sku_code, style_id FROM products GROUP BY sku_code, style_id;"
        )
        _FakeCrew.next_pk = "sku_code"
        builder = self._builder()
        with patch.object(sql_executor, "Crew", _FakeCrew), patch.object(
            sql_executor, "TaskFactory", _FakeTaskFactory
        ):
            created, _ = builder.build_all(self._spec(), "products -> products")
        self.assertEqual(created, ["Dim_Products"])
        self.assertEqual(builder.table_keys(), {"Dim_Products": "sku_code"})

    def test_fix_table_does_not_overwrite_the_frozen_key(self):
        _FakeCrew.next_sql = (
            "CREATE OR REPLACE TABLE Dim_Products AS "
            "SELECT sku_code, style_id FROM products GROUP BY sku_code, style_id;"
        )
        _FakeCrew.next_pk = "sku_code"
        builder = self._builder()
        with patch.object(sql_executor, "Crew", _FakeCrew), patch.object(
            sql_executor, "TaskFactory", _FakeTaskFactory
        ):
            builder.build_all(self._spec(), "products -> products")

            # A corrective fix call tries to redeclare the key under pressure —
            # this must be ignored, not trusted.
            _FakeCrew.next_pk = "style_id"
            ok = builder.fix_table("Dim_Products", "Dim PK uniqueness failed")

        self.assertTrue(ok)
        self.assertEqual(builder.table_keys(), {"Dim_Products": "sku_code"})


if __name__ == "__main__":
    unittest.main()
